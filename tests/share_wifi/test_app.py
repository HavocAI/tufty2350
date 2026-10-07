"""The app shell: menus, navigation, the config file, and connecting.

These drive App.frame() the way run() would, with stubbed buttons, and assert
on what was drawn. They check the state machine, not the appearance.
"""

import math
import time

from harness import FakeSecrets, load_module
from harness import load_app as harness_load_app


def load_app(*args, **kwargs):
    """The app, with its transfer loop taking one reading per update().

    On the badge the loop reads until the pixels must change, which a test
    that scripts one field a frame -- or steps a real transfer whose peer
    never appears -- would never reach. The loop itself is tested below with
    TRANSFER_PASSES as the badge has it.
    """
    namespace = harness_load_app(*args, **kwargs)
    namespace["TRANSFER_PASSES"] = 1
    return namespace

# The protocol's own names, for those the app does not import itself.
protocol = load_module("share_wifi", "protocol")

CREDENTIALS = {"HomeNet": "hunter2!!", "Cafe": "flatwhite"}


def app_with(networks=None):
    namespace = load_app("share_wifi", networks=networks)
    return namespace, namespace["app"]


class Drawn(list):
    """What a frame drew; `text in drawn` ignores where lines were broken.

    The app breaks text into lines itself (layout_text), so a message arrives
    as "Turn the lights\noff. ..." -- and where the breaks fall depends on the
    font. Membership compares with every line break read as a space, which is
    what a test asking "was this message drawn?" means.
    """

    def __contains__(self, item):
        if list.__contains__(self, item):
            return True
        if not isinstance(item, str):
            return False
        flat = item.replace("\n", " ")
        return any(isinstance(d, str) and d.replace("\n", " ") == flat
                   for d in self)


def frame(namespace, *buttons):
    """One frame, with these buttons reading as newly pressed."""
    badge, screen = namespace["badge"], namespace["screen"]
    screen.drawn = []
    badge.press(*buttons)
    namespace["app"].frame()
    badge.tick()
    return Drawn(screen.drawn)


def test_main_menu_lists_every_action():
    namespace, app = app_with()
    drawn = frame(namespace)
    text = " ".join(str(d) for d in drawn)
    for item in namespace["MENU_ITEMS"]:
        assert item in text, "%r is not on the main menu" % item


def test_down_wraps_around_the_menu():
    # Counted from MENU_ITEMS rather than written out, so adding an entry does
    # not look like a broken test. What is being asserted is the wrapping.
    namespace, app = app_with()
    count = len(namespace["MENU_ITEMS"])
    assert app.cursor == 0
    for expected in range(1, count):
        frame(namespace, "DOWN")
        assert app.cursor == expected
    frame(namespace, "DOWN")
    assert app.cursor == 0, "bottom should wrap to the top"


def test_up_wraps_around_the_menu():
    namespace, app = app_with()
    frame(namespace, "UP")
    assert app.cursor == len(namespace["MENU_ITEMS"]) - 1, \
        "top should wrap to the bottom"


def test_connect_with_no_networks_says_so():
    namespace, app = app_with({})
    frame(namespace, "DOWN")
    frame(namespace, "DOWN")
    drawn = frame(namespace, "A")
    drawn = frame(namespace)
    assert any("No Networks" in str(d) for d in drawn)


def test_b_returns_to_the_main_menu_from_connect():
    namespace, app = app_with(CREDENTIALS)
    frame(namespace, "DOWN")
    frame(namespace, "DOWN")
    frame(namespace, "A")
    assert app.screen == "connect_list"
    frame(namespace, "B")
    assert app.screen == "main"


def test_b_on_the_main_screen_does_nothing():
    namespace, app = app_with()
    frame(namespace, "B")
    assert app.screen == "main", "B at the top level should be inert"


def test_share_asks_which_network_first():
    namespace, app = app_with(CREDENTIALS)
    frame(namespace, "A")                 # Share
    assert app.screen == "pick_share"
    drawn = frame(namespace)
    text = " ".join(str(d) for d in drawn)
    assert "HomeNet" in text and "Cafe" in text


def test_share_builds_a_payload_from_the_chosen_network():
    namespace, app = app_with(CREDENTIALS)
    frame(namespace, "A")                 # Share
    frame(namespace, "A")                 # take the first, alphabetically Cafe
    assert app.screen == "align"
    frame(namespace, "A")                 # past the alignment line
    assert app.screen == "transfer"
    transfer = app.transfer
    assert transfer.leader is True
    assert transfer.payload == [b"Cafe", b"flatwhite",
                                bytes([namespace["SEC_UNKNOWN"]])]
    assert transfer.blocks, "payload should already be split into blocks"


def test_receive_starts_as_the_follower_with_no_payload():
    namespace, app = app_with()
    frame(namespace, "DOWN")
    frame(namespace, "A")                 # Receive
    assert app.screen == "align"
    frame(namespace, "A")                 # past the alignment line
    assert app.screen == "transfer"
    assert app.transfer.leader is False
    assert app.transfer.payload is None


def test_connecting_calls_wifi_connect_every_frame():
    # connect() is non-blocking: calling it repeatedly is what moves the
    # attempt along, so once per frame is the contract.
    namespace, app = app_with(CREDENTIALS)
    frame(namespace, "DOWN")
    frame(namespace, "DOWN")
    frame(namespace, "A")                 # Connect
    frame(namespace, "A")                 # first network
    assert app.screen == "connecting"

    wifi = namespace["wifi"]
    before = len(wifi.calls)
    frame(namespace)
    frame(namespace)
    assert len(wifi.calls) == before + 2
    assert wifi.calls[-1] == ("Cafe", "flatwhite")


def test_successful_connection_reports_it():
    namespace, app = app_with(CREDENTIALS)
    frame(namespace, "DOWN")
    frame(namespace, "DOWN")
    frame(namespace, "A")
    frame(namespace, "A")
    namespace["wifi"].connected = True
    frame(namespace)
    assert app.screen == "connect_result"
    assert app.error is None


def test_failed_connection_shows_the_status_description():
    namespace, app = app_with(CREDENTIALS)
    frame(namespace, "DOWN")
    frame(namespace, "DOWN")
    frame(namespace, "A")
    frame(namespace, "A")
    namespace["wifi"].state = (-3, "auth failed")
    frame(namespace)
    assert app.screen == "connect_result"
    assert "auth failed" in app.message


def test_received_credential_is_saved_and_merged():
    # State.modify merges. State.save would drop every other network.
    namespace, app = app_with(CREDENTIALS)
    transfer = namespace["Transfer"](False)
    transfer.result = namespace["OK"]
    transfer.received = protocol.build_tokens(b"NewNet", b"letmein123")
    app.finish_transfer(transfer)

    stored = namespace["State"].data
    assert stored["NewNet"] == {"key": "letmein123",
                                "security": namespace["SEC_UNKNOWN"]}
    assert stored["HomeNet"] == "hunter2!!", "existing networks must survive"


def test_undecodable_received_payload_reports_decode_error():
    namespace, app = app_with()
    transfer = namespace["Transfer"](False)
    transfer.result = namespace["OK"]
    transfer.received = list(b"no separator here")
    app.finish_transfer(transfer)
    assert app.error == namespace["ERR_DECODE"]


def test_transfer_failure_offers_retry():
    namespace, app = app_with(CREDENTIALS)
    frame(namespace, "A")
    frame(namespace, "A")                 # start sharing
    frame(namespace, "A")                 # past the alignment line
    app.transfer.fail(namespace["ERR_NO_PEER"])
    frame(namespace)
    assert app.screen == "result"
    assert app.error == namespace["ERR_NO_PEER"]

    frame(namespace, "B")                 # retry
    assert app.screen == "align"
    frame(namespace, "A")                 # past the alignment line
    assert app.screen == "transfer"
    assert app.transfer.payload == [b"Cafe", b"flatwhite",
                                    bytes([namespace["SEC_UNKNOWN"]])]


def test_ssid_escaping_round_trips():
    namespace, _ = app_with()
    escape, unescape = namespace["escape_ssid"], namespace["unescape_ssid"]
    for raw in (b"PlainName",
                b"\xff\xfe raw",        # not valid UTF-8
                b"caf\xc3\xa9",         # valid UTF-8, non-ASCII
                b"%41",                 # looks exactly like our escape marker
                b"%",
                b"%hello",
                b"100%",                # a % that is not leading is unambiguous
                b"%%"):
        assert unescape(escape(raw)) == raw, raw


def test_escaped_ssid_is_not_confused_with_a_real_percent_name():
    # "%41" stored verbatim reads back as b"A" -- a different network, silently.
    namespace, _ = app_with()
    escape, unescape = namespace["escape_ssid"], namespace["unescape_ssid"]
    assert escape(b"%41") != "%41"
    assert unescape(escape(b"%41")) == b"%41"
    assert unescape(escape(b"A")) == b"A"


def test_malformed_stored_ssid_does_not_crash():
    # Anything we did not write should degrade, not take out the Share screen.
    namespace, _ = app_with()
    unescape = namespace["unescape_ssid"]
    for stored in ("%hello", "%4", "%zz", "%"):
        assert isinstance(unescape(stored), bytes)


def test_connecting_times_out():
    namespace, app = app_with(CREDENTIALS)
    frame(namespace, "DOWN")
    frame(namespace, "DOWN")
    frame(namespace, "A")
    frame(namespace, "A")
    assert app.screen == "connecting"

    # A radio that never connects and never reports failure must not hang here.
    namespace["wifi"].state = (0, "connecting")
    app.connect_started -= namespace["CONNECT_TIMEOUT_MS"] + 1
    frame(namespace)
    assert app.screen == "connect_result"
    assert app.error == "Timed out"


def test_valid_credential_accepts_real_credentials():
    namespace, _ = app_with()
    check = namespace["valid_credential"]
    unknown = namespace["SEC_UNKNOWN"]
    # Two fields: a badge that predates the security field. The type is
    # inferred rather than the credential refused.
    for ssid, key in [(b"HomeNet", b"hunter2!!"),
                      (b"A" * 32, b"k" * 63),
                      (b"A" * 32, b"k" * 64),      # hex PSK
                      (b"x", b"12345678"),
                      (b"\xff\xfe raw", b"password1")]:
        assert check([ssid, key]) == (ssid, key, unknown), (ssid, key)

    # Three fields, each security type with a key that suits it.
    for ssid, key, security in [
            (b"HomeNet", b"hunter2!!", namespace["SEC_WPA2"]),
            (b"HomeNet", b"k" * 64, namespace["SEC_WPA_WPA2"]),
            (b"OldNet", b"abcde", namespace["SEC_WEP"]),
            (b"OldNet", b"0123456789", namespace["SEC_WEP"]),
            (b"Cafe", b"", namespace["SEC_OPEN"])]:
        fields = [ssid, key, bytes([security])]
        assert check(fields) == (ssid, key, security), fields


def test_valid_credential_rejects_everything_else():
    # These used to be the protocol's rules. They are 802.11's, so they belong
    # to the app; the protocol just carries fields.
    namespace, _ = app_with()
    check = namespace["valid_credential"]
    cases = {
        "no fields": [],
        "one field": [b"ssid"],
        "four fields": [b"a", b"b", b"c", b"d"],
        "unknown security code": [b"net", b"password1", bytes([99])],
        "security field not one byte": [b"net", b"password1", b"\x00\x00"],
        "open network carrying a key": [b"net", b"password1", bytes([0])],
        "encrypted network with no key": [b"net", b"", bytes([3])],
        "enterprise cannot be carried": [b"net", b"password1", bytes([5])],
        "wep key of the wrong length": [b"net", b"abcdef", bytes([1])],
        "empty ssid": [b"", b"password1"],
        "ssid too long": [b"n" * 33, b"password1"],
        "key too short": [b"net", b"short"],
        "key too long": [b"net", b"k" * 65],
        "key not utf-8": [b"net", b"\xff\xfe" + b"abcdefgh"],
        "nothing at all": None,
    }
    for name, fields in cases.items():
        assert check(fields) is None, name


# --- State.load's real contract ----------------------------------------------
# The API notes say State.load(name, defaults) returns the saved data. The badge
# returns a bool, and App.__init__ died on sorted() before drawing a frame:
#
#     TypeError: bool object isn't iterable
#
# 139 tests passed at the time, because the stub modelled the documentation.
# These run the app both ways.


def app_against_the_badges_state(networks=None):
    namespace = load_app("share_wifi", networks=networks, state_returns_flag=True)
    return namespace, namespace["app"]


def test_the_app_starts_when_state_load_returns_a_flag():
    namespace, app = app_against_the_badges_state(CREDENTIALS)
    assert isinstance(app.networks, dict), \
        "networks should be a dict whatever State.load returns, got %r" % (app.networks,)
    assert app.names == sorted(CREDENTIALS)


def test_saved_networks_survive_a_flag_returning_state_load():
    # The failure worth guarding is not the crash, which is loud. It is coming
    # back with {} and silently forgetting every saved network.
    namespace, app = app_against_the_badges_state(CREDENTIALS)
    unknown = namespace["SEC_UNKNOWN"]
    expected = dict((name, (key, unknown)) for name, key in CREDENTIALS.items())
    assert app.networks == expected, \
        "saved networks were lost: %r" % (app.networks,)


def test_the_share_menu_lists_networks_under_either_contract():
    for flag in (False, True):
        namespace = load_app("share_wifi", networks=CREDENTIALS,
                             state_returns_flag=flag)
        frame(namespace, "A")                       # Share
        drawn = frame(namespace)
        text = " ".join(str(d) for d in drawn)
        for name in CREDENTIALS:
            assert name in text, "%s missing with returns_flag=%s" % (name, flag)


def test_an_empty_store_is_still_a_dict():
    namespace, app = app_against_the_badges_state({})
    assert app.networks == {}
    assert app.names == []


