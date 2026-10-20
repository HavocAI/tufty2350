"""The USB dev link's server (devserver.py), over a fake port: sessions,
dispatch, the /devtests scan, `new`, custom runs and their cleanup order, the
ring interlock, cancels, and a host that goes away."""

import os
import shutil
import subprocess
import tempfile

from devlink_fakes import builtin, dl, ds, server, streams
from harness import APPS, Skipped

HELLO_TOOL = '''
DESCRIPTION = "says hello"
EXT = "txt"

def run(api, args):
    api.send("hello " + " ".join(args))
'''

STREAMER = '''
def run(api, args):
    with api.stream("bin") as s:
        for i in range(10):
            s.write(bytes(range(256)) * 2)
    api.log("done")
    print("printed", 1)
    return {"n": 10}
'''

RAISER = '''
def run(api, args):
    s = api.stream("csv")
    s.write("a,b\\n")
    raise ValueError("nope")
'''


def kinds(frames):
    return [f["t"] for f in frames]


def last(frames):
    return frames[-1]


# --- sessions --------------------------------------------------------------

def test_nothing_is_answered_but_hello_before_a_session():
    srv, port, hooks, host = server(builtins={"clear": builtin("clear", [])})
    got = host.ask({"t": "run", "tool": "clear", "args": []})
    assert kinds(got) == ["error"] and got[0]["code"] == "no_session"
    assert srv.running is None


def test_a_command_queued_before_the_link_opened_is_refused():
    # F1: bytes left in stdin while the link was closed must not run.
    record = []
    srv, port, hooks, host = server(builtins={"clear": builtin("clear",
                                                               record)})
    host.send({"t": "run", "tool": "clear", "args": []})
    host.send({"t": "hello", "id": "n", "proto": dl.PROTO})
    srv.service()
    got = host.frames()
    assert kinds(got) == ["error", "hello"]
    assert record == []


def test_hello_echoes_the_nonce_and_the_rid():
    srv, port, hooks, host = server()
    got = host.hello("abc")
    assert got[0]["t"] == "hello" and got[0]["id"] == "abc"
    assert got[0]["rid"] == host.rid and got[0]["proto"] == dl.PROTO
    assert got[0]["tethered"] is True


def test_a_wrong_protocol_is_refused():
    srv, port, hooks, host = server()
    got = host.ask({"t": "hello", "id": "x", "proto": 99})
    assert got[0]["code"] == "proto" and srv.session is None


def test_bye_ends_the_session():
    srv, port, hooks, host = server()
    host.hello()
    got = host.ask({"t": "bye"})
    assert kinds(got) == ["bye"] and srv.session is None


def test_garbage_and_ctrl_c_between_frames_are_ignored():
    srv, port, hooks, host = server()
    port.inbox.extend(b"\x03\x03\r\x01 junk \xff\n")
    got = host.hello()
    assert kinds(got) == ["hello"]


def test_cancel_is_never_answered_on_its_own():
    # N4: a late cancel's reply would be read as the next command's.
    srv, port, hooks, host = server()
    host.hello()
    assert host.ask({"t": "cancel"}) == []


def test_a_bug_in_dispatch_is_an_error_not_a_reset():
    srv, port, hooks, host = server()
    host.hello()
    srv.tool_list = None        # calling it raises TypeError
    got = host.ask({"t": "list"})
    assert got[0]["code"] == "internal" and "TypeError" in got[0]["text"]


def test_every_reply_carries_the_rid_it_answers():
    srv, port, hooks, host = server({"hello.py": HELLO_TOOL})
    host.hello()
    rid = host.send({"t": "run", "tool": "hello", "args": ["x"]})
    srv.service()
    got = host.frames()
    assert all(f["rid"] == rid for f in got), got


# --- list -------------------------------------------------------------------

def test_list_has_built_ins_and_files_with_load_errors():
    srv, port, hooks, host = server(
        {"hello.py": HELLO_TOOL, "broken.py": "def run(:\n",
         "norun.py": "X = 1\n", "Bad-Name.py": "def run(a, b): pass\n",
         "notes.txt": "ignored"},
        {"clear": builtin("clear", [])})
    host.hello()
    tools = host.ask({"t": "list"})[0]["tools"]
    by = {t["name"]: t for t in tools}
    assert by["clear"]["builtin"] and by["clear"]["ext"] == "json"
    assert by["hello"]["desc"] == "says hello" and by["hello"]["ext"] == "txt"
    assert by["hello"]["error"] is None
    assert "SyntaxError" in by["broken"]["error"]
    assert by["norun"]["error"] == "no run(api, args)"
    assert by["Bad-Name"]["error"] == "bad name"
    assert "notes" not in by


