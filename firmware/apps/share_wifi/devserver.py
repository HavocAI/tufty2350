"""The USB dev link's command server: sessions, dispatch, the /devtests/
scan, `new` uploads, and running subtools with the Api they are handed.

Pure, like protocol.py. Everything badge-side is injected:

    port      read(limit) -> bytes, write(bytes) -> bool, arm_cancel(),
              disarm_cancel(), and `b_fired` (usbport.UsbPort on the badge)
    root      the directory subtools live in ("/devtests"); their library
              modules are in root/lib, on sys.path while a subtool runs
    hooks     the badge, as an object: usb_connected(), poll(), b_held(),
              status(text), restore(), collect(), format_exc(error),
              save(tool, entry) -> bool, firmware, and what Api exposes --
              light(), temp (callable or None), ring() (a reader or None),
              ring_note(), backlight(v), present(), ticks_us(), ticks_ms()
    builtins  {name: Builtin}: instant commands of the app's own

A run blocks: `run(api, args)` is called inside one service() call.

A subtool may also list `MENU = (("label", [args]), ...)`: Dev Mode then
shows each entry once the subtool is on the badge, and runs it with no host
through `run_local()`, its results saved by `api.save()` alone.

Specification: "USB dev link" in .agents/share_wifi/app-spec.md.
"""

import json
import os
import sys

from devlink import (
    APP, CHUNK_OUT, FIELD_CAP, LINE_MAX_BADGE, NEW_MAX, PROTO, Assembler,
    LineReader, b64, cap_head, cap_tail, crc32, encode, valid_ext,
    valid_subtest, valid_tool_name,
)

QUIET_READ = 256        # bytes a cancelled() check reads at most
DISARM_TRIES = 5        # disarm_cancel() retried past a KeyboardInterrupt
LIB = "lib"


class RingBusy(Exception):
    """A USB write, or an ADC read, while the DMA ring has the ADC."""


class Builtin:
    """An instant app command: `fn(args)` returns (data, saved, text) --
    data sent as <name>.json unless None."""

    def __init__(self, name, desc, fn):
        self.name = name
        self.desc = desc
        self.fn = fn


class Stream:
    """One file to the host, sent in CHUNK_OUT pieces as it fills."""

    def __init__(self, api, name, ext):
        self.api = api
        self.server = api.server
        self.sid = self.server.next_sid()
        self.buf = bytearray(CHUNK_OUT)
        self.fill = 0
        self.seq = 0
        self.size = 0
        self.crc = 0
        self.open = True
        self.server.send({"t": "open", "sid": self.sid, "name": name,
                          "ext": ext, "rid": self.server.run_rid})

    def write(self, data):
        # Checked before the buffer changes: a refusal half way would leave
        # the count and CRC out of step with what was sent.
        self.api.writing()
        if isinstance(data, str):
            data = data.encode()
        view = memoryview(data)
        at = 0
        n = len(view)
        while at < n:
            take = min(CHUNK_OUT - self.fill, n - at)
            self.buf[self.fill:self.fill + take] = view[at:at + take]
            self.fill += take
            at += take
            if self.fill == CHUNK_OUT:
                self._flush()

    def _flush(self):
        if self.fill:
            piece = memoryview(self.buf)[:self.fill]
            self.crc = crc32(piece, self.crc)
            self.size += self.fill
            self.server.send({"t": "chunk", "sid": self.sid, "seq": self.seq,
                              "b": b64(piece), "rid": self.server.run_rid})
            self.seq += 1
            self.fill = 0

    def close(self, ok=True):
        if not self.open:
            return
        if ok:
            # Not polled: a run that has returned is not cancelled by a B
            # held as it is tidied.
            self.api.ring_check()
            self._flush()
        self.open = False
        self.server.send({"t": "end", "sid": self.sid, "size": self.size,
                          "crc": self.crc, "ok": ok,
                          "rid": self.server.run_rid})

    def __enter__(self):
        return self

    def __exit__(self, kind, value, trace):
        self.close(kind is None)


