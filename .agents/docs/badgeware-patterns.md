# Badgeware patterns

How to actually build things. The other files say what the API *is*;
this one says what working code *looks like*, and why.

Every pattern here is either taken from an app in this repo or reduced from one.
Where a pattern exists because of a bug we hit, the bug is named — that is
usually the part worth remembering.

Reference: [badgeware-api.md](badgeware-api.md) ·
[badgeware-graphics.md](badgeware-graphics.md) ·
[badgeware-hardware.md](badgeware-hardware.md)

Confidence markers (**[source-v3]**, **[measured-v2]** and the rest) name the
firmware a claim holds for; the legend is in [README.md](README.md).

---

## 1. The app skeleton

An app is a folder with `__init__.py` and `icon.png`. Without the icon it will
not appear in the launcher.

```python
badge.mode(HIRES | VSYNC)

MARGIN = 6
LINE_HEIGHT = 16

def update():
    screen.pen = color.black
    screen.clear()
    screen.pen = color.white
    screen.text("hello", MARGIN, MARGIN)

run(update)
```

No imports for badge globals — `badge`, `screen`, `color`, `font`, `image`,
`rect`, `run`, `State` and the `BUTTON_*` constants are all injected. **Not
every name the docs call injected actually is** — `spritesheet` is documented
as one and raises `NameError` on this badge. See "What is NOT a global" in
[badgeware-api.md](badgeware-api.md).

**`run()` clears the screen for you, and a return value stops the loop.**
**[source-v3]** From `_run.__call__` in the firmware: each iteration is
`badge.clear()`, then `update()`, then `display.update()` and `badge.poll()` —
and `if (result := update()) is not None: return`. So the screen arrives at
`update()` already filled with `badge.default_clear` and the pen set to
`badge.default_pen`, and **returning anything but `None` from `update()` quits
the app**. Do not end `update()` with a bare `return something`.