def test_long_fields_are_capped_so_the_reply_stays_short():
    srv, port, hooks, host = server(
        {"wordy.py": 'DESCRIPTION = "x" * 5000\ndef run(a, b): pass\n'})
    host.hello()
    tool = host.ask({"t": "list"})[0]["tools"][0]
    assert len(tool["desc"]) == dl.FIELD_CAP


def test_list_reloads_only_a_file_that_changed():
    srv, port, hooks, host = server({"hello.py": HELLO_TOOL})
    loads = []
    probe = srv.probe
    srv.probe = lambda stem, source=None: loads.append(stem) or probe(stem,
                                                                      source)
    host.hello()
    host.ask({"t": "list"})
    assert loads == []
    path = os.path.join(srv.root, "hello.py")
    with open(path, "a") as handle:
        handle.write("# more\n")
    host.ask({"t": "list"})
    assert loads == ["hello"]


def test_a_stale_tmp_is_removed_at_scan():
    srv, port, hooks, host = server({"x.py.tmp": "partial"})
    assert not os.path.exists(os.path.join(srv.root, "x.py.tmp"))


# --- custom runs -------------------------------------------------------------

def test_a_run_sends_its_file_and_a_result():
    srv, port, hooks, host = server({"hello.py": HELLO_TOOL})
    host.hello()
    got = host.ask({"t": "run", "tool": "hello", "args": ["a", "b"]})
    assert kinds(got) == ["started", "open", "chunk", "end", "result"]
    files = streams(got)
    opened, data, end = files[got[1]["sid"]]
    assert opened["name"] == "hello" and opened["ext"] == "txt"
    assert data == b"hello a b" and end["ok"] and end["size"] == 9
    assert end["crc"] == dl.crc32(b"hello a b")
    assert last(got)["files"] == 1 and port.arms == port.disarms == 1
    assert hooks.statuses and hooks.restores == 1


def test_a_stream_across_chunks_a_log_print_and_a_return_value():
    srv, port, hooks, host = server({"streamer.py": STREAMER})
    host.hello()
    got = host.ask({"t": "run", "tool": "streamer", "args": []})
    files = list(streams(got).values())
    assert files[0][1] == (bytes(range(256)) * 2) * 10
    assert files[0][2]["ok"]
    assert files[1][0]["ext"] == "json"
    assert files[1][1] == b'{"n": 10}'
    logs = [f["text"] for f in got if f["t"] == "log"]
    assert logs == ["done", "printed 1"]
    assert last(got)["t"] == "result" and last(got)["files"] == 2


def test_a_raise_is_reported_with_its_traceback_and_streams_end_not_ok():
    srv, port, hooks, host = server({"raiser.py": RAISER})
    host.hello()
    got = host.ask({"t": "run", "tool": "raiser", "args": []})
    end = [f for f in got if f["t"] == "end"][0]
    assert not end["ok"]
    assert last(got)["code"] == "raised"
    assert "ValueError: nope" in last(got)["text"]
    assert srv.running is None and port.arms == port.disarms


def test_system_exit_in_a_subtool_is_caught():
    srv, port, hooks, host = server(
        {"quits.py": "import sys\ndef run(api, args):\n    sys.exit(3)\n"})
    host.hello()
    got = host.ask({"t": "run", "tool": "quits", "args": []})
    assert last(got)["code"] == "raised"
    assert "SystemExit" in last(got)["text"]


def test_a_value_that_is_not_json_is_an_error_and_the_run_still_ends():
    srv, port, hooks, host = server(
        {"odd.py": "def run(api, args):\n    return object()\n"})
    host.hello()
    got = host.ask({"t": "run", "tool": "odd", "args": []})
    assert last(got)["code"] == "raised"
    assert srv.running is None
    assert kinds(host.ask({"t": "list"})) == ["tools"]


def test_unknown_tools_and_bad_args():
    srv, port, hooks, host = server({"broken.py": "def run(:\n"})
    host.hello()
    assert host.ask({"t": "run", "tool": "nope"})[0]["code"] == "unknown_tool"
    got = host.ask({"t": "run", "tool": "broken"})
    assert got[0]["code"] == "raised"
    assert srv.running is None
    got = host.ask({"t": "run", "tool": "x", "args": "notalist"})
    assert got[0]["code"] == "bad_args"


