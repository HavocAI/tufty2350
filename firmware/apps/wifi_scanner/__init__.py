from network import WLAN

badge.mode(HIRES | VSYNC)

MARGIN = 4
LINE_HEIGHT = 16
SCAN_INTERVAL = 3000    # ms between rescans

SCROLLBAR_WIDTH = 3
SCROLLBAR_GAP = 2
MIN_THUMB = 6

LABEL_GAP = 6           # between the channel label and the bar
COUNT_GAP = 6           # between the bar area and the count column
BAR_HEIGHT = 10

BASE_CHANNELS = range(1, 12)   # always listed, even with nothing on them

SECURITY_NAMES = {
    0: "Open",
    1: "WEP",
    2: "WPA-PSK",
    3: "WPA2-PSK",
    4: "WPA/WPA2-PSK",
    5: "WPA2-Enterprise",
}

nic = None
seen = {}          # bssid -> (net tuple, ticks when last seen)
last_scan = None   # badge.ticks of the last completed scan


# ROM fonts live on `font` in the docs but on `rom_font` in some firmware, and
# not every build ships every name. Take the first one that actually resolves.
def pick_font(*names):
    namespaces = []
    try:
        namespaces.append(rom_font)
    except NameError:
        pass
    try:
        namespaces.append(font)
    except NameError:
        pass

    for namespace in namespaces:
        for name in names:
            chosen = getattr(namespace, name, None)
            if chosen is not None:
                return chosen
    return None


_font = pick_font("smart", "memo", "sins")
if _font is not None:
    screen.font = _font


# --- layout -----------------------------------------------------------------

SUBTITLE_Y = MARGIN
HELP_Y = screen.height - LINE_HEIGHT - MARGIN
STATUS_Y = HELP_Y - LINE_HEIGHT
CONTENT_TOP = SUBTITLE_Y + LINE_HEIGHT + 2
CONTENT_HEIGHT = STATUS_Y - CONTENT_TOP
VISIBLE_ROWS = CONTENT_HEIGHT // LINE_HEIGHT
TEXT_WIDTH = screen.width - (2 * MARGIN) - SCROLLBAR_WIDTH - SCROLLBAR_GAP


# --- data -------------------------------------------------------------------

def security_name(code):
    return SECURITY_NAMES.get(code, f"Unknown ({code})")


def ssid_of(net):
    name = net[0]
    if isinstance(name, bytes):
        name = name.decode("utf-8", "replace")
    return name or "<hidden>"


# BSSID is stable across rescans; SSID and channel are not.
def key_of(net):
    return net[1]


# A network missing from the most recent scan is kept in the list, but marked.
def is_stale(key):
    return seen[key][1] != last_scan


def age_seconds(key):
    return (badge.ticks - seen[key][1]) // 1000


# Always chart the standard 2.4GHz channels, plus anything else we've seen
# (12-14 outside the US, or 5GHz channels).
def channels():
    return sorted(set(BASE_CHANNELS) | {net[2] for net, _ in seen.values()})


def keys_on(channel):
    found = [key for key, (net, _) in seen.items() if net[2] == channel]
    return sorted(found, key=lambda k: ssid_of(seen[k][0]))


