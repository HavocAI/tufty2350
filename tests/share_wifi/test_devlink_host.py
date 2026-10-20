"""The host companion (devlink_host.py) against a fake serial port: the
session, saving and its naming rules, a hostile name, interrupts, a badge
that goes away, and the shell."""

import os
import tempfile

import devlink_host as dh
from devlink_fakes import dl


class Clock:
    """Moves on 0.1 s each time it is read, so deadlines pass at once."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 0.1
        return self.t


class FakeSerial:
    """Answers through `respond(frame) -> [frames or raw bytes]`; raises
    what `fail` holds from the next read."""

    def __init__(self, respond=None):
        self.respond = respond or (lambda frame: [])
        self.inbox = bytearray()
        self.written = bytearray()
        self.reader = dl.LineReader(dl.LINE_MAX_HOST)
        self.fail = None
        self.interrupts = 0

    def write(self, data):
        self.written.extend(data)
        for item in self.reader.feed(data):
            if isinstance(item, dict):
                for out in self.respond(item):
                    self.inbox.extend(out if isinstance(out, bytes)
                                      else dl.encode(out))

    def read(self, n):
        if self.interrupts and not self.inbox:     # Ctrl-C while waiting
            self.interrupts -= 1
            raise KeyboardInterrupt
        if self.fail is not None:
            raise self.fail
        data = bytes(self.inbox[:n])
        del self.inbox[:n]
        return data

    def reset_input_buffer(self):
        self.inbox = bytearray()

    def close(self):
        pass


def badge(extra=None):
    """A responder that says hello and hands anything else to `extra`."""
    def respond(frame):
        if frame["t"] == "hello":
            return [{"t": "hello", "id": frame["id"], "rid": frame["rid"],
                     "proto": dl.PROTO, "firmware": "BW", "app": "x",
                     "tethered": True, "running": None}]
        return extra(frame) if extra else []
    return respond


def client(respond=None, confirm=lambda question: "n"):
    out = []
    saver = dh.Saver(os.path.join(tempfile.mkdtemp(prefix="out"), "out"),
                     out=out.append,
                     clock=lambda: (2026, 10, 8, 12, 0, 0, 0, 0, 0))
    port = FakeSerial(respond or badge())
    c = dh.Client(port, saver, out=out.append, clock=Clock(),
                  confirm=confirm)
    return c, port, saver, out


def run_reply(chunks, end=None, sid=1, name="spin", ext="csv"):
    def extra(frame):
        if frame["t"] != "run":
            return []
        rid = frame["rid"]
        data = b"".join(chunks)
        out = [{"t": "started", "tool": "x", "hint": "", "rid": rid},
               {"t": "open", "sid": sid, "name": name, "ext": ext,
                "rid": rid}]
        for seq, piece in enumerate(chunks):
            out.append({"t": "chunk", "sid": sid, "seq": seq,
                        "b": dl.b64(piece), "rid": rid})
        out.append(end or {"t": "end", "sid": sid, "size": len(data),
                           "crc": dl.crc32(data), "ok": True, "rid": rid})
        out.append({"t": "result", "tool": "x", "ok": True, "files": 1,
                    "saved": None, "rid": rid})
        return out
    return extra


def test_hello_ignores_noise_and_stale_frames_before_its_echo():
    def respond(frame):
        return [b"- ERROR: old\n",
                dl.encode({"t": "result", "rid": 99}),
                dl.encode({"t": "hello", "id": "someone-else", "rid": 1}),
                ] + badge()(frame)
    c, port, saver, out = client(respond)
    info = c.hello()
    assert info["firmware"] == "BW" and not c.stale
    assert "badge: - ERROR: old" in out


def test_no_hello_reply_says_why():
    c, port, saver, out = client(lambda frame: [])
    try:
        c.hello()
    except dh.NoReply as error:
        assert "mid-test" in str(error)
    else:
        raise AssertionError("no NoReply")


def test_a_protocol_mismatch_stops():
    c, port, saver, out = client(lambda f: [{"t": "error", "code": "proto",
                                             "text": "badge speaks 2",
                                             "rid": f["rid"]}])
    try:
        c.hello()
    except RuntimeError as error:
        assert "mismatch" in str(error)
    else:
        raise AssertionError("no mismatch")


def test_a_late_reply_is_not_taken_for_the_next_one():
    # N4: replies are paired by rid.
    def extra(frame):
        return [{"t": "error", "code": "idle", "rid": frame["rid"] - 1},
                {"t": "tools", "tools": [], "rid": frame["rid"]}]
    c, port, saver, out = client(badge(extra))
    c.hello()
    assert c.list()["t"] == "tools"


def test_no_session_says_hello_again_and_does_not_resend():
    sent = []

    def extra(frame):
        sent.append(frame["t"])
        return [{"t": "error", "code": "no_session", "rid": frame["rid"]}]
    c, port, saver, out = client(badge(extra))
    c.hello()
    assert c.run("clear", []) is None
    assert sent == ["run"], "never resent"
    assert any("Re-issue" in line for line in out)


def test_a_run_saves_its_file_by_the_naming_rule():
    data = [b"a,b\n", b"1,2\n"]
    c, port, saver, out = client(badge(run_reply(data)))
    c.hello()
    frame = c.run("spin", [])
    assert frame["t"] == "result"
    path = os.path.join(saver.outdir, "spin.20261008-120000.csv")
    with open(path, "rb") as handle:
        assert handle.read() == b"a,b\n1,2\n"


def test_a_second_file_in_the_same_second_gets_a_suffix():
    c, port, saver, out = client(badge(run_reply([b"x"])))
    c.hello()
    c.run("spin", [])
    c.run("spin", [])
    names = sorted(os.listdir(saver.outdir))
    assert names == ["spin.20261008-120000-2.csv", "spin.20261008-120000.csv"]


def test_a_hostile_name_or_extension_stays_inside_outdir():
    c, port, saver, out = client(badge(run_reply([b"x"], name="../../evil",
                                                 ext="../x")))
    c.hello()
    c.run("spin", [])
    assert os.listdir(saver.outdir) == ["unnamed.20261008-120000.bin"]
    assert any("bad name" in line for line in out)
    parent = os.path.dirname(saver.outdir)
    assert os.listdir(parent) == ["out"]


def test_a_bad_crc_or_an_early_end_keeps_a_part_file():
    bad = {"t": "end", "sid": 1, "size": 1, "crc": 0, "ok": True, "rid": 2}
    c, port, saver, out = client(badge(run_reply([b"x"], end=bad)))
    c.hello()
    c.run("spin", [])
    assert os.listdir(saver.outdir) == ["spin.20261008-120000.csv.part"]
    assert any("INCOMPLETE" in line for line in out)


def test_a_missing_chunk_keeps_a_part_file():
    def extra(frame):
        out = run_reply([b"a", b"b"])(frame)
        return [f for f in out if not (f.get("t") == "chunk"
                                       and f.get("seq") == 0)]
    c, port, saver, out = client(badge(extra))
    c.hello()
    c.run("spin", [])
    assert os.listdir(saver.outdir)[0].endswith(".part")


def test_an_unknown_stream_is_ignored():
    def extra(frame):
        out = run_reply([b"x"])(frame)
        return out[:1] + [{"t": "chunk", "sid": 7, "seq": 0, "b": "eA==",
                           "rid": frame["rid"]}] + out[1:]
    c, port, saver, out = client(badge(extra))
    c.hello()
    assert c.run("spin", [])["t"] == "result"


def test_unplugged_mid_stream_keeps_the_part_and_says_so():
    def extra(frame):
        return run_reply([b"x"])(frame)[:3]     # open, one chunk, then gone
    c, port, saver, out = client(badge(extra))
    c.hello()
    shell = dh.Shell(c, out=out.append)
    real = port.read

    def read(n):
        data = real(n)
        if not data:
            raise OSError(5, "device disconnected")
        return data
    port.read = read
    shell.onecmd("run spin")
    assert os.listdir(saver.outdir)[0].endswith(".part")
    assert any("disconnected" in line for line in out)
    assert c.stale


def started(builtin):
    def extra(frame):
        if frame["t"] == "run":
            return [{"t": "started", "tool": frame["tool"],
                     "builtin": builtin, "rid": frame["rid"]}]
        return []
    return extra


def sent_frames(port):
    return [f for f in dl.LineReader().feed(bytes(port.written))
            if isinstance(f, dict)]


def test_ctrl_c_on_a_custom_run_cancels_with_0x03_and_twice_abandons():
    c, port, saver, out = client(badge(started(False)))
    c.hello()
    port.interrupts = 2
    assert c.run("spin", []) is None
    assert [f["t"] for f in sent_frames(port)].count("cancel") == 2
    assert bytes(port.written).count(dh.CTRL_C) == 2, \
        "a raw 0x03 with every Ctrl-C"
    assert c.stale and c.abandoned == "spin"


def test_after_abandoning_a_plain_hello_goes_first():
    c, port, saver, out = client(badge(started(False)))
    c.hello()
    port.interrupts = 2
    c.run("spin", [])
    port.written = bytearray()
    c.hello()                               # the badge answers
    assert dh.CTRL_C not in bytes(port.written)
    assert c.abandoned is None


def test_0x03_only_after_no_reply_and_a_yes():
    answering = []

    def respond(frame):
        if frame["t"] == "run":
            return started(False)(frame)
        if frame["t"] == "hello" and answering:
            return badge()(frame)
        return []
    asked = []
    c, port, saver, out = client(respond, confirm=lambda q: asked.append(q)
                                 or "n")
    answering.append(1)
    c.hello()
    answering.pop()
    port.interrupts = 2
    c.run("spin", [])
    port.written = bytearray()
    try:
        c.hello()
    except dh.NoReply:
        pass
    else:
        raise AssertionError("no NoReply")
    assert asked and "spin" in asked[0]
    assert dh.CTRL_C not in bytes(port.written), "refused: nothing sent"
    c.confirm = lambda q: "y"
    answering.append(1)
    port.written = bytearray()

    hellos = []

    def respond_late(frame):            # silent to the plain hello only
        if frame["t"] == "hello":
            hellos.append(1)
            if len(hellos) == 1:
                return []
        return respond(frame)
    port.respond = respond_late
    c.hello()
    assert dh.CTRL_C in bytes(port.written) and c.abandoned is None


def test_a_disconnect_forgets_the_abandoned_run():
    c, port, saver, out = client(badge(started(False)))
    c.hello()
    port.interrupts = 2
    c.run("spin", [])
    port.fail = OSError(5, "gone")
    try:
        c.hello()
    except dh.Disconnected:
        pass
    assert c.abandoned is None


def test_the_shell_cancel_asks_before_a_0x03():
    c, port, saver, out = client(badge(started(False)))
    c.hello()
    port.interrupts = 2
    c.run("spin", [])
    shell = dh.Shell(c, out=out.append)
    port.written = bytearray()
    shell.onecmd("cancel")                  # the fixture answers "n"
    assert dh.CTRL_C not in bytes(port.written)
    assert sent_frames(port)[0]["t"] == "cancel"
    c.confirm = lambda q: "y"
    shell.onecmd("cancel")
    assert dh.CTRL_C in bytes(port.written)


def test_reconnect_with_no_badge_or_a_busy_port_keeps_the_shell():
    c, port, saver, out = client()
    c.hello()

    def nobody():
        raise dh.NoBadge("no badge found")
    shell = dh.Shell(c, out=out.append, reconnect=nobody)
    assert not shell.onecmd("reconnect")
    assert any("no badge found" in line for line in out)

    def busy():
        raise OSError(16, "Device or resource busy")
    shell.reconnector = busy
    assert not shell.onecmd("reconnect")
    assert any("busy" in line for line in out)


def test_a_cancel_answered_by_the_runs_end():
    def extra(frame):
        if frame["t"] == "run":
            return [{"t": "started", "tool": "spin", "rid": frame["rid"]}]
        if frame["t"] == "cancel":
            return [{"t": "cancelled", "tool": "spin", "by": "host",
                     "rid": 2}]
        return []
    c, port, saver, out = client(badge(extra))
    c.hello()
    port.interrupts = 1
    assert c.run("spin", [])["t"] == "cancelled"
    assert not c.abandoned


def test_upload_sends_chunks_and_ctrl_c_stops_it():
    silent = []

    def extra(frame):
        if frame["t"] == "new":
            return [{"t": "ack", "seq": -1, "rid": frame["rid"]}]
        if frame["t"] == "data" and not silent:
            return [{"t": "ack", "seq": frame["seq"], "rid": frame["rid"]}]
        return []
    c, port, saver, out = client(badge(extra))
    c.hello()
    path = os.path.join(tempfile.mkdtemp(), "big.py")
    with open(path, "w") as handle:
        handle.write("x = 1\n" * 300)
    frame = c.upload(path)
    assert frame["t"] == "ack"
    sent = [f for f in dl.LineReader().feed(bytes(port.written))
            if isinstance(f, dict) and f["t"] == "data"]
    assert b"".join(dl.unb64(f["b"]) for f in sent) == b"x = 1\n" * 300
    silent.append(1)            # the badge stops answering; Ctrl-C
    port.interrupts = 1
    assert c.upload(path) is None and c.stale


def test_the_shell_confirms_clear_and_lists_local_tools():
    ran = []

    def extra(frame):
        ran.append(frame.get("tool"))
        return [{"t": "result", "rid": frame["rid"]}]
    c, port, saver, out = client(badge(extra))
    c.hello()
    tools = tempfile.mkdtemp()
    with open(os.path.join(tools, "spin.py"), "w") as handle:
        handle.write('DESCRIPTION = "loops"\ndef run(api, args):\n  pass\n')
    shell = dh.Shell(c, tools_dir=tools, out=out.append,
                     confirm=lambda prompt: "n")
    shell.onecmd("run clear")
    assert ran == []
    shell.confirm = lambda prompt: "y"
    shell.onecmd("run clear")
    assert ran == ["clear"]
    shell.onecmd("local")
    assert any("spin" in line and "loops" in line for line in out)
    assert shell.complete_new("sp") == ["spin"]


def test_the_subtools_parse_and_have_descriptions():
    names = dict(dh.local_tools(dh.TOOLS))
    tools = ("mirror", "second_badge", "frame_probe", "room_flicker",
             "fast_loopback", "files", "reset")
    assert set(tools) <= set(names), names
    assert all(names[n] for n in tools)


def test_sync_uploads_every_library_then_every_subtool_with_force():
    news = []

    def extra(frame):
        if frame["t"] == "new":
            news.append((frame["file"], frame["lib"], frame["force"]))
            return [{"t": "ack", "seq": -1, "rid": frame["rid"]}]
        if frame["t"] == "data":
            return [{"t": "saved", "file": "x", "rid": frame["rid"]}]
        return []
    c, port, saver, out = client(badge(extra))
    c.hello()
    tools = tempfile.mkdtemp()
    os.mkdir(os.path.join(tools, "lib"))
    for name in ("lib/kit.py", "a.py", "b.py"):
        with open(os.path.join(tools, name), "w") as handle:
            handle.write('DESCRIPTION = "x"\n')
    shell = dh.Shell(c, tools_dir=tools, out=out.append)
    shell.onecmd("sync")
    assert shell.ok
    assert news == [("kit.py", True, True), ("a.py", False, True),
                    ("b.py", False, True)]


def test_new_lib_looks_in_the_lib_folder_and_says_so():
    news = []

    def extra(frame):
        if frame["t"] == "new":
            news.append((frame["file"], frame["lib"]))
            return [{"t": "error", "code": "exists", "rid": frame["rid"]}]
        return []
    c, port, saver, out = client(badge(extra))
    c.hello()
    tools = tempfile.mkdtemp()
    os.mkdir(os.path.join(tools, "lib"))
    with open(os.path.join(tools, "lib", "kit.py"), "w") as handle:
        handle.write("X = 1\n")
    shell = dh.Shell(c, tools_dir=tools, out=out.append)
    shell.onecmd("new kit --lib")
    assert news == [("kit.py", True)]


# --- remove ------------------------------------------------------------------------

def remover(missing=("nope",)):
    def extra(frame):
        if frame["t"] == "remove":
            names = frame["names"]
            return [{"t": "removed", "rid": frame["rid"],
                     "removed": [n for n in names if n not in missing],
                     "errors": [[n, "no such subtool"] for n in names
                                if n in missing]}]
        if frame["t"] == "list":
            return [{"t": "tools", "tools": [
                {"name": "measurements", "builtin": True},
                {"name": "hello", "builtin": False}], "rid": frame["rid"]}]
        return []
    return badge(extra)


def test_remove_asks_first_and_no_sends_nothing():
    c, port, saver, out = client(remover())
    c.hello()
    shell = dh.Shell(c, out=out.append, confirm=lambda q: "n")
    shell.onecmd("remove hello")
    assert not shell.ok and "remove" not in [
        f["t"] for f in sent_frames(port)]


def test_remove_with_a_miss_removes_the_rest_and_fails():
    c, port, saver, out = client(remover())
    c.hello()
    asked = []
    shell = dh.Shell(c, out=out.append,
                     confirm=lambda q: asked.append(q) or "y")
    shell.onecmd("remove hello nope")
    assert "hello, nope" in asked[0]
    assert "removed: hello" in out and "error: nope: no such subtool" in out
    assert not shell.ok, "a name that missed fails the command"
    shell.onecmd("remove hello")
    assert shell.ok
    # the tool list is refreshed for completion: built-ins never offered
    assert shell.complete_remove("") == ["hello"]


# --- scripted ------------------------------------------------------------------

class Stdin:
    def __init__(self, tty):
        self.tty = tty

    def isatty(self):
        return self.tty


def script(argv, respond, tty=False, monkey=None):
    """main() with a fake port; returns (exit status, printed lines)."""
    import builtins
    import sys
    printed = []
    saved = (dh.find_port, dh.open_port, sys.stdin, builtins.print,
             dh.time.sleep)
    port = FakeSerial(respond)
    dh.find_port = lambda: "fake"
    dh.open_port = (monkey or (lambda name: port))
    sys.stdin = Stdin(tty)
    builtins.print = lambda *a, **k: printed.append(" ".join(map(str, a)))
    try:
        status = dh.main(argv)
    finally:
        (dh.find_port, dh.open_port, sys.stdin, builtins.print,
         dh.time.sleep) = saved
    return status, printed, port


def test_a_scripted_run_saves_its_file_and_exits_0():
    outdir = os.path.join(tempfile.mkdtemp(), "out")
    status, printed, port = script([outdir, "run", "spin", "3"],
                                   badge(run_reply([b"a,b\n"])))
    assert status == 0
    names = os.listdir(outdir)
    assert len(names) == 1 and names[0].startswith("spin.") \
        and names[0].endswith(".csv")
    runs = [f for f in sent_frames(port) if f["t"] == "run"]
    assert runs[0]["args"] == ["3"]
    assert [f["t"] for f in sent_frames(port)][-1] == "bye"


def test_scripted_failures_exit_1():
    outdir = tempfile.mkdtemp()
    error = badge(lambda f: [{"t": "error", "code": "unknown_tool",
                              "rid": f["rid"]}])
    assert script([outdir, "run", "nope"], error)[0] == 1
    assert script([outdir, "new", "/no/such.py"], badge())[0] == 1
    cancelled = badge(lambda f: [{"t": "started", "rid": f["rid"]},
                                 {"t": "cancelled", "by": "B",
                                  "rid": f["rid"]}])
    assert script([outdir, "run", "mirror"], cancelled)[0] == 1


def test_scripted_list_and_local_exit_0():
    outdir = tempfile.mkdtemp()
    assert script([outdir, "list"], remover())[0] == 0
    assert script([outdir, "local"], badge())[0] == 0


def test_scripted_remove_asks_and_yes_skips_it():
    outdir = tempfile.mkdtemp()
    # stdin not a terminal and no --yes: counted as no
    status, printed, port = script([outdir, "remove", "hello"], remover())
    assert status == 1 and "remove" not in [f["t"]
                                             for f in sent_frames(port)]
    status, printed, port = script([outdir, "--yes", "remove", "hello"],
                                   remover())
    assert status == 0 and "removed: hello" in printed
    status, printed, port = script([outdir, "-y", "remove", "hello", "nope"],
                                   remover())
    assert status == 1 and "removed: hello" in printed


def test_no_badge_or_no_link_exits_3_and_a_disconnect_exits_1():
    outdir = tempfile.mkdtemp()

    def nobody(name):
        raise dh.NoBadge("no badge found")
    assert script([outdir, "list"], badge(), monkey=nobody)[0] == 3
    assert script([outdir, "list"], lambda f: [])[0] == 3   # no hello reply

    def gone(frame):
        if frame["t"] == "run":
            raise OSError(5, "unplugged")
        return badge()(frame)
    assert script([outdir, "run", "spin"], gone)[0] == 1


def test_ctrl_c_in_a_scripted_run_cancels_and_exits_non_zero():
    outdir = tempfile.mkdtemp()

    def respond(frame):
        if frame["t"] == "run":
            return [{"t": "started", "builtin": False, "rid": frame["rid"]}]
        if frame["t"] == "cancel":
            return [{"t": "cancelled", "by": "host", "rid": 2}]
        return badge()(frame)

    def opener(name):
        port = FakeSerial(respond)
        port.interrupts = 1
        return port
    status, printed, port = script([outdir, "run", "spin"], respond,
                                   monkey=opener)
    assert status == 1 and "cancelled (by host)" in printed



def test_badly_asked_scripted_commands_exit_2():
    outdir = tempfile.mkdtemp()
    assert script([outdir, "frobnicate"], badge())[0] == 2
    assert script([outdir, "run"], badge())[0] == 2
    assert script([outdir, "new"], badge())[0] == 2
    assert script([outdir, "remove"], badge())[0] == 2


def test_ctrl_c_while_connecting_exits_130_quietly():
    def opener(name):
        raise KeyboardInterrupt
    status, printed, port = script([tempfile.mkdtemp(), "list"], badge(),
                                   monkey=opener)
    assert status == 130


def test_yes_never_answers_the_ctrl_c_question():
    # Scripted, not on a terminal, with --yes: the client's own "send
    # Ctrl-C to a run left going?" still counts as no.
    seen = {}

    def opener(name):
        port = FakeSerial(badge())
        seen["port"] = port
        return port
    real = dh.Client.__init__

    def spy(self, *a, **k):
        real(self, *a, **k)
        seen["client"] = self
    dh.Client.__init__ = spy
    try:
        script([tempfile.mkdtemp(), "--yes", "list"], badge(), monkey=opener)
    finally:
        dh.Client.__init__ = real
    assert seen["client"].confirm("send Ctrl-C?") == "n"
