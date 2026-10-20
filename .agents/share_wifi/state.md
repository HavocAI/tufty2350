# share_wifi and the optical link: where things stand (2026-10-19)

A briefing for an agent new to this project. It says what the app and its
protocol are, what has been proved and how, where everything lives, how to
work here, and what is left. Read it, then [AGENTS.md](../../AGENTS.md).

## 1. What this is

`share_wifi` is an app for the **Tufty 2350**, a Pimoroni badge with a
2.8-inch screen, an RP2350 chip and a light sensor beside the screen. It
hands a Wi-Fi network from one badge to another **with light alone**: one
badge holds a white screen and steps its backlight; the other reads the
steps with its light sensor. No radio, no pairing, no cable.

The app is the **proof of concept** for the **optical data transfer
protocol**, which carries any list of byte strings. A credential is the
smallest useful payload. **Files are the goal**, so speed matters. The
user declared the protocol complete on 2026-10-19, which lifts their
2026-10-13 rule that files wait for it: the file layer is next.

Status in one paragraph: **transfers work end to end in simulation**,
every scenario delivering. **On hardware, one badge facing a mirror** has
run every piece a transfer uses -- the ring reader, the slot-rate symbols,
the fast blocks through the protocol's own code -- and decoded them. **Two
badges have never completed a transfer: there is no second badge.**
The work is committed on branch `terrys_toys` as `055c262` ("completed
v2 protocol", 2026-10-19).

## 2. Rules for working here

The user's standing preferences:

- **Keep replies short.** Follow Orwell's six rules.
- **Do the work yourself.** Spawn other agents only for a narrow
  specialty, and tell them: read-only git, never touch the badge's serial
  port.
- **No commits** unless the user asks. **No state-changing git** (stash,
  checkout, reset, add, mv) at all.
- **When the work is done, check every doc against the code and the final
  results**, and say what you checked.
- **share_wifi is unreleased:** no backward compatibility. Both badges
  must run the same build.
- **Signal on the backlight only and read through the DMA ring**; no
  pixel signalling anywhere; the payload in fast blocks only.
- **`.bin` captures stay out of git** (`.gitignore` covers them).
- **One seed is not evidence**: sweep at least 8 seeds before concluding
  anything about the link (AGENTS.md, "One seed is not evidence").
- **Ignore `wifi_scanner` and `sand_squirrel`**: other work.

Two firmwares, and only one is the badge's (AGENTS.md, "Two firmwares"):

| | The badge | This repo's source and the emulator |
|---|---|---|
| Version | **v2.0.2** (`BW-1.27.0`, 2026-04-10) | v3 |
| Clock, PWM period | 200 MHz, 327.68 µs | 250 MHz, 262 µs |
| Panel refresh | 60 Hz | 90 Hz |
| Backlight floor | none | 12.5% duty |

So **the source in `modules/` and the emulator are not evidence about the
badge**. Every figure in the docs carries a marker naming the firmware it
holds for: `[measured-v2]`, `[measured-all]` (physical, any firmware),
`[source-v3]`, `[simulation]`, `[inferred-…]`, `[unmeasured]`
([.agents/docs/README.md](../docs/README.md)).

**The badge.** The user handles everything physical: copying the app
folder onto the badge in USB disk mode, opening Dev Mode's *USB Dev Link*,
placing the badge on a mirror, the light. Every measurement is a dev link
subtool, uploaded over the link and then run over it or from Dev Mode's
menu untethered. Once the link is open -- by hand, or with no hands by
`tests/share_wifi/autorun_host.py` -- you may run measurements with the
host program when the user asks
(`sg dialout -c "python3 tests/share_wifi/devlink_host.py OUTDIR run TOOL ARGS"`;
`sync` first after any change to `devtests/`), and say when the badge
needs moving ("The USB dev link" in [testing.md](../docs/testing.md)).

## 3. The app

User flow ("Screens" in [app-spec.md](app-spec.md)):

- **Share**: pick a saved network, *Line the badges up*, A. The badge is
  the **Leader** and sends.
- **Receive**: *Line the badges up*, A. The badge is the **Follower**;
  a credential that arrives is saved and joined.
- **Connect**: join a saved network. Open networks use an empty key; WEP
  is refused (the firmware cannot join it) but can be shared.
- **C held 5 s** on the main screen reveals **Dev Mode**: *Display Saved
  Measurements*, *Clear Saved Measurements* (the file and every stored
  tool) and the *USB Dev Link*, then one item per uploaded tool's `MENU`
  entry ("Dev Mode" in [app-spec.md](app-spec.md)).
- **B on the main menu** leaves the app.

*Line the badges up* draws a centre line: hold the other badge's edge on
it. The sensor sits ~1 mm outside the screen's right edge, so a flush hold
puts it over the peer's bezel; this hold puts it over the peer's screen.
During a transfer both screens are plain white, the case lights show
progress, B cancels. A badge whose ring cannot be made, or with no fast
pacer, refuses to transfer and says why.

A credential is three fields: SSID, key, security byte. Saved networks
live in `/state/share_wifi.json`; at launch the app adds the badge's own
network from `secrets`.

The app's files ([firmware/apps/share_wifi/](../../firmware/apps/share_wifi/),
overview in its [README](../../firmware/apps/share_wifi/README.md)):