def test_a_bad_ext_or_name_from_a_subtool_is_refused_at_the_call():
    srv, port, hooks, host = server({"ext.py": (
        "def run(api, args):\n"
        "    try:\n        api.send(b'x', ext='../x')\n"
        "    except ValueError:\n        api.log('ext refused')\n"
        "    try:\n        api.send(b'x', name='../x')\n"
        "    except ValueError:\n        api.log('name refused')\n")})
    host.hello()
    got = host.ask({"t": "run", "tool": "ext", "args": []})
    assert [f["text"] for f in got if f["t"] == "log"] == [
        "ext refused", "name refused"]
    assert not [f for f in got if f["t"] == "open"]


def test_named_streams_are_saved_under_tool_and_name():
    srv, port, hooks, host = server({"two.py": (
        "def run(api, args):\n"
        "    api.send(b'a', 'csv', 'left')\n"
        "    api.send(b'b', 'csv', 'right')\n")})
    host.hello()
    got = host.ask({"t": "run", "tool": "two", "args": []})
    assert [f["name"] for f in got if f["t"] == "open"] == [
        "two_left", "two_right"]


# --- the ring ----------------------------------------------------------------

RINGER = '''
from devserver import RingBusy

def run(api, args):
    reader = api.ring_start()
    refused = []
    for call in (api.light, api.temp, lambda: api.send(b"x"),
                 lambda: api.status("x")):
        try:
            call()
        except RingBusy:
            refused.append(1)
    api.log("dropped while the ring runs")
    print("so is this")
    api.ring_stop()
    api.send(bytes([len(refused)]))
'''


def test_the_ring_blocks_the_adc_and_every_write_and_drops_logs():
    srv, port, hooks, host = server({"ringer.py": RINGER})
    host.hello()
    got = host.ask({"t": "run", "tool": "ringer", "args": []})
    files = list(streams(got).values())
    assert files[0][1] == bytes([4])
    logs = [f["text"] for f in got if f["t"] == "log"]
    assert logs == ["2 lines dropped while the ring ran"]
    assert not hooks.ring_reader.running


def test_a_raise_with_the_ring_running_still_leaves_a_working_link():
    # N3: the ring stops before the streams end, or their end is refused.
    srv, port, hooks, host = server({"ringraise.py": (
        "def run(api, args):\n"
        "    s = api.stream('bin')\n"
        "    api.ring_start()\n"
        "    raise RuntimeError('mid-capture')\n")})
    host.hello()
    got = host.ask({"t": "run", "tool": "ringraise", "args": []})
    assert [f["ok"] for f in got if f["t"] == "end"] == [False]
    assert last(got)["code"] == "raised"
    assert not hooks.ring_reader.running and not srv.ring_active
    assert kinds(host.ask({"t": "list"})) == ["tools"]


def test_cleanup_runs_in_the_pinned_order():
    order = []
    srv, port, hooks, host = server({"o.py": (
        "def run(api, args):\n"
        "    api.stream('bin').write(b'x')\n"
        "    api.ring_start()\n")})
    host.hello()
    port.disarm_cancel = lambda: order.append("disarm")
    hooks.ring_reader.stop = lambda: order.append("ring")
    hooks.restore = lambda: order.append("panel")
    hooks.collect = lambda: order.append("collect")
    port.on_write = lambda data: order.append(
        dl.LineReader().feed(data)[0]["t"])
    host.ask({"t": "run", "tool": "o", "args": []})
    tail = order[order.index("disarm"):]
    assert tail == ["disarm", "ring", "panel", "chunk", "end", "result",
                    "collect"], \
        tail


# --- cancels -------------------------------------------------------------------

POLLER = '''
def run(api, args):
    n = 0
    while not api.cancelled():
        n += 1
        if n > 1000:
            api.log("never cancelled")
            return
    api.log("stopped")
'''


def test_b_held_cancels_a_polling_run():
    srv, port, hooks, host = server({"poller.py": POLLER})
    host.hello()
    hooks.held = True
    got = host.ask({"t": "run", "tool": "poller", "args": []})
    assert last(got)["t"] == "cancelled" and last(got)["by"] == "B"


def test_a_host_cancel_is_read_by_cancelled_which_never_writes():
    srv, port, hooks, host = server({"poller.py": POLLER})
    host.hello()
    host.send({"t": "run", "tool": "poller", "args": []})
    host.send({"t": "cancel"})
    host.send({"t": "list"})            # mid-run: dropped, not answered later
    writes = []
    real = port.write

    def watch(data):
        writes.append(srv.running)
        return real(data)
    port.write = watch
    srv.service()
    got = host.frames()
    assert last(got)["t"] == "cancelled" and last(got)["by"] == "host"
    assert [f["t"] for f in got if f["t"] == "tools"] == []