# --- drawing, with and without the alignment constants ----------------------
# The firmware hoists LEFT/CENTER/TOP/MIDDLE into `builtins`, so they are
# reachable by bare name and nowhere else -- not in the app's `globals()`, and
# not through `getattr(builtins, "CENTER")` either. An app that probes for them
# the wrong way gets "absent" on a badge that has them, which is what happened:
# the probe was `globals().get("CENTER")` and it could never answer yes.
#
# The harness now injects into `__builtins__` the way the firmware does, so the
# two arms below are a real question rather than a foregone one. `alignment`
# defaults to False because an app must still draw on a firmware without them.


def test_the_app_draws_with_no_alignment_constants_at_all():
    namespace, app = app_with(CREDENTIALS)
    assert namespace["_HAS_ALIGN"] is False
    drawn = frame(namespace)
    assert drawn, "nothing was drawn"


def test_the_alignment_probe_finds_constants_that_are_only_builtins():
    # The regression this exists for: an injected global is not in the module
    # namespace, so a probe through globals() reads None on a badge that has
    # the constant. Assert on the drawing call, not just the flag, because the
    # flag being right is only half of it.
    namespace = load_app("share_wifi", networks=CREDENTIALS, alignment=True)
    assert namespace["_HAS_ALIGN"] is True, \
        "the probe missed constants the firmware hoists into builtins"

    screen = namespace["screen"]
    passed = []
    real_text = screen.text
    screen.text = lambda m, *a, **k: (passed.append(k), real_text(m, *a, **k))[1]
    namespace["draw_centered"]("centre me")
    screen.text = real_text
    assert "align" in passed[-1], \
        "alignment was available and the app still placed the text by hand"


def test_every_screen_survives_both_firmware_dialects():
    for alignment in (False, True):
        namespace = load_app("share_wifi", networks=CREDENTIALS,
                             alignment=alignment)
        assert namespace["_HAS_ALIGN"] is alignment
        # Walk the menus: main, Share, its network list, back, then Debug.
        for buttons in (("A",), ("B",), ("DOWN",), ("DOWN",), ("DOWN",),
                        ("A",), ("B",)):
            drawn = frame(namespace, *buttons)
            assert drawn, "nothing drawn with alignment=%s" % alignment


def test_fit_truncates_to_the_width_it_is_given():
    namespace, app = app_with()
    fit, screen = namespace["fit"], namespace["screen"]
    assert fit("short", 400) == "short"
    clipped = fit("a network name far too long for this box", 80)
    assert clipped.endswith("...")
    assert screen.measure_text(clipped)[0] <= 80


def test_a_centred_line_is_placed_not_just_emitted():
    # Without align=, the x we pass is the whole of the centring. A regression
    # here draws everything hard against the left margin and looks fine to any
    # test that only asserts on the text.
    namespace, app = app_with()
    screen = namespace["screen"]
    placed = []
    real_text = screen.text
    screen.text = lambda m, *a, **k: (placed.append((m, a, k)), real_text(m, *a, **k))[1]
    namespace["draw_centered"]("centre me")
    screen.text = real_text
    message, args, kwargs = placed[-1]
    assert not kwargs, "the fallback path should pass no align= or overflow="
    assert args[0] > namespace["MARGIN"], \
        "centred text was left at the margin: x=%r" % (args[0],)


# --- the three transmit fields ----------------------------------------------
# Two schemes, from protocol.LEVEL_SCHEMES: SCHEME_FIELDS (the default) puts the
# level in the pixels at full backlight, SCHEME_BACKLIGHT puts it in the
# backlight with white pixels. Calibration rejects a staircase unless
# L_mid - L_black >= span / 4 and L_white - L_mid >= span / 4, so a middle level
# outside that window makes every transfer end ERR_CONTRAST no matter how good
# the optics are. Checking that is pure arithmetic: no part of it waits on a
# badge.

import simulator

# Relative luminance of the panel's output. Gamma is unmeasured beyond one
# mirror run, so the plausible range is checked: 1.0 (the framebuffer value IS
# the light) to 2.5. A field set that works at one end and not the other is not
# a field set.

PANEL_BLACK = (0, 0, 0)          # FIELD_BLACK: not the palette's tinted
PANEL_WHITE = (255, 255, 255)    # FIELD_WHITE  color.black and color.white
PALETTE_GREY = (78, 74, 78)      # color.grey -- what this used to transmit


def luminance(rgb, gamma):
    r, g, b = rgb
    return (0.2126 * (r / 255.0) ** gamma
            + 0.7152 * (g / 255.0) ** gamma
            + 0.0722 * (b / 255.0) ** gamma)


def separation(mid, gamma):
    """(low gap, high gap) as fractions of the swing, per the spec's check."""
    black = luminance(PANEL_BLACK, gamma)
    white = luminance(PANEL_WHITE, gamma)
    span = white - black
    return (luminance(mid, gamma) - black) / span, (white - luminance(mid, gamma)) / span


def schemes(namespace):
    return (namespace["SCHEME_FIELDS"], protocol.SCHEME_BACKLIGHT)


def fields_of(namespace, scheme=None):
    """(FIELD_COLOR, FIELD_BACKLIGHT) for one scheme, fields by default."""
    scheme = scheme or namespace["SCHEME_FIELDS"]
    return namespace["FIELD_COLOR"][scheme], namespace["FIELD_BACKLIGHT"][scheme]


def test_the_fields_scheme_holds_the_backlight_at_full():
    # Beacons are drawn in it, before anything is calibrated.
    namespace, app = app_with()
    colors, lights = fields_of(namespace)
    assert set(lights.values()) == {1.0}, \
        "the fields scheme should hold the backlight at full"


def test_a_transfer_reads_through_the_launchs_reader():
    # Under CPython there is no `machine`, so no ring: the POLLED reader,
    # light_level() into bins, made once per launch and the reason kept.
    namespace, app = app_with(CREDENTIALS)
    frame(namespace, "DOWN")
    frame(namespace, "A")                 # Receive
    frame(namespace, "A")                 # past the alignment line
    reader = app.transfer.reader
    assert reader is namespace["polled_reader"]()
    assert not app.transfer.exact
    assert namespace["ring_reader"]() is None
    assert namespace["ring_note"](), "why there is no ring is kept"
    app.transfer = None
    app.begin_transfer(False)
    assert app.transfer.reader is reader, "one reader per launch"


def test_the_app_draws_each_scheme_from_the_protocols_table():
    namespace, app = app_with()
    for scheme in schemes(namespace):
        colors, lights = fields_of(namespace, scheme)
        for symbol, (pixels, light) in namespace["LEVEL_SCHEMES"][scheme].items():
            assert colors[symbol] == namespace["field_pen"](pixels)
            assert lights[symbol] == light


# The fields scheme.

def test_the_middle_field_can_actually_be_calibrated():
    namespace, app = app_with()
    mid = fields_of(namespace)[0][protocol.MID]
    assert mid[0] == "rgb", "the middle field should be named, not a palette entry"
    for gamma in (1.0, 1.4, 1.8, 2.2, 2.5):
        low, high = separation(mid[1:4], gamma)
        assert low >= 0.25, "gamma %.1f: only %.0f%% above black" % (gamma, low * 100)
        assert high >= 0.25, "gamma %.1f: only %.0f%% below white" % (gamma, high * 100)


def test_the_palette_grey_is_kept_out_of_the_transmit_fields():
    # Named so the next person to reach for color.grey finds out why not here
    # rather than on two badges pointed at each other. It fails the check above
    # at every gamma.
    low, _ = separation(PALETTE_GREY, 2.2)
    assert low < 0.25, "color.grey passes now; this test has gone stale"
    namespace, app = app_with()
    color = namespace["color"]
    fields = fields_of(namespace)[0]
    assert fields[protocol.MID] != color.grey
    assert len(set((fields[protocol.LOW], fields[protocol.MID],
                    fields[protocol.HIGH]))) == 3, \
        "the three fields must be three distinct colours"


# The backlight scheme. simulator.backlight_duty() is the firmware's own
# value-to-duty curve, read out of modules/c/st7789/, and light is taken as
# proportional to duty -- true for an averaging sensor, the one case this
# scheme can work in. [inferred-v3]

def test_the_backlight_middle_level_can_actually_be_calibrated():
    namespace, app = app_with()
    share = simulator.backlight_duty(protocol.MID_BACKLIGHT)
    assert share >= 0.25, "only %.0f%% above black" % (share * 100)
    assert 1 - share >= 0.25, "only %.0f%% below white" % ((1 - share) * 100)


def test_the_obvious_backlight_values_are_kept_out_of_the_middle():
    # Named so the next person to reach for a quarter or a third finds out why
    # not here rather than on two badges pointed at each other. The firmware's
    # gamma-2.8 curve puts them at 14% and 16% duty, and both failed every
    # simulated transfer.
    for value in (0.25, 0.33):
        assert simulator.backlight_duty(value) < 0.25, \
            "backlight %.2f passes now; this test has gone stale" % value
    namespace, app = app_with()
    assert protocol.MID_BACKLIGHT not in (0.25, 0.33)


def test_the_backlight_scheme_never_changes_a_pixel():
    # Every symbol is white pixels; only the backlight moves, low being off.
    # A backlight change is live at once, where a pixel change waits for a
    # present and the liquid crystal's settle -- so the scheme is fast only
    # if it never touches a pixel. show_full() compares pens by identity, so
    # one pen object is what makes a symbol change a display.backlight()
    # call and nothing else.
    namespace, app = app_with()
    color = namespace["color"]
    fields, lights = fields_of(namespace, protocol.SCHEME_BACKLIGHT)
    low, mid, high = protocol.LOW, protocol.MID, protocol.HIGH
    assert lights[low] == protocol.LOW_BACKLIGHT == 0.5, \
        "low is the dim backlight, not off"
    assert lights[high] == 1.0
    assert 0.0 < lights[mid] < 1.0
    assert fields[low] is fields[mid] is fields[high]
    # The panel's full white, not the palette's tinted color.white.
    assert fields[high] == color.rgb(255, 255, 255)


def test_a_backlight_scheme_symbol_change_draws_nothing():
    namespace, app = app_with()
    display = namespace["display"]
    screen = namespace["screen"]
    show_field = namespace["show_field"]
    scheme = protocol.SCHEME_BACKLIGHT
    show_field(protocol.LOW, scheme)
    fills = len(screen.fills)
    backlights = len(display.backlights)
    for symbol in (protocol.HIGH, protocol.MID, protocol.LOW, protocol.HIGH):
        show_field(symbol, scheme)
    assert len(display.backlights) == backlights + 4
    assert display.backlights[-4:] == [1.0, protocol.MID_BACKLIGHT,
                                       protocol.LOW_BACKLIGHT, 1.0]
    assert len(screen.fills) == fills, "a symbol change refilled the screen"
    # The fields scheme's do change the pixels, and refill.
    show_field(protocol.LOW, protocol.SCHEME_FIELDS)
    assert len(screen.fills) == fills + 1


def test_fields_use_the_full_range_not_the_tinted_palette():
    # On the Tufty color.black is rgb(20, 30, 40) and color.white
    # rgb(222, 238, 214): drawing fields with them gave away part of the swing.
    namespace, app = app_with()
    color = namespace["color"]
    for scheme in schemes(namespace):
        fields, _ = fields_of(namespace, scheme)
        assert color.black not in fields.values(), scheme
        assert color.white not in fields.values(), scheme
    assert namespace["field_pen"]("white") == color.rgb(255, 255, 255)
    assert namespace["field_pen"]("black") == color.rgb(0, 0, 0)


# --- the backlight ----------------------------------------------------------

def test_every_frame_is_drawn_in_the_scheme_the_transfer_is_in():
    for scheme in ("fields", "backlight"):
        namespace = in_transfer()
        transfer = namespace["app"].transfer
        screen, display = namespace["screen"], namespace["display"]
        colors, lights = fields_of(namespace, scheme)
        pairs = set((colors[s], lights[s])
                    for s in (protocol.LOW, protocol.MID, protocol.HIGH))
        transfer.scheme = scheme          # as calibration would leave it
        symbols = [protocol.LOW, protocol.MID, protocol.HIGH] * 2
        scripted(namespace, symbols)
        for symbol in symbols:
            frame(namespace)
            # What is up after the frame, whichever of the two it changed:
            # the last pen filled and the last backlight set.
            pen, light = screen.fills[-1], display.backlights[-1]
            assert (pen, light) in pairs, \
                "%s: pen %r at backlight %r is not one of the three levels" % (
                    scheme, pen, light)
            assert (pen, light) == (colors[symbol], lights[symbol]), \
                (scheme, symbol, pen, light)


def test_a_transfer_puts_the_backlight_back_when_it_ends():
    # The firmware leaves it at 230/255 with nothing to read the setting back,
    # and a transfer raises it to full or, in the backlight scheme, can end on
    # black with it off -- so the app owes a restore on the way out.
    for scheme in ("fields", "backlight"):
        namespace, app = app_with(CREDENTIALS)
        display = namespace["display"]
        frame(namespace, "DOWN")
        frame(namespace, "A")             # Receive
        frame(namespace, "A")             # past the alignment line
        assert app.screen == "transfer"
        app.transfer.scheme = scheme
        frame(namespace)
        assert display.backlights[-1] in fields_of(namespace, scheme)[1].values()

        app.transfer.fail(namespace["ERR_CANCELLED"])
        frame(namespace)
        assert app.screen == "result"
        assert display.backlights[-1] == namespace["BACKLIGHT_NORMAL"], \
            "%s: the backlight was left at a transmit level" % scheme


# --- connecting -------------------------------------------------------------

def test_connect_hands_the_radio_the_ssid_and_not_the_stored_name():
    # Names are stored escaped, because a config key has to survive JSON. The
    # radio wants the octets back: "%41" on the list is the SSID "A".
    namespace, app = app_with({"%41": "hunter2!!"})
    frame(namespace, "DOWN")
    frame(namespace, "DOWN")
    frame(namespace, "A")                 # Connect
    frame(namespace, "A")                 # the one network
    assert app.screen == "connecting"
    assert app.connect_name == "%41", "the screen should still show the stored name"
    frame(namespace)                      # connect() goes out from the loop
    assert namespace["wifi"].calls[-1] == ("A", "hunter2!!")


