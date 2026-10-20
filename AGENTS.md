# Working in this repo

A fork of the Tufty 2350 firmware (Badgeware, MicroPython) with our own apps
alongside the stock ones. Each folder in `firmware/apps/` is one app:
`__init__.py` is the entry point, `icon.png` is the launcher icon.

```
firmware/apps/<app>/  one app -- __init__.py, icon.png, its assets
modules/              the firmware's own Python and C modules
tests/                host-only: the harness and runner, shared by every app
  <app>/              one app's tests, and any tools only it needs
.agents/              notes for agents; start at .agents/README.md
  docs/               the badge and the test harness: reference notes
  optical_data_transfer/  the protocol: spec, reasons, tests, data
  share_wifi/         the app: its spec and the briefing
  tasks/              one file per piece of work; the protocol's in tasks/optical_data_transfer/
```

**Tests mirror the apps**, in `tests/<app>/` with the same name as
`firmware/apps/<app>/`. They stay out of `firmware/` because everything under it
ends up on a badge — see Building. Only what more than one app uses sits
directly in `tests/` — the harness, the runner.

## Building

Apps are installed by copying the app folder onto the badge in USB disk mode —
see [APPS.md](APPS.md).

The firmware build packs the whole of `firmware/` into the FAT image of the
`-with-filesystem` UF2 (`PIMORONI_FATFS_DIR` in
[board/mpconfigboard.cmake](board/mpconfigboard.cmake)). There is no exclusion
list: anything put under `firmware/` ships, which is why host-only code lives
elsewhere.

CI lints all of `firmware/` with [ci/ruff.toml](ci/ruff.toml), which is set up
for MicroPython's injected globals; `tests/` is CPython and is not linted. Ruff does not know what MicroPython lacks, so
three things CPython accepts and this badge does not still need watching by
eye: `**` unpacking in a call, `%r`, and `global` after the name has been used.
Every one of those is a bug already shipped to hardware.

**Read [.agents/docs/badgeware-patterns.md](.agents/docs/badgeware-patterns.md) before writing
badge code.** It is the how-to: app skeleton, loop shapes, menus, scrolling, text
layout, blocking calls, and the traps that have already cost us time. The rest of
[.agents/docs/](.agents/docs) is reference — start at [.agents/docs/README.md](.agents/docs/README.md), which says
which file answers which question and how confident each claim is.
The API is small but unusual, and guessing at it wastes a hardware round-trip.
Official docs: <https://badgewa.re/docs>. If you learn something those files don't
cover, or that contradicts them, update them in the same change.

## Two firmwares: the badge runs v2, this repo is v3

**The badge on the desk runs v2.0.2** -- `BW-1.27.0, on 2026-04-10`, read off
it by the `frame_probe` subtool (`frame_probe.firmware.version`).
**This repo's firmware source** -- [modules/](modules/), [board/](board/) -- is
**v3.0.2-era**, upstream through 2026-08-17, and **the emulator** at
<https://try.badgewa.re> runs current upstream, v3.x. Neither is evidence
about the badge.

**Every confidence marker in these notes names the firmware it holds for.** A
claim can be true of one and false of the other, and several in this repo
were believed "wrong" when they were only v2:

| Marker | Means |
|---|---|
| **[measured-v2]** | measured on the badge running v2: every frame time, allocation cost and hardware run, summarised in `.agents/optical_data_transfer/data.md` |
| **[verified-v2]** | seen to happen, or not, on the badge running v2 |
| **[measured-v3]**, **[verified-v3]** | the same on hardware running v3 -- nothing yet |
| **[measured-all]**, **[verified-all]** | measured or seen on the v2 badge, but a physical property -- the sensor, the panel, the light between them -- so it holds whatever the firmware |
| **[source-v3]** | read in this repo's firmware source, which is v3 |
| **[source-v2]** | read in this repo's git history as it stood at v2.0.2 (2026-04-10) |
| **[emulator-v3]** | run on the emulator, which is v3 |
| **[inferred-v2]**, **[inferred-v3]**, **[inferred-all]** | reasoned from other facts, for that firmware or for any |
| **[unmeasured]**, **[unverified]** | not known for either firmware; a suffix (**[unmeasured-v3]**) narrows it to one |
| **[datasheet]** | from a component's datasheet: hardware, so any firmware |

[.agents/docs/README.md](.agents/docs/README.md) has the full rules.

Every call v2 has, with its exact behaviour, is in
[.agents/docs/badgeware-api-v2.md](.agents/docs/badgeware-api-v2.md), read
from the v2.0.2 source (commit `db958ce`). What is known to differ:

