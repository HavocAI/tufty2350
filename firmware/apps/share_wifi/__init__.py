# Share WiFi credentials between badges over the backlight and light sensor.
#
# Protocol specification: .agents/optical_data_transfer/spec.md
#
# A per-frame state machine, but for the transfer loop, which runs a whole
# transfer inside one update(): it transmits on the backlight alone, behind
# a white screen drawn once, and reads the peer through the DMA ring
# (sections 5 and 6 of the spec). Every screen with text is HIRES.

import gc
import json
import os
import time
from array import array

# Not an injected global: a firmware module (modules/common/wifi.py).
import wifi

# The launcher puts the app's folder on sys.path, so siblings are top-level.
from protocol import (
    BEACON_LOW_BACKLIGHT, LOW_BACKLIGHT, ERR_CANCELLED, ERR_CONTRAST, ERR_RING,
    ERR_CRC, ERR_DECODE, ERR_LOST_PEER, ERR_NO_PEER, LEVEL_SCHEMES, OK,
    PHASE_ANNOUNCE, PHASE_DONE, PHASE_LISTEN, Transfer,
)
import reader as readers

# The dev link's modules load when it opens, never at launch: a transfer's
# heap is smaller without them [measured-v2].

badge.mode(HIRES | VSYNC)

CONNECT_TIMEOUT_MS = 30000   # a UI deadline, not part of the wire protocol
WIFI_PENDING = (0, 1, 2, 3)  # idle/connecting/connected/got-ip; anything else failed

# What the firmware sets at init; there is no getter to read first.
BACKLIGHT_NORMAL = 230 / 255

ERROR_TEXT = {
    ERR_NO_PEER: "No other badge found.\nPoint them at each other.",
    ERR_CONTRAST: "Can't see the other screen.\nMove closer, dim the lights.",
    ERR_LOST_PEER: "Lost the other badge.",
    ERR_CRC: "Too many errors.\nHold the badges still.",
    ERR_DECODE: "Received data was invalid.",
    ERR_CANCELLED: "Cancelled.",
    ERR_RING: "The light sensor stopped\nreading. Relaunch the app.",
}

NO_RING_TEXT = "This badge can't read its light\nsensor fast enough to transfer."
# ... nor send a fast block: no divider gives its clock the common period.
NO_FAST_TEXT = "This badge can't send\nfast blocks."

# --- layout -----------------------------------------------------------------

MARGIN = 6
LINE_HEIGHT = 16
ALIGN_LINE = 2                  # px: the alignment screen's middle line


# `font.<name>` on v3, `rom_font.<name>` on v2, probed by bare name; a
# missing font keeps the default.
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
# /state/share_wifi.json. SSIDs that are not valid UTF-8 are stored
# percent-escaped, so keys survive a JSON round trip.

CONFIG = "share_wifi"

# 802.11 and WPA limits: the caller's, not the protocol's.
SSID_MAX = 32
KEY_MIN = 8
KEY_MAX = 63
PSK_LEN = 64

# WEP keys: 5 or 13 characters, or 10 or 26 hex digits -- all outside WPA's.
WEP_LENGTHS = (5, 10, 13, 26)

# A credential's third field: MicroPython's generic auth numbering, NOT
# WLAN.scan()'s CYW43_AUTH_FLAG_* bitmask. 255 is a key of unrecorded type.
# The field exists because an empty key is otherwise ambiguous between "no
# password" and "password lost".
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


WEP_UNSUPPORTED = "WEP networks not supported"

# wifi.status() has raised NameError from inside wifi.py: an unreadable
# status reads as still connecting, since is_connected() decides success and
# CONNECT_TIMEOUT_MS failure.
STATUS_UNREADABLE = "Wi-Fi status unavailable"


def wifi_status():
    """(code, description, readable) from the firmware."""
    try:
        code, description = wifi.status()
    except Exception:   # noqa: BLE001 - it has raised NameError from inside wifi.py
        return 1, STATUS_UNREADABLE, False      # 1 is "Connecting"
    return code, description, True


def wep_supported():
    """Whether this firmware can join a WEP network. Probed: the cyw43
    driver refuses WEP before touching the radio, and network.WLAN exports
    no SEC_WEP. A firmware that gained it would export it -- and would then
    need a join through network.WLAN, since wifi.connect() passes no
    security=."""
    try:
        from network import WLAN
    except ImportError:
        return False
    return hasattr(WLAN, "SEC_WEP")


def valid_key(key, security):
    """Whether `key` is a usable key for a network of this type."""
    if security == SEC_OPEN:
        return len(key) == 0
    if security == SEC_ENTERPRISE:
        return False        # needs an identity and a certificate, not a key
    if len(key) == 0:
        return False
    if security == SEC_WEP:
        return len(key) in WEP_LENGTHS
    return KEY_MIN <= len(key) <= KEY_MAX or len(key) == PSK_LEN


def valid_credential(fields):
    """A received payload as (ssid, key, security), or None. Two fields:
    the type is inferred."""
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
    # A leading "%" marks a hex-escaped SSID, so one that starts with "%" is
    # escaped too, or "%41" would read back as b"A".
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
        return text.encode("utf-8")     # not ours: a wrong name, not a crash