def test_an_interrupt_is_a_cancel_and_says_who_sent_it():
    srv, port, hooks, host = server(
        {"spin.py": "def run(api, args):\n    raise KeyboardInterrupt\n"})
    host.hello()
    got = host.ask({"t": "run", "tool": "spin", "args": []})
    assert last(got)["t"] == "cancelled" and last(got)["by"] == "host"
    port.arm_cancel = lambda: setattr(port, "b_fired", True)
    got = host.ask({"t": "run", "tool": "spin", "args": []})
    assert last(got)["by"] == "B"


def test_a_second_interrupt_in_the_disarm_cannot_escape():
    # N2: interrupts already on their way land in disarm_cancel() itself.
    srv, port, hooks, host = server(
        {"spin.py": "def run(api, args):\n    raise KeyboardInterrupt\n"})
    host.hello()
    port.interrupt_disarm = 3
    got = host.ask({"t": "run", "tool": "spin", "args": []})
    assert last(got)["t"] == "cancelled"
    assert port.disarms == 1 and port.interrupt_disarm == 0
    port.interrupt_disarm = 2
    got = host.ask({"t": "run", "tool": "spin", "args": []})
    assert last(got)["t"] == "cancelled" and port.disarms == 2


def test_a_hello_mid_run_cancels_it_and_is_answered_after():
    srv, port, hooks, host = server({"poller.py": POLLER})
    host.hello("old")
    host.send({"t": "run", "tool": "poller", "args": []})
    host.send({"t": "hello", "id": "new", "proto": dl.PROTO})
    srv.service()
    got = host.frames()
    assert kinds(got)[-2:] == ["cancelled", "hello"]
    assert got[-2]["by"] == "hello" and got[-1]["id"] == "new"


# --- a host that goes away ------------------------------------------------------

def test_a_short_write_marks_the_host_lost_and_ends_the_run():
    srv, port, hooks, host = server({"poller.py": (
        "def run(api, args):\n"
        "    for i in range(100):\n"
        "        api.log('x')\n"
        "        if api.cancelled():\n"
        "            return\n")})
    host.hello()
    count = []

    def short(data):
        count.append(1)
        port.short = len(count) > 2
    port.on_write = short
    host.ask({"t": "run", "tool": "poller", "args": []})
    assert srv.lost and srv.running is None
    assert len(count) == 3, "nothing written once the host was lost"
    assert "gone" in srv.status_text()
    port.short = False
    port.on_write = None
    assert host.hello()[0]["t"] == "hello" and not srv.lost


def test_vbus_gone_cancels_a_run_and_says_unplugged():
    srv, port, hooks, host = server({"poller.py": POLLER})
    host.hello()
    host.send({"t": "run", "tool": "poller", "args": []})
    hooks.connected = False
    srv.service()
    assert srv.running is None and "unplugged" in srv.status_text()


# --- new -----------------------------------------------------------------------

def upload(host, name, source, force=False, chunk=dl.CHUNK_IN):
    raw = source.encode()
    got = host.ask({"t": "new", "file": name, "size": len(raw),
                    "crc": dl.crc32(raw), "force": force})
    if got[0]["t"] != "ack":
        return got
    out = got
    for seq, at in enumerate(range(0, len(raw), chunk)):
        got = host.ask({"t": "data", "seq": seq, "b": dl.b64(raw[at:at + chunk])})
        out += got
        if got[0]["t"] not in ("ack",):
            break
    return out


def test_new_saves_a_valid_subtool_through_a_tmp_file():
    srv, port, hooks, host = server(builtins={"clear": builtin("clear", [])})
    host.hello()
    renames = []
    real = os.rename
    os.rename = lambda a, b: (renames.append((a, b)), real(a, b))
    try:
        got = upload(host, "big.py", HELLO_TOOL + "#" * 2000)
    finally:
        os.rename = real
    assert last(got)["t"] == "saved" and last(got)["tool"]["name"] == "big"
    assert renames[0][0].endswith("big.py.tmp")
    with open(os.path.join(srv.root, "big.py")) as handle:
        assert handle.read().startswith("\nDESCRIPTION")
    assert "big" in srv.tools


def test_new_refuses_a_syntax_error_and_writes_nothing():
    srv, port, hooks, host = server()
    host.hello()
    got = upload(host, "bad.py", "def run(:\n    pass\n")
    assert last(got)["code"] == "syntax"
    assert os.listdir(srv.root) == []


