# The protocol transmits on the whole screen, at LORES, and nothing else is
# drawn while it does: see "the whole screen, at LORES" below and "Screen as a
# transmitter" in the protocol. Every screen with text is HIRES.

# Share WiFi credentials between badges over the screen and light sensor.
#
# Protocol specification: .agents/docs/optical-data-transfer-protocol.md
# API notes: .agents/docs/badgeware-api.md, .agents/docs/badgeware-hardware.md
#
# Everything here is a per-frame state machine: transmitting means changing the
# screen, which only happens when a frame is presented, so a blocking loop
# would transmit nothing at all.

import gc
import json
import os
import time

# wifi is NOT one of the injected globals -- it is modules/common/wifi.py in the
# firmware, and the bundled apps import it like this. State, badge, screen and
# the rest genuinely are builtins.
import wifi

# Absolute, not relative: the launcher does sys.path.insert(0, app_dir) before
# importing an app, so sibling modules are plain top-level imports. The
# fallback covers being imported as a package instead. Named rather than `*`,
# so every protocol name the app depends on is listed here.
try:
    from protocol import (
        ERR_CANCELLED, ERR_CONTRAST, ERR_CRC, ERR_DECODE, ERR_LOST_PEER,
        BACKLIGHT_INDEX, ERR_NO_PEER, FAST_INDEX, LEVEL_SCHEMES, LOW, OK,
        PHASE_ANNOUNCE, PHASE_DONE, PHASE_PRESENT,
        SCHEME_BACKLIGHT, SCHEME_FIELDS, Levels, SlotClock, SlotReceiver,
        Transfer, parse_payload,
    )
except ImportError:
    from .protocol import (
        ERR_CANCELLED, ERR_CONTRAST, ERR_CRC, ERR_DECODE, ERR_LOST_PEER,
        BACKLIGHT_INDEX, ERR_NO_PEER, FAST_INDEX, LEVEL_SCHEMES, LOW, OK,
        PHASE_ANNOUNCE, PHASE_DONE, PHASE_PRESENT,
        SCHEME_BACKLIGHT, SCHEME_FIELDS, Levels, SlotClock, SlotReceiver,
        Transfer, parse_payload,
    )

# Dev Mode's measurement engine: pure, like the protocol, and imported the same
# way. Its PHASE_DONE is not imported -- the protocol's has the same name.
try:
    from measure import (
        PHASE_ANSWERING, PHASE_CALLING, PHASE_MEASURING, PHASE_NOISE,
        PHASE_SENDING, TOOL_MIRROR, TOOL_SECOND_BADGE, MirrorRun, SecondBadgeRun,
        core_temp_c, merge_results, pages, tool_result,
    )
except ImportError:
    from .measure import (
        PHASE_ANSWERING, PHASE_CALLING, PHASE_MEASURING, PHASE_NOISE,
        PHASE_SENDING, TOOL_MIRROR, TOOL_SECOND_BADGE, MirrorRun, SecondBadgeRun,
        core_temp_c, merge_results, pages, tool_result,
    )

# Dev Mode's frame timing probe: pure too, with the badge handed in as hooks.
try:
    from probe import STEP_TITLES, STEPS, TOOL_PROBE, FrameProbe, Hooks
except ImportError:
    from .probe import STEP_TITLES, STEPS, TOOL_PROBE, FrameProbe, Hooks

# Dev Mode's room-flicker tool: pure, the sensor and clock handed in.
try:
    from flicker import TOOL_FLICKER, Capture, analyse, summary
except ImportError:
    from .flicker import TOOL_FLICKER, Capture, analyse, summary

badge.mode(HIRES | VSYNC)

CONNECT_TIMEOUT_MS = 30000   # a UI deadline, not part of the wire protocol
WIFI_PENDING = (0, 1, 2, 3)  # idle/connecting/connected/got-ip; anything else failed

# During a transfer each symbol sets its own backlight (FIELD_BACKLIGHT, under
# drawing): full throughout for the fields scheme, so the peer gets the widest
# swing the hardware has, and per level for the backlight one. The firmware sets 230/255 at init
# and nothing reads the light sensor to change it, so that is what "restore it
# after" means -- there is no getter to read first.
BACKLIGHT_NORMAL = 230 / 255

ERROR_TEXT = {
    ERR_NO_PEER: "No other badge found.\nPoint them at each other.",
    ERR_CONTRAST: "Can't see the other screen.\nMove closer, dim the lights.",
    ERR_LOST_PEER: "Lost the other badge.",
    ERR_CRC: "Too many errors.\nHold the badges still.",
    ERR_DECODE: "Received data was invalid.",
    ERR_CANCELLED: "Cancelled.",
}

# --- layout -----------------------------------------------------------------

MARGIN = 6
LINE_HEIGHT = 16
ALIGN_LINE = 2                  # px: the alignment screen's middle line

# Dev Mode's handshake screen -- the one screen that shows a field and text
# together -- puts the field in TWO BANDS, one down each edge, with the text
# between them. Two rather than one half because the sensor is not central: it
# sits about 1mm outside the RIGHT edge at mid height and reads a patch near
# itself, and two badges facing each other are mirrored, so one band would be a
# bet on how they are held. The text column is drawn on black and must not
# animate: a sensor whose patch straddles a band edge picks some of it up.
# Transfers and tests used this layout too, until it measured at three 60 Hz
# refreshes a frame; they now fill the whole screen.


# `font.<name>` works on current firmware, but was seen raising on an older
# build, so resolve defensively and treat a missing font as "keep the default".
# `rom_font` is not a real name on any build; it is probed only because an
# older one was thought to have it.
def pick_font(*names):
    spaces = []
    try:
        spaces.append(font)
    except NameError:
        pass
    try:
        spaces.append(rom_font)     # noqa: F821 - probed, may not exist
    except NameError:
        pass
    for space in spaces:
        for name in names:
            chosen = getattr(space, name, None)
            if chosen is not None:
                return chosen
    return None


_font = pick_font("smart", "memo", "sins")
if _font is not None:
    screen.font = _font



# --- config file ------------------------------------------------------------
# State takes a name, not a path, and puts it at /state/share_wifi.json.
# Keys must survive a JSON round trip, so SSIDs that are not valid UTF-8 are
# stored percent-escaped.

CONFIG = "share_wifi"

# 802.11 and WPA limits. These live here, not in the protocol: the protocol
# carries a list of byte fields and has no opinion about what they mean.
SSID_MAX = 32
KEY_MIN = 8
KEY_MAX = 63
PSK_LEN = 64

# WEP keys are 5 or 13 characters, or the same written as 10 or 26 hex digits.
# All four are outside the WPA range, so without knowing the security type a
# WEP key looks like a corrupt WPA one.
WEP_LENGTHS = (5, 10, 13, 26)

# Security types, as a credential's third field. The codes are the generic
# MicroPython auth-mode numbering, NOT what WLAN.scan() returns on this badge --
# that is a CYW43_AUTH_FLAG_* bitmask. 255 is this app's: a key whose network
# type nobody recorded.
#
# The field exists because key length has no single answer -- 0 for open, 5 or
# 13 for WEP, 8..63 for WPA -- and because an empty key is otherwise ambiguous
# between "no password" and "password lost".
SEC_OPEN = 0
SEC_WEP = 1
SEC_WPA = 2
SEC_WPA2 = 3
SEC_WPA_WPA2 = 4
SEC_ENTERPRISE = 5
SEC_UNKNOWN = 255

SECURITY_NAMES = {
    SEC_OPEN: "Open",
    SEC_WEP: "WEP",
    SEC_WPA: "WPA",
    SEC_WPA2: "WPA2",
    SEC_WPA_WPA2: "WPA/WPA2",
    SEC_ENTERPRISE: "Enterprise",
    SEC_UNKNOWN: "Unknown",
}


def security_name(security):
    return SECURITY_NAMES.get(security, "Unknown (%d)" % security)


# Shown instead of attempting a join this firmware cannot make.
WEP_UNSUPPORTED = "WEP networks not supported"


# wifi.status() raised NameError from inside wifi.py on one badge, on a branch
# only reached once a connection is live. Nothing here is load-bearing --
# is_connected() decides success and CONNECT_TIMEOUT_MS decides failure -- so an
# unreadable status is reported as STILL CONNECTING rather than as a failure,
# which would abandon an attempt that may be about to succeed.
STATUS_UNREADABLE = "Wi-Fi status unavailable"


def wifi_status():
    """(code, description, readable) from the firmware."""
    try:
        code, description = wifi.status()
    except Exception:   # noqa: BLE001 - it has raised NameError from inside wifi.py
        return 1, STATUS_UNREADABLE, False      # 1 is "Connecting"
    return code, description, True


def wep_supported():
    """Whether this firmware can select WEP when joining a network.

    Probed, not hardcoded. Today it is always False, and the reason is in the
    radio driver rather than in anything above it: `cyw43_ll_wifi_join()`
    accepts Open, WPA-TKIP, WPA2-AES, WPA2-mixed and the two WPA3 modes, and
    returns -CYW43_EINVAL for anything else before it issues a single ioctl.
    There is no code in that driver to install a WEP key either -- it only ever
    writes `wsec_pmk`, which is the WPA passphrase path.

    The constant is the signal because it is the only part of this that is
    visible from Python: `network.WLAN` exports SEC_OPEN, SEC_WPA_WPA2,
    SEC_WPA3 and SEC_WPA2_WPA3, and no SEC_WEP, because there is nothing behind
    it. A firmware that gained WEP would export it alongside the rest.

    If this ever returns True, one more thing is needed before a join will
    work: `wifi.connect()` passes no `security=` to `wlan.connect()`, so WEP
    would have to go through `network.WLAN` directly. That is the point to wire
    it, and it is why this gates the attempt rather than pretending the
    ordinary path would carry it.
    """
    try:
        from network import WLAN
    except ImportError:
        return False
    return hasattr(WLAN, "SEC_WEP")