**That clear is not free.** **[measured-v2]** A HIRES full-screen fill costs
11.4 ms against a 16.7 ms refresh, so an `update()` that fills the screen again
-- with its own background, say -- has spent two fills before drawing anything.
Set `badge.default_clear = None` and `badge.clear()` skips the fill; the
framebuffer then keeps the last frame, so a screen that has not changed needs
no redraw at all. `share_wifi` does this while it transmits. Put it back
(`color.black` is the firmware's default) before any screen that relies on it.

**Set the pen before `clear()`.** `clear()` fills with the *current* pen, so the
order matters and getting it backwards is silent:

```python
screen.pen = color.white
screen.clear()            # whole screen white
screen.text(msg, 10, 10)  # white text on white. Nothing visible.
```

That exact sequence is what made `wifi_scanner` flash a blank white screen.

---

## 2. Choosing a loop shape

Two shapes are documented and both work. **Pick one per app and never mix them**,
because the rule is that exactly one thing presents the frame.

| | `run(update)` | `while True` + `badge.update()` |
|---|---|---|
| Who presents | `run()` does | you do |
| Call `badge.update()`? | **never** | every iteration |
| Sub-screens | one state variable | nested loops with `return` |
| Suits | state machines, apps with modes | linear flows, wizards |

`run(update)` calls your function over and over, "clearing the screen, running
your function, showing the frame, and reading the buttons, until your function
returns something". Returning non-`None` stops it.

The manual loop nests naturally, which is why `wifi_scanner` uses it — a detail
view is just a function with its own loop that `return`s:

```python
def show_detail(key):
    while True:
        draw_detail(key)
        badge.update()
        if badge.pressed(BUTTON_B):
            return          # back to whatever called us
```

**In a manual loop, call `badge.update()` every single iteration.** It is what
polls the buttons, not just what shows the frame. Skipping it on frames where
nothing changed freezes input completely — no press will ever register again.

### A third shape: reading inside `update()` without presenting

When what matters is *reading* something often -- the light sensor, in
`share_wifi`'s transfer -- presenting every frame is the cost: on v2
`display.update()` blocks for ~12 ms of each 16.7 ms frame, and nothing
reads meanwhile **[measured-v2]**. The transfer instead loops *inside* one
`update()` call, under `run()`, and only returns when the screen's pixels
must change:

```python
def do_transfer(self):
    passes = 0
    while True:
        field = transfer.step(time.ticks_ms(), badge.light_level())
        if transfer.phase == PHASE_DONE:
            return self.finish_transfer(transfer)
        if backlight_changed:
            display.backlight(level)     # live at once: no present needed
        if pixels_changed:
            fill(pen)
            return                       # run() presents it, then polls
        passes += 1
        if passes % POLL_PASSES == 0:
            badge.poll()                 # buttons and badge.ticks
            if badge.pressed(BUTTON_B):
                transfer.fail(ERR_CANCELLED)
```

- **Poll the buttons yourself**: `badge.poll()` is separate from
  `display.update()` **[source-v2]**, and nothing else polls while you loop.
  HOME is a pin interrupt and works regardless.
- **What is on screen stays there.** The panel refreshes from its own memory,
  so a screen not presented holds its image **[inferred-all]**.
- **The backlight needs no present** -- `display.backlight()` sets a PWM duty
  directly -- so a loop that only changes it never returns at all.
- **Leave a seam for tests**: a cap on passes per `update()`, 0 on the
  badge, so a test can step it one reading a frame.

---

## 3. Decide once per frame

The most expensive bug in this repo, and it will happen again to anyone who does
not know this.

`badge.ticks` is "the number of milliseconds since Badgeware was powered on, **as
of when the current frame started**". It does not move between `update()` calls.
So a deadline tested *after* the frame is presented reads a newer clock than the
code that drew the frame:

```python
# WRONG. The frame that acts is never the frame that showed it was about to.
while True:
    draw_status(badge.ticks - last < INTERVAL)   # drew "7s to go"
    badge.update()                               # ticks jumps forward here
    if badge.ticks - last >= INTERVAL:           # now true
        rescan()                                 # acts, having shown "7s to go"
```

```python
# RIGHT. One decision, used for both drawing and acting.
while True:
    due = badge.ticks - last >= INTERVAL
    draw_status(due)
    badge.update()
    if due:
        rescan()
        last = badge.ticks
```

`wifi_scanner` displayed "Rescan in 1 sec" and never once showed "Rescanning
channels", because the scan always fired on the frame after the one that drew.

**For anything finer than a frame, `badge.ticks` cannot help you.** Use
`time.ticks_ms()` / `time.ticks_us()`, which read live, and subtract with
`time.ticks_diff()` because raw values wrap.

---

## 4. Buttons

Edge detection is built in. Never hand-roll a "was it down last frame" set.

```python
if badge.pressed(BUTTON_A):    # the press transition, this frame only
if badge.released(BUTTON_A):
if badge.held(BUTTON_A):       # continuous, for movement
if badge.changed(BUTTON_A):
if badge.pressed():            # no argument: list of every button pressed
```

Constants are bare globals: `BUTTON_A`, not `badge.BUTTON_A`. `BUTTON_HOME`
belongs to the launcher — do not bind it.

`pressed()` for menus and selections, `held()` for anything continuous.
`if badge.pressed():` is the idiom for "any button dismisses this".

---

## 5. Screen chrome

Give every screen the same frame so layout cannot drift between them. One
function, called by all of them:

```python
TITLE_Y = MARGIN
HELP_Y = screen.height - LINE_HEIGHT - MARGIN
BODY = rect(MARGIN, MARGIN + LINE_HEIGHT * 2,
            screen.width - 2 * MARGIN,
            screen.height - LINE_HEIGHT * 3 - 2 * MARGIN)

# NOTE: align= and overflow= need constants that DO exist here, but cannot be
# probed with globals() -- see §18. Real code goes through a wrapper like
# `share_wifi`'s draw_text(), which probes once and keeps a manual path for a
# firmware without them. overflow=ELLIPSES truncates by word; prefer measuring.
def draw_chrome(subtitle, help_text):
    screen.pen = color.white
    screen.text(subtitle, rect(MARGIN, TITLE_Y, screen.width - 2 * MARGIN, LINE_HEIGHT),
                align=(CENTER, TOP))
    screen.pen = color.grey
    screen.text(help_text, rect(MARGIN, HELP_Y, screen.width - 2 * MARGIN, LINE_HEIGHT),
                overflow=ELLIPSES)
```

Derive the body area from the chrome constants rather than repeating numbers, so
adding a header line shrinks the body automatically.

**Always read `screen.width` / `screen.height`.** Hardcoding 320x240 breaks the
moment anything switches to `LORES`.

---

## 6. Text that fits

`text()` has a real layout engine. Use it instead of arithmetic.

```python
screen.text(msg, x, y)                                   # top-left
screen.text(msg, rect(x, y, w, h))                       # word wrap
screen.text(msg, r, align=(CENTER, MIDDLE))  # centred
screen.text(msg, r, overflow=ELLIPSES)             # truncate with "..."
screen.text("BIG", x, y, 3)                              # 4th arg is SIZE
```

**The fourth positional argument is size, not width.** `screen.text(s, x, y, 300)`
draws at 300× scale. Widths go in a `rect`.

**Never count characters to fit text** — ROM fonts are not monospaced. Use
`measure_text()`, or `overflow=ELLIPSES` if the constant exists (see §18 — on
this badge it does not):

```python
w, h = screen.measure_text(msg)
screen.text(msg, (screen.width - w) // 2, y)   # centring without align
```

`wifi_scanner` has a `fit()` helper that trims a string in a per-character
`measure_text()` loop. That looked like a workaround for not knowing about
`ELLIPSES`; it is in fact the only thing that works on this badge, where the
constant is undefined. Copy it, or copy `share_wifi`'s `draw_text()`, which
probes once and uses the native path wherever it is available.

---

## 7. Menus with a wrapping cursor

```python
def move_cursor(cursor, count):
    if count == 0:
        return 0
    if badge.pressed(BUTTON_UP):
        cursor = (cursor - 1) % count
    if badge.pressed(BUTTON_DOWN):
        cursor = (cursor + 1) % count
    return cursor


def draw_menu(items, cursor):
    y = MARGIN + LINE_HEIGHT * 2
    for i, item in enumerate(items):
        screen.pen = color.white if i == cursor else color.smoke
        screen.text(("> " if i == cursor else "  ") + item,
                    rect(MARGIN, y, screen.width - 2 * MARGIN, LINE_HEIGHT),
                    overflow=ELLIPSES)
        y += LINE_HEIGHT
```

Modulo gives you wrap-around at both ends for free. Always handle the empty list
— an empty menu with a cursor is a crash waiting to happen.

---

## 8. Scrolling a list longer than the screen

Keep `scroll` as the index of the first visible row and nudge it just enough to
keep the cursor on screen:

```python
VISIBLE = BODY.h // LINE_HEIGHT

def clamp_scroll(scroll, cursor, total):
    if cursor < scroll:
        scroll = cursor
    elif cursor >= scroll + VISIBLE:
        scroll = cursor - VISIBLE + 1
    return max(0, min(scroll, max(0, total - VISIBLE)))
```

A proportional scrollbar, drawn only when it is needed:

```python
def draw_scrollbar(scroll, total, top, height):
    if total <= VISIBLE:
        return
    x = screen.width - MARGIN - 3
    screen.pen = color.grey
    screen.rectangle(x, top, 3, height)
    thumb = max(6, height * VISIBLE // total)
    travel = total - VISIBLE
    screen.pen = color.white
    screen.rectangle(x, top + (height - thumb) * scroll // travel, 3, thumb)
```

Floor the thumb height, or a long list makes it vanish.

**Selection that survives a data refresh** must be held as an identity, not an
index. `wifi_scanner` re-scans every few seconds and the row order changes, so it
holds the selected network's BSSID and re-finds it each frame, falling back to
the nearest position when it disappears. An index would silently jump to an
unrelated row.

---

## 9. Persisting settings

```python
settings = State.load("my_app", {"brightness": 80})
State.modify("my_app", {"brightness": 50})    # merges
State.save("my_app", settings)                # replaces the whole object
```

**`modify` merges, `save` replaces.** Using `save` to update one key drops every
other key. Values must survive a JSON round trip, so store components rather than
`vec2` or `color` objects.

**Never write per frame.** The writable volume is ~256 blocks of 4KB and wears
out; a 10-byte file still costs 4KB. Buffer in RAM, write at a natural moment.

---

## 10. Wi-Fi

Connecting is non-blocking *by design*. Calling `connect()` repeatedly is what
moves the attempt along:

```python
def update():
    wifi.connect(ssid, password)       # every frame while attempting
    if wifi.is_connected():
        ...
    else:
        code, description = wifi.status()
        draw_status(description)
```

With no arguments it reads `WIFI_SSID` / `WIFI_PASSWORD` from `secrets.py`.

**Scanning is a different story.** There is no scan in the `wifi` module; it goes
through MicroPython's stack directly, and it *blocks* for seconds:

```python
from network import WLAN
nic = WLAN(WLAN.IF_STA)
nic.active(True)
nets = nic.scan()      # (ssid, bssid, channel, rssi, security, hidden)
```

`ssid` is `bytes` — decode it.

**The security field is not the 0-5 auth enum.** **[source-v3]** On this badge
`network_cyw43.c` puts the scan result's `auth_mode` straight into the tuple,
and the driver documents that as the `CYW43_AUTH_FLAG_*` **bitmask**: WPA2-AES
is `0x00400004`, WPA3 is `0x01000000`, Enterprise sets `0x02000000`. Only Open
(`0`) and WEP (`1`) happen to coincide with the conventional numbering. Decode
the flags, and map with a fallback rather than indexing a list:

```python
WPA3, WPA2, WPA = 0x01000000, 0x00400000, 0x00200000
def security_name(flags):
    if flags & 0x02000000: return "Enterprise"
    if flags & WPA3: return "WPA3"
    if flags & WPA2: return "WPA2"
    if flags & WPA:  return "WPA"
    if flags & 0x0001: return "WEP"
    return "Open" if flags == 0 else "Unknown (%d)" % flags
```

`wifi_scanner` still maps 0-5 and so renders almost every real network as
`Unknown (4194308)`. That is a live bug, not a hypothetical.

---

## 11. Unavoidably blocking calls

There is **no threading and no multiprocessing** on this badge. There is nowhere
to put a slow call. So draw first, present, *then* block:

```python
draw_list()
screen.text("Scanning...", MARGIN, STATUS_Y)
badge.update()          # the message is now actually on the glass
nets = nic.scan()       # stalls here for seconds
```

Present before blocking or the user stares at the previous frame with no
explanation. Buttons do not respond during the stall — presses are dropped, not
queued — so keep blocking work rare and tell the user it is happening.

---

## 12. Motion that does not depend on frame rate

```python
SPEED = 60      # pixels per second

def update():
    global x
    x += SPEED * (badge.ticks_delta / 1000)
```

Or a `tween`, which self-times against the badge clock:

```python
slide = tween(vec2(0, 0), vec2(100, 0), duration=500, easing=tween.CUBIC_OUT)
slide.start()

def update():
    screen.blit(sprite, slide.now.x, slide.now.y)
```

---

## 13. Probing for firmware differences

The docs and the badge in front of you do not always agree, and neither always
matches the firmware source.

`font.<name>` **works** — **[emulator-v3]** `font.smart` returns
`pixel_font("/rom/fonts/smart.ppf")`, and `getattr(font, "nosuch", None)` gives
`None` rather than raising, so a probe with a default is safe. An earlier note
here recorded `font.smart` raising `type object 'type' has no attribute
'smart'`, which would mean older firmware on that unit. The probe costs nothing
and covers both, so it stays — but do not carry the claim forward as fact.

When the docs might be wrong, resolve defensively and degrade rather than crash:

```python
def pick_font(*names):
    spaces = []
    try:
        spaces.append(font)             # what the firmware source uses
    except NameError:
        pass
    try:
        spaces.append(rom_font)         # not in current firmware; probed anyway
    except NameError:
        pass
    for space in spaces:
        for name in names:
            chosen = getattr(space, name, None)
            if chosen is not None:
                return chosen
    return None

chosen = pick_font("smart", "memo", "sins")
if chosen is not None:
    screen.font = chosen        # otherwise keep the default
```

Note the `NameError` guards: a missing *global* is a `NameError`, a missing
*attribute* is an `AttributeError`, and you may hit either.

Better still, check rather than reason. `print(dir(font))` settles it on the
badge in seconds, and the simulator at <https://try.badgewa.re> answers the
same question without hardware at all — see "Before you write code" in
[../../AGENTS.md](../../AGENTS.md).

---

## 14. Staying off the allocator

The heap is 8MB with no swap, and "creating objects inside a tight loop triggers
garbage collection, which causes pauses".

- Build lists, buffers and `shape`s once, outside the frame path.
- Use the in-place `vec2` operators (`+=`) rather than making new vectors.
- Call `gc.collect()` at a moment you choose — between levels, between blocks,
  while idle — rather than letting it land mid-frame.
- Hoist anything invariant out of a per-row draw loop.

In the Optical Data Transfer Protocol a GC pause that straddles a slot boundary corrupts a transmitted
symbol, so buffers are preallocated at transfer start and `gc.collect()` is
called only while waiting on an acknowledgement.

---

## 15. Splitting an app across files

The launcher does `os.chdir(path)`, `sys.path.insert(0, path)`, then imports the
app — so **the app's own folder is on `sys.path`** and siblings are plain
top-level imports, not relative ones:

```python
# firmware/apps/share_wifi/__init__.py
from protocol import *      # NOT "from .protocol import"
import protocol
```

The bundled `plucky_cluck` app does the same with `from chicken import Chicken`.
Assets go in `assets/` or `images/` beside the code, and relative paths work
because the working directory is the app folder.

Put anything that does not touch the badge in its own module. It can then be
imported under plain CPython and tested without stubs — see the next section.

## 16. Remember what is not a global

`badge`, `screen`, `display`, `State`, `rtc`, `color`, `font`, `image`, `rect`
and the `BUTTON_*` constants are injected. **`wifi` is not** — it is a module:

```python
import wifi
wifi.connect(ssid, psk)
```

Using it bare is a `NameError` at launch, and a stubbed test will not catch it,
because a stub satisfies both spellings. Same for `secrets`, `ntptime`,
`machine` and `network`.

## 17. Making it testable

Badge apps are plain modules reading injected globals, so stubbing those globals
lets the logic run under CPython. Keep the parts worth testing — codecs, parsers,
state machines — as free functions or small classes that do not touch `screen`,
and the rest follows.

```
python3 tests/run_tests.py
```

See [testing.md](testing.md). This cannot test drawing or real
timing, but it catches the class of bug that reading does not.

## 18. MicroPython is not CPython in the small

Stubbed tests run under CPython, so a construct CPython accepts will pass the
whole suite and then fail on the badge. These are the ones that have.

**Do not unpack keyword arguments with `**`.** **[verified-v2]**

```python
kwargs = {} if frame_ms is None else {"frame_ms": frame_ms}
transfer = Transfer(leader, payload, **kwargs)     # WRONG on the badge
```

On the badge this put `leader` into the *payload* slot, one position to the
left, and the failure surfaced further down as

    TypeError: Bool object isn't iterable

pointing at a line containing no loop — the call that had shifted, not the
`build_tokens(*payload)` that eventually iterated the bool. Pass every argument
positionally or by name:

```python
transfer = Transfer(leader, payload, frame_ms=frame_ms)
```

If a default is what you are trying to preserve, name the default directly
rather than building it conditionally, and put the function below whatever
defines it.

**`*` unpacking in a call is fine** — `build_tokens(*fields)` works. It is `**`
that bit.

**Avoid `%r`.** **[inferred-all]** `%`-formatting does not carry `%r` on every
build. Use `%s` on something you have already turned into text, and wrap a
`decode()` you did not produce yourself:

```python
try:
    text = raw.decode("utf-8")
except (ValueError, UnicodeError):
    text = "%d bytes, not text" % len(raw)
```

**You cannot probe for an injected global with `globals()`.** **[source-v3]**
This is the one to remember, because the wrong version of it reads perfectly
well and can never work.

The firmware injects its globals into `builtins` — `badgeware/__init__.py` is a
run of `builtins.CENTER = image.CENTER` and `setattr(builtins, k, v)`. An
injected name is therefore **not in the app module's namespace**, so:

    globals().get("CENTER")        ->  None, whether or not it exists
    getattr(builtins, "CENTER")    ->  AttributeError, likewise

Bare-name lookup is the only spelling that finds one. A probe has to name the
constant and catch `NameError`:

```python
try:
    #         (left, top)   (left, middle)  (centre, top)  (centre, middle)
    _ALIGN = ((LEFT, TOP), (LEFT, MIDDLE), (CENTER, TOP), (CENTER, MIDDLE))
    _HAS_ALIGN = True
except NameError:
    _ALIGN = None
    _HAS_ALIGN = False
```

An earlier version of this file said the opposite — that the probe *must* use
`globals()` — and `share_wifi` followed it. The result was a probe that
answered "absent" on a firmware carrying every one of those names, so the
alignment path was dead code for as long as it stood. The test harness agreed,
because it injected the stand-ins into the module namespace rather than into
`__builtins__` the way the badge does. Two wrongs that matched.

**The constants themselves do exist**, under both spellings: `image.CENTER` and
bare `CENTER` are the same object. `ELLIPSES` exists too and is still not worth
using — it truncates by **word**, so a one-line box holding a long hyphenated
SSID comes out as `"..."` and nothing else. Measure and truncate by character
for anything name-shaped; `share_wifi`'s `fit()` is that, paired with the
native `align=`.

**Stubs that model the documentation instead of the badge.** **[source-v3]**
`State.load(name, defaults)` is documented to return the saved data. It returns
a **bool**. The stub in `tests/harness.py` modelled the documented contract, so
143 tests passed while the app crashed in `App.__init__` before drawing a frame.

A stub is a claim about the firmware, and it is only as good as the claim. When
one is contradicted, fix the stub to model what the badge does -- and keep the
documented shape too, so tests run both ways. `FakeState(returns_flag=...)` is
the worked example.

Worse, the permissive `Stub` returns a callable for *any* attribute, so a name
this firmware does not have becomes a lambda and is passed silently into the
drawing code. That is right for `badge.*`, which is mostly fire-and-forget, and
wrong for anything whose attributes are values. Those get `StrictStub`, which
raises exactly as the badge does -- `image` is the first.

**The pattern behind all three.** A test suite that runs under CPython proves the
logic, not the dialect. When something works in the suite and fails on the
badge, suspect a construct before suspecting the logic — and when it is one,
delete the construct rather than reasoning about how MicroPython parses it.
