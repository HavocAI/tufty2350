"""The USB dev link's wire format, shared by the badge and the host program.

One frame is one line, in both directions, and begins with a newline:

    \\n#DL <crc32 as 8 lowercase hex> <json>\\n

The leading newline ends whatever fragment is on the wire before the frame
(a short write, mpremote's raw-REPL bytes, v2's stale TX tail), and `#DL ` is
also found part way through a line, the text before it being noise. The CRC
is binascii.crc32 over exactly the JSON bytes sent. The JSON is an object
whose "t" names its type; binary rides in a "b" field as base64. No byte
below 0x20 but the two newlines is ever in a frame, so neither Ctrl-C nor
the raw REPL's control bytes can be. A line without `#DL ` is noise: a
print(), the firmware's "- ERROR:" line, a REPL echo.

Pure: no badge globals, json and binascii only, the same under CPython and
MicroPython. The host imports this file from here.

Specification: "USB dev link" in .agents/share_wifi/app-spec.md.
"""

import binascii
import json

PROTO = 1
APP = "devlink 1"
PREFIX = b"#DL "

LINE_MAX_BADGE = 2048       # the longest line the badge accepts, bytes
LINE_MAX_HOST = 65536       # the longest the host accepts
CHUNK_IN = 512              # raw bytes in one upload `data` frame
CHUNK_OUT = 768             # raw bytes in one `chunk` frame from the badge
FIELD_CAP = 120             # chars: a tool's desc, args, load error
TEXT_CAP = 1024             # chars: log and error text
NAME_MAX = 24               # a tool name
SUBTEST_MAX = 49            # <tool>_<name>
EXT_MAX = 8
NEW_MAX = 65536             # bytes: the largest subtool or library `new` accepts


class Noise:
    """A line that was not a frame."""

    def __init__(self, text):
        self.text = text


class BadFrame:
    """A line that claimed to be a frame and was not one: why, and the line."""

    def __init__(self, reason, line=b""):
        self.reason = reason
        self.line = line


def crc32(data, value=0):
    return binascii.crc32(data, value) & 0xffffffff


def encode(obj):
    """One frame, as the bytes to write."""
    payload = json.dumps(obj).encode()
    return b"\n#DL " + ("%08x " % crc32(payload)).encode() + payload + b"\n"


def b64(data):
    return binascii.b2a_base64(data)[:-1].decode()


def unb64(text):
    return binascii.a2b_base64(text)


def _text(raw):
    try:
        return raw.decode()
    except UnicodeError:
        return str(raw)


class LineReader:
    """Bytes in, frames out. `feed()` returns a list of dicts (frames),
    Noise and BadFrame, in the order they arrived. A line longer than
    `limit` is dropped up to its newline, reported once as BadFrame."""

    def __init__(self, limit=LINE_MAX_BADGE):
        self.limit = limit
        self.buf = bytearray()
        self.skipping = False
        self.bad = 0

    def feed(self, data):
        out = []
        data = bytes(data)
        start = 0
        n = len(data)
        while start < n:
            nl = data.find(b"\n", start)
            end = n if nl < 0 else nl
            if self.skipping:
                if nl >= 0:
                    self.skipping = False
            else:
                self.buf.extend(data[start:end])
                if len(self.buf) > self.limit:
                    self.buf = bytearray()
                    self.skipping = nl < 0
                    self.bad += 1
                    out.append(BadFrame("too long"))
                elif nl >= 0:
                    line = bytes(self.buf)
                    self.buf = bytearray()
                    self._line(line, out)
            if nl < 0:
                break
            start = nl + 1
        return out

    def _line(self, line, out):
        if line.endswith(b"\r"):
            line = line[:-1]
        at = line.find(PREFIX)
        if at < 0:
            if line.strip():
                out.append(Noise(_text(line)))
            return
        if line[:at].strip():
            out.append(Noise(_text(line[:at])))
        rest = line[at + len(PREFIX):]
        frame = parse(rest)
        if isinstance(frame, BadFrame):
            self.bad += 1
        out.append(frame)


def parse(rest):
    """`<crc> <json>` to a frame dict, or a BadFrame."""
    if len(rest) < 10 or rest[8:9] != b" ":
        return BadFrame("short", rest)
    try:
        want = int(rest[:8], 16)
    except ValueError:
        return BadFrame("crc field", rest)
    payload = rest[9:]
    if crc32(payload) != want:
        return BadFrame("crc", rest)
    try:
        frame = json.loads(payload.decode())
    except (ValueError, UnicodeError):
        return BadFrame("json", rest)
    if not isinstance(frame, dict) or not isinstance(frame.get("t"), str):
        return BadFrame("no type", rest)
    return frame


class Assembler:
    """A stream or upload rebuilt from numbered chunks. `add()` gives None
    or why it refused; `finish()` the same for the declared size and CRC."""

    def __init__(self, limit):
        self.limit = limit
        self.data = bytearray()
        self.seq = 0
        self.crc = 0

    def add(self, seq, text):
        if seq != self.seq:
            return "chunk %d where %d was due" % (seq, self.seq)
        try:
            raw = unb64(text)
        except (ValueError, TypeError):
            return "bad base64"
        if len(self.data) + len(raw) > self.limit:
            return "too big"
        self.data.extend(raw)
        self.crc = crc32(raw, self.crc)
        self.seq += 1
        return None

    def finish(self, size, crc):
        if len(self.data) != size:
            return "size %d, %d declared" % (len(self.data), size)
        if self.crc != crc:
            return "crc"
        return None


_NAME_FIRST = "abcdefghijklmnopqrstuvwxyz"
_NAME_REST = _NAME_FIRST + "0123456789_"


def valid_name(text, limit=NAME_MAX):
    """A tool or subtest name: [a-z][a-z0-9_]*, at most `limit` long."""
    if not isinstance(text, str) or not 0 < len(text) <= limit:
        return False
    if text[0] not in _NAME_FIRST:
        return False
    for ch in text:
        if ch not in _NAME_REST:
            return False
    return True


def valid_tool_name(stem):
    return valid_name(stem, NAME_MAX)


def valid_subtest(name):
    return valid_name(name, SUBTEST_MAX)


def valid_ext(ext):
    """[a-z0-9]{1,8}: never a dot, a slash or anything a path could use."""
    if not isinstance(ext, str) or not 0 < len(ext) <= EXT_MAX:
        return False
    for ch in ext:
        if ch not in _NAME_REST or ch == "_":
            return False
    return True


def cap_head(text, n=FIELD_CAP):
    """`text` cut to `n` characters, keeping the start."""
    text = str(text)
    return text if len(text) <= n else text[:n - 3] + "..."


def cap_tail(text, n=TEXT_CAP):
    """`text` cut to `n` characters, keeping the end: a traceback's last
    lines are the ones that say what went wrong."""
    text = str(text)
    return text if len(text) <= n else "..." + text[len(text) - n + 3:]