def valid_key(key, security):
    """Whether `key` is a usable key for a network of this type."""
    if security == SEC_OPEN:
        return len(key) == 0        # there is no key, and a key would be wrong
    if security == SEC_ENTERPRISE:
        # Needs an identity and usually a certificate, neither of which is a
        # passphrase and neither of which this carries. Better to refuse than
        # to deliver something that cannot connect.
        return False
    if len(key) == 0:
        return False
    if security == SEC_WEP:
        return len(key) in WEP_LENGTHS
    return KEY_MIN <= len(key) <= KEY_MAX or len(key) == PSK_LEN


def valid_credential(fields):
    """A received payload as (ssid, key, security), or None.

    Two fields is what a badge sent before there was a security field; the type
    is inferred rather than the credential refused.
    """
    if fields is None or len(fields) not in (2, 3):
        return None
    ssid = fields[0]
    key = fields[1]
    if len(fields) == 3:
        if len(fields[2]) != 1:
            return None
        security = fields[2][0]
    else:
        security = SEC_OPEN if not key else SEC_UNKNOWN
    if security not in SECURITY_NAMES:
        return None
    if not 1 <= len(ssid) <= SSID_MAX:
        return None
    if not valid_key(key, security):
        return None
    try:
        key.decode("utf-8")     # the SSID may be arbitrary octets; the key may not
    except UnicodeError:
        return None
    return ssid, key, security


def credential_fields(ssid_bytes, key_text, security):
    """A credential as the list of byte fields the protocol carries."""
    return [ssid_bytes, key_text.encode("utf-8"), bytes([security])]


def escape_ssid(raw):
    # A leading "%" marks a hex-escaped SSID, so an SSID that genuinely starts
    # with "%" has to be escaped too even though it is valid UTF-8. Otherwise
    # the SSID "%41" is stored verbatim and read back as b"A" -- a different
    # network, silently. A "%" anywhere else is unambiguous and kept as is.
    try:
        text = raw.decode("utf-8")
    except (UnicodeError, AttributeError):
        text = None
    if text is None or text.startswith("%"):
        return "%" + "".join("%02x" % b for b in raw)
    return text


def unescape_ssid(text):
    if not text.startswith("%"):
        return text.encode("utf-8")
    body = text[1:]
    try:
        if len(body) % 2:
            raise ValueError("odd length")
        return bytes(int(body[i:i + 2], 16) for i in range(0, len(body), 2))
    except ValueError:
        # Not something we wrote. Better a wrong name than a crash on the
        # screen that lists saved networks.
        return text.encode("utf-8")


def radio_ssid(name):
    """The SSID to hand the radio, from the name the config file is keyed by.

    Only the radio wants the original octets; everything else uses the escaped
    name. An ordinary SSID round trips to the identical string.
    """
    raw = unescape_ssid(name)
    try:
        return raw.decode("utf-8")
    except UnicodeError:
        return raw


def load_networks():
    """Every saved network, as {escaped ssid: (key, security)}.

    State.load() returns a bool and fills the dict it was handed, whatever the
    prose says; taking the return value when it happens to be a dict costs one
    comparison and covers a build that does. Returning {} on an unexpected
    shape would silently forget every saved network.
    """
    loaded = {}
    try:
        returned = State.load(CONFIG, loaded)
    except Exception:   # noqa: BLE001 - a corrupt file must not stop the app
        return {}
    raw = returned if isinstance(returned, dict) else loaded
    if not isinstance(raw, dict):
        return {}
    networks = {}
    for name in raw:
        networks[name] = stored_entry(raw[name])
    return networks


def stored_entry(entry):
    """One stored value as (key, security).

    A bare string is what earlier versions wrote; infer its type rather than
    dropping the network. Anything else is treated as a missing key, so a
    hand-edited config cannot stop the app listing the rest.
    """
    if isinstance(entry, dict):
        key = entry.get("key", "")
        security = entry.get("security", SEC_UNKNOWN)
    else:
        key = entry
        security = None
    if not isinstance(key, str):
        key = ""
    if security is None:
        security = SEC_OPEN if not key else SEC_UNKNOWN
    if not isinstance(security, int):
        security = SEC_UNKNOWN
    return (key, security)


def save_network(ssid_bytes, key_bytes, security):
    # modify() merges. save() would replace the object and drop every other
    # network. Called once, after a transfer verifies -- never per frame.
    State.modify(CONFIG, {escape_ssid(ssid_bytes): {
        "key": key_bytes.decode("utf-8"), "security": security}})


# --- this badge's own network -----------------------------------------------
# `secrets` is the only place a key exists in readable form: the CYW43 driver's
# config() has no getter for a station PSK, so the network a badge is currently
# on cannot be recovered -- only the one it was provisioned with.


def this_badge_credential():
    """This badge's own network as (ssid, key, security), or None.

    Imported here and guarded so a badge with neither /secrets.py nor
    /system/secrets.py loses the feature instead of failing to launch. Not
    secrets.require(), which reports a missing key by calling fatal_error().
    """
    try:
        import secrets
    except ImportError:
        return None
    ssid = getattr(secrets, "WIFI_SSID", "") or ""
    key = getattr(secrets, "WIFI_PASSWORD", "") or ""
    if not ssid:
        return None                 # an unprovisioned badge: both are ""
    # secrets.py records no security type, so infer one: no password means an
    # open network, and with a password we genuinely do not know which kind.
    security = SEC_OPEN if not key else SEC_UNKNOWN
    # Through the same gate a received credential goes through, so a key too
    # short for its network type is rejected here rather than being saved and
    # then refused at the far end.
    return valid_credential(credential_fields(ssid.encode("utf-8"), key,
                                              security))


def seed_own_network(networks):
    """Add this badge's own network to `networks` and the config file, once.

    A name already in the file wins: it may have been received from another
    badge or corrected by hand, and overwriting it every launch would undo that
    and cost a flash write to do it.
    """
    credential = this_badge_credential()
    if credential is None:
        return False
    ssid, key, security = credential
    name = escape_ssid(ssid)
    if name in networks:
        return False
    save_network(ssid, key, security)       # the one flash write this can do
    networks[name] = (key.decode("utf-8"), security)    # known to decode
    return True


# --- light sampling ---------------------------------------------------------

def sample_light():
    # Median of three immediate reads. light_level() is a raw ADC read, so this
    # costs almost nothing and takes the edge off single-sample noise.
    a = badge.light_level()
    b = badge.light_level()
    c = badge.light_level()
    if a > b:
        a, b = b, a
    if b > c:
        b = c if c > a else a
    return b


def probe_receiver():
    # The probe's listener: a data-phase slot receiver on the 50 ms rung, with
    # the dark mirror run's levels and a v2 frame. What it decides is thrown
    # away; only what a pass of it costs is kept.
    levels = Levels()
    levels.fit(32, 64, 112)
    clock = SlotClock()
    clock.start(time.ticks_ms(), FAST_INDEX)
    return SlotReceiver(clock, levels, 17)


def probe_listener():
    # The probe's transfer: a continuous Follower in the data phase on the
    # backlight's starting rung, listening for a block, with the dark mirror
    # run's levels. Built before the step that times it.
    transfer = Transfer(False, frame_ms=lambda: 17,
                        schemes=(SCHEME_FIELDS, SCHEME_BACKLIGHT),
                        continuous=True)
    levels = Levels()
    levels.fit(32, 64, 112)
    transfer.levels = levels
    transfer.scheme = SCHEME_BACKLIGHT
    now = time.ticks_ms()
    transfer.now = now
    transfer.start_data(now, BACKLIGHT_INDEX)
    return transfer


# --- drawing ----------------------------------------------------------------

# The light-level schemes this badge may transmit with; see LEVEL_SCHEMES in
# protocol.py. Both: calibration measures both, and a continuous badge takes
# the backlight whenever it calibrated -- 32 ms slots against pixels' 80 --
# falling back to fields when its middle step does not hold, as it would on
# a sensor that follows the backlight's PWM. Fields alone is
# (SCHEME_FIELDS,), and works against a peer on either setting.
SIGNALLING = (SCHEME_FIELDS, SCHEME_BACKLIGHT)

FIELD_COLOR = {}        # scheme -> {symbol: pen}
FIELD_BACKLIGHT = {}    # scheme -> {symbol: display.backlight() value}


# A transmitted field's "black" and "white" are the panel's full range, not
# the palette's: on the Tufty `color.black` and `color.white` are tinted --
# rgb(20, 30, 40) and rgb(222, 238, 214), from the TUFTY=1 build -- which
# gives away part of the swing the peer's sensor sees. Menus and text keep the
# palette.
FIELD_BLACK = (0, 0, 0)
FIELD_WHITE = (255, 255, 255)


def field_pen(pixels):
    if pixels == "white":
        pixels = FIELD_WHITE
    elif pixels == "black":
        pixels = FIELD_BLACK
    return color.rgb(pixels[0], pixels[1], pixels[2])