def test_new_refuses_a_file_without_run():
    srv, port, hooks, host = server()
    host.hello()
    got = upload(host, "norun.py", "X = 1\n")
    assert last(got)["code"] == "raised"
    assert os.listdir(srv.root) == []


def test_new_refuses_bad_names_built_ins_and_big_files():
    srv, port, hooks, host = server(builtins={"clear": builtin("clear", [])})
    host.hello()
    for name in ("../x.py", "x.txt", "Clear.py", "clear.py", "1.py"):
        assert upload(host, name, HELLO_TOOL)[0]["code"] == "bad_name", name
    got = host.ask({"t": "new", "file": "x.py", "size": dl.NEW_MAX + 1})
    assert got[0]["code"] == "too_big"


def test_new_overwrites_only_with_force():
    srv, port, hooks, host = server({"hello.py": HELLO_TOOL})
    host.hello()
    assert upload(host, "hello.py", HELLO_TOOL)[0]["code"] == "exists"
    got = upload(host, "hello.py", HELLO_TOOL.replace("says", "now says"),
                 force=True)
    assert last(got)["t"] == "saved"
    assert last(got)["tool"]["desc"] == "now says hello"


def test_new_checks_order_and_crc():
    srv, port, hooks, host = server()
    host.hello()
    raw = HELLO_TOOL.encode()
    host.ask({"t": "new", "file": "a.py", "size": len(raw), "crc": 1})
    got = host.ask({"t": "data", "seq": 0, "b": dl.b64(raw)})
    assert got[0]["code"] == "crc"
    host.ask({"t": "new", "file": "a.py", "size": len(raw), "crc": 1})
    got = host.ask({"t": "data", "seq": 1, "b": dl.b64(raw)})
    assert got[0]["code"] == "bad_frame" and srv.upload is None
    got = host.ask({"t": "new", "file": "a.py", "size": 0, "crc": 0})
    assert got[0]["code"] == "bad_args" and "empty" in got[0]["text"]


def test_commands_during_an_upload_are_busy_and_hello_drops_it():
    srv, port, hooks, host = server()
    host.hello()
    host.ask({"t": "new", "file": "a.py", "size": 10, "crc": 0})
    assert host.ask({"t": "list"})[0]["code"] == "busy"
    assert srv.upload is not None
    host.hello()
    assert srv.upload is None


def test_a_flash_error_is_io_and_leaves_no_tmp():
    srv, port, hooks, host = server()
    host.hello()
    real = os.rename

    def fail(a, b):
        raise OSError(28, "no space")
    os.rename = fail
    try:
        got = upload(host, "a.py", HELLO_TOOL)
    finally:
        os.rename = real
    assert last(got)["code"] == "io"
    assert os.listdir(srv.root) == []


# --- built-ins ------------------------------------------------------------------

def test_a_built_in_answers_at_once_with_its_data():
    record = []
    srv, port, hooks, host = server(builtins={"measurements": builtin(
        "measurements", record, data={"steps": [1, 2]}, text="sent")})
    host.hello()
    got = host.ask({"t": "run", "tool": "measurements", "args": ["x"]})
    assert kinds(got) == ["started", "open", "chunk", "end", "result"]
    assert record == [("measurements", ["x"])]
    assert list(streams(got).values())[0][1] == b'{"steps": [1, 2]}'
    assert last(got)["files"] == 1 and last(got)["text"] == "sent"
    assert srv.running is None
    got = host.ask({"t": "run", "tool": "measurements"})
    assert last(got)["t"] == "result"


def test_a_built_in_without_data_sends_no_file():
    srv, port, hooks, host = server(builtins={"clear": builtin(
        "clear", [], saved=True, text="Measurements cleared")})
    host.hello()
    got = host.ask({"t": "run", "tool": "clear"})
    assert kinds(got) == ["started", "result"]
    assert got[1]["saved"] is True and got[1]["files"] == 0


# --- under MicroPython ------------------------------------------------------------