def test_a_second_attempt_resets_the_radio_first():
    # wifi.connect() keeps a module-level WLAN and, once it exists, ignores the
    # SSID it is handed -- so without a disconnect first, picking a second
    # network reports the first one's result. It is also what puts back the
    # retry budget wifi spends by calling fatal_error().
    namespace, app = app_with(CREDENTIALS)
    wifi = namespace["wifi"]
    frame(namespace, "DOWN")
    frame(namespace, "DOWN")
    frame(namespace, "A")                 # Connect
    frame(namespace, "A")                 # first network
    assert wifi.disconnects == 1
    frame(namespace)
    first = wifi.calls[-1]

    app.start_connect("HomeNet")
    assert wifi.disconnects == 2
    frame(namespace)
    assert wifi.calls[-1] != first
    assert wifi.calls[-1] == ("HomeNet", "hunter2!!")


# --- this badge's own network -----------------------------------------------
# Seeded at launch so a badge that has never received a credential still has
# something to share. secrets is the only readable source: the CYW43 driver's
# config() will return the SSID it is associated with and has no getter for the
# key at all, so "whatever it is connected to now" cannot be recovered.

OWN = FakeSecrets(ssid="Havoc Guest", password="sail-the-seas-42")


def test_the_badge_shares_its_own_network_without_receiving_one_first():
    namespace = load_app("share_wifi", networks={}, secrets=OWN)
    app = namespace["app"]
    assert app.names == ["Havoc Guest"]
    assert app.networks["Havoc Guest"] == ("sail-the-seas-42",
                                           namespace["SEC_UNKNOWN"])
    # and it reached the config file, not just the list in RAM
    assert namespace["State"].data == {
        "Havoc Guest": {"key": "sail-the-seas-42",
                        "security": namespace["SEC_UNKNOWN"]}}


def test_the_seeded_network_can_be_picked_and_shared():
    # The point of seeding: an otherwise empty badge has something to send.
    namespace = load_app("share_wifi", networks={}, secrets=OWN)
    app = namespace["app"]
    frame(namespace, "A")                 # Share
    assert app.screen == "pick_share"
    frame(namespace, "A")                 # the seeded network
    assert app.screen == "align"
    frame(namespace, "A")                 # past the alignment line
    assert app.screen == "transfer"
    assert app.transfer.leader is True
    assert app.transfer.payload == [b"Havoc Guest", b"sail-the-seas-42",
                                    bytes([namespace["SEC_UNKNOWN"]])]


def test_a_name_already_in_the_file_is_not_overwritten():
    # It may have been received from another badge, or corrected by hand.
    # secrets does not get to undo that on every launch.
    stored = {"Havoc Guest": "a-better-key"}
    namespace = load_app("share_wifi", networks=stored, secrets=OWN)
    assert namespace["app"].networks["Havoc Guest"][0] == "a-better-key"


def test_seeding_does_not_write_to_flash_when_there_is_nothing_to_add():
    # Flash is ~256 blocks of 4KB and wears out, so seeding must be a
    # first-launch cost, not a per-launch one. This is about the APP's writes:
    # State.load() itself writes the defaults back when it finds no file, and
    # that one is the firmware's and happens with or without seeding.
    for networks, secrets in (({"Havoc Guest": "sail-the-seas-42"}, OWN),
                              ({"HomeNet": "hunter2!!"}, FakeSecrets()),
                              ({}, FakeSecrets())):
        namespace = load_app("share_wifi", networks=networks, secrets=secrets)
        assert namespace["State"].writes == 0, \
            "launch wrote to flash with nothing to seed: %r" % (networks,)


def test_an_unprovisioned_badge_seeds_nothing():
    # WIFI_SSID = "" is what the firmware ships in /system/secrets.py.
    namespace = load_app("share_wifi", networks={}, secrets=FakeSecrets())
    assert namespace["app"].names == []
    assert namespace["this_badge_credential"]() is None


def test_a_badge_on_an_open_network_seeds_it_as_open():
    # No password is not a missing password: it is an open network, and the
    # whole point of carrying a security type is that one can be shared.
    namespace = load_app("share_wifi", networks={},
                         secrets=FakeSecrets(ssid="Open Net", password=""))
    app = namespace["app"]
    assert app.names == ["Open Net"]
    assert app.networks["Open Net"] == ("", namespace["SEC_OPEN"])
    assert "Open" in " ".join(app.labels), \
        "the list should say so: %r" % (app.labels,)


def test_an_open_network_is_shared_and_connected_to_without_a_key():
    namespace = load_app("share_wifi", networks={},
                         secrets=FakeSecrets(ssid="Open Net", password=""))
    app = namespace["app"]
    frame(namespace, "A")                 # Share
    frame(namespace, "A")                 # the open network
    frame(namespace, "A")                 # past the alignment line
    assert app.transfer.payload == [b"Open Net", b"",
                                    bytes([namespace["SEC_OPEN"]])]

    # And the radio gets an empty key, which is what selects CYW43_AUTH_OPEN.
    app.start_connect("Open Net")
    frame(namespace)
    assert namespace["wifi"].calls[-1] == ("Open Net", "")


def test_a_key_too_short_for_wpa_is_not_seeded():
    # A five-character key could be WEP, but secrets.py records no type and
    # guessing one would save something the far end then refuses. Rejected by
    # the same valid_credential() a received payload goes through.
    namespace = load_app("share_wifi", networks={},
                         secrets=FakeSecrets(ssid="Some Net",
                                             password="short"))
    assert namespace["app"].names == [], \
        "seeded a credential the protocol will not carry"


def test_a_missing_secrets_module_is_not_fatal():
    # Both /secrets.py and /system/secrets.py absent. The badge loses the
    # feature; it does not lose the app.
    import sys
    saved = sys.modules.pop("secrets", None)

    class Missing(dict):
        def __getitem__(self, key):
            raise ImportError("no module named 'secrets'")

    try:
        namespace = load_app("share_wifi", networks={"HomeNet": "hunter2!!"})
        del sys.modules["secrets"]
        assert namespace["this_badge_credential"]() is None
        assert namespace["app"].names == ["HomeNet"]
    finally:
        if saved is not None:
            sys.modules["secrets"] = saved


def test_an_escaped_ssid_is_seeded_under_its_escaped_name():
    # A name beginning with "%" is stored escaped, or "%41" reads back as "A".
    namespace = load_app("share_wifi", networks={},
                         secrets=FakeSecrets(ssid="%41", password="hunter2!!"))
    app = namespace["app"]
    # The whole name is hex-escaped, not just the leading "%": b"%41" is
    # 0x25 0x34 0x31.
    assert app.names == ["%253431"], "seeded under %r" % app.names
    assert namespace["radio_ssid"]("%253431") == "%41"


# --- the transmitted field --------------------------------------------------
# During a transfer the whole screen is the field, at LORES, and nothing else
# is drawn. On the badge (v2 firmware, 60 Hz) a HIRES fill costs 11.4 ms and a
# frame of two bands and a text column took three refreshes; LORES is a
# quarter of the pixels. run()'s own clear is switched off and the field is
# drawn only when it changes. See "Screen as a transmitter" in the protocol.


def in_transfer(leader=False):
    """A namespace parked on the transfer screen, one frame drawn."""
    namespace = load_app("share_wifi", networks=CREDENTIALS, alignment=True)
    if leader:
        frame(namespace, "A")             # Share
        frame(namespace, "A")             # first network
    else:
        frame(namespace, "DOWN")
        frame(namespace, "A")             # Receive
    assert namespace["app"].screen == "align"
    frame(namespace, "A")                 # past the alignment line
    assert namespace["app"].screen == "transfer"
    return namespace


def scripted(namespace, fields):
    """Make the transfer's field_at() -- the loop's first call, once a pass
    -- return these fields, one a frame, and its work do nothing."""
    queue = list(fields)
    transfer = namespace["app"].transfer
    transfer.field_at = lambda t: queue.pop(0)
    transfer.work = lambda t: None


def test_a_transfer_signals_at_lores_with_runs_clear_off():
    namespace = in_transfer()
    badge = namespace["badge"]
    assert badge.current_mode == namespace["LORES"] | namespace["VSYNC"]
    assert badge.default_clear is None, \
        "run() would clear the screen before every frame"


def test_the_field_is_the_whole_screen_and_nothing_else():
    namespace = in_transfer()
    screen = namespace["screen"]
    scripted(namespace, [protocol.HIGH, protocol.LOW, protocol.MID])
    colors = namespace["FIELD_COLOR"][namespace["app"].transfer.scheme]
    for symbol in (protocol.HIGH, protocol.LOW, protocol.MID):
        screen.rects = []
        screen.fills = []
        drawn = frame(namespace)
        assert screen.fills == [colors[symbol]], screen.fills
        assert screen.rects == [], "bands drawn during a transfer"
        assert list(drawn) == [], "text drawn during a transfer: %r" % (
            list(drawn),)


# --- the listen loop, as the badge runs it --------------------------------------

class FakeReader:
    """A reader whose clock moves 1 ms each time it is read."""

    def __init__(self):
        self.t = 0
        self.stops = 0
        self.pumped = []

    def pump(self, until):
        self.pumped.append(until)

    def now_us(self):
        self.t += 1000
        return self.t

    def stop(self):
        self.stops += 1


class LoopTransfer:
    """Stands in for a Transfer: the fields to show, one a pass, and DONE
    once they run out. Counts its passes."""

    def __init__(self, protocol_scheme, fields, leader=True):
        self.scheme = protocol_scheme
        self.fields = list(fields)
        self.phase = protocol.PHASE_DATA
        self.result = None
        self.leader = leader
        self.received = []
        self.calls = 0
        self.reader = FakeReader()
        self.collect_due = False

    def field_at(self, t):
        self.calls += 1
        if not self.fields:
            self.result = protocol.OK
            self.phase = protocol.PHASE_DONE
            return protocol.LOW
        return self.fields.pop(0)

    def work(self, t):
        pass

    def due_us(self):
        return self.reader.t

    def fail(self, code):
        self.result = code
        self.phase = protocol.PHASE_DONE


def looping(leader=True):
    """A namespace on the transfer screen with the loop as the badge has it."""
    namespace = in_transfer(leader)
    namespace["TRANSFER_PASSES"] = 0
    return namespace


def test_the_listen_loop_changes_the_backlight_without_presenting():
    # The backlight scheme: every symbol white, so a whole exchange runs in
    # one update() -- every backlight change made in place, nothing presented
    # and nothing filled -- until the transfer ends.
    namespace = looping()
    app, display, screen = namespace["app"], namespace["display"], namespace["screen"]
    scheme = protocol.SCHEME_BACKLIGHT
    # The white is already up, as the backlight staircase left it.
    namespace["show_full"](namespace["FIELD_COLOR"][scheme][protocol.LOW],
                           protocol.LOW_BACKLIGHT)
    symbols = [protocol.LOW] * 5 + [protocol.HIGH] * 40 + [protocol.MID] * 40 \
        + [protocol.LOW] * 20
    fake = LoopTransfer(scheme, symbols)
    app.transfer = fake
    presents, fills = display.updates, len(screen.fills)
    lights = len(display.backlights)
    app.do_transfer()
    assert fake.calls == len(symbols) + 1, "one field a pass, then done"
    assert display.updates == presents, "the loop presented"
    assert len(screen.fills) == fills, "the loop filled the screen"
    # Low, high, middle, low: each change made once.
    assert display.backlights[lights:lights + 3] == \
        [1.0, protocol.MID_BACKLIGHT, protocol.LOW_BACKLIGHT], \
        display.backlights[lights:]
    assert app.screen != namespace["TRANSFER"], "a finished transfer moves on"


def test_the_listen_loop_returns_to_present_a_change_of_pixels():
    namespace = looping()
    app, screen = namespace["app"], namespace["screen"]
    scheme = protocol.SCHEME_FIELDS
    namespace["show_full"](namespace["FIELD_COLOR"][scheme][protocol.LOW], 1.0)
    fake = LoopTransfer(scheme, [protocol.LOW] * 30 + [protocol.HIGH] * 30)
    app.transfer = fake
    fills = len(screen.fills)
    app.do_transfer()
    # Thirty blacks read without drawing, then the high drawn and handed to
    # run() to present: thirty-one readings.
    assert fake.calls == 31, fake.calls
    assert screen.fills[fills:] == [namespace["FIELD_COLOR"][scheme][protocol.HIGH]]
    assert app.screen == namespace["TRANSFER"]


def test_the_reader_lives_for_the_whole_transfer():
    # Built in begin_transfer and stopped in finish_transfer, the single
    # exit: do_transfer() returns to run() at every pixel change, and must
    # not touch the reader then.
    namespace = looping()
    app = namespace["app"]
    scheme = protocol.SCHEME_FIELDS
    namespace["show_full"](namespace["FIELD_COLOR"][scheme][protocol.LOW], 1.0)
    fake = LoopTransfer(scheme, [protocol.LOW, protocol.HIGH, protocol.LOW,
                                 protocol.HIGH, protocol.LOW])
    app.transfer = fake
    returns = 0
    while app.screen == namespace["TRANSFER"]:
        app.do_transfer()
        returns += 1
        assert returns < 20
    assert returns >= 4, "every pixel change returns to run()"
    assert fake.reader.stops == 1, "stopped once, at the end"


def test_a_collection_the_protocol_asks_for_happens_between_its_calls():
    namespace = looping()
    app = namespace["app"]
    scheme = protocol.SCHEME_BACKLIGHT
    namespace["show_full"](namespace["FIELD_COLOR"][scheme][protocol.LOW],
                           protocol.LOW_BACKLIGHT)
    fake = LoopTransfer(scheme, [protocol.LOW] * 4)
    order = []
    fake.work = lambda t: (order.append("work"),
                           setattr(fake, "collect_due", True))
    import gc
    real = gc.collect
    gc.collect = lambda *a: order.append("collect") or real()
    try:
        app.transfer = fake
        app.do_transfer()
    finally:
        gc.collect = real
    assert "collect" in order and order[0] == "work"
    assert all(order[i] != order[i + 1] or order[i] == "work"
               for i in range(len(order) - 1)), order
    assert not fake.collect_due


