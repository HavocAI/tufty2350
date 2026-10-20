# Badgeware v2 API reference

Every function, method, attribute and global the **v2.0.2** firmware gives an
app -- the firmware on the badge on the desk -- with its exact behaviour and an
example of each part in use.

**Source:** this repo's git history at **`db958ce`** (2026-04-10 14:06, the last
commit on the v2.0.2 release date), extracted with
`git archive db958ce modules board`. Everything below is read from that tree
-- **[source-v2]** throughout unless marked otherwise -- with file and line
where it helps. Where the badge has confirmed it, **[verified-v2]** is added.
Confidence markers are explained in [README.md](README.md).

**How you know the badge is v2:** `os.uname().version` reads
`BW-1.27.0, on 2026-04-10` (MicroPython 1.27 -- the build bumped to 1.28 on
2026-06-20), and `display` has no `set_framerate`. Three v2 bugs in this
document have each been hit on the badge, which pins it to this build:
`wifi.status()` raising `NameError`, the alignment constants missing, and
lowercase `spritesheet` missing.

For **v3** -- this repo's current source, and the emulator -- see
[badgeware-api.md](badgeware-api.md). The table of what differs is the first
section of [../../AGENTS.md](../../AGENTS.md); the API-level differences are
gathered at the end of this file.

---

## Contents