UNIX_SCRIPT = r"""
import sys, os
sys.path.insert(0, %(app)r)
import devlink as d
import devserver as ds

class Port:
    b_fired = False
    def __init__(self):
        self.inbox = b""
        self.out = []
    def read(self, n):
        data, self.inbox = self.inbox[:n], self.inbox[n:]
        return data
    def write(self, data):
        self.out.append(data)
        return True
    def arm_cancel(self):
        pass
    def disarm_cancel(self):
        pass

class Hooks:
    firmware = "mp"
    temp = None
    def usb_connected(self): return True
    def poll(self): pass
    def b_held(self): return False
    def status(self, t): pass
    def restore(self): pass
    def collect(self): pass
    def format_exc(self, e): return "%%s: %%s" %% (type(e).__name__, e)
    def ring(self): return None
    def ring_note(self): return "none"
    def light(self): return 1
    def ticks_us(self): return 0
    def ticks_ms(self): return 0
    def backlight(self, v): pass
    def present(self): pass

port = Port()
srv = ds.Server(port, %(root)r, Hooks(), {})
srv.scan()
src = %(src)r.encode()
frames = [{"t": "hello", "id": "x", "proto": d.PROTO, "rid": 1},
          {"t": "new", "file": "s.py", "size": len(src), "crc": d.crc32(src), "rid": 2},
          {"t": "data", "seq": 0, "b": d.b64(src), "rid": 3},
          {"t": "list", "rid": 4},
          {"t": "run", "tool": "s", "args": ["a"], "rid": 5}]
for f in frames:
    port.inbox += d.encode(f)
    srv.service()
for raw in port.out:
    sys.stdout.write(raw)
"""


def test_the_server_under_micropython():
    tool = os.environ.get("MICROPYTHON") or shutil.which("micropython")
    if not tool or not os.path.exists(tool):
        raise Skipped("no MicroPython unix port: set MICROPYTHON")
    root = tempfile.mkdtemp(prefix="mpdev")
    with tempfile.TemporaryDirectory() as out:
        script = os.path.join(out, "s.py")
        with open(script, "w") as handle:
            handle.write(UNIX_SCRIPT % {"app": os.path.join(APPS, "share_wifi"),
                                        "root": root, "src": STREAMER})
        result = subprocess.run([tool, script], capture_output=True)
    assert result.returncode == 0, result.stderr + result.stdout
    got = [f for f in dl.LineReader(dl.LINE_MAX_HOST).feed(result.stdout)
           if isinstance(f, dict)]
    assert kinds(got)[:4] == ["hello", "ack", "saved", "tools"], kinds(got)
    files = list(streams(got).values())
    assert files[0][1] == (bytes(range(256)) * 2) * 10
    assert files[0][2]["crc"] == dl.crc32(files[0][1])
    assert last(got)["t"] == "result"
    shutil.rmtree(root)


def test_devserver_knows_nothing_of_the_badge():
    assert ds.Server and not hasattr(ds, "badge")


def test_a_built_in_that_raises_does_not_stick_the_link():
    def fn(args):
        raise OSError(5, "flash")
    srv, port, hooks, host = server(builtins={
        "clear": ds.Builtin("clear", "d", fn)})
    host.hello()
    got = host.ask({"t": "run", "tool": "clear"})
    assert kinds(got) == ["started", "error"] and "OSError" in got[1]["text"]
    assert srv.running is None


def test_an_interrupt_in_a_write_mid_chunk_still_ends_the_run_cleanly():
    # A KeyboardInterrupt can land inside the Api's own write: the stream
    # still ends not ok, the run is cancelled, and the link answers after.
    srv, port, hooks, host = server({"streamer.py": STREAMER})
    host.hello()
    count = []

    def interrupt(data):
        count.append(1)
        if len(count) == 3:
            raise KeyboardInterrupt
    port.on_write = interrupt
    got = host.ask({"t": "run", "tool": "streamer", "args": []})
    port.on_write = None
    assert [f["ok"] for f in got if f["t"] == "end"] == [False]
    assert last(got)["t"] == "cancelled" and last(got)["by"] == "host"
    assert port.arms == port.disarms == 1
    assert kinds(host.ask({"t": "list"})) == ["tools"]


def test_a_run_that_returned_is_not_cancelled_by_b_held_while_tidying():
    srv, port, hooks, host = server({"hello.py": HELLO_TOOL})
    host.hello()
    restore = hooks.restore

    def press_b():
        hooks.held = True
        restore()
    hooks.restore = press_b
    got = host.ask({"t": "run", "tool": "hello", "args": ["x"]})
    assert last(got)["t"] == "result"
    assert [f["ok"] for f in got if f["t"] == "end"] == [True]


def test_a_raise_before_the_run_starts_does_not_leave_the_link_busy():
    srv, port, hooks, host = server({"hello.py": HELLO_TOOL})
    host.hello()

    def broken(text):
        raise RuntimeError("display")
    hooks.status = broken
    got = host.ask({"t": "run", "tool": "hello", "args": []})
    assert got[-1]["code"] == "internal"
    assert srv.running is None
    assert kinds(host.ask({"t": "list"})) == ["tools"]


