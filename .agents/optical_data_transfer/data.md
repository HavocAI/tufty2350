# Optical link: the data

Every measurement and simulation result that a decision about the optical
link rests on, with the decision it supports. **This is the only record of
the data**: the raw files, dated run logs and sweep outputs it was drawn
from were deleted on 2026-10-19, except the mirror captures a test decodes
(§14). Where an old design was dropped, the figure that dropped it is kept.

- **What to build** is in the [spec](spec.md); **why each value**, in the
  [context](context.md); **how to check it**, in the [tests](tests.md).
- To measure again, the subtools and the simulator are in §15.

Every figure carries a marker naming the firmware it holds for:

| Marker | Means |
|---|---|
| [measured-v2] | measured on the badge, which runs v2.0.2 (BW-1.27.0) |
| [measured-all] | measured on that badge, but physical (sensor, panel, light): any firmware |
| [source-v2], [source-v3] | read in the firmware source at that version |
| [simulation] | the link simulator: both badges' real code against a model of the hardware |
| [inferred-…] | reasoned from other figures |
| [unmeasured] | not known |

**No transfer has run between two badges.** Every hardware figure is one
badge reading its own light off a mirror; every transfer figure is
simulation.

## 1. Setup

| | |
|---|---|
| Badge | Tufty 2350, firmware v2.0.2 (`BW-1.27.0`, 2026-04-10), 200 MHz |
| This repo's firmware source, the emulator | v3, 250 MHz: not evidence about the badge |
| Mirror A | 3.5 mm back-silvered glass, badge flat on it: the reference for swings, settles and lags |
| Mirror B | 1.37 mm back-silvered glass, badge ~1 cm above it in a box: closed = dark, open = lit. From 2026-10-09 |
| Light for lit runs | the user's LED room light only; the box angled for more |
| Lit | a badge whose black reads ≥ 64 counts (`LIT_BLACK_COUNTS`) |
| Count | one `light_level()` unit; one ADC step = 16 counts |
| Sweep row | 8 seeds x 6 start scenarios x 3 payloads (a WPA2 credential, an open one, the longest) = 144 runs; "72 seeds" = 1296 |
| Simulator rows | `lit`, `mirror` (dark), `lit30`/`lit47` (30/47% LED flicker), `led50`/`led60` (LED flicker, mains drifting), `half50`/`half60` (a light flickering once a mains cycle: none measured does), `aligned`/`overlap` (a peer's predicted swing), `k2.0`/`k3.0` (sensor 2x/3x slower), `worst` (slow and noisy), `gc70` (65-81 ms collections), `self5` (5% of a badge's own light on its sensor), `v3`, `v2v3`/`v3v2` (mixed pair) |

## 2. The platform (v2)

