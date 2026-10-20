"""The USB dev link's wire format (devlink.py): one parser for both ends.

Also run under the real MicroPython unix port, where it is found, because the
badge's json writes non-ASCII raw where CPython escapes it and the CRC is over
the bytes as sent: a frame encoded by one must parse in the other.
"""

import json
import os
import shutil
import subprocess
import tempfile

from harness import APPS, Skipped, load_module

dl = load_module("share_wifi", "devlink")


def frames(items):
    return [i for i in items if isinstance(i, dict)]


def test_a_frame_round_trips():
    obj = {"t": "log", "text": "hello", "n": [1, 2], "rid": 3}
    out = dl.LineReader().feed(dl.encode(obj))
    assert out == [obj]


def test_a_frame_starts_and_ends_with_a_newline():
    raw = dl.encode({"t": "x"})
    assert raw.startswith(b"\n#DL ") and raw.endswith(b"\n")


def test_non_ascii_round_trips():
    obj = {"t": "log", "text": "23 °C"}
    assert dl.LineReader().feed(dl.encode(obj)) == [obj]


def test_no_control_byte_but_the_newlines():
    obj = {"t": "log", "text": "a\x03b\x01c\x04\td\r\n", "b": dl.b64(b"\x03")}
    raw = dl.encode(obj)
    inner = raw[1:-1]
    assert all(byte >= 0x20 for byte in inner), raw
    assert dl.LineReader().feed(raw) == [obj]


def test_a_bad_crc_is_a_bad_frame_not_an_exception():
    raw = bytearray(dl.encode({"t": "x", "v": 1}))
    raw[-3] = ord("2")          # inside the JSON: the CRC no longer matches
    reader = dl.LineReader()
    out = reader.feed(bytes(raw))
    assert len(out) == 1 and isinstance(out[0], dl.BadFrame)
    assert out[0].reason == "crc" and reader.bad == 1


def test_bad_json_and_a_missing_type_are_bad_frames():
    payload = b"{not json"
    line = b"#DL %08x " % dl.crc32(payload) + payload + b"\n"
    assert isinstance(dl.LineReader().feed(line)[0], dl.BadFrame)
    payload = b'{"x": 1}'
    line = b"#DL %08x " % dl.crc32(payload) + payload + b"\n"
    out = dl.LineReader().feed(line)
    assert out[0].reason == "no type"


def test_noise_comes_back_apart_from_frames():
    raw = b"- ERROR: boom\r\n" + dl.encode({"t": "a"}) + b">>> \n"
    out = dl.LineReader().feed(raw)
    noise = [i.text for i in out if isinstance(i, dl.Noise)]
    assert noise == ["- ERROR: boom", ">>> "]
    assert frames(out) == [{"t": "a"}]


def test_a_fragment_without_a_newline_does_not_swallow_the_next_frame():
    # mpremote's raw-REPL entry has no newline; nor does a short write.
    reader = dl.LineReader()
    out = reader.feed(b"\r\x03\x03\r\x01")
    assert out == []
    out = reader.feed(dl.encode({"t": "hello"}))
    assert frames(out) == [{"t": "hello"}]


def test_the_prefix_is_found_part_way_through_a_line():
    # The same fragment, had the frame lost its leading newline.
    raw = b"junk" + dl.encode({"t": "a"})[1:]
    out = dl.LineReader().feed(raw)
    assert frames(out) == [{"t": "a"}]
    assert out[0].text == "junk"


def test_a_frame_split_across_feeds():
    raw = dl.encode({"t": "a", "v": "x" * 100})
    reader = dl.LineReader()
    got = []
    for i in range(0, len(raw), 7):
        got += reader.feed(raw[i:i + 7])
    assert frames(got) == [{"t": "a", "v": "x" * 100}]


def test_a_long_line_is_dropped_up_to_its_newline_then_reading_resumes():
    reader = dl.LineReader(100)
    out = reader.feed(b"x" * 150)
    assert [i.reason for i in out] == ["too long"]
    out = reader.feed(b"y" * 50 + dl.encode({"t": "a"}))
    assert frames(out) == [{"t": "a"}]


