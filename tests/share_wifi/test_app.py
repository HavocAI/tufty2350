"""The app shell: menus, navigation, the config file, and connecting.

These drive App.frame() the way run() would, with stubbed buttons, and assert
on what was drawn. They check the state machine, not the appearance.
"""

import math
import time

from held import HeldTransfer
from harness import FakeSecrets, load_module
import held
from harness import load_app as harness_load_app


def load_app(*args, **kwargs):
    """The app, with its transfer loop taking one reading per update().

    On the badge the loop reads until the transfer ends, which a test that
    scripts one symbol a frame -- or steps a real transfer whose peer never
    appears -- would never reach. The loop itself is tested below with
    TRANSFER_PASSES as the badge has it.
    """
    namespace = harness_load_app(*args, **kwargs)
    namespace["TRANSFER_PASSES"] = 1
    give_ring(namespace)
    give_pacer(namespace)
    return namespace


class FakePacer:
    """fasttx.Pacer as the app uses it, playing nothing: CPython has no
    DMA, and a Leader needs a pacer to share."""

    def __init__(self):
        self.played = []

    def opened(self, div16):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cc_for(self, backlight, level):
        return int(level * 1000)

    def word(self, cc):
        return cc

    def transmit(self, div16, words, n):
        self.played.append(n)

    def release(self):
        pass


def give_pacer(namespace):
    """The fast block's pacer, as fast_pacer() would have made it."""
    from array import array
    namespace["_FAST"]["pacer"] = (FakePacer(), 16, array("I", bytes(4 * 8000)))


def give_ring(namespace):
    """The DMA ring a transfer reads through, as ring_reader() would have
    made it: a RingReader over a ListRing nothing fills, on the real clock.
    Under CPython there is no `machine`, so ring_reader() itself refuses."""
    readers = namespace["readers"]
    namespace["_READERS"]["ring"] = readers.RingReader(
        held.ListRing(4096, 20))
    namespace["_READERS"]["note"] = None

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
    assert transfer.fast is not None, "a Leader shares in fast blocks"


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
    transfer = HeldTransfer(False)
    transfer.result = namespace["OK"]
    transfer.received = [b"NewNet", b"letmein123"]
    app.finish_transfer(transfer)

    stored = namespace["State"].data
    assert stored["NewNet"] == {"key": "letmein123",
                                "security": namespace["SEC_UNKNOWN"]}
    assert stored["HomeNet"] == "hunter2!!", "existing networks must survive"


def test_undecodable_received_payload_reports_decode_error():
    namespace, app = app_with()
    transfer = HeldTransfer(False)
    transfer.result = namespace["OK"]
    transfer.received = [b"one field only, no key or type"[:40] * 2]
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


# --- the levels: the backlight on a white screen ----------------------------
# Every symbol is a display.backlight() value from protocol.LEVEL_SCHEMES,
# behind a white screen drawn once. Calibration rejects a staircase unless
# L_mid - L_black >= span / 4 and L_white - L_mid >= span / 4, so a middle
# level outside that window makes every transfer end ERR_CONTRAST no matter
# how good the optics are. simulator.backlight_duty() is the firmware's own
# value-to-duty curve, read out of modules/c/st7789/, and light is taken as
# proportional to duty -- true for an averaging sensor, which the badge's
# is [measured-v2].

import simulator


def test_a_transfer_reads_through_the_launchs_ring():
    namespace, app = app_with(CREDENTIALS)
    frame(namespace, "DOWN")
    frame(namespace, "A")                 # Receive
    frame(namespace, "A")                 # past the alignment line
    reader = app.transfer.reader
    assert reader is namespace["ring_reader"]()
    assert type(reader) is namespace["readers"].RingReader
    app.transfer = None
    app.begin_transfer(False)
    assert app.transfer.reader is reader, "one reader per launch"