def test_on_exit_stops_the_ring_and_never_raises():
    # HOME calls on_exit() and then resets; a raise would skip the reset.
    namespace, app = app_with()

    class Ring:
        stopped = 0

        def stop(self):
            Ring.stopped += 1
            raise OSError("anything")
    namespace["_READERS"]["ring"] = Ring()
    namespace["on_exit"]()
    assert Ring.stopped == 1
    namespace["_READERS"]["ring"] = None
    namespace["on_exit"]()


def test_b_cancels_from_inside_the_listen_loop():
    namespace = looping()
    app, badge = namespace["app"], namespace["badge"]
    scheme = protocol.SCHEME_BACKLIGHT
    namespace["show_full"](namespace["FIELD_COLOR"][scheme][protocol.LOW], 0.0)
    fake = LoopTransfer(scheme, [protocol.LOW] * 1000)
    app.transfer = fake
    polls = [0]

    def poll():
        # B goes down part way through: the loop's own poll must see it.
        polls[0] += 1
        badge.queued = [namespace["BUTTON_B"]] if polls[0] == 3 else []
    badge.poll = poll
    app.do_transfer()
    assert fake.result == protocol.ERR_CANCELLED
    assert fake.calls < 1000 and polls[0] >= 3
    # One poll every POLL_MS of the reader's clock, which moves a ms a pass.
    assert fake.calls <= 3 * namespace["POLL_MS"] + 2, fake.calls


def test_a_transfer_starts_its_reader_and_its_clock_at_zero():
    namespace = in_transfer()
    transfer = namespace["app"].transfer
    assert transfer.started == 0 and transfer.phase_started == 0
    assert transfer.reader.now_us() < 10 ** 6


def test_the_field_is_drawn_only_when_it_changes():
    # The framebuffer keeps the field, and run()'s clear is off, so a frame
    # that changes nothing has nothing to draw.
    namespace = in_transfer()
    screen, display = namespace["screen"], namespace["display"]
    sequence = [protocol.HIGH, protocol.HIGH, protocol.HIGH, protocol.LOW,
                protocol.LOW, protocol.HIGH]
    scripted(namespace, [protocol.LOW] + sequence)
    frame(namespace)                    # LOW: the first frame draws
    draws = []
    for _ in sequence:
        screen.fills = []
        display.backlights = []
        frame(namespace)
        draws.append(len(screen.fills))
        # The fields scheme holds the backlight at full: never set again.
        assert display.backlights == []
    assert draws == [1, 0, 0, 1, 0, 1], draws


def test_the_field_carries_a_protocol_level_and_nothing_else():
    namespace = in_transfer()
    screen, app = namespace["screen"], namespace["app"]
    levels = set(namespace["FIELD_COLOR"][app.transfer.scheme].values())
    for _ in range(12):
        screen.fills = []
        frame(namespace)
        for pen in screen.fills:
            assert pen in levels, \
                "field pen %r is not one of the three levels" % (pen,)


def test_leaving_a_transfer_brings_back_hires_and_runs_clear():
    namespace = in_transfer()
    badge, app = namespace["badge"], namespace["app"]
    frame(namespace, "B")               # cancel
    assert app.screen == "result"
    assert badge.current_mode == namespace["HIRES"] | namespace["VSYNC"]
    assert badge.default_clear == namespace["color"].black
    assert namespace["display"].backlights[-1] == namespace["BACKLIGHT_NORMAL"]
    drawn = frame(namespace)
    assert drawn, "the result screen drew nothing"


def test_a_new_transfer_redraws_its_first_field():
    # A mode change reinterprets the framebuffer, so what was "on screen"
    # before it is not -- the first field after one must be drawn.
    namespace = in_transfer()
    screen, app = namespace["screen"], namespace["app"]
    scripted(namespace, [protocol.LOW] * 2)
    frame(namespace)
    frame(namespace, "B")               # cancel
    frame(namespace, "B")               # the result screen's retry
    frame(namespace, "A")               # past the alignment line
    assert app.screen == "transfer", app.screen
    scripted(namespace, [protocol.LOW])
    screen.fills = []
    frame(namespace)
    assert screen.fills, "the first field of a new transfer was not drawn"


def test_menus_still_use_the_whole_width():
    # The bands are Dev Mode's handshake screen's rule. A menu is measured by
    # nobody, and halving it would cost the help lines their text.
    namespace = load_app("share_wifi", networks=CREDENTIALS, alignment=True)
    screen = namespace["screen"]
    screen.boxes = []
    frame(namespace)
    widest = max(x + w for _, x, y, w, h in screen.boxes)
    assert widest > screen.width - screen.width // 4, \
        "the main menu was confined to the transfer screen's text column"


def test_a_message_with_room_to_wrap_is_not_truncated_instead():
    # fit() measures the string unwrapped, so running it before handing a rect
    # to the layout engine turns a message that would have wrapped into one cut
    # short. The narrow left column made that the common case.
    namespace = load_app("share_wifi", networks=CREDENTIALS, alignment=True)
    screen = namespace["screen"]
    long_message = "Can't see the other screen. Move closer, dim the lights."
    screen.drawn = []
    namespace["draw_text"](long_message, (0, 0, 120, namespace["LINE_HEIGHT"] * 4))
    assert screen.drawn[-1].replace("\n", " ") == long_message, \
        "truncated instead of wrapped: %r" % (screen.drawn[-1],)
    assert "\n" in screen.drawn[-1], "it is wider than the box: it must wrap"

    # One line high, though, and there is nowhere for the rest to go.
    screen.drawn = []
    namespace["draw_text"](long_message, (0, 0, 120, namespace["LINE_HEIGHT"]))
    assert screen.drawn[-1].endswith("..."), \
        "a single-line box should still truncate: %r" % (screen.drawn[-1],)


# --- WEP -------------------------------------------------------------------
# A WEP credential is carried, stored and shared like any other. Only joining
# is refused, and only because the radio driver has no WEP support: it rejects
# the auth type before touching the chip and has no code to install a WEP key.
# The refusal is gated on a live probe rather than hardcoded, so a firmware
# that gained WEP would stop being blocked here.

WEP_NET = {"OldRouter": {"key": "abcde", "security": 1}}


def wep_capable(enabled):
    """Pretend network.WLAN does or does not advertise a WEP security type."""
    import sys
    WLAN = sys.modules["network"].WLAN
    if enabled:
        WLAN.SEC_WEP = 1
    elif hasattr(WLAN, "SEC_WEP"):
        del WLAN.SEC_WEP


def test_connecting_to_wep_is_refused_with_the_reason():
    namespace, app = app_with(WEP_NET)
    wep_capable(False)
    frame(namespace, "DOWN")
    frame(namespace, "DOWN")
    frame(namespace, "A")                 # Connect
    frame(namespace, "A")                 # the WEP network
    assert app.screen == "connect_result"
    assert app.error == namespace["WEP_UNSUPPORTED"]
    assert "WEP networks not supported" in app.message
    assert namespace["wifi"].calls == [], \
        "the radio should not have been touched at all"
    drawn = " ".join(str(d) for d in frame(namespace))
    assert "WEP networks not supported" in drawn, drawn


def test_a_wep_network_is_still_listed_and_still_shareable():
    # Refusing to join is not refusing to carry: the credential is worth
    # handing to a phone that can use it.
    namespace, app = app_with(WEP_NET)
    wep_capable(False)
    assert app.names == ["OldRouter"]
    assert "WEP" in " ".join(app.labels), app.labels
    frame(namespace, "A")                 # Share
    frame(namespace, "A")                 # the WEP network
    assert app.screen == "align"
    frame(namespace, "A")                 # past the alignment line
    assert app.screen == "transfer"
    assert app.transfer.payload == [b"OldRouter", b"abcde",
                                    bytes([namespace["SEC_WEP"]])]


def test_a_received_wep_credential_is_saved():
    namespace, app = app_with()
    wep_capable(False)
    transfer = namespace["Transfer"](False)
    transfer.result = namespace["OK"]
    transfer.received = protocol.build_tokens(
        b"OldRouter", b"abcde", bytes([namespace["SEC_WEP"]]))
    app.finish_transfer(transfer)
    assert namespace["State"].data["OldRouter"] == {"key": "abcde",
                                                    "security": 1}


def test_the_block_lifts_if_the_firmware_gains_wep():
    # The point of probing rather than hardcoding. This is the only test that
    # asserts on a capability the badge does not currently have.
    namespace, app = app_with(WEP_NET)
    try:
        wep_capable(True)
        assert namespace["wep_supported"]() is True
        frame(namespace, "DOWN")
        frame(namespace, "DOWN")
        frame(namespace, "A")             # Connect
        frame(namespace, "A")             # the WEP network
        assert app.screen == "connecting", \
            "a WEP-capable firmware should not be blocked"
        assert app.error != namespace["WEP_UNSUPPORTED"]
    finally:
        wep_capable(False)


def test_the_probe_says_no_on_this_firmware():
    namespace, app = app_with()
    wep_capable(False)
    assert namespace["wep_supported"]() is False


def test_other_security_types_are_not_blocked():
    namespace, app = app_with({"HomeNet": {"key": "hunter2!!", "security": 3},
                               "Cafe": {"key": "", "security": 0}})
    wep_capable(False)
    for index in (0, 1):
        app.start_connect(app.names[index])
        assert app.screen == "connecting", app.names[index]
        frame(namespace)
    assert len(namespace["wifi"].calls) == 2


def test_a_refusal_does_not_follow_you_to_the_next_network():
    # The refusal sets an error without going near the radio, so the next
    # attempt has to start clean or it reports the wrong network's failure.
    namespace, app = app_with({"OldRouter": {"key": "abcde", "security": 1},
                               "HomeNet": {"key": "hunter2!!", "security": 3}})
    wep_capable(False)
    app.start_connect("OldRouter")
    assert app.error == namespace["WEP_UNSUPPORTED"]
    app.start_connect("HomeNet")
    assert app.screen == "connecting"
    assert app.error is None, "stale error carried over: %r" % (app.error,)


# --- a firmware that raises from status() ------------------------------------
# Seen on a badge: `NameError: name 'wifi' isn't defined`, raised from inside
# wifi.py's own status(). Neither tufty2350's main branch nor the simulator
# build has that fault, so it is a difference between firmware builds and
# nothing this app can fix -- but it took the app down on the connecting
# screen, and that part is the app's to answer for.


def test_a_firmware_that_raises_from_status_does_not_take_the_app_down():
    namespace, app = app_with(CREDENTIALS)
    namespace["wifi"].status_raises = True
    app.start_connect("HomeNet")
    for _ in range(5):
        drawn = frame(namespace)
    assert app.screen == "connecting", "the app left the connecting screen"
    assert any("unavailable" in str(d) for d in drawn), \
        "it should say the status could not be read: %r" % (drawn,)


def test_an_unreadable_status_is_treated_as_still_connecting():
    # Not as a failure. is_connected() decides success and the timeout decides
    # failure; turning an unreadable status into an error would abandon an
    # attempt that may be about to succeed.
    namespace, app = app_with(CREDENTIALS)
    namespace["wifi"].status_raises = True
    app.start_connect("HomeNet")
    frame(namespace)
    assert app.screen == "connecting"
    assert app.error is None

    namespace["wifi"].connected = True
    frame(namespace)
    assert app.screen == "connect_result"
    assert app.error is None, "a connection that worked must not report an error"


def test_an_unreadable_status_still_times_out_and_says_why():
    namespace, app = app_with(CREDENTIALS)
    namespace["wifi"].status_raises = True
    app.start_connect("HomeNet")
    app.connect_started -= namespace["CONNECT_TIMEOUT_MS"] + 1
    frame(namespace)
    assert app.screen == "connect_result"
    assert "Timed out" in app.message
    assert "unavailable" in app.message, \
        "a timeout with no readable status should name the real obstacle: %r" % app.message




# --- paged lists -------------------------------------------------------------

MANY = {"Net%02d" % i: "password%02d" % i for i in range(30)}


def open_connect(namespace):
    frame(namespace, "DOWN")
    frame(namespace, "DOWN")
    frame(namespace, "A")                 # Connect
    assert namespace["app"].screen == "connect_list"


def shown_networks(drawn):
    return [d.strip(" >") for d in drawn if str(d).lstrip(" >").startswith("Net")]


def test_rows_per_page_come_from_the_screen():
    namespace, app = app_with()
    assert namespace["menu_rows"]() == 12, "320x240 holds twelve rows"
    namespace["screen"].height = 120
    assert namespace["menu_rows"]() == 4, "computed, not hardcoded"


def test_a_long_list_shows_one_page_and_its_number():
    namespace, app = app_with(MANY)
    open_connect(namespace)
    drawn = frame(namespace)
    assert shown_networks(drawn) == ["Net%02d" % i for i in range(12)]
    assert "1/3" in drawn
    # On the title line, at its right.
    screen = namespace["screen"]
    box = [b for b in screen.boxes if b[0] == "1/3"][0]
    assert box[2] == namespace["MARGIN"]
    assert box[1] + box[3] == screen.width - namespace["MARGIN"]


def test_moving_past_a_page_edge_shows_the_next_page():
    namespace, app = app_with(MANY)
    open_connect(namespace)
    for _ in range(11):
        frame(namespace, "DOWN")
    drawn = frame(namespace)
    assert app.cursor == 11 and "1/3" in drawn
    drawn = frame(namespace, "DOWN")
    assert app.cursor == 12, "Down still moves one row"
    assert "2/3" in drawn
    assert shown_networks(drawn) == ["Net%02d" % i for i in range(12, 24)]
    assert any(str(d).startswith("> Net12") for d in drawn), \
        "selection lands on the new page's first row"


def test_moving_above_a_page_shows_the_previous_page_on_its_last_row():
    namespace, app = app_with(MANY)
    open_connect(namespace)
    for _ in range(12):
        frame(namespace, "DOWN")
    drawn = frame(namespace, "UP")
    assert app.cursor == 11
    assert "1/3" in drawn
    assert any(str(d).startswith("> Net11") for d in drawn)


