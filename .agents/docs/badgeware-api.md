# Badgeware API reference

Core app surface: globals, the loop, buttons, text, state, wifi, filesystem,
time, the MicroPython dialect -- as v3 has it. **The badge on the desk runs
v2.0.2**, whose API is in [badgeware-api-v2.md](badgeware-api-v2.md), read
from the v2 source. For how to *use* these, see
[badgeware-patterns.md](badgeware-patterns.md). Confidence markers are explained
in [README.md](README.md).

Source pages: `/docs/api/{badge,builtins,image,color,font,wifi,rect}.md`,
`/docs/guides/{buttons,text,networking,performance,badge-differences}.md`,
`/docs/introduction/{your-first-app,meet-your-badge}.md`

Siblings: [badgeware-hardware.md](badgeware-hardware.md) for what the Tufty 2350
can do — display panel, sensor, timing floors, power, flash. [badgeware-graphics.md](badgeware-graphics.md)
for images, sprites, vector shapes, brushes and the maths types.

---

## Globals

## Which firmware is this?

**There is no badgeware version constant.** No `__version__`, no version file
on `/system`, nothing on `badge`. `os.uname()` is the only record of the build,
and it carries MicroPython's version and build date rather than Pimoroni's
release number — so the release is read off the date, and what the API actually
has is read by probing for it.
`rampage/tools/probe_badge.py` in the `tufty_badge` repo does both.