| | v2.0.2 -- the badge | v3 -- this repo's source, the emulator |
|---|---|---|
| Panel refresh | **60Hz** **[measured-v2]**: 16.71 ms | 90Hz: `badge.mode()` calls `display.set_framerate(90)` on every mode change (2026-06-30) |
| `display.set_framerate` | absent **[measured-v2]** | present |
| GC block size | 16 bytes: a 4-item tuple costs **65-145 µs** to allocate, varying run to run, a 2-item one 2.9 **[measured-v2]** | 32 bytes (2026-07-31); a 4-item tuple fits one block. Unmeasured |
| `MICROPY_GC_NO_SCAN`, `MICROPY_HW_VM_IN_RAM` | no | yes (2026-06-20): ~20% faster interpreter |
| System clock | **200 MHz** **[measured-v2]** (`machine.freq()`): the backlight's PWM is 3,052 Hz, a 328 µs period | 250 MHz (2026-06-20): 3,815 Hz, 262 µs **[source-v3]** |
| `display.backlight(v)` | PWM duty `v^2.8`, **no floor**: the backlight is dark below about 0.45, and a mirror run read 0.0-0.4 as off **[measured-v2]**; the backlight scheme's middle, 0.80, is ~54% duty | a floor at 12.5% duty (2026-08-08), though 0 is still off **[source-v3]**; 0.80 is ~59%. The driver does not light below roughly 11% duty on either **[source-v3]**; this badge's measured 7-9% (`channel`, 2026-10-09f) **[measured-all]** |
| Alignment constants (`CENTER`, `MIDDLE`, ...) | **absent** under both spellings: both raised on the first frame **[verified-v2]** | present, hoisted into `builtins` (2026-07-29) |
| `screen.text(rect)` layout | the older text path | native layout in `screen.text` (2026-07-29) |
| Spritesheets | neither `spritesheet` nor `image.spritesheet` **[verified-v2]**; but the Python **`SpriteSheet(path, cols, rows)`** and `AnimatedSprite` globals exist **[source-v2]**, untried on the badge | `image.spritesheet(cols, rows)` (v3.0.0, 2026-08-07) |
| ROM fonts | **`rom_font.smart`**; `font` is the vector type and has no named fonts **[source-v2]** | `font.smart`; no `rom_font` |
| `screen.text` | `(str, x, y)` or `(str, vec2)` only: **no rect, no wrapping, no align**; `text.draw(img, str, rect)` wraps **[source-v2]** | `screen.text(str, rect, align=...)` lays out natively |
| GIF | **no decoder at all**: anything not PNG or JPEG is `unable to read image! 4` **[source-v2]** | decoded into a sheet, within limits |
| `color.black`, `color.white` | **tinted**: rgb(20, 30, 40) and rgb(222, 238, 214), from the `TUFTY=1` build **[source-v2]**; `share_wifi`'s fields use the full rgb(0, 0, 0) and rgb(255, 255, 255) | the same, while `TUFTY=1` is defined **[inferred-v3]** |
| picovector | the bundled one | upstream's module (2026-04-29) |
| `display.update()` waiting for vsync | a tight spin | services USB and the scheduler while it waits |
| `wifi.status()` | raised `NameError: name 'wifi' isn't defined` from inside `wifi.py` **[verified-v2]** | no such reference |

Physical facts do not depend on the firmware and were measured on this badge:
the sensor's 12-bit ADC, its position, the panel's settle time, how much
light reaches it. How fast the badge draws, allocates and presents does.