def radio_ssid(name):
    """The SSID to hand the radio, from the name the config file is keyed by."""
    raw = unescape_ssid(name)
    try:
        return raw.decode("utf-8")
    except UnicodeError:
        return raw


def load_state(name):
    """`name`'s state file as a dict, {} when missing, unreadable or not a
    dict. State.load() fills the dict it is handed and returns a bool."""
    loaded = {}
    try:
        returned = State.load(name, loaded)
    except Exception:   # noqa: BLE001 - a corrupt file must not stop the app
        return {}
    data = returned if isinstance(returned, dict) else loaded
    return data if isinstance(data, dict) else {}


def load_networks():
    """Every saved network, as {escaped ssid: (key, security)}."""
    raw = load_state(CONFIG)
    networks = {}
    for name in raw:
        networks[name] = stored_entry(raw[name])
    return networks


def stored_entry(entry):
    """One stored value as (key, security). A bare string has its type
    inferred; anything else is a missing key, so a hand-edited config
    cannot stop the app listing the rest."""
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
    # modify() merges; save() would drop every other network.
    State.modify(CONFIG, {escape_ssid(ssid_bytes): {
        "key": key_bytes.decode("utf-8"), "security": security}})


# --- this badge's own network -----------------------------------------------
# `secrets` is the only readable copy of a key: the CYW43 driver's config()
# has no getter for a station PSK.


def this_badge_credential():
    """This badge's own network as (ssid, key, security), or None. Not
    secrets.require(), which reports a missing key with fatal_error()."""
    try:
        import secrets
    except ImportError:
        return None
    ssid = getattr(secrets, "WIFI_SSID", "") or ""
    key = getattr(secrets, "WIFI_PASSWORD", "") or ""
    if not ssid:
        return None
    security = SEC_OPEN if not key else SEC_UNKNOWN
    return valid_credential(credential_fields(ssid.encode("utf-8"), key,
                                              security))


def seed_own_network(networks):
    """Add this badge's own network to `networks` and the config file, once.
    A name already in the file wins: it may have been corrected by hand."""
    credential = this_badge_credential()
    if credential is None:
        return False
    ssid, key, security = credential
    name = escape_ssid(ssid)
    if name in networks:
        return False
    save_network(ssid, key, security)
    networks[name] = (key.decode("utf-8"), security)
    return True


# --- the reader ---------------------------------------------------------------
# The DMA ring (fastadc.Ring under reader.RingReader), made once a launch on
# the first transfer -- verify() takes ~0.4 s -- with the reason it was
# refused kept: a badge without it does not transfer.

_READERS = {}


def ring_reader():
    """The reader, or None; `ring_note()` says why not. Tried once."""
    if "ring" not in _READERS:
        _READERS["ring"] = None
        _READERS["note"] = None
        try:
            import fastadc
            problem = fastadc.check(badge.light_level)
            ring = None
            if problem is None:
                ring = fastadc.Ring()
                problem = ring.verify(readers)
            if problem is None:
                _READERS["ring"] = readers.RingReader(ring)
            else:
                _READERS["note"] = problem
        except Exception as error:     # noqa: BLE001 - every refusal is reported
            _READERS["note"] = "%s: %s" % (type(error).__name__, error)
    return _READERS["ring"]


def ring_note():
    ring_reader()
    return _READERS["note"]


# --- the fast block ----------------------------------------------------------
# A burst goes out through a DMA channel paced by the backlight's PWM wrap
# (fasttx.Pacer), at a period every clock reaches (fastblock.common_div());
# fastblock.Decoder decodes one. Made once a launch. A badge whose clock no
# divider reaches still receives, but cannot share.

_FAST = {}


def fast_module():
    """fastblock, or None if it does not import."""
    if "module" not in _FAST:
        try:
            import fastblock
            _FAST["module"] = fastblock
        except Exception:   # noqa: BLE001 - then no transfer
            _FAST["module"] = None
    return _FAST["module"]


def fast_pacer():
    """(pacer, div16, words) for sending bursts, or None: tried once."""
    if "pacer" not in _FAST:
        _FAST["pacer"] = None
        fb = fast_module()
        try:
            import machine
            import rp2
            import fasttx
            div16 = fb.common_div(machine.freq())[0] if fb else None
            if div16 is not None:
                most = 0
                for periods in fb.RUNGS:
                    symbols = fb.PREAMBLE_LEN + fb.TAIL + fb.stuffed_len(
                        fb.HEADER + fb.block_bytes(periods) + fb.CRC)
                    most = max(most, periods * symbols + 1)
                words = array("I", bytes(4 * most))
                _FAST["pacer"] = (fasttx.Pacer(machine.mem32, rp2.DMA()),
                                  div16, words)
        except Exception:   # noqa: BLE001 - then it cannot share
            _FAST["pacer"] = None
    return _FAST["pacer"]