def test_wrapping_goes_to_the_last_and_first_pages():
    namespace, app = app_with(MANY)
    open_connect(namespace)
    drawn = frame(namespace, "UP")
    assert app.cursor == 29
    assert "3/3" in drawn
    assert shown_networks(drawn) == ["Net%02d" % i for i in range(24, 30)]
    drawn = frame(namespace, "DOWN")
    assert app.cursor == 0
    assert "1/3" in drawn


def test_the_share_list_is_paged_too():
    namespace, app = app_with(MANY)
    frame(namespace, "A")                 # Share
    drawn = frame(namespace, "UP")
    assert "3/3" in drawn
    frame(namespace, "A")
    frame(namespace, "A")                 # past the alignment line
    assert app.transfer.payload[0] == b"Net29", "A acts on the selected row"


def test_a_list_that_fits_shows_no_page_number():
    namespace, app = app_with(CREDENTIALS)
    open_connect(namespace)
    drawn = frame(namespace)
    assert not any("/" in str(d) and str(d)[0].isdigit() for d in drawn)


def test_exactly_one_page_shows_no_page_number():
    namespace, app = app_with({"Net%02d" % i: "password1" for i in range(12)})
    open_connect(namespace)
    drawn = frame(namespace, "UP")
    assert app.cursor == 11
    assert "1/1" not in drawn and "1/2" not in drawn
    assert len(shown_networks(drawn)) == 12


# --- dev mode ----------------------------------------------------------------

def main_text(namespace, *buttons):
    return " ".join(str(d) for d in frame(namespace, *buttons))


def test_dev_mode_is_hidden_at_launch():
    namespace, app = app_with()
    assert "Dev Mode" not in main_text(namespace)


def test_c_reveals_dev_mode_and_selects_it():
    namespace, app = app_with()
    text = main_text(namespace, "C")
    assert "> Dev Mode" in text
    assert app.cursor == len(namespace["MENU_ITEMS"])


def test_c_again_does_nothing():
    namespace, app = app_with()
    frame(namespace, "C")
    frame(namespace, "UP")
    before = app.cursor
    drawn = frame(namespace, "C")
    assert app.cursor == before
    assert [d for d in drawn if "Dev Mode" in str(d)] == ["  Dev Mode"]


def test_dev_mode_stays_after_leaving_the_main_screen():
    namespace, app = app_with(CREDENTIALS)
    frame(namespace, "C")
    frame(namespace, "DOWN")              # wraps to Share
    frame(namespace, "A")
    assert app.screen == "pick_share"
    frame(namespace, "B")
    assert "Dev Mode" in main_text(namespace)


def test_dev_mode_is_not_remembered_across_launches():
    namespace, app = app_with()
    frame(namespace, "C")
    namespace, app = app_with()
    assert "Dev Mode" not in main_text(namespace)


def test_c_does_nothing_off_the_main_screen():
    namespace, app = app_with(CREDENTIALS)
    open_connect(namespace)
    frame(namespace, "C")
    assert app.screen == "connect_list"
    frame(namespace, "B")
    assert "Dev Mode" not in main_text(namespace)


def open_dev(namespace):
    frame(namespace, "C")
    frame(namespace, "A")
    assert namespace["app"].screen == namespace["DEV"]


def test_dev_mode_menu_lists_its_tools():
    namespace, app = app_with()
    open_dev(namespace)
    drawn = frame(namespace)
    assert drawn[0] == "Dev Mode"
    text = " ".join(str(d) for d in drawn)
    for item in namespace["DEV_ITEMS"]:
        assert item in text
    assert "A - Select   B - Back" in drawn


def test_dev_mode_menu_moves_and_wraps():
    namespace, app = app_with()
    open_dev(namespace)
    frame(namespace, "DOWN")
    assert app.cursor == 1
    frame(namespace, "UP")
    frame(namespace, "UP")
    assert app.cursor == len(namespace["DEV_ITEMS"]) - 1


def test_b_leaves_dev_mode_and_c_does_nothing_there():
    namespace, app = app_with()
    open_dev(namespace)
    frame(namespace, "C")
    assert app.screen == namespace["DEV"]
    frame(namespace, "B")
    assert app.screen == "main"


def test_each_dev_tool_opens_its_first_screen():
    expected = (("DEV_PROMPT", "Dark room"), ("DEV_ROLE", "Who goes first?"),
                ("DEV_PROBE_PROMPT", "Probe frame timing"),
                ("DEV_FLICKER", "Room flicker"),
                ("DEV_DISPLAY", "Measurements"),
                ("DEV_SAVED", "Done"))
    for index, (target, title) in enumerate(expected):
        namespace, app = app_with()
        open_dev(namespace)
        for _ in range(index):
            frame(namespace, "DOWN")
        frame(namespace, "A")
        assert app.screen == namespace[target]
        drawn = frame(namespace)
        assert drawn[0] == title, drawn


# --- dev mode: the measurement screens ---------------------------------------

measure = load_module("share_wifi", "measure")


def dev_app():
    # With the alignment constants, so messages and the centre column wrap
    # rather than being cut to one line and the full strings can be asserted.
    namespace = load_app("share_wifi", alignment=True)
    return namespace, namespace["app"]


class FastClock:
    """time.ticks_ms/ticks_us under the test's control.

    Every ticks_us() call moves it 20us on, standing in for a light_level()
    read, so a settle capture's read-until-the-window-ends loop terminates.
    A frame moves it on by `frame_ms`. Patched into `time` itself, which the
    app and measure.py share.
    """

    def __init__(self):
        self.us = 10000000

    def ticks_us(self):
        self.us += 20
        return self.us

    def ticks_ms(self):
        return self.us // 1000

    def __enter__(self):
        self.saved = (time.ticks_ms, time.ticks_us)
        time.ticks_ms = self.ticks_ms
        time.ticks_us = self.ticks_us
        return self

    def __exit__(self, *exc):
        time.ticks_ms, time.ticks_us = self.saved
        return False


def brightness(pixels, backlight):
    if pixels == "white":
        level = 1.0
    elif pixels == "black":
        level = 0.05
    else:
        level = (sum(pixels) / 765.0) ** 2.2
    return level * backlight


def panel_light(view, ambient=32, span=160):
    """A sensor reading whatever `view()` says is on the screen it faces.

    Quantised to 16 counts, like the badge's. `view` returns the run whose
    field is in front of this sensor, or None for a screen showing no run.
    """
    def light():
        run = view()
        level = 0.0 if run is None else brightness(run.pixels, run.backlight)
        return (ambient + int(level * span)) // 16 * 16
    return light


def mirror_badge(namespace):
    app = namespace["app"]
    namespace["badge"].light_level = panel_light(lambda: app.run)


def drive(namespace, clock, screen_name, frame_ms=50, limit=4000,
          seen=None):
    """Frames until the app leaves `screen_name`; every string drawn, if asked."""
    app = namespace["app"]
    namespace["badge"].ticks_delta = frame_ms
    for _ in range(limit):
        if app.screen != screen_name:
            return
        clock.us += frame_ms * 1000
        drawn = frame(namespace)
        if seen is not None:
            seen.update(str(d).replace("\n", " ") for d in drawn)
    raise AssertionError("still on %s after %d frames" % (screen_name, limit))


def open_tool(namespace, index):
    open_dev(namespace)
    for _ in range(index):
        frame(namespace, "DOWN")
    frame(namespace, "A")


def test_the_mirror_tool_starts_with_the_dark_room_prompt():
    namespace, app = dev_app()
    open_tool(namespace, 0)
    drawn = frame(namespace)
    assert drawn[0] == "Dark room"
    assert ("Turn the lights off. Hold the badge screen-first against a "
            "mirror.") in drawn
    assert "A - Start   B - Cancel" in drawn


def test_b_at_the_prompt_cancels_and_saves_nothing():
    namespace, app = dev_app()
    open_tool(namespace, 0)
    writes = namespace["State"].writes
    frame(namespace, "B")
    assert app.screen == namespace["DEV"]
    assert app.cursor == 0
    assert namespace["State"].writes == writes
    assert "measurements" not in namespace["State"].files


def test_b_mid_test_cancels_restores_the_panel_and_saves_nothing():
    namespace, app = dev_app()
    mirror_badge(namespace)
    with FastClock() as clock:
        open_tool(namespace, 0)
        frame(namespace, "A")
        assert app.screen == namespace["DEV_MEASURE"]
        writes = namespace["State"].writes
        for _ in range(200):            # past the delay and into the tests
            clock.us += 50000
            frame(namespace)
        assert app.run.test_name is not None
        frame(namespace, "B")
    assert app.screen == namespace["DEV"] and app.cursor == 0
    assert app.run is None
    assert namespace["State"].writes == writes
    assert namespace["display"].backlights[-1] == namespace["BACKLIGHT_NORMAL"]
    assert namespace["badge"].lights == 0.0


def test_the_run_waits_three_seconds_after_a():
    namespace, app = dev_app()
    mirror_badge(namespace)
    with FastClock() as clock:
        open_tool(namespace, 0)
        frame(namespace, "A")
        clock.us += 2900000
        drawn = frame(namespace)
        assert not app.run_begun
        assert "Starting" in drawn and "B - Cancel" in drawn
        clock.us += 200000
        frame(namespace)
        assert app.run_begun


def complete_mirror_run(namespace, seen=None):
    app = namespace["app"]
    with FastClock() as clock:
        open_tool(namespace, 0)
        frame(namespace, "A")
        drive(namespace, clock, namespace["DEV_MEASURE"], seen=seen)
        assert app.screen == namespace["DEV_PROMPT"]
        drawn = frame(namespace)
        assert drawn[0] == "Bright room"
        assert ("Turn the lights on. Hold the badge against the mirror "
                "again.") in drawn
        frame(namespace, "A")
        drive(namespace, clock, namespace["DEV_MEASURE"], seen=seen)


def test_a_whole_mirror_run_saves_both_conditions_and_says_done():
    namespace, app = dev_app()
    mirror_badge(namespace)
    state = namespace["State"]
    writes = state.writes
    seen = set()
    complete_mirror_run(namespace, seen)
    assert app.screen == namespace["DEV_SAVED"]
    assert state.writes == writes + 1, "written once, at the end"
    saved = state.files["measurements"]["mirror"]
    assert sorted(saved) == ["dark", "lit"]
    for condition in ("dark", "lit"):
        assert sorted(saved[condition]) == sorted(
            list(measure.MIRROR_TESTS) + ["temperature_c"])
    dark = saved["dark"]
    assert dark["adc_step"] == 16
    assert dark["levels"]["fields"]["span"] > 0
    # No text is drawn while a test runs -- the whole screen is its field --
    # and a mirror run is tests from start to end, so no progress line shows.
    assert not any(":" in line and "/11" in line for line in seen), \
        sorted(line for line in seen if "/11" in line)
    assert namespace["display"].backlights[-1] == namespace["BACKLIGHT_NORMAL"]
    assert namespace["badge"].lights == 0.0

    drawn = frame(namespace)
    assert drawn[0] == "Done"
    assert "Measurements saved" in drawn
    assert "Any button - Back" in drawn
    frame(namespace, "UP")
    assert app.screen == namespace["DEV"] and app.cursor == 0


def test_a_write_that_fails_says_the_measurements_were_not_saved():
    # The firmware's State.save() swallows OSError whenever /state exists, so
    # the app writes the file itself to see one. A failed write must not read
    # as a saved one: a run is minutes of holding a badge still.
    namespace, app = dev_app()
    mirror_badge(namespace)
    state = namespace["State"]
    state.fail_writes = True
    complete_mirror_run(namespace)
    assert app.screen == namespace["DEV_SAVED"]
    assert "mirror" not in state.files.get("measurements", {})
    assert namespace["display"].backlights[-1] == namespace["BACKLIGHT_NORMAL"]
    drawn = frame(namespace)
    assert drawn[0] == "Done"
    assert "Could not save measurements" in drawn
    assert "Measurements saved" not in drawn
    frame(namespace, "B")
    assert app.screen == namespace["DEV"]


def test_the_app_writes_measurements_itself_rather_than_through_state():
    # State.save() would hide the error this exists to report.
    source = open(namespace_source()).read()
    body = source.split("def save_measurements", 1)[1].split("\n    def ", 1)[0]
    assert "write_measurements(" in body
    assert "State.modify" not in body and "State.save" not in body


def namespace_source():
    import os
    from harness import APPS
    return os.path.join(APPS, "share_wifi", "__init__.py")


def test_a_test_fills_the_whole_screen_with_its_field_and_no_text():
    namespace, app = dev_app()
    mirror_badge(namespace)
    screen = namespace["screen"]
    with FastClock() as clock:
        open_tool(namespace, 0)
        frame(namespace, "A")
        # The start delay: the status column, on black.
        clock.us += 1000000
        drawn = frame(namespace)
        assert "B - Cancel" in drawn and "Starting" in drawn
        clock.us += 2100000
        frame(namespace)
        assert app.run.test_name is not None
        badge = namespace["badge"]
        assert badge.current_mode == namespace["LORES"] | namespace["VSYNC"]
        assert badge.default_clear is None
        seen = set()
        # The frame above drew the first test's field.
        shown = namespace["pixels_pen"](app.run.pixels)
        for _ in range(200):            # through several tests
            clock.us += 50000
            screen.rects = []
            screen.fills = []
            drawn = frame(namespace)
            if app.screen != namespace["DEV_MEASURE"]:
                break
            pen = namespace["pixels_pen"](app.run.pixels)
            # Filled only when the pixels change: the framebuffer keeps them,
            # and a backlight-only change is a display.backlight() call.
            assert screen.fills == ([pen] if pen is not shown else []), \
                (screen.fills, pen, shown)
            shown = pen
            assert screen.rects == [], "bands drawn during a test"
            assert not any(isinstance(d, str) for d in drawn), \
                "text drawn over a test: %r" % (list(drawn),)
            assert namespace["display"].backlights[-1] == app.run.backlight
            seen.add(app.run.test_name)
        assert len(seen) >= 3, seen
        frame(namespace, "B")           # B still cancels mid-test
    assert app.screen == namespace["DEV"]
    assert badge.current_mode == namespace["HIRES"] | namespace["VSYNC"]
    assert badge.default_clear == namespace["color"].black