| | Value | Marker |
|---|---|---|
| System clock | 200 MHz (`machine.freq()`) | measured-v2 |
| Backlight PWM | 65,536 counts at divider 1: 327.68 µs, 3,052 Hz | measured-v2, source-v2 |
| Panel refresh | 16.71 ms (59.8 Hz); no `display.set_framerate` | measured-v2 |
| Writing a frame | 7.78 ms; a present blocks ~12.4 ms at 60 Hz | measured-v2 |
| `screen.clear()`, HIRES | 11.4 ms | measured-v2 |
| Panel's own settle, full swing to 90% | 16-24 ms (datasheet: 16 ms round trip) | measured-all |
| A Python call | 5.1 µs, every run from 2026-10-05 | measured-v2 |
| An empty timed loop (the probe's) | 41 µs, later 76-81: the probe timing its own bookkeeping, not the heap | measured-v2, inferred-v2 |
| Allocating a 2-item / 4-item / 8-item tuple | 2.9 µs / 65-155 µs / 330-650 µs (16-byte GC blocks), varying run to run | measured-v2 |
| `gc.mem_alloc()`, `mem_free()` | ~110 ms each: a walk of the whole table | source-v2 |
| `badge.light_level()` | ~8 µs over its loop; read from the registers, 2.8 µs | measured-v2 |
| Chip temperature over the runs | 25-32 °C | measured-v2 |

**Collections** follow where live objects sit, not only how many bytes:
0.31 ms + 0.049 ms per KB live + 0.00708 ms per KB up to the highest live
block, within 0.28 ms over 42 readings (2026-10-13) [measured-v2]. That
block is wherever the app loaded, and v2's `launch()` imports an app
without collecting first, so garbage the launcher left sets it:

| Context | Heap in use | `gc.collect()` |
|---|---|---|
| a minimal app, share_wifi not loaded | 72-92 KB | 10.3-11.9 ms |
| share_wifi, from the menu | 397-450 KB | 26.6-31 ms |
| share_wifi, fresh launch | 272-449 KB | 26.3-54 ms: extent ~1.9-4 MB |
| share_wifi over USB, 0 / 2 / 4 / 6 MB of garbage first | 261 KB | 17.1 / 29.0 / 43.2 / 57.3 ms |
| inside an open dev link session | 466-475 KB | 67-81 ms |
| after a fast block | -- | 41-47 ms dark, 63-74 lit |

Decisions:
- **No pixel signalling.** A present blocks 12.4 ms and the panel settles
  in 16-24 ms; a backlight change is live at the next PWM wrap (≤ 328 µs).
  Every symbol is a backlight level behind a white screen drawn once.
- **Never take timing over an open dev link session**: collections double
  there. Timing figures come from a run started by the link and then
  closed, or from the menu.
- **The simulator's collections**: 25-46 ms by default, 65-81 in the `gc70`
  rows: a transfer on v2 may meet ~17 to ~80 ms, and `gc70` bounds the
  worst. A stub `__init__.py` that loads the app low would save ~3% of a
  file's time: not worth it.
- **Allocate nothing per slot.** A 4-item tuple can cost a slot's budget.
- **Keep a per-call frame under 11 words**: a larger one is heap-allocated
  on every call (`correct()` cost 0.43-0.73 ms until rewritten).

## 3. The light sensor and the backlight

### Reading

- **12-bit ADC in a 16-bit frame**: every reading a multiple of 16 counts
  [measured-all]. → `SENSOR_STEP` 16, `QUANTISATION_SD` 4.62.
- **Position**: 1 mm outside the right edge of the active area, mid height;
  it sees a patch near itself [measured-all]. Two badges facing are
  mirrored. → The whole screen is white during a transfer; the alignment
  screen puts half the peer's screen over the sensor.
- **It averages the PWM** at every duty 0.25-1.0 [measured-v2]: the sensor
  responds in 0.2-7 ms against a 328 µs period.
- **Noise**: one reading sd 2-10 counts, peak to peak two ADC steps; a mean
  of hundreds of ring readings ~1 count [measured-all]. Averaging falls as
  √n to 16-64 readings, then flattens at ~0.5 count [measured-v2].
- **Swing**, off to full on white: mirror A 80 counts dark, 80-96 lit; mirror
  B 76-89 dark, 85-112 lit [measured-all]. A sensor partly blocked read 64.
  A peer is predicted ~95 aligned edge to edge, ~210 overlapping the sensor
  by 2-5 mm, from the solid angle it fills [inferred-all, unmeasured]
  ([optical coupling](optical-coupling.md)).
- **Room light at the sensor**: black 32-33 counts dark, 48-96 lit on the
  mirror; 178-411 under the user's LED light at 1 cm; 1,242-2,794 near a
  strong LED [measured-all]. → `LIT_BLACK_COUNTS` 64.

### Levels

| Level | Value | v2 duty | Why |
|---|---|---|---|
| high | 1.0 | 100% | full |
| middle | 0.82 | 57% | 0.74 read two ADC steps above black on six mirror runs [measured-all]; 0.25/0.33 failed calibration every time, 0.93 sat on high [simulation] |
| low (data) | 0.5 | 14% | under one ADC step above off, but keeps the sensor lit: the dark edge delay fell from 1.2-1.9 ms to 0.3, and a loopback block ran at 25 and 16 ms with no error [measured-all, 2026-10-06] |
| beacon low | 0.0 | 0 | off on both firmwares; v3's 12.5% floor lifts 0.5 [source-v3] |

- **Backlight curve**: duty `v^2.8`, no floor on v2 [source-v2]; the LED
  driver gives no light below 7-9% duty, a value of ~0.45 [measured-all].
- **Full-range white**: `color.white` is tinted rgb(222, 238, 214) on this
  build [source-v2]; the transfer draws rgb(255, 255, 255).
- **Faster PWM buys nothing**: at 12.2 and 24.4 kHz the bandwidth is
  233-331 Hz, no better, and the curve dead to 16-20% duty, saturated above
  ~70% [measured-all, 2026-10-09]. → The stock PWM.

### How fast the sensor follows

- **Slower in less light** [measured-all, 2026-10-05]: 90% of a step in
  1.0-1.3 ms lit; dark, off to full 3.87 ms, a fall 3.24, off to middle 7.75.
- **Through the ring, the scheme's own levels** (M4) [measured-all]:
  worst t90 on mirror A 1.38 ms lit, 2.40 dark; on mirror B 0.67-0.90 lit,
  1.72-2.30 dark (2026-10-13, -19). `T_low` crossed 0.40 / 0.51 ms lit (rise to high
  / to middle), 0.75 / 1.28 dark. → 20 ms staircase steps hold enough
  settled samples (they would fail past a ~5 ms t90).
- **Model** `ds/dt = (I - s) s / K`, K 36 counts·ms; fits on arbitrary
  multi-level sequences gave K 28-40 dark, 30-54 lit, and this model
  fitted them best (2026-10-09) [measured-all]. → The simulator's sensor.
- **Bandwidth**: -3 dB at 276 Hz dark, 406 Hz lit. A plain slicer decodes 2
  levels error-free to 1 ms symbols, 4 levels to 2 ms, 8 levels to 8 ms
  [measured-all, 2026-10-09]. → The sensor, not the backlight, limits the
  rate; extra levels are worth less than shorter symbols.
- **The backlight call lands late**: 346-358 µs mean after the slot
  boundary, at most 489; a transfer's own path adds ~150-166 µs
  [measured-v2]. A DMA-paced burst starts 46-77 µs after its boundary
  [measured-v2, 2026-10-12].

## 4. The DMA ring

- **Works on v2** [measured-v2, 2026-10-07, and every run since]:
  49,998-49,999 readings a second, quiet or presenting; no FIFO overrun,
  fault or anchor drift; a 28.8-54 ms collection mid-capture lost nothing;
  a 0.82 s blocking burst lost nothing. Kernel 0.31-0.32 µs a reading over
  the uncached alias; anchor bracket 12-14 µs.
- **What it replaced**: a protocol call once a reading cost ~1 ms; a polled
  listener reached 2,166 passes a second at best and was blind 12.4 ms of
  every present [measured-v2].
- **Capacity**: 16,384 readings, 327.7 ms; 317.7 ms usable.

Decisions: **every reading comes through the ring**; the protocol decides a
slot after the fact from its history; **no polled fallback** (a polled tier
cost a mixed pair half its rate and a calibration flag, and every supported
firmware has the ring [measured-v2]).

## 5. What the protocol's work costs on v2

| Work | Cost | Marker |
|---|---|---|
| A decided slot, lit 8 ms rung, no flicker, decided middle | 2.35-2.45 ms mean, 3.2 max | measured-v2 |
| The same decided low, the learner learning (no fit) | 4.10; 3.86 mean, 4.77 max on 2026-10-19 | measured-v2 |
| With a fitted flicker: a middle slot / a low slot (it tracks) | 2.62 (2.98 max) / 4.23 (4.93 max); 2026-10-19 2.41 (5.64) / 3.12 (5.11) | measured-v2 |
| `amount()` / `track()` / `update()` | 0.16-0.20 / 0.21-0.24 / 0.39-0.46 ms | measured-v2 |
| The steadiness check (a line's rms) | 1.23 ms on 50 samples, 0.83-0.88 on 30, 1.62 on 80 | measured-v2, 2026-10-13, -19 |
| The flicker fit, four harmonics / six / both tried | 101-105 / 141 / ~245 ms on 50 samples; 155 on 80 | measured-v2, 2026-10-14, -19 |
| Fast block: decoder training, three starts | 300-434 ms, in clusters near 320 and 425 | measured-v2, 2026-10-12 |
| Fast block: a retune | 54-70 ms | measured-v2, 2026-10-12 |
| Fast block: deciding a symbol (viper) | ~5 µs | inferred-v2 |

The simulator charges these: a check 4 ms, the fit 150 ms, a training 318
or 426 ms, a retune 56-66 ms.

Decisions:
- **6 ms is off the ladder**: a fitted low slot's 4.93 ms max exceeds the
  ~3 ms a 6 ms slot leaves. Lit pairs start at 8 ms.
- **A steady room skips the fit**: the check costs ~1 ms against ~105-140.
- **The Follower decodes a fast block only after it is over** (§10.4): a
  training outlasts the ring.
- **Later blocks retune** instead of training (§10.5).

## 6. Room light

**Every LED light measured flickers at twice the mains**, not as a sine
[measured-all, 2026-10-06 to 10-18]. Sizes in counts:

| Room | Mean | 120 Hz | 240 Hz | 360 Hz | Fit | Left in an 8 ms window |
|---|---|---|---|---|---|---|
| 1, 2, 3, 7 | 79-985 | ≤ 1.7 | ≤ 0.2 | -- | steady | 0.5-1.4 |
| 4 | 88 | 7.1 | 2.6 | -- | 120 | 2.2 → 0.8 |
| 6 | 497 | 22.0 | 5.0 | -- | 120, 240 | 10.2 → 0.7 |
| the user's LED, mirror B at 1 cm (2026-10-13, -18) | 79-411 | ≤ 3.8 | -- | -- | steady | -- |
| a strong LED, further off (2026-10-14) | 516 | 232 | 70 | 0.7 | fitted: sd 158.6 → 2.9 | -- |
| a strong LED, near (2026-10-14) | 2,794 | 1,312 | 765 | 114 | refused with four harmonics; ~6 left with the line refit and six | -- |
| 5, facing a lamp | 3,637 | 1,688 | 983 | -- | refused | 334 |

- **Mains drift**: 6-14 mHz against this badge's clock, wandering 2-17°
  over 24 s [measured-v2]. → The flicker hold 500 ms (§8).
- **Fit, not learn**: 47% flicker at the lit mirror's light is ±22 counts,
  more than an ADC step, and an 8 ms window is under half a 120 Hz cycle. A
  filter learning only from dark slots delivered 77 of 144 with the mains
  0.02 Hz off, and 0 under a once-a-cycle light [simulation]. → Each badge
  fits the flicker once, then corrects every part from a table and tracks
  the drift. The learner stays, for what a fit leaves.
- **The line refitted in each pass**: over 100 ms a sine is not
  independent of a ramp; a line taken off first left 3-5% of any flicker
  and refused any over ~250 counts [simulation, 2026-10-14].
- **Four harmonics first, six only when four leave more than 12 counts**:
  six always fitted the sensor's own distortion as flicker and a bright
  burst read 3 ms early [simulation].
- **Keep harmonics down to 2 counts in a room that flickers**: a dropped
  3.8-count third harmonic left one phase in five 11-20 counts off and lost
  once-a-cycle runs; a room whose strongest is under 4 is still steady
  [simulation, 2026-10-13].
- **Steady-room skip**: a straight-line rms under 1.87 counts leaves no room
  for a harmonic the fit would keep; it never skipped one that would
  [simulation, 200 windows].
- **Fast blocks need the flicker regressors, tracked**: with the mains 0.3
  Hz off, fixed regressors made 274 errors in 16,000 symbols, tracked 0
  [simulation]. On the mirror at one period under LED light, without them
  the errors clustered at up to half the symbols; with them 0-0.54%
  [measured-v2, 2026-10-12].
- **A light this strong this close defeats any signal**: under the near
  strong LED (black 1,242, the mirror's swing 112) fast blocks decoded 0 of
  8, the fit refused [measured-v2, 2026-10-14]. Two badges facing shade
  their sensors; how much room light then reaches one is [unmeasured].

## 7. Slot-rate symbols on the mirror

**M5 loopback** (a block sent on the backlight, decoded by the real
receiver): **0 errors at 16, 13, 10 and 8 ms, lit and dark**, on mirror A
(2026-10-07, -09) and mirror B (10-09, 10-13, 10-19), and at 6 ms on most
runs (2-4 errors on two; 6 ms is off the ladder) [measured-v2].

**Edge lag** (slot boundary to the receiver's `T_low` crossing):

| Run | Lit | Dark |
|---|---|---|
| 10-07, mirror A | 802 µs (539-1043) | 1511 (915-2349) |
| 10-09, mirror A | 796 (706-865 by rung) | 1251 (1148-1313) |
| 10-09, mirror B | -- | 1423 (1340-1580) |
| 10-13, mirror B | 519-671 means, maxima ≤ 740 | 1200-1326 means, maxima 1693-1812 |
| 10-19, mirror B (black 50) | -- | 1025-1174 means at 16-8 ms, maxima ≤ 1481 |

A transfer transmits ~150 µs later than the loopback. → **`EDGE_LAG_EXACT_LIT_US`, `_DARK_US`
950 µs lit, 1650 dark**, the conservative end. One dark run's maxima passed
1650 with nothing failing; set to 1100 or 2200, eight dark rows still
deliver 144 of 144 [simulation].

**Settle and guard** (lateness 0.49 ms + t90 + an early lock + the
transmit path) [measured-v2]: lit 2.28 ms → `SETTLE_LIT_US` 2500; dark 3.64
ms, ~4.2 at a dimmer light → 5000. A rise to the middle crosses `T_low`
0.53 ms after one to high → `GUARD_DARK_US` 2000.

**Start rungs**: lit 8 ms (6 is off the ladder, §5). Dark 16 ms, not 10:
at 10 ms the measured sensor delivers 144 of 144 in every dark row, a
sensor 2x slower 138, 3x slower 52 -- against 144 and 106 at 16 ms
[simulation]. Held until a second badge measures a peer's light.

**Separability**: adjacent levels 32-48 counts apart, 4-8 sd of one
reading [measured-all].

| Swing | `MIN_GAP` 32 | `MIN_GAP_EXACT` 16 |
|---|---|---|
| 80 (mirror A), 95 (aligned peer) | 144 / 144 | 144 / 144 |
| 64 (an obstructed sensor) | 0 | 144 |
| 56, with slow sensor, noise, flicker or v3 | 0 | 144 each |
| 48 | 0 | 0 (span under `MIN_SWING`) |

[simulation] → `MIN_GAP_EXACT` 16, `MIN_SWING` 48, `GAP_SIGMAS` 4 (over
1296 runs, 4 refused 57 links of which 45 would have delivered; 5 refused
256; 6 refused 499).

## 8. The handshake [simulation unless marked]

| From both badges facing, skew-free | Steady lit | 47% flicker | Dark |
|---|---|---|---|
| Two staircases, 40/64 ms beacons (until 2026-10-11) | 2.6 s | 3.5 | 4.4 |
| One Announce at 14 ms, one staircase, slot blocks | 2.0 | 2.7 | 3.8 |
| The same, fast blocks, the hold 500 ms (now) | **1.4** | **1.7-1.8** | **2.1** |

- **Announce half 14 ms**, detected from 2 ms samples: ±20% is 11.2-16.8
  ms, clear of mains flicker's 4.2-5 ms halves and a once-a-cycle light's
  8.3-10. On the mirror it matched 42-44 ms in, swing 103-110, and never at
  twice the period [measured-v2, 2026-10-13]. Backlight beacons, because
  pixel beacons would wait ~19 ms for a present and 16-24 ms for the panel.
- **One staircase, the Follower's**: the Leader's only told the Follower
  its levels, which the first Begin shows in a known order.
- **20 ms steps**: 6-9 settled samples a step at the measured t90
  [measured-all]; 16 ms saved ~10 ms, 14 ms lost half the flickering runs.
- **The Leader reads on a 20 ms moving mean**: uncorrected, a once-a-cycle
  light hid the closing gap (`half50` 0 of 144).
- **The Leader fits on the gap, after the staircase**: before it, the
  Follower is dark only ~50-90 ms. Fitting at 100 ms into the gap took 47%
  flicker rows from 6 of 144 to 144.
- **The Follower decides no slot before its fit** (`half50` 12 → 144 of
  144); **its check's median is the provisional black** (`v3v2/lit` 136 →
  144).
- **Flicker hold 500 ms** from the gap's start, since 2026-10-14 (800
  before), from the measured drift: at 0.01 Hz every hold from 200 to 800
  delivered every flicker row; with none most failed; at 0.02 Hz 400 lost 2
  of 1296.
- **No light taken during the hold** (`half60` 430 → 432 of 432).
- **A hold only the Follower expects** (2026-10-19): a Leader whose fit
  found the room steady facing a Follower whose fit found flicker sends at
  once, into the hold, and was missed whole; forced in `lit47`, seed 1 of
  8 ended `ERR_LOST_PEER`, the Follower giving up 1.55 s before the
  Leader's reply wait ran out. → The Leader sends its first block again
  after a silent reply wait; the Follower waits for it (`first_grace`)
  until a Begin frames, not until its first light (the missed burst's
  tail). Forced, 8 of 8 in `lit47` and `half60`, ~1.4 s slower; every
  standard run otherwise identical. No sweep row produces the mismatch on
  its own.
- **Rejected, 2026-10-19: the Follower fitting before its staircase**, on
  the Announce's off halves, its flag in the staircase order. The flag was
  read right in all 480 runs checked and every row delivered 144, but
  flicker rooms went 1.8 → 2.1 s and `k2.0`/`k3.0` +0.4-0.6 s: the
  Follower's fit stall moved out of the gap, where it overlaps the
  Leader's. The hold could not shrink to pay for it: at 300 ms 2.0 s, at
  200 the `gc70` rows delivered 120 and 90 of 144, with none every run
  failed -- it is bounded by the Leader's own fit and collection.
- **Refusals end `ERR_CONTRAST` in 2-6 s**, not at the 60 s timeout.

## 9. The data phase [simulation]

Each rule exists because a sweep failed without it:

| Rule | Without it |
|---|---|
| A Follower asks for a block it never framed | 178 of 192 delivered; with framing errors injected, 96 of 384. With it 187 and 384 |
| ... after `RESEND_QUIET_SLOTS` of black, no part lit | answering at once landed inside the block still going out, and the pair walked the ladder a rung apart to ~306 s |
| Complete Ack heard less its first symbol | 4 of 144 `k3.0` runs ended `ERR_LOST_PEER` delivered; after, 432 of 432 |
| A Follower that demoted alone checks the Leader's rate | 2 of 1296 `half50` runs split a rung apart to the deadline; after, none |
| A lock's edge must stay lit 3 ms | 2 of 432 `v3/lit47` grids moved half a slot |
| Train on every Begin; black from the dark before | training black from the Begin's lows lost 4 of 432 `k3.0` |
| After a stall the Leader reads only its last `WATCH_SLOTS` lead-in slots | catching up on every slot sent a Begin's first high 5.5 ms late (`half60gc70`) |
| The stall budget, 64 slots | 18 of 56 hostile cases never ended; an honest transfer stalls ≤ 24 slots, median 21 |
| The block deadline per block, 300 s | a transfer-wide deadline cut a 512-byte payload at 449 bytes |

**Fast blocks only, since 2026-10-18.** Removing slot blocks, a Follower
without the fast module, the fallback after F3, slot rungs slower than 32
ms and breaking in left 3,454 of 3,456 standard runs identical, rung, time,
result and invariants. The other two were `k3.0` runs that had fallen back
and delivered in ~17 s; staying on F3 they deliver in 10.2. Breaking in
never fired in any of the 3,456.

Rejected, one line each: negotiation and verification phases (a test burst
passes on links too fast for a payload); a gamble rung above the start (74
of 96 against 93); three-edge lock averaging (8/8 → 0/8 on a slow
transmitter); control patterns at half rate (169/192); demoting on silence
(rescued nothing, failures 6x longer).

## 10. Fast blocks

### 10.1 Choosing the scheme (2026-10-09/10)

The sensor model was judged against measured captures out of sample: it is
optimistic where errors are rare, 1.6-2.3x for the linear equaliser,
3.4-4x for the DFE [measured-all against inferred-all].

Errors in 24,000 symbols on the measured model, low 0.5 [simulation]:

| Scheme | Symbol | Linear EQ | DFE | MLSE |
|---|---|---|---|---|
| 2-PAM dark | 1 period, 0.33 ms | 254 | 140 | 99 |
| 2-PAM dark | 2 periods, 0.66 ms | 7 | 5 | 0 |
| 2-PAM dark | 3 periods, 0.98 ms | 0 | 0 | 0 |
| 2-PAM lit | 1 period | 47 | 15 | 0 |
| 2-PAM lit | 2 periods | 0 | 0 | 0 |
| 4-PAM dark | 4 periods, low 0.6 | 6 | 1 | 0 |
| 4-PAM lit | 4 periods | 0 | 0 | 0 |
| 8-PAM dark / lit | 6 periods | 1103 / 367 | 872 / 344 | 100 / 50 |

- **Every dark error at 2 periods is one event**: a high after five or more
  lows. Bit stuffing that caps runs of the low at 4 (3.3% rate) gave 0 of
  96,000 dark with the DFE, 2 with the linear EQ.
- **4-PAM at 4 periods** carries the same 1.5 kbit/s as 2-PAM at 2, with
  more errors. **8-PAM** fails without MLSE.
- **DCO-OFDM, uncoded**: 0.80-0.95 kbit/s at FFT 32-64, under 2-PAM's 1.5
  raw; the water-filling bound on the measured response is 5.1-5.4 kbit/s
  [inferred-all, measured channel].
- **Clock offset**: over 2 s blocks, 100 ppm cost 19-42 errors in 24,000;
  over 1 s, none; 230 ppm breaks it [simulation]. → **Blocks capped at 1 s
  on air**; the pair's offset is [unmeasured].
- **Acquisition**: a trained equaliser absorbs up to 1.5 symbols of grid
  error [simulation].

→ **F0 = 2-PAM at 2 PWM periods (0.66 ms), runs of the low ≤ 4, a linear
equaliser trained on a 66-symbol preamble, tracked flicker regressors.**
Rungs F0-F3 = 2, 3, 4, 6 periods; 128, 64, 32, 32 bytes a block.

### 10.2 F0 on the mirror (2026-10-10, mirror B)

**0 errors in 32,496 F0 symbols per room**, dark and dim lit, linear EQ
and DFE alike: ≤ 1e-4 at 95% [measured-v2]. Unstuffed, the dark made 2
(linear) and 1 (DFE) errors, each a high after six or more lows. The
production decoder (`fastblock`) decodes the same captures with 0 errors
at the trigger's computed landing, no search (`test_fastblock.py`, §14).
In the 1024-symbol sweep the model's optimism held where errors are rare
(linear 2.9x, DFE 3.9x pooled) and refuted MLSE's (6-30x).

### 10.3 The edge lag a burst sees (2026-10-12/13, mirror B)

The rise through `T_low` after the first word lands [measured-v2]:

| Room | Lag | Assumed |
|---|---|---|
| dark | 405-1061 µs, mostly 800-920 | `EDGE_LAG_FAST_DARK_US` 750 |
| dim LED (black 55-58) | 200-641 | -- |
| lit LED (black 178-411) | 365-457, ~370; a few early reads at 40-131, likely flicker peaks | `EDGE_LAG_FAST_LIT_US` 400 |

The decoder's three starts, a quarter symbol apart, absorbed every lag
[measured-v2]. → The constants stand.

### 10.4 Decoding after the burst, not during it (2026-10-12)

The production sender and a Follower's own receiver on one badge
(`fast_loopback`) [measured-v2]:

| Follower | Dark | Dim lit | Lit |
|---|---|---|---|
| feeding the decoder as the bins arrive (old) | 6 of 12, 6 of 12 | 7 of 12 | 8 of 12 |
| waiting until the burst is binned | 12 of 12 | 12 of 12 | 12 of 12 |
| the protocol's code, waiting | 12 of 12 | -- | 12 of 12 |

Every lost block was one whose training took ~420 ms, past the ring's
317.7 ms: the readings not yet binned were overwritten. The protocol's code
replied 355-481 ms after the burst ended. → **The Follower feeds the
decoder nothing until the burst is over; `FAST_REPLY_MS` 800.** The
simulator had missed it (a 409.6 ms ring that never overwrote, a training
priced at 75 ms); given the badge's, the old Follower decoded 46-63% of
bursts, the new 96-100% [simulation].

### 10.5 Training once a rung (2026-10-12)

| | Count | Time | Reply after the burst | Decoded |
|---|---|---|---|---|
| Training, dark / lit | 8 / 4 | 300-403 ms | 347-481 ms | all |
| Retune, dark / lit | 12 / 16 | 54-70 ms | 89-123 ms | all |

[measured-v2, mirror B, five blocks a rung]. Dark, 4 of 16 retunes were
refused and trained instead; lit, none. In simulation, searching the start
only at the training's three offsets made dark retunes fail 12% (trainings
7%) and walk pairs down to F2; at twice the resolution, 4.7% against 5.4%.
A retune whose fit error exceeded the training's by 0.25 failed 6-7% of
the time, by 0.5 11%, 0.75 25%, ≥ 1.0 about half → `RETUNE_SSE_SLACK` 0.6.
On a 2 KB payload retuning took a lit pair from 86 to 110 B/s, flicker 85
→ 107, dark 40 → 47 [simulation].

### 10.6 Fast blocks on the mirror, and under room light (2026-10-13 to 19)

**The fast-only build, dark, mirror B (2026-10-19)**, `fast_loopback
0,1,2,3 3 0 defer`: **12 of 12, every rung**, each rung's first block
trained (307-403 ms) and the rest retuned (55-61 ms); edge lag 375-804 µs
(750 assumed); the trigger 46-63 µs after its boundary; a 0.82 s blocking
burst lost no reading [measured-v2].

`fast_loopback 0 8 0 defer fit` (the room's flicker fitted first, as the
gap fits it) [measured-v2]:

| Light | Black | Decoded | Fit |
|---|---|---|---|
| the user's LED, box angled (10-13) | 400-411 | 8 of 8: one trained (reply 551 ms), seven retuned (212-221) | steady |
| the room's LED alone (10-13) | 225-241 | 8 of 8 | steady |
| a strong LED, near (10-14) | 1,242 | 0 of 8 | refused (since fixed in simulation, §6) |
| far from the light (10-18) | 79 | 8 of 8: replies 204-219 ms | steady (120 Hz 3.8 counts) |

So weak flicker left uncorrected costs nothing; **the flicker-fitted decode
has not run on hardware**: it wants a spot with ~20-250 counts of flicker
(room light ~150-500 at the sensor).

### 10.7 What the fast path's rules rest on [simulation]

| Rule | Evidence |
|---|---|
| F0 to start lit, F1 dark | dark, F0 decoded 5 of 16 bursts in simulation, F1 16 of 16. On the mirror dark F0 decoded 11 of 11 deferred [measured-v2]: to revisit with two badges |
| The reply waits for the burst | a NAK sent at a fixed time went out during the burst |
| The burst's end read flicker-corrected at half `T_low` | raw bins took a once-a-cycle trough for dark |
| Missed fast Begins are asked for, once no part is lit | `LOST_PEER_SLOTS` ended 12 of 1296 `half60` runs; asking while the burst was on air was unheard |
| A low rung bit reads as 0 | its Data Sync Error went out over the burst; with both fixes `half50` 1282 → 1296 of 1296 |
| Hysteresis only against the adjacent threshold | the band held a slow sensor's first high low and lost an ACK |
| Stepping back up after `FAST_UP_ACKS` (4) ACKs | after two failed bursts, a lit 2 KB payload 58 → 85 B/s, 8 KB 64 → 108; dark 28 → 42, 30 → 49. Credentials never step |
| Past F3, stay on F3 | the slot-block fallback it replaced took ~17 s where F3 takes 10.2 (`k3.0`) |

### 10.8 One-period symbols with a block code: feasible, not built

2-PAM at one period (0.33 ms, 3 kbit/s raw), 8,124 symbols a capture,
mirror B, 2026-10-12 [measured-v2]:

| Room | Linear EQ (fastblock), start searched | Clustered | RS(64,48) codewords failing |
|---|---|---|---|
| dark | 0.31-0.65% | 4 of 156 errors | 0 of 60 |
| lit, steady LED, flicker regressors on | 0-0.54% (0.16% pooled) | 0 of 51 | 1 of 60 |

- **MLSE failed outright** on most captures: thousands of errors where the
  linear EQ made tens. → MLSE dropped.
- **Capping runs of the low made no difference** at one period, which
  matters: bit stuffing and a block code do not mix (one error near a
  stuffed bit shifts every bit after it).
- **Not built.** With 1 s blocks it would give ~165 B/s lit and ~145 dark,
  against F0's ~118 and ~98 with retuning [inferred]. One period halves the
  tolerance for the pair's clock offset; at 0.5 s blocks it falls to ~100,
  no better than F0. Waits on a second badge.

## 11. Where it stands [simulation, 2026-10-19]

| From both badges facing, skew-free median | Steady lit | 47% flicker | Dark |
|---|---|---|---|
| A credential | 1.4 s | 1.7-1.8 | 2.1 |
| 2 KB payload, retuning | 110 B/s | 107 | 47 |
| 2 KB payload, training every block | 86 | 85 | 40 |

- **Every row of the 24-row standard set delivers 144 of 144** over 8
  seeds: `v2/` `mirror` 2.1 s, `lit` 1.4, `lit30`/`lit47` 1.8, `aligned`/
  `overlap` 2.1, `led60`/`led50`/`half60`/`half50` 1.7-1.8, `k2.0` 4.4,
  `k3.0` 6.2, `self5` 1.8, `worst` 6.6, `gc70` 2.1, `litgc70` 1.4,
  `lit47gc70`/`half60gc70` 1.7-1.8; `v3/mirror` 2.0, `v3/lit47` 1.8;
  `v2v3/mirror` 2.1, `v2v3/lit47` 1.7, `v3v2/lit` 1.4, `v3v2/lit47` 1.7.
- **Over 72 seeds the twelve hard rows deliver 1296 of 1296**: `half50`,
  `half60`, `half60gc70`, `lit47gc70`, `k3.0`, `worst`, `mirror`, `lit`,
  `led60`, `led50`, `lit47`, `v3/lit47`.
- **Rungs**: lit pairs end on 8 ms, dark on 16; a few `half60` runs on 10
  or 13.
- **I2** (the grid check, a diagnostic) trips in about a third of `k3.0`
  runs, all delivered: a slow sensor crosses `T_low` late on a NAK's
  opening middle and the next Begin corrects it.
- **Raw rates**: control slots 125 bit/s at 8 ms, 62 at 16; F0 1.5 kbit/s,
  F1 1.0. The first version, pixel fields whose panel settle held slots to
  80 ms or more, managed 12.5 bit/s [inferred-v2]; before the ring a
  credential took 10.6-15.3 s.

## 12. Decision index

| Decision | Value | Rests on |
|---|---|---|
| Signal on the backlight only, white screen drawn once | -- | §2, §3 |
| Read through a DMA ring, no fallback | 50 kHz, 327.7 ms | §4 |
| Levels | 0.5 / 0.82 / 1.0; beacons 0 / 1.0 | §3 |
| `SENSOR_STEP`, `MIN_SWING`, `MIN_GAP_EXACT`, `GAP_SIGMAS` | 16, 48, 16, 4 | §3, §7 |
| `LIT_BLACK_COUNTS` | 64 | §3 |
| Slot ladder; start rungs | 32-8 ms; 8 ms lit, 16 ms dark | §5, §7, §9 |
| `EDGE_LAG_EXACT_*` | 950 / 1650 µs | §7 |
| `SETTLE_*`, `GUARD_DARK_US` | 2500 / 5000, 2000 µs | §7 |
| Announce, staircase, gap, hold | 14 ms; 20 ms steps; 110 ms gap; 500 ms hold | §8 |
| A hold only the Follower expects: resend, `first_grace` | -- | §8 |
| Flicker: fit with the line refit, 4 then 6 harmonics, keep to 2 counts, track, steady skip | 1.87 counts rms | §6 |
| Data-phase recovery rules | -- | §9 |
| Fast blocks only | -- | §9 |
| F0-F3 scheme | 2-PAM, 2/3/4/6 periods, RLL 4, linear EQ | §10.1-10.2 |
| Blocks capped | 1 s on air | §10.1 |
| `EDGE_LAG_FAST_*` | 400 / 750 µs | §10.3 |
| Decode after the burst; `FAST_REPLY_MS` | 800 ms | §10.4 |
| Retune later blocks; `RETUNE_SSE_SLACK` | 0.6 | §10.5 |
| Fast start rungs; stepping up | F0 lit, F1 dark; 4 ACKs | §10.7 |
| Rejected: pixels, polling, faster PWM, MLSE, 4/8-PAM, OFDM, fitting before the staircase | -- | §2-§4, §8, §10.1, §10.8 |
| Deferred: one period + block code; dark F0; 10 ms dark slots | -- | §7, §10.7, §10.8 |

## 13. Not known

- A second badge: its swing, edge lag and settle; the pair's clock offset
  (sets the block cap and the one-period case); self-coupling off its
  glass; two badges in different light (the Follower decides the pair's,
  so a dark Leader facing a lit Follower starts on 8 ms).
- A flicker-fitted fast-block decode on hardware (§10.6).
- How often two badges' flicker fits disagree (§8).
- v3: the ring, slot costs, the dim low under the 12.5% floor, the
  fractional PWM divider.
- Whether a ring reader decodes level gaps of 16-30 counts on hardware.
- Unplugging during a dev link run other than `files`, and the RP2350
  errata, for the dev link [unverified].

## 14. The data kept whole

[captures/2026-10-10/](captures/2026-10-10/): the F0 mirror captures of
§10.2, `dark/` and `lit/`, `pam_link.*.bin` -- 256-symbol smokes, the
1024-symbol sweep (2/4/8 levels x 1-6 periods) and four 8,192-symbol runs
each of F0 and unstuffed per room. `tests/share_wifi/test_fastblock.py`
decodes them with the production decoder and checks it against
`fastmodel.py`'s reference receiver; it skips where they are absent. They
are out of git (`.gitignore`) and live on the user's disk. Their sequence
is pinned in `test_prbs_is_the_mirror_captures_sequence`.

## 15. Measuring again

- **On the badge**: the dev link subtools in `tests/share_wifi/devtests/` --
  `mirror dark|lit` (levels, noise, curve, PWM averaging, M4, M5),
  `second_badge`, `frame_probe` (timing, M1, M3, M7, collections),
  `room_flicker` (M6), `fast_loopback` (fast blocks on a mirror, with
  `fast_loopback_host.py` to judge a saved run), `files`. How to run them,
  with hands or without: "The USB dev link" and "Fresh launches over USB"
  in [../docs/testing.md](../docs/testing.md).
- **In simulation**: `tests/share_wifi/simulator.py` and
  `tests/share_wifi/sweep.py ROW...` (rows in §1; `SEED0`/`SEED1`, `PATCH`,
  `DRIFT`, `PROFILE` in the environment). A change to the link is checked
  by the 24-row set at 8 seeds and the twelve hard rows at 72 against §11.