| File | What |
|---|---|
| [`__init__.py`](../../firmware/apps/share_wifi/__init__.py) | the app: menus, storage, Wi-Fi, the transfer loop (`App.do_transfer`), Dev Mode, the fast-block sender (`fast_pacer`, `play_burst`) |
| [`protocol.py`](../../firmware/apps/share_wifi/protocol.py) | the protocol: state machine and wire format, pure Python, no badge globals |
| [`reader.py`](../../firmware/apps/share_wifi/reader.py) | `RingReader`: times and means from the DMA ring |
| [`fastadc.py`](../../firmware/apps/share_wifi/fastadc.py) | the ADC and DMA registers behind the ring, its viper kernels, `Ring.verify()` |
| [`fastblock.py`](../../firmware/apps/share_wifi/fastblock.py) | the fast block: encoder and decoder (`Decoder`) |
| [`fasttx.py`](../../firmware/apps/share_wifi/fasttx.py) | `Pacer`: plays a fast block by DMA paced on the PWM wrap |
| `devlink.py`, `devserver.py`, `usbport.py` | the USB dev link, badge side |

Everything under `firmware/` ships to the badge. Host-only code lives in
`tests/`; the measurement subtools and their libraries in
[`tests/share_wifi/devtests/`](../../tests/share_wifi/devtests/), which
reach the badge over the link.

## 4. The protocol

**The spec is the authority**:
[spec.md](../optical_data_transfer/spec.md), with its reasons in the
[context](../optical_data_transfer/context.md) and its checks in the
[tests document](../optical_data_transfer/tests.md). Those three link only
to each other, so they can be handed on alone. Its constants are checked
against the code by `tests/share_wifi/test_spec_matches_code.py`. **The
data behind every decision** -- every measurement and sweep, and the only
record of them -- is [data.md](../optical_data_transfer/data.md).

### 4.1 The physical layer