def test_sys_exit_at_module_level_is_listed_not_fatal():
    srv, port, hooks, host = server(
        {"exits.py": "import sys\nsys.exit(1)\ndef run(api, args): pass\n"})
    host.hello()
    tools = host.ask({"t": "list"})[0]["tools"]
    assert "SystemExit" in tools[0]["error"]
    got = upload(host, "exits2.py",
                 "import sys\nsys.exit(1)\ndef run(api, args): pass\n")
    assert last(got)["code"] == "raised"


# --- remove ----------------------------------------------------------------------

def test_remove_deletes_named_subtools_and_reports_each_miss():
    srv, port, hooks, host = server(
        {"hello.py": HELLO_TOOL, "spin.py": HELLO_TOOL},
        {"mirror": builtin("mirror", [])})
    host.hello()
    got = host.ask({"t": "remove", "names": ["hello", "nope", "mirror",
                                             "spin"]})
    assert got[0]["t"] == "removed"
    assert got[0]["removed"] == ["hello", "spin"]
    assert got[0]["errors"] == [["nope", "no such subtool"],
                                ["mirror", "no such subtool"]]
    assert os.listdir(srv.root) == []
    tools = host.ask({"t": "list"})[0]["tools"]
    assert [t["name"] for t in tools] == ["mirror"]


def test_remove_refuses_a_bad_names_list_or_none():
    srv, port, hooks, host = server({"hello.py": HELLO_TOOL})
    host.hello()
    assert host.ask({"t": "remove", "names": "x"})[0]["code"] == "bad_args"
    assert host.ask({"t": "remove", "names": []})[0]["code"] == "bad_args"
    assert host.ask({"t": "remove"})[0]["code"] == "bad_args"


def test_remove_builds_no_path_from_a_traversal_shaped_name():
    srv, port, hooks, host = server({"hello.py": HELLO_TOOL})
    host.hello()
    outside = os.path.join(os.path.dirname(srv.root), "victim.py")
    with open(outside, "w") as handle:
        handle.write("x")
    names = ["../victim", "/etc/passwd", "a/../hello", "hello/..", ""]
    got = host.ask({"t": "remove", "names": names})
    assert got[0]["removed"] == []
    assert [e[1] for e in got[0]["errors"]] == ["no such subtool"] * 5
    assert os.path.exists(outside) and os.listdir(srv.root) == ["hello.py"]
    os.remove(outside)


def test_a_file_named_like_a_built_in_can_be_removed():
    # Put there by the REPL (`new` refuses the name): listed as broken,
    # never run, but removable.
    srv, port, hooks, host = server(
        {"clear.py": "def run(a, b): pass\n"},
        {"clear": builtin("clear", [])})
    host.hello()
    tools = host.ask({"t": "list"})[0]["tools"]
    assert [t["error"] for t in tools if not t["builtin"]] == ["bad name"]
    got = host.ask({"t": "remove", "names": ["clear", "clear"]})
    assert got[0]["removed"] == ["clear"]
    assert got[0]["errors"] == [["clear", "no such subtool"]]
    assert os.listdir(srv.root) == []


# --- libraries -------------------------------------------------------------------

LIB_MODULE = "VALUE = 42\n\ndef twice(x):\n    return 2 * x\n"
USES_LIB = '''
DESCRIPTION = "uses a library"

def run(api, args):
    import kit
    return {"twice": kit.twice(kit.VALUE)}
'''


def upload_lib(host, name, text):
    raw = text.encode()
    got = host.ask({"t": "new", "file": name, "size": len(raw),
                    "crc": dl.crc32(raw), "lib": True})
    if got[0]["t"] != "ack":
        return got
    return host.ask({"t": "data", "seq": 0, "b": dl.b64(raw)})


def test_a_library_is_saved_in_lib_and_imported_by_a_run_then_dropped():
    import sys
    srv, port, hooks, host = server({"user.py": USES_LIB})
    host.hello()
    got = upload_lib(host, "kit.py", LIB_MODULE)
    assert last(got)["t"] == "saved" and last(got)["file"] == "lib/kit.py"
    assert os.path.exists(os.path.join(srv.root, "lib", "kit.py"))
    listed = host.ask({"t": "list"})[0]
    assert listed["libs"] == ["kit"]
    assert [t["name"] for t in listed["tools"]] == ["user"]
    got = host.ask({"t": "run", "tool": "user"})
    assert last(got)["t"] == "result"
    assert b'"twice": 84' in list(streams(got).values())[0][1]
    assert "kit" not in sys.modules, "dropped, so the next run loads anew"
    assert srv.root + "/lib" not in sys.path