1. [Globals](#1-globals)
2. [The loop: `run`, `launch`, `fatal_error`, `reset`](#2-the-loop)
3. [`badge`](#3-badge)
4. [Buttons and ticks: `_input`](#4-buttons-and-ticks)
5. [`display`](#5-display)
6. [`screen` and `image`](#6-screen-and-image)
7. [Text and fonts](#7-text-and-fonts)
8. [`color`](#8-color)
9. [`shape`, `brush`](#9-shape-and-brush)
10. [`vec2`, `rect`, `mat3`, `algorithm`](#10-vec2-rect-mat3-algorithm)
11. [`SpriteSheet`, `AnimatedSprite`](#11-spritesheet-and-animatedsprite)
12. [`State`, files, memory, maths helpers](#12-state-files-memory-maths)
13. [`rtc`](#13-rtc)
14. [`wifi`, `secrets`](#14-wifi-and-secrets)
15. [`powman`: sleep and wake](#15-powman)
16. [Boot, filesystems, build](#16-boot-filesystems-build)
17. [Traps: v2 bugs and undefined behaviour](#17-traps)
18. [v2 against v3](#18-v2-against-v3)

**Calling convention for every C binding** (picovector, `display`,
`powman`): **positional arguments only.** A keyword argument raises
`TypeError: function doesn't take keyword arguments` -- except in
constructors (`rect`, `vec2`, `image`, `mat3`), which *silently ignore*
keywords. Most bindings check only a minimum argument count, so a call with
too few arguments for its form can read past the end of the argument list:
undefined behaviour rather than an error (see [Traps](#17-traps)).

---

## 1. Globals

Injected into `builtins` at boot by `badgeware/__init__.py` and its
submodules. An app uses them bare. `globals()` cannot see them -- probe by
naming one and catching `NameError`.

| Name | What | Source |
|---|---|---|
| `image`, `color`, `rect`, `vec2`, `shape`, `brush`, `font`, `pixel_font`, `mat3`, `algorithm` | picovector types: every non-dunder name in `picovector.__dict__`, copied by a loop | `__init__.py:192-194` |
| `OFF`, `X2`, `X4` | `image.OFF` (0), `image.X2` (1), `image.X4` (2): antialias levels | `__init__.py:197-199` |
| `display` | the `st7789.ST7789()` driver | `__init__.py:188,202` |
| `screen` | `image(display.WIDTH, display.HEIGHT, memoryview(display))` -- **rebound by `badge.mode()`** | `badge.py:132` |
| `badge` | the `Badge()` instance | `badge.py:225` |
| `run`, `launch`, `reset`, `fatal_error` | the loop and its exits | `__init__.py:203-207` |
| `loop` | `None`, or the `run` instance while one is running | `__init__.py:205` |
| `LORES` (0b00), `HIRES` (0b01), `VSYNC` (0b10) | display modes | `badge.py:11-13` |
| `FAST_UPDATE`, `FULL_UPDATE`, `MEDIUM_UPDATE`, `DITHER` | Badger e-paper modes; do nothing on a Tufty | `badge.py:15-18` |
| `BUTTON_A`, `BUTTON_B`, `BUTTON_C`, `BUTTON_UP`, `BUTTON_DOWN`, `BUTTON_HOME` | `machine.Pin` objects | `badge.py:20-25` |
| `rom_font`, `load_font`, `text` | ROM fonts, a font loader, the text helper class | `text.py:227-229` |
| `State` | JSON state per app | `state.py:49` |
| `file_exists`, `is_dir` | filesystem tests | `filesystem.py:21-22` |
| `free` | collect and print free memory | `memory.py:23` |
| `rtc` | the real-time clock | `rtc.py:80` |
| `clamp`, `rnd`, `frnd` | maths helpers | `math.py:21-23` |
| `SpriteSheet`, `AnimatedSprite` | sprite sheets, in Python | `sprite.py:49-50` |

**Not globals:** `wifi`, `secrets`, `powman`, `easing`, `board` -- import them.
`badgeware.set_brightness`, `badgeware.get_exception`, `badgeware.message`,
`badgeware.DEFAULT_FONT` live in the `badgeware` module only. There are **no**
alignment or overflow constants (`CENTER`, `LEFT`, `MIDDLE`, `ELLIPSES`, ...)
**[verified-v2]**, and **no lowercase `spritesheet`** **[verified-v2]**.

---

## 2. The loop

### `run(update, duration=None)`

`run` is a **class** (`_run`). Calling it with one callable runs the loop at
once; `run(duration=N)(update)` works as a decorator-style call.
(`__init__.py:26-67`)

Each iteration, in order:

1. `badge.clear()` -- fill with `badge.default_clear`, unless that is `None`;
   then `screen.pen = badge.default_pen`.
2. `result = update()` -- **if `result` is not `None`, store it in
   `.result` and return at once, without presenting this frame.**
3. `display.update()` -- present (waits for vsync, then writes the panel).
4. `badge.poll()` -- read the buttons, advance `badge.ticks`.
5. If `duration` was given and `.ticks >= duration`, return.

Before the first iteration it polls once and records `start = badge.ticks`. Any
exception in `update()` goes to `fatal_error("Error!", ...)`, which resets the
badge. On exit it clears the screen and restores the previous `loop`.

| Member | | |
|---|---|---|
| `.ticks` | property | ms since the loop started (`badge.ticks - start`) |
| `.progress` | property | `ticks / duration`, unclamped; `0` with no duration |
| `.result` | attribute | what `update()` returned to end the loop |

`run(...)` returns the `_run` instance, not the result -- read `.result`.

```python
def update():
    screen.pen = color.white
    screen.text("Hello", 10, 10)
    if badge.pressed(BUTTON_B):
        return "bye"            # quits: this frame is never shown

r = run(update)
print(r.result)                 # "bye"
```

### `launch(path)` (`__init__.py:70-109`)

How the menu starts an app: installs a HOME `IRQ_FALLING` handler that calls
the app's `on_exit()` (if any) and then `reset()`; `os.chdir(path)`;
`sys.path.insert(0, path)`; `__import__(path)`. The app may block in its
import -- `run(update)` at module level does. Returns the app's `on_exit()`
result, if any. Exceptions go to `fatal_error`. Afterwards it unloads every
module the app imported and runs `gc.collect()`.

**HOME resets the badge** (`machine.reset()`), so nothing an app changes --
modes, `default_clear`, the backlight -- survives into the launcher.

### `fatal_error(title, error)` and `reset()`

`fatal_error` prints `- ERROR: ...`, switches to `HIRES` (dropping `VSYNC`)
and scales the old screen up if it was `LORES`, draws a message box, waits for
any button, then `machine.reset()`. It never returns. `reset()` waits for HOME
to be released (HOME is also BOOT), then `machine.reset()`.

Firmware helpers that call `fatal_error` instead of raising:
`secrets.require()`, `wifi.connect()` with no credentials, and `wifi.tick()`
once retries run out.

---

## 3. `badge`

`badge.py`. Module constants (not globals): `MODEL` ("tufty"), `UID` (hex
`unique_id`), `LIGHT_SENSOR` (an `ADC` on `LIGHT_SENSE`).

| Member | Signature | Returns | Behaviour |
|---|---|---|---|
| `default_clear` | attribute | `color` or `None` | what `clear()` fills with; `None` skips the fill. Black at boot |
| `default_pen` | attribute | `color` | the pen `clear()` leaves set. White at boot |
| `model`, `uid` | attributes | str | |
| `ticks` | property | int ms | `_input.ticks`: the clock **at the last poll**, frozen between polls |
| `ticks_delta` | property | int ms | ms between the last two polls |
| `poll()` | | None | read buttons, advance `ticks` (`_input.poll()`) |
| `resolution` | property | `(w, h)` | `screen.width, screen.height` |
| `clear()` | | True | `screen.pen = default_clear; screen.clear()` (if not None), then `screen.pen = default_pen` |
| `update()` | | True | `display.update()`, `clear()`, `poll()` -- for a manual loop; **never inside `run()`** |
| `mode(m=None)` | | current mode, or None | With `m`: if unchanged, nothing. Otherwise `display.fullres(m & HIRES)`, `display.set_vsync(m & VSYNC)`, and **rebinds `builtins.screen`** to a new `image` over the framebuffer, keeping its `font` and `pen`. **No `set_framerate`**: the panel stays at 60Hz **[measured-v2]** |
| `light_level()` | | int 0-65535 | raw `ADC.read_u16()`; a 12-bit conversion, so multiples of 16 **[measured-all]** |
| `pressed(b=None)`, `held(b=None)`, `released(b=None)`, `changed(b=None)` | | bool, or tuple | see [Buttons](#4-buttons-and-ticks) |
| `caselights(*v)` | one value, or four | list of 4 | the rear LEDs, 0.0-1.0, duty `int(v**2.2 * 65535)` at 500Hz. One argument sets all four; **fewer than four (but more than one) raises `IndexError`**. No arguments: returns the current values |
| `battery_voltage()` | | float V | 10-sample mean, against the 1.1V reference |
| `battery_level()` | | int 0-100 | from the voltage curve |
| `usb_connected()` | | bool | VBUS present |
| `is_charging()` | | bool | False without USB |
| `disk_free(mountpoint="/system")` | | `(total, used, free)` bytes | `os.statvfs` |
| `sleep(duration=None)` | | never returns | `powman.goto_dormant_for(duration)` (timer wake only) if given, else `powman.sleep()` (buttons or RTC alarm). Waking is a reboot |
| `wake_reason()` | | int | `powman.get_wake_reason()` |
| `woken_by_button()` | | bool | reason is a front button |
| `pressed_to_wake(b)` | | bool | `b` was held at wake |
| `woken_by_reset()` | | bool | **compares with 255 (`WAKE_UNKNOWN`), not `WAKE_RESET`** -- a v2 bug |

```python
badge.mode(HIRES | VSYNC)           # an app wanting 320x240 must ask
badge.caselights(0.5)               # all four rear LEDs at half
print(badge.light_level(), badge.battery_level())
```

---

## 4. Buttons and ticks

The C module `_input` (`picovector/micropython/input.cpp`), wrapped by
`badge`. `badge.poll()` -- which `run()` and `badge.update()` call once a
frame -- samples the six buttons (active low) and the millisecond clock.

| `_input` name | | |
|---|---|---|
| `poll()` | function | as above. **The first poll also ORs in the buttons held at wake**, so a wake button reads pressed on the first frame |
| `ticks` | int ms | `mp_hal_ticks_ms()` at the last poll |
| `ticks_delta` | int ms | between the last two polls; the whole uptime on the first |
| `held` | tuple of `Pin` | down now |
| `pressed` | tuple of `Pin` | went down at the last poll |
| `released` | tuple of `Pin` | went up at the last poll |
| `changed` | tuple of `Pin` | changed at the last poll |

The tuples are in the order HOME, A, B, C, UP, DOWN, and a new tuple is built
on every read. `badge.pressed(BUTTON_A)` is `BUTTON_A in _input.pressed`;
`badge.pressed()` with no argument returns the tuple, truthy if anything was
pressed. Edge detection is already done -- `pressed` fires once per press.

**`badge.ticks` does not move inside a frame.** Every read between two polls
returns the same value. A deadline tested after the frame is presented reads a
newer clock than the code that drew it. For sub-frame timing use
`time.ticks_ms()` / `time.ticks_us()`.

```python
def update():
    if badge.pressed(BUTTON_A):       # once per press
        start()
    if badge.held(BUTTON_UP):         # every frame while down
        scroll(-1)
    print(badge.ticks_delta)          # this frame's length, ms
```

---

## 5. `display`

`st7789.ST7789()`, a singleton (`modules/c/st7789/`).

| Member | Signature | Behaviour |
|---|---|---|
| `update()` | | Waits for the previous frame's DMA; **if vsync is on, busy-waits while the panel's TE line is low** (a level, not an edge); then writes the whole frame column by column, converting RGB888 to RGB565 (LORES pixel-doubled). Returns with the last column still in flight. Blocks about 12.4 ms at LORES when called mid-frame -- the wait plus a 7.8 ms write **[measured-v2]** |
| `backlight(f)` | float 0.0-1.0 | PWM duty `(int(f*255)/255) ** 2.8`, **no floor**: 0.25 is 2%, 0.5 14%, 0.74 43%, 1.0 100%. The backlight is dark below about 0.45 **[measured-v2]**. Out-of-range values are undefined. No getter: there is no way to read the level back. Init level is 230/255 (~75% duty) |
| `set_vsync(b)` | bool | `badge.mode()` sets it from `VSYNC` |
| `fullres(b)` | bool | 320x240 or 160x120; `badge.mode()` calls it |
| `command(reg, data)` | int, tuple of ints | raw ST7789 command; a non-tuple `data` sends none |
| `set_max_pio_clock(hz)` | int | default 44MHz |
| `WIDTH`, `HEIGHT` | read | 320x240 or 160x120 |
| buffer protocol | `memoryview(display)` | the static 320x240x4-byte framebuffer; LORES uses the first 160x120 words |

**`set_framerate` does not exist** **[verified-v2]**: the panel keeps its
init rate, 60Hz (16.7 ms) **[measured-v2]**. Nor do `set_mode` or `speed`.

```python
display.backlight(1.0)          # full; remember to put it back (230/255)
display.update()                # present now, from inside update() -- the
                                # loop's own present follows
```

---

## 6. `screen` and `image`

`screen` is an `image` over the display's framebuffer. Every `image` method
below works on `screen`.

### Constructing and loading

| Call | Returns | Behaviour |
|---|---|---|
| `image(w, h)` | image | a new RGBA8888 image, on the heap |
| `image(w, h, buffer)` | image | over a writable buffer, in place; no size check |
| `image.load(path_or_bytes, [w], [h])` | image | **PNG or JPEG only.** A str is a path; bytes are decoded directly. Tries PNG, then JPEG. `w`/`h` scale **down** only (larger is clamped to the source), one of them 0 keeps the aspect. Indexed PNGs load as palette images. Errors: `ValueError("unable to read image! N")` |
| `img.load_into(path_or_bytes)` | None | decode into an existing image at its size |
| `img.window(rect)` / `img.window(x, y, w, h)` | image | a **view sharing pixels**, clipped to the parent. `pen` and `font` read back as `None` on the view |

**There is no GIF decoder and no `save`.** Any file that is not PNG or JPEG
fails the PNG check, then the JPEG check, and raises **`ValueError: unable to
read image! 4`** -- the error the `rampage` GIF hit on the badge
**[verified-v2]**. On v2 that error means "not a PNG or JPEG", not "too big".

PNG limits: interlaced and 16-bit PNGs fail (code 5); rows over 8192 bytes
(about 2048 px of RGBA) fail (7); grey-plus-alpha PNGs decode to garbage
without an error; 1-bit greyscale decodes to nothing. Decoders work in a
static 70KB buffer. JPEGs much larger than the target are decoded at 1/2, 1/4
or 1/8 scale.

### Drawing

All drawing uses `img.pen` and is clipped to `img.clip`, except `blit`.

| Method | Forms | Behaviour |
|---|---|---|
| `clear()` | | fills the **clip rect** with the current pen. With no pen ever set, does nothing |
| `rectangle(...)` | `(rect)`, `(x, y, w, h)` | filled |
| `line(...)` | `(vec2, vec2)`, `(x1, y1, x2, y2)` | 1 px, integer coordinates |
| `circle(...)` | `(vec2, r)`, `(x, y, r)` | filled; `r` truncated to int |
| `triangle(...)` | `(v1, v2, v3)`, `(x1, y1, ..., y3)` | filled |
| `put(...)` | `(x, y)`, `(vec2)` | one pixel |
| `get(...)` | `(x, y)`, `(vec2)` | a new `color`; coordinates clamped into the clip |
| `shape(s)` | `(shape)`, `([shapes])` | renders vector shapes with the pen (see [shape](#9-shape-and-brush)) |
| `text(...)` | see [Text](#7-text-and-fonts) | |
| `blit(src, ...)` | `(vec2)`, `(x, y)` **floats only**, `(rect)`, `(src_rect, dst_rect)` | copy `src` in. A `rect` scales (nearest neighbour); a negative destination width or height flips. **Ignores `clip`** -- clips to the image bounds only. **`blit(img, 10, 20)` with ints raises `TypeError`**: use `vec2(10, 20)` or floats |
| `blit_hspan`, `blit_vspan` | `(src, vec2, count, uv0, uv1)` or 8 scalars | one textured row or column, sampled from `src` along uv0 to uv1 |
| `blur(radius)` | float | in place |
| `dither()` | | 4x4 ordered dither to four greys |
| `onebit()` | | luminance over 128 to white, else black |
| `monochrome()` | | to greyscale |
| `batch(commands)` | list of tuples | runs `("rectangle", 10, 10, 50, 50)`-style commands in one call; a one-parameter tuple like `("pen", c)` sets an attribute. Every entry must be a **tuple**; `onebit`, `monochrome`, `get`, `measure_text`, `window` are not allowed |

### Attributes

| Attribute | R/W | |
|---|---|---|
| `width`, `height` | R | |
| `clip` | R/W | a `rect`; a copy on read; intersected with the bounds on write. **Set** a new rect -- changing the copy changes nothing |
| `pen` | R/W | set a `color` or a `brush`; reads back as a `brush`, or `None` if never set. **Ints are not accepted** |
| `font` | R/W | a `pixel_font` or a vector `font`; `None` is refused |
| `alpha` | R/W | 0-255 global opacity; 256 wraps to 0 |
| `antialias` | R/W | `OFF`/`X2`/`X4` -- **stored but apparently unused** by this build's rasteriser |
| `has_palette` | R | |
| `raw` | R | the pixel buffer as a `bytearray`, by reference |

**Assigning `width`, `height` or `has_palette` does not raise -- it sets
`antialias`**, a fall-through in the attribute handler.

```python
screen.pen = color.black            # pen first: clear() fills with the pen
screen.clear()
screen.pen = color.white
screen.rectangle(10, 10, 60, 20)
screen.circle(vec2(100, 60), 15)

logo = image.load("assets/logo.png")
screen.blit(logo, vec2(4, 4))                          # unscaled
screen.blit(logo, rect(0, 0, 64, 64))                  # scaled to 64x64
screen.blit(logo, rect(0, 0, 16, 16), rect(80, 0, 32, 32))   # part, scaled

old = screen.clip
screen.clip = rect(0, 0, 80, 120)   # draw into the left half only
screen.rectangle(0, 0, 320, 240)
screen.clip = old
```

---

## 7. Text and fonts

### `img.text(str, x, y, [size])` and `img.text(str, vec2, [size])`

Draws one line at the top-left point with the image's `font` and `pen`.
**That is all it does on v2:**

- **No `rect` form, no wrapping, no `"\n"` handling, no `align=`, no
  `overflow=`.** A `rect` where the point goes raises `TypeError: can't convert
  rect to float`. Wrap and place text yourself -- or use `text.draw()` below.
- **No font set:** `OSError("target image has no font")`.
- `size` scales a **vector** font (default 12) and is **ignored** for a pixel
  font.
- Pixel font: each glyph advances its width + 1; a space advances
  `font width // 3`. Drawn only where it meets the clip.

### `img.measure_text(str)` (pixel font), `img.measure_text(str, size)` (vector font)

Returns `(w, h)` as **floats**. `h` is the font height (pixel) or `size`
(vector). It looks glyphs up **byte by byte, not as UTF-8**, so non-ASCII text
measures wrong. A pixel font's width includes a trailing 1 px gap.
**Omitting `size` with a vector font is undefined behaviour**, not an error.

### Fonts

| Name | | |
|---|---|---|
| `rom_font.<name>` | global | a `pixel_font` loaded from `/rom/fonts/<name>.ppf` and cached. Missing: `AttributeError`, so `getattr(rom_font, "x", None)` is safe. `dir(rom_font)` lists them. **This is how v2 names a ROM font** |
| `font.<name>` | | **does not exist on v2** -- `font` is the vector font type, with `load` only. `getattr(font, "smart", None)` is `None` |
| `pixel_font.load(path)` | | a `.ppf` (`ppf!` header) |
| `font.load(path)` | | a vector `.af` (`af!?` header) |
| `load_font(name)` | global | `rom_font.<name>` first, then searches `/rom/fonts`, `/system/assets/fonts`, `/fonts`, `/assets` and `""` for `.af` then `.ppf`. Not found: `OSError('Font "x" not found!')` |
| `pixel_font.height`, `pixel_font.name` | R | there is no width attribute |

The default font is `rom_font.sins` (`badge.mode()` falls back to it).

```python
screen.font = rom_font.smart
w, h = screen.measure_text("Hello")         # floats
screen.text("Hello", (screen.width - w) / 2, 10)     # centred by hand
```

### The `text` helper (`text.py`)

| Call | Returns | Behaviour |
|---|---|---|
| `text.draw(img, str, bounds=None, line_spacing=1, word_spacing=1, size=24)` | `rect` used | **Word-wraps** within `bounds` (default: the whole image) and clips to it. A space is `font height / 3 * word_spacing`. A word wider than the box is not broken. Inline `[pen:r,g,b]` markup changes colour mid-text |
| `text.scroll(str, font_face=None, font_size=None, target=None, speed=25, gap=None, align="middle")` | an update function | a marquee, `speed` px/s. **The returned function returns its progress (a float), so passing it straight to `run()` quits after one frame** -- call it from your own `update()` instead |
| `text.tokenise(img, str, glyph_renderers=None, size=24)` | tokens | what `draw` lays out |

```python
def update():
    screen.pen = color.white
    text.draw(screen, "A message long enough to wrap onto a second line.",
              rect(10, 10, 140, 60))

ticker = text.scroll("Breaking news ...", speed=40)
def update():
    ticker()                        # its progress is ignored here
```

---

## 8. `color`

Not constructible: `color()` raises. Make one with a factory or use a named
one.

| Call | Behaviour |
|---|---|
| `color.rgb(r, g, b, [a=255])` | 0-255 each. Out-of-range values **wrap** (256 is 0), not clamp |
| `color.hsv(h, s, v, [a=255])` | all 0-255 -- **hue too** (0-255 maps to 0-360 degrees) |
| `color.oklch(l, c, h, [a=255])` | 0-255 each |
| `c.p` | the premultiplied pixel word, read only. There is no `.r`/`.g`/`.b`/`.a` |

`blend`, `darken` and `lighten` do not exist in v2.

**Named colours** -- the badge build defines `TUFTY=1`, so **black and white
are tinted, not pure**:

| Name | RGB | | Name | RGB |
|---|---|---|---|---|
| `color.black` | **20, 30, 40** | | `color.white` | **222, 238, 214** |
| `grape` | 68, 36, 52 | | `navy` | 48, 52, 109 |
| `grey` | 78, 74, 78 | | `brown` | 133, 76, 48 |
| `green` | 52, 101, 36 | | `red` | 208, 70, 72 |
| `taupe` | 117, 113, 97 | | `blue` | 89, 125, 206 |
| `orange` | 210, 125, 44 | | `smoke` | 133, 149, 161 |
| `lime` | 109, 170, 44 | | `latte` | 210, 170, 153 |
| `cyan` | 109, 194, 202 | | `yellow` | 218, 212, 94 |
| `light_grey` | 192, 192, 192 | | `dark_grey` | 64, 64, 64 |
| `transparent` | 0, 0, 0, alpha 0 | | | |

For true black or white use `color.rgb(0, 0, 0)` and
`color.rgb(255, 255, 255)`.

```python
screen.pen = color.rgb(255, 0, 0, 128)      # half-transparent red
screen.pen = color.hsv(170, 255, 255)       # 170 of 255 is about 240 degrees: blue
```

---

## 9. `shape` and `brush`

### `shape` -- vector shapes, drawn with `img.shape(s)`

Not constructible: use the factories. All angles are in **degrees**, with
**0 straight up**, clockwise (y points down).

| Factory | Notes |
|---|---|
| `shape.rectangle(x, y, w, h)`, `shape.rectangle(rect)` | |
| `shape.rounded_rectangle(x, y, w, h, r1, [r2, r3, r4])`, `(rect, r1, ...)` | corners top-left, top-right, bottom-right, bottom-left; missing radii copy `r1` |
| `shape.circle(x, y, r)`, `(vec2, r)` | a 32-sided polygon |
| `shape.regular_polygon(x, y, r, sides)`, `(vec2, r, sides)` | first vertex straight down |
| `shape.star(x, y, spikes, outer_r, inner_r)`, `(vec2, ...)` | |
| `shape.squircle(x, y, size, [n=4.0])`, `(vec2, ...)` | `n` at least 2 |
| `shape.arc(x, y, inner_r, outer_r, from_deg, to_deg)` | a ring segment; equal angles give NaN points |
| `shape.pie(x, y, r, from_deg, to_deg)` | |
| `shape.line(x1, y1, x2, y2, width)`, `(vec2, vec2, width)` | a quad, no end caps |
| `shape.custom([vec2, ...], [vec2, ...], ...)` | one polygon per **list** of `vec2`; tuples are refused |

| Member | |
|---|---|
| `s.stroke(width)` | turns the shape into an outline band of that width, **in place**, and returns it |
| `s.transform` | get: **a copy** of the `mat3`; set: a `mat3`. `s.transform.rotate(10)` changes the copy only -- build the matrix and assign it |

Shapes take the image's `pen`; they have no colour of their own.

```python
card = shape.rounded_rectangle(0, 0, 140, 100, 7)
edge = shape.rounded_rectangle(0, 0, 140, 100, 7).stroke(2)
m = mat3().translate(90, 70).rotate(15)
card.transform = m
edge.transform = m
screen.pen = color.navy
screen.shape(card)
screen.pen = color.white
screen.shape(edge)
```

### `brush` -- patterned and image pens

Not constructible. A plain `color` assigned to `pen` becomes a solid brush by
itself.

| Factory | Notes |
|---|---|
| `brush.pattern(c1, c2, index)` | built-in 8x8 pattern 0-37 |
| `brush.pattern(c1, c2, (r0, ..., r7))` | a **tuple** of 8 row bytes, MSB leftmost: set bits draw `c1`, clear `c2` |
| `brush.image(img, transform)` | the image, tiled. **Always pass the `mat3`**: with one argument the binding reads past its arguments |

```python
screen.pen = brush.pattern(color.white, color.black, 5)
screen.rectangle(0, 0, 80, 60)
```

---

## 10. `vec2`, `rect`, `mat3`, `algorithm`

### `vec2(x, y)` / `vec2()`

Floats. `.x`, `.y` read/write. `+ -` with a `vec2`; `* /` with a `vec2`
(component-wise) or a number; `==`. **`+=` and friends build a new `vec2`**
(there are no in-place operators); `2 * v` and `-v` are unsupported.
`v.transform(m)` transforms in place and returns `None`. A bad constructor
call raises `OSError`.

### `rect(x, y, w, h)` / `rect(other)` / `rect()`

Floats. `.x`/`.l`, `.y`/`.t`, `.w`, `.h` read/write; `.r` and `.b` read the
right and bottom edges and **set `w` and `h`**.

| Method | |
|---|---|
| `inflate(a, [b=a])`, `deflate(a, [b=a])` | grow or shrink on every side; **mutate and return self** |
| `offset(x, y)`, `offset(vec2)` | move, **truncated to whole pixels**; mutates and returns self |
| `intersection(r)` | a new rect; `rect(0, 0, 0, 0)` if they do not overlap |
| `intersects(r)` | bool; touching edges do not count |
| `contains(vec2)`, `contains(rect)` | bool |
| `empty()` | `w == 0 or h == 0` |

`==` compares identity, not contents.

### `mat3()`

An identity matrix. **Every method changes the matrix it is called on** and
also returns a copy; transforms compose by post-multiplying, so
`mat3().translate(...).rotate(...)` rotates points first, then moves them.

| Method | |
|---|---|
| `rotate(degrees)`, `rotate_radians(r)` | |
| `translate(x, y)`, `translate(vec2)` | |
| `scale(sx, [sy=sx])` | |
| `multiply(m)`, `m1 * m2` | `*` also changes `m1` |
| `inverse(_)` | **needs a dummy argument**: `m.inverse()` raises `TypeError` -- write `m.inverse(0)` |

The elements cannot be read.

### `algorithm`

| Function | |
|---|---|
| `algorithm.clip_line(p1, p2, rect)` | Cohen-Sutherland: True if any of the segment is inside, and **moves `p1`/`p2` in place** to the clipped ends |
| `algorithm.dda(origin, angle_rad, depth)` | grid cells along a ray: a list of `(hit, cell, edge, offset, distance)` |
| `algorithm.raycast(pos, angle, fov, rays, max, map, width, height, screen_width)` | a raycaster over a writable `bytearray` map; **pass all nine arguments** -- fewer is undefined |

---

## 11. `SpriteSheet` and `AnimatedSprite`

Pure Python over `image.load` and `image.window` (`sprite.py`). **These exist
on v2** -- it is the lowercase `spritesheet` global and v3's
`image.spritesheet()` that do not **[verified-v2]** for both lowercase forms.
`SpriteSheet` itself has not been run on the badge, but every bundled v2 app
with sprites uses it.

| Member | |
|---|---|
| `SpriteSheet(path, columns, rows)` | loads the image and pre-builds a window per cell |
| `.sprite(x, y)` | the cell's `image` window, for `blit` |
| `.animation(x=0, y=0, count=None, horizontal=True)` | an `AnimatedSprite`; `count` defaults to the column count |
| `.image`, `.sw`, `.sh`, `.sprites` | the sheet, cell size, cells |
| `AnimatedSprite.frame(i=0)` | the `i`th frame, wrapping |
| `AnimatedSprite.count()` | the frame count -- **a method** |

```python
bee = SpriteSheet("assets/bee.png", 4, 1)
fly = bee.animation()
def update():
    frame = fly.frame(badge.ticks // 100)
    screen.blit(frame, vec2(60, 40))
```

---

## 12. `State`, files, memory, maths

### `State` -- one JSON file per name, `/state/<name>.json`

| Call | Returns | Behaviour |
|---|---|---|
| `State.load(name, defaults)` | **bool** | if the file holds a JSON object, **updates `defaults` in place** and returns True. Missing, bad JSON or not an object: **saves `defaults` to the file** and returns False. Read the dict you passed, not the return value |
| `State.save(name, data)` | None | writes `json.dumps(data)`. Creates `/state` if missing. **Any other `OSError` is swallowed**, so a failed write looks like a saved one; an unserialisable value raises `TypeError` and leaves an empty file |
| `State.modify(name, data)` | None | load, update, save |
| `State.delete(name)` | None | errors swallowed |

```python
config = {"name": "Anon", "theme": 0}
State.load("myapp", config)         # config now holds what was saved
config["theme"] = 1
State.save("myapp", config)
```

### Files, memory, maths

| Global | |
|---|---|
| `file_exists(path)` | bool |
| `is_dir(path)` | truthy `0x4000`, `0`, or `False` -- not a strict bool |
| `free(message="")` | `gc.collect()`, then prints free KB and the change since the last call |
| `clamp(v, lo, hi)` | |
| `rnd(a, [b])` | `randint(a, b)`, or `randint(0, a)`. **`b=0` counts as absent** |
| `frnd(a, [b])` | the same with `uniform` |

`easing` (import it) has `linear` and `easeIn`/`easeOut`/`easeInOut` x `Quad,
Cubic, Quart, Quint, Sine, Expo, Circ, Back, Elastic, Bounce`, each a function
of `x` in 0-1.

---

## 13. `rtc`

A PCF85063A on I2C, synced with `machine.RTC` at boot: whichever has a year of
2025 or later wins.

| Member | |
|---|---|
| `rtc.datetime(dt=None)` | get or set `(year, month, day, hour, minute, second, weekday)` |
| `rtc.time_from_ntp()` | set the time over the network (needs wifi); raises on failure |
| `rtc.localtime_to_rtc()`, `rtc.rtc_to_localtime()` | sync one way |
| `rtc.set_alarm(hours=0, minutes=0, seconds=0)` | an alarm **that far from now** (relative) |
| `rtc.clear_alarm()`, `rtc.alarm_status()` | |
| `rtc.set_timer(ticks, enable_interrupt=True)`, `rtc.timer_elapsed()` | the countdown timer; reading clears the flag |

```python
rtc.set_alarm(minutes=30)
badge.sleep()                       # wakes on the alarm or a button -- a reboot
```

---

## 14. `wifi` and `secrets`

### `import wifi` -- not a global (`modules/common/wifi.py`)

| Call | Returns | Behaviour |
|---|---|---|
| `wifi.connect(ssid=None, psk=None, timeout=60, retries=5)` | bool | Non-blocking: starts a connection and returns `False`; **call it every frame** until it returns `True`. With no arguments uses `secrets.WIFI_SSID`/`WIFI_PASSWORD`, and an empty SSID is a `fatal_error` (reset). **Once started, later calls ignore new credentials** and just report `isconnected()` -- `wifi.disconnect()` first to change network. An empty `psk` joins an open network |
| `wifi.tick()` | bool | the timeout and retries live here, not in `connect()`: on timeout or failure it retries, and **when retries run out it calls `fatal_error` -- a reset** |
| `wifi.is_connected()` | bool | |
| `wifi.disconnect()` | None | and deactivates the radio |
| `wifi.status()` | -- | **always raises `NameError: name 'wifi' isn't defined`** -- a v2 bug **[verified-v2]**. Use `wifi.wlan.status()` or `is_connected()` |
| `wifi.get_status(code)` | str | the text for a status code; `NameError` before `connect()`, `KeyError` for an unknown code |
| `wifi.ip()`/`ipv4()`, `ipv6()`, `subnet()`, `gateway()`, `nameserver()` | str or None | |
| `wifi.wlan` | `network.WLAN` or None | the interface, once `connect()` has run |

Status codes: 0 Idle, 1 Connecting, 2 Connected, 3 Got IP, -1 failed, -2 no
access point, -3 wrong password.

**WEP cannot be joined** and the security field of `WLAN.scan()` is the
driver's bitmask, not 0-5 -- both from the cyw43 driver, read in the v3
source and assumed the same on v2 **[inferred-all]**; see
[badgeware-api.md](badgeware-api.md).

```python
import wifi

def update():
    if wifi.connect("HomeNet", "hunter2!!"):      # every frame
        screen.text("Connected: " + wifi.ip(), 10, 10)
    else:
        screen.text("Connecting...", 10, 10)
```

### `import secrets`

Imports `/secrets.py` (LittleFS) first, then `/system/secrets.py` (the FAT
volume); every non-dunder name in it becomes an attribute. With neither,
`ImportError`. **`secrets.require(*keys)` calls `fatal_error` -- a reset --
for any key that is missing, `None` or empty**, so test them yourself.

```python
import secrets
ssid = getattr(secrets, "WIFI_SSID", "")
```

---

## 15. `powman`

Sleep, wake and power (`modules/c/powman/`). Waking from any sleep is a
**reboot**: the app starts again from the launcher.

| Call | |
|---|---|
| `powman.sleep([timeout_s])` | off until a front button, the RTC alarm, or the timeout (whole seconds) |
| `powman.goto_dormant_for(seconds)` | off for that long; **timer wake only** |
| `powman.goto_dormant_until(rtc_tuple)` | off until a time; the powman clock it compares against is fixed at boot, so this is unreliable |
| `powman.get_wake_reason()` | one of the `WAKE_*` constants |
| `powman.get_wake_buttons()` | the buttons held at wake, as `Pin`s |
| `powman.shipping_mode()` | off until RESET |

Constants: `WAKE_BUTTON_A` 0, `WAKE_BUTTON_B` 1, `WAKE_BUTTON_C` 2,
`WAKE_BUTTON_UP` 3, `WAKE_BUTTON_DOWN` 4, `WAKE_DOUBLETAP` 5, `WAKE_USER_SW`
6, `WAKE_VBUS_DETECT` 7, `WAKE_RTC` 8, `WAKE_ALARM` 0xf0, `WAKE_RESET` 0xf1,
`WAKE_WATCHDOG` 0xf2, `WAKE_UNKNOWN` 0xff.

A double tap of RESET wakes into USB disk mode (`WAKE_DOUBLETAP`). Holding
RESET plays a case-light animation and powers off -- into shipping mode if UP
and DOWN are held too.

---

## 16. Boot, filesystems, build

1. **`_boot_fat.py`**: LittleFS on the last 1MB of user flash, mounted at
   **`/`, read-write** (formatted if it will not mount). The FAT volume on
   the rest is mounted at **`/system`, read-only**. Disk mode exposes the FAT
   volume -- so **nothing an app writes, `/state` included, is visible over
   USB**; get it off with `mpremote` or by printing it.
2. **`boot.py`**: C++ allocations to the MicroPython heap, then `import
   badgeware` -- the display, the globals, the RTC sync, and
   `badge.mode(LORES | VSYNC)`.
3. **`main.py`**: a double-tap wake goes into disk mode; otherwise it runs
   the launcher, `/system/main.py`.

Fonts are in ROMFS at `/rom/fonts`. MicroPython 1.27, the C object
representation `MICROPY_OBJ_REPR_A`, under which **every float is a heap
object**, boxed on each arithmetic result, single precision (32-bit)
**[source-v2, `py/objfloat.c`]** -- an earlier note here said floats were
box-free; the GC heap
in 8MB of PSRAM with **16-byte blocks** -- so any object bigger than one
block, a 4-tuple or a `rect`, is slow to allocate: 65-145 us, varying run to
run, against 2.9 us for a pair **[measured-v2]**. No threads. Frozen in: networking, `urllib.urequest`,
`umqtt.simple`, `aioble`, `datetime`, `asyncio`.

---

## 17. Traps

v2 bugs and undefined behaviour, from the source. None of these raises the
error you might expect.

| Call | What happens |
|---|---|
| `wifi.status()` | always `NameError` **[verified-v2]** |
| `blit(img, 10, 20)` (ints) | `TypeError` -- only `vec2` or floats work |
| `img.width = 5` | sets `antialias`, no error |
| `mat3.inverse()` | `TypeError`: needs a dummy argument |
| `m1 * m2`, any `mat3` method | changes `m1` |
| `s.transform.rotate(10)` | changes a copy, not the shape |
| `brush.image(img)` | reads past its arguments: pass a `mat3` |
| `measure_text(s)` with a vector font | undefined: `size` is required |
| `text.scroll(...)` passed to `run()` | quits after one frame |
| `badge.woken_by_reset()` | compares against `WAKE_UNKNOWN` |
| `rnd(5, 0)` | `randint(0, 5)`: 0 counts as no argument |
| `color.rgb(300, 0, 0)` | wraps to 44, not clamped |
| `State.save` failing | silent, unless `/state` is missing |
| `caselights(a, b)` | `IndexError`: one value or four |
| `display.backlight(0.3)` | under 4% duty: dark **[measured-v2]** |
| any image that is not PNG or JPEG | `ValueError: unable to read image! 4` |
| `image.get()` | allocates outside the GC heap every call -- avoid in loops |
| too few arguments to `window`, `text`, `image()`, `squircle`, `rounded_rectangle`, `stroke`, `translate` | reads past the arguments: undefined |

---

## 18. v2 against v3

What an app written against the v3 docs or emulator meets on this badge.

| | v2.0.2 (this file) | v3 ([badgeware-api.md](badgeware-api.md)) |
|---|---|---|
| Text in a box | `img.text(str, x, y)` only; `text.draw(img, str, rect)` wraps | `screen.text(str, rect, align=..., overflow=...)` lays out natively |
| Alignment, overflow constants | none | `LEFT`, `CENTER`, `RIGHT`, `TOP`, `MIDDLE`, `BOTTOM`, `CLIP`, `ELLIPSES` |
| Named ROM fonts | `rom_font.smart` | `font.smart`; `rom_font` gone |
| Sprites | `SpriteSheet(path, cols, rows)`, `AnimatedSprite` | `image.spritesheet(cols, rows)` |
| GIF | not decoded at all | decoded into a sheet, within limits |
| `display.set_framerate` | absent; 60Hz | present; `badge.mode()` sets 90Hz |
| `display.backlight` | `v^2.8`, no floor | `v^2.8` over a 12.5% floor |
| `wifi.status()` | raises `NameError` | works |
| GC blocks | 16 bytes | 32 bytes |
| MicroPython | 1.27 | 1.28 |

**Write for both**: probe for v3 names with a bare-name lookup in a `try`
(`CENTER`, `font.smart` via `getattr`), keep a path that works without them,
pass `vec2` to `blit`, and use `wifi.is_connected()` rather than
`wifi.status()`. `share_wifi`'s `draw_text()` and `pick_font()` are worked
examples.