def field_colors():
    # Every scheme, once, so that switching mid-transfer allocates nothing.
    # One pen per pixel value, shared: show_full() compares pens by identity,
    # so symbols with the same pixels -- all three of the backlight scheme's
    # -- change only the backlight and never refill the screen.
    pens = {}
    for scheme in LEVEL_SCHEMES:
        levels = LEVEL_SCHEMES[scheme]
        FIELD_COLOR[scheme] = {}
        FIELD_BACKLIGHT[scheme] = {}
        for symbol in levels:
            pixels, light = levels[symbol]
            if pixels not in pens:
                pens[pixels] = field_pen(pixels)
            FIELD_COLOR[scheme][symbol] = pens[pixels]
            FIELD_BACKLIGHT[scheme][symbol] = light


field_colors()


def fill(pen):
    # clear() fills with the current pen, so the pen goes first.
    screen.pen = pen
    screen.clear()


# The alignment constants exist, but an injected global lives in `builtins` and
# not in the app's namespace -- so `globals().get("CENTER")` reads None whether
# the badge has it or not, and so does getattr(builtins, ...). Naming it and
# catching NameError is the only probe that works.
#
# `overflow=ELLIPSES` is deliberately unused: it truncates by WORD, so a
# one-line box holding a long hyphenated SSID comes out as "..." and nothing
# else. fit() truncates by character and keeps the start of the name.
try:
    #         (left, top)   (left, middle)  (centre, top)  (centre, middle)
    _ALIGN = ((LEFT, TOP), (LEFT, MIDDLE), (CENTER, TOP), (CENTER, MIDDLE))
    _HAS_ALIGN = True
except NameError:
    _ALIGN = None
    _HAS_ALIGN = False


def body_box(top=0):
    """The area between the title and the help line, as (x, y, w, h)."""
    return (MARGIN, MARGIN + top, screen.width - 2 * MARGIN,
            screen.height - 2 * MARGIN - LINE_HEIGHT - top)


def band_width():
    """How wide each band is: a quarter of the screen, each edge."""
    return screen.width // 4


def signal_boxes():
    """Dev Mode's handshake bands, as (x, y, w, h) tuples.

    The protocol transmits on the whole screen now (see show_field()); only
    Dev Mode's handshake still uses bands, so its status can sit between them.
    Hard to the screen edges: the sensor sits a millimetre outside them, so the
    outermost pixels are the ones that couple best.
    """
    band = band_width()
    return ((0, 0, band, screen.height),
            (screen.width - band, 0, band, screen.height))


def text_column(top=0, bottom=0):
    """The middle, between the two bands and inside its margins.

    Only Dev Mode's handshake screen uses this; menus have the whole width.
    """
    band = band_width()
    return (band + MARGIN, MARGIN + top,
            screen.width - 2 * band - 2 * MARGIN,
            screen.height - 2 * MARGIN - top - bottom)


# --- signalling: the whole screen, at LORES ---------------------------------
# While a field is transmitted -- a transfer, or one of Dev Mode's tests -- the
# whole screen is the field, at LORES, and nothing else is drawn. Measured on
# the badge (v2 firmware, BW-1.27.0, 60 Hz panel): one HIRES fill costs
# 11.4 ms, run() does one before update() is even called, and a frame that
# drew two bands and a column of text took 50 ms -- three refreshes. At LORES
# a fill is a quarter of the pixels. run()'s own clear is switched off
# (default_clear None) and the field is drawn only when it changes, since the
# framebuffer keeps it: a frame that changes nothing costs the protocol's own
# work and the present. See "Screen as a transmitter" in the protocol.
SIGNAL_MODE = LORES | VSYNC
TEXT_MODE = HIRES | VSYNC

# What is on screen while signalling: the pen and the backlight, or None to
# force the next show to draw.
_SHOWN = [None, None]


def begin_signalling():
    badge.mode(SIGNAL_MODE)
    badge.default_clear = None
    # Neither the pixels nor the backlight are known after a mode change and
    # whatever set the backlight since: both are drawn afresh.
    _SHOWN[0] = None
    _SHOWN[1] = None


def end_signalling():
    """Back to HIRES text screens. The caller restores the backlight."""
    badge.mode(TEXT_MODE)
    badge.default_clear = color.black
    _SHOWN[0] = None
    _SHOWN[1] = None