class Api:
    """What a subtool's run(api, args) gets."""

    def __init__(self, server, name, args):
        self.server = server
        self.hooks = server.hooks
        self.name = name
        self.args = args
        self.streams = []
        self.ring = None
        self.dropped = 0        # log lines dropped while the ring ran
        self.stop = None        # why cancelled() said yes, once it has
        hooks = self.hooks
        self.ticks_us = hooks.ticks_us
        self.ticks_ms = hooks.ticks_ms
        self.backlight = hooks.backlight
        self.present = hooks.present

    # -- writing

    def ring_check(self):
        if self.ring is not None:
            raise RingBusy("stop the ring before writing to USB")

    def writing(self):
        """Every writing call: refused while the ring runs, and the buttons
        polled, so B is seen even by a subtool that never asks."""
        self.ring_check()
        self.hooks.poll()
        if self.stop is None and self.hooks.b_held():
            self.stop = "B"

    def _subtest(self, name):
        if name is None:
            return self.name
        full = self.name + "_" + str(name)
        if not valid_subtest(full):
            raise ValueError("bad name: %s" % name)
        return full

    def stream(self, ext=None, name=None):
        ext = ext or self.server.ext_of(self.name)
        if not valid_ext(ext):
            raise ValueError("bad ext: %s" % ext)
        subtest = self._subtest(name)
        self.writing()
        stream = Stream(self, subtest, ext)
        self.streams.append(stream)
        return stream

    def send(self, data, ext=None, name=None):
        """One file: bytes or str as they are, a dict or list as JSON."""
        if isinstance(data, (dict, list)):
            data = json.dumps(data).encode()
            ext = ext or "json"
        stream = self.stream(ext, name)
        stream.write(data)
        stream.close()

    def save(self, entry, name=None):
        """`entry` into the badge's measurements file, under the tool's
        name (or `name`): what Dev Mode's display shows. True if written."""
        self.ring_check()
        return self.hooks.save(name or self.name, entry)

    def log(self, text):
        """A log line. Dropped and counted while the ring runs, so a debug
        print() cannot abort a capture."""
        if self.ring is not None:
            self.dropped += 1
            return
        self.writing()
        self.server.send({"t": "log", "text": cap_tail(text),
                          "rid": self.server.run_rid})

    def status(self, text):
        self.writing()
        self.hooks.status(text)

    # -- reading

    def cancelled(self):
        """True once B is held, the host cancelled, or the host is gone.
        Reads at most QUIET_READ bytes of USB and never writes."""
        if self.stop is not None:
            return True
        hooks = self.hooks
        hooks.poll()
        server = self.server
        if hooks.b_held():
            self.stop = "B"
        elif server.lost or not hooks.usb_connected():
            self.stop = "host lost"
        else:
            server.run_input()
            if server.cancel_req is not None:
                self.stop = server.cancel_req
        return self.stop is not None

    def light(self):
        self._adc()
        return self.hooks.light()

    def temp(self):
        self._adc()
        reader = self.hooks.temp
        return reader() if reader is not None else None

    def _adc(self):
        if self.ring is not None:
            raise RingBusy("the ring has the ADC")

    def tethered(self):
        return self.hooks.usb_connected()

    # -- the ring

    def ring_start(self):
        """The DMA ring's reader, started, or None (ring_note() says why).
        While it runs, light(), temp() and every write raise RingBusy."""
        if self.ring is not None:
            return self.ring
        reader = self.hooks.ring()
        if reader is None:
            return None
        reader.start()
        self.ring = reader
        self.server.ring_active = True
        return reader

    def ring_stop(self):
        reader = self.ring
        if reader is None:
            return
        self.ring = None
        self.server.ring_active = False
        reader.stop()
        if self.dropped:
            dropped = self.dropped
            self.dropped = 0
            self.log("%d lines dropped while the ring ran" % dropped)

    def ring_note(self):
        return self.hooks.ring_note()


