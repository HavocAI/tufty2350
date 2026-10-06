"""The specification must agree with the implementation.

.agents/docs/optical-data-transfer-protocol.md states constants, control patterns and
exit values as fact. A spec that has quietly drifted from the code is worse than
no spec, because the next person trusts it. These tests parse the document and
check the claims that can be checked mechanically.

They cannot check prose. Behaviour described in words still has to be kept in
step by hand when either side changes.
"""

import os
import re

from harness import APPS, REPO, load_module

protocol = load_module("share_wifi", "protocol")
SPEC = os.path.join(REPO, ".agents", "docs", "optical-data-transfer-protocol.md")
# The app's own document: what share_wifi adds on top of the protocol.
APP_SPEC = os.path.join(REPO, ".agents", "docs", "share_wifi_spec.md")


def spec_text():
    return open(SPEC).read()


def app_spec_text():
    return open(APP_SPEC).read()


def test_the_spec_exists_where_the_code_says_it_does():
    source = open(os.path.join(APPS, "share_wifi", "protocol.py")).read()
    assert ".agents/docs/optical-data-transfer-protocol.md" in source
    assert os.path.exists(SPEC)


def test_constant_table_matches_the_module():
    # Scoped to the constants table, so the exit-value table two sections down
    # does not get parsed as constants.
    table = spec_text().split("## Constants", 1)[1].split("\n---\n", 1)[0]
    rows = re.findall(r"^\| `([A-Z][A-Z_]+)` \| ([^|]+) \|", table, re.M)
    checked = 0
    for name, stated in rows:
        actual = getattr(protocol, name, None)
        assert actual is not None, "%s is in the table but not in the module" % name
        stated = stated.strip()
        if isinstance(actual, tuple):
            rendered = ", ".join(str(v) for v in actual)
        elif isinstance(actual, float):
            rendered = "%.2f" % actual
        else:
            rendered = str(actual)
        assert stated == rendered, \
            "%s: spec says %r, code says %r" % (name, stated, rendered)
        checked += 1
    assert checked >= 15, "only matched %d constants; has the table moved?" % checked


# Documented by a table of their own, or not a tuning value a reimplementer
# would need: symbols, control patterns, exit values, phase identifiers, and the
# match strings derived from the patterns.
DOCUMENTED_ELSEWHERE = {"HIGH", "MID", "LOW", "OK", "SEPARATOR",
                        "BEGIN_BLOCK", "END_BLOCK", "CRC_GOOD", "CRC_BAD",
                        "SYNC_ERROR", "COMPLETE", "COMPLETE_ACK",
                        "STAIRCASE_MS", "STAIRCASE_BODY_MS",
                        "LEVEL_SCHEMES", "ASCENDING", "REVERSED"}


def module_constants():
    """Every module-level constant in protocol.py, read from the source.

    Derived rather than listed. A hand-maintained list is exactly the thing that
    drifts: STALL_SLOTS, MAX_BLOCK_ATTEMPTS and BLOCK_TIMEOUT_MS were all
    added to the spec table and all three were missing from the list that was
    supposed to enforce it, so nothing would have noticed them being deleted.
    """
    source = open(os.path.join(APPS, "share_wifi", "protocol.py")).read()
    names = set(re.findall(r"^([A-Z][A-Z_0-9]*)\s*=", source, re.M))
    return {n for n in names
            if n not in DOCUMENTED_ELSEWHERE
            and not n.startswith(("ERR_", "PHASE_"))
            and not n.endswith("_TAIL")}


def test_every_module_constant_is_documented():
    # Anything a reader would need in order to reimplement this.
    table = spec_text().split("## Constants", 1)[1].split("\n---\n", 1)[0]
    documented = set(re.findall(r"^\| `([A-Z][A-Z_0-9]*)` \|", table, re.M))
    expected = module_constants()
    assert expected <= documented, \
        "in protocol.py but not in the spec's constant table: %s" \
        % sorted(expected - documented)
    assert len(expected) >= 21, \
        "only found %d constants; has the parsing broken?" % len(expected)


def test_control_patterns_match():
    patterns = {
        "Begin Block": protocol.BEGIN_BLOCK,
        "End Block": protocol.END_BLOCK,
        "CRC Good": protocol.CRC_GOOD,
        "CRC Bad": protocol.CRC_BAD,
        "Data Sync Error": protocol.SYNC_ERROR,
        "Complete": protocol.COMPLETE,
        "Complete Ack": protocol.COMPLETE_ACK,
    }
    text = spec_text()
    for name, value in patterns.items():
        row = re.search(r"^\| %s \| `([^`]*)` \|" % re.escape(name), text, re.M)
        assert row, "%s is not in the control pattern table" % name
        assert row.group(1) == value, \
            "%s: spec %r, code %r" % (name, row.group(1), value)


def test_exit_values_match():
    stated = set(re.findall(r"^\| `(OK|ERR_[A-Z_]+)` \|", spec_text(), re.M))
    actual = {n for n in dir(protocol) if n == "OK" or n.startswith("ERR_")}
    assert stated == actual, \
        "spec only: %s | code only: %s" % (sorted(stated - actual), sorted(actual - stated))


