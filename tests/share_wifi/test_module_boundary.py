"""The protocol module must stay free of the badge.

That separation is what lets the wire format be tested at all. If a `screen.`
or a `badge.` creeps into protocol.py, the module stops importing off the badge
and every test above it has to go back through the stub machinery. Cheaper to
assert the boundary than to rediscover it.
"""

import ast
import os
import subprocess
import sys

from harness import APPS, load_module

PROTOCOL = os.path.join(APPS, "share_wifi", "protocol.py")

# Names the firmware injects. None of them may appear in the protocol module.
BADGE_GLOBALS = {
    "badge", "screen", "color", "font", "rom_font", "image", "rect", "vec2",
    "run", "loop", "State", "wifi", "rtc", "spritesheet", "shape", "brush",
    "mat3", "tween", "algorithm", "HIRES", "LORES", "VSYNC", "DITHER",
    "BUTTON_A", "BUTTON_B", "BUTTON_C", "BUTTON_UP", "BUTTON_DOWN", "BUTTON_HOME",
}


def loaded_names(path):
    tree = ast.parse(open(path).read())
    bound, read = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            (bound if isinstance(node.ctx, ast.Store) else read).add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
    return bound, read


def test_protocol_references_no_badge_globals():
    _, read = loaded_names(PROTOCOL)
    leaked = sorted(read & BADGE_GLOBALS)
    assert not leaked, "protocol.py reaches for the badge: %s" % leaked