class Server:
    def __init__(self, port, root, hooks, builtins):
        self.port = port
        self.root = root
        self.hooks = hooks
        self.builtins = builtins
        self.reader = LineReader(LINE_MAX_BADGE)
        self.queue = []             # frames read and not yet dispatched
        self.session = None
        self.lost = False
        self.ever_hello = False
        self.tools = {}             # stem -> (signature, entry)
        self.upload = None
        self.running = None         # the tool running, or None
        self.run_rid = None
        self.cancel_req = None      # "host" or "hello", for the running tool
        self.stash = None           # a hello read mid-run, answered after it
        self.ring_active = False
        self.sid = 0
        self.last = "-"
        self.runs = 0               # runs finished, so the app can disarm B

    # -- output

    def send(self, obj, force=False):
        """One frame. False, and nothing written, once the host is gone or
        before a hello -- unless `force`d, for the replies that say so."""
        if self.ring_active:
            raise RingBusy("stop the ring before writing to USB")
        if not force and (self.lost or self.session is None):
            return False
        if not self.hooks.usb_connected():
            self.lost = True
            return False
        if not self.port.write(encode(obj)):
            self.lost = True
            return False
        return True

    def reply(self, frame, obj, force=False):
        obj["rid"] = frame.get("rid")
        return self.send(obj, force)

    def error(self, frame, code, text="", force=False):
        return self.reply(frame, {"t": "error", "code": code,
                                  "text": cap_tail(text)}, force)

    def next_sid(self):
        self.sid += 1
        return self.sid

    def host_label(self):
        if not self.hooks.usb_connected():
            return "unplugged"
        if self.lost:
            return "gone (no reply to a write)"
        if self.session is not None:
            return "connected"
        return "waiting" if self.ever_hello else "waiting (or power only)"

    def status_text(self):
        broken = 0
        for stem in self.tools:
            if self.tools[stem][1].get("error"):
                broken += 1
        tools = "Tools: %d in %s" % (len(self.tools), self.root)
        if broken:
            tools += " (%d failed to load)" % broken
        return "Host: %s\n%s\nLast: %s" % (self.host_label(), tools,
                                           self.last)

    # -- input

    def service(self):
        """Read what has arrived and answer every complete frame. True if a
        run ended in this call."""
        runs = self.runs
        self.take(LINE_MAX_BADGE)
        queue = self.queue
        while queue:
            # One at a time: a run dispatched here reads the rest of the
            # queue (a cancel, a hello) through run_input().
            self.dispatch_safely(queue.pop(0))
        return self.runs != runs

    def take(self, limit):
        data = self.port.read(limit)
        if data:
            for item in self.reader.feed(data):
                if isinstance(item, dict):
                    self.queue.append(item)

    def dispatch_safely(self, frame):
        try:
            self.dispatch(frame)
        except (Exception, SystemExit) as error:   # noqa: BLE001 - not a reset
            try:
                self.error(frame, "internal", self.hooks.format_exc(error))
            except Exception:   # noqa: BLE001 - nothing more to say
                pass

    def dispatch(self, frame):
        kind = frame["t"]
        if kind == "hello":
            self.hello(frame)
            return
        if kind == "cancel":
            # Never answered on its own: during a run the run's last frame
            # is the answer, and idle there is nothing to cancel.
            if self.running is not None:
                self.cancel_req = "host"
            return
        if self.session is None:
            self.error(frame, "no_session", "send hello first", force=True)
            return
        if kind == "bye":
            self.upload = None
            self.reply(frame, {"t": "bye", "reason": "host"})
            self.session = None
            return
        if self.upload is not None and kind != "data":
            self.error(frame, "busy", "an upload is in progress")
            return
        if kind == "list":
            self.scan()
            self.reply(frame, {"t": "tools", "tools": self.tool_list(),
                               "libs": self.libs()})
        elif kind == "run":
            self.run(frame)
        elif kind == "new":
            self.new(frame)
        elif kind == "data":
            self.data(frame)
        elif kind == "remove":
            self.remove(frame)
        else:
            self.error(frame, "bad_frame", "unknown type " + kind)

    def hello(self, frame):
        if frame.get("proto") != PROTO:
            self.error(frame, "proto", "badge speaks %d" % PROTO, force=True)
            return
        self.session = str(frame.get("id"))
        self.lost = False
        self.ever_hello = True
        self.upload = None
        self.reply(frame, {
            "t": "hello", "id": self.session, "proto": PROTO, "app": APP,
            "firmware": self.hooks.firmware,
            "tethered": self.hooks.usb_connected(),
            "builtins": len(self.builtins), "custom": len(self.tools)})

    def run_input(self):
        """Mid-run, for cancelled(): reads a little and never writes. A
        `cancel` or `hello` cancels the run; the hello is answered after it.
        Anything else is dropped: a stop-and-wait host sends nothing else."""
        self.take(QUIET_READ)
        queue = self.queue
        while queue:
            item = queue.pop(0)
            if item["t"] == "cancel":
                self.cancel_req = "host"
            elif item["t"] == "hello":
                self.stash = item
                self.cancel_req = "hello"

    # -- tools

    def ext_of(self, stem):
        entry = self.tools.get(stem)
        if entry is not None:
            return entry[1].get("ext") or "bin"
        return "bin"

    def tool_list(self):
        out = []
        for name in self.builtins:
            out.append({"name": name, "desc": cap_head(self.builtins[name].desc),
                        "args": "", "ext": "json", "builtin": True,
                        "error": None})
        for stem in sorted(self.tools):
            out.append(self.tools[stem][1])
        return out

    def path(self, stem, lib=False):
        return self.root + ("/" + LIB if lib else "") + "/" + stem + ".py"

    def libs(self):
        return libs(self.root)

    def scan(self):
        """/devtests/ read: a file is loaded again only if its size or mtime
        changed; a stale .tmp is removed."""
        try:
            names = os.listdir(self.root)
        except OSError:
            names = []
        seen = {}
        for filename in names:
            path = self.root + "/" + filename
            if filename.endswith(".tmp"):
                try:
                    os.remove(path)
                except OSError:
                    pass
                continue
            if not filename.endswith(".py"):
                continue
            stem = filename[:-3]
            try:
                st = os.stat(path)
                signature = (st[6], st[8])
            except OSError:
                continue
            seen[stem] = True
            old = self.tools.get(stem)
            if old is not None and old[0] == signature:
                continue
            self.tools[stem] = (signature, self.probe(stem))
        for stem in list(self.tools):
            if stem not in seen:
                del self.tools[stem]

    def load(self, stem, source=None, printer=None):
        """A subtool's namespace, executed fresh. Raises what it raises."""
        if source is None:
            with open(self.path(stem)) as handle:
                source = handle.read()
        code = compile(source, self.path(stem), "exec")
        namespace = {"__name__": stem}
        namespace["print"] = printer or _no_print
        exec(code, namespace)
        return namespace

    def probe(self, stem, source=None):
        entry = {"name": stem, "desc": "", "args": "", "ext": "bin",
                 "builtin": False, "error": None}
        if not valid_tool_name(stem) or stem in self.builtins:
            entry["error"] = "bad name"
            return entry
        try:
            namespace = self.with_libs(self.load, stem, source)
        except (Exception, SystemExit) as error:   # noqa: BLE001 - listed
            entry["error"] = cap_tail(self.hooks.format_exc(error), FIELD_CAP)
            return entry
        if not callable(namespace.get("run")):
            entry["error"] = "no run(api, args)"
        entry["desc"] = cap_head(namespace.get("DESCRIPTION", ""))
        entry["args"] = cap_head(namespace.get("ARGS", ""))
        ext = namespace.get("EXT", "bin")
        entry["ext"] = ext if valid_ext(ext) else "bin"
        return entry

    def with_libs(self, fn, *args):
        return with_libs(self.root, fn, *args)

    # -- runs

    def run(self, frame):
        name = frame.get("tool")
        args = frame.get("args") or []
        if not isinstance(args, list):
            self.error(frame, "bad_args", "args must be a list")
            return
        args = [str(a) for a in args]
        builtin = self.builtins.get(name)
        if builtin is not None:
            self.run_builtin(frame, builtin, args)
            return
        self.scan()
        entry = self.tools.get(name)
        if entry is None:
            self.error(frame, "unknown_tool", str(name))
            return
        if entry[1].get("error"):
            self.error(frame, "raised", entry[1]["error"])
            return
        self.run_custom(frame, name, args)

    def begin(self, frame, name):
        self.running = name
        self.run_rid = frame.get("rid")
        self.cancel_req = None
        self.stash = None
        self.reply(frame, {"t": "started", "tool": name, "hint": ""})

    def end(self, text):
        self.last = text
        self.running = None
        self.cancel_req = None
        self.runs += 1

    def result(self, name, files, saved=None, text=""):
        self.send({"t": "result", "tool": name, "ok": True, "saved": saved,
                   "files": files, "tethered": self.hooks.usb_connected(),
                   "text": text, "rid": self.run_rid})
        self.end("run %s -- %d file%s sent" % (name, files,
                                                "" if files == 1 else "s"))

    def run_builtin(self, frame, builtin, args):
        name = builtin.name
        self.begin(frame, name)
        try:
            data, saved, text = builtin.fn(args)
            files = 0
            if data is not None:
                Api(self, name, args).send(data, "json")
                files = 1
            self.result(name, files, saved, text)
        except Exception as error:   # noqa: BLE001 - the link must not stick
            self.send({"t": "error", "code": "raised",
                       "text": cap_tail(self.hooks.format_exc(error)),
                       "rid": self.run_rid})
            self.end("run %s -- raised" % name)

    def remove_tool(self, stem):
        """Delete one subtool, or failing that one library: None, or why
        not. Only a name the scan or the library list holds can go, so no
        path is built from what the host sent. One flash write."""
        self.scan()
        lib = stem not in self.tools
        if lib and stem not in self.libs():
            return "no such subtool"
        try:
            os.remove(self.path(stem, lib))
        except OSError as error:
            return "could not delete: %s" % error
        self.tools.pop(stem, None)
        return None

    def remove(self, frame):
        """`remove` deletes the named subtools it can and says why not for
        the rest."""
        names = frame.get("names")
        if not isinstance(names, list) or not names:
            self.error(frame, "bad_args", "name the subtools to remove")
            return
        removed = []
        errors = []
        for name in names:
            name = str(name)
            problem = self.remove_tool(name)
            if problem is None:
                removed.append(name)
            else:
                errors.append([name, problem])
        if removed:
            self.last = "removed " + ", ".join(removed)
        self.reply(frame, {"t": "removed", "removed": removed,
                           "errors": errors})

    def run_custom(self, frame, name, args):
        self.begin(frame, name)
        try:
            self.with_libs(self.custom, name, args)
        finally:
            # Whatever raised between begin() and the end, the link must not
            # stay busy for good.
            if self.running is not None:
                self.end("run %s -- ended" % name)
            # A hello stashed during the run is answered even if it raised.
            if self.stash is not None:
                stash = self.stash
                self.stash = None
                self.dispatch_safely(stash)

    def custom(self, name, args):
        api = Api(self, name, args)
        try:
            namespace = self.load(name, printer=_printer(api))
            entry = namespace.get("run")
        except (Exception, SystemExit) as error:   # noqa: BLE001 - reported
            self.send({"t": "error", "code": "raised",
                       "text": cap_tail(self.hooks.format_exc(error)),
                       "rid": self.run_rid})
            self.end("run %s -- failed to load" % name)
            return
        self.hooks.collect()
        self.hooks.status("Running %s\nB cancels" % name)
        outcome, value = self.guarded(entry, api, args)
        self.finish_custom(api, name, outcome, value)

    def guarded(self, entry, api, args):
        """run(api, args) with the hard cancel armed. Returns (outcome,
        value or error): "ok", "cancelled" or "raised". Disarming comes
        first of all the cleanup, and is retried past an interrupt that
        was already on its way, so no second KeyboardInterrupt can get out
        of here (and past run()'s `except Exception`)."""
        outcome = "raised"
        value = None
        done = False
        try:
            try:
                self.port.arm_cancel()
                value = entry(api, args)
                outcome = "ok"
            finally:
                done = self.disarm()
        except KeyboardInterrupt:
            outcome = "cancelled"
        except (Exception, SystemExit) as error:   # noqa: BLE001
            value = error
        if not done:
            self.disarm()
        return outcome, value

    def disarm(self):
        for _ in range(DISARM_TRIES):
            try:
                self.port.disarm_cancel()
                return True
            except KeyboardInterrupt:
                pass
        return False

    def finish_custom(self, api, name, outcome, value):
        """Cleanup in a pinned order: (disarmed already) the ring, the
        panel, the streams, the last frame, the collector."""
        try:
            api.ring_stop()
        except Exception:   # noqa: BLE001 - the ring must not keep the link
            self.ring_active = False
            api.ring = None
        try:
            self.hooks.restore()
        except Exception:   # noqa: BLE001
            pass
        # Decided once, before any tidying can poll B.
        stopped = api.stop
        returned = outcome == "ok" and stopped is None
        try:
            for stream in api.streams:
                stream.close(returned)
            if returned and value is not None:
                try:
                    payload = json.dumps(value).encode()
                except Exception as error:   # noqa: BLE001 - not JSON
                    outcome = "raised"
                    value = error
                else:
                    stream = Stream(api, name, "json")
                    api.streams.append(stream)
                    stream.write(payload)
                    stream.close()
            self.last_frame(name, outcome, value, stopped, len(api.streams))
        finally:
            if self.running is not None:
                self.end("run %s -- ended" % name)
            self.hooks.collect()

    def last_frame(self, name, outcome, value, stopped, files):
        rid = self.run_rid
        if outcome == "raised":
            self.send({"t": "error", "code": "raised",
                       "text": cap_tail(self.hooks.format_exc(value)),
                       "rid": rid})
            self.end("run %s -- raised" % name)
        elif outcome == "cancelled" or stopped is not None:
            by = stopped
            if outcome == "cancelled":
                by = "B" if self.port.b_fired else (self.cancel_req or "host")
            self.send({"t": "cancelled", "tool": name, "by": by, "rid": rid})
            self.end("run %s -- cancelled (%s)" % (name, by))
        else:
            self.result(name, files)

    # -- new

    def new(self, frame):
        """An upload: a subtool, or with `lib` a library module for them,
        which is compiled but not run."""
        filename = str(frame.get("file", ""))
        stem = filename[:-3] if filename.endswith(".py") else ""
        lib = bool(frame.get("lib"))
        if not valid_tool_name(stem) or not lib and stem in self.builtins:
            self.error(frame, "bad_name", filename)
            return
        size = frame.get("size")
        if not isinstance(size, int) or size <= 0:
            self.error(frame, "bad_args", "an empty file")
            return
        if size > NEW_MAX:
            self.error(frame, "too_big", "at most %d bytes" % NEW_MAX)
            return
        if not frame.get("force"):
            try:
                os.stat(self.path(stem, lib))
                self.error(frame, "exists", filename)
                return
            except OSError:
                pass
        self.upload = (stem, lib, size, frame.get("crc"), Assembler(size))
        self.reply(frame, {"t": "ack", "seq": -1})

    def data(self, frame):
        if self.upload is None:
            self.error(frame, "bad_frame", "no upload")
            return
        stem, lib, size, crc, assembler = self.upload
        problem = assembler.add(frame.get("seq"), frame.get("b", ""))
        if problem is not None:
            self.upload = None
            self.error(frame, "too_big" if problem == "too big"
                       else "bad_frame", problem)
            return
        if len(assembler.data) < size:
            self.reply(frame, {"t": "ack", "seq": frame.get("seq")})
            return
        self.upload = None
        problem = assembler.finish(size, crc)
        if problem is not None:
            self.error(frame, "crc", problem)
            return
        self.save_upload(frame, stem, lib, bytes(assembler.data))

    def save_upload(self, frame, stem, lib, raw):
        path = self.path(stem, lib)
        try:
            source = raw.decode()
        except UnicodeError:
            self.error(frame, "syntax", "not UTF-8")
            return
        try:
            compile(source, path, "exec")
        except SyntaxError as error:
            self.error(frame, "syntax", self.hooks.format_exc(error))
            return
        entry = None
        if not lib:
            entry = self.probe(stem, source)
            if entry["error"]:
                self.error(frame, "raised", entry["error"])
                return
        tmp = path + ".tmp"
        try:
            for folder in (self.root, self.root + "/" + LIB)[:1 + lib]:
                try:
                    os.stat(folder)
                except OSError:
                    os.mkdir(folder)
            with open(tmp, "wb") as handle:
                handle.write(raw)
            os.rename(tmp, path)
        except OSError as error:
            try:
                os.remove(tmp)
            except OSError:
                pass
            self.error(frame, "io", self.hooks.format_exc(error))
            return
        name = (LIB + "/" if lib else "") + stem + ".py"
        if not lib:
            self.tools.pop(stem, None)
            self.scan()
            listed = self.tools.get(stem)
            entry = listed[1] if listed else entry
        self.reply(frame, {"t": "saved", "file": name, "tool": entry})
        self.last = "new " + name

    # -- the end

    def close(self):
        """B on the link screen: a best-effort `bye`."""
        if self.session is not None and not self.lost:
            try:
                self.send({"t": "bye", "reason": "B", "rid": None})
            except Exception:   # noqa: BLE001 - leaving anyway
                pass
        self.session = None
        self.upload = None