def test_a_badge_without_the_ring_refuses_to_transfer_and_says_why():
    # Under CPython fastadc cannot import `machine`: the ring is refused, as
    # on a firmware without what it needs, and there is no other reader.
    namespace = harness_load_app("share_wifi", networks=CREDENTIALS)
    app, badge, display = namespace["app"], namespace["badge"], \
        namespace["display"]
    frame(namespace, "DOWN")
    frame(namespace, "A")                 # Receive
    frame(namespace, "A")                 # past the alignment line
    assert app.transfer is None, "a transfer started without the ring"
    assert app.screen == "result"
    note = namespace["ring_note"]()
    assert note, "why there is no ring is kept"
    assert namespace["NO_RING_TEXT"] in app.error and note in app.error
    assert display.backlights[-1] == namespace["BACKLIGHT_NORMAL"]
    assert badge.default_clear is not None
    drawn = frame(namespace)
    text = " ".join(str(d) for d in drawn)
    assert "light" in text, list(drawn)
    # A retry would meet the same refused ring: only a relaunch helps.
    assert "Retry" not in text, list(drawn)
    frame(namespace, "B")
    assert app.screen == "result", "B retried a refused ring"
    for name in ("polled_reader", "transfer_reader", "RING_ENABLED"):
        assert name not in namespace, name


def test_the_backlight_middle_level_can_actually_be_calibrated():
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
    assert protocol.MID_BACKLIGHT not in (0.25, 0.33)


def test_the_transfer_white_is_the_full_range_not_the_tinted_palette():
    # On the Tufty color.white is rgb(222, 238, 214): drawing with it gave
    # away part of the swing.
    namespace, app = app_with()
    color = namespace["color"]
    assert namespace["WHITE_PEN"] == color.rgb(255, 255, 255)
    assert namespace["WHITE_PEN"] != color.white
    for name in ("FIELD_COLOR", "FIELD_BACKLIGHT", "show_field"):
        assert name not in namespace, name


# --- the backlight ----------------------------------------------------------

def test_every_field_is_its_tables_backlight():
    for scheme in (protocol.SCHEME_BEACON, protocol.SCHEME_BACKLIGHT):
        namespace = in_transfer()
        transfer = namespace["app"].transfer
        display = namespace["display"]
        transfer.scheme = scheme          # as calibration would leave it
        symbols = [protocol.LOW, protocol.MID, protocol.HIGH] * 2
        scripted(namespace, symbols)
        for symbol in symbols:
            frame(namespace)
            assert display.backlights[-1] == \
                protocol.LEVEL_SCHEMES[scheme][symbol], (scheme, symbol)


def test_a_transfer_puts_the_backlight_back_when_it_ends():
    # The firmware leaves it at 230/255 with nothing to read the setting back,
    # and a transfer can end with it off -- so the app owes a restore on the
    # way out.
    for scheme in (protocol.SCHEME_BEACON, protocol.SCHEME_BACKLIGHT):
        namespace, app = app_with(CREDENTIALS)
        display = namespace["display"]
        frame(namespace, "DOWN")
        frame(namespace, "A")             # Receive
        frame(namespace, "A")             # past the alignment line
        assert app.screen == "transfer"
        app.transfer.scheme = scheme
        frame(namespace)
        assert display.backlights[-1] in \
            protocol.LEVEL_SCHEMES[scheme].values()

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


# --- the transfer screen -----------------------------------------------------
# White, drawn once and presented before the reader starts, at the app's own
# HIRES; every symbol after it a backlight change and nothing drawn. run()'s
# own clear is off while it runs. See section 5 of the protocol spec.


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


def test_a_transfer_keeps_the_apps_mode_with_runs_clear_off():
    namespace = in_transfer()
    badge = namespace["badge"]
    assert badge.current_mode == namespace["HIRES"] | namespace["VSYNC"]
    assert namespace["LORES"] | namespace["VSYNC"] not in badge.modes, \
        "a transfer switched to LORES"
    assert badge.default_clear is None, \
        "run() would clear the white before every frame"


def test_the_screen_is_white_drawn_once_and_nothing_else():
    namespace = load_app("share_wifi", networks=CREDENTIALS, alignment=True)
    screen, display = namespace["screen"], namespace["display"]
    frame(namespace, "DOWN")
    frame(namespace, "A")                 # Receive
    screen.fills = []
    presents = display.updates
    frame(namespace, "A")                 # past the alignment line
    assert namespace["app"].screen == "transfer"
    # run()'s clear of that frame, then the white, once.
    assert screen.fills[-1] == namespace["WHITE_PEN"], screen.fills
    assert screen.fills.count(namespace["WHITE_PEN"]) == 1, screen.fills
    assert display.updates > presents, "the white was never presented"
    scripted(namespace, [protocol.HIGH, protocol.LOW, protocol.MID] * 4)
    for _ in range(12):
        screen.rects = []
        screen.fills = []
        drawn = frame(namespace)
        assert screen.fills == [], "a symbol drew pixels"
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
        self.fast_burst = None

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
    # Every symbol a backlight level on the white, so a whole exchange runs
    # in one update() -- every backlight change made in place, nothing
    # presented and nothing filled -- until the transfer ends.
    namespace = looping()
    app, display, screen = namespace["app"], namespace["display"], namespace["screen"]
    scheme = protocol.SCHEME_BACKLIGHT
    namespace["show_backlight"](protocol.LOW_BACKLIGHT)
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