def test_the_handshake_keeps_the_bands_and_the_status_column():
    # Only the tests go full screen: while the badges find each other the
    # user can still read what is happening.
    namespace, app = dev_app()
    namespace["badge"].light_level = lambda: 32
    screen = namespace["screen"]
    with FastClock() as clock:
        open_tool(namespace, 1)
        frame(namespace, "A")           # I go first
        frame(namespace, "A")           # start
        for _ in range(100):            # delay, noise second, calling
            clock.us += 50000
            screen.rects = []
            drawn = frame(namespace)
            assert "B - Cancel" in drawn
            if app.run_begun:
                assert app.run.test_name is None
                assert len(screen.rects) == 2, "the bands, not the whole screen"
        assert app.run.phase == measure.PHASE_CALLING


def test_a_frame_with_a_capture_due_is_presented_before_it_is_read():
    # Under run(update) the firmware presents after update() returns, then
    # polls and clears -- ~30 ms on hardware before a capture could start, so
    # every rise had crossed by the first read. A frame with a settle capture
    # due is presented by the app itself, and read the moment that returns.
    namespace, app = dev_app()
    mirror_badge(namespace)
    display = namespace["display"]
    calls = []
    early = due_frames = 0
    with FastClock() as clock:
        open_tool(namespace, 0)
        frame(namespace, "A")
        clock.us += 3100000
        frame(namespace)
        run = app.run
        real_presented = run.presented

        def presented(present_us, returned_us=None):
            calls.append((display.updates, present_us, returned_us))
            return real_presented(present_us, returned_us)
        run.presented = presented
        while app.screen == namespace["DEV_MEASURE"] and len(calls) < 5:
            before = display.updates
            clock.us += 50000
            frame(namespace)
            if display.updates != before:
                early += 1
    assert len(calls) == 5 and early == 5, (len(calls), early)
    for updates, present_us, returned_us in calls:
        assert updates >= 1, "read before the frame was presented"
        assert returned_us is not None and returned_us >= present_us
    # Frames without a capture are left to run() to present.
    assert early == len(calls)


def test_the_temperature_reader_probes_for_core_temp():
    import sys
    import types
    namespace, app = dev_app()
    reader = namespace["core_temp_reader"]
    saved = sys.modules.get("machine")
    try:
        # No machine module at all, as under CPython: no sensor.
        sys.modules.pop("machine", None)
        assert reader() is None
        # A firmware whose ADC has no CORE_TEMP: no sensor, no guessed channel.
        fake = types.ModuleType("machine")

        class NoTemp:
            def __init__(self, channel):
                raise AssertionError("constructed an ADC on channel %r" % (channel,))
        fake.ADC = NoTemp
        sys.modules["machine"] = fake
        assert reader() is None
        # CORE_TEMP: the reads are averaged and converted.
        made = []

        class ADC:
            CORE_TEMP = 8

            def __init__(self, channel):
                made.append(channel)
                self.n = 0

            def read_u16(self):
                self.n += 1
                return 14020 if self.n % 2 else 14022
        fake.ADC = ADC
        read = reader()
        assert made == [8]
        assert read() == namespace["core_temp_c"](14021)
    finally:
        if saved is None:
            sys.modules.pop("machine", None)
        else:
            sys.modules["machine"] = saved


def test_a_backlight_step_is_set_inside_its_capture_with_no_present_first():
    # display.backlight() is live the moment it is called. If the app set it
    # with the rest of the field, or presented first, the edge would be over
    # before the capture's first read.
    namespace, app = dev_app()
    mirror_badge(namespace)
    display = namespace["display"]
    seen = []
    with FastClock() as clock:
        open_tool(namespace, 0)
        frame(namespace, "A")
        clock.us += 3100000
        frame(namespace)
        run = app.run
        real_changed = run.changed
        at_frame = {}

        def changed(change):
            entered = (display.updates, len(display.backlights))

            def wrapped():
                seen.append((at_frame["start"], entered,
                             (display.updates, len(display.backlights))))
                change()
                seen.append(("set", display.backlights[-1], run.backlight))
            return real_changed(wrapped)
        run.changed = changed
        while app.screen == namespace["DEV_MEASURE"] and len(seen) < 6:
            at_frame["start"] = (display.updates, len(display.backlights))
            clock.us += 50000
            frame(namespace)
    assert len(seen) == 6, seen
    for before, after in zip(seen[0::2], seen[1::2]):
        start, entered, inside = before
        # Nothing presented and no backlight set between the frame starting
        # and the change, which the capture makes itself.
        assert start == entered == inside, before
        assert after[1] == after[2], after


def test_a_run_with_failed_tests_still_saves_them():
    # A sensor that reads nothing: most tests fail, and the run is still saved.
    namespace, app = dev_app()
    namespace["badge"].light_level = lambda: 0
    complete_mirror_run(namespace)
    assert app.screen == namespace["DEV_SAVED"]
    dark = namespace["State"].files["measurements"]["mirror"]["dark"]
    assert "error" in dark["adc_step"]
    assert "error" in dark["levels"]["fields"]
    assert "frames" in dark["frame_timing"]


def test_b_at_the_bright_prompt_drops_the_dark_results_too():
    namespace, app = dev_app()
    mirror_badge(namespace)
    with FastClock() as clock:
        open_tool(namespace, 0)
        frame(namespace, "A")
        drive(namespace, clock, namespace["DEV_MEASURE"])
    assert app.screen == namespace["DEV_PROMPT"] and app.condition == "lit"
    writes = namespace["State"].writes
    frame(namespace, "B")
    assert app.screen == namespace["DEV"]
    assert namespace["State"].writes == writes
    assert app.dark_result is None
    assert namespace["display"].backlights[-1] == namespace["BACKLIGHT_NORMAL"]


def test_a_mirror_run_keeps_the_second_badge_results():
    namespace, app = dev_app()
    mirror_badge(namespace)
    other = {"role": "first", "dark": {"ambient": 16}}
    namespace["State"].files["measurements"] = {"second_badge": other}
    complete_mirror_run(namespace)
    saved = namespace["State"].files["measurements"]
    assert saved["second_badge"] == other
    assert "mirror" in saved


def test_the_role_screen_asks_who_goes_first_and_has_no_back():
    namespace, app = dev_app()
    open_tool(namespace, 1)
    drawn = frame(namespace)
    assert drawn[0] == "Who goes first?"
    assert "A - I go first   B - I go second" in drawn
    frame(namespace, "C")
    assert app.screen == namespace["DEV_ROLE"]
    frame(namespace, "A")
    assert app.screen == namespace["DEV_PROMPT"] and app.measure_first is True

    namespace, app = dev_app()
    open_tool(namespace, 1)
    frame(namespace, "B")
    assert app.screen == namespace["DEV_PROMPT"] and app.measure_first is False
    drawn = frame(namespace)
    assert drawn[0] == "Dark room"
    # The alignment screen, titled with the room.
    assert namespace["ALIGN_TEXT"] in drawn
    assert not any("Turn the lights" in str(d) for d in drawn)
    frame(namespace, "B")               # the way out is B at the prompt
    assert app.screen == namespace["DEV"] and app.cursor == 1


def test_the_first_badge_holds_still_then_calls_until_it_gives_up():
    namespace, app = dev_app()
    namespace["badge"].light_level = lambda: 32
    with FastClock() as clock:
        open_tool(namespace, 1)
        frame(namespace, "A")           # I go first
        frame(namespace, "A")           # start
        clock.us += 3100000
        drawn = frame(namespace)
        # The run opens on its noise second, then calls.
        assert "Hold still" in drawn and "Dark room" in drawn
        assert isinstance(app.run, measure.SecondBadgeRun)
        assert app.run.first is True
        clock.us += measure.NOISE_MS * 1000
        frame(namespace)
        drawn = frame(namespace)
        assert "Calling the other badge" in drawn and "Dark room" in drawn
        assert "No answer from the other badge" not in drawn
        # Nobody answers: a minute on, it says so, and holds that up.
        clock.us += measure.HANDSHAKE_TIMEOUT_MS * 1000
        frame(namespace)
        drawn = frame(namespace)
        assert "No answer from the other badge" in drawn
        assert app.screen == namespace["DEV_MEASURE"]
        clock.us += measure.NO_ANSWER_HOLD_MS * 1000
        frame(namespace)
        assert app.screen == namespace["DEV_PROMPT"]
        assert app.dark_result["levels"] == {"error": "no handshake"}
        frame(namespace, "B")
    assert app.screen == namespace["DEV"] and app.cursor == 1
    assert namespace["display"].backlights[-1] == namespace["BACKLIGHT_NORMAL"]


def test_the_second_badge_says_it_is_answering():
    namespace, app = dev_app()
    namespace["badge"].light_level = lambda: 32
    with FastClock() as clock:
        open_tool(namespace, 1)
        frame(namespace, "B")           # I go second
        frame(namespace, "A")           # start
        clock.us += 3100000
        frame(namespace)
        clock.us += measure.NOISE_MS * 1000
        frame(namespace)
        drawn = frame(namespace)
        assert "Answering the other badge" in drawn and "Dark room" in drawn