def _no_print(*args):
    pass


# --- with no host: Dev Mode's own runs ----------------------------------------

def libs(root):
    try:
        names = os.listdir(root + "/" + LIB)
    except OSError:
        return []
    return sorted(n[:-3] for n in names if n.endswith(".py"))


def with_libs(root, fn, *args):
    """fn(*args) with root/lib on sys.path; the libraries it imported
    dropped after, so the next run loads a new upload and a transfer's
    heap does not keep them."""
    lib = root + "/" + LIB
    names = libs(root)
    sys.path.insert(0, lib)
    try:
        return fn(*args)
    finally:
        if lib in sys.path:
            sys.path.remove(lib)
        for name in names:
            sys.modules.pop(name, None)


def _load(root, stem):
    path = root + "/" + stem + ".py"
    with open(path) as handle:
        code = compile(handle.read(), path, "exec")
    namespace = {"__name__": stem, "print": _no_print}
    exec(code, namespace)
    return namespace


def menu_entries(root):
    """[(label, stem, args)] for every stored subtool that lists a MENU, by
    name; one that fails to load is left out."""
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    out = []
    for filename in names:
        stem = filename[:-3]
        if not filename.endswith(".py") or not valid_tool_name(stem):
            continue
        try:
            menu = with_libs(root, _load, root, stem).get("MENU") or ()
            for label, args in menu:
                out.append((cap_head(label, 40), stem,
                            [str(a) for a in args]))
        except (Exception, SystemExit):     # noqa: BLE001 - not listed
            pass
    return out


