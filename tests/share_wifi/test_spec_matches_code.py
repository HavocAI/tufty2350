"""The specification must agree with the implementation.

.agents/optical_data_transfer/spec.md, the authority, states
constants, control patterns, symbols and exit values as fact. A spec that has
quietly drifted from the code is worse than no spec, because the next person
trusts it. These tests parse the document and check the claims that can be
checked mechanically; the context's per-block overhead too.

They cannot check prose. Behaviour described in words still has to be kept in
step by hand when either side changes.
"""

import os
import re

from harness import APPS, REPO, load_module

protocol = load_module("share_wifi", "protocol")
DOCS = os.path.join(REPO, ".agents", "optical_data_transfer")
SPEC = os.path.join(DOCS, "spec.md")
CONTEXT = os.path.join(DOCS, "context.md")
# The app's own document: what share_wifi adds on top of the protocol.
APP_SPEC = os.path.join(REPO, ".agents", "share_wifi", "app-spec.md")


def spec_text():
    return open(SPEC).read()


def app_spec_text():
    return open(APP_SPEC).read()


def section(text, heading):
    """From `heading` to the next heading of the same or a higher level."""
    level = heading.split(" ", 1)[0]
    body = text.split(heading, 1)[1]
    stop = re.search(r"^#{1,%d} " % len(level), body, re.M)
    return body[:stop.start()] if stop else body


def split_values(cell):
    """A table cell's values, split at commas outside parentheses."""
    out, depth, cur = [], 0, ""
    for ch in cell:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    out.append(cur.strip())
    return out


def render(value):
    if isinstance(value, tuple):
        return ", ".join(render(v) for v in value)
    if isinstance(value, float):
        return "%.2f" % value
    return str(value)


def constant_rows():
    """(name, stated) for every row of the constant tables: section 3 and
    section 15. A row may name several constants, its values in order;
    a row naming one takes the whole cell, a tuple's included."""
    text = spec_text()
    tables = section(text, "## 3. Constants") + section(text, "## 15. Mains flicker")
    rows = []
    for names, cell in re.findall(
            r"^\| ((?:`[A-Z][A-Z_0-9]*`(?:, )?)+) \| ([^|]+) \|", tables, re.M):
        names = re.findall(r"`([A-Z][A-Z_0-9]*)`", names)
        cell = cell.strip()
        if len(names) == 1:
            rows.append((names[0], cell))
            continue
        values = split_values(cell)
        assert len(values) == len(names), (names, cell)
        rows.extend(zip(names, values))
    return rows


def test_the_spec_exists_where_the_code_says_it_does():
    source = open(os.path.join(APPS, "share_wifi", "protocol.py")).read()
    assert ".agents/optical_data_transfer/spec.md" in source
    assert os.path.exists(SPEC)


def test_constant_tables_match_the_module():
    checked = 0
    for name, stated in constant_rows():
        actual = getattr(protocol, name, None)
        assert actual is not None, "%s is in a table but not in the module" % name
        rendered = render(actual)
        if isinstance(actual, tuple) and stated.startswith("("):
            rendered = "(%s)" % rendered
        assert stated == rendered, \
            "%s: spec says %r, code says %r" % (name, stated, rendered)
        checked += 1
    assert checked >= 100, "only matched %d constants; have the tables moved?" % checked


# Documented by a table of their own, or not a tuning value a reimplementer
# would need: symbols, control patterns, exit values, phase identifiers, and the
# match strings derived from the patterns.
DOCUMENTED_ELSEWHERE = {"HIGH", "MID", "LOW", "OK",
                        "FAST_BEGIN", "CRC_GOOD", "CRC_BAD",
                        "SYNC_ERROR", "COMPLETE", "COMPLETE_ACK",
                        "STAIRCASE",
                        "LEVEL_SCHEMES", "SCHEME_BEACON", "SCHEME_BACKLIGHT"}


def module_constants():
    """Every module-level constant in protocol.py, read from the source,
    tuple assignments included.

    Derived rather than listed. A hand-maintained list is exactly the thing that
    drifts: STALL_SLOTS, MAX_BLOCK_ATTEMPTS and BLOCK_TIMEOUT_MS were all
    added to the spec table and all three were missing from the list that was
    supposed to enforce it, so nothing would have noticed them being deleted.
    """
    source = open(os.path.join(APPS, "share_wifi", "protocol.py")).read()
    names = set()
    for lhs in re.findall(r"^([A-Z][A-Z_0-9]*(?:, [A-Z][A-Z_0-9]*)*)\s*=", source, re.M):
        names.update(lhs.split(", "))
    return {n for n in names
            if n not in DOCUMENTED_ELSEWHERE
            and not n.startswith(("ERR_", "PHASE_"))
            and not n.endswith("_TAIL")}


def test_every_module_constant_is_documented():
    # Anything a reader would need in order to reimplement this.
    documented = {name for name, _ in constant_rows()}
    expected = module_constants()
    assert expected <= documented, \
        "in protocol.py but not in the spec's constant tables: %s" \
        % sorted(expected - documented)
    assert len(expected) >= 100, \
        "only found %d constants; has the parsing broken?" % len(expected)