def test_the_noise_is_measured_after_the_delay_not_during_it():
    # The hand that pressed A may still be moving the badge during the delay,
    # so the handshake's threshold comes from the run's first second.
    namespace, app = dev_app()
    clock_ref = []

    def light():
        now = clock_ref[0].us // 1000
        # Wild during the delay, a 32-count spread once the run is going.
        if not app.run_begun:
            return 32 + (400 if (now // 50) % 2 else 0)
        return 32 + 16 * ((now // 50) % 3)     # 32 counts peak to peak
    namespace["badge"].light_level = light
    with FastClock() as clock:
        clock_ref.append(clock)
        open_tool(namespace, 1)
        frame(namespace, "B")           # I go second
        frame(namespace, "A")           # start
        for _ in range(64):             # past the 3 s delay
            clock.us += 50000
            frame(namespace)
        assert app.run_begun and app.run.phase == measure.PHASE_NOISE
        drawn = frame(namespace)
        assert "Hold still" in drawn
        for _ in range(25):             # through the noise second
            clock.us += 50000
            frame(namespace)
    assert app.run.phase == measure.PHASE_ANSWERING
    assert app.run.pulse_noise == 32
    assert app.run.pulse_threshold == 48    # 1.5 x 32: the floor exactly


def test_two_badges_measure_each_other_and_each_saves_its_own_role():
    # Both apps on one clock, each sensor reading the other's run. Coarse
    # frames, so the fast symbol rungs may count errors; the point here is
    # the screens and the saving, which measure.py's own tests do not cover.
    first, app_a = dev_app()
    second, app_b = dev_app()
    # A run's field is only on screen once it has begun: through the start
    # delay the screen is black, which is what the noise is measured against.
    first["badge"].light_level = panel_light(
        lambda: app_b.run if app_b.run_begun else None)
    second["badge"].light_level = panel_light(
        lambda: app_a.run if app_a.run_begun else None)
    mirror = {"dark": {"ambient": 16}}
    second["State"].files["measurements"] = {"mirror": mirror}
    with FastClock() as clock:
        open_tool(first, 1)
        frame(first, "A")
        open_tool(second, 1)
        frame(second, "B")
        frame_ms = 40
        first["badge"].ticks_delta = second["badge"].ticks_delta = frame_ms
        seen = {"first": set(), "second": set()}
        for _ in range(20000):
            if (app_a.screen == first["DEV_SAVED"]
                    and app_b.screen == second["DEV_SAVED"]):
                break
            clock.us += frame_ms * 1000
            for namespace, app, role in ((first, app_a, "first"),
                                         (second, app_b, "second")):
                drawn = frame(namespace, "A") \
                    if app.screen == namespace["DEV_PROMPT"] else frame(namespace)
                seen[role].update(str(d).replace("\n", " ") for d in drawn)
        else:
            raise AssertionError("the two badges never finished")
    # Each badge has called, answered, sent and measured, and said so.
    for role in ("first", "second"):
        for text in ("Hold still", "Calling the other badge",
                     "Answering the other badge", "Sending",
                     "Measuring the other badge"):
            assert text in seen[role], (role, text)
        assert "No answer from the other badge" not in seen[role], role
    for namespace, role in ((first, "first"), (second, "second")):
        saved = namespace["State"].files["measurements"]["second_badge"]
        assert saved["role"] == role
        for condition in ("dark", "lit"):
            assert sorted(saved[condition]) == sorted(
                list(measure.SECOND_BADGE_TESTS) + ["temperature_c"])
            assert saved[condition]["ambient"] != {"error": "no handshake"}, \
                "%s badge never shook hands with the other" % role
    assert second["State"].files["measurements"]["mirror"] == mirror


# --- dev mode: display measurements -------------------------------------------

def open_display(namespace):
    open_tool(namespace, namespace["DISPLAY_INDEX"])
    assert namespace["app"].screen == namespace["DEV_DISPLAY"]


# --- dev mode: measure room flicker -------------------------------------------

FLICKER_INDEX = 3


def flicker_tool():
    namespace, app = dev_app()
    open_tool(namespace, FLICKER_INDEX)
    assert app.screen == namespace["DEV_FLICKER"]
    return namespace, app


def test_the_flicker_tool_says_what_to_do_and_b_with_nothing_leaves():
    namespace, app = flicker_tool()
    drawn = frame(namespace)
    assert drawn[0] == "Room flicker"
    assert "A - Measure   B - Save & exit" in drawn
    frame(namespace, "B")
    assert app.screen == namespace["DEV"] and app.cursor == FLICKER_INDEX
    assert "measurements" not in namespace["State"].files, "saved nothing"


def flickering(namespace, clock, hz=100, size=20):
    # Flicker of `size` counts on 400, as a lit room's lights might.
    namespace["badge"].light_level = lambda: int(
        400 + size * math.sin(2 * math.pi * hz * clock.us / 1e6))


def drift_frames(namespace, app, limit=20):
    """Frames until the drift windows end, each checked: the case lights held
    off, run()'s clear off, nothing drawn. How many it took."""
    badge = namespace["badge"]
    count = 0
    while app.screen == namespace["DEV_FLICKER_DRIFT"]:
        assert badge.default_clear is None
        badge.lights = 1.0
        drawn = frame(namespace)
        assert badge.lights == 0.0, "case lights on during the drift"
        assert not drawn, drawn
        count += 1
        assert count <= limit
    return count


def test_each_a_takes_a_measurement_with_the_backlight_off():
    namespace, app = flicker_tool()
    namespace["DRIFT_WINDOWS"] = 3
    display = namespace["display"]
    badge = namespace["badge"]
    with FastClock() as clock:
        flickering(namespace, clock)
        for n in (1, 2, 3):
            before = len(display.backlights)
            drawn = frame(namespace, "A")
            assert namespace["FLICKER_MEASURING"] in " ".join(drawn).replace("\n", " "), drawn
            # Off for the capture and the drift windows, back to normal after.
            assert app.screen == namespace["DEV_FLICKER_DRIFT"]
            assert display.backlights[before:] == [0.0]
            assert drift_frames(namespace, app) == 3
            assert display.backlights[before:] == \
                [0.0, namespace["BACKLIGHT_NORMAL"]], display.backlights[before:]
            assert badge.default_clear is not None, "run()'s clear back on"
            assert len(app.flicker_results) == n
            drawn = frame(namespace)
            assert any(("%d measured" % n) in line for line in drawn), drawn
    result = app.flicker_results[-1]
    assert result["peak_hz"] == 100, result
    assert abs(result["peak_counts"] - 20) < 2, result
    assert result["hz"]["100"] > 10 * result["hz"]["60"], result["hz"]
    assert result["filter"]["verdict"] == "flicker", result["filter"]
    assert result["drift"]["hz"] == 100 and result["drift"]["windows"] == 3, \
        result["drift"]


def test_b_during_the_drift_stops_it_and_keeps_the_measurement():
    namespace, app = flicker_tool()
    with FastClock() as clock:
        flickering(namespace, clock)
        frame(namespace, "A")
        frame(namespace)
        frame(namespace, "B")               # held as the frame ends
    assert app.screen == namespace["DEV_FLICKER"]
    assert app.flicker_results[-1]["drift"] == {"error": "cancelled"}
    assert namespace["display"].backlights[-1] == namespace["BACKLIGHT_NORMAL"]
    assert namespace["badge"].default_clear is not None
    frame(namespace, "B")                   # and B again saves it
    saved = namespace["State"].files["measurements"]["room_flicker"]
    assert saved["1"]["drift"] == {"error": "cancelled"}


def test_a_drift_that_reads_nothing_still_ends():
    namespace, app = flicker_tool()
    namespace["DRIFT_WINDOWS"] = 3
    with FastClock() as clock:
        flickering(namespace, clock)
        frame(namespace, "A")
        assert app.screen == namespace["DEV_FLICKER_DRIFT"]
        # A capture that never reads -- the window passes with no reading.
        app.flicker.take = lambda light, ticks: None
        app.flicker.counts[:] = [0] * len(app.flicker.counts)
        assert drift_frames(namespace, app) == 3
    assert app.flicker_results[-1]["drift"] == {"error": "too few windows"}


def test_a_steady_room_has_no_drift_to_measure_and_takes_no_windows():
    namespace, app = flicker_tool()
    with FastClock():
        namespace["badge"].light_level = lambda: 400
        frame(namespace, "A")
    assert app.screen == namespace["DEV_FLICKER"]
    assert app.flicker_results[-1]["drift"] == {"error": "no mains flicker"}
    assert namespace["display"].backlights[-2:] == \
        [0.0, namespace["BACKLIGHT_NORMAL"]]


def test_twenty_measurements_are_saved_and_there_is_no_cap():
    namespace, app = flicker_tool()
    namespace["DRIFT_WINDOWS"] = 3
    with FastClock() as clock:
        flickering(namespace, clock, hz=120)
        for _ in range(21):
            frame(namespace, "A")
            drift_frames(namespace, app)
    frame(namespace, "B")
    saved = namespace["State"].files["measurements"]["room_flicker"]
    assert len(saved) == 21
    import json
    size = len(json.dumps(saved))
    # The spec's storage budget: about 550 bytes a measurement.
    assert size < 21 * 720, size


def test_b_saves_every_measurement_numbered_and_exits():
    namespace, app = flicker_tool()
    namespace["State"].files["measurements"] = MEASURED
    with FastClock():
        namespace["badge"].light_level = lambda: 400
        frame(namespace, "A")
        frame(namespace, "A")
    frame(namespace, "B")
    assert app.screen == namespace["DEV_SAVED"] and app.saved_ok
    saved = namespace["State"].files["measurements"]
    assert saved["mirror"] == MEASURED["mirror"], "the other tools' results kept"
    assert sorted(saved["room_flicker"]) == ["1", "2"]
    assert saved["room_flicker"]["1"]["mean"] == 400.0
    assert saved["room_flicker"]["1"]["peak_counts"] < 0.5   # steady light
    frame(namespace)
    frame(namespace, "A")                  # Done -> Dev Mode, on this tool
    assert app.screen == namespace["DEV"] and app.cursor == FLICKER_INDEX


# --- dev mode: clear saved measurements ---------------------------------------

def clear_saved(namespace):
    open_tool(namespace, namespace["CLEAR_INDEX"])
    assert namespace["app"].screen == namespace["DEV_SAVED"]
    return frame(namespace)


def test_clear_deletes_every_measurement_and_says_so():
    namespace, app = dev_app()
    namespace["State"].files["measurements"] = MEASURED
    drawn = clear_saved(namespace)
    assert drawn[0] == "Done"
    assert "Measurements cleared" in drawn
    assert "measurements" not in namespace["State"].files
    frame(namespace, "A")
    assert app.screen == namespace["DEV"] and \
        app.cursor == namespace["CLEAR_INDEX"]
    frame(namespace, "UP")                  # display measurements
    frame(namespace, "A")
    assert "No measurements saved" in frame(namespace)


def real_delete_measurements(fake_os, path):
    """The app's own delete_measurements(), which the harness replaces,
    compiled from the source against `fake_os` and `path`."""
    import os
    import ast
    from harness import APPS
    source = open(os.path.join(APPS, "share_wifi", "__init__.py")).read()
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "delete_measurements")
    namespace = {"os": fake_os, "MEASUREMENTS_PATH": path}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "app", "exec"),
         namespace)
    return namespace["delete_measurements"]


def test_the_real_delete_measurements_on_a_real_directory():
    import os
    import tempfile
    import types
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "measurements.json")
        delete = real_delete_measurements(os, path)
        with open(path, "w") as handle:
            handle.write("{}")
        assert delete() is True and not os.path.exists(path)
        assert delete() is True, "absent is cleared too"
        # remove raising and the file still there: not cleared.
        with open(path, "w") as handle:
            handle.write("{}")

        def refuse(_path):
            raise OSError(5, "EIO")
        stuck = types.SimpleNamespace(remove=refuse, stat=os.stat)
        assert real_delete_measurements(stuck, path)() is False
        assert os.path.exists(path)


def test_clear_with_nothing_saved_says_the_same():
    namespace, app = dev_app()
    drawn = clear_saved(namespace)
    assert "Measurements cleared" in drawn


def test_a_file_still_there_after_clearing_says_it_could_not():
    namespace, app = dev_app()
    namespace["State"].files["measurements"] = MEASURED
    namespace["State"].fail_deletes = True
    drawn = clear_saved(namespace)
    assert "Could not clear" in drawn
    assert namespace["State"].files["measurements"] == MEASURED


def test_done_after_clearing_does_not_stick_to_the_next_save():
    namespace, app = dev_app()
    clear_saved(namespace)
    frame(namespace, "A")
    frame(namespace, "UP")
    frame(namespace, "UP")                  # measure room flicker
    frame(namespace, "A")
    assert app.screen == namespace["DEV_FLICKER"]
    with FastClock():
        namespace["badge"].light_level = lambda: 400
        frame(namespace, "A")
    frame(namespace, "B")
    assert "Measurements saved" in frame(namespace)


def test_display_with_nothing_saved_says_so():
    namespace, app = dev_app()
    open_display(namespace)
    drawn = frame(namespace)
    assert drawn[0] == "Measurements"
    assert "No measurements saved" in drawn
    assert "Any button - Back" in drawn
    frame(namespace, "DOWN")
    assert app.screen == namespace["DEV"] and \
        app.cursor == namespace["DISPLAY_INDEX"]