def clear_tools(root):
    """Every stored subtool and library deleted: True if none is left."""
    left = False
    for folder in (root + "/" + LIB, root):
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            if name == LIB:
                continue
            try:
                os.remove(folder + "/" + name)
            except OSError:
                left = True
    return not left


class _NullStream:
    """A stream with no host: the data goes nowhere."""

    def write(self, data):
        pass

    def close(self, ok=True):
        pass

    def __enter__(self):
        return self

    def __exit__(self, kind, value, trace):
        pass


class LocalApi(Api):
    """The Api of a run Dev Mode starts itself: no host, so what is sent
    goes nowhere -- the subtool's api.save() keeps its result -- and B held
    is the only cancel."""

    def __init__(self, hooks, name, args):
        self.server = None
        self.hooks = hooks
        self.name = name
        self.args = args
        self.streams = []
        self.ring = None
        self.dropped = 0
        self.stop = None
        self.ticks_us = hooks.ticks_us
        self.ticks_ms = hooks.ticks_ms
        self.backlight = hooks.backlight
        self.present = hooks.present

    def stream(self, ext=None, name=None):     # noqa: ARG002 - Api's signature
        self.writing()
        return _NullStream()

    def send(self, data, ext=None, name=None):     # noqa: ARG002 - Api's signature
        self.writing()

    def log(self, text):
        pass

    def cancelled(self):
        if self.stop is None:
            self.hooks.poll()
            if self.hooks.b_held():
                self.stop = "B"
        return self.stop is not None

    def ring_start(self):
        if self.ring is None:
            reader = self.hooks.ring()
            if reader is None:
                return None
            reader.start()
            self.ring = reader
        return self.ring

    def ring_stop(self):
        reader = self.ring
        if reader is not None:
            self.ring = None
            reader.stop()


def run_local(root, hooks, stem, args):
    """Runs a stored subtool with no host: ("ok", value), ("cancelled",
    None) or ("raised", error). The ring is stopped and the panel restored
    whatever happened."""
    api = LocalApi(hooks, stem, args)

    def call():
        return _load(root, stem)["run"](api, args)
    try:
        value = with_libs(root, call)
        outcome = "cancelled" if api.stop is not None else "ok"
    except (Exception, SystemExit) as error:   # noqa: BLE001 - reported
        outcome, value = "raised", error
    try:
        api.ring_stop()
    finally:
        hooks.restore()
        hooks.collect()
    return outcome, value


def _printer(api):
    def printer(*args):
        api.log(" ".join([str(a) for a in args]))
    return printer