def test_protocol_imports_in_a_bare_interpreter():
    # Not merely "imports under the harness" -- imports with nothing set up at
    # all, in a separate process, which is the real claim being made. -B
    # because the child does not inherit the runner's dont_write_bytecode.
    result = subprocess.run(
        [sys.executable, "-B", "-c",
         "import sys; sys.path.insert(0, %r); import protocol; "
         "print(protocol.fast_stream([b'123456789']).hex())"
         % os.path.join(APPS, "share_wifi")],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0109313233343536373839"


def test_the_reader_is_injected_not_imported():
    # The one thing the protocol cannot do by itself is read the sensor. A
    # reader arrives as an argument -- the ring's -- and the protocol never
    # reaches for fastadc, machine or the badge.
    protocol = load_module("share_wifi", "protocol")
    source = open(PROTOCOL).read()
    for name in ("fastadc", "import machine", "rp2."):
        assert name not in source, name

    class Reader:
        asked = []

        def newest_us(self):
            self.asked.append("newest")
            return 0

    transfer = protocol.Transfer(True, (b"net", b"password1"), Reader())
    transfer.work(1000)
    assert transfer.reader is not None and Reader.asked, \
        "work() should ask the reader"


def test_reader_references_no_badge_globals():
    _, read = loaded_names(os.path.join(APPS, "share_wifi", "reader.py"))
    leaked = sorted(read & BADGE_GLOBALS)
    assert not leaked, "reader.py reaches for the badge: %s" % leaked


def test_the_dev_link_modules_reference_no_badge_globals():
    # devlink.py is the wire format the host imports too; devserver.py the
    # command server, handed the port and the badge. Neither may reach for
    # either. usbport.py is the badge-only exception, like fastadc.py.
    for module in ("devlink.py", "devserver.py", "usbport.py"):
        _, read = loaded_names(os.path.join(APPS, "share_wifi", module))
        leaked = sorted(read & BADGE_GLOBALS)
        assert not leaked, "%s reaches for the badge: %s" % (module, leaked)


def test_the_dev_link_modules_import_in_a_bare_interpreter():
    result = subprocess.run(
        [sys.executable, "-B", "-c",
         "import sys; sys.path.insert(0, %r); import devlink, devserver, "
         "usbport; print(devlink.PROTO)" % os.path.join(APPS, "share_wifi")],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"


def test_the_fast_block_modules_reference_no_badge_globals():
    # fastblock.py is the fast block's wire format and decoder, as pure as
    # protocol.py; fasttx.py touches registers, but through what it is
    # handed, like usbport.py.
    for module in ("fastblock.py", "fasttx.py"):
        _, read = loaded_names(os.path.join(APPS, "share_wifi", module))
        leaked = sorted(read & BADGE_GLOBALS)
        assert not leaked, "%s reaches for the badge: %s" % (module, leaked)
        source = open(os.path.join(APPS, "share_wifi", module)).read()
        for name in ("import machine", "import rp2", "import fastadc"):
            assert name not in source, (module, name)


def test_the_fast_block_modules_import_in_a_bare_interpreter():
    # Bare CPython: fastblock's viper kernel runs there as Python, through
    # its own stubs, with no fakeviper.
    result = subprocess.run(
        [sys.executable, "-B", "-c",
         "import sys; sys.path.insert(0, %r); import fastblock, fasttx; "
         "print(fastblock.common_div(200000000)[0], len(fastblock.encode("
         "1, b'')))" % os.path.join(APPS, "share_wifi")],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "16 141"


def test_reader_imports_in_a_bare_interpreter():
    result = subprocess.run(
        [sys.executable, "-B", "-c",
         "import sys; sys.path.insert(0, %r); import reader; "
         "print(reader.ref_sum([1, 2, 3, 4], 3, 1, 3))"
         % os.path.join(APPS, "share_wifi")],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "9"


def test_app_shell_still_exposes_the_protocol():
    # The app imports it with a star import; if that breaks, the app breaks.
    from harness import load_app
    app = load_app("share_wifi")
    protocol = load_module("share_wifi", "protocol")
    for name in ("Transfer", "OK", "ERR_NO_PEER"):
        assert name in app, "%s missing from the app namespace" % name
        assert app[name] is getattr(protocol, name)


# The firmware injects a specific set of names. Anything else must be imported,
# and getting that wrong is a NameError at launch that no off-badge test would
# otherwise catch. Taken from the builtins.* assignments in
# pimoroni/tufty2350 modules/common/badgeware/.
FIRMWARE_BUILTINS = {
    "badge", "screen", "display", "run", "launch", "loop", "State", "rtc",
    "reset", "fatal_error", "text", "add_glyph", "add_sprite",
    "clamp", "rnd", "frnd", "file_exists", "is_dir", "free",
    "LORES", "HIRES", "VSYNC", "DITHER",
    "FAST_UPDATE", "MEDIUM_UPDATE", "FULL_UPDATE",
    "BUTTON_A", "BUTTON_B", "BUTTON_C", "BUTTON_UP", "BUTTON_DOWN", "BUTTON_HOME",
    "LEFT", "CENTER", "RIGHT", "TOP", "MIDDLE", "BOTTOM", "CLIP", "ELLIPSES",
    "OFF", "X2", "X4",
    # everything picovector exports is hoisted too
    "image", "font", "rect", "vec2", "mat3", "color", "brush", "shape",
    "spritesheet", "tween", "algorithm", "spritesheet",
}

NOT_BUILTIN = {"wifi", "secrets", "ntptime", "network", "machine", "time", "gc",
               "json", "math", "random", "os", "sys"}

# Names deliberately probed inside a try/except NameError, to cope with firmware
# differences -- or, DEV_AUTORUN, set only by a host launching the app over
# the REPL (tests/usbrun.py). Usually absent; referencing them
# is safe.
PROBED = {"rom_font", "DEV_AUTORUN"}


def test_app_imports_everything_that_is_not_a_builtin():
    source = open(os.path.join(APPS, "share_wifi", "__init__.py")).read()
    tree = ast.parse(source)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name != "*":
                    imported.add(alias.asname or alias.name)

    bound, read = loaded_names(os.path.join(APPS, "share_wifi", "__init__.py"))
    # Protocol names included: the app imports them by name, not with `*`.
    unexplained = read - bound - imported - FIRMWARE_BUILTINS - PROBED
    unexplained -= set(dir(__builtins__)) if isinstance(__builtins__, dict) is False else set(__builtins__)
    assert not unexplained, "not a builtin and not imported: %s" % sorted(unexplained)


def test_wifi_is_imported_not_assumed_global():
    # `wifi` is modules/common/wifi.py in the firmware, not an injected global.
    # Using it bare is a NameError the moment the app launches.
    source = open(os.path.join(APPS, "share_wifi", "__init__.py")).read()
    tree = ast.parse(source)
    imports_wifi = any(
        isinstance(n, ast.Import) and any(a.name == "wifi" for a in n.names)
        for n in ast.walk(tree))
    uses_wifi = any(
        isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
        and n.value.id == "wifi" for n in ast.walk(tree))
    if uses_wifi:
        assert imports_wifi, "app uses wifi.* but never imports it"


# The protocol carries byte fields and nothing more. It used to know that a
# payload was an SSID and a key, how long each may be, and that one of them had
# to be text -- all of which is 802.11's business, not the transport's.
WIFI_VOCABULARY = ("ssid", "802.11", "wpa", "psk", "passphrase", "utf-8")


def test_protocol_knows_nothing_about_wifi():
    source = open(PROTOCOL).read().lower()
    found = [word for word in WIFI_VOCABULARY if word in source]
    # "share_wifi" appears once, naming the app as the first caller.
    assert found == [], "protocol.py still mentions %s" % found


def test_payload_is_an_arbitrary_list_of_byte_fields():
    protocol = load_module("share_wifi", "protocol")
    for fields in ([b"one"], [b"a", b"b"], [b"a", b"b", b"c"], [b"", b"x"]):
        assert protocol.parse_fast_stream(protocol.fast_stream(fields)) \
            == fields


def test_the_app_owns_credential_validation():
    from harness import load_app
    app = load_app("share_wifi")
    assert "valid_credential" in app, "the app must do its own checking now"
    assert app["valid_credential"]([b"net", b"password1", bytes([3])]) == \
        (b"net", b"password1", 3)
    assert app["valid_credential"]([b"net"]) is None