def fast_for(leader):
    """The fast block's module for a transfer, or None if this badge cannot
    take part: a Leader needs the pacer too, a Follower only the decoder."""
    fb = fast_module()
    if fb is None or leader and fast_pacer() is None:
        return None
    return fb


# A burst's words are filled during the lead-in's low slots, where a pass
# held up changes nothing shown, and it is played from BURST_LEAD_US before
# its start, pump() waiting to the start.
BURST_LEAD_US = 2000


def fill_burst(burst):
    """The burst's CC words, once: low and high read from the firmware's
    own backlight curve, the first time inside an open pacer."""
    if _FAST.get("filled") is burst:
        return
    fb = fast_module()
    pacer, div16, words = fast_pacer()
    if "low" not in _FAST:
        with pacer.opened(div16):
            _FAST["low"] = pacer.word(pacer.cc_for(display.backlight,
                                                   LOW_BACKLIGHT))
            _FAST["high"] = pacer.word(pacer.cc_for(display.backlight, 1.0))
    _FAST["n"] = fb.fill_words(words, burst[1], burst[2], _FAST["low"],
                               _FAST["high"])
    _FAST["filled"] = burst


def play_burst(transfer, burst):
    """Leader: the burst, from its start, through the pacer; the ring reads
    through it, and the pacer puts DIV and CC back after."""
    fill_burst(burst)
    pacer, div16, words = fast_pacer()
    reader = transfer.reader
    reader.pump(burst[0])
    transfer.burst_started(reader.now_us())
    pacer.transmit(div16, words, _FAST["n"])


def on_exit():
    """HOME: the launcher calls this, then resets the badge. A raise here
    would skip the reset, so nothing escapes."""
    try:
        ring = _READERS.get("ring")
        if ring is not None:
            ring.stop()
    except Exception:   # noqa: BLE001 - HOME must still reset
        pass
    try:
        port = app.link_port
        if port is not None:
            port.close()
    except Exception:   # noqa: BLE001 - HOME must still reset
        pass
    try:
        sending = _FAST.get("pacer")
        if sending is not None:
            sending[0].release()
    except Exception:   # noqa: BLE001 - HOME must still reset
        pass


# --- drawing ----------------------------------------------------------------

# The panel's full white: `color.white` is tinted on the Tufty, which would
# dim what the peer sees. Made once; _SHOWN compares it by identity.
WHITE_PEN = color.rgb(255, 255, 255)


def fill(pen):
    # clear() fills with the current pen, so the pen goes first.
    screen.pen = pen
    screen.clear()


# An injected global lives in `builtins`, so only naming it finds it. Not
# overflow=ELLIPSES: it truncates by word, and a long SSID becomes "...".
try:
    #         (left, top)   (left, middle)  (centre, top)  (centre, middle)
    _ALIGN = ((LEFT, TOP), (LEFT, MIDDLE), (CENTER, TOP), (CENTER, MIDDLE))
    _HAS_ALIGN = True
except NameError:
    _ALIGN = None
    _HAS_ALIGN = False


def body_box():
    """The area between the title and the help line, as (x, y, w, h)."""
    return (MARGIN, MARGIN, screen.width - 2 * MARGIN,
            screen.height - 2 * MARGIN - LINE_HEIGHT)


# What is on screen: the pen and the backlight, or None to force the next
# show to draw.
_SHOWN = [None, None]


def show_backlight(backlight):
    """Only the backlight, which needs no present: the PWM duty is live."""
    display.backlight(backlight)
    _SHOWN[1] = backlight


# --- text layout ------------------------------------------------------------
# The app breaks lines itself: screen.text(text, rect) clips lines that do
# not fit, and whether v2 wraps at all is not to be leaned on. A space is
# counted at the wider of measure_text()'s and the wrapped layout's widths,
# or the firmware may wrap a line measured to fit.
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
    """`text` broken into lines no wider than `width`, at spaces where it
    can. Runs of spaces are kept (help lines use them as gaps); a break
    swallows the spaces it falls on; a word wider than the box is broken
    between characters."""
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


# draw_text() runs every frame, so a layout is kept rather than redone.
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
    text = layout_text(text, width, height)
    if _HAS_ALIGN:
        screen.text(text, rect(x, y, width, height),
                    align=_ALIGN[(2 if center else 0) + (1 if middle else 0)])
        return
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
    light sensor, which sits beside the RIGHT edge. The other badge's edge is
    held on the line, putting its screen over this one's sensor."""
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
    """The title, centred, `reserve` pixels kept clear at each end."""
    screen.pen = color.white
    draw_text(text, (MARGIN + reserve, MARGIN,
                     screen.width - 2 * MARGIN - 2 * reserve, LINE_HEIGHT),
              center=True)


def draw_centered(text, pen=None):
    screen.pen = pen or color.white
    draw_text(text, body_box(), center=True, middle=True)


def draw_screen(title, body, help_text=None, pen=None):
    """A text screen: title, centred body and, if given, the help line."""
    fill(color.black)
    draw_title(title)
    draw_centered(body, pen)
    if help_text:
        draw_help(help_text)


def menu_top():
    return MARGIN + LINE_HEIGHT + MARGIN