def test_symbol_table_matches():
    text = spec_text()
    for symbol, meaning in ((protocol.HIGH, "binary 1"),
                            (protocol.MID, "binary 0"),
                            (protocol.LOW, "break, never payload")):
        row = re.search(r"^\| `%s` \|.*\| %s \|" % (re.escape(symbol), re.escape(meaning)),
                        text, re.M)
        assert row, "symbol %r (%s) is not in the table as written" % (symbol, meaning)


def test_per_block_overhead_figure_is_right():
    # The throughput section quotes a number a reader might budget against.
    overhead = (protocol.RESYNC_LEAD_IN + len(protocol.BEGIN_BLOCK)
                + len(protocol.END_BLOCK) + 16 + len(protocol.CRC_GOOD))
    stated = re.search(r"Per-block overhead is (\d+) slots", spec_text())
    assert stated, "throughput section no longer quotes an overhead"
    assert int(stated.group(1)) == overhead, \
        "spec says %s slots, computed %d" % (stated.group(1), overhead)


def test_no_references_to_deleted_phases():
    # A capability exchange and a verification burst both existed and both were
    # removed. The spec may explain why they are gone; it must not describe them
    # as if they still run.
    text = spec_text()
    for phrase in ("Capability Pattern", "Rate Proposal", "ERR_SYNC_TIMEOUT",
                   "Calibration Ready Pattern"):
        assert phrase not in text, "spec still mentions %r" % phrase


# --- the app's own claims ----------------------------------------------------
# The sections after "The share_wifi app" state values that live in
# __init__.py rather than protocol.py. They drift the same way and can be
# checked the same way.


def app_namespace():
    from harness import load_app
    return load_app("share_wifi")


# What the app transmits for "white" and "black": the panel's full range, as
# the spec writes it (FIELD_WHITE and FIELD_BLACK in __init__.py).
PALETTE_RGB = {"white": (255, 255, 255), "black": (0, 0, 0)}


def test_the_levels_in_the_spec_are_the_ones_transmitted():
    # The symbol table gives each level's pixels and backlight, per scheme.
    # Getting this wrong is how the middle field once came to be a level
    # calibration could never accept.
    #
    # Split on a horizontal rule, not on "---": a markdown table's own
    # separator row is three dashes too.
    table = spec_text().split("## Light levels and symbols", 1)[1]
    table = table.split("\n---\n", 1)[0]
    rows = re.findall(r"^\| `(.)` \| [^|]+ \|([^|]+)\|([^|]+)\|", table, re.M)
    assert len(rows) == 3, "expected three levels, parsed %r" % (rows,)
    cell = re.compile(r"rgb\((\d+, \d+, \d+)\) at `([\d.]+)`")
    for column, scheme in ((1, protocol.SCHEME_FIELDS),
                           (2, protocol.SCHEME_BACKLIGHT)):
        for row in rows:
            symbol = row[0]
            found = cell.search(row[column])
            assert found, "%s, %r: no 'rgb(...) at `x`' in %r" % (
                scheme, symbol, row[column])
            rgb = tuple(int(v) for v in found.group(1).split(", "))
            light = float(found.group(2))
            pixels, actual_light = protocol.LEVEL_SCHEMES[scheme][symbol]
            actual_rgb = PALETTE_RGB.get(pixels, pixels)
            assert rgb == tuple(actual_rgb), \
                "%s, %r: spec says rgb%r, code sends %r" % (scheme, symbol, rgb, pixels)
            assert light == actual_light, \
                "%s, %r: spec says backlight %r, code sets %r" % (
                    scheme, symbol, light, actual_light)


def test_the_security_codes_in_the_spec_are_the_ones_the_app_uses():
    app = app_namespace()
    section = app_spec_text().split("## Security types", 1)[1].split("\n## ", 1)[0]
    # The table is indented under a bullet, so allow leading whitespace.
    rows = re.findall(r"^\s*\| (\d+(?:, \d+)*) \| ([^|]+) \|", section, re.M)
    assert rows, "the security table did not parse"
    stated = set()
    for codes, _name in rows:
        for code in codes.split(", "):
            stated.add(int(code))
    known = set(app["SECURITY_NAMES"])
    assert stated <= known, \
        "spec lists codes the app does not know: %r" % sorted(stated - known)
    for name in ("SEC_OPEN", "SEC_WEP", "SEC_WPA", "SEC_WPA2", "SEC_WPA_WPA2",
                 "SEC_ENTERPRISE"):
        assert app[name] in stated, "%s (%d) is missing from the spec table" % (
            name, app[name])


def test_the_transmit_geometry_in_the_spec_matches_the_app():
    # The whole screen, at LORES, run()'s clear off: the spec says so and the
    # app does so.
    app = app_namespace()
    section = spec_text().split("## Screen as a transmitter", 1)[1]
    section = section.split("\n---\n", 1)[0]
    for phrase in ("whole screen", "`LORES | VSYNC`",
                   "`badge.default_clear = None`", "only when it changes"):
        assert phrase in section, "the spec no longer says %r" % phrase
    assert app["SIGNAL_MODE"] == app["LORES"] | app["VSYNC"]
    screen, badge = app["screen"], app["badge"]
    app["begin_signalling"]()
    assert badge.current_mode == app["SIGNAL_MODE"]
    assert badge.default_clear is None
    screen.rects = []
    screen.fills = []
    app["show_field"](protocol.HIGH)
    assert screen.rects == [] and len(screen.fills) == 1
    app["end_signalling"]()
    assert badge.current_mode == app["TEXT_MODE"]