def test_a_library_is_compiled_not_run_and_never_listed_as_a_tool():
    srv, port, hooks, host = server()
    host.hello()
    got = upload_lib(host, "boom.py", "raise ValueError('ran')\n")
    assert last(got)["t"] == "saved", "a library is not executed on upload"
    got = upload_lib(host, "bad.py", "def (:\n")
    assert last(got)["code"] == "syntax"
    assert host.ask({"t": "list"})[0]["tools"] == []


def test_remove_takes_a_library_by_name():
    srv, port, hooks, host = server()
    host.hello()
    upload_lib(host, "kit.py", LIB_MODULE)
    got = host.ask({"t": "remove", "names": ["kit"]})
    assert got[0]["removed"] == ["kit"]
    assert os.listdir(os.path.join(srv.root, "lib")) == []


def test_api_save_hands_the_entry_to_the_badge_under_the_tools_name():
    tool = '''
def run(api, args):
    api.save({"dark": 1})
    api.save({"1": 2}, "room")
'''
    srv, port, hooks, host = server({"saver.py": tool})
    host.hello()
    host.ask({"t": "run", "tool": "saver"})
    assert hooks.saved == [("saver", {"dark": 1}), ("room", {"1": 2})]


# --- with no host ----------------------------------------------------------------

def test_menu_entries_list_each_stored_tools_menu_by_name():
    root = tempfile.mkdtemp(prefix="devtests")
    for name, text in (("b.py", "MENU = (('bee', [1]),)\ndef run(a, b): pass\n"),
                       ("a.py", "MENU = (('ay', []), ('ay2', ['x']))\n"),
                       ("c.py", "def run(a, b): pass\n"),
                       ("Bad.py", "MENU = (('no', []),)\n"),
                       ("d.py", "raise ValueError\n")):
        with open(os.path.join(root, name), "w") as handle:
            handle.write(text)
    assert ds.menu_entries(root) == [("ay", "a", []), ("ay2", "a", ["x"]),
                                     ("bee", "b", ["1"])]
    assert ds.menu_entries(root + "/nowhere") == []


def test_run_local_runs_with_no_host_and_restores_the_panel():
    from devlink_fakes import Hooks
    root = tempfile.mkdtemp(prefix="devtests")
    os.mkdir(os.path.join(root, "lib"))
    with open(os.path.join(root, "lib", "kit2.py"), "w") as handle:
        handle.write("V = 7\n")
    with open(os.path.join(root, "t.py"), "w") as handle:
        handle.write("def run(api, args):\n"
                     "    import kit2\n"
                     "    r = api.ring_start()\n"
                     "    api.ring_stop()\n"
                     "    with api.stream('bin') as s:\n"
                     "        s.write(b'x')\n"
                     "    api.send({'a': 1})\n"
                     "    api.log('hi')\n"
                     "    api.save({'v': kit2.V})\n"
                     "    return args\n")
    hooks = Hooks()
    assert ds.run_local(root, hooks, "t", ["z"]) == ("ok", ["z"])
    assert hooks.saved == [("t", {"v": 7})] and hooks.restores == 1
    assert not hooks.ring_reader.running
    import sys
    assert "kit2" not in sys.modules
    hooks.held = True
    with open(os.path.join(root, "w.py"), "w") as handle:
        handle.write("def run(api, args):\n"
                     "    r = api.ring_start()\n"
                     "    while not api.cancelled():\n"
                     "        pass\n")
    assert ds.run_local(root, hooks, "w", []) == ("cancelled", None)
    assert not hooks.ring_reader.running
    with open(os.path.join(root, "x.py"), "w") as handle:
        handle.write("def run(api, args):\n    raise OSError('x')\n")
    outcome, error = ds.run_local(root, hooks, "x", [])
    assert outcome == "raised" and isinstance(error, OSError)


def test_clear_tools_deletes_every_subtool_and_library():
    root = tempfile.mkdtemp(prefix="devtests")
    os.mkdir(os.path.join(root, "lib"))
    for name in ("a.py", "lib/k.py", "notes.txt"):
        open(os.path.join(root, name), "w").close()
    assert ds.clear_tools(root) is True
    assert os.listdir(root) == ["lib"] and os.listdir(root + "/lib") == []
    assert ds.clear_tools(root + "/nowhere") is True