def menu_rows():
    """Rows between the title and the help line, read from the screen
    every time, since badge.mode() can change its size."""
    help_top = screen.height - LINE_HEIGHT - MARGIN // 2
    return max(1, (help_top - menu_top()) // LINE_HEIGHT)


# "n/N" strings for paged titles, built once per page rather than per frame.
_PAGE_LABELS = {}


def page_label(page, pages):
    key = (page, pages)
    label = _PAGE_LABELS.get(key)
    if label is None:
        label = "%d/%d" % (page + 1, pages)
        _PAGE_LABELS[key] = label
    return label


def draw_paged_title(title, label, label_width):
    """The title, with `label` -- "n/N", or None -- at the right."""
    if not label:
        draw_title(title)
        return
    draw_title(title, reserve=label_width + MARGIN)
    draw_text(label, (screen.width - MARGIN - label_width, MARGIN,
                      label_width, LINE_HEIGHT))


def draw_menu(title, items, cursor, help_text, empty="Nothing here"):
    """A list, paged when it has more rows than fit; the page follows from
    the cursor."""
    fill(color.black)
    count = len(items)
    rows = menu_rows()
    pages = (count + rows - 1) // rows
    page = cursor // rows if count else 0
    if pages > 1:
        label = page_label(page, pages)
        draw_paged_title(title, label, screen.measure_text(label)[0])
    else:
        draw_title(title)
    if not items:
        draw_centered(empty)
    else:
        y = menu_top()
        width = screen.width - 2 * MARGIN
        first = page * rows
        for i in range(first, min(first + rows, count)):
            screen.pen = color.white if i == cursor else color.smoke
            draw_text(("> " if i == cursor else "  ") + items[i],
                      (MARGIN, y, width, LINE_HEIGHT))
            y += LINE_HEIGHT
    draw_help(help_text)


def move_cursor(cursor, count):
    """Up and down move one row, wrapping at both ends."""
    if count == 0:
        return 0
    if badge.pressed(BUTTON_UP):
        cursor = (cursor - 1) % count
    if badge.pressed(BUTTON_DOWN):
        cursor = (cursor + 1) % count
    return cursor


# Passes one update() may make while a transfer runs; 0 is no limit -- the
# loop returns to run() only when the transfer ends. A test sets 1.
TRANSFER_PASSES = 0
# How often the loop polls the buttons, which also keeps badge.ticks moving.
POLL_MS = 50


def transfer_lights(transfer):
    # Both screens face away from their users, so the case lights say what
    # is happening.
    caselights_for("searching" if transfer.phase in (PHASE_ANNOUNCE, PHASE_LISTEN)
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
    else:
        badge.caselights(0.0)


# --- the transfer screen -----------------------------------------------------
# White, drawn once and presented before the reader starts; every symbol is
# then a display.backlight() call, and run()'s clear is off until it ends.

def begin_transfer_screen():
    badge.default_clear = None
    # The backlight off first, so the white is written unseen.
    show_backlight(BEACON_LOW_BACKLIGHT)
    fill(WHITE_PEN)
    _SHOWN[0] = WHITE_PEN
    display.update()


def show_level(backlight):
    """A symbol: the backlight, set only if it changed."""
    if _SHOWN[1] != backlight:
        show_backlight(backlight)


def restore_panel():
    """Text screens again: run()'s clear back, the backlight back to normal
    and the case lights off."""
    badge.default_clear = color.black
    _SHOWN[0] = None
    show_backlight(BACKLIGHT_NORMAL)
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
DEV_HOLD_MS = 5000              # how long C is held to reveal it

DEV = "dev"
DEV_ITEMS = ("Display Saved Measurements", "Clear Saved Measurements",
             "USB Dev Link")
DISPLAY_INDEX = 0
CLEAR_INDEX = 1
LINK_INDEX = 2
DEV_DISPLAY = "dev_display"
DEV_DONE = "dev_done"
DEV_RUNNING = "Starts in 3 s. Hold B to cancel between steps."
MEASURE_DELAY_MS = 3000
DEV_LINK = "dev_link"
LINK_TITLE = "USB dev link"
LINK_ROOT = "/devtests"         # subtools, on the writable LittleFS
CLEARED = "Measurements and dev tools cleared"

# /state/measurements.json: one object per tool, written by the dev link's
# subtools (Api.save()).
MEASUREMENTS = "measurements"
MEASUREMENTS_PATH = "/state/%s.json" % MEASUREMENTS

# A display line too wide for the screen continues on the rows below,
# indented by this much.
HANG = 2 * MARGIN


def wrap_value(line, width):
    """`line` as (text, continued) rows: the first `width` wide, the rest
    `width - HANG` and drawn that far in. Nothing is cut."""
    rows = wrap_lines(line, width)
    if len(rows) == 1:
        return [(line, False)]
    first = rows[0]     # wrap_lines() keeps the spacing: a prefix
    rest = line[len(first):].lstrip(" ")
    out = [(first, False)]
    for row in wrap_lines(rest, width - HANG):
        out.append((row, True))
    return out


def fit_tail(text, width):
    """Shorten `text` to `width` pixels from the FRONT: a path's last parts
    say which section it is."""
    if screen.measure_text(text)[0] <= width:
        return text
    start = 0
    while start < len(text) and \
            screen.measure_text("..." + text[start:])[0] > width:
        start += 1
    return "..." + text[start:]


def ordered_keys(obj):
    """Numeric keys by value, then the rest alphabetically: the file comes
    back from JSON in no order."""
    numeric = []
    other = []
    for key in obj:
        try:
            numeric.append((float(key), key))
        except ValueError:
            other.append(key)
    numeric.sort()
    other.sort()
    return [k for _, k in numeric] + other


def sections(obj, path, out):
    """(title, lines) for every object holding values that are not
    objects, its own values ahead of its children's."""
    keys = ordered_keys(obj)
    lines = [key + ": " + json.dumps(obj[key]) for key in keys
             if not isinstance(obj[key], dict)]
    if lines:
        out.append((path or "measurements", lines))
    for key in keys:
        if isinstance(obj[key], dict):
            sections(obj[key], path + "." + key if path else key, out)
    return out


def measurement_pages(data, per, wrap):
    """Sections as screens of at most `per` rows, `wrap(line)` giving a
    line's rows; a line's rows stay on one page where they fit."""
    out = []
    for title, lines in sections(data, "", []):
        first = len(out)
        page = []
        for line in lines:
            rows = wrap(line)
            if page and len(page) + len(rows) > per:
                out.append((title, page))
                page = []
            for row in rows:
                if len(page) == per:
                    out.append((title, page))
                    page = []
                page.append(row)
        if page:
            out.append((title, page))
        for i in range(first + 1, len(out)):
            out[i] = (title + " (cont.)", out[i][1])
    return out


def write_measurements(data):
    """Write the whole measurements file, raising OSError if it cannot. Not
    State.save(), which hides a failed write."""
    try:
        os.stat("/state")
    except OSError:
        os.mkdir("/state")
    with open(MEASUREMENTS_PATH, "w") as handle:
        handle.write(json.dumps(data))


def save_measurement(tool, entry):
    """`entry` under `tool` in the measurements file -- merged key by key
    into a dict already there, so one condition's run keeps the other's --
    True if written."""
    data = load_state(MEASUREMENTS)
    old = data.get(tool)
    if isinstance(old, dict) and isinstance(entry, dict):
        old.update(entry)
        entry = old
    data[tool] = entry
    try:
        write_measurements(data)
        return True
    except Exception:   # noqa: BLE001 - OSError, or run() would reset
        return False


def delete_measurements():
    """True if the measurements file is gone. Not State.delete(), which
    hides a failure: whether the file is still there is the answer."""
    try:
        os.remove(MEASUREMENTS_PATH)
    except OSError:
        pass
    try:
        os.stat(MEASUREMENTS_PATH)
    except OSError:
        return True
    return False


# The RP2350's sensor: 27 C at 0.706 V, -1.721 mV/C; 16 reads averaged.
TEMP_READS = 16


def core_temp_reader():
    """The chip's temperature as a callable giving degrees C, or None. No
    channel number is guessed: ADC.CORE_TEMP is 4 on an RP2350A and 8 on a
    B, where 4 is an ordinary pin."""
    try:
        import machine
        adc = machine.ADC(machine.ADC.CORE_TEMP)
    except Exception:   # noqa: BLE001 - ImportError, AttributeError, ValueError
        return None

    def read():
        total = 0
        for _ in range(TEMP_READS):
            total += adc.read_u16()
        volts = total / TEMP_READS * 3.3 / 65535
        return round(27 - (volts - 0.706) / 0.001721, 1)
    return read


def tethered():
    """Whether USB power is present (VBUS), or None if unknown."""
    try:
        return bool(badge.usb_connected())
    except Exception:   # noqa: BLE001 - informational only
        return None


def format_exc(error):
    """An exception with its traceback, as text, for the dev link."""
    try:
        import io
        import sys
        out = io.StringIO()
        sys.print_exception(error, out)
        return out.getvalue()
    except Exception:   # noqa: BLE001 - CPython has no print_exception
        return "%s: %s" % (type(error).__name__, error)


def firmware_version():
    try:
        return os.uname().version
    except Exception:   # noqa: BLE001 - informational only
        return "unknown"


class LinkHooks:
    """The badge, as the dev link's server -- or a run Dev Mode starts
    itself, titled `title` -- is handed it."""

    def __init__(self, title=LINK_TITLE):
        self.title = title
        self.firmware = firmware_version()
        self.temp = core_temp_reader()
        self.light = badge.light_level
        self.ring = ring_reader
        self.ring_note = ring_note
        self.backlight = show_backlight
        self.present = display.update
        self.ticks_us = time.ticks_us
        self.ticks_ms = time.ticks_ms
        self.format_exc = format_exc
        self.collect = gc.collect
        self.save = save_measurement

    def usb_connected(self):
        return tethered() is not False

    def poll(self):
        badge.poll()

    def b_held(self):
        return badge.held(BUTTON_B)

    def status(self, text):
        draw_screen(self.title, text, "B - Cancel")
        display.update()

    def restore(self):
        restore_panel()


def clear_all():
    """The measurements file and every stored dev tool and library gone:
    True if nothing is left."""
    import devserver
    tools = devserver.clear_tools(LINK_ROOT)
    return delete_measurements() and tools


def link_builtins():
    """The link's instant commands: the measurements file, sent or cleared."""
    import devserver

    def measurements(_args):
        return load_state(MEASUREMENTS), None, ""

    def clear(_args):
        ok = clear_all()
        return None, ok, CLEARED if ok else "Could not clear"

    return {
        "measurements": devserver.Builtin(
            "measurements", "send the saved measurements file", measurements),
        "clear": devserver.Builtin(
            "clear", "delete the measurements file and the stored dev tools",
            clear),
    }


class App:
    def __init__(self):
        self.screen = MAIN
        self.cursor = 0
        # A list: C appends Dev Mode for this run only.
        self.menu_items = list(MENU_ITEMS)
        self.c_since = None             # when C went down, while held
        self.networks = load_networks()
        seed_own_network(self.networks)
        self.names = sorted(self.networks)
        self.relabel()
        self.transfer = None
        self.poll_at = 0
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
        self.retry_target = None
        # Dev Mode: Done's message; the display's pages, as (title, label,
        # label width, rows); the link's server and port while open, and
        # whether B counts on its screen yet.
        self.done_message = ""
        self.done_index = 0
        self.dev_items = list(DEV_ITEMS)
        self.dev_tools = []             # (label, stem, args) per tool item
        self.pages = []
        self.page = 0
        self.link = None
        self.link_port = None
        self.link_b_armed = True
        # What a host launching over the REPL asked for (autorun()), read
        # on the first frame: None until then, "" from the menu.
        self.autorun = None
        self.handlers = {
            MAIN: self.do_main, PICK_SHARE: self.do_pick_share,
            ALIGN: self.do_align, TRANSFER: self.do_transfer,
            RESULT: self.do_result, CONNECT_LIST: self.do_connect_list,
            CONNECTING: self.do_connecting,
            CONNECT_RESULT: self.do_connect_result, DEV: self.do_dev,
            DEV_DISPLAY: self.do_dev_display, DEV_DONE: self.do_dev_done,
            DEV_LINK: self.do_dev_link,
        }

    # -- main menu

    def dev_held(self):
        """True once C has been held DEV_HOLD_MS."""
        if not badge.held(BUTTON_C):
            self.c_since = None
            return False
        if self.c_since is None:
            self.c_since = badge.ticks
        return badge.ticks - self.c_since >= DEV_HOLD_MS

    def do_main(self):
        self.cursor = move_cursor(self.cursor, len(self.menu_items))
        if self.dev_held() and DEV_MODE not in self.menu_items:
            self.menu_items.append(DEV_MODE)
            self.cursor = len(self.menu_items) - 1
        draw_menu("Share WiFi", self.menu_items, self.cursor,
                  "Up/Down - Move   A - Select   B - Exit")
        caselights_for("idle")
        if badge.pressed(BUTTON_B):
            return True                 # leave the app
        if badge.pressed(BUTTON_A):
            choice = self.menu_items[self.cursor]
            if choice == "Receive":
                self.to_align(leader=False)
            elif choice == DEV_MODE:
                self.scan_tools()
                self.to_dev(0)
            else:
                self.refresh_networks()
                self.cursor = 0
                self.screen = PICK_SHARE if choice == "Share" else CONNECT_LIST
        return None

    def refresh_networks(self):
        self.networks = load_networks()
        self.names = sorted(self.networks)
        self.relabel()

    # Built when the list changes, never per frame.
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
            # the failed transfer's result, which offers the retry again.
            if self.align_from in (PICK_SHARE, RESULT):
                self.screen = self.align_from
            else:
                self.to_main()

    # -- the transfer itself

    def begin_transfer(self, leader, payload=None):
        self.retry_target = (leader, payload)
        begin_transfer_screen()
        reader = ring_reader()      # ~0.4 s, the first time a launch
        fast = fast_for(leader) if reader is not None else None
        if fast is None:
            restore_panel()
            self.error = ("%s\n(%s)" % (NO_RING_TEXT, ring_note())
                          if reader is None else NO_FAST_TEXT)
            self.screen = RESULT
            return
        gc.collect()        # now, rather than part way through a symbol
        reader.start()
        self.transfer = Transfer(leader, payload, reader, fast=fast)
        self.poll_at = 0
        self.screen = TRANSFER

    def do_transfer(self):
        """The transfer loop: wait for what is due, show the field, work.

        The field for now is shown before the work, so a change of
        backlight lands on time. Nothing is presented: the whole transfer
        runs in this one call, unless TRANSFER_PASSES says otherwise.
        Collections happen here, when the protocol asks, never inside its
        calls. See section 6 of the protocol spec.
        """
        transfer = self.transfer
        reader = transfer.reader
        if badge.pressed(BUTTON_B):
            transfer.fail(ERR_CANCELLED)
        limit = TRANSFER_PASSES
        passes = 0
        shown_field = shown_scheme = None
        while True:
            if transfer.collect_due:
                transfer.collect_due = False
                gc.collect()
            if transfer.phase == PHASE_DONE:
                self.finish_transfer(transfer)
                return
            reader.pump(transfer.due_us())
            t = reader.now_us()
            field = transfer.field_at(t)
            scheme = transfer.scheme
            if field != shown_field or scheme != shown_scheme:
                shown_field = field
                shown_scheme = scheme
                show_level(LEVEL_SCHEMES[scheme][field])
            transfer.work(t)
            burst = transfer.fast_burst
            if burst is not None:
                if t >= burst[0] - BURST_LEAD_US:
                    play_burst(transfer, burst)
                else:
                    fill_burst(burst)
            passes += 1
            if t >= self.poll_at or passes == limit:
                self.poll_at = t + POLL_MS * 1000
                badge.poll()
                if badge.pressed(BUTTON_B):
                    transfer.fail(ERR_CANCELLED)
                transfer_lights(transfer)
            if passes == limit:
                return

    def finish_transfer(self, transfer):
        # The single exit from the transfer screen.
        transfer.reader.stop()
        restore_panel()
        if transfer.result != OK:
            self.error = transfer.result
            self.screen = RESULT
            return
        if transfer.leader:
            self.message = "Shared."
            self.error = None
            self.screen = RESULT
            return
        parsed = valid_credential(transfer.received)
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
        if self.error:
            # The ring is tried once a launch: only a relaunch can help it.
            retry = not (self.error == ERR_RING
                         or self.error.startswith(NO_RING_TEXT)
                         or self.error == NO_FAST_TEXT)
            draw_screen("Transfer failed",
                        ERROR_TEXT.get(self.error, self.error),
                        "A - Main Menu   B - Retry" if retry
                        else "A - Main Menu", color.smoke)
            caselights_for("error")
            if retry and badge.pressed(BUTTON_B) and self.retry_target:
                leader, payload = self.retry_target
                self.to_align(leader, payload)
            elif badge.pressed(BUTTON_A):
                self.to_main()
        else:
            draw_screen("Done", self.message, "Any button - Main Menu")
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
        self.error = None
        security = self.networks.get(name, ("", SEC_UNKNOWN))[1]
        # Only joining is refused: a WEP credential is still worth carrying.
        if security == SEC_WEP and not wep_supported():
            self.error = WEP_UNSUPPORTED
            self.message = "%s\n\n%s" % (name, WEP_UNSUPPORTED)
            self.screen = CONNECT_RESULT
            return
        self.connect_ssid = radio_ssid(name)
        # An empty key selects CYW43_AUTH_OPEN.
        self.connect_key = self.networks.get(name, ("", SEC_UNKNOWN))[0]
        self.connect_started = time.ticks_ms()
        # wifi.connect() keeps its WLAN and ignores a new SSID once it
        # exists; disconnect() also restores the retry budget it spends by
        # calling fatal_error().
        wifi.disconnect()
        self.screen = CONNECTING

    def do_connecting(self):
        # Non-blocking: each call starts or moves the attempt along.
        wifi.connect(self.connect_ssid, self.connect_key)
        code, description, readable = wifi_status()

        dots = "." * (1 + (badge.ticks // 400) % 3)
        draw_screen("Connecting", self.connect_name + "\n" + dots, "B - Back")
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
        # The firmware's rule: 0-3 keep going, anything else is an error.
        if code not in WIFI_PENDING or timed_out:
            if timed_out and code >= 0:
                description = ("Timed out\n" + STATUS_UNREADABLE if not readable
                               else "Timed out")
            self.message = "%s\n\n%s (%d)" % (self.connect_name, description, code)
            self.error = description
            self.screen = CONNECT_RESULT

    def do_connect_result(self):
        caselights_for("idle")
        if self.error:
            draw_screen("Connection failed", self.message,
                        "A - Retry   B - Back", color.smoke)
            if badge.pressed(BUTTON_A):
                self.start_connect(self.connect_name)
            elif badge.pressed(BUTTON_B):
                self.screen = CONNECT_LIST
        else:
            draw_screen("Connected", self.message, "Any button - Back")
            if badge.pressed():
                self.screen = CONNECT_LIST

    # -- dev mode

    def to_dev(self, index):
        self.cursor = min(index, len(self.dev_items) - 1)
        self.screen = DEV

    def scan_tools(self):
        """Dev Mode's items: the three, then every stored dev tool's MENU
        entries (devserver.menu_entries())."""
        try:
            import devserver
            self.dev_tools = devserver.menu_entries(LINK_ROOT)
        except Exception:   # noqa: BLE001 - no tools, not a reset
            self.dev_tools = []
        self.dev_items = list(DEV_ITEMS) + [t[0] for t in self.dev_tools]

    def do_dev(self):
        self.cursor = move_cursor(self.cursor, len(self.dev_items))
        draw_menu("Dev Mode", self.dev_items, self.cursor,
                  "A - Select   B - Back")
        caselights_for("idle")
        if badge.pressed(BUTTON_B):
            self.to_main()
        elif badge.pressed(BUTTON_A):
            if self.cursor == DISPLAY_INDEX:
                self.open_measurements()
            elif self.cursor == CLEAR_INDEX:
                try:
                    ok = clear_all()
                except Exception:   # noqa: BLE001 - run() would reset
                    ok = False
                self.scan_tools()
                self.done(CLEARED if ok else "Could not clear", CLEAR_INDEX)
            elif self.cursor == LINK_INDEX:
                self.open_link()
            else:
                self.run_tool(self.cursor)

    def run_tool(self, index):
        """A stored dev tool's MENU entry, with no host: blocking, its
        result saved by the tool itself (api.save())."""
        import devserver
        label, stem, args = self.dev_tools[index - len(DEV_ITEMS)]
        # A wait first, so the hand that pressed A can get clear.
        draw_screen(label, DEV_RUNNING)
        badge.caselights(0.0)
        display.update()
        time.sleep_ms(MEASURE_DELAY_MS)
        outcome, value = devserver.run_local(LINK_ROOT, LinkHooks(label),
                                             stem, args)
        if outcome == "ok":
            message = "Done: see Display Saved Measurements"
        elif outcome == "cancelled":
            message = "Cancelled"
        else:
            message = "%s: %s" % (type(value).__name__, value)
        self.done(message, index)

    def done(self, message, index):
        self.done_message = message
        self.done_index = index
        self.screen = DEV_DONE

    def do_dev_done(self):
        draw_screen("Done", self.done_message, "Any button - Back")
        caselights_for("idle")
        if badge.pressed():
            self.to_dev(self.done_index)

    def open_measurements(self):
        """Every page built once, here, rather than per frame."""
        data = load_state(MEASUREMENTS)
        self.pages = []
        self.page = 0
        built = []
        width = screen.width - 2 * MARGIN
        if data:
            try:
                built = measurement_pages(data, menu_rows(),
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
        caselights_for("idle")
        if not self.pages:
            draw_screen("Measurements", "No measurements saved",
                        "Any button - Back")
            if badge.pressed():
                self.to_dev(DISPLAY_INDEX)
            return
        fill(color.black)
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
        draw_paged_title(title, label, label_width)
        screen.pen = color.white
        y = menu_top()
        width = screen.width - 2 * MARGIN
        for text, continued in lines:
            indent = HANG if continued else 0
            draw_text(text, (MARGIN + indent, y, width - indent, LINE_HEIGHT))
            y += LINE_HEIGHT
        draw_help("Up/Down - Page   A/B/C - Exit")

    # -- USB dev link: the screen that owns the USB serial while open; see
    # "USB dev link" in the spec.

    def open_link(self):
        import devserver
        import usbport
        port = usbport.badge_port(BUTTON_B)
        port.open()
        self.link_port = port
        self.link = devserver.Server(port, LINK_ROOT, LinkHooks(),
                                     link_builtins())
        try:
            self.link.scan()
        except (Exception, SystemExit):   # noqa: BLE001 - listed later
            pass
        self.link_b_armed = not badge.held(BUTTON_B)
        self.screen = DEV_LINK

    def close_link(self):
        link = self.link
        port = self.link_port
        self.link = None
        self.link_port = None
        try:
            link.close()
        finally:
            port.close()
        self.scan_tools()               # the host may have sent some
        self.to_dev(LINK_INDEX)

    def do_dev_link(self):
        link = self.link
        if link.service():
            # A run ended in this frame: B, which may have cancelled it,
            # counts again only once a later frame sees it released.
            self.link_b_armed = False
        elif not badge.held(BUTTON_B):
            self.link_b_armed = True
        if self.link_b_armed and badge.pressed(BUTTON_B):
            self.close_link()
            return
        draw_screen(LINK_TITLE, link.status_text(), "B - Exit")
        caselights_for("idle")

    def to_main(self):
        self.screen = MAIN
        self.cursor = 0
        self.error = None
        self.transfer = None

    def frame(self):
        return self.handlers[self.screen]()


app = App()


def autorun():
    """What a host launching this app over the REPL asked for, set as
    builtins.DEV_AUTORUN before launch() (tests/usbrun.py): "link" opens the
    USB dev link, and the app quits back to the REPL when it closes. None
    from the menu. Bare-name lookup, as for any injected builtin."""
    try:
        return DEV_AUTORUN    # noqa: F821 - a builtin only the host sets
    except NameError:
        return None


def update():
    if app.autorun is None:
        app.autorun = autorun() or ""
        if app.autorun != "link" and app.autorun:
            return True     # not one this app knows: back to the REPL
        if app.autorun:
            app.open_link()
    elif app.autorun and app.link is None:
        return True         # the link closed: back to the REPL
    if app.frame():
        on_exit()           # B on the main menu: leave the app
        return True
    return None


# run() clears the screen, calls update(), presents the frame and polls the
# buttons. Never call badge.update() from inside it.
run(update)