def test_control_patterns_match():
    patterns = {
        "Fast Begin": protocol.FAST_BEGIN,
        "CRC Good": protocol.CRC_GOOD,
        "CRC Bad": protocol.CRC_BAD,
        "Data Sync Error": protocol.SYNC_ERROR,
        "Complete": protocol.COMPLETE,
        "Complete Ack": protocol.COMPLETE_ACK,
    }
    text = section(spec_text(), "## 11. Control patterns")
    for name, value in patterns.items():
        row = re.search(r"^\| %s \| `([^`]*)` \|" % re.escape(name), text, re.M)
        assert row, "%s is not in the control pattern table" % name
        assert row.group(1) == value, \
            "%s: spec %r, code %r" % (name, row.group(1), value)


def test_exit_values_match():
    text = section(spec_text(), "## 17. Exit values")
    stated = set(re.findall(r"^\| `(OK|ERR_[A-Z_]+)` \|", text, re.M))
    actual = {n for n in dir(protocol) if n == "OK" or n.startswith("ERR_")}
    assert stated == actual, \
        "spec only: %s | code only: %s" % (sorted(stated - actual), sorted(actual - stated))


def test_symbol_table_matches():
    text = section(spec_text(), "## 4. Symbols and levels")
    for symbol, meaning in ((protocol.HIGH, "bit 1"),
                            (protocol.MID, "bit 0"),
                            (protocol.LOW, "never a data bit")):
        row = re.search(r"^\| `%s` [a-z]+ \|.*\| [^|]*%s[^|]* \|$"
                        % (re.escape(symbol), re.escape(meaning)), text, re.M)
        assert row, "symbol %r (%s) is not in the table as written" % (symbol, meaning)


def test_per_block_overhead_figure_is_right():
    # The context quotes a number a reader might budget against.
    overhead = (protocol.RESYNC_LEAD_IN + len(protocol.FAST_BEGIN)
                + protocol.FAST_RUNG_BITS + protocol.FAST_GAP_SLOTS
                + len(protocol.CRC_GOOD))
    stated = re.search(r"\*\*Per-block overhead\*\* (\d+) slots", open(CONTEXT).read())
    assert stated, "the context no longer quotes an overhead"
    assert int(stated.group(1)) == overhead, \
        "context says %s slots, computed %d" % (stated.group(1), overhead)


def test_no_references_to_deleted_phases():
    # A capability exchange and a verification burst both existed and both were
    # removed. The spec must not describe them as if they still run.
    text = spec_text()
    for phrase in ("Capability Pattern", "Rate Proposal", "ERR_SYNC_TIMEOUT",
                   "Calibration Ready Pattern", "Present beacon"):
        assert phrase not in text, "spec still mentions %r" % phrase


# --- the app's own claims ----------------------------------------------------
# The sections after "The share_wifi app" state values that live in
# __init__.py rather than protocol.py. They drift the same way and can be
# checked the same way.


def app_namespace():
    from harness import load_app
    return load_app("share_wifi")


def test_the_levels_in_the_spec_are_the_ones_transmitted():
    # The symbol table gives each level's backlight, per table. Getting
    # this wrong is how the middle level once came to be one calibration
    # could never accept.
    #
    # Split on a horizontal rule, not on "---": a markdown table's own
    # separator row is three dashes too.
    table = section(spec_text(), "## 4. Symbols and levels")
    rows = re.findall(r"^\| `(.)` [a-z]+ \|([^|]+)\|([^|]+)\|", table, re.M)
    assert len(rows) == 3, "expected three levels, parsed %r" % (rows,)
    cell = re.compile(r"([\d.]+)")
    for column, scheme in ((1, protocol.SCHEME_BEACON),
                           (2, protocol.SCHEME_BACKLIGHT)):
        for row in rows:
            symbol = row[0]
            if "not sent" in row[column]:
                continue    # the beacons send no middle
            found = cell.search(row[column])
            assert found, "%s, %r: no backlight in %r" % (scheme, symbol,
                                                         row[column])
            assert float(found.group(1)) == \
                protocol.LEVEL_SCHEMES[scheme][symbol], (scheme, symbol)
    assert set(protocol.LEVEL_SCHEMES) == {protocol.SCHEME_BEACON,
                                           protocol.SCHEME_BACKLIGHT}


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


def test_the_transfer_screen_in_the_spec_matches_the_app():
    # A white screen drawn once, run()'s clear off, no mode switch: the
    # spec says so and the app does so.
    app = app_namespace()
    text = section(spec_text(), "## 5. The screen")
    for phrase in ("whole screen", "rgb(255, 255, 255)", "clear off",
                   "present once", "only when the symbol changes",
                   "No screen-mode switch"):
        assert phrase in text, "the spec no longer says %r" % phrase
    screen, badge, display = app["screen"], app["badge"], app["display"]
    mode = badge.current_mode
    screen.fills = []
    app["begin_transfer_screen"]()
    assert badge.current_mode == mode, "a transfer switched mode"
    assert badge.default_clear is None
    assert len(screen.fills) == 1
    assert display.backlights[-1] == protocol.BEACON_LOW_BACKLIGHT
    assert display.updates >= 1, "the white was never presented"
    app["restore_panel"]()
    assert badge.default_clear is not None
    assert badge.current_mode == mode