- **Every symbol is a backlight level** behind a white screen drawn once
  ([spec §4](../optical_data_transfer/spec.md#L149),
  [§5](../optical_data_transfer/spec.md#L167)). Data levels: low 0.5
  (dim, not off), middle 0.82, high 1.0; beacons use off and full. A change
  is live at the next PWM wrap, within 328 µs; the panel itself takes
  16-24 ms to settle, which is why pixels are not used
  ([data §2](../optical_data_transfer/data.md#L42), [§3](../optical_data_transfer/data.md#L89)).
- **The sensor** is a 12-bit ADC (one step = 16 counts). Off to full reads
  **76-112 counts** off a mirror -- five to seven ADC steps -- with 2-10
  counts of noise a reading. It answers in ~0.7-1.4 ms lit and 2.3-7.8 ms
  dark (90% of a step), and its bandwidth is 276 Hz dark, 406 Hz lit.
  **The sensor, not the backlight, limits the rate**
  ([data §3](../optical_data_transfer/data.md#L89)).
- **Reading**: the ADC free-runs at 50 kHz into a 16,384-reading DMA ring
  (327.7 ms) in PSRAM; viper kernels sum it. The protocol decides each
  slot after the fact from the ring's history, so a collection or a slow
  pass delays a decision but loses nothing
  ([spec §6](../optical_data_transfer/spec.md#L178),
  [data §4](../optical_data_transfer/data.md#L151)).

### 4.2 A transfer, step by step

1. **Discovery** ([spec §8](../optical_data_transfer/spec.md#L232)):
   the Leader flashes the *Announce* beacon (off/full, 14 ms halves),
   recognised by its period. The Follower stays dark until it sees it.
2. **Calibration** ([§9](../optical_data_transfer/spec.md#L267)): the
   Follower answers at once with a *staircase* -- low, high, middle, 20 ms
   each, once if its room is lit, twice if dark -- then 110 ms of dark.
   The Leader reads its thresholds from it; the repetitions set the pair's
   light (and so the start speed).
3. **Flicker**: each badge fits the room's mains flicker (LED lights
   flicker at 120 Hz, up to 47% of their light) in the dark gap, then
   corrects every reading and tracks the mains' drift
   ([§15](../optical_data_transfer/spec.md#L703)). If the Leader's fit found
   flicker it holds its first block back to 500 ms after the gap began
   (`CAL_GAP_FAST_FLICKER_MS`). If it did not hold and hears nothing,
   it sends the first block again: a Follower that found flicker took
   none of it as light (§9.7, §13.1).
4. **Slots** ([§10](../optical_data_transfer/spec.md#L428)): a slot clock,
   8 ms lit and 16 ms dark, on a ladder down to 32 ms, for the control
   patterns only. A receiver re-locks onto the sender's first edge out of
   black at every message.
5. **Data** ([§13](../optical_data_transfer/spec.md#L536),
   [§14](../optical_data_transfer/spec.md#L593)): **fast blocks** (4.3 below),
   each answered with CRC Good or Bad. A Follower that misses a Begin asks
   again with a Data Sync Error, which steps the slot rate down. There is
   no slower fallback (since 2026-10-18).
6. **Complete**, acknowledged; both end with an exit value
   ([§17](../optical_data_transfer/spec.md#L783)).

The badges' roles are fixed by the menu choice. Every wait has a deadline
([§16](../optical_data_transfer/spec.md#L764)).

### 4.3 Fast blocks

([spec §13.1](../optical_data_transfer/spec.md#L538),
[§14.2](../optical_data_transfer/spec.md#L662),
[Appendix A](../optical_data_transfer/spec.md#L937))

- **The burst**: 2-level PAM on the backlight, a symbol 2, 3, 4 or 6 PWM
  periods long (rungs **F0-F3**, 0.66-1.97 ms), each duty written by DMA on
  the PWM wrap so every boundary falls on the sender's crystal. A 66-symbol
  preamble, a 4-byte header, up to 128 bytes (F0), a CRC-32; runs of the
  low capped at 4 by bit stuffing; under 1 s on air.
- **Around it**, at the slot rate: a lead-in, a fast Begin pattern, the
  rung in two bits, two dark slots; afterwards the Follower's ACK or NAK.
- **Rungs**: F0 if lit, F1 if dark; two NAKs on a rung step down; past F3,
  F3 again until the block's 48 attempts are spent (`ERR_CRC`); after four
  ACKs below the start rung the Leader steps back up.
- **The receiver** bins the ring at 100 µs and decodes with a linear
  equaliser trained on the preamble, with regressors for the mains flicker.
  **It feeds the decoder nothing until the burst is over**: training takes
  300-434 ms on the badge, longer than the ring holds. The first block on a
  rung **trains**; later ones **retune** (54-70 ms): the taps kept, only the
  flicker terms and constant refitted.

## 5. What has been proved, and how

| Evidence | Where | Result |
|---|---|---|
| The DMA ring on the badge | `fastadc.Ring`, 2026-10-07 onward | 49,998-49,999 readings/s, no loss through presents, collections or a 0.82 s blocking burst [measured-v2] ([data §4](../optical_data_transfer/data.md#L151)) |
| Slot-rate symbols on the mirror (M5 loopback) | mirror A and B | 0 errors at 16, 13, 10 and 8 ms, lit and dark [measured-v2] ([data §7](../optical_data_transfer/data.md#L238)) |
| F0 bursts on the mirror | 2026-10-10 | 0 errors in 32,496 symbols per room [measured-v2] ([data §10.2](../optical_data_transfer/data.md#L401)) |
| The transfer's own fast-block code on one badge (`fast_loopback`) | 2026-10-12 to 18 | old streaming decoder: about half lost; deferred: every block; retuning: 40 of 40; under room light 8 of 8, but never with a flicker fit (the rooms read steady, or flickered too hard) [measured-v2] ([data §10.4-10.6](../optical_data_transfer/data.md#L424)) |
| Whole transfers | the link simulator | the 24 standard rows 144 of 144 at 8 seeds; the twelve hard rows 1296 of 1296 at 72 [simulation] ([data §11](../optical_data_transfer/data.md#L508)) |

Speed, both badges facing to done, skew-free medians [simulation]:

| | Steady lit | 47% flicker | Dark |
|---|---|---|---|
| A credential | **1.4 s** | 1.7-1.8 | 2.1 |
| A credential in the slot blocks removed 2026-10-18 | 2.0 | 2.7 | 3.8 |
| A 2 KB payload | **110 B/s** | 107 | 47 |

**What the mirror cannot show**: a second badge's light, edge lag and
settle; the two crystals' offset (it sets the 1 s block cap); light
reflecting back off the peer's glass onto a badge's own sensor
([data §13](../optical_data_transfer/data.md#L561)).

## 6. Where the code does what

`protocol.py` ([file](../../firmware/apps/share_wifi/protocol.py)):

| Piece | Line |
|---|---|
| `Transfer` -- one badge's transfer; `work(t)` runs everything due by `t` µs, `due_us()` says when next, `field_at(t)` the symbol to show | [L1418](../../firmware/apps/share_wifi/protocol.py#L1418), work [L1639](../../firmware/apps/share_wifi/protocol.py#L1639) |
| Discovery: `step_announce`, `step_listen` | [L1805](../../firmware/apps/share_wifi/protocol.py#L1805), [L1863](../../firmware/apps/share_wifi/protocol.py#L1863) |
| Calibration: `StaircaseReader`, `settle_staircase`, `step_cal_gap`, `follower_fit` | [L1154](../../firmware/apps/share_wifi/protocol.py#L1154), [L1820](../../firmware/apps/share_wifi/protocol.py#L1820), [L1923](../../firmware/apps/share_wifi/protocol.py#L1923), [L2326](../../firmware/apps/share_wifi/protocol.py#L2326) |
| Flicker: `FlickerFilter` | [L188](../../firmware/apps/share_wifi/protocol.py#L188) |
| Slots: `SlotClock`, `try_lock` (phase lock), `rebase`, `demote` | [L1331](../../firmware/apps/share_wifi/protocol.py#L1331), [L2144](../../firmware/apps/share_wifi/protocol.py#L2144), [L2197](../../firmware/apps/share_wifi/protocol.py#L2197), [L2214](../../firmware/apps/share_wifi/protocol.py#L2214) |
| Data phase: `start_data`, `work_data`, `decide_slot`, `leader_rx`, `follower_rx`, `collect_block` | [L1954](../../firmware/apps/share_wifi/protocol.py#L1954), [L2259](../../firmware/apps/share_wifi/protocol.py#L2259), [L2843](../../firmware/apps/share_wifi/protocol.py#L2843), [L2944](../../firmware/apps/share_wifi/protocol.py#L2944), [L2994](../../firmware/apps/share_wifi/protocol.py#L2994), [L3042](../../firmware/apps/share_wifi/protocol.py#L3042) |
| Asking again: `request_resend`, `send_sync_error` | [L2983](../../firmware/apps/share_wifi/protocol.py#L2983), [L3071](../../firmware/apps/share_wifi/protocol.py#L3071) |
| Fast blocks: `send_block`, `fast_listen`, `fast_reply_rx`, `fast_receive`, `burst_over` | [L2006](../../firmware/apps/share_wifi/protocol.py#L2006), [L2452](../../firmware/apps/share_wifi/protocol.py#L2452), [L2468](../../firmware/apps/share_wifi/protocol.py#L2468), [L2544](../../firmware/apps/share_wifi/protocol.py#L2544), [L2621](../../firmware/apps/share_wifi/protocol.py#L2621) |
| Payload: `fast_stream`, `parse_fast_stream`, `fast_block` | [L3078](../../firmware/apps/share_wifi/protocol.py#L3078), [L3104](../../firmware/apps/share_wifi/protocol.py#L3104), [L3097](../../firmware/apps/share_wifi/protocol.py#L3097) |
| The constants, each with its reason in a comment | from [L28](../../firmware/apps/share_wifi/protocol.py#L28); fast-block ones from [L773](../../firmware/apps/share_wifi/protocol.py#L773) |

`fastblock.Decoder`: `begin` [L545](../../firmware/apps/share_wifi/fastblock.py#L545), `feed` [L594](../../firmware/apps/share_wifi/fastblock.py#L594), `train` [L732](../../firmware/apps/share_wifi/fastblock.py#L732), `retune` [L773](../../firmware/apps/share_wifi/fastblock.py#L773); the viper kernel `_run` [L283](../../firmware/apps/share_wifi/fastblock.py#L283). `reader.RingReader` [L86](../../firmware/apps/share_wifi/reader.py#L86): `check` (once a block), `parts`, `rise`, `bins`.

## 7. Tools

| Tool | What it does |
|---|---|
| `python3 tests/run_tests.py` | the suite, all passing. Set `MPY_CROSS` (mpy-cross) and `MICROPYTHON` (the unix port) to run the tests that compile for the badge or run under real viper; otherwise they skip ([testing.md](../docs/testing.md)) |
| `tests/share_wifi/simulator.py` | two badges' real code on one timeline against a model of the measured hardware, asserting the link invariants every tick ([invariants](../optical_data_transfer/invariants.md)) |
| `tests/share_wifi/sweep.py ROW…` | the simulator over 8 seeds x 6 scenarios x 3 payloads a row; `SEED0`/`SEED1` for more seeds, `PATCH=NAME=value` to try a constant. The rows to run and the figures to beat: [testing.md](../docs/testing.md) and [data §11](../optical_data_transfer/data.md#L508) |
| `tests/share_wifi/devlink_host.py OUTDIR` | the host side of the USB dev link: `sync` uploads every subtool and library, `run TOOL ARGS` runs one and saves its output |
| `tests/share_wifi/autorun_host.py OUTDIR "run …"`, `tests/usbrun.py` | the same with no hands: reset the badge, put this tree's app in `/runs/`, open the link, run |
| `tests/share_wifi/devtests/` | the subtools: `mirror`, `second_badge`, `frame_probe`, `room_flicker`, `fast_loopback` (the transfer's fast-block code on one badge), `files`, `reset`; `lib/` their libraries |
| `tests/share_wifi/fast_loopback_host.py` | prints and judges a saved `fast_loopback` run |
| `tests/share_wifi/devlink_examples.py DIR` | writes the link's example subtools (`hello`, `spin`, `ring_trace`) for the hardware checklist |

**How the simulator earns trust**: its sensor model was fitted to the
badge's measured response; its ring, collections, decoder training and
retune costs are the badge's. It missed one real failure until 2026-10-12
(its ring never overwrote and it priced training at 75 ms); fixed, it
reproduced the badge's losses ([data §10.4](../optical_data_transfer/data.md#L424)).

## 8. Recent changes

- **2026-10-19: the docs reorganised.** The protocol's documents are in
  `.agents/optical_data_transfer/`, the app's in `.agents/share_wifi/`;
  every measurement and sweep is summarised in `data.md`, and the raw
  files, the dated history and the sweep outputs are deleted, except the
  mirror captures a test decodes. The one-off research tools and subtools
  went too.
- **2026-10-19: a hold only the Follower expects is survived** (data §8).
  Fitting before the staircase was tried and rejected: +0.3 s in flicker.
- **2026-10-18: fast blocks only** (data §9): slot blocks, a Follower
  without the fast module, the fallback after F3, rungs slower than 32 ms
  and breaking in are gone. A badge with no fast pacer cannot share.
- **2026-10-18: Dev Mode off the badge.** Three items plus the uploaded
  tools' entries; every tool a dev link subtool; the app's source 552 →
  290 KB.

**On the badge, 2026-10-19** (mirror B, dark, tethered, over the link):
this build's app and dev link, the subtools synced and loading, `mirror
dark` (M5 0 errors at 16-6 ms), `fast_loopback` through the fast-only
protocol (12 of 12, every rung), `room_flicker` (steady) and
`frame_probe` -- data §5, §7, §10.6. Not yet: a lit run, the Dev Mode menu
entries run untethered, and the hard cancel on this build.

## 9. What is left

([tasks index](../tasks/README.md); each task is one file in
[.agents/tasks/](../tasks/))

**Blocked on a second badge** (none is available): transfers each way;
the clock offset between two badges; a real peer's light and lag; starting
dark pairs on F0 and on 10 ms slots; the one-period scheme. These are
[second-badge-measurements](../tasks/optical_data_transfer/second-badge-measurements.md),
[fast-blocks-data](../tasks/optical_data_transfer/fast-blocks-data.md),
[short-handshake](../tasks/optical_data_transfer/short-handshake.md),
[fast-staircase](../tasks/optical_data_transfer/fast-staircase.md),
[short-gaps](../tasks/optical_data_transfer/short-gaps.md) and
[apply-hw1-results](../tasks/optical_data_transfer/apply-hw1-results.md).

**One badge, a light that flickers**: a flicker-fitted fast-block decode
on the mirror ([fast-blocks-data](../tasks/optical_data_transfer/fast-blocks-data.md)): `run
fast_loopback 0 8 0 defer fit` where the fit finds flicker -- room light
~150-500 counts at the sensor, flicker ~20-250.

**On the badge, still to do** (one badge): the lit runs (`mirror lit`,
`fast_loopback` lit); the tools from Dev Mode's menu, untethered; the
hard cancel (B, and Ctrl-C at the interactive host) on this build.

**An agent, no hands:** nothing open.

**Files, unblocked 2026-10-19** (the protocol complete, the user):
the file layer of
[file-transfer-speed](../tasks/optical_data_transfer/file-transfer-speed.md)
-- announcement, resume bitmap, storing -- needs no hardware to build.

**v3**: nothing has run on a v3 badge
([v3-dim-low](../tasks/optical_data_transfer/v3-dim-low.md)).

## 10. Traps already hit

- **MicroPython on the badge mishandles `**kwargs` in a call, `%r`, and
  `global` after use**; lint does not catch them (AGENTS.md, "The rules
  that actually bite").
- **Allocation is dear on v2**: a 4-item tuple 65-155 µs; a function whose
  frame passes 11 words allocates every call (AGENTS.md, "Allocate outside
  hot loops").
- **The dev link uploads at most 64 KB**; `new` refuses a name that exists
  (`--force`, or `remove` it first).
- **Never write USB or read the ADC while the ring runs.**
- **Never take timing figures inside an open dev link session**:
  collections double there.
- **`run()` clears the screen every frame and a return value quits the
  app**; button constants are bare globals (AGENTS.md;
  [badgeware-patterns.md](../docs/badgeware-patterns.md)).
- **A green suite covers only what it tests**: test at the seams
  (AGENTS.md, "Test at the seams").

## 11. Things written nowhere else

- **`sand_squirrel` fails CI's lint** (`rom_font` undefined); lint only
  `firmware/apps/share_wifi/` to check your work:
  `ruff check --config ci/ruff.toml firmware/apps/share_wifi/`.
- **The suite prints `FAIL` lines on success.**
  `test_fast_loopback.test_the_summary_names_every_rule_that_failed` runs
  the host checker on a deliberately broken record. Trust the final
  `N passed, M failed` line.
- **The tests that need MicroPython** skip unless `MPY_CROSS` and
  `MICROPYTHON` point at builds of the `bw-1.27.0` branch: the
  README's quick start says how. Compile viper code for the badge with
  `mpy-cross -march=armv7emsp`.
- **The badge now** (2026-10-19): v2.0.2, holding this build of
  share_wifi and, in `/devtests`, every current subtool and library. It
  sits tethered in a box over mirror B, ~1 cm from the glass; closed, its
  black read 43-50 counts.
- **Light**: the user has only LED room lights. Opening the box read black
  55-58 counts, classed dark; a lit run needs the box angled to the light
  until black reads ≥ 64. Moving a light during a capture ruins it.

## 12. Documents

| Question | Document |
|---|---|
| Where to start; all the documents | [.agents/README.md](../README.md) |
| How to work in this repo | [AGENTS.md](../../AGENTS.md) |
| What the protocol must do | [spec](../optical_data_transfer/spec.md), [context](../optical_data_transfer/context.md), [tests](../optical_data_transfer/tests.md) |
| The data behind each decision | [data.md](../optical_data_transfer/data.md) |
| The link invariants the simulator asserts | [invariants.md](../optical_data_transfer/invariants.md) |
| What the app does on top | [app-spec.md](app-spec.md) |
| The badge's hardware and the API on v2 | [badgeware-hardware.md](../docs/badgeware-hardware.md), [badgeware-api-v2.md](../docs/badgeware-api-v2.md) |
| How to write badge code | [badgeware-patterns.md](../docs/badgeware-patterns.md) |
| Tests, the dev link, its checklist | [testing.md](../docs/testing.md) |
