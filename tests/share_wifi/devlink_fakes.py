"""Fakes for the USB dev link's tests: a port, the badge's hooks, and a way to
talk to a Server as the host would. Not a test file; the runner skips it."""

import os
import tempfile
import traceback

from harness import load_module

dl = load_module("share_wifi", "devlink")
ds = load_module("share_wifi", "devserver")


class FakePort:
    """Byte queues for both directions. `short` makes writes come back
    short; `interrupt_disarm` raises KeyboardInterrupt from the next
    disarm_cancel() calls, as an interrupt already on its way would."""

    def __init__(self):
        self.inbox = bytearray()
        self.outbox = bytearray()
        self.short = False
        self.armed = False
        self.arms = 0
        self.disarms = 0
        self.b_fired = False
        self.interrupt_disarm = 0
        self.reads = 0
        self.writes = 0
        self.on_write = None

    def read(self, limit=2048):
        self.reads += 1
        data = bytes(self.inbox[:limit])
        del self.inbox[:limit]
        return data

    def write(self, data):
        self.writes += 1
        if self.on_write is not None:
            self.on_write(data)
        if self.short:
            return False
        self.outbox.extend(data)
        return True

    def arm_cancel(self):
        self.arms += 1
        self.armed = True
        self.b_fired = False

    def disarm_cancel(self):
        if self.interrupt_disarm:
            self.interrupt_disarm -= 1
            raise KeyboardInterrupt
        self.disarms += 1
        self.armed = False


class FakeReader:
    def __init__(self):
        self.running = False
        self.starts = 0

    def start(self):
        self.running = True
        self.starts += 1

    def stop(self):
        self.running = False


class Hooks:
    def __init__(self):
        self.firmware = "test"
        self.connected = True
        self.held = False
        self.polls = 0
        self.statuses = []
        self.restores = 0
        self.ring_reader = FakeReader()
        self.temp = lambda: 30.0
        self.light = lambda: 400
        self.ticks_us = lambda: 0
        self.ticks_ms = lambda: 0
        self.backlight = lambda v: None
        self.present = lambda: None
        self.collect = lambda: None
        self.saved = []

    def save(self, tool, entry):
        self.saved.append((tool, entry))
        return True

    def usb_connected(self):
        return self.connected

    def poll(self):
        self.polls += 1

    def b_held(self):
        return self.held

    def status(self, text):
        self.statuses.append(text)

    def restore(self):
        self.restores += 1

    def ring(self):
        return self.ring_reader

    def ring_note(self):
        return None

    def format_exc(self, error):
        return "".join(traceback.format_exception(type(error), error,
                                                  error.__traceback__))


class Host:
    """The host's half, minimal: frames in, frames out."""

    def __init__(self, server, port):
        self.server = server
        self.port = port
        self.reader = dl.LineReader(dl.LINE_MAX_HOST)
        self.rid = 0

    def send(self, obj, raw=b""):
        if "rid" not in obj:
            self.rid += 1
            obj["rid"] = self.rid
        self.port.inbox.extend(raw + dl.encode(obj))
        return obj["rid"]

    def frames(self):
        out = [f for f in self.reader.feed(bytes(self.port.outbox))
               if isinstance(f, dict)]
        self.port.outbox = bytearray()
        return out

    def ask(self, obj, raw=b""):
        self.send(obj, raw)
        self.server.service()
        return self.frames()

    def hello(self, nonce="n1"):
        return self.ask({"t": "hello", "id": nonce, "proto": dl.PROTO})


def builtin(name, record, data=None, saved=None, text=""):
    """An instant built-in that records its calls and gives back `data`."""
    def fn(args):
        record.append((name, args))
        return data, saved, text
    return ds.Builtin(name, name + " desc", fn)


def server(files=None, builtins=None):
    """A Server over a FakePort and a temp root holding `files`."""
    root = tempfile.mkdtemp(prefix="devtests")
    for name, text in (files or {}).items():
        with open(os.path.join(root, name), "w") as handle:
            handle.write(text)
    port = FakePort()
    hooks = Hooks()
    srv = ds.Server(port, root, hooks, builtins or {})
    srv.scan()
    return srv, port, hooks, Host(srv, port)


def streams(frames):
    """{sid: (open frame, bytes, end frame)} from a run's frames."""
    out = {}
    for f in frames:
        if f["t"] == "open":
            out[f["sid"]] = [f, b"", None]
        elif f["t"] == "chunk":
            out[f["sid"]][1] += dl.unb64(f["b"])
        elif f["t"] == "end":
            out[f["sid"]][2] = f
    return out
