#!/usr/bin/env python3
"""Host companion for share_wifi's USB dev link (Dev Mode > USB dev link).

    sudo apt install python3-serial   (or, in a venv: pip install pyserial)
    python3 tests/share_wifi/devlink_host.py OUTDIR [--port PORT] [--tools DIR]

Connects to the badge over USB, lists its tools, runs them, uploads
subtools to /devtests/ and their libraries to /devtests/lib/ (`sync` sends
every one in --tools DIR and DIR/lib), removes them, and saves every file
the badge sends as OUTDIR/<subtest>.<YYYYmmdd-HHMMSS>.<ext>. Type `help` at
the prompt.

Or give one command after OUTDIR to run it and exit, for scripts:

    python3 tests/share_wifi/devlink_host.py OUTDIR sync
    python3 tests/share_wifi/devlink_host.py OUTDIR run frame_probe
    python3 tests/share_wifi/devlink_host.py OUTDIR --yes remove spin hello

Exit status: 0 done; 1 the command failed, was cancelled (Ctrl-C during a
run included) or was not confirmed; 2 bad arguments, an unknown command
included; 3 no badge or no link; 130 interrupted outside a run.

Host-only, so it lives here and not under firmware/ (everything there ships).
It imports the wire format from firmware/apps/share_wifi/devlink.py, the same
file the badge runs. pyserial is imported only to open a port, so the tests
need none.

Specification: "USB dev link" in .agents/share_wifi/app-spec.md.
"""

import argparse
import ast
import cmd
import importlib.util
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
APP = os.path.join(REPO, "firmware", "apps", "share_wifi")
TOOLS = os.path.join(HERE, "devtests")