def test_the_transfer_loop_draws_no_pixels_whatever_it_shows():
    # Every symbol of both tables, through the transfer loop as the badge
    # runs it, from a scripted transfer: nothing is filled or presented, and
    # every backlight set is one of the tables'.
    namespace = looping()
    app, display, screen = namespace["app"], namespace["display"], \
        namespace["screen"]
    levels = set()
    for table in protocol.LEVEL_SCHEMES.values():
        levels.update(table.values())
    presents, fills = display.updates, len(screen.fills)
    for scheme in (protocol.SCHEME_BEACON, protocol.SCHEME_BACKLIGHT):
        lights = len(display.backlights)
        fake = LoopTransfer(scheme, [protocol.LOW, protocol.HIGH,
                                     protocol.MID, protocol.LOW] * 10)
        app.transfer = fake
        app.screen = namespace["TRANSFER"]
        app.do_transfer()
        assert fake.result == protocol.OK
        set_during = display.backlights[lights:]
        assert set(set_during[:-1]) <= levels, set_during
        assert set_during[-1] == namespace["BACKLIGHT_NORMAL"]
    assert display.updates == presents, "a transfer presented"
    assert len(screen.fills) == fills, "a transfer filled the screen"


def test_the_reader_lives_for_the_whole_transfer():
    # Built in begin_transfer and stopped in finish_transfer, the single
    # exit: do_transfer() returning to run() -- every pass, here -- must not
    # touch the reader.
    namespace = looping()
    namespace["TRANSFER_PASSES"] = 1
    app = namespace["app"]
    fake = LoopTransfer(protocol.SCHEME_BACKLIGHT,
                        [protocol.LOW, protocol.HIGH, protocol.LOW,
                         protocol.HIGH, protocol.LOW])
    app.transfer = fake
    returns = 0
    while app.screen == namespace["TRANSFER"]:
        app.do_transfer()
        returns += 1
        assert returns < 20
    assert returns >= 5, "one pass a call"
    assert fake.reader.stops == 1, "stopped once, at the end"


def test_a_collection_the_protocol_asks_for_happens_between_its_calls():
    namespace = looping()
    app = namespace["app"]
    scheme = protocol.SCHEME_BACKLIGHT
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


def test_the_backlight_is_set_only_when_the_field_changes():
    namespace = in_transfer()
    display = namespace["display"]
    sequence = [protocol.HIGH, protocol.HIGH, protocol.HIGH, protocol.LOW,
                protocol.LOW, protocol.HIGH]
    scripted(namespace, [protocol.LOW] + sequence)
    frame(namespace)
    sets = []
    for _ in sequence:
        display.backlights = []
        frame(namespace)
        sets.append(len(display.backlights))
    assert sets == [1, 0, 0, 1, 0, 1], sets


def test_leaving_a_transfer_brings_back_runs_clear_and_the_backlight():
    namespace = in_transfer()
    badge, app = namespace["badge"], namespace["app"]
    frame(namespace, "B")               # cancel
    assert app.screen == "result"
    assert badge.current_mode == namespace["HIRES"] | namespace["VSYNC"]
    assert badge.default_clear == namespace["color"].black
    assert namespace["display"].backlights[-1] == namespace["BACKLIGHT_NORMAL"]
    drawn = frame(namespace)
    assert drawn, "the result screen drew nothing"


def test_a_new_transfer_draws_its_white_again():
    # The result screen between drew over it.
    namespace = in_transfer()
    screen, app = namespace["screen"], namespace["app"]
    scripted(namespace, [protocol.LOW] * 2)
    frame(namespace)
    frame(namespace, "B")               # cancel
    frame(namespace, "B")               # the result screen's retry
    screen.fills = []
    frame(namespace, "A")               # past the alignment line
    assert app.screen == "transfer", app.screen
    assert namespace["WHITE_PEN"] in screen.fills


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
    transfer = HeldTransfer(False)
    transfer.result = namespace["OK"]
    transfer.received = [b"OldRouter", b"abcde",
                         bytes([namespace["SEC_WEP"]])]
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