MEASURED = {
    "mirror": {
        "dark": {
            "ambient": 32,
            "adc_step": 16,
            "levels": {"fields": {"black": 32, "mid": 64, "white": 112,
                                  "span": 80, "mid_share": 0.4}},
            "backlight_curve": dict(("%d.%d" % (t // 10, t % 10), 16 * t)
                                    for t in range(14)),
            "settle": {"error": "no rise transition fitted"},
        },
    },
}


def test_display_pages_through_the_file():
    for flag in (False, True):
        namespace = load_app("share_wifi", state_returns_flag=flag,
                             alignment=True)
        app = namespace["app"]
        namespace["State"].files["measurements"] = MEASURED
        open_display(namespace)
        expected = measure.pages(MEASURED, namespace["menu_rows"]())
        count = len(expected)
        assert count == 5     # dark; backlight_curve and its (cont.); ...
        drawn = frame(namespace)
        assert drawn[0] == "mirror.dark"
        assert "1/%d" % count in drawn
        assert "ambient: 32" in drawn and "adc_step: 16" in drawn
        assert "Up/Down - Page   A/B/C - Exit" in drawn
        drawn = frame(namespace, "DOWN")
        assert drawn[0] == expected[1][0]
        assert "2/%d" % count in drawn
        drawn = frame(namespace, "UP")
        drawn = frame(namespace, "UP")              # wraps to the last page
        assert "%d/%d" % (count, count) in drawn
        assert drawn[0] == "mirror.dark.settle"
        assert 'error: "no rise transition fitted"' in drawn
        drawn = frame(namespace, "DOWN")            # and back to the first
        assert "1/%d" % count in drawn
        titles = [p[0] for p in expected]
        assert "mirror.dark.backlight_curve (cont.)" in titles


def test_a_b_and_c_each_leave_the_display():
    for button in ("A", "B", "C"):
        namespace, app = dev_app()
        namespace["State"].files["measurements"] = MEASURED
        open_display(namespace)
        frame(namespace, "DOWN")
        frame(namespace, button)
        assert app.screen == namespace["DEV"] and \
            app.cursor == namespace["DISPLAY_INDEX"], button


def test_display_builds_its_pages_once():
    namespace, app = dev_app()
    namespace["State"].files["measurements"] = MEASURED
    open_display(namespace)
    built = app.pages
    frame(namespace)
    frame(namespace, "DOWN")
    assert app.pages is built


def test_a_long_title_keeps_its_end():
    namespace, app = dev_app()
    long_path = {"second_badge": {"dark": {"symbol_errors": {
        "backlight_and_some_more_words": {"50": {"symbols": 29,
                                                 "errors": 0}}}}},
        "mirror": {"dark": {"ambient": 32}}}
    namespace["State"].files["measurements"] = long_path
    open_display(namespace)
    titles = [p[0] for p in app.pages]
    assert titles[0] == "mirror.dark"
    assert titles[1].startswith("...") and titles[1].endswith(
        "_some_more_words.50")
    width = namespace["screen"].measure_text(titles[1])[0]
    reserve = app.pages[1][2] + namespace["MARGIN"]
    assert width <= namespace["screen"].width - 2 * namespace["MARGIN"] \
        - 2 * reserve


# --- dev mode: the case lights ask for attention ------------------------------

def lights_over(namespace, frames=40):
    """Every case-light level a screen shows over `frames` frames."""
    badge = namespace["badge"]
    seen = set()
    for _ in range(frames):
        frame(namespace)
        seen.add(badge.lights)
    return seen


def test_the_case_lights_flash_while_a_measurement_screen_waits():
    # The user is holding the badge screen-first against a mirror or another
    # badge, so a screen that needs them flashes the rear LEDs.
    namespace, app = dev_app()
    open_tool(namespace, 0)                     # the Dark room prompt
    assert app.screen == namespace["DEV_PROMPT"]
    assert lights_over(namespace) == {0.0, 1.0}

    namespace, app = dev_app()
    open_tool(namespace, 1)                     # Who goes first?
    assert app.screen == namespace["DEV_ROLE"]
    assert lights_over(namespace) == {0.0, 1.0}


def test_the_case_lights_flash_on_done_and_stay_off_while_measuring():
    namespace, app = dev_app()
    mirror_badge(namespace)
    badge = namespace["badge"]
    levels = set()
    real = badge.caselights

    def record(level):
        if app.screen == namespace["DEV_MEASURE"] and app.run_begun:
            levels.add(level)
        real(level)
    badge.caselights = record
    complete_mirror_run(namespace)
    assert levels == {0.0}, "the lights must stay off while measuring"
    assert app.screen == namespace["DEV_SAVED"]
    assert lights_over(namespace) == {0.0, 1.0}


def test_the_case_lights_flash_while_no_answer_is_shown():
    namespace, app = dev_app()
    namespace["badge"].light_level = lambda: 32      # nobody answers
    badge = namespace["badge"]
    lit = set()
    with FastClock() as clock:
        open_tool(namespace, 1)
        frame(namespace, "A")                   # I go first
        frame(namespace, "A")                   # start
        for _ in range(2000):
            clock.us += 50000
            drawn = frame(namespace)
            if "No answer from the other badge" in drawn:
                lit.add(badge.lights)
            elif app.screen == namespace["DEV_MEASURE"] and app.run_begun:
                assert badge.lights == 0.0, "lit while measuring"
            if app.screen == namespace["DEV_PROMPT"]:
                break
    assert lit == {0.0, 1.0}, lit


# --- dev mode: probe frame timing ---------------------------------------------

PROBE_INDEX = 2


def run_probe(namespace, limit=20):
    """Prompt, A, then frames until the probe leaves its screen."""
    app = namespace["app"]
    open_tool(namespace, PROBE_INDEX)
    assert app.screen == namespace["DEV_PROBE_PROMPT"]
    frame(namespace, "A")
    seen = []
    for _ in range(limit):
        if app.screen != namespace["DEV_PROBE"]:
            break
        seen.append(frame(namespace))
    return seen


def test_the_probe_prompt_says_what_it_does_and_b_goes_back():
    namespace, app = dev_app()
    open_tool(namespace, PROBE_INDEX)
    drawn = frame(namespace)
    assert drawn[0] == "Probe frame timing"
    assert "A - Start   B - Back" in drawn
    frame(namespace, "B")
    assert app.screen == namespace["DEV"] and app.cursor == PROBE_INDEX
    assert app.probe is None


def test_the_probe_shows_each_step_before_it_runs_and_then_saves():
    import gc
    namespace, app = dev_app()
    namespace["State"].files["measurements"] = MEASURED
    seen = run_probe(namespace)
    steps, titles = namespace["STEPS"], namespace["STEP_TITLES"]
    for i, name in enumerate(steps):
        assert "%s %d/%d" % (titles[name], i + 1, len(steps)) in seen[i], \
            seen[i]
    assert app.screen == namespace["DEV_SAVED"] and app.saved_ok
    saved = namespace["State"].files["measurements"]
    assert saved["mirror"] == MEASURED["mirror"], "the mirror results were lost"
    probe = saved["frame_probe"]
    assert sorted(probe) == sorted(list(steps) + ["firmware", "temperature_c"])
    for name in steps:
        if name != "ring":
            assert "error" not in probe[name], (name, probe[name])
    assert sorted(probe["breakdown"]) == ["field", "status"]
    assert sorted(probe["breakdown"]["field"]) == sorted(
        ["frame", "clear", "field", "measure", "present", "poll"])
    assert sorted(probe["breakdown"]["status"]) == sorted(
        ["frame", "clear", "bands", "column", "lights", "measure", "present",
         "poll"])
    assert probe["firmware"]["set_framerate"] in (True, False)
    reads = dict(probe["reads"])
    detail = reads.pop("detail")
    assert sorted(reads) == ["loop", "read", "sample", "transfer",
                             "transfer_timed"]
    for entry in reads.values():
        assert entry["calls"] > 0 and entry["per_s"] > 0, entry
    # Where the instrumented listener's time went: one work() a pass, and
    # its parts each timed.
    timed = reads["transfer_timed"]["calls"]
    assert detail["work"]["calls"] == timed, (detail["work"], timed)
    for name in ("window_value", "decide_slot", "data_field"):
        assert name in detail, detail
    assert isinstance(detail["phase"], int)
    assert "flicker" in detail and detail["exact"] is False
    # No ring here: the ring step says why, and the firmware entry too.
    assert "error" in probe["ring"]["quiet"]
    assert probe["firmware"]["ring"]
    # What it changed, it put back.
    assert gc.isenabled()
    assert namespace["display"].backlights[-1] == namespace["BACKLIGHT_NORMAL"]
    assert app.probe is None and app.probe_run is None
    frame(namespace)
    frame(namespace, "A")
    assert app.screen == namespace["DEV"] and app.cursor == PROBE_INDEX


def test_the_probe_presents_its_status_before_blocking():
    namespace, app = dev_app()
    open_tool(namespace, PROBE_INDEX)
    frame(namespace, "A")
    display = namespace["display"]
    before = display.updates
    frame(namespace)                       # the refresh step
    # One present to show "Refresh rate 1/6", then the step's own.
    assert display.updates == before + 1 + 1 + load_module("share_wifi", "probe").PRESENTS


def test_the_reads_step_never_presents():
    # The point of it: a listener on a static screen, reading with nothing
    # presented. Only the status frame shown before it goes out.
    namespace, app = dev_app()
    open_tool(namespace, PROBE_INDEX)
    frame(namespace, "A")
    while app.probe.step_name != "reads":
        frame(namespace)
    display = namespace["display"]
    before = display.updates
    frame(namespace)
    assert "reads" in app.probe.results()
    assert display.updates == before + 1


def test_the_probe_transfer_pass_is_a_listener_that_stays_one():
    # What the reads step times as "transfer": the real loop's pass, on a
    # Transfer that keeps listening however long nobody sends.
    namespace, app = dev_app()
    reader = namespace["polled_reader"]()
    t = [10000000]
    saved = time.ticks_us
    time.ticks_us = lambda: t[0]
    reader.ticks_us = time.ticks_us
    reader.light = lambda: (t.__setitem__(0, t[0] + 60) or 32)
    try:
        listener = namespace["probe_listener"](reader)
        assert listener.phase == protocol.PHASE_DATA
        assert listener.scheme == protocol.SCHEME_BACKLIGHT
        assert protocol.SLOT_LADDER[listener.clock.index] == 32
        app.probe_listener = listener
        app.probe_passes = 0
        for _ in range(3000):
            app.probe_transfer()
    finally:
        time.ticks_us = saved
    assert t[0] - 10000000 > 2000000, "it should have listened for seconds"
    assert listener.phase == protocol.PHASE_DATA, listener.result
    assert listener.result is None


def test_b_cancels_the_probe_and_saves_nothing():
    namespace, app = dev_app()
    open_tool(namespace, PROBE_INDEX)
    frame(namespace, "A")
    frame(namespace)
    frame(namespace, "B")
    assert app.screen == namespace["DEV"] and app.cursor == PROBE_INDEX
    assert "measurements" not in namespace["State"].files
    assert app.probe is None
    assert namespace["display"].backlights[-1] == namespace["BACKLIGHT_NORMAL"]


def test_a_failed_probe_save_says_so():
    namespace, app = dev_app()
    namespace["State"].fail_writes = True
    run_probe(namespace)
    assert app.screen == namespace["DEV_SAVED"] and not app.saved_ok
    assert "Could not save measurements" in frame(namespace)


def test_probe_results_appear_in_display_measurements():
    namespace, app = dev_app()
    run_probe(namespace)
    frame(namespace, "A")                  # Done -> Dev Mode
    frame(namespace, "DOWN")
    frame(namespace, "DOWN")
    frame(namespace, "A")
    assert app.screen == namespace["DEV_DISPLAY"]
    titles = [page[0] for page in app.pages]
    assert any("frame_probe.refresh" in t for t in titles), titles
    assert any("breakdown.status" in t for t in titles), titles
    for _ in range(len(app.pages)):        # every page drawn, for the fit test
        frame(namespace, "DOWN")


def test_the_panel_is_restored_after_each_step_so_the_next_status_shows():
    # The breakdown draws the measuring screen, whose first step is the
    # ambient reading with the backlight off.
    namespace, app = dev_app()
    open_tool(namespace, PROBE_INDEX)
    frame(namespace, "A")
    display = namespace["display"]
    while app.probe.step_name != "breakdown":
        frame(namespace)
    frame(namespace)
    assert 0.0 in display.backlights, "the breakdown never set its backlight"
    assert display.backlights[-1] == namespace["BACKLIGHT_NORMAL"]


# --- display measurements: values wider than the screen ------------------------

LONG_VALUES = {"frame_probe": {
    "firmware": {"version": "v1.26.0-preview.123.gabcdef0 on 2026-02-26 "
                            "(GNU 14.2.1 MinSizeRel) with a lot more words",
                 "set_framerate": False},
    "write": {"error": "AttributeError: 'ST7789' object has no attribute "
                       "'set_vsync' and this message goes on and on"},
    "gc": {"blob": "x" * 90}}}


def test_a_value_wider_than_the_screen_wraps_instead_of_being_cut():
    namespace, app = dev_app()
    namespace["State"].files["measurements"] = LONG_VALUES
    open_display(namespace)
    width = namespace["screen"].width - 2 * namespace["MARGIN"]
    hang = namespace["HANG"]
    seen = {}
    for page in app.pages:
        rows = page[3]
        for text, continued in rows:
            assert not text.endswith("..."), text
            limit = width - hang if continued else width
            assert namespace["text_width"](text) <= limit, (text, limit)
        seen[page[0]] = rows
    version = seen["frame_probe.firmware"]
    assert version[0][0].startswith("version: ") and not version[0][1]
    assert len([r for r in version if r[1]]) >= 1, version
    # Every character is still there, in order, the breaks aside.
    for key, rows_key in (("version", "frame_probe.firmware"),
                          ("error", "frame_probe.write"),
                          ("blob", "frame_probe.gc")):
        joined = "".join(t.replace(" ", "") for t, _ in seen[rows_key])
        section = rows_key.split(".")[1]
        value = LONG_VALUES["frame_probe"][section][key]
        assert ('"%s"' % value).replace(" ", "") in joined, (key, joined)


def test_wrapped_rows_are_drawn_indented_under_their_line():
    namespace, app = dev_app()
    namespace["State"].files["measurements"] = LONG_VALUES
    open_display(namespace)
    margin, hang = namespace["MARGIN"], namespace["HANG"]
    boxes = []
    real = namespace["draw_text"]

    def spy(text, box, center=False, middle=False):
        boxes.append((text, box))
        return real(text, box, center, middle)
    namespace["draw_text"] = spy
    for _ in range(len(app.pages)):
        page = app.pages[app.page]
        boxes[:] = []
        frame(namespace)
        for text, continued in page[3]:
            box = [b for t, b in boxes if t == text][0]
            assert box[0] == margin + (hang if continued else 0), (text, box)
        frame(namespace, "DOWN")


def test_the_status_between_tests_goes_back_to_hires_with_runs_clear():
    # A 2nd-badge run goes from a turn's tests back to its status column; the
    # column is text and must be HIRES, with run()'s clear back on.
    namespace, app = dev_app()
    namespace["badge"].light_level = lambda: 32
    badge = namespace["badge"]
    with FastClock() as clock:
        open_tool(namespace, 1)
        frame(namespace, "A")           # I go first
        frame(namespace, "A")           # start
        for _ in range(80):             # into the noise second and calling
            clock.us += 50000
            frame(namespace)
        assert app.run_begun and app.run.test_name is None
        # As a turn's last test would have left it.
        namespace["begin_signalling"]()
        app.signalling = True
        clock.us += 50000
        screen = namespace["screen"]
        screen.rects = []
        drawn = frame(namespace)
    assert badge.current_mode == namespace["HIRES"] | namespace["VSYNC"]
    assert badge.default_clear == namespace["color"].black
    assert not app.signalling
    assert len(screen.rects) == 2 and "B - Cancel" in drawn


# --- the alignment screen ---------------------------------------------------
# Before every transfer: a line down the middle, and the instruction on the
# half away from the light sensor. The other badge's edge goes on the line,
# putting its screen over this one's sensor rather than its bezel.


def test_share_and_receive_both_stop_at_the_alignment_line_first():
    for buttons in ((("A",), ("A",)), (("DOWN",), ("A",))):
        namespace, app = app_with(CREDENTIALS)
        for pressed in buttons:
            frame(namespace, *pressed)
        assert app.screen == "align" and app.transfer is None
        frame(namespace)
        assert app.screen == "align", "it waits for A"
        frame(namespace, "A")
        assert app.screen == "transfer"


def test_the_alignment_screen_is_a_middle_line_and_text_on_the_left():
    namespace = load_app("share_wifi", networks=CREDENTIALS, alignment=True)
    app = namespace["app"]
    frame(namespace, "DOWN")
    frame(namespace, "A")                 # Receive
    screen = namespace["screen"]
    screen.rects = []
    screen.boxes = []
    drawn = frame(namespace)
    assert namespace["ALIGN_TEXT"] in drawn
    assert namespace["ALIGN_TEXT"] == \
        "Align edge of badge with line. A to continue"
    middle = screen.width // 2
    (line,) = screen.rects
    _, x, y, w, h = line
    assert x <= middle < x + w and y == 0 and h == screen.height, line
    # The light sensor is beside the RIGHT edge: nothing is written there.
    for message, x, y, w, h in screen.boxes:
        assert x + w <= middle - 1, (message, x, w)


def test_b_on_the_alignment_screen_goes_back_where_it_came_from():
    namespace, app = app_with(CREDENTIALS)
    frame(namespace, "A")                 # Share
    frame(namespace, "A")                 # a network
    frame(namespace, "B")
    assert app.screen == "pick_share" and app.transfer is None
    namespace, app = app_with(CREDENTIALS)
    frame(namespace, "DOWN")
    frame(namespace, "A")                 # Receive
    frame(namespace, "B")
    assert app.screen == "main" and app.transfer is None


def test_a_retry_lines_the_badges_up_again():
    namespace, app = app_with(CREDENTIALS)
    frame(namespace, "A")
    frame(namespace, "A")
    frame(namespace, "A")                 # past the alignment line
    app.transfer.fail(namespace["ERR_NO_PEER"])
    frame(namespace)
    frame(namespace, "B")                 # retry
    assert app.screen == "align"
    frame(namespace, "B")                 # not yet: back to the result
    assert app.screen == "result"
    frame(namespace, "B")
    frame(namespace, "A")
    assert app.screen == "transfer" and app.transfer.leader is True


def test_the_2nd_badge_prompt_is_the_alignment_screen():
    namespace, app = dev_app()
    open_tool(namespace, 1)
    frame(namespace, "A")                 # I go first
    screen = namespace["screen"]
    screen.rects = []
    drawn = frame(namespace)
    assert drawn[0] == "Dark room"
    assert namespace["ALIGN_TEXT"] in drawn
    assert any(r[1] <= screen.width // 2 < r[1] + r[3]
               and r[4] == screen.height for r in screen.rects), screen.rects


def test_the_probe_times_a_full_staircase_replay():
    # M7's replay: every kept sample of a full buffer re-fed, after a fit.
    namespace, app = dev_app()
    out = namespace["probe_replay"]()
    protocol = load_module("share_wifi", "protocol")
    assert 0 < out["samples"] <= protocol.FLICKER_BUFFER, out
    assert out["us"] >= 0
