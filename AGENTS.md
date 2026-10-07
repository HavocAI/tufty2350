# Working in this repo

A fork of the Tufty 2350 firmware (Badgeware, MicroPython) with our own apps
alongside the stock ones. Each folder in `firmware/apps/` is one app:
`__init__.py` is the entry point, `icon.png` is the launcher icon.

```
firmware/apps/<app>/  one app -- __init__.py, icon.png, its assets
modules/              the firmware's own Python and C modules
tests/                host-only: the harness and runner, shared by every app
  <app>/              one app's tests, and any tools only it needs
.agents/              notes for agents: tasks/, context/ and
  docs/               reference notes; start at .agents/docs/README.md
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
[.agents/docs/](.agents/docs/) is reference — start at [.agents/docs/README.md](.agents/docs/README.md), which says
which file answers which question and how confident each claim is.
The API is small but unusual, and guessing at it wastes a hardware round-trip.
Official docs: <https://badgewa.re/docs>. If you learn something those files don't
cover, or that contradicts them, update them in the same change.

## Two firmwares: the badge runs v2, this repo is v3

**The badge on the desk runs v2.0.2** -- `BW-1.27.0, on 2026-04-10`, read off
it by Dev Mode's *probe frame timing* (`frame_probe.firmware.version`).
**This repo's firmware source** -- [modules/](modules/), [board/](board/) -- is
**v3.0.2-era**, upstream through 2026-08-17, and **the emulator** at
<https://try.badgewa.re> runs current upstream, v3.x. Neither is evidence
about the badge.

**Every confidence marker in these notes names the firmware it holds for.** A
claim can be true of one and false of the other, and several in this repo
were believed "wrong" when they were only v2:

| Marker | Means |
|---|---|
| **[measured-v2]** | measured on the badge running v2: every frame time, allocation cost and hardware run in `.agents/docs/hardware-measurement-*.json` |
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
| `display.backlight(v)` | PWM duty `v^2.8`, **no floor**: the backlight is dark below about 0.45, and a mirror run read 0.0-0.4 as off **[measured-v2]**; the backlight scheme's middle, 0.80, is ~54% duty | a floor at 12.5% duty (2026-08-08), though 0 is still off **[source-v3]**; 0.80 is ~59%. The driver does not light below roughly 11% duty on either |
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

**If the badge is updated to v3**, re-run Dev Mode's *probe frame timing* and
the mirror tool before trusting any timing figure here: the refresh, the fill
and allocation costs, and everything derived from them -- the 60Hz rows of the
protocol's tables, the simulator's `--v2` profile -- were all measured on v2.
Two badges on different firmware must still interoperate: calibration agrees
the starting rung from both badges' frame-rate classes, so a 60Hz and a 90Hz
badge meet on the slower one's (see "Agreeing a rung" in the protocol).

## Apps

The ones these notes are about. The rest of `firmware/apps/` is the stock
Pimoroni set plus other team apps; see [README.md](README.md).

- `wifi_scanner` — scans and charts WiFi channels. Working, with one known bug:
  it maps `WLAN.scan()`'s security field as the 0-5 auth enum, and on this badge
  that field is the `CYW43_AUTH_FLAG_*` **bitmask** — WPA2 reads as `0x00400004`
  — so almost every network renders as `Unknown (4194308)`. **[source-v3]** It is
  also the one app the simulator cannot run, because there is no `network`
  module there.
- `share_wifi` — shares credentials between badges over the screen and light
  sensor. Six files: `protocol.py` is the wire format and state machine, pure
  Python with no badge globals and no knowledge of WiFi; `measure.py` is Dev
  Mode's measurement engine, `probe.py` its frame timing probe and
  `flicker.py` its room-flicker capture, all pure in the same way;
  `__init__.py` is the shell around them — drawing, buttons, storage,
  connecting; and `fastadc.py`, badge-only, reads the sensor straight from
  the RP2350's ADC registers, by viper and by DMA, for the probe.

  It completes a transfer end to end **in simulation**: `python3
  tests/share_wifi/simulator.py` delivers the payload in all six scenarios with
  both badges reporting `OK`, and asserts the link invariants on every tick
  rather than at the end. Swept over 8 seeds x 6 scenarios x 3 payloads — a WPA2
  credential, an open one with an empty key, and the longest a credential can
  be — it delivers **144 of 144** in the fields scheme stepping once a frame,
  and 144 of 144 letting calibration choose a scheme. `--loop` runs the badges
  as the app now does, **reading continuously** (below): with the sensor as
  measured (`--optics=mirror`) and calibration choosing (`--auto`), 144 of
  144 on the backlight's 32 ms rung in the dark, median 15.3 s, and on its
  16 ms rung in a lit room, 10.5 s; and 144 of 144 on pixels' 80 ms rung,
  23.6 s. The app shell also launches and walks every screen in
  the Badgeware simulator without raising — but that is the API working, not
  the optics.

  **A transfer reads continuously.** `do_transfer()` is a loop that reads the
  sensor back to back for a 2 ms sample -- `read_sample()`, nothing but
  locals -- and hands their mean to `Transfer(continuous=True).sample()`,
  which pools samples into 8 ms ones for calibration. A change of
  backlight is made in the loop; only a change of pixels returns to `run()` to
  be presented. So on the backlight scheme, whose pixels are white for every
  symbol, nothing is presented in the data phase and neither badge is ever
  blind; and the scheme's 1-8 ms sensor settle, rather than the panel's 24,
  sets its rung: it starts on 32 ms, pixels on 80 -- and on **16 ms when both
  badges are in a lit room**, where the sensor is fastest, which each says
  in its staircase (the LIT class). A continuous badge takes the backlight
  whenever it calibrated, calibrates in 150 ms steps rather than 200, and
  **breaks in**: a Follower that loses the block shows high, and the Leader,
  which reads while it transmits, stops at once instead of sending the rest
  -- up to 17 s of it -- then resends after repeating its last verdict. See
  "Reading continuously" in the protocol, which also has the stress sweeps
  and where it fails. Room lights flicker at 120 Hz, some by nearly half
  their light (measured), so in the data phase a continuous badge learns the
  flicker before calibration -- the region, 50 or 60 Hz, and the light's
  own shape, from a window its peer is known dark in -- then corrects every
  sample with a table and tracks the mains' drift with a phase lock (see
  "Mains flicker is fitted" in the protocol; in simulation a 0.02 Hz drift
  took the old learner to 77 of 144, and the fit to 144). It takes the
  flicker off every 2 ms sample: `sample()` takes the sample's time in µs for it.
  Before any fit, a Follower in the handshake drops a coarse sample its own
  present cut short (`HANDSHAKE_GAP_MS`): at three times the lit room's
  light one such sample hid the Leader's beacon edge and 30 of 144 failed;
  now 144 (see "Peer present" in the protocol).
  **The protocol is called once a sample, not once a reading**, because on
  the badge each Python operation costs microseconds and a protocol call
  about a millisecond [measured-v2]: called per reading, it took every
  pass -- 380, then 705, then 1,073 µs a reading -- and a shortcut for
  readings in the same ms never ran, since no two readings ever were.
  MicroPython's unix port, six to fifteen times *faster* on the same
  change, is no guide to the badge's costs. See "What a reading costs" in
  the protocol.

  The screen transmits on **the whole screen, at `LORES`, with nothing else
  drawn**, `run()`'s clear switched off and the field redrawn only when it
  changes; the case lights say what is happening. It used to be two edge bands
  with a column of text between them, and on the v2 badge that screen took
  three 60Hz refreshes a frame -- drawing, not the panel write, was the cost. The
  whole screen also settles where the sensor is: it sits about 1mm outside the
  *right* edge and reads a patch near itself, and two badges facing each other
  are mirrored, so whichever edge it is near is now the field. See "Screen as a
  transmitter" in the protocol.

  **Two light-level schemes**, in `LEVEL_SCHEMES` in `protocol.py`. *Fields*,
  the original, puts the level in the pixels at full backlight. *Backlight*
  uses white pixels for every symbol -- low included, so it never changes a
  pixel -- and sets `display.backlight()` per symbol: 0.5 for low -- dim, not
  off, which keeps the sensor faintly lit and cut the dark room's edge delay
  from 1.2-1.9 ms to 0.3 on the badge -- 0.82 for the middle (57% PWM duty on
  the badge's v2, whose backlight has no floor; 61% on v3 -- see the table
  above), 1.0 for high. `SIGNALLING` in `__init__.py`
  says which a badge may use, and offers both: calibration measures both,
  signalled by the order of the staircase steps, and a continuous badge
  keeps the backlight unless its middle step did not hold. See "Choosing a
  scheme" in the spec.

  Beware what `tests/share_wifi/simulator.py` takes for granted. It once
  modelled a middle field at 45% of the swing while the app sent `color.grey`,
  7% of it, and every scenario passed. Its default assumes the sensor
  *averages* the backlight's ~3.8kHz PWM, which the second hardware run
  **[measured-v2]** at every duty from 0.25 to 1.0; `--flicker` models one that
  follows it, where the backlight scheme cannot work and choosing falls back
  to fields. It runs both badges at 11.1 ms frames, with each field on the
  panel the instant it is decided, unless told otherwise. `--v2` models the
  badge on the desk as measured on 2026-10-04 -- 60 Hz, a field reaching the
  panel 19 ms after its frame starts, a panel settling 90% in 24 ms -- and
  delivery falls from 136 to 99 of 144 in it. See "What the measurement
  found" in the protocol. `--optics=mirror|aligned|overlap` replaces its
  idealised sensor -- a 9000-count swing, every count resolved -- with the
  measured one: 16-count steps, Gaussian noise, an 80-count swing or the swing
  a second badge is predicted to give. In it the old fixed contrast floor,
  `MIN_CONTRAST` (192), refused everything; the noise-based check that
  replaced it (below) delivers 314 of 432 under v2 timing. See
  [.agents/docs/optical-coupling.md](.agents/docs/optical-coupling.md).
  `--auto` lets calibration choose. `--loop` (with `--v3`, `--slow-panel`,
  `--optics=`) runs continuous badges instead, modelled from the measured
  read rate, present, panel dead time and backlight, and with `--optics` a
  sensor whose speed is set by the light on it, fitted to `backlight_step`.
  The levels are pinned in the spec and checked against the app, the
  protocol's table and the simulator.

  **Calibration agrees the starting rung.** Each badge's staircase carries
  its frame-rate class in its repetition count -- 3 for FAST, 5 for SLOW (the
  100 ms rung) -- and both start on the slower class's. FAST stands for the
  fastest rung the scheme's settle allows: 80 ms on pixels, three of the
  panel's measured 24 ms, and 32 ms for continuous badges on the backlight. A
  continuous badge is always FAST: its frames do not bound its sampling. Before it, each badge started on the rung its own frame
  time allowed, and a 90Hz badge paired with a 30Hz one delivered 35 and 0 of
  144 in simulation; now 143 and 144. See "Agreeing a rung" in the protocol.

  The epoch calibration derives is only an estimate on the badge that *measured*
  the staircase rather than transmitting it, so receivers do not depend on it:
  they phase-lock onto the peer's real first edge out of black at the start of
  every message. See "Phase locking" in the spec.

  **It has now been measured on hardware (v2), and the swing is thin.** Mirror
  runs put the panel's whole black-to-white swing at **64-96 counts -- four to
  six ADC steps** -- with a standard deviation of 2-10 counts per reading. A
  fixed floor of 192 counts, `MIN_CONTRAST`, once refused all of it, on a
  reading of the noise as its peak-to-peak spread (32) rather than its
  standard deviation. It is gone: calibration measures each level's noise on
  the link and needs each gap to be `GAP_SIGMAS` (4) standard deviations wide
  -- see "Separability" in the protocol. The middle field,
  by contrast, is vindicated -- 0.400 of the swing, exactly where it was
  designed to land. A second run (2026-10-03, Dev Mode) agrees, and gives the
  backlight scheme the same swing: no help. See "What the measurement found"
  in the protocol, and the two `.agents/docs/hardware-measurement-*.json`
  files.

  The debug menu that produced those numbers has been removed. What it did,
  and the three things learned building it, are under "How the constants were
  measured" in the spec.

  **Dev Mode** replaces it: hidden at launch, revealed by C on the main
  screen, it runs a fixed schedule of tests -- levels, noise, gamma, backlight
  curve, flicker, settling, backlight step, a mirror loopback (with a dim-low
  variant), frame timing --
  in a dark and then
  a lit room,
  against a mirror or a second badge, and saves the results to
  `/state/measurements.json`. The screens are in `__init__.py`; the schedule
  and every calculation are in `measure.py`, tested under CPython by
  `tests/share_wifi/test_measure.py` against a simulated panel. The mirror
  tool and the probe below have run on the badge; the 2nd-badge tool has not.
  It also measures `averaging` -- whether averaging readings resolves finer
  than an ADC step -- and an `eye` diagram of two- and three-level symbols at
  50-100 ms, with the badges held on the alignment line every transfer now
  starts with: the other badge's edge on a line down the middle of the
  screen, so its screen covers this one's sensor.
  `backlight_step` times a change made by the backlight alone, on white
  pixels: `display.backlight()` is a PWM duty, live at once **[source-v2]**,
  so it skips both the present's ~19 ms and the panel's 16-24 ms settle, and
  what is left is the sensor's own response. Its first run (2026-10-05c) put
  that at **about 1.0-1.3 ms to 90% in a lit room and up to 7.8 ms in the dark**:
  the phototransistor is slower the less light it has. So the backlight
  scheme, which now keeps its pixels white for every symbol, low included,
  is no longer bound by the panel at all. Its capture runs around the call,
  not after a present. `loopback`, mirror only, sends a symbol block on the
  badge's own backlight and decodes the reflection with the transfer's own
  continuous receiver at 50 down to 16 ms: the first hardware check of the
  continuous path, and a measurement of the edge delay it assumes.
  **measure room flicker** is a tool of its own: A takes a measurement of
  about 30 s, as many as wanted, B saves them all under `room_flicker` -- the
  strongest frequency from 20 Hz to 1 kHz and its size in counts, the
  amplitude at every harmonic the filter fits (1-4 x 50 and 60 Hz), the
  transfer's own `FlickerFilter` fit on the capture with the flicker left
  before and after correction, and the mains' drift from 80 more windows,
  in mHz **against the badge's own clock** -- because mains flicker would be
  noise inside the lit room's 16 ms slots. `flicker.py` is its pure module.
  *clear saved measurements* deletes the file. The probe's
  `flicker_filter` step times the filter itself.
  See "Dev Mode" in
  [.agents/docs/share_wifi_spec.md](.agents/docs/share_wifi_spec.md), whose
  "Known gaps" are open decisions.

  Dev Mode's **probe frame timing** found why the first mirror run measured
  54 ms frames where 11 ms was expected: the badge's v2 firmware runs the
  panel at 60Hz, and a HIRES screen that filled twice and drew bands and text
  took three refreshes. It times the panel's refresh, a frame's write, each
  part of a signalling and a status frame, how fast the sensor can be read
  with nothing presented (`reads`: what a receiver holding its screen static
  would get), allocation and garbage collection, and saves them under
  `frame_probe`. Its first `reads` run (2026-10-05b) put such a listener at
  2,166 readings a second -- about 40 in a 50 ms slot's read window, against
  about one while presenting every frame -- which is why a transfer now reads
  continuously. `reads.transfer` times a pass of the real listen loop, the
  read rate the simulator's 0.43 ms assumes -- 380 µs on 2026-10-06.
  `fast_reads` reads the sensor straight from the ADC, through `fastadc.py`:
  by viper, and by DMA at 500k and 100k a second, each giving its rate and
  how the noise falls when 1-256 readings are averaged -- how short a slot
  could be. Its first run (2026-10-06a): viper reads at 359k a second,
  2.8 µs, and averaging 64 readings brings the noise to about a count, so
  noise no longer limits a slot -- the sensor's settle does. In the same
  run `reads.transfer` got *slower*, 705 µs a pass against 380, and the
  next, with `reads.detail` timing it, showed why: a protocol call per
  reading costs a ms. `reads.transfer` is now a pass of the sampling loop,
  and `reads.detail` the call's parts and the readings a sample held. Every
  Dev Mode tool and the probe also record the chip's temperature,
  `temperature_c`, at each test. It works on v2, and ruled itself out as why
  the panel's edges were slower in some runs. See "probe frame timing" in the spec
  and "What the hardware runs at" in
  [.agents/docs/badgeware-hardware.md](.agents/docs/badgeware-hardware.md).

  **A framing failure costs one rung, not the transfer.** A Follower that loses
  framing part way through a block now waits for `RESEND_QUIET_SLOTS` of black
  -- the Leader does not listen while it transmits -- before sending its one
  Data Sync Error, so one failure is one request and one demotion. Answering at
  once used to land inside the block still going out, demote the Follower on
  its own, and walk the two down the ladder a rung apart until both gave up
  with `ERR_LOST_PEER` at about 306 s. `tests/share_wifi/test_recovery.py`
  covers that, the empty payload (now one empty block rather than an
  `IndexError`), and the CRC retry budget a demotion the Follower asks for
  must restore.

  A credential is three fields -- SSID, key, security -- so an open network can
  be shared with no password and a WEP key is not mistaken for a corrupt WPA
  one. **The badge cannot join a WEP network**: the cyw43 driver rejects the
  auth type before touching the radio and has no code to install a WEP key, so
  connecting is refused up front with "WEP networks not supported" while
  sharing and storing still work. That refusal is probed, not hardcoded.
  `protocol.py` did not change to carry the security field: it takes a list of
  byte fields of any length and has never cared how many there are.

  At launch it seeds the config file with the badge's own network, from
  `secrets`, so a badge that has never received a credential still has one to
  share. That is the only readable source: the CYW43 driver's `config()` has no
  getter for a station PSK, so the network a badge is *currently* on cannot be
  recovered. See "This badge's own network" in
  [.agents/docs/share_wifi_spec.md](.agents/docs/share_wifi_spec.md).

  The protocol is specified in
  [.agents/docs/optical-data-transfer-protocol.md](.agents/docs/optical-data-transfer-protocol.md);
  keep the two in step when either changes. It carries a list of byte fields of
  any length, and is named for what it does, not for its first user. Every
  judgement about what a field should contain — including how long it may be —
  belongs to the caller; `valid_credential()` in `__init__.py` is where WiFi's
  own limits live.

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
rescans the heap's allocation table. A `gc.collect()` costs 16-64 ms -- a
slot or more. v3's 32-byte blocks move that threshold, not the cost, and
are unmeasured. And never call `gc.mem_alloc()` in a timed path -- it walks
the whole table, ~110 ms on v2.

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
`share_wifi` switches to `LORES` and back on the badge:
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
takes `frame_ms=lambda: badge.ticks_delta` rather than reaching for it.

`firmware/apps/share_wifi/protocol.py` is the worked example, and
`tests/share_wifi/test_module_boundary.py` asserts the separation holds.
`measure.py` beside it is the second: it takes `light=badge.light_level` and
`ticks_us=time.ticks_us`, and `tests/share_wifi/test_measure.py` checks it
references no badge globals. `probe.py` takes even the present, the clear and
the collector as hooks, and `tests/share_wifi/test_probe.py` checks the same. So
does `flicker.py`, which takes the sensor and the clock, in
`tests/share_wifi/test_flicker.py`. `fastadc.py` is the exception, and
deliberately: it is registers and nothing else, and the arithmetic on what
it reads is `probe.fast_stats()`.

## Picking up an in-flight task

Briefs for work in progress live in [.agents/](.agents/), one file per task, with
any context they need beside them. They are disposable: when a task lands, its
brief is deleted and anything durable moves into `.agents/docs/` or here.

Nothing is currently open. [.agents/context/optical-link-invariants.md](.agents/context/optical-link-invariants.md)
outlives its task: it is what `tests/share_wifi/simulator.py` asserts, and it is
the right place to start if you change anything about the optical link.

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