def hold_c(namespace, ms=5000):
    """C held for `ms`, a frame at a time, then released."""
    badge = namespace["badge"]
    real = badge.held
    badge.held = lambda button=None: button == "C" or real(button)
    try:
        for _ in range(-(-ms // badge.ticks_delta) + 1):
            frame(namespace)
    finally:
        badge.held = real
    frame(namespace)


def test_holding_c_five_seconds_reveals_dev_mode_and_selects_it():
    namespace, app = app_with()
    text = main_text(namespace, "C")
    assert "Dev Mode" not in text, "a press is not a hold"
    hold_c(namespace)
    text = main_text(namespace)
    assert "> Dev Mode" in text
    assert app.cursor == len(namespace["MENU_ITEMS"])


def test_dev_mode_stays_after_leaving_the_main_screen():
    namespace, app = app_with(CREDENTIALS)
    hold_c(namespace)
    frame(namespace, "DOWN")              # wraps to Share
    frame(namespace, "A")
    assert app.screen == "pick_share"
    frame(namespace, "B")
    assert "Dev Mode" in main_text(namespace)


def test_dev_mode_is_not_remembered_across_launches():
    namespace, app = app_with()
    hold_c(namespace)
    namespace, app = app_with()
    assert "Dev Mode" not in main_text(namespace)


def test_c_does_nothing_off_the_main_screen():
    namespace, app = app_with(CREDENTIALS)
    open_connect(namespace)
    hold_c(namespace)
    assert app.screen == "connect_list"
    frame(namespace, "B")
    assert "Dev Mode" not in main_text(namespace)


def open_dev(namespace):
    hold_c(namespace)
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


# --- dev mode: what stays on the badge --------------------------------------
# The tools are dev link subtools, tested in test_devtools.py; the link in
# test_devmode_app.py.


def dev_app():
    # With the alignment constants, so messages and the centre column wrap
    # rather than being cut to one line and the full strings can be asserted.
    namespace = load_app("share_wifi", alignment=True)
    return namespace, namespace["app"]


def open_tool(namespace, index):
    open_dev(namespace)
    for _ in range(index):
        frame(namespace, "DOWN")
    frame(namespace, "A")


def test_the_app_writes_measurements_itself_rather_than_through_state():
    # State.save() would hide the error this exists to report.
    source = open(namespace_source()).read()
    body = source.split("def save_measurement(", 1)[1].split("\n\n\ndef ", 1)[0]
    assert "write_measurements(" in body
    assert "State.modify" not in body and "State.save" not in body


def namespace_source():
    import os
    from harness import APPS
    return os.path.join(APPS, "share_wifi", "__init__.py")


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
        assert read() == round(27 - (14021 * 3.3 / 65535 - 0.706) / 0.001721,
                               1)
    finally:
        if saved is None:
            sys.modules.pop("machine", None)
        else:
            sys.modules["machine"] = saved


# --- dev mode: display measurements -------------------------------------------

def open_display(namespace):
    open_tool(namespace, namespace["DISPLAY_INDEX"])
    assert namespace["app"].screen == namespace["DEV_DISPLAY"]


# --- dev mode: clear saved measurements ---------------------------------------

def clear_saved(namespace):
    open_tool(namespace, namespace["CLEAR_INDEX"])
    assert namespace["app"].screen == namespace["DEV_DONE"]
    return frame(namespace)


def test_clear_deletes_every_measurement_and_says_so():
    namespace, app = dev_app()
    namespace["State"].files["measurements"] = MEASURED
    drawn = clear_saved(namespace)
    assert drawn[0] == "Done"
    assert namespace["CLEARED"] in drawn
    assert "measurements" not in namespace["State"].files
    frame(namespace, "A")
    assert app.screen == namespace["DEV"] and \
        app.cursor == namespace["CLEAR_INDEX"]
    frame(namespace, "UP")                  # display saved measurements
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
    assert namespace["CLEARED"] in drawn


def test_a_file_still_there_after_clearing_says_it_could_not():
    namespace, app = dev_app()
    namespace["State"].files["measurements"] = MEASURED
    namespace["State"].fail_deletes = True
    drawn = clear_saved(namespace)
    assert "Could not clear" in drawn
    assert namespace["State"].files["measurements"] == MEASURED


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
        expected = namespace["measurement_pages"](
            MEASURED, namespace["menu_rows"](), lambda line: [line])
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
    rows = seen["frame_probe.firmware"]
    at = [i for i, r in enumerate(rows) if r[0].startswith("version: ")][0]
    assert not rows[at][1] and rows[at + 1][1], rows
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




# --- display: sections and pages, the app's own --------------------------------

def sample_measurements():
    return {
        "second_badge": {"role": "first",
                         "dark": {"ambient": 16,
                                  "loopback_ring": {"error": "no sync symbol"}}},
        "mirror": {"dark": {
            "ambient": 32,
            "levels": {"mid_share": 0.4, "high": 112, "span": 80, "low": 32,
                       "mid": 64},
            "backlight_curve": {"1.0": 112, "0.0": 16, "0.5": 64},
            "flicker": {"1.0": {"verdict": "averages", "min": 96}},
        }},
    }


def test_sections_are_titled_by_json_path_numbers_first_then_by_name():
    namespace, app = app_with()
    got = namespace["sections"](sample_measurements(), "", [])
    assert [title for title, _ in got] == [
        "mirror.dark", "mirror.dark.backlight_curve",
        "mirror.dark.flicker.1.0", "mirror.dark.levels", "second_badge",
        "second_badge.dark", "second_badge.dark.loopback_ring"]
    by_title = dict(got)
    assert by_title["mirror.dark"] == ["ambient: 32"]
    assert by_title["mirror.dark.backlight_curve"] == [
        "0.0: 16", "0.5: 64", "1.0: 112"]
    assert by_title["mirror.dark.levels"] == [
        "high: 112", "low: 32", "mid: 64", "mid_share: 0.4", "span: 80"]
    assert by_title["mirror.dark.flicker.1.0"] == ["min: 96",
                                                   'verdict: "averages"']
    assert by_title["second_badge"] == ['role: "first"']


def test_pages_split_long_sections_with_cont_titles():
    namespace, app = app_with()
    pages = namespace["measurement_pages"]
    got = pages(sample_measurements(), 3, lambda line: [line])
    titles = [title for title, _ in got]
    assert titles[3:5] == ["mirror.dark.levels", "mirror.dark.levels (cont.)"]
    assert got[3][1] == ["high: 112", "low: 32", "mid: 64"]
    assert got[4][1] == ["mid_share: 0.4", "span: 80"]
    flat = [line for _, lines in got for line in lines]
    assert flat == [line for _, lines in namespace["sections"](
        sample_measurements(), "", []) for line in lines]
    assert pages({}, 5, lambda line: [line]) == []


def test_wrapped_lines_keep_their_rows_together_on_a_page():
    namespace, app = app_with()

    def wrap(line):
        return [(word, i > 0) for i, word in enumerate(line.split(" "))]
    got = namespace["measurement_pages"](
        {"s": {"a": "x", "b": "one two three", "c": "y"}}, 4, wrap)
    assert got == [
        ("s", [("a:", False), ('"x"', True)]),
        ("s (cont.)", [("b:", False), ('"one', True), ("two", True),
                       ('three"', True)]),
        ("s (cont.)", [("c:", False), ('"y"', True)])]


def test_a_line_longer_than_a_page_is_split_across_pages():
    namespace, app = app_with()
    got = namespace["measurement_pages"](
        {"s": {"a": "1 2 3 4 5 6 7", "b": "z"}, "t": {"c": 1}}, 3,
        lambda line: line.split(" "))
    assert [title for title, _ in got] == ["s", "s (cont.)", "s (cont.)",
                                           "s (cont.)", "t"]
    assert got[2][1] == ["6", '7"']
    assert got[3][1] == ["b:", '"z"']


def test_every_shown_line_reads_back_as_the_value_it_shows():
    # The display is for typing a file back in by hand: every line's value is
    # JSON, and the paths and lines rebuild the file exactly.
    import json
    namespace, app = app_with()
    data = sample_measurements()
    data["mirror"]["dark"]["flicker"]["0.74"] = {"sd": 6.2, "verdict": "x y"}
    got = namespace["measurement_pages"](data, 4, lambda line: [line])
    rebuilt = {}
    for title, lines in got:
        node, rest, src = rebuilt, title.replace(" (cont.)", ""), data
        while rest:
            key = next(k for k in sorted(src, key=len, reverse=True)
                       if rest == k or rest.startswith(k + "."))
            node, src, rest = node.setdefault(key, {}), src[key], \
                rest[len(key) + 1:]
        for line in lines:
            key, value = line.split(": ", 1)
            node[key] = json.loads(value)
    assert rebuilt == data