Feature to release, from the
[tufty2350 release notes](https://github.com/pimoroni/tufty2350/releases):

| Release | Date | What arrived |
|---|---|---|
| v1.0.0 | 2026-01-27 | shipping firmware |
| v2.0.0 | 2026-02-26 | `SpriteSheet` and `AnimatedSprite` as builtins; `io` becomes `badge`; `run()` becomes a builtin; `VSYNC` |
| v2.0.1 | 2026-02-26 | |
| v2.0.2 | 2026-04-10 | |
| v3.0.0 | 2026-08-07 | `image.spritesheet(cols, rows)` returning a `spritesheet` object with `.sprite(x, y)` / `.sprite(n)`; palette access on P8 images |
| v3.0.1 | 2026-08-08 | |
| v3.0.2 | 2026-08-17 | |
| v3.1.0 | 2026-09-14 | QR code factory on `image`; `transform=` on text |
| v3.1.1 | 2026-09-23 | |

**This badge is v2.x or earlier.** `spritesheet` raises `NameError` and
`image.spritesheet` raises `AttributeError`, and that method arrived in v3.0.0.
Which means **`SpriteSheet` may well exist here under that name** — a builtin
since v2.0.0, and not something any current doc mentions. Worth probing before
writing anything that cuts up an image.

The rest of this file was read from the firmware repo's `main`, which is v3.1.x.
Treat it as an upper bound on what this badge has, not a description of it.

---

**[source-v3]** Read from the firmware source,
[pimoroni/tufty2350](https://github.com/pimoroni/tufty2350), by collecting every
`builtins.X =` assignment in `modules/common/badgeware/`. The official docs list
is incomplete and, in one case, misleading.

Injected into every app — no imports needed:

| Global | What it is |
|---|---|
| `screen` | The framebuffer, an `image`. Rebound whenever `badge.mode()` changes |
| `badge` | Hardware: display mode, buttons, timing, power, light sensor |
| `display` | The ST7789 driver itself, including `display.backlight(0.0-1.0)` |
| `State` | Persistent JSON storage |
| `rtc` | The battery-backed real-time clock |
| `run`, `launch`, `loop` | App loop driver, launcher, current loop |
| `reset`, `fatal_error` | Reboot, and the firmware's error screen |
| `color`, `font`, `image`, `rect`, `vec2`, `mat3`, `brush`, `shape`, `tween`, `algorithm` | Whatever `picovector` exports, hoisted wholesale — see the warning below |
| `LEFT`, `CENTER`, `RIGHT`, `TOP`, `MIDDLE`, `BOTTOM` | Text alignment. **[source-v3]** present on v3, both bare and on `image`; **absent on the badge's v2** -- see below |
| `CLIP`, `ELLIPSES` | Text overflow, same two spellings |
| `OFF`, `X2`, `X4` | Antialiasing levels, same two spellings |
| `LORES`, `HIRES`, `VSYNC`, `DITHER` | Display modes |
| `BUTTON_A`, `BUTTON_B`, `BUTTON_C`, `BUTTON_UP`, `BUTTON_DOWN`, `BUTTON_HOME` | Button ids (bare globals, **not** `badge.BUTTON_A`) |
| `clamp()`, `rnd()`, `frnd()`, `file_exists()`, `is_dir()`, `free()` | Utilities |
| `text()`, `add_glyph()`, `add_sprite()` | Rich-text helpers |

### There is no spritesheet on this badge, under any spelling

**[verified-v2]** `rampage` shipped first with
the documented global and then with the method the firmware's own apps use.
Both panicked at launch:

```
spritesheet.load("boat.gif")
  NameError: name 'spritesheet' isn't defined

image.load("boat.png").spritesheet(6, 5)
  AttributeError: 'image' object has no attribute 'spritesheet'
```

This firmware predates the spritesheet type. The hoist is a loop over
`picovector.__dict__`, so the table above is only ever *whatever that module
happens to export on the build in question* — it is not a promise, and both the
official docs and the firmware repo's `main` have moved on from what is on this
unit.

For animation, use the two calls `firmware/apps/demos/demos/sprites.py` is
built on and this badge has demonstrably run:

```python
sprites = [image.load("images/%03d.png" % i) for i in range(count)]
screen.blit(sprites[n], dest)
```

`image.window(rect)` is documented as a sub-image view sharing pixels, which
would allow a single sheet with no spritesheet type at all. **[unverified]**
here — `rampage/tools/probe_badge.py` in the `tufty_badge` repo is what settles it, and settling it
costs a USB cable rather than a flash-and-reboot cycle.

The same caution applies to every other name in that row. `image`, `color`,
`rect`, `vec2` and `font` are used by shipped apps and are safe; the rest are
inherited from a doc page, not from reading the badge.

### What is NOT a global, despite looking like one

**`wifi` must be imported.** It is `modules/common/wifi.py` in the firmware, not
an injected name, and the bundled apps all do `import wifi`. Using it bare is a
`NameError` the moment the app launches — which no off-badge test will catch,
because a stub can satisfy either form.

```python
import wifi          # required
wifi.connect(ssid, psk)
```

Same for `secrets`, `ntptime`, and anything from `machine` or `network`.

**`secrets` is worth knowing in detail**, because it is where a badge's own
credentials live. **[source-v3]** `modules/common/secrets.py` imports `/secrets.py`
and falls back to `/system/secrets.py`, copying the contents to module scope:
`WIFI_SSID`, `WIFI_PASSWORD`, `REGION`, `TIMEZONE`. Stock values are `""`, which
is what an unprovisioned badge looks like. **Do not call `secrets.require()`** —
it reports a missing value by calling `fatal_error()`, which resets the badge.

---

## How an app is launched

**[source-v3]** From `launch()` in `modules/common/badgeware/__init__.py`:

```python
os.chdir(path)
sys.path.insert(0, path)
__import__(path)            # the app blocks here
```

Three consequences:

- **The app's own folder is on `sys.path`**, so a multi-file app imports its
  siblings as plain top-level modules — `from chicken import Chicken`, not
  `from .chicken import`. The bundled `plucky_cluck` app does exactly this.
- The working directory is the app folder, so relative asset paths work.
- `BUTTON_HOME` is wired to an IRQ that quits to the launcher. Do not bind it.

An app folder holds `__init__.py`, `icon.png`, any sibling modules, and
optionally `assets/` or `images/`.

---

## The two loop patterns

Both are documented and valid. Pick one per app and do not mix them.

### `run(update)` — the loop drives you

> "Runs your app. Hand `run()` a function and it calls it over and over: clearing
> the screen, running your function, showing the frame, and reading the buttons,
> until your function returns something."

```python
def update():
    screen.text("hello", 10, 10)

run(update)
```

`run()` does the clear, the frame present and the button poll. **Do not call
`badge.update()` inside an app driven by `run()`** — you would present twice per
frame. Returning any non-`None` value stops the loop; `run()` returns a loop
object whose `.result` holds that value. Takes an optional `duration=` in ms.

**`run` is a class, not a function.** **[source-v3]** `builtins.run = _run`, and
`_run.__init__` calls `self(args[0])` when handed one callable, which is why
both `run(update)` and `@run` work. Its loop body is:

```python
badge.clear()                       # default_clear, then pen = default_pen
if (result := update()) is not None: ...
display.update()
badge.poll()                        # this is what advances badge.ticks
```

Note it calls `display.update()` and `badge.poll()` directly rather than
`badge.update()`. `badge.ticks` still advances once per frame, because `poll()`
is what moves it. An unhandled exception inside `update()` goes to
`fatal_error()` — the error screen, then a reset.

The two docs pages describe this differently — `/docs/api/badge.md` shows
`run(update)`, while `/docs/introduction/your-first-app.md` writes everything as
nested `while True` loops calling `badge.update()`. Both work. The rule is that
exactly one of them presents the frame.

### `while True` + `badge.update()` — you drive the loop

```python
while True:
    screen.text("hello", 10, 10)
    badge.update()
```

`badge.update()` "pushes the `screen` image to the physical display, then runs
`badge.clear()` and `badge.poll()`". That poll is what refreshes button state.
This pattern nests naturally — a sub-screen is just a function with its own
`while` loop that `return`s when you want to go back.

**The trap:** if you only call `badge.update()` on frames where something changed,
buttons stop being polled and input dies. Call it every single iteration.

---

## Buttons

```python
if badge.pressed(BUTTON_C):   # fired this frame on the press edge
if badge.released(BUTTON_C):  # fired this frame on the release edge
if badge.held(BUTTON_C):      # true continuously while down
if badge.changed(BUTTON_C):   # fired this frame on any transition
```

Edge detection is built in — **never hand-roll a "was it down last frame" set.**
Each call takes an optional button; with no argument it returns the collection
of buttons matching that condition — a **tuple** in practice, not a list, so
test it for truthiness rather than comparing to `[]`:

```python
for b in badge.held():
    print("holding", b)
```

Use `pressed()` for menus and selections, `held()` for continuous movement.
All four report state as of the most recent `badge.update()` (or the poll that
`run()` does for you).

---

## Screen and drawing

`screen` is a global `image` bound to the framebuffer.

Properties: `width`, `height` (read-only), `pen`, `font`, `cursor`, `clip`, `alpha`.

```python
screen.pen = color.white
screen.clear()                      # fills with the current pen/brush
screen.rectangle(x, y, w, h)        # or screen.rectangle(rect)
screen.circle(x, y, r)
screen.line(x0, y0, x1, y1)
screen.triangle(...)
screen.put(x, y)                    # single pixel
screen.shape(...)                   # antialiased vector shapes (slower)
```

The canvas is cleared for you each frame via `badge.default_clear()`, so an
explicit `screen.clear()` is only needed when you want a specific background
color — and then set `screen.pen` **before** clearing:

```python
screen.pen = color.black
screen.clear()
screen.pen = color.white   # now draw
```

Setting the pen after the clear and then drawing is how you get a blank screen in
the fill color.

### Text

The full signature, from `/docs/api/text.md`:

```python
.text(message, bounds, size, align, overflow, line_height, word_spacing, transform)
```

Common forms:

**`measure_text()` returns floats**, not ints — `(28.0, 12.0)` for "Hello" in
`font.smart`. **[emulator-v3]** Fine for arithmetic, but `//` on them yields a
float too, so anything that must be an integer needs `int()`.

```python
screen.text("hello", 10, 10)                    # x, y = top-left
screen.text("BIG", 10, 40, 3)                   # pixel font: integer scale
screen.text("Any size", 10, 44, 30)             # vector font: pixel size
screen.text("two\nlines", 10, 10)               # newlines break at original x
screen.text(msg, rect(10, 10, 140, 100))        # a rect gives word wrap
screen.text(line)                               # no coords: uses screen.cursor
w, h = screen.measure_text(msg)
```

Returns a `rect` bounding what it drew, which chains nicely for stacked layout.

**Alignment:** `align=(horizontal, vertical)`, default `(LEFT, TOP)`.
Horizontal is `LEFT` / `CENTER` / `RIGHT`, vertical is
`TOP` / `MIDDLE` / `BOTTOM`.

> **Both spellings exist on v3, and neither on v2.** **[source-v3]** The v3
> source's `badgeware/__init__.py` reads `builtins.CENTER = image.CENTER` for
> all eight names, so there `image.CENTER` and bare `CENTER` are the same
> object -- but those lines arrived on 2026-07-29, after the v2.0.2 the badge
> runs, and on the badge both spellings raised on the first frame **[verified-v2]**. An earlier note here called the "absent" report wrong; it
> was right about v2. Probe, and keep a path that works without them.
>
> **They cannot be probed with `globals()`.** An injected global lives in
> `builtins`, not in the app module's namespace, so `globals().get("CENTER")`
> reads `None` whether the constant exists or not — and `getattr(builtins,
> "CENTER")` raises `AttributeError` as well. Bare-name lookup is the only
> spelling that finds one. A probe therefore has to name the constant and catch
> `NameError`:
>
> ```python
> try:
>     _ALIGN = ((LEFT, TOP), (CENTER, MIDDLE))
>     _HAS_ALIGN = True
> except NameError:
>     _HAS_ALIGN = False
> ```
>
> `share_wifi`'s `draw_text()` is the worked example, and `pick_font()` beside
> it is the same shape. Keep the manual path: it is what runs on a firmware old
> enough to lack the constants, and `fit()` truncates better than `ELLIPSES`
> does (below).

**Overflow:** `overflow=CLIP` (default) or `ELLIPSES`.

```python
screen.text(ssid, rect(x, y, w, LINE_HEIGHT), overflow=ELLIPSES)
```

`ELLIPSES` truncates natively, in C — but **by word**, not by character.
**[emulator-v3]** A one-line box given `"A-Very-Long-Network-Name-That-Will-Never-Fit"`
comes back as `"..."` and nothing else, because the first word already overruns;
the same box with `CLIP` draws 407px of text into 308px. Neither is what a list
of SSIDs wants, which is why `wifi_scanner`'s per-character `measure_text()`
loop is not the dead pattern it was taken for. `share_wifi` uses the native
`align=` and its own `fit()`, and no `overflow=` at all.

Also available: `line_height` (multiplier, default 1), `word_spacing` (multiplier,
default 1), `transform` (a `mat3` applied after layout), and inline `[markup]`.

**[source-v3]** **The fourth positional argument is size, not width.**
`screen.text(msg, x, y, 300)` draws at 300x scale rather than wrapping at 300px.
Widths belong in a `rect`.

Centring, if you are not using `align`:

```python
screen.text(t, (screen.width - screen.measure_text(t)[0]) // 2, y)
```

### Fonts

All ROM fonts are pixel fonts, sized by whole-number scale (`1` native, `2`
double). Names include: `smart`, `ark`, `desert`, `torch`, `sins`, `teatime`,
`hungry`, `kobold`, `lookout`, `loser`, `winds`, `match`, `corset`, `nope`,
`unfair`, `saga`, `memo`, `outflank`, `salty`, `awesome`, `compass`, `yolk`,
`vest`, `holotype`, `yesterday`, `absolute`, `fear`, `troll`, `bacteria`,
`curse`, `ziplock`, `futile`, `manticore`, `more`, `ignore`.

```python
screen.font = font.smart
screen.font = font.load("/path/to/font.af")   # .af = vector, .ppf = pixel
```

**[emulator-v3]** **`font.<name>` works on current firmware.** `font.smart` returns
`pixel_font("/rom/fonts/smart.ppf")`, and `getattr(font, "nosuch", None)` gives
`None` rather than raising, so a probe with a default is safe.

An earlier note here recorded `font.smart` raising
`type object 'type' has no attribute 'smart'` on the badge in this repo. That
cannot be true of the build the source describes, so if it recurs the badge is
running **older firmware** in which `font` resolved to the Font class rather
than the ROM namespace. `pick_font()` costs nothing and covers it either way.

Note that **`rom_font` appears nowhere in the current firmware**. An earlier
version of these notes claimed ROM fonts "live on `rom_font`"; the source does
not support that. Prefer `font`, keep a cheap probe for `rom_font`, and treat a
missing font as "keep the default" rather than a crash — see `pick_font()` in
`share_wifi`.

The definitive check, on the badge:

```python
print(dir(font))
```

### Color

16 named colors (DawnBringer 16): `black`, `grape`, `navy`, `grey`, `brown`,
`green`, `red`, `taupe`, `blue`, `orange`, `smoke`, `lime`, `latte`, `cyan`,
`yellow`, `white`.

```python
screen.pen = color.white
screen.pen = color.rgb(r, g, b, a)      # 0-255, alpha optional
screen.pen = color.hsv(h, s, v, a)
screen.pen = color.oklch(l, c, h, a)    # perceptually uniform; best for adjusting
```

---

## `badge` module

```python
badge.mode(HIRES | VSYNC)   # set; badge.mode() with no args reads current
badge.update()              # present frame + clear + poll (manual-loop apps only)
badge.ticks                 # ms since power-on, as of the last update()
badge.ticks_delta           # ms since the previous update()
badge.light_level()         # raw u16 from the light sensor (Tufty only)
badge.caselights(level)     # rear LEDs, 0-1
badge.battery_level()       # percent; also battery_voltage(), usb_connected(), is_charging()
badge.sleep()               # low power; woken_by_button(), wake_reason(), ...
```

**[source-v3]** **`badge.ticks` only advances when `update()` is called.** Every read
within a single frame returns the same value. This is not cosmetic — a deadline checked
*after* `badge.update()` sees a newer clock than the code that drew the frame, so
the frame that decides to act is never the frame that drew the decision. Decide
once at the top of the loop and pass the decision down. This caused a real bug in
`wifi_scanner`, where "Rescanning..." was never displayed.

**[source-v3]** **[verified-v2]** **There is backlight control**, though the official docs do not mention
it. `display` is a global and `display.backlight(level)` takes 0.0-1.0. The
firmware sets 230/255 at init; the bundled `tomato` app dims to 0.5 while its
timer runs. `badgeware.set_brightness(value)` wraps the same call.

**There is no getter.** **[emulator-v3]** `display.backlight()` with no argument
raises `TypeError: backlight() takes 2 positional arguments but 1 were given`,
so an app that changes the level cannot read the old one back — restore it to
230/255, the value the firmware sets and never touches again.

Critically, **nothing in the firmware reads `light_level()` to drive the
backlight** — there is no auto-brightness loop. See the hardware notes.

Mode constants — Tufty: `LORES` (160x120), `HIRES` (320x240), `VSYNC` (prevents
tearing), and `DITHER`. Values are `LORES = 0b00`, `HIRES = 0b01`, `VSYNC = 0b10`,
`DITHER = 1 << 8`. The remaining mode constants in the official docs are for
Badger hardware and do nothing here.

**`badge.mode()` rebinds `screen`.** **[source-v3]** Changing mode assigns a new
`builtins.screen` over the new framebuffer, carrying the previous `font` and
`pen` across. Anything holding its own reference to the old `screen` keeps
drawing into a buffer nobody presents — read the global each time. Calling it
with the mode already set returns `None` and does nothing; the firmware
initialises to `LORES | VSYNC`, so an app wanting hires must say so.

**`badge.caselights(level)` is gamma-corrected and returns state.**
**[source-v3]** It stores the level then writes `int(level ** 2.2 * 65535)` to
each PWM channel, and returns the list of all four levels whether or not you
passed any. `caselights(a, b, c, d)` sets them individually.

Read dimensions from `screen.width` / `screen.height` rather than hardcoding —
they change with the mode.

---

## State

Per-app persistence as JSON, in `/state/<name>.json`.

```python
State.load(name, defaults)   # SEE BELOW -- returns a bool on this badge
State.save(name, data)       # replace the whole saved state
State.modify(name, data)     # merge changes in without reloading
State.delete(name)           # remove it
```

**`load()` does not return the saved data.** **[source-v3]** It returns a **bool**
and fills in the dict it was handed. Code that used the return value directly
died on the first frame:

    TypeError: bool object isn't iterable

Confirmed in `badgeware/state.py`: `defaults.update(data); return True`, or
`State.save(app, defaults); return False` when there was nothing to read. So a
first load also *writes* the defaults out. The official docs agree — the
"saved data, or defaults" phrasing is elsewhere and is what misled this file.

Write it so that either contract works, and never take the return value on
trust:

```python
loaded = {}
returned = State.load("my_app", loaded)
data = returned if isinstance(returned, dict) else loaded
```

`firmware/apps/share_wifi/__init__.py`'s `load_networks()` is the worked example. Getting this
wrong the other way is worse than the crash: return `{}` on the unexpected shape
and the app silently forgets everything it had saved.

> "Everything in the dictionary has to survive a round trip through JSON, so
> numbers, strings, booleans, lists and dictionaries of those."

So no vectors or colors — store their components. The name must be distinct per app
and identical between save and load. `/state` directories are created as needed.


---

## Wi-Fi

**`import wifi` first** — it is a module in the firmware, not an injected global.

The `wifi` module handles **connecting**, and is non-blocking:

```python
wifi.connect()          # no args: reads WIFI_SSID / WIFI_PASSWORD from secrets.py
                        # optional timeout= (default 60s), retries= (default 5)
wifi.is_connected()
wifi.status()           # (code, human-readable description)
wifi.disconnect()
wifi.ip() / wifi.ipv4() / wifi.ipv6()
wifi.subnet() / wifi.gateway() / wifi.nameserver()
```

> "Calling `connect()` repeatedly both starts the attempt and moves it along, so
> your app can keep drawing while it waits."

**[source-v3]** **Status codes**, from `modules/common/wifi.py`:

| Code | Meaning |
|---|---|
| 0 | Idle |
| 1 | Connecting |
| 2 | Connected |
| 3 | Got IP |
| -1 | Connection failed |
| -2 | Access point not found |
| -3 | Incorrect password |

The firmware's own failure test is `status() not in (0, 1, 2, 3)` — **not**
`code < 0`.

Three things in `modules/common/wifi.py` that bite: **[source-v3]**

- **`connect()` ignores the SSID once a `WLAN` exists.** It keeps one at module
  scope and short-circuits: `if wlan: _tick(); return is_connected()`. So
  picking a second network without `wifi.disconnect()` first reports the
  *first* one's result.
- **It reports failure by resetting the badge.** `connect()` retries internally
  (`timeout=60`, `retries=5`) and then calls `fatal_error()` — the error screen,
  then `machine.reset()`. It does not raise anything an app can catch, so keep
  your own deadline and stop calling it. Same for `secrets.require()`.
- **It never forwards `security=`** to `wlan.connect()`. That leaves exactly two
  reachable modes: an empty key selects `CYW43_AUTH_OPEN`, and a non-empty one
  selects `CYW43_AUTH_WPA2_MIXED_PSK`. WPA3 and WEP need the kwarg, so they need
  `network.WLAN` directly.

**An empty key is how you join an open network.** **[source-v3]** From
`network_cyw43.c`: "Default to open when no password set."

**`wifi.status()` can raise, and builds differ.** **[verified-v2]** One badge reported `NameError: name 'wifi' isn't defined` raised from
*inside* `wifi.py`, at its own line 88, in `status()` — on the branch only
reached once a connection attempt is live, which is why it surfaces on the
connecting screen and never before. Neither `tufty2350`'s main branch nor the
simulator's frozen build has that fault: main's `status()` reads

```python
def status():
  if wlan is None:
    return 0, get_status(0)
  return wlan.status(), get_status(wlan.status())
```

with no reference to `wifi` anywhere in the module. So there are at least three
`wifi.py` builds in circulation and they are not interchangeable. **Wrap the
call.** Nothing needs it to succeed — `is_connected()` decides success and your
own deadline decides failure — so treat an unreadable status as "still trying"
rather than as an error, or a connection that was about to work gets abandoned.
`share_wifi`'s `wifi_status()` is the worked example.

**WEP cannot be joined at all.** **[source-v3]** `cyw43_ll_wifi_join()` accepts
Open, WPA-TKIP, WPA2-AES, WPA2-mixed and the two WPA3 modes, and returns
`-CYW43_EINVAL` for anything else *before issuing a single ioctl*. The driver
also contains no `WLC_SET_KEY` / `wsec_key_t` path — it only ever writes
`wsec_pmk`, the WPA passphrase ioctl. `network.WLAN` exports `SEC_OPEN`,
`SEC_WPA_WPA2`, `SEC_WPA3` and `SEC_WPA2_WPA3`, and no `SEC_WEP`, because there
is nothing behind it.

**`WLAN.ioctl(cmd, buf)` is a raw passthrough** to the chip, should you ever
need past the driver. `cmd` packs the direction into the low bit —
`cyw43_do_ioctl(self, cmd & 1 ? SDPCM_SET : SDPCM_GET, cmd >> 1, ...)` — so a
set is `(WLC_COMMAND << 1) | 1`, and `buf` must be a mutable `bytearray`. It is
a hole, not an API: the struct layouts are undocumented and whether the chip
firmware implements a given feature is unknowable from source.

**Scanning is not in the `wifi` module.** The networking guide notes "MicroPython
gives you the full `network` stack if you want it", so scanning goes through it
directly:

```python
from network import WLAN
nic = WLAN(WLAN.IF_STA)
nic.active(True)
nets = nic.scan()   # BLOCKING - seconds. See wifi_scanner.
```

Each entry is the standard MicroPython tuple
`(ssid, bssid, channel, rssi, security, hidden)`. `ssid` is `bytes`, so decode it.

**`security` is a bitmask here, not the 0-5 auth enum.** **[source-v3]**
`network_cyw43.c` puts the scan result's `auth_mode` straight into the tuple,
and `cyw43_ll.h` documents that field as "security and authentication flags" —
the `CYW43_AUTH_FLAG_*` set:

| Flag | Meaning |
|---|---|
| `0x0001` | WEP |
| `0x0002` / `0x0004` | TKIP / AES |
| `0x00200000` | WPA |
| `0x00400000` | WPA2 |
| `0x01000000` | WPA3 |
| `0x02000000` | Enterprise |

So WPA2-AES reads as `0x00400004` (4194308), not `3`. Only Open (`0`) and WEP
(`1`) coincide with the conventional numbering. Test the flags and keep a
fallback. **`wifi_scanner` still maps 0-5 and so shows almost every real network
as `Unknown (4194308)` — a live bug.**

---

## Filesystem

16MB of flash in three volumes:

| Volume | Size | Contents | Writable |
|---|---|---|---|
| `/` | 1MB | app data and caches | yes, by apps |
| `/system` | 12MB | apps, assets, `main.py` | host only, in disk mode |
| `/rom` | 1MB | built-in fonts | never |

```python
with open("/notes.txt", "w") as f:
    f.write("hello badge\n")

import json
with open("/settings.json", "w") as f:
    json.dump(settings, f)

try:
    with open("/settings.json", "r") as f:
        settings = json.load(f)
except (OSError, ValueError):
    settings = defaults
```

Two constraints that bite:

- `/` has only **~256 blocks of 4KB**, so a couple of hundred files maximum, and a
  10-byte file still costs 4KB. Consolidate into one JSON file rather than many
  small ones.
- **Flash wear.** "Buffer your data in memory and save it every now and then,
  rather than on every frame."

`/system` is writable only over USB disk mode, where the badge mounts as `TUFTY`.
Runtime code cannot write there, by design.

**Which volume is which, and what disk mode actually shows.** **[source-v3]**
From `modules/common/_boot_fat.py`:

```python
vfs.mount(lfs, "/")                          # LittleFS, the last 1MB of flash
vfs.mount(fat, "/system", readonly=True)     # FAT, everything before it
```

`rp2.enable_msc()` in `modules/python/_msc.py` exposes the **FAT** volume. So
the volume a host sees over USB is the read-only one, and everything an app
writes — `/`, `/state` — is on the volume it does not see. **There is no path
an app can write that a user can then drag off the badge.** Getting data off
means the REPL (`mpremote fs cp :/state/thing.json .`) or `print()` and copy
from the terminal.

`State` (above) is the structured way to persist; raw files are for everything else.

---

## Time

```python
badge.ticks         # ms since power-on, as of when the current frame started
badge.ticks_delta   # ms since the previous frame
```

Frame-rate independent movement:

```python
SPEED = 60  # pixels per second

def update():
    x += SPEED * (badge.ticks_delta / 1000)
```

For sub-frame timing, the `time` module reads **live**, unlike `badge.ticks`:

```python
time.ticks_ms()     # live
time.ticks_us()
time.time_ns()
time.ticks_diff(a, b)   # use this to subtract - raw tick values wrap
```

That distinction matters. `badge.ticks` is a per-frame snapshot, so anything needing
resolution finer than a frame — measuring a pulse, timing an optical symbol — has to
use `time.ticks_ms()` or `time.ticks_us()`.

### rtc

Two synchronised clocks: the RP2350's internal one, which loses time when power
goes, and an onboard battery-backed PCF85063A that survives sleep and power loss.
The global `rtc` object talks to the latter.

```python
rtc.datetime()                 # (year, month, day, hour, minute, second, dow); also sets
rtc.set_alarm(hours=, minutes=, seconds=)
rtc.alarm_status()             # has the alarm fired
rtc.clear_alarm()
rtc.set_timer(seconds)
rtc.localtime_to_rtc()
rtc.rtc_to_localtime()
rtc.time_from_ntp()            # needs wifi; UTC only, apply your own offset
```

The RTC keeps running through sleep, so it is what wakes the badge on schedule.

---

## The MicroPython dialect

> "The syntax, control flow, functions, classes, list comprehensions, f-strings,
> exceptions — all there, all familiar."

Built in: `time`, `math`, `random`, `json`, and `machine` for GPIO, I2C, SPI, ADC
and PWM.

What is missing, and what it costs you:

- **No `threading`, no `multiprocessing`.** "Full `threading` and `multiprocessing`
  aren't really a thing." There is no way to move a blocking call off the frame
  loop — if it blocks, it blocks the app. This is why `WLAN.scan()` stalls
  `wifi_scanner` and why `share_wifi` cannot scan in a background task.
- **Single-precision floats**, so slightly less decimal accuracy than CPython.
- **8MB heap with no swap.** "Large chunks of data or a slow leak will eventually
  trip a `MemoryError`."
- **GC pauses.** "Creating objects inside a tight loop triggers garbage collection,
  which causes pauses." Preallocate buffers, and call `gc.collect()` at a moment you
  choose rather than letting it surprise you mid-frame.

---

## Development workflow

Run straight onto the device from an editor — Thonny, or VS Code with the
**MicroPico** extension. "Plug your badge in over USB, and hit run. Your code runs
live on the badge — no copying files, no reboot — so you can tweak and re-run in
seconds."

For permanent installs, mount the badge in disk mode and write to `/system`.

---

## Performance, hardware limits, app layout

Moved to [badgeware-hardware.md](badgeware-hardware.md) so there is one copy:
the `native`/`viper` tiers and allocation rules, the display panel and its response
time, sensor specs, timing floors, power and sleep, and the app folder layout.