# Whole seconds left before the next scan, rounded up so it never shows 0
# while the app is still waiting.
def seconds_to_scan():
    remaining = SCAN_INTERVAL - (badge.ticks - last_scan)
    return max(1, -(-remaining // 1000))


def scan_status(due):
    if last_scan is None:
        return "Scanning, Please Wait..."
    if due:
        return "Rescanning channels"
    secs = seconds_to_scan()
    return f"Rescan in {secs} sec" if secs == 1 else f"Rescan in {secs} secs"


def scan_due():
    return last_scan is None or badge.ticks - last_scan >= SCAN_INTERVAL


# Blocking - takes seconds. Callers draw and present a frame first so the badge
# isn't showing a stale screen with no explanation while this runs.
def rescan():
    global nic, last_scan
    if nic is None:
        nic = WLAN(WLAN.IF_STA)
        nic.active(True)

    now = badge.ticks
    for net in nic.scan():
        seen[key_of(net)] = (net, now)
    last_scan = now


# --- drawing ----------------------------------------------------------------

def frame():
    screen.pen = color.black
    screen.clear()
    screen.pen = color.white


# Trim text until it clears the scrollbar, so the two never overlap.
def fit(text, width):
    if screen.measure_text(text)[0] <= width:
        return text
    while text and screen.measure_text(text + "...")[0] > width:
        text = text[:-1]
    return text + "..."


# Centred by measurement rather than an alignment rect, so it stays correct
# whatever font the firmware actually gave us.
def draw_centered(text, y):
    screen.text(text, (screen.width - screen.measure_text(text)[0]) // 2, y)


# `due` is decided once at the top of each frame and passed through: badge.ticks
# only advances on badge.update(), so re-checking after the frame is presented
# would trigger the scan without ever having drawn "Rescanning channels".
def draw_chrome(subtitle, help_text, due):
    screen.pen = color.white
    draw_centered(subtitle, SUBTITLE_Y)
    screen.text(scan_status(due), MARGIN, STATUS_Y)
    screen.pen = color.grey
    screen.text(fit(help_text, screen.width - (2 * MARGIN)), MARGIN, HELP_Y)
    screen.pen = color.white


# Proportional thumb down the right edge, drawn only when the list overflows.
def draw_scrollbar(scroll, total):
    if total <= VISIBLE_ROWS:
        return

    x = screen.width - MARGIN - SCROLLBAR_WIDTH
    screen.pen = color.grey
    screen.rectangle(x, CONTENT_TOP, SCROLLBAR_WIDTH, CONTENT_HEIGHT)

    thumb = max(MIN_THUMB, CONTENT_HEIGHT * VISIBLE_ROWS // total)
    furthest = total - VISIBLE_ROWS
    offset = (CONTENT_HEIGHT - thumb) * scroll // furthest if furthest else 0

    screen.pen = color.white
    screen.rectangle(x, CONTENT_TOP + offset, SCROLLBAR_WIDTH, thumb)


# Keep `cursor` inside the visible window and return the adjusted scroll.
def clamp_scroll(scroll, cursor, total):
    if cursor < scroll:
        scroll = cursor
    elif cursor >= scroll + VISIBLE_ROWS:
        scroll = cursor - VISIBLE_ROWS + 1
    return max(0, min(scroll, max(0, total - VISIBLE_ROWS)))


# --- screens ----------------------------------------------------------------

def show_detail(key):
    while True:
        due = scan_due()

        frame()
        y = CONTENT_TOP

        if key not in seen:
            screen.text("Network no longer listed", MARGIN, y)
        else:
            net = seen[key][0]
            for line in (
                f"Name:     {ssid_of(net)}",
                f"Security: {security_name(net[4])}",
                f"Signal:   {net[3]} dBm",
                f"Channel:  {net[2]}",
            ):
                screen.text(fit(line, TEXT_WIDTH), MARGIN, y)
                y += LINE_HEIGHT

            stale = is_stale(key)
            screen.pen = color.grey if stale else color.white
            screen.text(
                f"Seen:     {age_seconds(key)}s ago (out of range)" if stale
                else "Seen:     just now",
                MARGIN, y,
            )

        draw_chrome("Network Details", "B: back", due)
        badge.update()

        if badge.pressed(BUTTON_B):
            return
        if due:
            rescan()


def show_channel(channel):
    cursor = 0
    scroll = 0

    while True:
        due = scan_due()
        keys = keys_on(channel)
        cursor = min(cursor, max(0, len(keys) - 1))
        scroll = clamp_scroll(scroll, cursor, len(keys))

        frame()
        y = CONTENT_TOP

        if not keys:
            screen.text(f"No networks on channel {channel}", MARGIN, y)
        else:
            for i in range(scroll, min(scroll + VISIBLE_ROWS, len(keys))):
                key = keys[i]
                screen.pen = color.grey if is_stale(key) else color.white
                marker = "> " if i == cursor else "  "
                screen.text(fit(marker + ssid_of(seen[key][0]), TEXT_WIDTH), MARGIN, y)
                y += LINE_HEIGHT
            draw_scrollbar(scroll, len(keys))

        draw_chrome(f"Channel {channel}", "UP/DOWN: select  A: details  B: back", due)
        badge.update()

        if badge.pressed(BUTTON_B):
            return
        if keys:
            if badge.pressed(BUTTON_UP):
                cursor = (cursor - 1) % len(keys)
            if badge.pressed(BUTTON_DOWN):
                cursor = (cursor + 1) % len(keys)
            if badge.pressed(BUTTON_A):
                show_detail(keys[cursor])
        if due:
            rescan()


# One horizontal bar per channel: a grey bar for everything ever seen there,
# overlaid with a bright bar for what the last scan actually found.
def draw_bars(found, cursor, scroll):
    ordered = sorted(found)
    busiest = max(1, max(len(keys) for keys in found.values()))

    # Measure the real gutters rather than reserving guessed widths, so the
    # busiest channel's bar spans every pixel not needed by something else.
    labels = {c: f"{'>'} Ch {c}" for c in ordered}
    label_width = max(screen.measure_text(t)[0] for t in labels.values())
    count_width = max(screen.measure_text(str(len(k)))[0] for k in found.values())

    bar_left = MARGIN + label_width + LABEL_GAP
    right_edge = screen.width - MARGIN
    if len(ordered) > VISIBLE_ROWS:
        right_edge -= SCROLLBAR_WIDTH + SCROLLBAR_GAP
    count_left = right_edge - count_width
    bar_space = count_left - COUNT_GAP - bar_left

    y = CONTENT_TOP
    for i in range(scroll, min(scroll + VISIBLE_ROWS, len(ordered))):
        channel = ordered[i]
        keys = found[channel]
        live = sum(1 for key in keys if not is_stale(key))
        selected = i == cursor

        screen.pen = color.white if selected else color.grey
        screen.text(labels[channel] if selected else f"  Ch {channel}", MARGIN, y)

        bar_y = y + (LINE_HEIGHT - BAR_HEIGHT) // 2
        if keys:
            screen.pen = color.grey
            screen.rectangle(bar_left, bar_y, max(1, bar_space * len(keys) // busiest), BAR_HEIGHT)

        if live:
            screen.pen = color.cyan if selected else color.blue
            screen.rectangle(bar_left, bar_y, max(1, bar_space * live // busiest), BAR_HEIGHT)

        # Right-aligned in its own column, so counts line up down the screen.
        count = str(len(keys))
        screen.pen = color.white if selected else color.grey
        screen.text(count, right_edge - screen.measure_text(count)[0], y)
        y += LINE_HEIGHT


def show_graph():
    cursor = 0
    scroll = 0

    while True:
        due = scan_due()
        found = {channel: keys_on(channel) for channel in channels()}
        cursor = min(cursor, max(0, len(found) - 1))
        scroll = clamp_scroll(scroll, cursor, len(found))

        frame()
        draw_bars(found, cursor, scroll)
        draw_scrollbar(scroll, len(found))

        draw_chrome("Channels", "UP/DOWN: channel  A: networks on channel", due)
        badge.update()

        if badge.pressed(BUTTON_UP):
            cursor = (cursor - 1) % len(found)
        if badge.pressed(BUTTON_DOWN):
            cursor = (cursor + 1) % len(found)
        if badge.pressed(BUTTON_A):
            show_channel(sorted(found)[cursor])
        if due:
            rescan()


show_graph()