**If the badge is updated to v3**, re-run the `frame_probe` and `mirror`
subtools before trusting any timing figure here: the refresh, the fill
and allocation costs, and everything derived from them -- the 60Hz figures in
`data.md`, the simulator's `--v2` profile -- were all measured on v2.
Two badges on different firmware must still interoperate: frame rate no
longer sets a rung -- nothing is presented in a transfer -- and calibration
agrees the pair's light -- the Follower decides, the Leader adopts (see
section 9.6 of the protocol spec). Every supported firmware, v2.0.2 and
later, reads through the DMA ring, and a badge whose ring is refused does
not transfer (since 2026-10-11). In simulation a v2/v3 pair delivers 144 of
144 in every row [simulation; v3's curve source-v3, unmeasured].

## Apps

The ones these notes are about. The rest of `firmware/apps/` is the stock
Pimoroni set plus other team apps; see [README.md](README.md).

- `wifi_scanner` — scans and charts WiFi channels. Working, with one known bug:
  it maps `WLAN.scan()`'s security field as the 0-5 auth enum, and on this badge
  that field is the `CYW43_AUTH_FLAG_*` **bitmask** — WPA2 reads as `0x00400004`
  — so almost every network renders as `Unknown (4194308)`. **[source-v3]** It is
  also the one app the simulator cannot run, because there is no `network`
  module there.
- `share_wifi` — shares credentials between badges with light: one badge
  steps its backlight behind a white screen, the other reads its light
  sensor through a DMA ring. Its `README.md` is the overview for people;
  the app is the proof of concept for the **optical data transfer
  protocol**, which carries a list of byte fields of any length and knows
  nothing of WiFi. **The protocol is complete** (the user, 2026-10-19);
  **files are the goal**, and the file layer is next. **New to it? Start at [.agents/README.md](.agents/README.md)
  and the briefing, [.agents/share_wifi/state.md](.agents/share_wifi/state.md).**

  Nine Python files. Pure, with no badge globals, tested under CPython:
  `protocol.py` (the wire format and state machine), `reader.py` (how a
  transfer reads the sensor, as times and means), `fastblock.py` (the fast
  block's codec, a viper kernel that is plain Python under CPython),
  `devlink.py` (the USB dev link's wire format, imported by the host program
  too) and `devserver.py` (its command server). Badge-only, registers and
  firmware calls with their logic handed in: `fastadc.py` (the ADC and DMA
  behind the ring), `fasttx.py` (a fast block played on the backlight by
  DMA paced on the PWM wrap) and `usbport.py` (the USB serial). `__init__.py`
  is the shell: drawing, buttons, storage, connecting, the transfer loop,
  and Dev Mode's three items.

  **Where it stands.** In simulation every scenario delivers: the 24-row
  sweep 144 of 144 a row over 8 seeds, the twelve hard rows 1296 of 1296
  over 72; a credential takes 1.4 s lit, 1.7-1.8 in flicker, 2.1 dark.
  On hardware (v2), **one badge facing a mirror** has run every piece a
  transfer uses -- the ring, slot-rate symbols, fast blocks through the
  protocol's own code -- and decoded them. **No transfer between two
  badges has run: there is no second badge.** The figures, and what each
  decision rests on, are in
  [.agents/optical_data_transfer/data.md](.agents/optical_data_transfer/data.md).

  **How a transfer works**, in brief (the spec has it all):
  - **Signals on the backlight alone**, behind a white screen drawn once;
    nothing signals in pixels. Data levels 0.5 / 0.82 / 1.0, beacons 0 / 1.0
    (`LEVEL_SCHEMES`).
  - **Reads through the DMA ring**: the ADC free-runs at 50 kHz into 32 KB,
    327 ms of readings; the protocol decides each slot after the fact. A
    badge whose ring cannot be made or verified refuses to transfer; there
    is no polled fallback.
  - **Handshake**: the Leader's Announce (14 ms halves), the Follower's one
    staircase, whose repetitions carry the pair's light (the Follower
    decides, the Leader adopts); each badge fits the room's mains flicker
    in the shared gap and corrects every reading after; the Leader holds
    its first block back in flicker.
  - **Payload: fast blocks only** (since 2026-10-18): 2-level PAM at 2-6
    PWM periods a symbol (F0-F3), each block framed and answered at the
    slot rate (8 ms lit, 16 dark, down to 32). No slot-symbol payload, no
    breaking in, and a badge without the fast pacer cannot share.
  - **Not backward compatible**: both badges need the same build;
    share_wifi is unreleased.

  **Every measurement tool lives off the badge**, in
  `tests/share_wifi/devtests/`: the subtools `mirror`, `second_badge`,
  `frame_probe`, `room_flicker`, `fast_loopback`, `files` and `reset`, over
  the libraries in `devtests/lib/` (`measure.py`, `probe.py`, `flicker.py`
  pure; `fastloop.py`; `toolkit.py`). They reach the badge over the dev
  link, never in the app; uploaded ones with `MENU` entries also run from
  Dev Mode, untethered.

  **Dev Mode**, revealed by holding C for 5 s on the main screen, has
  *Display Saved Measurements*, *Clear Saved Measurements* (the file and
  every stored tool) and *USB Dev Link*, then the uploaded tools' entries.
  B on the main menu leaves the app.

  **The USB dev link** lets a computer drive the badge over the cable.
  `tests/share_wifi/devlink_host.py OUTDIR` (needs pyserial: `sudo apt
  install python3-serial`, or pip in a venv; run it as `sg dialout -c
  "..."`) lists and runs the subtools and two instant built-ins
  (`measurements`, `clear`), uploads with `new` (`--lib` for a library;
  `sync` sends the whole tree), deletes with `remove`, and saves what they
  send as `OUTDIR/<subtest>.<timestamp>.<ext>`. Given a command after
  OUTDIR it runs that one and exits 0 or non-zero, for scripts.
  - While the link is open, Ctrl-C is off, so mpremote cannot interrupt the
    app; B gives the port back. A run is armed with a hard cancel: B's IRQ
    injects Ctrl-C through dupterm, and the host's raw 0x03 does it too.
  - No USB write and no ADC read is allowed while the DMA ring runs.
  - Timing figures never come from inside an open link session:
    collections double there.
  - **With no hands**: the badge plugged in with its menu running,
    `tests/usbrun.py` resets it over the REPL, puts this tree's share_wifi
    in `/runs/` and launches it with `builtins.DEV_AUTORUN = "link"`;
    `tests/share_wifi/autorun_host.py OUTDIR "run mirror dark" ...` syncs
    the subtools and runs each command. It cannot change the light, the
    mirror, or the number of badges.
  - The v2 checklist passed on 2026-10-08/09 **[verified-v2]**; still
    **[unverified]**: unplugging during a run other than `files`'s, and the
    RP2350 errata. The how-to and checklist: "The USB dev link" in
    `.agents/docs/testing.md`; the wire format: the app spec.

  **The simulator.** `tests/share_wifi/simulator.py` runs both badges'
  production code on one timeline against a model of the measured
  hardware, asserting the link invariants on every tick;
  `tests/share_wifi/sweep.py ROW...` sweeps it, a row 8 seeds x 6 scenarios
  x 3 payloads. Beware what it takes for granted: that the sensor averages
  the PWM (measured), a sensor model fitted to the mirror, a peer's swing
  predicted rather than measured, and the costs measured on v2. A pass
  means "agrees with the model". The app shell also walks every screen in
  the Badgeware emulator without raising -- the API working, not the optics.

  **The swing is thin**: the backlight's whole swing, off to full, is
  64-112 counts on a mirror -- four to seven ADC steps -- with 2-10 counts
  of noise a reading, so calibration measures each level's noise and needs
  every gap `GAP_SIGMAS` (4) standard deviations wide.

  A credential is three fields -- SSID, key, security -- so an open network
  can be shared with no password and a WEP key is not mistaken for a
  corrupt WPA one. **The badge cannot join a WEP network**: the cyw43
  driver rejects the auth type before touching the radio, so connecting is
  refused up front with "WEP networks not supported" while sharing and
  storing still work; the refusal is probed, not hardcoded. At launch the
  app seeds its config file with the badge's own network, from `secrets`:
  the driver has no getter for a station PSK, so the network a badge is
  *currently* on cannot be recovered. Every judgement about what a field
  may contain belongs to the caller: `valid_credential()` in `__init__.py`
  is where WiFi's limits live.

  **The documents** ([.agents/README.md](.agents/README.md) indexes them):
  the protocol in [.agents/optical_data_transfer/](.agents/optical_data_transfer)
  -- `spec.md` **the authority**, `context.md` its reasons, `tests.md` its
  checks (the three link only to each other and to `data.md`, so an agent
  with only that folder can work from them), `data.md` the evidence,
  `invariants.md` what the simulator asserts; the app in
  [.agents/share_wifi/](.agents/share_wifi) -- `app-spec.md` and the
  briefing `state.md`. Keep the spec and the code in step when either
  changes, and add new evidence to `data.md` in the section it bears on,
  dated, with its marker.

- `rampage_loop` — plays a boat video on a loop. It is a separate, later app
  from the `rampage` that several notes below refer to; that one, with its
  frame-building pipeline and `probe_badge.py`, stayed in the `tufty_badge`
  repo. The lessons it taught — no GIFs, no spritesheet on this firmware — are
  rules below.

## Before you write code

**There is an emulator, and it is the real firmware -- the current one, v3,
not the badge's v2.** <https://try.badgewa.re> runs the same MicroPython WASM
build with the same `badgeware/` modules as current upstream -- the
simulator's copy of `badgeware/__init__.py` differs from the firmware's in one
line, the frozen-module path. An earlier version of this file said no emulator
existed; that cost this repo several rounds of guessing at the API and at
least one "verified on hardware" claim that was wrong.

It answers questions about the *v3 API*: which globals exist, what a call
returns, whether a name raises. Where v3 and v2 differ (above) it answers for
v3 only, and the badge is the tiebreaker. It says nothing about the panel, the light
sensor, real frame timing or anything else physical, because there is no
hardware under it. `network` is absent, so `wifi_scanner` will not load there.

Two ways in. The page itself drives it with Playwright. Headless, it is a page
of Node: fetch `simulator/filesystem.json` and the files it lists into an
Emscripten FS, import `simulator/asyncify/micropython.mjs`, stub the four host
hooks the hardware shims call (`blitrgba`, `sim_buttons`, `sim_pwm_set`,
`backlight`), then `sys.path.insert(0, "/")`, `import badgeware`, and launch the
app the way `launch()` does. Swapping `run` for a frame-limited stand-in that
mirrors `_run.__call__` lets you step frames and script buttons.

When that is not enough, the firmware source is the tiebreaker for v3, and it
is this repo: [modules/common/badgeware/](modules/common/badgeware/) (upstream
is <https://github.com/pimoroni/tufty2350>). Reading `state.py` settles what
`State.load()` returns in less time than arguing about it. For v2, `git log`
on this repo is the next best thing: anything committed after 2026-04-10 is
not on the badge, and the dates in the table above came from there.

`python3 -c "import ast; ast.parse(...)"` still catches syntax errors, but it is
no longer the entire safety net.

The user can also run code live on the badge from VS Code with the MicroPico
extension, plugged in over USB — no file copying, no reboot. That is the only
way to settle anything about the panel or the sensor, and it is cheap *for
them*. Ask rather than guess.

- Re-read the API notes rather than assuming the API resembles CPython, Pygame or
  the older Pimoroni Pico libraries. It resembles none of them.
- Say plainly which parts of a change you could not verify, and what the user
  should watch for when they run it.
- Prefer one obvious way of doing something over a clever one. A bug found on
  hardware costs a flash-and-reboot cycle to diagnose.

## The rules that actually bite

These are mistakes already made in this repo, not hypotheticals.

**Present the frame every iteration.** In a manual loop, `badge.update()` is what
polls the buttons as well as showing the frame. Skipping it on "nothing changed"
frames freezes input completely. If you use `run(update)` instead, the loop does
this for you and you must *not* call `badge.update()` yourself.

**This badge's firmware (v2.0.2) is older than the docs, the emulator and this
repo's source (v3). Neither documented spritesheet spelling exists on it.**
**[verified-v2]** `rampage` cost two flash-and-reboot cycles
finding this out:

```
spritesheet.load(path)                    NameError: name 'spritesheet' isn't defined
image.load(path).spritesheet(cols, rows)  AttributeError: 'image' object has no
                                          attribute 'spritesheet'
```

The first is in the official API docs as a hoisted global, with a worked GIF
example. The second is what the firmware repo's own bundled apps use
(`snarky_sciuridae`). **Neither is evidence about the badge on the desk.** The
globals are a loop over `picovector.__dict__`, so the documented table is only
ever a guess at what that build exports, and `firmware/apps/` on `main` is
written against whatever picovector `main` has.

So the order of evidence is: what this badge has already run > a bundled app >
the firmware source > the official docs. When none of those settles it, **ask
the user to run a probe** --
`rampage/tools/probe_badge.py` in the `tufty_badge` repo is one, and
costs them a USB cable and ten seconds against your flash-and-reboot cycle.
Bare-name lookup inside a `try` is the only probe that finds a global.

For animation the calls this badge is known to have are `image.load(path)` and
`screen.blit(img, rect)`, the two `firmware/apps/demos/demos/sprites.py` uses.
The v2 source also has `image.window(rect)` -- a view sharing the sheet's
pixels -- and the Python `SpriteSheet(path, cols, rows)` built on it, which
every v2 bundled app with sprites uses **[source-v2]**; neither has been run
on the badge yet. Pass `blit` a `vec2` or floats: v2 refuses integer `x, y`.

**Do not ship an animation as a GIF.** **[verified-v2]** On v2 there is no GIF
decoder at all: `image.load` tries PNG, then JPEG, and anything else raises
`ValueError: unable to read image! 4` **[source-v2]** -- exactly what `rampage`
got at 160x90 by 91 frames. v3 does decode GIFs, but composites every frame
into one 8bpp sheet checked against a build limit, with the frame table in the
60KB rasteriser buffer and 256 colours across all frames **[source-v3]**, so
even there only tiny GIFs load. Ship one PNG per frame and carry the timings in a generated
Python file -- `rampage/tools/build_frames.py` in the `tufty_badge` repo
is the converter, and "Ship one PNG per frame instead" in
[.agents/docs/badgeware-graphics.md](.agents/docs/badgeware-graphics.md) has the reasons.


**`run()` already cleared the screen, and a return value quits the app.**
**[source-v3]**, and the clear costs 11.4 ms a HIRES frame on the badge
**[measured-v2]**: `_run.__call__` is `badge.clear()`, `update()`, `display.update()`,
`badge.poll()` -- and `if (result := update()) is not None: return`. So `update()`
starts on a screen already filled with `badge.default_clear`, and ending it with
`return something` closes the app instead of drawing a frame.

**Don't hand-roll button edge detection.** `badge.pressed(BUTTON_C)` already fires
only on the press transition. There is also `released()`, `held()` and `changed()`.
Button constants are bare globals: `BUTTON_C`, not `badge.BUTTON_C`.

**Set the pen before `clear()`, not after.** `clear()` fills with the current pen.
Setting it to white, clearing, and then drawing white text gives a blank white
screen. The order is: pen to background, clear, pen to foreground, draw.

**Get the screen size from `screen.width` / `screen.height`.** Hardcoded 320x240
breaks the moment someone switches to `LORES`.

**Do not unpack keyword arguments with `**`.** It put an argument one slot to
the left on the badge (v2) and surfaced as `TypeError: Bool object isn't iterable`
from a line with no loop in it. Pass arguments positionally or by name. `*`
unpacking in a call is fine; it is `**` that bit. Avoid `%r` too. Both are in
[.agents/docs/badgeware-patterns.md](.agents/docs/badgeware-patterns.md) under "MicroPython is
not CPython in the small" — the class of bug a CPython test suite cannot catch.

**`global` goes at the top of the function**, before any use of the name.
MicroPython raises `SyntaxError: identifier redefined as global` otherwise —
a rule CPython is laxer about.

**Keep blocking calls out of the loop.** `WLAN.scan()` takes seconds and stalls
every frame it runs in. `wifi.connect()` is deliberately non-blocking: call it
repeatedly and keep drawing. If something must block, draw a status message and
`badge.update()` *before* starting it, or the user stares at a stale frame.

**`wifi` is not a global — import it.** `badge`, `screen`, `display`, `State`,
`rtc`, `color`, `font` and the `BUTTON_*` constants are injected; `wifi` is a
firmware module. Using it bare is a `NameError` at launch that stubbed tests
cannot catch, because a stub satisfies either spelling. `secrets` is the same:
a module, and one that imports `/secrets.py` before `/system/secrets.py`.

**Firmware modules differ between builds, and one of them raises.**
**[verified-v2]** `wifi.status()` threw `NameError: name
'wifi' isn't defined` from inside `wifi.py` itself on the badge, where neither
this repo's v3 source nor the emulator has any such reference. Wrap calls into firmware
helpers whose result is not load-bearing, and degrade rather than die: for
`status()` that means "still trying", because `is_connected()` is what decides
success and your own deadline is what decides failure.

**Some firmware helpers report failure by resetting the badge.**
`secrets.require()` and `wifi.connect()` both call `fatal_error()` when they
give up — the error screen, then `machine.reset()`. Neither raises something an
app can catch, so check the values yourself and keep your own deadline.

**Fonts: `font.<name>` works on v3.** **[emulator-v3]**
`font.smart` resolves and `getattr(font, "nosuch", None)` returns `None` rather
than raising. An earlier note recorded `font.smart` raising, which would mean
older firmware -- plausibly the badge's v2 -- so `pick_font()` stays: resolve
defensively, treat a missing font as "keep the default" rather than a crash.
`rom_font` does not exist.

**Allocate outside hot loops.** See the performance notes; reach for
`@micropython.native` or `@micropython.viper` before restructuring anything.
On v2 firmware **[measured-v2]** a 2-item tuple costs 2.9 µs and a 4-item one
65-145 µs, depending on the run: anything bigger than one 16-byte GC block
rescans the heap's allocation table. A `gc.collect()` costs 10-81 ms -- a
slot or more -- 0.31 ms + 0.049 ms per KB live + 0.00708 ms per KB up to
the highest live block (2026-10-13, [measured-v2]), which is wherever the
app loaded, since v2's `launch()` does not collect first: share_wifi 17 ms
from a clean heap, 57 with 6 MB of garbage below it. v3's 32-byte blocks move that threshold, not the cost, and
are unmeasured. And never call `gc.mem_alloc()` or `gc.mem_free()` in a timed path: both
are the same `gc_info()` walk of the whole table **[source-v2]**, ~110 ms on v2
-- `mem_free()` as dear as `mem_alloc()`. **A call can allocate too**: a
Python function whose frame -- locals plus stack -- is over 11 words gets
it from the heap on every call, and frees it after (`VM_MAX_STATE_ON_STACK`,
`py/objfun.c`; pystack off). Read in the pimoroni/micropython fork at
`f173bb7b6`, older than both pins (bw-1.27.0, bw-1.28.0-3), not at either:
that the pins keep it is **[inferred-v2]** **[inferred-v3]**, and on the
badge it is **[unverified]** until M7 shows the cost. That was why share_wifi's `correct()` cost
0.43-0.73 ms **[inferred-v2]**; keep per-sample functions small, and see
`tests/share_wifi/test_flicker_frames.py` for counting a call's bytes
under the unix port. Import what only one mode uses
inside that mode: share_wifi's dev link loads its modules when the link
opens, not at launch.

**The frame rate is the firmware's, and a frame is whole refreshes.**
**[measured-v2]** The badge on the desk is v2 (`BW-1.27.0`, 2026-04-10): no
`display.set_framerate`, so the panel runs at **60Hz**, not the 90Hz v3 sets.
With `VSYNC` every frame is a whole number of 16.7 ms refreshes, and writing
one takes 7.8 ms, leaving ~8.9 ms for everything else. `run()` clears the
screen before every `update()` -- **11.4 ms at HIRES** -- so a HIRES app that
also fills the screen itself is two refreshes before it has drawn anything.
`badge.default_clear = None` turns `run()`'s clear off; `LORES` fills a
quarter of the pixels. See "What the hardware runs at" in
[.agents/docs/badgeware-hardware.md](.agents/docs/badgeware-hardware.md).

**`badge.ticks` only moves when the buttons are polled.** `badge.poll()` is what
advances it, which `badge.update()` does and which `run()` does for you — so it
moves once a frame either way, and every read inside one frame gives the same
number. So a deadline checked *after* presenting the frame is
reading a newer clock than the code that drew it, and the frame that acts is never
the frame that showed it was about to. Decide once at the top of the loop, pass the
decision into the drawing code, and act on that same value. This is what made
`wifi_scanner` display "Rescan in 1 sec" instead of "Rescanning channels".

**The fourth argument to `screen.text()` is size, not width.** Passing a pixel width
there scales the glyphs by that factor. Wrap with a `rect`, or measure and trim.

**Don't truncate text that had room to wrap.** A per-character `fit()` measures
the string unwrapped, so calling it before handing a `rect` to the layout engine
turns a message that would have wrapped onto a second line into one cut short
with an ellipsis. Fit only when the box is a single line high. A narrow column
makes this the common case rather than a corner of it.

**Never count characters to fit text.** ROM fonts are not monospaced — measure.

**You cannot probe for an injected global with `globals()`.** **[source-v3]** The
firmware puts them in `builtins` — `builtins.CENTER = image.CENTER` — so
`globals().get("CENTER")` reads `None` whether the badge has the constant or
not, and `getattr(builtins, "CENTER")` raises too. Bare-name lookup is the only
spelling that finds one, so a probe must name it and catch `NameError`, the way
`pick_font()` does. `share_wifi`'s alignment probe was `globals()` and was dead
code for as long as it stood.

**The alignment constants exist on v3 and not on v2.** **[source-v3]** in the
v3 source, under both spellings (`builtins.CENTER = image.CENTER`, added
2026-07-29), and **[verified-v2]** absent on the badge's v2, where
both spellings raised on the first frame. So probe for them, as `draw_text()`
does, and keep a path that places text without them -- the harness runs
without them by default for that reason. `overflow=ELLIPSES` arrived with them
and is not worth using where it exists: it truncates by **word**, so a one-line box holding a long hyphenated SSID comes out as `"..."`
and nothing else. `wifi_scanner`'s `fit()` helper — a per-character
`measure_text()` loop — is the right pattern for a list of names, and
`share_wifi`'s `draw_text()` pairs it with the native `align=`.

**`WLAN.scan()`'s security field is a bitmask, not the 0-5 auth enum.**
**[source-v3]** `network_cyw43.c` hands back the scan result's `auth_mode` raw,
and the driver documents it as `CYW43_AUTH_FLAG_*`: WPA2-AES is `0x00400004`,
WPA3 `0x01000000`, Enterprise `0x02000000`. Only Open (0) and WEP (1) coincide
with the conventional numbering. `wifi_scanner` still maps 0-5, which is why it
labels nearly everything `Unknown (4194308)`.

**`badge.mode()` rebinds `screen`.** **[source-v3]** **[verified-v2]** --
changing mode assigns a new `builtins.screen` over the new framebuffer. Anything holding its own reference
keeps drawing into a buffer nobody presents — read the global each time. The
firmware boots `LORES | VSYNC`, so an app wanting hires has to say so.

**No threads, so blocking really does block.** MicroPython here has no `threading`
and no `multiprocessing`. There is no background task to push `WLAN.scan()` onto.
Anything slow either runs in the frame loop or does not run.

**The light sensor is 12-bit, not 16.** **[measured-all]** on the v2 badge -- an
ADC property, so v3 should read the same. Every reading in a mirror run was a
multiple of **16**, so `light_level()` is a 4096-step
conversion in a 16-bit frame and one step is 16 counts. Any contrast figure is
really denominated in those: a threshold that is not a multiple of 16 claims
precision the hardware has not got. The panel's full swing measured **five of
those steps**, with two steps of noise.

**The light sensor does not see the whole screen.** It is ~1mm outside the right
edge of the active area, at mid height — about six pixels out, at 5.6 px/mm. It
reads a patch near itself, so "the peer reads the average brightness of the whole
screen" is wrong and led to a wrong derate. Anything optical has to reason about
*where* on the peer's screen the sensor is looking, and two badges facing each
other are mirrored, so right meets left unless one is upside down. **[measured-all]**

**Don't allocate per frame.** The heap is 8MB with no swap, and object creation in a
tight loop triggers GC pauses. Preallocate buffers, use the in-place `vec2` operators,
and call `gc.collect()` at a moment you pick.

**Disk mode shows `/system`, and apps cannot write there.** **[source-v3]**
from the source: `_boot_fat.py` mounts LittleFS at `/` and the FAT volume at `/system` with
`readonly=True`; `rp2.enable_msc()` exposes the FAT one. So anything an app
writes -- `/state` included -- is on the volume a host does not see when the
badge is plugged in. To get data off, use the REPL (`mpremote fs cp :/path .`)
or print it and copy it out of the terminal. Do not design a feature around a
file the user can drag off the badge; there isn't one.

**Don't write to flash per frame.** `/` is ~256 blocks of 4KB and wears out. Buffer
in RAM, save occasionally, and consolidate into one JSON file rather than many small
ones — a 10-byte file still costs a 4KB block.

## Keep hardware out of the logic

Anything with a wire format, a parser or a state machine belongs in its own
module that never mentions `badge`, `screen` or `color`. It can then be imported
and tested under plain CPython, with none of the stub machinery. Where such a
module genuinely needs something only the badge knows, inject it — `Transfer`
takes its `reader` rather than reaching for the sensor.

`firmware/apps/share_wifi/protocol.py` is the worked example, and
`tests/share_wifi/test_module_boundary.py` asserts the separation holds.
The dev link's libraries, in `tests/share_wifi/devtests/lib/`, keep the
rule too: `measure.py` takes its reader, backlight and clock, `probe.py`
even the present, the clear and the collector as hooks, `flicker.py` the
sensor and the clock, and `test_measure.py`, `test_probe.py` and
`test_flicker.py` check each references no badge globals. `devserver.py` takes the USB port and
the badge as hooks, and `devlink.py`, the wire format, is imported by the
host program as it stands; `tests/share_wifi/test_module_boundary.py` checks
both. `fastadc.py`, `fasttx.py` and `usbport.py` are the exceptions, and
deliberately: registers and firmware calls and nothing else, with
`machine.mem32`
and the DMA channel handed to fasttx and every usbport dependency handed in,
so their logic is tested under CPython (`test_fasttx.py`,
`test_usbport.py`). `fastblock.py` is pure: `test_module_boundary.py`.

## Picking up an in-flight task

Briefs for work in progress live in [.agents/tasks/](.agents/tasks), one file per
task. They are disposable: when a task lands, its brief is deleted and anything
durable moves into the `.agents/` folder it belongs to, or here.

Open tasks, their owners (several need a human at the badge) and what each
waits on are indexed in [.agents/tasks/README.md](.agents/tasks/README.md).
[.agents/optical_data_transfer/invariants.md](.agents/optical_data_transfer/invariants.md)
is what `tests/share_wifi/simulator.py` asserts, and it is the right place to
start if you change anything about the optical link.

## Testing without a badge

```
python3 tests/run_tests.py
```

Badge apps are plain modules reading globals that do not exist in CPython, so
supplying stubs lets you `exec()` an app and call its internals directly. That is
what [tests/harness.py](tests/harness.py) does; see [.agents/docs/testing.md](.agents/docs/testing.md).

Tests go in `tests/<app>/test_*.py` and need no registering — every folder in
`tests/` is found. Helpers that are not tests live there too and are
not named `test_*`, so the runner leaves them alone.

**Run the suite before claiming a change works, and add to it when you touch
anything with a wire format, a parser or a state machine.**

**One seed is not evidence in the optical link.** Sweep at least 8. During the
phase-locking work seed 1 passed every scenario while half the other seeds
failed, more than once and on different changes each time — a green seed 1 sent
two earlier attempts down the wrong path. `tests/share_wifi/simulator.py` takes
a `seed`; use it before concluding anything about reliability.

**Test at the seams, not just the units.** `share_wifi` had 44 passing tests
while being fundamentally broken: the transmitter emitted one symbol per *frame*
into a receiver decoding one per *slot*, so no transfer could ever complete. Every
test passed because they all fed symbol lists straight into the decoder and none
ran the frame loop. A green suite is evidence about what it covers and nothing
else — say which seam is untested rather than letting the count speak for you. It caught bugs in
`share_wifi` that careful reading had missed: a slot ladder that stepped the
wrong way, and an End Block Pattern whose leading symbols were being consumed as
payload. It says nothing about drawing, real frame timing, or whether the
hardware constants are right.

## Writing specs in this repo

`share_wifi` is a spec in a comment block, and it is held to the same standard as
code. A spec that defers a decision has not made it:

- Name the constant and its units, or say it must be measured on hardware and give
  a starting value. `[unmeasured]` is an acceptable answer; silence is not.
- Pin every wire format completely — a "16-bit CRC" is not a specification until the
  polynomial, initial value, reflection and bit order are stated.
- If two sections could contradict each other, one of them is the authority. Say so.
- Prefer a rule an implementer can test locally over one requiring agreement between
  two devices. Protocols converge when only one side is allowed to move.

## Style

Match what is already there: module-level constants in caps, small named helper
functions, comments only where the *why* isn't obvious from the code. Nested
`while` loops with an early `return` are the idiomatic way to do sub-screens —
prefer that over a state-machine variable, which is what the docs' own example
app does.