def _load_devlink():
    # Everything under firmware/ ships: loading devlink.py from there must
    # not leave a __pycache__ beside it.
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(
        "devlink", os.path.join(APP, "devlink.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dl = _load_devlink()

VID, PID = 0x2E8A, 0x1101          # board/mpconfigboard.h, v2 and v3
HELLO_WAIT_S = 2.0
REPLY_WAIT_S = 5.0
COMPILE_WAIT_S = 30.0
SETTLE_S = 0.05                    # the firmware flushes 16 SOFs after DTR
CTRL_C = b"\x03"


class Disconnected(Exception):
    """The port went away: unplugged, reset, or closed under us."""


class NoReply(Exception):
    """No reply in time."""


class NoBadge(Exception):
    """No port to open: none found, several, busy, or refused."""


# --- saving ------------------------------------------------------------------

class Saver:
    """Files from the badge, as OUTDIR/<subtest>.<timestamp>.<ext>.

    Written as <final>.part and renamed only when the stream's end matches
    its size and CRC; anything else leaves the .part, and says why. Names
    and extensions come from the badge, so both are checked here and the
    path is confirmed to be inside OUTDIR: the host never trusts the peer
    with a path."""

    def __init__(self, outdir, out=print, clock=time.localtime):
        self.outdir = os.path.realpath(outdir)
        os.makedirs(self.outdir, exist_ok=True)
        self.out = out
        self.clock = clock
        self.open = {}              # sid -> [handle, final, size, crc, seq]

    def start(self, sid, name, ext):
        if not dl.valid_subtest(name):
            self.out("badge sent a bad name %r: saving as 'unnamed'" % name)
            name = "unnamed"
        if not dl.valid_ext(ext):
            self.out("badge sent a bad extension %r: saving as .bin" % ext)
            ext = "bin"
        stamp = time.strftime("%Y%m%d-%H%M%S", self.clock())
        n = 1
        while True:
            suffix = "" if n == 1 else "-%d" % n
            final = os.path.join(self.outdir, "%s.%s%s.%s" % (name, stamp,
                                                              suffix, ext))
            if os.path.dirname(os.path.realpath(final)) != self.outdir:
                raise ValueError("refusing a path outside OUTDIR: %s" % final)
            if os.path.exists(final):
                n += 1
                continue
            try:
                handle = open(final + ".part", "xb")
            except FileExistsError:
                n += 1
                continue
            break
        self.open[sid] = [handle, final, 0, 0, 0]
        return final

    def chunk(self, sid, seq, text):
        entry = self.open.get(sid)
        if entry is None:
            return                  # a stream this session never opened
        if entry[4] is None:
            return                  # already broken
        if seq != entry[4]:
            self.abort(sid, "chunk %s missing" % entry[4])
            return
        raw = dl.unb64(text)
        entry[0].write(raw)
        entry[2] += len(raw)
        entry[3] = dl.crc32(raw, entry[3])
        entry[4] += 1

    def end(self, sid, size, crc, ok):
        entry = self.open.pop(sid, None)
        if entry is None:
            return None
        handle, final, got, got_crc, seq = entry
        handle.close()
        if seq is None:
            return None
        if not ok:
            return self._keep(final, "the badge ended it early")
        if got != size or got_crc != crc:
            return self._keep(final, "size or CRC wrong (%d of %d bytes)"
                              % (got, size))
        os.replace(final + ".part", final)
        self.out("saved %s (%d bytes)" % (final, got))
        return final

    def abort(self, sid, why):
        entry = self.open.get(sid)
        if entry is None or entry[4] is None:
            return
        entry[0].close()
        entry[4] = None
        self._keep(entry[1], why)

    def abort_all(self, why):
        for sid in list(self.open):
            self.abort(sid, why)
        self.open = {}

    def _keep(self, final, why):
        self.out("INCOMPLETE %s.part: %s" % (final, why))
        return None


# --- the link ------------------------------------------------------------------

class Client:
    """The host's side of the link over `port`: anything with read(n),
    write(b), reset_input_buffer() and close() -- a pyserial Serial, or a
    test's fake."""

    def __init__(self, port, saver, out=print, clock=time.monotonic,
                 confirm=input):
        self.port = port
        self.confirm = confirm
        self.saver = saver
        self.out = out
        self.clock = clock
        self.reader = dl.LineReader(dl.LINE_MAX_HOST)
        self.rid = 0
        self.info = None
        self.stale = True
        self.tools = []
        self.queue = []
        # The custom run this host stopped waiting for, which may still be
        # going, or None. Only for it is a raw 0x03 ever sent outside a run
        # being waited on -- and then only after no reply and the user's
        # say-so: 0x03 is noise to an open link but Ctrl-C to an app whose
        # link is closed, or to the launcher after a reset.
        self.abandoned = None

    # -- raw

    def gone(self, error):
        """The port failed: whatever ran is gone or out of reach."""
        self.abandoned = None
        self.stale = True
        return Disconnected(str(error))

    def write(self, data):
        try:
            self.port.write(data)
        except (OSError, ValueError) as error:
            raise self.gone(error) from error

    def send(self, obj, prefix=b""):
        self.rid += 1
        obj["rid"] = self.rid
        self.write(prefix + dl.encode(obj))
        return self.rid

    def next_frame(self, deadline):
        """The next frame, showing noise as it passes; NoReply at the
        deadline (None: wait for ever)."""
        while True:
            if self.queue:
                return self.queue.pop(0)
            if deadline is not None and self.clock() > deadline:
                raise NoReply()
            try:
                data = self.port.read(4096)
            except (OSError, ValueError) as error:
                raise self.gone(error) from error
            for item in self.reader.feed(data or b""):
                if isinstance(item, dict):
                    self.queue.append(item)
                elif isinstance(item, dl.Noise):
                    self.out("badge: " + item.text)

    def reply(self, rid, wait=REPLY_WAIT_S):
        """The frame answering `rid`; anything else is a late reply to an
        earlier request and is dropped."""
        deadline = None if wait is None else self.clock() + wait
        while True:
            frame = self.next_frame(deadline)
            if frame.get("rid") == rid:
                return frame

    # -- session

    def hello(self):
        """A fresh session: a plain hello first. Only if that gets no reply
        while a custom run this host abandoned may still be going, and the
        user agrees, does a second go out behind a raw 0x03, which
        hard-cancels that run."""
        try:
            frame, rid = self.hello_once(b"")
        except NoReply:
            if self.abandoned is None or not self.agree(
                    "The badge is not answering. If it is still running %s, "
                    "send Ctrl-C to stop it? (If the link has been closed "
                    "this stops the app.) [y/N] " % self.abandoned):
                raise NoReply("no reply: the badge may be mid-test (it "
                              "answers at its next prompt), or Dev Mode > "
                              "USB dev link is not open") from None
            self.abandoned = None
            frame, rid = self.hello_once(CTRL_C)
        self.info = frame
        self.stale = False
        self.abandoned = None
        self.out("badge: %s, %s, %s" % (
            frame.get("firmware"), frame.get("app"),
            "tethered" if frame.get("tethered") else "not tethered"))
        return frame

    def hello_once(self, prefix):
        try:
            self.port.reset_input_buffer()
        except (OSError, ValueError) as error:
            raise self.gone(error) from error
        self.queue = []
        self.reader = dl.LineReader(dl.LINE_MAX_HOST)
        nonce = os.urandom(4).hex()
        rid = self.send({"t": "hello", "id": nonce, "proto": dl.PROTO},
                        prefix)
        deadline = self.clock() + HELLO_WAIT_S
        while True:
            frame = self.next_frame(deadline)
            if frame["t"] == "error" and frame.get("code") == "proto" \
                    and frame.get("rid") == rid:
                raise RuntimeError("protocol mismatch: %s; this host speaks "
                                   "%d" % (frame.get("text"), dl.PROTO))
            if frame["t"] == "hello" and frame.get("id") == nonce:
                return frame, rid

    def agree(self, question):
        try:
            return self.confirm(question).strip().lower() == "y"
        except EOFError:
            return False

    def ensure(self):
        """A session, or False: a hello that adopted a run the user then
        stopped waiting for leaves none, and the command is not sent."""
        if self.stale:
            self.hello()
        return not self.stale

    def request(self, obj, wait=REPLY_WAIT_S):
        """Send one frame and return its reply. `no_session` means the badge
        lost ours: say hello again but do NOT resend -- an automatic retry
        of `run clear` is exactly what the session rule exists to stop."""
        if not self.ensure():
            self.out("not sent: say it again once the badge answers")
            return None
        rid = self.send(obj)
        try:
            frame = self.reply(rid, wait)
        except NoReply:
            self.stale = True
            raise
        if frame["t"] == "error" and frame.get("code") == "no_session":
            self.stale = True
            self.hello()
            self.out("the badge had no session; reconnected. Re-issue the "
                     "command.")
            return None
        return frame

    def list(self):
        frame = self.request({"t": "list"})
        if frame is not None and frame["t"] == "tools":
            self.tools = frame["tools"]
        return frame

    def remove(self, names):
        """Delete subtools (or libraries) from /devtests/: the badge
        removes those it can and says why not for the rest (`removed`).
        The tool list is refreshed after, for completion."""
        frame = self.request({"t": "remove", "names": list(names)})
        if frame is not None and frame["t"] == "removed":
            try:
                self.list()
            except (NoReply, Disconnected):
                pass
        return frame

    def cancel(self, hard=False):
        """Cancel whatever runs: the frame, and with `hard` a raw 0x03 that
        interrupts a custom run that never polls. Never answered on its own."""
        self.write(dl.encode({"t": "cancel", "rid": None})
                   + (CTRL_C if hard else b""))

    def cancel_abandoned(self):
        """The shell's `cancel`: the frame, and the 0x03 only for a custom
        run this host abandoned, and only if the user agrees."""
        hard = self.abandoned is not None and self.agree(
            "Send Ctrl-C to stop %s? (If the link has been closed this stops "
            "the app.) [y/N] " % self.abandoned)
        self.cancel(hard)
        if hard:
            self.abandoned = None

    def run(self, tool, args):
        frame = self.request({"t": "run", "tool": tool, "args": list(args)})
        if frame is None:
            return None
        if frame["t"] != "started":
            return frame
        return self.pump(frame["rid"], tool)

    def pump(self, rid, tool):
        """A run's frames until its last: files saved as they come. Ctrl-C
        once cancels it, with a raw 0x03 that interrupts a run that never
        polls; twice stops waiting."""
        interrupts = 0
        while True:
            try:
                frame = self.reply(rid, None)
                if self.handle(frame, tool):
                    return frame
            except KeyboardInterrupt:
                interrupts += 1
                self.cancel(hard=True)
                if interrupts == 1:
                    self.out("cancel sent: the badge stops at its next check "
                             "or prompt (B on the badge is immediate). "
                             "Ctrl-C again to stop waiting.")
                    continue
                self.saver.abort_all("stopped waiting")
                self.stale = True
                self.abandoned = tool
                self.out("stopped waiting for %s" % tool)
                return None
            except Disconnected:
                self.saver.abort_all("the badge went away")
                raise

    def handle(self, frame, tool):
        """One frame of a run; True for its last."""
        kind = frame["t"]
        if kind == "open":
            self.saver.start(frame["sid"], frame.get("name", ""),
                             frame.get("ext", ""))
        elif kind == "chunk":
            self.saver.chunk(frame["sid"], frame.get("seq"),
                             frame.get("b", ""))
        elif kind == "end":
            self.saver.end(frame["sid"], frame.get("size"),
                           frame.get("crc"), frame.get("ok"))
        elif kind in ("log", "status"):
            self.out("%s: %s" % (tool, frame.get("text", "")))
        elif kind in ("result", "cancelled", "error"):
            self.saver.abort_all("the run ended without closing it")
            return True
        return False

    def upload(self, path, force=False, lib=False):
        with open(path, "rb") as handle:
            raw = handle.read()
        name = os.path.basename(path)
        frame = self.request({"t": "new", "file": name, "size": len(raw),
                              "crc": dl.crc32(raw), "force": bool(force),
                              "lib": bool(lib)})
        if frame is None or frame["t"] != "ack":
            return frame
        try:
            for seq, at in enumerate(range(0, len(raw), dl.CHUNK_IN)):
                # The last chunk's reply waits on the badge compiling the
                # whole file: a 60 KB library takes seconds.
                last = at + dl.CHUNK_IN >= len(raw)
                frame = self.request({"t": "data", "seq": seq,
                                      "b": dl.b64(raw[at:at + dl.CHUNK_IN])},
                                     COMPILE_WAIT_S if last else REPLY_WAIT_S)
                if frame is None or frame["t"] != "ack":
                    return frame
        except KeyboardInterrupt:
            self.stale = True       # the next hello drops the partial upload
            self.out("upload stopped")
            return None
        return frame

    def bye(self):
        try:
            if not self.stale:
                self.request({"t": "bye"}, wait=1.0)
        except (NoReply, Disconnected):
            pass


# --- the shell ---------------------------------------------------------------

def describe(frame):
    kind = frame["t"]
    if kind == "result":
        return "done: %s file%s%s%s" % (
            frame.get("files"), "" if frame.get("files") == 1 else "s",
            {True: ", saved on the badge", False: ", NOT saved on the badge",
             None: ""}[frame.get("saved")],
            (" -- " + frame["text"]) if frame.get("text") else "")
    if kind == "cancelled":
        return "cancelled (by %s)" % frame.get("by")
    if kind == "error":
        return "error %s: %s" % (frame.get("code"), frame.get("text"))
    if kind == "saved":
        return "saved %s on the badge" % frame.get("file")
    if kind == "removed":
        lines = ["removed: %s" % (", ".join(frame.get("removed") or [])
                                  or "nothing")]
        for name, why in frame.get("errors") or []:
            lines.append("error: %s: %s" % (name, why))
        return "\n".join(lines)
    return str(frame)


def local_tools(directory):
    """(name, description) of each subtool in `directory`, read by ast:
    a host never executes them."""
    out = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return out
    for filename in names:
        if not filename.endswith(".py"):
            continue
        desc = ""
        try:
            tree = ast.parse(open(os.path.join(directory, filename)).read())
            for node in tree.body:
                if (isinstance(node, ast.Assign) and len(node.targets) == 1
                        and getattr(node.targets[0], "id", "") == "DESCRIPTION"
                        and isinstance(node.value, ast.Constant)):
                    desc = str(node.value.value)
        except (OSError, SyntaxError) as error:
            desc = "(%s)" % type(error).__name__
        out.append((filename[:-3], desc))
    return out


class Shell(cmd.Cmd):
    prompt = "badge> "
    intro = None

    def __init__(self, client, tools_dir=TOOLS, out=print, confirm=input,
                 reconnect=None):
        cmd.Cmd.__init__(self)
        self.ok = True              # the last command's outcome, for scripts
        self.usage = False          # ... and whether it was badly asked
        self.client = client
        self.tools_dir = tools_dir
        self.say = out
        self.confirm = confirm
        self.reconnector = reconnect

    def safely(self, action):
        try:
            return action()
        except Disconnected as error:
            self.say("badge disconnected (%s). `reconnect` when it is back."
                     % error)
            self.client.stale = True
        except NoReply as error:
            self.say(str(error) or "no reply from the badge")
            self.client.stale = True
        except NoBadge as error:
            self.say("%s. `reconnect` to try again." % error)
            self.client.stale = True
        except (RuntimeError, OSError, ValueError) as error:
            self.say(str(error))
            self.client.stale = True
        self.ok = False
        return None

    def onecmd(self, line):
        self.ok = True
        self.usage = False
        return cmd.Cmd.onecmd(self, line)

    def default(self, line):
        self.misused("unknown command: %s (`help` lists them)"
                     % line.split()[0])

    def misused(self, text):
        self.fail(text)
        self.usage = True

    def fail(self, text):
        self.say(text)
        self.ok = False

    def agreed(self, question):
        try:
            answer = self.confirm(question)
        except EOFError:
            answer = ""
        if answer.strip().lower() == "y":
            return True
        self.fail("not confirmed: nothing done")
        return False

    def show(self, frame):
        """Print a command's last frame; a failure, a cancel, a removal
        that missed a name or no frame at all count as not ok."""
        if frame is None:
            self.ok = False
            return
        for line in describe(frame).split("\n"):
            self.say(line)
        if frame["t"] in ("error", "cancelled") or (
                frame["t"] == "removed" and frame.get("errors")):
            self.ok = False

    def emptyline(self):
        pass

    def do_list(self, _arg):
        """list -- the badge's tools"""
        frame = self.safely(self.client.list)
        if frame is None or frame["t"] != "tools":
            self.show(frame)
            self.ok = False
            return
        if frame.get("libs"):
            self.say("  libraries: " + ", ".join(frame["libs"]))
        for tool in frame["tools"]:
            kind = "built-in" if tool.get("builtin") else "file"
            args = (" " + tool["args"]) if tool.get("args") else ""
            line = "  %-14s %-8s %s%s" % (tool["name"], kind,
                                          tool.get("desc", ""), args)
            if tool.get("error"):
                line += "  [failed to load: %s]" % tool["error"]
            self.say(line)

    def do_local(self, _arg):
        """local -- the subtools in --tools DIR, to send with `new`"""
        for name, desc in local_tools(self.tools_dir):
            self.say("  %-14s %s" % (name, desc))

    def do_run(self, arg):
        """run <tool> [args...] -- run a tool; files it sends are saved"""
        words = arg.split()
        if not words:
            self.misused("run what? `list` shows the tools")
            return
        if words[0] == "clear" and not self.agreed(
                "Delete the saved measurements on the badge? [y/N] "):
            return
        self.show(self.safely(lambda: self.client.run(words[0], words[1:])))

    def complete_run(self, text, *_ignored):
        return [t["name"] for t in self.client.tools
                if t["name"].startswith(text)]

    def do_new(self, arg):
        """new <name or path> [--force] [--lib] -- upload a subtool to
        /devtests/, or with --lib a library to /devtests/lib/"""
        words = arg.split()
        force = "--force" in words
        lib = "--lib" in words
        words = [w for w in words if w not in ("--force", "--lib")]
        if not words:
            self.misused("new what? `local` lists the subtools here")
            return
        path = words[0]
        if not os.path.exists(path):
            folder = os.path.join(self.tools_dir, "lib") if lib \
                else self.tools_dir
            candidate = os.path.join(folder, path
                                     if path.endswith(".py") else path + ".py")
            if os.path.exists(candidate):
                path = candidate
            else:
                self.fail("no such file: %s" % path)
                return
        self.show(self.safely(lambda: self.client.upload(path, force, lib)))

    def do_sync(self, _arg):
        """sync -- upload every subtool in --tools DIR and every library in
        DIR/lib, replacing what the badge has"""
        jobs = [(os.path.join(self.tools_dir, "lib", n + ".py"), True)
                for n, _ in local_tools(os.path.join(self.tools_dir, "lib"))]
        jobs += [(os.path.join(self.tools_dir, n + ".py"), False)
                 for n, _ in local_tools(self.tools_dir)]
        for path, lib in jobs:
            frame = self.safely(lambda: self.client.upload(path, True, lib))
            if frame is None or frame["t"] != "saved":
                self.say("%s:" % os.path.basename(path))
                self.show(frame)
                self.ok = False
                return
            self.say("saved %s" % frame.get("file"))

    def do_remove(self, arg):
        """remove name... -- delete subtools or libraries from the badge's
        /devtests/, after a y/N (--yes skips it)"""
        names = arg.split()
        if not names:
            self.misused("remove what? `list` shows the subtools")
            return
        if not self.agreed("Remove %s from the badge? [y/N] "
                           % ", ".join(names)):
            return
        self.show(self.safely(lambda: self.client.remove(names)))

    def complete_remove(self, text, *_ignored):
        return [t["name"] for t in self.client.tools
                if not t.get("builtin") and t["name"].startswith(text)]

    def complete_new(self, text, *_ignored):
        return [n for n, _ in local_tools(self.tools_dir)
                if n.startswith(text)]

    def do_cancel(self, _arg):
        """cancel -- cancel a run this host stopped waiting for"""
        self.safely(self.client.cancel_abandoned)

    def do_reconnect(self, _arg):
        """reconnect -- find the badge again and say hello"""
        if self.reconnector is not None:
            self.safely(self.reconnector)
        else:
            self.safely(self.client.hello)

    def do_quit(self, _arg):
        """quit -- say bye and close"""
        self.client.bye()
        return True

    do_exit = do_quit

    def do_EOF(self, _arg):          # noqa: N802 - cmd's name for Ctrl-D
        self.say("")
        return self.do_quit(_arg)


def find_port():
    from serial.tools import list_ports
    found = [p.device for p in list_ports.comports()
             if p.vid == VID and p.pid == PID]
    if len(found) == 1:
        return found[0]
    if not found:
        raise NoBadge("no badge found (USB %04x:%04x); plug it in or give "
                      "--port" % (VID, PID))
    raise NoBadge("several badges: %s; choose one with --port"
                  % ", ".join(found))


def open_port(name):
    try:
        import serial
    except ImportError:
        raise NoBadge("this needs pyserial: sudo apt install python3-serial, "
                      "or pip install pyserial in a venv") from None
    try:
        port = serial.Serial(name, timeout=0.2, write_timeout=2,
                             exclusive=True)
        port.dtr = True
        time.sleep(SETTLE_S)
        port.reset_input_buffer()
    except (OSError, ValueError) as error:  # SerialException is an OSError
        raise NoBadge("cannot open %s: %s" % (name, error)) from None
    return port


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Talk to share_wifi's USB dev link.")
    parser.add_argument("outdir", help="where files from the badge are saved")
    parser.add_argument("--port", help="serial port (default: find the badge)")
    parser.add_argument("--tools", default=TOOLS,
                        help="local subtools for `new` and `local`")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="answer yes to `remove` and `run clear`")
    parser.add_argument("command", nargs=argparse.REMAINDER,
                        help="one command and its arguments: run it and exit")
    args = parser.parse_args(argv)
    # Options may also follow OUTDIR, before the command.
    command = list(args.command)
    while command and command[0].startswith("-"):
        option = command.pop(0)
        if option in ("-y", "--yes"):
            args.yes = True
        elif option in ("--port", "--tools") and command:
            setattr(args, option[2:], command.pop(0))
        else:
            parser.error("unknown option before the command: %s" % option)
    args.command = command

    def out(text):
        print(text)

    saver = Saver(args.outdir, out=out)
    holder = {}
    # Nobody to ask on a stdin that is not a terminal: no.
    if sys.stdin.isatty():
        ask = input
    else:
        ask = lambda question: "n"                # noqa: E731
    # --yes answers `remove` and `run clear`, never the client's "send
    # Ctrl-C to a run left going?", which could stop the app.
    confirm = (lambda question: "y") if args.yes else ask   # noqa: E731

    def connect():
        old = holder.get("port")
        if old is not None:
            try:
                old.close()
            except OSError:
                pass
        # A new port is a replug or a reset: no run of ours survives it.
        client.abandoned = None
        holder["port"] = open_port(args.port or find_port())
        client.port = holder["port"]
        client.info = None
        client.hello()

    client = Client(None, saver, out=out, confirm=ask)
    try:
        connect()
    except (NoReply, NoBadge, Disconnected, RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr)
        close(holder)
        return EXIT_NO_LINK
    except KeyboardInterrupt:
        close(holder)
        return EXIT_INTERRUPTED
    shell = Shell(client, args.tools, out=out, confirm=confirm,
                  reconnect=connect)
    if args.command:
        return scripted(shell, client, saver, holder, args.command)
    shell.say("commands: " + ", ".join(
        n[3:] for n in dir(shell) if n.startswith("do_") and n != "do_EOF"))
    shell.do_list("")
    while True:
        try:
            shell.cmdloop()
            break
        except KeyboardInterrupt:
            # Anywhere outside a run's wait (a list, a new, a hello): the
            # badge may hold a pending upload, so the next command says
            # hello first, which drops it; a file half written is reported.
            client.stale = True
            saver.abort_all("interrupted")
            print("^C  (quit or Ctrl-D to leave)")
    close(holder)
    return EXIT_OK


EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_NO_LINK = 3
EXIT_INTERRUPTED = 130


def close(holder):
    try:
        holder["port"].close()
    except (OSError, KeyError):
        pass


def scripted(shell, client, saver, holder, command):
    """One command from the command line, as typed at the prompt, then
    exit: 0 if it did what it was asked, 2 if it was badly asked, 1 if
    not."""
    try:
        shell.onecmd(" ".join(command))
        ok = shell.ok
        if command[0] not in ("quit", "exit"):
            client.bye()
    except KeyboardInterrupt:
        saver.abort_all("interrupted")
        close(holder)
        return EXIT_INTERRUPTED
    close(holder)
    if shell.usage:
        return EXIT_USAGE
    return EXIT_OK if ok else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