def measure_refresh():
    """The panel's refresh period, in whole ms, from presents with vsync on.

    Two back to back, timed after one to line up: 17 on the badge's v2, 11 on
    v3. The protocol's pixel timing is in refreshes now that the transfer
    loop no longer presents once a frame."""
    display.update()
    start = time.ticks_us()
    display.update()
    display.update()
    return max(1, (time.ticks_diff(time.ticks_us(), start) + 1000) // 2000)


def show_full(pen, backlight):
    """The whole screen in `pen` at `backlight`, each set only if it changed.

    The backlight scheme's symbols share one pen, so a change between them is
    a display.backlight() call and no fill: the framebuffer already holds the
    pixels, as run()'s clear is off while signalling.
    """
    if _SHOWN[1] != backlight:
        display.backlight(backlight)
        _SHOWN[1] = backlight
    if _SHOWN[0] is not pen:
        fill(pen)
        _SHOWN[0] = pen


def show_backlight(backlight):
    """Only the backlight, which needs no present: the PWM duty is live at
    once. The pixels stay as show_full() last drew them."""
    display.backlight(backlight)
    _SHOWN[1] = backlight


def show_field(field, scheme=SCHEME_FIELDS):
    """Transmit `field`, in `scheme`, on the whole screen."""
    show_full(FIELD_COLOR[scheme].get(field, color.black),
              FIELD_BACKLIGHT[scheme].get(field, 1.0))


# --- text layout ------------------------------------------------------------
# The app breaks text into lines itself rather than leaving it to
# screen.text(text, rect): that wraps and CLIPS to the rect, silently dropping
# lines that do not fit, and the badge's firmware is older than the source the
# wrapping was read from, so whether it wraps at all is not something to lean
# on. Lines are handed over already broken with "\n" and already short enough.
#
# A space is counted at the wider of its two widths. measure_text() gives one
# a third of the font's glyph width (3 px in `smart`); wrapped layout gives one
# a third of the font's height (5.3 px). A line measured the narrow way can be
# wrapped again by the firmware -- which is how a one-line "fit" string came to
# lose its second half.
_SPACE = [0]


def space_width():
    if not _SPACE[0]:
        one = screen.measure_text("A")
        both = screen.measure_text("A A")[0] - 2 * one[0]
        _SPACE[0] = max(both, (one[1] + 2) // 3, 1)
    return _SPACE[0]


def text_width(text):
    """Pixel width of one line of `text`, spaces counted conservatively."""
    words = str(text).split(" ")
    width = (len(words) - 1) * space_width()
    for word in words:
        if word:
            width += screen.measure_text(word)[0]
    return width


def wrap_lines(text, width):
    """`text` broken into lines no wider than `width`, at spaces where it can.

    Runs of spaces are kept: the help lines use them as gaps, and the firmware
    draws every one. A line break swallows the spaces it falls on. A word wider
    than the box on its own -- an SSID with no spaces -- is broken between
    characters rather than left to be clipped.
    """
    lines = []
    space = space_width()
    for hard in str(text).split("\n"):
        line = ""
        used = 0
        gap = 0                 # spaces waiting to go in front of the next word
        for word in hard.split(" "):
            if not word:
                gap += 1
                continue
            if line:
                gap += 1        # the space that split() consumed
            w = screen.measure_text(word)[0]
            while w > width and len(word) > 1:
                # As many characters as fit, on a line of their own.
                cut = len(word) - 1
                while cut > 1 and screen.measure_text(word[:cut])[0] > width:
                    cut -= 1
                if line:
                    lines.append(line)
                lines.append(word[:cut])
                line, used, gap = "", 0, 0
                word = word[cut:]
                w = screen.measure_text(word)[0]
            if line and used + gap * space + w > width:
                lines.append(line)
                line, used, gap = "", 0, 0
            if line:
                line += " " * gap + word
                used += gap * space + w
            else:
                line = " " * gap + word if not lines and gap else word
                used = (gap * space if line != word else 0) + w
            gap = 0
        lines.append(line)
    return lines


# draw_text() is called every frame, so a layout is kept rather than redone.
_LAYOUTS = {}


def layout_text(text, width, height):
    """`text` as "\n"-joined lines that fit (width, height), "..." if cut."""
    key = (text, width, height)
    laid = _LAYOUTS.get(key)
    if laid is not None:
        return laid
    rows = max(1, height // LINE_HEIGHT)
    lines = wrap_lines(text, width)
    if len(lines) > rows:
        lines = lines[:rows - 1] + [fit(" ".join(lines[rows - 1:]) + " ...",
                                        width)]
    laid = "\n".join(lines)
    if len(_LAYOUTS) >= 48:
        _LAYOUTS.clear()
    _LAYOUTS[key] = laid
    return laid


def fit(text, width):
    """Truncate to `width` pixels with an ellipsis, one character at a time."""
    text = str(text)
    if text_width(text) <= width:
        return text
    if text.endswith(" ..."):
        text = text[:-4]
    while text and text_width(text + "...") > width:
        text = text[:-1]
    return text.rstrip() + "..." if text else text


def draw_text(text, box, center=False, middle=False):
    """Draw `text` inside `box`, an (x, y, w, h) tuple."""
    x, y, width, height = box
    # Broken into lines that fit, here, whichever path draws them: see
    # layout_text(). A box one line high gets one line, cut with "...".
    text = layout_text(text, width, height)
    if _HAS_ALIGN:
        screen.text(text, rect(x, y, width, height),
                    align=_ALIGN[(2 if center else 0) + (1 if middle else 0)])
        return
    # No align: place each line ourselves, centred if asked.
    lines = text.split("\n")
    if middle:
        y += max(0, (height - len(lines) * LINE_HEIGHT) // 2)
    for line in lines:
        lx = x
        if center:
            lx += max(0, (width - text_width(line)) // 2)
        screen.text(line, lx, y)
        y += LINE_HEIGHT


def draw_alignment(text, title=None):
    """A vertical line down the middle, and `text` on the half away from the
    light sensor -- which sits beside the RIGHT edge, so the left half.

    The other badge's edge is held on the line, putting its screen over this
    one's sensor rather than its bezel beside it. See optical-coupling.md.
    """
    fill(color.black)
    middle = screen.width // 2
    screen.pen = color.white
    screen.rectangle(middle - ALIGN_LINE // 2, 0, ALIGN_LINE, screen.height)
    width = middle - ALIGN_LINE // 2 - 2 * MARGIN
    top = MARGIN
    if title:
        draw_text(title, (MARGIN, top, width, LINE_HEIGHT), center=True)
        top += 2 * LINE_HEIGHT
    draw_text(text, (MARGIN, top, width, screen.height - top - MARGIN),
              center=True, middle=True)


def draw_help(text):
    screen.pen = color.grey
    draw_text(text, (MARGIN, screen.height - LINE_HEIGHT - MARGIN // 2,
                     screen.width - 2 * MARGIN, LINE_HEIGHT))


def draw_title(text, reserve=0):
    """The title, centred. `reserve` keeps that many pixels clear at each end,
    for a page number on the right, without moving the centre."""
    screen.pen = color.white
    draw_text(text, (MARGIN + reserve, MARGIN,
                     screen.width - 2 * MARGIN - 2 * reserve, LINE_HEIGHT),
              center=True)


def draw_centered(text, pen=None):
    screen.pen = pen or color.white
    draw_text(text, body_box(), center=True, middle=True)


def menu_top():
    """Where a list's first row goes: one line below the title."""
    return MARGIN + LINE_HEIGHT + MARGIN


def menu_rows():
    """How many rows fit between the title and the help line: 12 at 320x240.

    Read from the screen every time, since badge.mode() can change its size.
    """
    help_top = screen.height - LINE_HEIGHT - MARGIN // 2
    return max(1, (help_top - menu_top()) // LINE_HEIGHT)


# "n/N" strings for paged titles, built once per distinct page rather than per
# frame.
_PAGE_LABELS = {}


def page_label(page, pages):
    key = (page, pages)
    label = _PAGE_LABELS.get(key)
    if label is None:
        label = "%d/%d" % (page + 1, pages)
        _PAGE_LABELS[key] = label
    return label


def draw_menu(title, items, cursor, help_text, empty="Nothing here"):
    """A list, paged when it has more rows than fit.

    The page is derived from the cursor, so moving one row past a page edge
    shows the adjacent page, and wrapping end <-> start shows the last or
    first page, with nothing extra to keep in step.
    """
    fill(color.black)
    count = len(items)
    rows = menu_rows()
    pages = (count + rows - 1) // rows
    page = cursor // rows if count else 0
    if pages > 1:
        label = page_label(page, pages)
        label_width = screen.measure_text(label)[0]
        draw_title(title, reserve=label_width + MARGIN)
        draw_text(label, (screen.width - MARGIN - label_width, MARGIN,
                          label_width, LINE_HEIGHT))
    else:
        draw_title(title)
    if not items:
        draw_centered(empty)
    else:
        y = menu_top()
        width = screen.width - 2 * MARGIN
        first = page * rows
        # A range rather than a slice, so drawing a page copies nothing.
        for i in range(first, min(first + rows, count)):
            screen.pen = color.white if i == cursor else color.smoke
            draw_text(("> " if i == cursor else "  ") + items[i],
                      (MARGIN, y, width, LINE_HEIGHT))
            y += LINE_HEIGHT
    draw_help(help_text)


def move_cursor(cursor, count):
    # Up and down move one row and wrap at both ends. Paging follows from the
    # cursor in draw_menu(), so it needs nothing here.
    if count == 0:
        return 0
    if badge.pressed(BUTTON_UP):
        cursor = (cursor - 1) % count
    if badge.pressed(BUTTON_DOWN):
        cursor = (cursor + 1) % count
    return cursor


# Progress goes to the rear LEDs, because during a transfer both screens face
# away from their users.
# Readings one update() may take while a transfer runs; 0 is no limit. The
# listen loop otherwise returns to run() only to present a change of pixels
# or when the transfer ends. A test stepping one field a frame sets 1.
TRANSFER_PASSES = 0
# Readings between button polls in it: about 7 ms on the badge.
POLL_PASSES = 16


def transfer_lights(transfer):
    # Both screens face away from their users, so the case lights say what is
    # happening.
    caselights_for("searching" if transfer.phase in (PHASE_ANNOUNCE, PHASE_PRESENT)
                   else "busy")


def caselights_for(state):
    if state == "searching":
        phase = (badge.ticks // 500) % 2
        badge.caselights(0.2 if phase else 0.0)
    elif state == "busy":
        badge.caselights(0.5)
    elif state == "error":
        phase = (badge.ticks // 120) % 2
        badge.caselights(1.0 if phase else 0.0)
    elif state == "attention":
        # A measurement screen waiting for the user, who is holding the badge
        # screen-first against a mirror or another badge and cannot see it.
        # Slower than the error blink, which means a transfer failed.
        phase = (badge.ticks // 250) % 2
        badge.caselights(1.0 if phase else 0.0)
    else:
        badge.caselights(0.0)


def restore_panel():
    """HIRES, backlight back to normal and case lights off, leaving a
    measuring screen."""
    end_signalling()
    display.backlight(BACKLIGHT_NORMAL)
    badge.caselights(0.0)


# --- screens ----------------------------------------------------------------

MAIN = "main"
PICK_SHARE = "pick_share"
TRANSFER = "transfer"
RESULT = "result"
CONNECT_LIST = "connect_list"
CONNECTING = "connecting"
CONNECT_RESULT = "connect_result"
ALIGN = "align"                 # the line to hold the other badge's edge on
ALIGN_TEXT = "Align edge of badge with line. A to continue"
MENU_ITEMS = ("Share", "Receive", "Connect")
DEV_MODE = "Dev Mode"           # appended to the main menu by C; never saved

DEV = "dev"
DEV_ITEMS = ("measure screen w/ mirror", "measure screen w/ 2nd badge",
             "probe frame timing", "measure room flicker",
             "display measurements")
TOOL_INDEX = {TOOL_MIRROR: 0, TOOL_SECOND_BADGE: 1, TOOL_PROBE: 2,
              TOOL_FLICKER: 3}
DISPLAY_INDEX = 4
DEV_ROLE = "dev_role"           # 2nd badge: who goes first
DEV_PROMPT = "dev_prompt"       # Dark room / Bright room
DEV_MEASURE = "dev_measure"     # the 3 s delay, then one condition's run
DEV_SAVED = "dev_saved"         # Done
DEV_DISPLAY = "dev_display"     # display measurements
DEV_PROBE_PROMPT = "dev_probe_prompt"   # probe frame timing: what it does
DEV_PROBE = "dev_probe"                 # probe frame timing: running
DEV_FLICKER = "dev_flicker"             # measure room flicker
FLICKER_TITLE = "Room flicker"
FLICKER_HELP = "A - Measure   B - Save & exit"

# --- dev mode ----------------------------------------------------------------
# /state/measurements.json, one object per tool. Written once, when a run ends.
MEASUREMENTS = "measurements"
MEASURE_DELAY_MS = 3000         # after A, so the hand pressing it can get clear

CONDITION_TITLES = {"dark": "Dark room", "lit": "Bright room"}
CONDITION_LABELS = {"dark": "Dark", "lit": "Bright"}     # "Dark 4/11: flicker"
PROMPTS = {
    (TOOL_MIRROR, "dark"): "Turn the lights off. Hold the badge screen-first "
                           "against a mirror.",
    (TOOL_MIRROR, "lit"): "Turn the lights on. Hold the badge against the "
                          "mirror again.",
    (TOOL_SECOND_BADGE, "dark"): ALIGN_TEXT,
    (TOOL_SECOND_BADGE, "lit"): ALIGN_TEXT,
}
PHASE_TEXT = {
    PHASE_NOISE: "Hold still",          # the handshake's noise is measured
    PHASE_CALLING: "Calling the other badge",
    PHASE_ANSWERING: "Answering the other badge",
    PHASE_SENDING: "Sending",
    PHASE_MEASURING: "Measuring the other badge",
}

# A run's pixels are "black", "white" or an (r, g, b) tuple from its schedule.
# Each distinct one becomes a pen once, so a frame looks one up rather than
# building a colour.
_PIXEL_PENS = {}


def pixels_pen(pixels):
    pen = _PIXEL_PENS.get(pixels)
    if pen is None:
        pen = field_pen(pixels)
        _PIXEL_PENS[pixels] = pen
    return pen


# A display-measurements line too wide for the screen continues on the rows
# below it, indented by this much so they do not read as lines of their own.
HANG = 2 * MARGIN


def wrap_value(line, width):
    """`line` as (text, continued) rows: the first `width` wide, the rest
    `width - HANG` and drawn that far in. Broken at spaces where it can be,
    and between characters where a run has none -- nothing is cut."""
    rows = wrap_lines(line, width)
    if len(rows) == 1:
        return [(line, False)]
    # wrap_lines() keeps the text's own spacing, so its first row is a prefix.
    first = rows[0]
    rest = line[len(first):].lstrip(" ")
    out = [(first, False)]
    for row in wrap_lines(rest, width - HANG):
        out.append((row, True))
    return out


def fit_tail(text, width):
    """Shorten `text` to `width` pixels from the FRONT, keeping its end.

    For a JSON path, whose last parts are the ones that say which section this
    is: "...dark.symbol_errors.fields.50" rather than "second_badge.dark.sy...".
    """
    if screen.measure_text(text)[0] <= width:
        return text
    start = 0
    while start < len(text) and \
            screen.measure_text("..." + text[start:])[0] > width:
        start += 1
    return "..." + text[start:]


def write_measurements(data):
    """Write the whole measurements file, raising OSError if it cannot.

    Not State.save(): it catches OSError and returns quietly whenever /state
    exists, so a failed write would read as a saved one. A run is minutes of
    holding a badge still -- saying it was saved when it was not is the worst
    answer this screen can give.
    """
    try:
        os.stat("/state")
    except OSError:
        os.mkdir("/state")
    with open("/state/%s.json" % MEASUREMENTS, "w") as handle:
        handle.write(json.dumps(data))


# Reads of the temperature sensor averaged into one figure: one read is
# noisy to about a degree.
TEMP_READS = 16


def core_temp_reader():
    """The chip's temperature sensor as a callable giving degrees C, or None.

    MicroPython's rp2 port names the sensor's ADC channel ADC.CORE_TEMP. It is
    channel 4 on an RP2350A and 8 on an RP2350B, where 4 is an ordinary pin
    that would read without complaint, so no number is guessed: a firmware
    without the name records that it has no sensor.
    """
    try:
        import machine
        adc = machine.ADC(machine.ADC.CORE_TEMP)
    except Exception:   # noqa: BLE001 - ImportError, AttributeError, ValueError
        return None

    def read():
        total = 0
        for _ in range(TEMP_READS):
            total += adc.read_u16()
        return core_temp_c(total / TEMP_READS)
    return read


def firmware_info():
    """What says which firmware this is: the build, and whether it can set
    the panel's frame rate (added 2026-06-30, with the 90 Hz default)."""
    info = {}
    try:
        info["version"] = os.uname().version
    except Exception:   # noqa: BLE001 - informational only
        pass
    info["set_framerate"] = getattr(display, "set_framerate", None) is not None
    return info


def load_measurements():
    """The measurements file as a dict, {} when there is none or it is unreadable.

    The same dance as load_networks(): State.load() returns a bool and fills
    the dict it was handed.
    """
    loaded = {}
    try:
        returned = State.load(MEASUREMENTS, loaded)
    except Exception:   # noqa: BLE001 - a corrupt file must not stop the app
        return {}
    data = returned if isinstance(returned, dict) else loaded
    return data if isinstance(data, dict) else {}


class App:
    def __init__(self):
        self.screen = MAIN
        self.cursor = 0
        # A list, not MENU_ITEMS itself: C appends Dev Mode for this run only.
        self.menu_items = list(MENU_ITEMS)
        self.networks = load_networks()
        # Once, at launch. Not in refresh_networks(), which runs every time a
        # list is opened: seeding there would re-check secrets for no reason.
        seed_own_network(self.networks)
        self.names = sorted(self.networks)
        self.labels = []
        self.relabel()
        self.transfer = None
        self.error = None
        self.message = ""
        self.connect_name = None        # as stored and shown: escaped
        self.connect_ssid = None        # as the radio wants it
        self.connect_key = None
        self.connect_started = 0
        # The transfer the alignment screen will start, and where it was
        # opened from, for B.
        self.align_leader = False
        self.align_payload = None
        self.align_from = MAIN
        self.status_readable = True
        self.retry_target = None
        # Dev Mode: the run in progress and what it has measured so far.
        self.measure_tool = None
        self.measure_first = None       # 2nd badge only: True to go first
        self.condition = "dark"
        self.prompt_text = ""
        self.run = None
        self.run_begun = False
        self.signalling = False         # LORES and full screen, during a test
        self.delay_until = 0
        self.dark_result = None
        self.saved_ok = False
        self.status_a = ""
        self.status_b = None
        self.status_c = None
        self.status_phase = None
        self.status_number = -1
        self.status_no_answer = False
        # measure room flicker: its capture buffers and the results so far.
        self.flicker = None
        self.flicker_results = []
        # probe frame timing: the probe, the run its frames measure with, and
        # the slot receiver its reads step times.
        self.probe = None
        self.probe_run = None
        self.probe_receiver = None
        self.probe_listener = None
        # display measurements: (title, label, label width, lines) per page.
        self.pages = []
        self.page = 0

    # -- main menu

    def do_main(self):
        self.cursor = move_cursor(self.cursor, len(self.menu_items))
        if badge.pressed(BUTTON_C) and DEV_MODE not in self.menu_items:
            self.menu_items.append(DEV_MODE)
            self.cursor = len(self.menu_items) - 1
        draw_menu("Share WiFi", self.menu_items, self.cursor,
                  "Up/Down - Move Selection   A - Select")
        caselights_for("idle")
        if badge.pressed(BUTTON_A):
            choice = self.menu_items[self.cursor]
            if choice == "Share":
                self.refresh_networks()
                self.cursor = 0
                self.screen = PICK_SHARE
            elif choice == "Receive":
                self.to_align(leader=False)
            elif choice == DEV_MODE:
                self.cursor = 0
                self.screen = DEV
            else:
                self.refresh_networks()
                self.cursor = 0
                self.screen = CONNECT_LIST

    def refresh_networks(self):
        self.networks = load_networks()
        self.names = sorted(self.networks)
        self.relabel()

    # Built when the list changes, never per frame: a menu is drawn every frame
    # and a string per row in the draw path is a per-frame allocation.
    def relabel(self):
        self.labels = []
        for name in self.names:
            security = self.networks[name][1]
            if security == SEC_OPEN:
                self.labels.append(name + "  (Open)")
            elif security == SEC_UNKNOWN:
                self.labels.append(name)
            else:
                self.labels.append("%s  (%s)" % (name, security_name(security)))

    # -- choose which credential to share

    def do_pick_share(self):
        self.cursor = move_cursor(self.cursor, len(self.names))
        draw_menu("Share which network?", self.labels, self.cursor,
                  "A - Share   B - Back", empty="No Networks")
        caselights_for("idle")
        if badge.pressed(BUTTON_B):
            self.to_main()
        elif badge.pressed(BUTTON_A) and self.names:
            name = self.names[self.cursor]
            key, security = self.networks[name]
            payload = credential_fields(unescape_ssid(name), key, security)
            self.to_align(leader=True, payload=payload)

    # -- lining the badges up, before every transfer

    def to_align(self, leader, payload=None):
        """The alignment screen; A on it starts the transfer these describe."""
        self.align_leader = leader
        self.align_payload = payload
        self.align_from = self.screen
        self.screen = ALIGN

    def do_align(self):
        draw_alignment(ALIGN_TEXT)
        caselights_for("idle")
        if badge.pressed(BUTTON_A):
            self.begin_transfer(self.align_leader, self.align_payload)
        elif badge.pressed(BUTTON_B):
            # Back where it came from: the network list, the main screen, or
            # the failed transfer's result -- which offers the retry again.
            if self.align_from == PICK_SHARE:
                self.screen = PICK_SHARE
            elif self.align_from == RESULT:
                self.screen = RESULT
            else:
                self.to_main()

    # -- the transfer itself

    def begin_transfer(self, leader, payload=None):
        begin_signalling()
        # Black, then the refresh timed against it, so nothing stale flashes.
        fill(FIELD_COLOR[SCHEME_FIELDS][LOW])
        refresh = measure_refresh()
        self.transfer = Transfer(leader, payload, frame_ms=lambda: refresh,
                                 schemes=SIGNALLING, continuous=True)
        self.transfer.started = time.ticks_ms()
        self.transfer.phase_started = self.transfer.started
        self.retry_target = (leader, payload)
        gc.collect()        # now, rather than part way through a symbol
        self.screen = TRANSFER

    def do_transfer(self):
        """The transfer's listen loop: a reading every pass, nothing presented.

        Each pass reads the sensor once and steps the protocol, which pools
        the readings. A change of backlight is made at once -- it needs no
        present. Only a change of pixels returns to run(), whose present shows
        it; that blocks reading for up to a refresh and a write, so the
        backlight scheme, whose pixels never change, is never blind. The
        buttons are polled every POLL_PASSES, which also keeps badge.ticks
        moving. See "Reading continuously" in the protocol.
        """
        transfer = self.transfer
        # B cancels, checked on entry -- run() has just polled -- and in the
        # loop.
        if badge.pressed(BUTTON_B):
            transfer.fail(ERR_CANCELLED)
        light_level = badge.light_level
        ticks_ms = time.ticks_ms
        ticks_us = time.ticks_us
        limit = TRANSFER_PASSES
        passes = 0
        while True:
            # The microseconds let the protocol take the room's mains
            # flicker off the reading.
            field = transfer.step(ticks_ms(), light_level(), ticks_us())
            if transfer.phase == PHASE_DONE:
                self.finish_transfer(transfer)
                return
            scheme = transfer.scheme
            backlight = FIELD_BACKLIGHT[scheme].get(field, 1.0)
            if _SHOWN[1] != backlight:
                show_backlight(backlight)
            pen = FIELD_COLOR[scheme].get(field, color.black)
            if _SHOWN[0] is not pen:
                # The whole screen is the field and nothing else is drawn.
                fill(pen)
                _SHOWN[0] = pen
                transfer_lights(transfer)
                return
            passes += 1
            if passes % POLL_PASSES == 0 or passes == limit:
                badge.poll()
                if badge.pressed(BUTTON_B):
                    transfer.fail(ERR_CANCELLED)
                transfer_lights(transfer)
            if passes == limit:
                return

    def finish_transfer(self, transfer):
        badge.caselights(0.0)
        # The single exit from the transfer screen, so the only place the
        # backlight has to go back down and HIRES come back.
        end_signalling()
        display.backlight(BACKLIGHT_NORMAL)
        if transfer.result != OK:
            self.error = transfer.result
            self.screen = RESULT
            return
        if transfer.leader:
            self.message = "Shared."
            self.error = None
            self.screen = RESULT
            return

        parsed = valid_credential(parse_payload(transfer.received))
        if parsed is None:
            self.error = ERR_DECODE
            self.screen = RESULT
            return
        ssid, key, security = parsed
        save_network(ssid, key, security)       # once, after verification
        self.refresh_networks()
        name = escape_ssid(ssid)
        self.cursor = self.names.index(name) if name in self.names else 0
        self.start_connect(name)

    def do_result(self):
        fill(color.black)
        if self.error:
            draw_title("Transfer failed")
            draw_centered(ERROR_TEXT.get(self.error, self.error), color.smoke)
            draw_help("A - Main Menu   B - Retry")
            caselights_for("error")
            if badge.pressed(BUTTON_B) and self.retry_target:
                leader, payload = self.retry_target
                self.to_align(leader, payload)
            elif badge.pressed(BUTTON_A):
                self.to_main()
        else:
            draw_title("Done")
            draw_centered(self.message)
            draw_help("Any button - Main Menu")
            caselights_for("idle")
            if badge.pressed():
                self.to_main()

    # -- connect

    def do_connect_list(self):
        self.cursor = move_cursor(self.cursor, len(self.names))
        draw_menu("Connect", self.labels, self.cursor,
                  "A - Connect   B - Back", empty="No Networks")
        caselights_for("idle")
        if badge.pressed(BUTTON_B):
            self.to_main()
        elif badge.pressed(BUTTON_A) and self.names:
            self.start_connect(self.names[self.cursor])

    def start_connect(self, name):
        self.connect_name = name
        # Cleared up front, so a refusal cannot be reported against the next
        # network.
        self.error = None
        security = self.networks.get(name, ("", SEC_UNKNOWN))[1]
        # Only joining is refused: a WEP credential is still worth carrying and
        # handing to something that can use it.
        if security == SEC_WEP and not wep_supported():
            self.error = WEP_UNSUPPORTED
            self.message = "%s\n\n%s" % (name, WEP_UNSUPPORTED)
            self.screen = CONNECT_RESULT
            return
        self.connect_ssid = radio_ssid(name)
        # An empty key is what selects CYW43_AUTH_OPEN. wifi.connect() never
        # forwards a security= kwarg, so open and WPA2-mixed are the only modes
        # reachable from here.
        self.connect_key = self.networks.get(name, ("", SEC_UNKNOWN))[0]
        self.connect_started = time.ticks_ms()
        # wifi.connect() keeps a module-level WLAN and ignores the SSID once
        # it exists, so without this a second network reports the first one's
        # result. It also restores the retry budget, which wifi spends by
        # calling fatal_error() -- the reset-the-badge error screen.
        wifi.disconnect()
        self.screen = CONNECTING

    def do_connecting(self):
        # connect() is non-blocking: calling it repeatedly both starts the
        # attempt and moves it along, so the app keeps drawing while it waits.
        wifi.connect(self.connect_ssid, self.connect_key)
        # Read once a frame and used twice: on a badge whose firmware raises
        # from status(), asking again is a second exception for nothing.
        code, description, readable = wifi_status()
        self.status_readable = readable

        fill(color.black)
        draw_title("Connecting")
        dots = "." * (1 + (badge.ticks // 400) % 3)
        draw_centered(self.connect_name + "\n" + dots)
        draw_help("B - Back")
        caselights_for("busy")
        if not readable:
            screen.pen = color.smoke
            draw_text(STATUS_UNREADABLE,
                      (MARGIN, screen.height - LINE_HEIGHT * 2 - MARGIN,
                       screen.width - 2 * MARGIN, LINE_HEIGHT))

        if wifi.is_connected():
            self.message = "Connected to\n" + self.connect_name
            self.error = None
            self.screen = CONNECT_RESULT
            return
        if badge.pressed(BUTTON_B):
            self.screen = CONNECT_LIST
            return

        timed_out = time.ticks_diff(time.ticks_ms(),
                                    self.connect_started) > CONNECT_TIMEOUT_MS

        # The firmware's own rule, from modules/common/wifi.py: 0 Idle,
        # 1 Connecting, 2 Connected, 3 Got IP are all "keep going", and anything
        # else is an error (-1 failed, -2 no such AP, -3 wrong password).
        # wifi.connect() retries internally, so the timeout here is the backstop
        # for a radio that sits on a neutral status forever.
        if code not in WIFI_PENDING or timed_out:
            if timed_out and code >= 0:
                # Name the real obstacle when there was one: a badge whose
                # firmware cannot report a status has no way to say *why* a
                # connection failed, and "Timed out" alone sends you looking
                # in the wrong place.
                description = ("Timed out\n" + STATUS_UNREADABLE if not readable
                               else "Timed out")
            self.message = "%s\n\n%s (%d)" % (self.connect_name, description, code)
            self.error = description
            self.screen = CONNECT_RESULT

    def do_connect_result(self):
        fill(color.black)
        caselights_for("idle")
        if self.error:
            draw_title("Connection failed")
            draw_centered(self.message, color.smoke)
            draw_help("A - Retry   B - Back")
            if badge.pressed(BUTTON_A):
                self.start_connect(self.connect_name)
            elif badge.pressed(BUTTON_B):
                self.screen = CONNECT_LIST
        else:
            draw_title("Connected")
            draw_centered(self.message)
            draw_help("Any button - Back")
            if badge.pressed():
                self.screen = CONNECT_LIST

    # -- dev mode

    def do_dev(self):
        self.cursor = move_cursor(self.cursor, len(DEV_ITEMS))
        draw_menu("Dev Mode", DEV_ITEMS, self.cursor, "A - Select   B - Back")
        caselights_for("idle")
        if badge.pressed(BUTTON_B):
            self.to_main()
        elif badge.pressed(BUTTON_A):
            if self.cursor == 0:
                self.begin_tool(TOOL_MIRROR, None)
            elif self.cursor == 1:
                self.screen = DEV_ROLE
            elif self.cursor == 2:
                self.screen = DEV_PROBE_PROMPT
            elif self.cursor == 3:
                self.begin_flicker()
            else:
                self.open_measurements()

    def to_dev(self, index):
        self.cursor = index
        self.screen = DEV

    def tool_index(self):
        return TOOL_INDEX.get(self.measure_tool, 0)

    # -- measure screen w/ 2nd badge: the role

    def do_dev_role(self):
        # No way back from here: B is "I go second". B at the prompt that
        # follows is the way out.
        fill(color.black)
        draw_title("Who goes first?")
        draw_centered("One badge must choose each.")
        draw_help("A - I go first   B - I go second")
        caselights_for("attention")    # waiting on the user
        if badge.pressed(BUTTON_A):
            self.begin_tool(TOOL_SECOND_BADGE, True)
        elif badge.pressed(BUTTON_B):
            self.begin_tool(TOOL_SECOND_BADGE, False)

    # -- both tools: prompt, delay, run, twice; then save

    def begin_tool(self, tool, first):
        self.measure_tool = tool
        self.measure_first = first
        self.dark_result = None
        self.prompt_for("dark")

    def prompt_for(self, condition):
        self.condition = condition
        # Looked up once here: a tuple key built per frame is an allocation.
        self.prompt_text = PROMPTS[(self.measure_tool, condition)]
        self.screen = DEV_PROMPT

    def do_dev_prompt(self):
        if self.measure_tool == TOOL_SECOND_BADGE:
            # The hold a transfer uses, so the swing measured is its swing.
            draw_alignment(self.prompt_text, CONDITION_TITLES[self.condition])
        else:
            fill(color.black)
            draw_title(CONDITION_TITLES[self.condition])
            draw_centered(self.prompt_text)
            draw_help("A - Start   B - Cancel")
        caselights_for("attention")    # waiting on the user
        if badge.pressed(BUTTON_B):
            self.cancel_measure()
        elif badge.pressed(BUTTON_A):
            self.start_condition()

    def start_condition(self):
        # Built here, during the delay, so the allocation of its buffers is not
        # in a measured frame. One run object per condition.
        temp = core_temp_reader()
        if self.measure_tool == TOOL_MIRROR:
            self.run = MirrorRun(badge.light_level, time.ticks_us, temp)
        else:
            self.run = SecondBadgeRun(self.measure_first, badge.light_level,
                                      time.ticks_us, temp)
        self.run_begun = False
        self.delay_until = time.ticks_add(time.ticks_ms(), MEASURE_DELAY_MS)
        self.status_a = "Starting"
        self.status_b = CONDITION_TITLES[self.condition]
        self.status_c = None
        self.status_number = -1
        self.screen = DEV_MEASURE

    def do_dev_measure(self):
        run = self.run
        if badge.pressed(BUTTON_B):
            self.cancel_measure()
            return

        now = time.ticks_ms()
        if not self.run_begun:
            if time.ticks_diff(now, self.delay_until) < 0:
                fill(color.black)
                self.draw_measure_column()
                badge.caselights(0.0)
                return
            run.begin(now)
            self.run_begun = True

        run.frame(now, badge.ticks_delta)
        if run.done:
            fill(color.black)
            self.end_condition(run)
            return

        self.update_measure_status(run)
        if run.test_name is not None:
            # A test is running: the whole screen is its field, with no text.
            # More of the panel reaches the sensor -- through a mirror, or
            # across to the other badge however it is held -- and nothing
            # drawn over the field moves the level being measured. A badge
            # measuring the other's tests shows its own field, black, the
            # same way, so its text cannot reflect back into its own sensor.
            # B still cancels; the status returns between tests.
            # Drawn as the protocol draws a transfer -- LORES, run()'s clear
            # off, redrawn only when the field changes -- so frame_timing and
            # the settle captures measure the frames a transfer will have.
            # The case lights were switched off before the first test and
            # nothing between tests turns them on, so they are left alone.
            if not self.signalling:
                begin_signalling()
                self.signalling = True
            # A backlight step's change is made inside its capture, below:
            # setting it here would put the edge before the first read.
            if not run.change_due:
                show_full(pixels_pen(run.pixels), run.backlight)
        else:
            # Start delay, noise second, handshake, waiting: HIRES, two edge
            # bands, with what is happening in the column between.
            if self.signalling:
                end_signalling()
                self.signalling = False
            screen.pen = color.black
            screen.clear()
            screen.pen = pixels_pen(run.pixels)
            for x, y, width, height in signal_boxes():
                screen.rectangle(x, y, width, height)
            self.draw_measure_column()
            display.backlight(run.backlight)
            # Case lights stay off while measuring: in the dark-room run they
            # would light the room the ambient and black readings are
            # measuring. They flash only once a handshake has given up and the
            # screen is holding "No answer from the other badge" -- nothing is
            # being measured then.
            if self.status_no_answer:
                caselights_for("attention")
            else:
                badge.caselights(0.0)
        # Presentation timing. A settle capture has to read the panel the
        # moment the frame showing its new field is up, with times measured
        # from the present call. Under run(update) the firmware presents after
        # update() returns and then polls and clears before the next update()
        # -- about 30 ms on the first hardware run, longer than any edge, so
        # every rise had already crossed by the first read. On a frame with a
        # capture due the app presents it itself, here, and reads from the
        # moment display.update() returns; the firmware's own present that
        # follows shows the same frame again, a frame's cost 40 times a run.
        # display.update() itself is blind -- nothing reads while it writes
        # the panel -- so its duration goes to presented() and into the
        # result as blind_ms. presented() blocks 60-80 ms; measure.py leaves
        # the frame after it out of frame_timing.
        #
        # A backlight step is the exception: display.backlight() is live the
        # moment it is called, with no present to wait for, so the capture
        # runs around that call instead -- reading from just before it.
        if run.change_due:
            backlight = run.backlight
            run.changed(lambda: show_backlight(backlight))
        elif run.loopback_due:
            # The badge sends a symbol block on its own backlight and reads
            # its reflection, as a transfer's listen loop would: blocks for
            # the block. The pixels are white already.
            run.loopback(show_backlight)
        elif run.capture_due:
            present_us = time.ticks_us()
            display.update()
            run.presented(present_us, time.ticks_us())

    def update_measure_status(self, run):
        """The centre column's text, rebuilt only when the phase or test moves."""
        second = self.measure_tool == TOOL_SECOND_BADGE
        phase = run.phase if second else None
        no_answer = run.no_handshake if second else False
        if (phase == self.status_phase and no_answer == self.status_no_answer
                and run.test_number == self.status_number):
            return
        self.status_phase = phase
        self.status_no_answer = no_answer
        self.status_number = run.test_number
        if run.test_name is None:
            progress = CONDITION_TITLES[self.condition]
        else:
            progress = "%s %d/%d: %s" % (CONDITION_LABELS[self.condition],
                                         run.test_number, run.test_count,
                                         run.test_name)
        if second:
            self.status_a = PHASE_TEXT.get(phase, "")
            self.status_b = progress
            self.status_c = ("No answer from the other badge" if no_answer
                             else None)
        else:
            self.status_a = progress
            self.status_b = None
            self.status_c = None

    def draw_measure_column(self):
        """Static text between the bands: it changes only between tests."""
        left, top, width, height = text_column()
        screen.pen = color.white
        draw_text(self.status_a, (left, top, width, LINE_HEIGHT * 2),
                  center=True)
        y = top + LINE_HEIGHT * 3
        if self.status_b:
            screen.pen = color.smoke
            draw_text(self.status_b, (left, y, width, LINE_HEIGHT * 2),
                      center=True)
        y += LINE_HEIGHT * 2 + MARGIN
        screen.pen = color.grey
        if self.status_c:
            draw_text(self.status_c, (left, y, width, LINE_HEIGHT * 2),
                      center=True)
        draw_text("B - Cancel",
                  (left, screen.height - LINE_HEIGHT - MARGIN, width,
                   LINE_HEIGHT))

    def end_condition(self, run):
        result = run.results()
        self.run = None
        restore_panel()
        self.signalling = False
        if self.condition == "dark":
            self.dark_result = result
            self.prompt_for("lit")
        else:
            self.save_measurements(self.dark_result, result)
            self.dark_result = None
            self.screen = DEV_SAVED
        gc.collect()

    def save_measurements(self, dark, lit):
        role = None
        if self.measure_tool == TOOL_SECOND_BADGE:
            role = "first" if self.measure_first else "second"
        entry = tool_result(dark, lit, role)
        # Merged here so the other tool's object is kept as it was, then
        # written in one go -- the one write a run makes.
        merged = merge_results(load_measurements(), self.measure_tool, entry)
        try:
            write_measurements(merged)
            self.saved_ok = True
        except OSError:
            self.saved_ok = False
        except Exception:   # noqa: BLE001 - run() would turn it into a reset
            self.saved_ok = False

    def cancel_measure(self):
        # Nothing is saved: the dark result, if any, is dropped with the run.
        self.run = None
        self.dark_result = None
        restore_panel()
        self.signalling = False
        self.to_dev(self.tool_index())
        gc.collect()

    def do_dev_saved(self):
        fill(color.black)
        draw_title("Done")
        draw_centered("Measurements saved" if self.saved_ok
                      else "Could not save measurements")
        draw_help("Any button - Back")
        caselights_for("attention")    # waiting on the user
        if badge.pressed():
            self.to_dev(self.tool_index())

    # -- display measurements

    def open_measurements(self):
        """Build every page once, here, rather than per frame."""
        data = load_measurements()
        self.pages = []
        self.page = 0
        built = []
        width = screen.width - 2 * MARGIN
        if data:
            try:
                built = pages(data, menu_rows(),
                              lambda line: wrap_value(line, width))
            except Exception:   # noqa: BLE001 - a hand-edited file
                built = []
        count = len(built)
        for i in range(count):
            title, lines = built[i]
            label = None
            label_width = 0
            if count > 1:
                label = page_label(i, count)
                label_width = screen.measure_text(label)[0]
            reserve = label_width + MARGIN if label else 0
            title = fit_tail(title, screen.width - 2 * MARGIN - 2 * reserve)
            self.pages.append((title, label, label_width, lines))
        self.screen = DEV_DISPLAY

    def do_dev_display(self):
        fill(color.black)
        caselights_for("idle")
        if not self.pages:
            draw_title("Measurements")
            draw_centered("No measurements saved")
            draw_help("Any button - Back")
            if badge.pressed():
                self.to_dev(DISPLAY_INDEX)
            return
        if (badge.pressed(BUTTON_A) or badge.pressed(BUTTON_B)
                or badge.pressed(BUTTON_C)):
            self.to_dev(DISPLAY_INDEX)
            return
        count = len(self.pages)
        if badge.pressed(BUTTON_UP):
            self.page = (self.page - 1) % count
        if badge.pressed(BUTTON_DOWN):
            self.page = (self.page + 1) % count
        title, label, label_width, lines = self.pages[self.page]
        if label:
            draw_title(title, reserve=label_width + MARGIN)
            draw_text(label, (screen.width - MARGIN - label_width, MARGIN,
                              label_width, LINE_HEIGHT))
        else:
            draw_title(title)
        screen.pen = color.white
        y = menu_top()
        width = screen.width - 2 * MARGIN
        for text, continued in lines:
            indent = HANG if continued else 0
            draw_text(text, (MARGIN + indent, y, width - indent, LINE_HEIGHT))
            y += LINE_HEIGHT
        draw_help("Up/Down - Page   A/B/C - Exit")

    # -- probe frame timing

    def do_dev_probe_prompt(self):
        fill(color.black)
        draw_title("Probe frame timing")
        draw_centered("Times the display, each part of a measuring frame "
                      "and how fast the sensor reads. Takes about 25 s; "
                      "the screen flashes and goes dark.")
        draw_help("A - Start   B - Back")
        caselights_for("idle")
        if badge.pressed(BUTTON_B):
            self.to_dev(TOOL_INDEX[TOOL_PROBE])
        elif badge.pressed(BUTTON_A):
            self.start_probe()

    def start_probe(self):
        gc.collect()
        self.measure_tool = TOOL_PROBE
        self.condition = "dark"
        # The frames signalling and status screens draw, in parts. `field`
        # is a transfer's or a test's frame -- LORES, run()'s clear off --
        # drawing its field every frame, so it is the frame that changes the
        # field: one that does not skips the fill. `status` is Dev Mode's
        # HIRES handshake screen, bands and text.
        field = (("field", self.probe_field), ("measure", self.probe_measure))
        status = (("bands", self.probe_bands),
                  ("column", self.draw_measure_column),
                  ("lights", self.probe_lights),
                  ("measure", self.probe_measure))
        hooks = Hooks(
            present=display.update,
            set_vsync=getattr(display, "set_vsync", None),
            poll=badge.poll, clear=badge.clear,
            screens=(("field", self.probe_start_field, field),
                     ("status", self.probe_start_status, status)),
            cancelled=self.probe_cancelled,
            collect=gc.collect, enable=gc.enable, disable=gc.disable,
            mem_alloc=getattr(gc, "mem_alloc", None),
            mem_free=getattr(gc, "mem_free", None),
            ticks_us=time.ticks_us,
            reads=(("loop", self.probe_idle), ("read", badge.light_level),
                   ("sample", sample_light), ("listen", self.probe_listen),
                   ("transfer", self.probe_transfer)),
            temp=core_temp_reader())
        self.probe_receiver = probe_receiver()
        self.probe_listener = probe_listener()
        self.probe_passes = 0
        self.probe = FrameProbe(hooks, firmware_info())
        self.screen = DEV_PROBE

    def probe_start_field(self):
        begin_signalling()
        self.probe_new_run()

    def probe_start_status(self):
        end_signalling()
        self.probe_new_run()

    def probe_new_run(self):
        # A fresh run per screen, built here so its buffers are not allocated
        # inside a timed frame.
        self.probe_run = MirrorRun(badge.light_level, time.ticks_us)
        self.probe_run.begin(time.ticks_ms())
        self.status_number = -1

    def probe_cancelled(self):
        return badge.pressed(BUTTON_B)

    def probe_field(self):
        display.backlight(self.probe_run.backlight)
        fill(pixels_pen(self.probe_run.pixels))

    def probe_bands(self):
        screen.pen = color.black
        screen.clear()
        screen.pen = pixels_pen(self.probe_run.pixels)
        for x, y, width, height in signal_boxes():
            screen.rectangle(x, y, width, height)

    def probe_lights(self):
        display.backlight(self.probe_run.backlight)
        badge.caselights(0.0)

    def probe_idle(self):
        pass

    def probe_listen(self):
        # One pass of a listener that never presents: the clock, a sample,
        # the protocol's slot receiver, and the poll and B check that keep
        # the buttons alive without run().
        self.probe_receiver.feed(time.ticks_ms(), sample_light())
        badge.poll()
        badge.pressed(BUTTON_B)

    def probe_transfer(self):
        # One pass of the transfer's own listen loop, as do_transfer() runs
        # it: a reading into a continuous Transfer listening in the data phase
        # on the backlight, and the buttons every POLL_PASSES. What it hears
        # is thrown away, and so are the budgets that would end it -- nobody
        # is transmitting.
        listener = self.probe_listener
        listener.step(time.ticks_ms(), badge.light_level(), time.ticks_us())
        listener.idle_slots = 0
        listener.stall_slots = 0
        self.probe_passes += 1
        if self.probe_passes % POLL_PASSES == 0:
            badge.poll()
            badge.pressed(BUTTON_B)

    def probe_measure(self):
        run = self.probe_run
        run.frame(time.ticks_ms(), badge.ticks_delta)
        self.update_measure_status(run)

    def do_dev_probe(self):
        probe = self.probe
        if badge.pressed(BUTTON_B) or probe.cancelled:
            self.end_probe()
            self.to_dev(TOOL_INDEX[TOOL_PROBE])
            return
        if probe.done:
            self.save_probe()
            return
        fill(color.black)
        draw_title("Probe frame timing")
        draw_centered("%s %d/%d" % (STEP_TITLES[probe.step_name],
                                    probe.index + 1, len(STEPS)))
        draw_help("B - Cancel")
        caselights_for("idle")
        # Shown before the step, which blocks for up to 12 s.
        display.update()
        probe.step()
        restore_panel()

    # -- measure room flicker
    # Its own tool: A takes a measurement, as many as wanted; B saves them all
    # and leaves. See "measure room flicker" in the spec.

    def begin_flicker(self):
        # The capture's bins, allocated here, once, not in the measuring frame.
        self.measure_tool = TOOL_FLICKER
        self.flicker = Capture()
        self.flicker_results = []
        self.screen = DEV_FLICKER

    def do_dev_flicker(self):
        results = self.flicker_results
        fill(color.black)
        draw_title(FLICKER_TITLE)
        if results:
            draw_centered("%d measured. Last: %s" % (len(results),
                                                    summary(results[-1])))
        else:
            draw_centered("Hold the badge where it will be used, sensor "
                          "towards the room. A measures for a quarter of a "
                          "second.")
        draw_help(FLICKER_HELP)
        caselights_for("idle")
        if badge.pressed(BUTTON_A):
            self.measure_flicker()
        elif badge.pressed(BUTTON_B):
            self.save_flicker()

    def measure_flicker(self):
        # Shown first: the capture and its analysis block for a second or two.
        fill(color.black)
        draw_title(FLICKER_TITLE)
        draw_centered("Measuring - hold still")
        display.update()
        temp = core_temp_reader()
        # The badge's own backlight off while it reads: its PWM must not be
        # what is measured. The screen goes dark for the quarter second.
        display.backlight(0.0)
        self.flicker.take(badge.light_level, time.ticks_us)
        display.backlight(BACKLIGHT_NORMAL)
        result = analyse(self.flicker)
        if temp is not None:
            result["temp_c"] = temp()
        self.flicker_results.append(result)

    def save_flicker(self):
        results = self.flicker_results
        self.flicker = None
        self.flicker_results = []
        if not results:
            self.to_dev(TOOL_INDEX[TOOL_FLICKER])
            return
        # Numbered from 1, so the display shows them in the order taken.
        numbered = {}
        for i in range(len(results)):
            numbered[str(i + 1)] = results[i]
        merged = merge_results(load_measurements(), TOOL_FLICKER, numbered)
        try:
            write_measurements(merged)
            self.saved_ok = True
        except Exception:   # noqa: BLE001 - OSError, or run() would reset
            self.saved_ok = False
        self.screen = DEV_SAVED

    def save_probe(self):
        results = self.probe.results()
        self.end_probe()
        merged = merge_results(load_measurements(), TOOL_PROBE, results)
        try:
            write_measurements(merged)
            self.saved_ok = True
        except Exception:   # noqa: BLE001 - OSError, or run() would reset
            self.saved_ok = False
        self.screen = DEV_SAVED

    def end_probe(self):
        self.probe = None
        self.probe_run = None
        self.probe_receiver = None
        self.probe_listener = None
        restore_panel()
        gc.collect()

    def to_main(self):
        self.screen = MAIN
        self.cursor = 0
        self.error = None
        self.transfer = None

    def frame(self):
        if self.screen == MAIN:
            self.do_main()
        elif self.screen == PICK_SHARE:
            self.do_pick_share()
        elif self.screen == ALIGN:
            self.do_align()
        elif self.screen == TRANSFER:
            self.do_transfer()
        elif self.screen == RESULT:
            self.do_result()
        elif self.screen == CONNECT_LIST:
            self.do_connect_list()
        elif self.screen == CONNECTING:
            self.do_connecting()
        elif self.screen == CONNECT_RESULT:
            self.do_connect_result()
        elif self.screen == DEV:
            self.do_dev()
        elif self.screen == DEV_ROLE:
            self.do_dev_role()
        elif self.screen == DEV_PROMPT:
            self.do_dev_prompt()
        elif self.screen == DEV_MEASURE:
            self.do_dev_measure()
        elif self.screen == DEV_SAVED:
            self.do_dev_saved()
        elif self.screen == DEV_DISPLAY:
            self.do_dev_display()
        elif self.screen == DEV_PROBE_PROMPT:
            self.do_dev_probe_prompt()
        elif self.screen == DEV_PROBE:
            self.do_dev_probe()
        elif self.screen == DEV_FLICKER:
            self.do_dev_flicker()


app = App()


def update():
    app.frame()


# run() clears the screen, calls update(), presents the frame and polls the
# buttons. Never call badge.update() from inside it.
run(update)
