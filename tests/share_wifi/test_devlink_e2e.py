"""End to end: the host program's Client and the badge's Server, wired
through an in-memory pipe and stepped cooperatively (each host read runs one
badge service), with the example subtools in devlink_examples.py and the real ones
from tests/share_wifi/devtests/."""

import os
import tempfile

import devlink_host as dh
from devlink_examples import EXAMPLES
from devlink_fakes import FakePort, FakeReader, Hooks, ds


class FakeRing:
    period_us = 20

    def __init__(self):
        self.pos = 0

    def position(self):
        self.pos += 5000
        return self.pos

    def health(self):
        return 0

    def read(self, index):
        return index & 0xFFF


class Pipe:
    """The serial port as the host sees it, with the badge on the far end.
    `cut()` unplugs it: the badge's writes go nowhere and, once what was
    sent has been read, the host's reads fail."""

    def __init__(self, server, port):
        self.server = server
        self.port = port
        self.unplugged = False

    def write(self, data):
        if self.unplugged:
            raise OSError(5, "unplugged")
        self.port.inbox.extend(data)

    def read(self, n):
        if not self.port.outbox:
            if self.unplugged:
                raise OSError(5, "unplugged")
            self.server.service()
        data = bytes(self.port.outbox[:n])
        del self.port.outbox[:n]
        return data

    def reset_input_buffer(self):
        self.port.outbox = bytearray()

    def close(self):
        pass

    def cut(self):
        self.unplugged = True
        self.port.short = True


def rig():
    root = tempfile.mkdtemp(prefix="devtests")
    port = FakePort()
    hooks = Hooks()
    ticks = [0]

    def ticks_ms():
        ticks[0] += 7
        return ticks[0]
    hooks.ticks_ms = ticks_ms
    reader = FakeReader()
    reader.ring = FakeRing()
    hooks.ring_reader = reader
    server = ds.Server(port, root, hooks, {})
    server.scan()
    pipe = Pipe(server, port)
    out = []
    saver = dh.Saver(tempfile.mkdtemp(prefix="out"), out=out.append)
    client = dh.Client(pipe, saver, out=out.append)
    client.hello()
    return client, server, port, pipe, saver, out, root


def example(name):
    """An example's path, written out, or a real subtool's."""
    if name not in EXAMPLES:
        return os.path.join(dh.TOOLS, name + ".py")
    path = os.path.join(tempfile.mkdtemp(prefix="examples"), name + ".py")
    with open(path, "w") as handle:
        handle.write(EXAMPLES[name])
    return path


def saved(saver):
    out = {}
    for name in os.listdir(saver.outdir):
        with open(os.path.join(saver.outdir, name), "rb") as handle:
            out[name] = handle.read()
    return out


def test_new_then_list_then_run_hello():
    client, server, port, pipe, saver, out, root = rig()
    assert client.upload(example("hello"))["t"] == "saved"
    assert os.path.exists(os.path.join(root, "hello.py"))
    tools = client.list()["tools"]
    assert [t["name"] for t in tools] == ["hello"]
    assert client.run("hello", ["a", "b"])["t"] == "result"
    files = saved(saver)
    txt = [v for k, v in files.items() if k.endswith(".txt")]
    js = [v for k, v in files.items() if k.endswith(".json")]
    assert txt == [b"hello from the badge: a b\n"]
    assert b'"tethered": true' in js[0] and b'"light": 400' in js[0]
    assert "hello: hello is running" in out
    assert all(k.startswith("hello.") for k in files)


def test_ring_trace_streams_its_readings_after_the_ring_stops():
    client, server, port, pipe, saver, out, root = rig()
    client.upload(example("ring_trace"))
    assert client.run("ring_trace", ["300"])["t"] == "result"
    csv = [v for k, v in saved(saver).items() if k.endswith(".csv")][0]
    lines = csv.decode().splitlines()
    assert lines[0] == "index,raw" and len(lines) == 1 + 300 * 1000 // 20
    assert not server.hooks.ring_reader.running


def test_spin_with_a_limit_ends_by_itself():
    client, server, port, pipe, saver, out, root = rig()
    client.upload(example("spin"))
    assert client.run("spin", ["0"])["t"] == "result"


def test_a_cut_mid_stream_leaves_a_part_and_the_badge_notices():
    client, server, port, pipe, saver, out, root = rig()
    client.upload(example("ring_trace"))
    writes = []

    def cut_later(data):
        writes.append(1)
        if len(writes) == 10:
            pipe.cut()
    port.on_write = cut_later
    try:
        client.run("ring_trace", ["300"])
    except dh.Disconnected:
        pass
    else:
        raise AssertionError("the host did not see the cut")
    names = os.listdir(saver.outdir)
    assert names and all(n.endswith(".part") for n in names), names
    assert server.lost and server.running is None
    assert len(writes) == 10, "the badge stopped writing once it noticed"


# --- the files subtool and sync ---------------------------------------------------

def test_files_lists_a_folder_and_sends_files_under_their_names():
    client, server, port, pipe, saver, out, root = rig()
    assert client.upload(example("files"))["t"] == "saved"
    tree = tempfile.mkdtemp(prefix="tree")
    os.mkdir(os.path.join(tree, "system"))
    with open(os.path.join(tree, "b.txt"), "wb") as handle:
        handle.write(b"hello")
    with open(os.path.join(tree, "system", "Big.BIN"), "wb") as handle:
        handle.write(bytes(range(256)) * 40)
    assert client.run("files", ["list", tree])["t"] == "result"
    listing = [v for k, v in saved(saver).items() if k.endswith(".json")][0]
    assert b'["b.txt", 5]' in listing and b'["system/", ' in listing
    frame = client.run("files", ["get", tree + "/b.txt",
                                 tree + "/system/Big.BIN"])
    assert frame["t"] == "result" and frame["files"] == 3
    by_name = saved(saver)
    assert [v for k, v in by_name.items() if k.startswith("files_b.")
            and k.endswith(".txt")] == [b"hello"]
    assert [v for k, v in by_name.items() if k.startswith("files_big.")
            and k.endswith(".bin")] == [bytes(range(256)) * 40]


def test_sync_sends_every_real_subtool_and_library_and_all_load():
    client, server, port, pipe, saver, out, root = rig()
    said = []
    shell = dh.Shell(client, out=said.append)
    from fakeviper import badge_modules
    with badge_modules():
        shell.onecmd("sync")
        tools = client.list()["tools"]
    assert shell.ok, said
    libs = sorted(n[:-3] for n in os.listdir(os.path.join(dh.TOOLS, "lib"))
                  if n.endswith(".py"))
    assert server.libs() == libs
    assert [t["name"] for t in tools if t["error"]] == [], tools
    assert {"mirror", "frame_probe", "room_flicker", "fast_loopback",
            "second_badge", "files"} <= {t["name"] for t in tools}


# --- remove, end to end -----------------------------------------------------------

def test_remove_by_name_through_the_server():
    client, server, port, pipe, saver, out, root = rig()
    client.upload(example("hello"))
    client.upload(example("spin"))
    frame = client.remove(["hello", "nope"])
    assert frame["removed"] == ["hello"]
    assert frame["errors"] == [["nope", "no such subtool"]]
    assert os.listdir(root) == ["spin.py"]
    assert [t["name"] for t in client.tools] == ["spin"], "list refreshed"