def test_the_host_takes_lines_the_badge_would_refuse():
    big = {"t": "tools", "tools": ["x" * 100] * 300}
    raw = dl.encode(big)
    assert len(raw) > dl.LINE_MAX_BADGE
    assert dl.LineReader(dl.LINE_MAX_HOST).feed(raw) == [big]
    assert isinstance(dl.LineReader().feed(raw)[0], dl.BadFrame)


def test_the_assembler_checks_order_size_and_crc():
    a = dl.Assembler(10)
    assert a.add(0, dl.b64(b"abc")) is None
    assert a.add(0, dl.b64(b"abc")) is not None          # duplicate
    assert a.add(2, dl.b64(b"abc")) is not None          # gap
    assert a.add(1, dl.b64(b"defgh")) is None
    assert a.finish(8, dl.crc32(b"abcdefgh")) is None
    assert a.finish(9, dl.crc32(b"abcdefgh")) is not None
    assert a.finish(8, 1) == "crc"
    assert dl.Assembler(3).add(0, dl.b64(b"abcd")) == "too big"


def test_names_and_extensions():
    for good in ("a", "spin", "ring_trace", "x" * 24):
        assert dl.valid_tool_name(good), good
    for bad in ("", "1a", "_a", "A", "a-b", "a.b", "../x", "x" * 25, None):
        assert not dl.valid_tool_name(bad), bad
    for good in ("csv", "json", "bin", "u16le"):
        assert dl.valid_ext(good)
    for bad in ("", "../x", "a/b", "c.sv", "a_b", "x" * 9, None, "CSV"):
        assert not dl.valid_ext(bad), bad
    assert dl.valid_subtest("ring_trace_part2")


def test_caps_keep_the_right_end():
    assert dl.cap_head("abcdef", 5) == "ab..."
    assert dl.cap_tail("abcdef", 5) == "...ef"
    assert dl.cap_head("abc", 5) == "abc"


UNIX_SCRIPT = r"""
import sys
sys.path.insert(0, %r)
import devlink as d
for raw in %r:
    out = d.LineReader(d.LINE_MAX_HOST).feed(raw)
    print(len(out), out[0]["t"] if isinstance(out[0], dict) else "bad")
sys.stdout.write(d.encode({"t": "log", "text": "23 °C", "n": [1, 2]}))
data = bytes(range(256)) * 4
for seq in range(0, len(data) // 256):
    sys.stdout.write(d.encode({"t": "chunk", "sid": 1, "seq": seq,
                               "b": d.b64(data[seq * 256:seq * 256 + 256])}))
"""


def test_frames_cross_between_cpython_and_micropython():
    tool = os.environ.get("MICROPYTHON") or shutil.which("micropython")
    if not tool or not os.path.exists(tool):
        raise Skipped("no MicroPython unix port: set MICROPYTHON")
    sent = [dl.encode({"t": "hello", "id": "abc", "text": "23 °C"}),
            dl.encode({"t": "data", "b": dl.b64(bytes(range(200)))})]
    with tempfile.TemporaryDirectory() as out:
        script = os.path.join(out, "d.py")
        with open(script, "w") as handle:
            handle.write(UNIX_SCRIPT % (os.path.join(APPS, "share_wifi"), sent))
        result = subprocess.run([tool, script], capture_output=True)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.split(b"\n", 2)
    assert lines[0] == b"1 hello" and lines[1] == b"1 data", lines
    got = frames(dl.LineReader(dl.LINE_MAX_HOST).feed(lines[2]))
    assert got[0] == {"t": "log", "text": "23 °C", "n": [1, 2]}
    assert b"".join(dl.unb64(f["b"]) for f in got[1:]) == bytes(range(256)) * 4
    # And the badge's own JSON is what the CRC covered: raw UTF-8.
    assert "°".encode() in lines[2]
    json.loads(lines[2].split(b" ", 2)[2].split(b"\n")[0].decode())


def test_a_remove_and_its_reply_round_trip():
    for obj in ({"t": "remove", "names": ["spin", "hello"], "rid": 4},
                {"t": "remove", "names": [], "rid": 5},
                {"t": "removed", "removed": ["spin"],
                 "errors": [["nope", "no such subtool"]], "rid": 4}):
        assert dl.LineReader().feed(dl.encode(obj)) == [obj]
