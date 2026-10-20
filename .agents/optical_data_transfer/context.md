# Optical Data Transfer Protocol: context

The data behind the spec's decisions
([spec](spec.md#L1)).
The spec says what to build; this says why each number is what it is.
Rejected alternatives get a line each, no more.

## Confidence markers

Every figure carries one. The marker names the firmware it holds for.

| Marker | Meaning |
|---|---|
| [measured-v2] | measured on the badge running v2.0.2 (BW-1.27.0, 2026-04-10) |
| [measured-all] | measured on the v2 badge, but a physical property (sensor, panel, light), so it holds on any firmware |
| [verified-v2] | seen to work, or fail, on the v2 badge |
| [source-v2] / [source-v3] | read in the firmware source at v2.0.2 (commit `db958ce`) / at v3 (the `modules/` and `board/` source on `main`) |
| [simulation] | from the link simulator (see "Simulation"), driving the real protocol code against a model of the hardware |
| [measured-v2 verdict] | a decision drawn from [measured-v2] figures |
| [measured-unix] | measured under MicroPython's unix port on a computer: shows what allocates, says nothing about the badge's timing |
| [inferred-v2] / [inferred-v3] / [inferred-all] | reasoned from other facts, for that firmware or any |
| [unmeasured] / [unverified] | not known for any firmware; a suffix (-v2, -v3) narrows it to one |
| [unexplained] | measured, but the cause is not known |

Two badges have never run a transfer between them. Every optical figure
below comes from one badge reading its own light off a mirror, or from
simulation.

## Terms

| Term | Meaning |
|---|---|
| v2, v3 | badge firmware. The test badge runs v2.0.2 (`BW-1.27.0`, 2026-04-10) at 200 MHz. The firmware source on `main`, and the emulator at try.badgewa.re, are v3, at 250 MHz. |
| count | one `light_level()` unit; one ADC step is 16 counts |
| swing | the reading's range from backlight off to full on a white screen |
| present | `display.update()`: copies the frame buffer to the panel, waiting for the panel's refresh. The protocol no longer presents during a transfer. |
| t10, t50, t90 | time from a backlight change until the reading has covered 10, 50 or 90% of the step |
| rung, slot, epoch | see the spec's terms |
| low slot | a slot decided low (black) |
| the ring, anchor | the DMA ring of ADC readings. The anchor ties a ring index to the badge's µs clock; a reading's time is anchor + 20 µs x index. |
| PSRAM | the badge's 8 MB external RAM, which holds the heap and the ring |
| uncached alias | a second address for the same PSRAM that bypasses the CPU's cache, so the CPU sees what DMA wrote |
| viper | MicroPython's fastest code emitter, used for the ring's summing kernels |
| fit, fit stall | the mains-flicker fit: a least-squares fit of harmonics to a window of samples. It blocks the badge for ~80-150 ms (the stall). |
| the learner | the fallback flicker filter: sines at 100 and 120 Hz, learnt slot by slot from slots decided low |
| track | the flicker filter's phase lock, which follows the mains' drift after a fit |
| unix port | MicroPython built for a computer, not the badge |
| frame (MicroPython) | a function call's local state. Over 11 words (`VM_MAX_STATE_ON_STACK`) it is allocated on the heap every call. |
| collection | a `gc.collect()` |
| ppm | parts per million: two crystals 100 ppm apart drift 100 µs a second |
| -3 dB | the frequency at which the sensor passes half the power of a slow change |
| PAM | pulse-amplitude modulation: each symbol is one of N light levels |
| DFE | decision-feedback equaliser: subtracts the tail of symbols already decided |
| de Bruijn sequence | a sequence holding every run of n symbols exactly once; a test pattern for every transition |

## Test setup and runs

Every hardware figure comes from one badge, the v2 one, reading its own
backlight off a mirror. There is no second badge.

- **Mirror A**: 3.5 mm glass, back-silvered. The badge lies screen down,
  pressed flat on it. Reference for every swing, settle and lag constant.
  Details in "Hardware: mirrors and geometry".
- **Mirror B**: 1.37 mm glass, back-silvered. From 2026-10-09f the badge
  sits ~1 cm above it in a box: **dark** is the box closed, **lit** the box
  open to the room.
- **Dark and lit** in a run: the room's own light. A badge is LIT when its
  black reads at least 64 counts (`LIT_BLACK_COUNTS`).
- **Dev Mode**: a hidden menu in share_wifi (C on the main screen) that runs
  hardware measurements and saves them to `/state/measurements.json`.
- **USB dev link**: Dev Mode driven from a computer over the USB cable.
  Timing taken over an open dev link session reads slow (collections 67-81
  ms); timing figures here come from the menu unless marked.
- **Run labels**: a date, plus a letter for later runs that day:
  `2026-10-07a`, shortened to `10-07a`; `10-09b-d` means runs b to d. The
  figures quoted here are the record; the raw captures are not part of
  these documents.
- **HW1** (2026-10-09) and **HW2** (2026-10-09b): the two full hardware
  checks of the ring design. HW1 was on mirror B with the sensor partly
  blocked; HW2 on mirror A, on battery.

**Dev Mode measurements**

| ID | Tool | Measures |
|---|---|---|
| M1 | `firmware` | firmware version and what the ring needs from it |
| M3 | `ring` (in the `frame_probe` subtool) | the real listener through the ring for 2 s: cost per pass and per decided slot, quiet, presenting, and with a fitted flicker |
| M4 | `backlight_step_ring` | every backlight transition's t10/t50/t90 through the ring, at 20 µs resolution |
| M5 | `loopback_ring` | a test block sent on the backlight and decoded off the mirror by the real receiver at 16-6 ms: errors and edge lag per rung |
| M6 | `window_residual` (in the `room_flicker` subtool) | the flicker left after correction in an 8 ms rung's window |
| M7 | `flicker_filter` (in the `frame_probe` subtool) | the cost of each flicker-filter call and of a fit |

Other tools named below:

| Tool | Measures |
|---|---|
| `backlight_step` | the sensor's response to a backlight change, read before the ring existed |
| `room_flicker` | the `room_flicker` subtool: ~30 s captures; the strongest frequency 20 Hz-1 kHz, each harmonic's size, the filter's fit on it, the mains' drift |
| `tx_path` | part of M3: time from a slot boundary to the backlight call in the transfer's own loop |
| `sensor_id` | dev-link subtool: de Bruijn sequences over 2, 4 and 8 levels at 8-0.5 ms symbols; fits the sensor model, gives each decoder's error rate |
| `channel` | dev-link subtool: the backlight duty paced by DMA; static curve and frequency response at three PWM rates |
| `pam_link` | dev-link subtool: the fast block (F0) end to end on the mirror |

**Simulation names**

| Name | Meaning |
|---|---|
| row | one sweep configuration: 8 seeds x 6 scenarios x 3 payloads = 144 runs |
| scenario | when the two badges start relative to each other, and the room: `ideal`, `datasheet`, `realistic` (Follower starts 1.5 s late), `close start`, `bright room`, `late start` |
| skew-free | a time with the scenario's start offset taken off |
| `v2`, `v3` | the timing profile a simulated badge has; `v2v3` and `v3v2` pair one of each |
| `lit47` | lit room, flicker 47% of the room light |
| `half50`, `half60` | a light flickering once a mains cycle (50 or 60 Hz) instead of twice. None measured does; a worst case. |
| `self5` | 5% of a badge's own screen reaches its own sensor |
| `swNN` | swing of NN counts |
| I1-I11 | the link invariants, listed in "Link invariants" below. I7: if the Leader reports `OK`, the Follower accepted every block. |

## Hardware: the light sensor

- **12-bit ADC in a 16-bit frame.** Every `light_level()` reading is a
  multiple of 16: one ADC step is 16 counts [measured-all]. Hence
  `SENSOR_STEP` 16 and `QUANTISATION_SD` = 16 / sqrt(12) = 4.62.
- **The sensor is a phototransistor 1 mm outside the right edge of the
  screen's active area, at mid height** [measured-all]. It sees a patch of
  the peer's screen near itself, not the whole screen. Two badges facing
  each other are mirrored, so its right edge meets the peer's left.
  Lighting the whole screen covers both cases.
- **It averages the backlight's PWM** at every duty from 0.25 to 1.0
  [measured-v2]. Its response is 0.2-7 ms against a 328 µs PWM period.
  Without this, a middle level would read as full or off at random.
- **It is slower in less light** [measured-all, `backlight_step`,
  2026-10-05e]: 90% of a step in 1.0-1.3 ms lit; in the dark, a rise from
  off 3.87 ms, a fall 3.24 ms, off to middle 7.75 ms.
- **Through the DMA ring** (2026-10-07a, mirror A, M4) [measured-all]:
  t90 of the worst transition 1.38 ms lit (middle to high), 2.40 ms dark
  (dim low to middle). `T_low` crossing 0.40/0.51 ms lit (rise to high/to
  middle), 0.75/1.28 ms dark.
- **Model.** `ds/dt = (I - s) * s / K`, K = 36 counts·ms [inferred-all,
  fitted to `backlight_step`]. Confirmed on arbitrary multi-level sequences
  by `sensor_id` (2026-10-09f): dark fits gave K 28-40, lit 30-54; the
  simple model fitted best [measured-all].
- **Noise.** One reading: standard deviation 2-10 counts, peak to peak two
  ADC steps (three once) [measured-all]. A ring mean of hundreds of
  readings: about 1 count [measured-v2].
- **Swing.** Backlight off to full on a white screen, through mirror A held
  flat: 80 counts dark, 80-96 lit [measured-all]. Five ADC steps.
- **Room light** reads 32 counts black in the dark and 48-96 lit on the
  mirror [measured-all]. Hence `LIT_BLACK_COUNTS` 64: four steps, between
  the two.

## Hardware: the backlight

- **A symbol is one `display.backlight()` call**: a PWM duty, live at the
  next PWM wrap [source-v2]. No present and no liquid-crystal settle.
- **PWM period 328 µs on v2** (3,052 Hz): the v2 badge runs at 200 MHz
  (`machine.freq()`) [measured-v2], and the driver sets wrap 65535, divider
  1 [source-v2]. v3 runs at 250 MHz: 262 µs [source-v3].
- **The call lands late**: 346-358 µs mean after the slot boundary, at
  most 489 [measured-v2, 2026-10-07a]. A transfer's own path (`field_at()`
  plus the call) adds ~150-166 µs more [measured-v2, `ring.tx_path`,
  2026-10-08/09/09b].
- **Curve.** v2 maps the value to duty `v^2.8` with no floor [source-v2];
  v3 the same with a 12.5% floor for any nonzero value [source-v3].
  The LED driver gives no light below 7-9% duty [measured-all, `channel`
  stairs, 2026-10-09f]; on v2 that is a backlight value of about 0.45.
- **Levels chosen.** High 1.0. Middle 0.82 (57% duty on v2, 61% on v3):
  0.74 read only two ADC steps above black on six mirror runs
  [measured-all]; 0.25 and 0.33 failed calibration every time in
  simulation; 0.93 sat too close to high [simulation]. Low 0.5 (14% duty
  on v2): see "Low is dim".
- **Low is dim.** 0.5 reads under one ADC step above off, but keeps the
  sensor faintly lit. It cut the dark room's edge delay from 1.2-1.9 ms to
  0.3 ms and carried a loopback block at 25 and 16 ms with no errors
  [measured-all, 2026-10-06].
- **Beacons use off, not the dim low.** 0 is off on both firmwares
  [source-v2, source-v3]; v3's floor lifts 0.5 to ~16% of the light
  [inferred-v3].
- **Full-range white.** `color.white` is tinted rgb(222, 238, 214) on
  this build [source-v2]; it would dim the light the peer sees. The
  transfer draws rgb(255, 255, 255).
- **Faster PWM buys nothing.** At 12.2 and 24.4 kHz the bandwidth is no
  better (233-331 Hz) and the static curve worse: dead to 16-20% duty,
  saturated above ~70% [measured-all, `channel`, 2026-10-09f].

## Hardware: mirrors and geometry

- **Mirror A** (3.5 mm glass, back-silvered) is the reference for every
  swing, settle and lag constant [measured-all].
- **Mirror B** (1.37 mm). HW1 (2026-10-09) read a smaller swing on it (64
  dark, 48 lit), first blamed on geometry. The user later found objects
  partly blocking the sensor. Unobstructed at 1 cm (2026-10-09f), B gave at
  least A's light: 76 counts dark and 85 lit low-to-high on the ring
  [measured-all]. HW1's figures stand as a dimmer peer's stand-in.
- **A mirror is not a second badge.** A reflection keeps world position;
  two badges mirror each other. A peer's swing is predicted at ~95 counts
  aligned edge to edge and ~210 with its screen 2-5 mm over the sensor
  [inferred-all]. Unmeasured. The reasoning: a lit surface lights the
  sensor in proportion to the solid angle it fills, so the ratio of two
  swings needs no absolute brightness. Mirror A's image (at twice the air
  gap plus the glass / 1.5) fills ~1.2 sr of a cosine sensor; a peer
  aligned edge to edge fills 1.06-1.27 sr with no mirror loss (~0.85), and
  one whose screen covers the sensor by 2-5 mm fills 1.9-2.9 sr. Scaling
  80 counts gives ~80-92 aligned and ~160-220 overlapping, ±30% for the
  sensor's angular response and the hold. The simulator uses 95 and 210.
- **The hold matters most.** Two badges held flush and square put each
  sensor over the other's bezel, not its screen.

## The DMA ring on the badge

- **Works on v2** [verified-v2, 2026-10-07a]: `Ring()` and `verify()`
  passed, so the uncached PSRAM alias, the cache clean and the ring fields
  all work. Heap at 0x110d0000, ring at 0x150d0000.
- **Rate**: 49,998-49,999 readings a second, quiet or while presenting; no
  FIFO overrun, fault or anchor drift [measured-v2, 2026-10-07a, 10-09b].
- **Collections do not lose readings**: a 28.8-29.8 ms collection
  mid-capture lost none [measured-v2].
- **Kernel cost**: 0.31-0.32 µs a reading over the uncached alias
  [measured-v2].
- **Anchor bracket** 12-14 µs; the newest reading sits 7-62 µs after "now"
  [measured-v2].
- **v3**: ring unverified. M1 and M3 must run there [unmeasured-v3].
- **Why the ring.** Calling the protocol once a reading cost ~1 ms a call
  on v2 [measured-v2]; a 2 ms polled loop read 2,166 times a second at
  best and was blind for 12.4 ms of every present [measured-v2]. The ring
  reads with no CPU and is never blind.
- **Why no fallback reader.** Every supported firmware (v2.0.2 and later)
  has the ring [verified-v2]. A polled tier cost a mixed pair half its rate
  and needed a tier flag in calibration. share_wifi is unreleased, so the
  polled reader was removed on 2026-10-11.

## Costs on the badge

All [measured-v2] unless marked.

- **A decided slot**, lit 8 ms rung: 2.35-2.45 ms mean, 3.2 max, no flicker
  fitted (10-07a). With a fitted flicker (10-09b): a middle slot 2.62 mean,
  2.98 max; a low slot, which also tracks, 4.23 mean, 4.93 max. An unfitted low
  slot (the learner) 4.10 mean.
- **Flicker calls**: `amount()` 0.16-0.20 ms; `track()` 0.21-0.24;
  `update()` 0.39-0.46; the fit 127-153 ms on the Leader's 80 samples,
  79-86 on the Follower's 50.
- **`correct()` cost 0.43-0.73 ms** against `amount()`'s 0.16-0.20. Cause:
  a MicroPython function whose frame exceeds 11 words is heap-allocated
  every call (`VM_MAX_STATE_ON_STACK`, read in the pimoroni fork at
  `f173bb7b6`; that both pins keep it [inferred-v2] [inferred-v3]).
  `amount()` and `table()` were over. Rewritten under 11 words, they
  allocate nothing under the unix port [measured-unix]; on the badge
  [unverified] until M7 re-times it.
- **Collections**: 28-31 ms at ~450 KB from the menu (10-07a, 10-09b-d);
  52-54 ms at 449 KB on a fresh launch (10-09f), cause [unexplained]; 67-81
  ms inside an open dev link session. A collection scales with the live
  heap's extent: 10.5 ms at 72 KB, 16-22 at 190-216 KB.
- **6 ms rung off the ladder** [measured-v2 verdict]: a fitted low slot's
  4.93 ms max exceeds the ~3 ms a 6 ms slot leaves. v3 needs its own M3.
- **Free heap** 7.5-7.6 MB; a slot leaves 220-236 bytes of garbage.

## Edge lags

`EDGE_LAG_EXACT_*` is the time from a slot boundary to the receiver's
`T_low` crossing: the transmitter's lateness plus the sensor's climb.

| Run | Lit, mean (range) | Dark, mean (range) |
|---|---|---|
| 10-07a, mirror A, M5, 5 rungs | 802 µs (539-1043) | 1511 (915-2349) |
| 10-09b, mirror A, M5 | 796 (706-865 by rung) | 1251 (1148-1313 by rung) |
| 10-09f, mirror B at 1 cm, M5 | -- (the room was dim, read as dark) | 1423 (1340-1580 by rung; single locks 886-2042) |

All [measured-v2]. A transfer transmits ~150 µs later than the loopback
(`tx_path`). So:

- **Lit 950 µs**: ~800 + 150.
- **Dark 1650 µs**: the conservative end of 1400-1660 (+150). Held as
  margin.
- **Single-lock scatter is what the window must absorb.** The latest dark
  lock lands ~540 µs past 1650: inside the 2000 µs dark guard. The
  earliest opens the window 4.2 ms after the peer's boundary, past the
  ~2.9 ms the edge needs [inferred-all].
- **Sensitivity**: dark lag set to 1100 or 2200 (±550 µs) still delivers
  144 of 144 on eight dark rows [simulation].
- The lag is the receiver's own constant, not the wire's: a badge on a
  different value still pairs.

## Settle, guard and the start rungs

- **Settle** is the time from the boundary until the reading is good:
  lateness 0.49 ms + sensor t90 + an early lock 0.26 ms + transmit path
  0.15 ms [measured-v2, mirror A].
  - Lit: 0.49 + 1.38 + 0.26 + 0.15 = 2.28 ms. `SETTLE_LIT_US` 2500.
  - Dark: 0.49 + 2.40 + 0.60 + 0.15 = 3.64 ms; ~4.2 ms at HW1's dimmer
    light (t90 2.48-2.96) [measured-all]. `SETTLE_DARK_US` 5000.
- **Guard**: a rise to the middle crosses `T_low` 0.53 ms later than one
  to high [measured-all]. `GUARD_DARK_US` 2000 keeps margin; lit 1000.
- **The window rule** (`max(settle, 40%)` to `d - max(guard, 15%)`, at
  least 2 ms) allows 6 ms lit and 10 ms dark.
- **Lit starts at 8 ms**: 6 ms is not on the ladder (see "Costs on the
  badge").
- **Dark starts at 16 ms, not 10.** At 10 ms the measured sensor delivers
  144 of 144 in every dark row, but a sensor 2x slower delivers 138 and 3x
  slower 52, against 144 and 106 at 16 ms [simulation]. Only the mirror's
  light has been measured; a peer's may be dimmer. Held until a second
  badge measures it.
- **On the mirror, every rung decoded**: M5 `loopback_ring` 0 errors at 16,
  13, 10, 8 and 6 ms, lit and dark, on mirror A (10-07a, 10-09b) and
  mirror B unobstructed (10-09f) [measured-v2].
- **The ladder stops at 32 ms** (2026-10-18): it carries only control
  patterns, and a Data Sync Error is the only demotion. No run in the
  sweeps went past 16 ms [simulation].

## Separability and the level gaps

- **Gaps on the mirror**: adjacent levels 32-48 counts apart, four to eight
  standard deviations of one reading [measured-all]. Every run's medians
  separated all three levels.
- **The old fixed floor** (`MIN_CONTRAST` 192 counts, from mistaking
  peak-to-peak noise for standard deviation) refused every measured swing.
  Replaced by checking each gap against its own measured noise.
- **`GAP_SIGMAS` 4.** Simulated with the measured sensor (16-count steps,
  noise sd 4-8, swings 80/95/210), accepting every link and recording each
  calibration's worst gap over noise [simulation]. With ideal timing, runs
  below about 4 fail and those above deliver. Over 1296 runs a threshold
  of 4 refused 57 links (45 would have delivered); 5 refused 256; 6
  refused 499.
- **`MIN_GAP_EXACT` 16** (one ADC step). Staircase samples are means of
  ~100 ring readings, ~1 count of noise, so the quantisation floor (4 x
  4.62 = 18.5) binds first. Swept, 8 seeds x 6 scenarios x 3 payloads
  [simulation]:

  | Swing | gap 32 | gap 16 |
  |---|---|---|
  | 80 (mirror A), 95 (aligned peer) | 144 / 144 | 144 / 144 |
  | 64 (HW1, obstructed), dark / lit | 0 / 0 | 144 / 144 |
  | 56, dark / lit; and with slow sensor, noise, flicker, v3 | 0 / 0 | 144 each |
  | 48 | 0 / 0 | 0 / 0 (span ~41, under `MIN_SWING`) |

  Whether a ring reader decodes gaps that thin on hardware: [unmeasured].
- **`MIN_SWING` 48**: three ADC steps; held-level noise is one or two steps
  peak to peak [measured-all].
- **Only settled samples count toward noise.** In simulation, transition
  samples doubled the measured spread and refused good links.
- **The middle band of an eighth.** Taken from the middle half, the quarter
  rule could never fail. Simulated, 384 conditions per middle level:
  middles at 7-12% of the swing accepted none, 30-50% all 384, 88-93% none
  [simulation].

## Beacons

- **One beacon, the Announce, half 14 ms** (2026-10-11). With ±20%
  tolerance it covers 11.2-16.8 ms: clear of mains flicker's 4.2-5 ms
  halves and a once-a-cycle light's 8.3-10. Detected from 2 ms samples
  over six halves (84 ms). At 40/64 ms halves on 8 ms samples, detection
  took 0.34 s; at 14 on 2 ms, 0.12 [simulation, 2026-10-11, every row
  delivering as before].
- **No answering beacon.** The Follower's Present beacon only told the
  Leader someone was there; its staircase says that too, and is the only
  signal that steps through three levels.
- **Why backlight beacons.** A backlight change is live within a PWM
  period and the sensor settles in 1-8 ms [measured-all]. Pixel beacons
  would spend ~19 ms waiting for a present and 16-24 ms settling the
  panel [measured-v2, measured-all], longer than a whole 14 ms half.
- **Regularity rule.** Without it, white noise crossed its middle about as
  often as a beacon and matched it. With it, noise of ±80 counts in 2 ms
  samples, each its own, matches under 0.2% of samples [simulation].
  Noise held for 16 ms frames (a test's model, not the ring) is a square
  wave a 14 ms half accepts; the ring's own noise is ~1 count.
- **20/32 ms halves on 4 ms samples** were also tried: detection 0.18 s and
  one `lit47gc70` run lost in 144 [simulation].

## Calibration timing

The short handshake (2026-10-11), skew-free medians of the typical payload,
8 seeds x 6 scenarios [simulation], against the handshake it replaced (two
staircases, 40/64 ms beacons):

| From both badges facing | Steady lit | Lit, 47% flicker | Dark |
|---|---|---|---|
| to the Leader having the staircase | 0.34 → 0.20 s | 0.34 → 0.20 | 0.34 → 0.26 |
| then to the data phase | 0.53 → 0.11 | 0.68 → 0.34 | 0.65 → 0.11 |
| data, linger included (in flicker the hold first) | 1.73 → 1.72 | 2.44 → 2.16 | 3.45 → 3.44 |
| **Total** | **2.6 → 2.0** | **3.5 → 2.7** | **4.4 → 3.8** |

("To the Leader having the staircase" was, before, the Leader detecting
the Present beacon.)

- **One staircase.** The Leader's staircase only gave the Follower the
  Leader's levels. The first Fast Begin (`-_ -_ _- `) shows them in a
  known order, so the Follower trains on it.
- **20 ms steps**: a step holds 6-9 settled samples (lit t90 ~1.4 ms, dark
  ~2.4-4) [measured-all, M4]. 16 ms saved ~10 ms; 14 lost half the
  flickering runs [simulation]. Settling on a peer's light: [unmeasured].
- **Repetitions**: 1 lit, 2 dark. They carry the pair's light.
- **Opening 40 ms**: the Leader's reader keeps 12 dark samples before the
  first rise (`LEAD_KEEP`).
- **The closing gap found 50 ms in**: past a step's 20 ms of black and the
  Announce's 17 ms half at most.
- **The Leader reads on a moving mean.** Uncorrected, a once-a-cycle light
  (10 ms halves) crossed the gap's line and hid the gap; the Leader then
  never left announcing (`half50`: 0 of 144) [simulation]. A 10-sample (20
  ms, one 50 Hz cycle) mean finds it; timing the gap from the raw run
  under the line puts it on the sample, where the mean alone was 4-8 ms
  early.
- **The Leader fits after the staircase, not before.** Before answering,
  the Follower is dark only for its detection time, ~50-90 ms, too short
  for a 100 ms fit; the closing gap is a clean 100 ms of dark. Fitting at
  100 ms into the gap and re-reading the staircase corrected took 47%
  flicker rows from 6 of 144 to 144 [simulation].
- **The Follower fits later in the gap**, from 70 ms (the Leader found the
  gap at 50, up to 10 late on its moving mean, and went dark), from the
  ring; a check over 60 ms ends before the earliest Begin (110 + 4 x 8). A
  window from 70 or 90 or 100 made no difference [simulation].
- **The Follower decides no slot before its fit.** Deciding on readings
  not yet corrected, a once-a-cycle light read as a lit slot, locked the
  grid and cancelled the start grace: the Follower gave up before the held
  block (`half50`: 12 of 144) [simulation].
- **The check's median is the provisional black.** From the Announce's
  off, a v3 Leader's brighter dim low (its 12.5% floor) sat ~5 counts
  under the provisional `T_low`, and the Follower locked on noise
  (`v3v2/lit`: 136 of 144); measured in the gap, 144 [simulation].
- **Provisional middle at 45%**: measured middles sit at 0.40-0.60 of the
  swing [measured-all]. Only the lock threshold and silence counting use
  it before the first Begin trains the levels.
- **Shared gap 110 ms** (= 70 - 20 + 60): the Leader settles at 100 and
  collects. 20 ms after finding it once put the Leader's first block past
  the Follower's `LOST_PEER_SLOTS` under 65-81 ms collections
  [simulation].
- **Flicker hold 500 ms**, decided by the Leader's own fit: 800 until
  2026-10-14, cut from the measured mains drift. At 0.01 Hz every hold
  from 200 to 800 delivered every flicker row; with none most runs failed
  [simulation, 2026-10-14]. The hold is
  counted from the epoch: a Leader late from its fit's stall (~360 ms in a
  trace) slid its block past the Follower's grace until it trimmed the
  hold instead.
- **In flicker the Follower takes no light before the hold ends** (4
  slots' margin): locks on flicker peaks in the hold moved its grid six
  times before the block in a `half60` trace; with the rule `half60` went
  from 430 to 432 of 432 [simulation].
- **A hold only the Follower expects is survived, not prevented**
  (2026-10-19). A Leader whose fit found the room steady facing a
  Follower whose fit found flicker sends at once, into the hold the
  Follower waits out, and hears nothing. Forced (`lit47`, the Leader's
  hold off) it lost seed 1 of 8 to `ERR_LOST_PEER`. Now the Leader sends
  its first block again after its silent reply wait, and the Follower
  allows that wait (`first_grace`) until it frames a Begin -- not until
  its first light, which was the tail of the missed burst. 8 of 8, ~1.4 s
  slower when it happens; every standard run otherwise identical
  [simulation]. No sweep row produces the mismatch on its own: how often
  two badges' fits disagree is [unmeasured].
- **Caught-up start.** After a stall, a pass works through a backlog of
  ring samples, each holding the field; starting the staircase on one
  once lost its whole high step. It waits for a caught-up pass, or 250 ms
  at most [simulation].
- **Closing gap at a third of the swing**, not a quarter: at three times
  the lit room's light with 47% flicker, the corrected dark sat on the
  quarter line and broke every run [simulation].
- **Refusals.** A refused staircase sends the Leader back to announcing;
  the Follower, watching the ring until its first Begin, sees the Announce
  held 168 ms (a Begin on 8 ms slots can match one 84 ms window but lasts
  72) at half its swing or more, and sends another. One refusal is
  allowed: what ended may have been hands moving before the badges were
  held together. With the middle at the low, both end `ERR_CONTRAST` in
  2-6 s [simulation]; without the watch, the Leader announced until the
  60 s timeout.
- **The Follower decides the pair's light.** Only a Leader's misread can
  split the pair, and the first block's demotion recovers it. With the
  Leader's staircase gone, the Follower no longer knows the Leader's light:
  a dark Leader facing a lit Follower starts on 8 ms [unmeasured: two
  badges in different light].
- **Not compatible with earlier builds.** share_wifi is unreleased; both
  badges need the same build.

## Mains flicker

- **Every LED light measured flickers at 120 Hz**, by 0.1-47% of the light
  the sensor saw, not as a sine: harmonics at about half the 120 Hz size
  [measured-all, `room_flicker`, 2026-10-06, 10-07a].

  Rooms are numbered in the order measured; each is one room and lamp
  position. Sizes in counts.

  | Room | Mean | 120 Hz | 240 Hz | Fit | Residual in an 8 ms rung's 3.5 ms window |
  |---|---|---|---|---|---|
  | 1, 2, 3, 7 | 79-985 | ≤ 1.7 | ≤ 0.2 | steady | 0.5-1.4 |
  | 4 | 88 | 7.1 | 2.6 | 120 | 2.2 → 0.8 |
  | 6 | 497 | 22.0 | 5.0 | 120, 240 | 10.2 → 0.7 |
  | 5 (facing the lamp) | 3637 | 1688 | 983 | refused | 334 |

- **Why fit, not learn.** At the lit mirror's 48 counts of room light, 47%
  flicker is ±22 counts, more than an ADC step. An 8 ms window (3.6 ms) is
  under half a 120 Hz cycle, so uncorrected flicker moves a median by most
  of its amplitude. A filter learning only from dark data slots delivered
  77 of 144 with the mains 0.02 Hz off, and 0 under a once-a-cycle light
  [simulation].
- **Mains drift**: 13-14 mHz at the mains, against this badge's clock
  (equivalently a crystal ~230 ppm slow; one badge cannot tell), wandering
  2-8° [measured-v2]. The simulator allows 0.02 Hz.
- **Steady-room skip**: a straight-line rms under 1.87 counts leaves no
  room for a harmonic the fit would keep (4 counts x 240 Hz's 0.66 gain
  through a 2 ms mean / sqrt(2)). Never skips a fit that would keep a
  harmonic [simulation, 200 windows]. The check's cost on v2:
  [unmeasured-v2]; the simulator charges 25 ms.
- **Four harmonics, not six.** Six would help only at four times a hold's
  light with strong 360 Hz, and their longer stall lands on every fitted
  badge. At a ~200 ms stall six lose to four; at 150 ms they roughly match
  [simulation; the six-harmonic stall inferred-v2]. Facing a lamp is out
  of scope; the fit refuses and the learner takes over.
- **Leader tracks while sending.** A Leader on the since-removed polled reader, whose fit stall blinded
  its only tracking window once learnt -9.8 steps/s of drift in a room with
  none and misread the CRC reply: 132 of 144 at 47% flicker; tracking
  during the block fixed it, 144 [simulation]. Assumes a badge's own screen
  barely reaches its own sensor [unmeasured]; the `self5` row (5%) still
  delivers 144 [simulation].

## The data phase

- **Half duplex, receiver holds black.** Doubles the payload rate and gives
  the transmitter a continuous black reference.
- **Symbols indexed by slot number.** Advancing per call once fired a whole
  pattern inside one slot; no transfer could complete.
- **A slot is never skipped mid-message.** Dropping one shifts every
  later symbol; the CRC catches one wrong bit, not a shifted stream. The
  slots the spec does stand aside (10.2) fall between messages, where
  nothing is being framed.
- **Rate changes move duration, never the epoch, and take effect after the
  announcing pattern.** Otherwise the badges end a slot or a rung apart.
- **Four black slots before every block.** Badges do not switch rate on the
  same slot; without the lead-in a resent Begin went out while the peer
  still sampled the old rate.
- **Asking for the block again.** A Follower that never matched Begin had
  nothing to report, and both badges stayed on their rung until
  `ERR_LOST_PEER`: every failing run in one sweep. Adding the request took
  delivery from 178 to 187 of 192, and with framing errors injected from 96
  to 384 of 384 [simulation].
- **Complete Ack heard less its first symbol** (2026-10-10). The Ack
  follows Complete by one silent slot; a sensor 3x slower rises too late
  for its first middle. Before: 4 of 144 runs ended `ERR_LOST_PEER` with
  the payload delivered (16 once more runs stayed on 16 ms); after: 432 of
  432 over 24 seeds [simulation].
- **Complete retry counts silent slots only**, capped at
  `LINGER_SLOTS - 2`. A Follower that collects on CRC Good answers
  Complete ten 8 ms slots late, and a retry counted from Complete's end
  transmitted over the answer [simulation].
- **The Leader claims OK only on the Ack.** Exiting on transmitting
  Complete let a Leader report success over a Follower that missed it
  (invariant I7).
- **Matching control patterns without their trailing black.** The trailing
  black after white is the hardest symbol on the wire; read as grey, CRC
  Good matched nothing and multi-block delivered nothing [simulation].
- **Re-base silence counted to the crossing's slot, not the crossing.**
  Counted to the crossing, a Leader locked on CRC replies late and read a
  CRC bit as Data Sync Error [simulation, sensor 3x slower].
- **A lock's edge must stay lit for 3 ms.** A peak of the flicker the fit
  left crossed `T_low` and moved a v3 pair's grid by half a slot (2 of 432
  `v3/lit47` runs); checked, 432 [simulation, 2026-10-11].
- **The Follower trains on every Begin.** The Begin is high, middle and
  low three times: the medians of its highs and middles are the Leader's
  levels, black the long dark before. Training black from the Begin's own
  lows lost 4 of 432 `k3.0` runs: a slow sensor leaves them unsettled
  [simulation]. Matching the Begin by its shape works before any levels are
  right; matching by decided symbols needs them.
- **Training allocates nothing.** It runs every slot a Follower decides low
  outside a block; on v2 a 4-item tuple costs 65-145 µs [measured-v2].
- **A Follower that demoted on its own checks the Leader's rate.** A Data
  Sync Error the Leader never heard left the pair a rung apart, and every
  exchange after demoted both: 2 of 1296 `half50` runs walked the ladder
  to the 300 s block deadline. Measured on the next message (every
  pattern but Complete repeats every 3 slots), the Follower moves to the
  Leader's rung: 1291 of 1296, none at the deadline, no other row moved
  [simulation, 2026-10-11]. It runs only after such a demotion, so costs
  nothing otherwise.
- **No Complete before a block.** Before one is held, `- - -` is the
  Announce, sent again after a refusal; taken for Complete, the Follower
  acknowledged and ended `ERR_DECODE` [simulation].

**Fast blocks** (spec 13.1, 14.2), each rule found by a sweep
[simulation, 2026-10-11]:

- **F1 to start dark.** Dark, F0 decoded 5 of 16 bursts and F1 16 of 16:
  the dim low's edge lags 0.75 ms, more than an F0 symbol.
- **The reply waits for the burst.** A NAK sent at a fixed time went out
  during the burst, and the Leader, on air, never heard it; an ACK in the
  burst's last slot lost its first symbol. The ACK now goes a slot after
  the burst's computed end, the NAK once the ring shows the burst over.
- **The burst's end, flicker corrected, at half `T_low`.** Raw bins took a
  high in a once-a-cycle light's trough for dark; a slow sensor's single
  highs do not all reach `T_low`.
- **The decoder's flicker at the light's fundamental**: twice the mains
  for an LED, the mains for a once-a-cycle light, which twice the mains
  left undecodable.
- **Hysteresis only against the adjacent threshold.** The band held a
  slow sensor's first high low, and the Leader lost an ACK.
- **Missed fast Begins are asked for** (`UNFRAMED_LIT_SLOTS`): a fast
  block is too short for `STALL_SLOTS` to ask, and `LOST_PEER_SLOTS` ended
  12 of 1296 `half60` runs. Counted since progress: a burst in a
  once-a-cycle light reads as fragments.
- **A low rung bit is a 0.** As a framing failure its Data Sync Error went
  out over the burst. With both fixes, over 72 seeds `half50` 1282 →
  1296 and `half60` 1284 → 1296 of 1296.

## Budgets and deadlines

- **The stall budget** exists because a peer stuck showing grey is not
  silent: every slot decodes, so the silence counter never fires. Driving
  each side into each state against an uncooperative peer, 18 of 56
  combinations never terminated before it [simulation].
- **64 slots**: a transfer that succeeds stalls at most 24 slots, median
  21, one outlier 42 [simulation].
- **The block deadline is per block, 300 s.** Over the whole transfer it
  capped payload length: a 512-byte payload died with 449 bytes delivered.
  The slowest honest block took 64 s, 108 on a 32 ms settle, 140 for a full
  block at the slowest rung [simulation].
- **`MAX_BLOCK_ATTEMPTS` = 48**, set when the slot ladder had 16 rungs
  of 3 tries. Kept: a block the Follower never decodes walks F0-F3 and
  then stays on F3, and ends `ERR_CRC` inside the block deadline
  [simulation]. Without it a Leader reading noise as Data Sync Errors
  resent for ever.
- **`TRANSFER_MAX_US` = 2^30 - 2^26** keeps microsecond times a small
  integer on MicroPython.

## Rejected designs

One line each; all measured [simulation] unless marked.

- **Negotiation and verification phases**: a short test burst passes on
  links too fast for a payload, and a link too broken for the burst cannot
  carry the verdict either.
- **A gamble rung above the start** (35 ms, in the 50 ms era): 74 of 96
  against 93 starting on proven ground; a half-working rung burns the
  retry budget.
- **Three-edge lock averaging**: better lock, but the link stopped
  demoting at rates where control patterns failed; 8/8 → 0/8 on a slow
  transmitter.
- **Control patterns at half rate**: 169/192; overhead 47 → 65 slots.
- **Demoting on silence**: rescued nothing, failures took 6x longer, and
  split rungs.
- **Adaptive repetition count**: the badge that knows the samples were bad
  is not the one transmitting, and no channel exists yet.
- **A length prefix instead of a black separator**, in slot blocks: 8
  slots a field against 1. Fast blocks carry varint lengths, a few ms.
- **DCO-OFDM for files** (uncoded, FFT 32 and 64): 0.8-0.95 kbit/s, under
  2-PAM's 1.5 raw [inferred-all, measured channel].
- **Two staircases and an answering beacon** (until 2026-10-11): 0.6-0.8 s
  slower a transfer, for nothing the one staircase and the Begin do not
  carry.
- **A flicker hold under 800 ms**: at 600 `half60gc70` 405 of 432 against
  425; at 400 or less most flickering runs demoted.
- **Slot blocks, and every fallback to them** (removed 2026-10-18): the
  payload in 64-byte blocks of slot symbols with a CRC-16, a Follower
  without the fast module (the `ASCENDING` staircase), a Leader falling
  back after F3, rungs slower than 32 ms, and breaking in. Over the 24
  standard rows at 8 seeds, 3,454 of 3,456 runs were identical without
  them; the other two, `k3.0` runs that had fallen back and delivered in
  17 s, stayed on F3 and delivered in 10.2. Breaking in never fired in
  any of the 3,456; over 72 seeds the 12 hard rows deliver 1296 of 1296
  [simulation].
- **The Follower fitting before its staircase** (2026-10-19), on the
  Announce's off halves, its flag in the staircase order, the Leader
  holding if either badge found flicker. It removed the mismatch above
  and the flag was read right in all 480 runs checked, but it put the
  Follower's fit stall in series with the handshake, where in the gap it
  overlaps the Leader's: flicker rooms 1.8 → 2.1 s, slow sensors +0.4-0.6
  s, steady rooms unchanged. The hold cannot pay for it: it is bounded by
  the Leader's own fit stall and collection, not the Follower's (at 300
  ms every flicker row delivers, 2.0 s; at 200 the `gc70` rows lose 24
  and 54 of 144) [simulation].
- **The Leader fitting before the staircase**: the Follower is dark only
  for its detection time, ~50-90 ms, before it answers.
- **Staircase steps of 16 or 14 ms**: ~10 ms saved at 16; half the
  flickering runs lost at 14.

## Simulation

- **The link simulator** runs both badges' real protocol and reader code
  on one shared timeline, each badge's ring filled from a model of the
  other's light, and asserts the link invariants (below) on every tick.
  The tests document says how to build one
  ([link simulator](tests.md#L87)).
- **Its model**, each figure from the sections above:

  | Part | Model |
  |---|---|
  | ring | 100 µs entries, each the sum of 5 readings; 327.7 ms of them, the badge's ring, each push overwriting the oldest; 318 ms usable; a reading's noise sd 8.5 counts |
  | sensor | `ds/dt = (I - s) x s / K`, K 36 counts·ms, a 0.18-count floor that starts a rise from dark; readings in 16-count steps |
  | light | room black (32 dark, 48 lit) plus the peer's swing (80 mirror, 95 aligned, 210 overlapping) x the LED's light |
  | backlight | duty `v^2.8` (v2) or the same with a 12.5% floor (v3); no light below 11% duty; the new duty applies at the next PWM wrap (328 µs v2, 262 µs v3) |
  | transmit | the call lands 0.35-0.65 ms after the boundary |
  | costs | a loop pass 0.15 ms, a wake 0.06, a decided slot 2.5, a 2 ms sample 0.3; flicker `amount()` 0.20, `track()` 0.24, `update()` 0.42, learner 0.12; the fit stalls 150 ms, the steady check 4 (its line measured at 0.8-1.2 on v2); replaying a kept sample 0.35; a fast block's decoder trainings 318 or 426 ms a block (three starts), as measured; a retune 56-66 ms, as measured |
  | collections | 25-46 ms; the `gc70` rows 65-81 |
  | clocks | each badge's crystal error in ppm, 0 unless a row sets it; the Follower starts after the Leader by the scenario's skew |
  | room flicker | `led` (2x and 4x mains, 0.3 the size), `half` (1x, 2x, 3x), `sine`; a share of room light (47% the worst measured); the mains drifting 0.02 Hz |
  | self-coupling | a share of a badge's own light reaching its own sensor (0 by default, 5% in `self5`) |

- **Payloads**, two fields each: `HomeNet` and `hunter2!!`; `OpenCafe`
  and an empty key; a 32-byte name and a 63-byte key, the longest WiFi
  allows.
- **A sweep** is 8 seeds x 6 scenarios x 3 payloads (144 runs a row):
  WPA2 credential, open network, longest credential.
- **Current results** (2026-10-18, fast blocks only, the Follower
  decoding once the burst is over, the badge's ring and trainings): every
  row of 24 delivers 144 of 144 over 8 seeds. Over 72 seeds every hard
  row delivers 1296 of 1296 [simulation] (section 11 of [data.md](data.md)). Pricing the steadiness
  check as measured (4 ms, was 25) first cost the once-a-cycle rows 5-6
  runs: a fit dropping a 3.8-count third harmonic left one phase in five
  11-20 counts off, and a Leader catching up its break watch (now the lead-in watch) after a
  collection sent a Begin's first high 5.5 ms late. Keeping harmonics
  down to `FLICKER_KEEP_COUNTS` (2) in a room that flickers, and watching
  only the last `WATCH_SLOTS` after a stall, fixed both.
- **Mixed v2/v3 pairs** (`v2v3`, `v3v2`): 144 each at 8 seeds [simulation;
  v3 timing source-v3].
- **One seed is not evidence.** Seed 1 passed while other seeds failed,
  more than once.
- **What it cannot show**: a second badge's light, its clock offset, a
  badge's own screen on its own sensor. A pass means "agrees with the
  model".

## Link invariants

What must hold throughout a transfer. Every bug that cost real time here
was a divergence that surfaced seconds later as a mystery timeout, so the
simulator asserts I1, I2, I4 and I7 on every tick, and I11 is tested by
driving each side into each state against a peer that stops answering.

| | Invariant | Why |
|---|---|---|
| I1 | Both badges are on the same rung, except while a rate change propagates. | A Leader on 16 ms and a Follower on 13 ms cannot hear each other; both end `ERR_LOST_PEER`. |
| I2 | The slot grids agree: the epochs differ, mod the slot, by under a quarter slot. | At half a slot the receiver reads the transmitter's transition. A diagnostic: a 3x slower sensor tripped it in 24 of 144 runs that all delivered; with fast blocks in 40, the Leader locking late on a NAK's opening middle. |
| I3 | A rate change never moves the epoch. | Re-basing on "now" leaves the grids a slot apart. |
| I4 | No chunk is accepted twice. | A duplicate corrupts the payload while every CRC passes. |
| I5 | The Leader's block index only advances. | |
| I6 | One badge transmits at a time. | Half duplex. |
| I7 | If the Leader reports `OK`, the Follower accepted every block. | It was once violated with the Follower holding nothing: a false claim to have shared a credential. The one that matters most. |
| I8 | Neither badge is left running once the other has finished. | A Complete repeat always lands inside the Follower's linger. |
| I9 | No more than `STALL_SLOTS` slots listening without progress. | A peer stuck on grey is not silent; every slot decodes, so a silence counter never fires. 18 of 56 hostile cases hung before it [simulation]. |
| I10 | Rate only steps slower, never below the slowest rung. | |
| I11 | Every wait ends: a result within `BLOCK_TIMEOUT_MS` of the last accepted block, whatever the peer does. | `MAX_BLOCK_ATTEMPTS` bounds the resends the Data Sync Error path would otherwise leave unbounded. |

The Follower's start grace (section 9.6 of the spec) widens I2's window
until it first decides a lit slot.

## Throughput

- **Raw rate** is 1 / slot: 125 bit/s on 8 ms, 62 bit/s on 16 ms; a fast
  block's, 1 / symbol: 1.5 kbit/s on F0, 1.0 on F1, before its preamble
  and stuffing.
- **With fast blocks**, a typical credential from facing to done: 1.4 s
  steady lit, 1.7-1.8 s in flicker, 2.1 s in the dark [simulation,
  skew-free medians, 2026-10-14, with trainings as measured on v2; the
  flicker hold 500 ms, set from a measured drift -- 2.0-2.1 s at 800].
- **Slot blocks**, removed 2026-10-18, took 2.0 s steady lit, 2.7 s in
  47% flicker and 3.8 s dark [simulation, 2026-10-11].
- **Per-block overhead** 26 slots: lead-in 4, Fast Begin 9, rung 2, gap
  2, reply 9; then the burst and the Follower's decode (up to
  `FAST_REPLY_MS`). Complete 6 more.

## Toward files: the fast block

- **The channel** [measured-all, `sensor_id` and `channel`, 2026-10-09f,
  mirror B at 1 cm]: -3 dB at 276 Hz dark and 406 Hz lit at the stock PWM.
  A plain slicer decodes 2 levels to 1 ms and 4 levels to 2 ms
  error-free; 8 levels need 8 ms.
- **F0 = 2-level PAM at 2 PWM periods** (0.66 ms), runs of the low capped at
  4 by bit stuffing (3.3% overhead), decided by a linear equaliser trained
  on a preamble [simulation, measured model].
- **Confirmed on the mirror** (2026-10-10, mirror B at 1 cm, v2): 0 errors
  in 32,496 symbols per room, linear equaliser and DFE alike; error rate ≤
  1e-4 at 95% [measured-v2]. Unstuffed, the dark made 2 (linear) and 1
  (DFE) errors, each a high after six or more lows.
- **Net**: ~0.90 kbit/s lit, ~0.76 dark on a 10 KB file, against 114 and 57
  bit/s today [inferred-all].
- **Blocks capped at 1 s on air.** The pair's clock offset is unmeasured.
  Over 2 s, 100 ppm costs 19-42 errors in 24000; over 1 s none; 230 ppm
  breaks it [simulation].
- **Flicker in a fast block** needs tracked regressors at the light's
  frequency (2 x mains, or the mains for a once-a-cycle light): fixed
  regressors 0.3 Hz off made 274 errors in 16000; tracked, 0
  [simulation].
- **In a transfer since 2026-10-11** (spec 13.1, 14.2): the payload goes in
  fast blocks whenever both badges can [simulation only].
- **Decoder cost on v2**: ~5 µs a symbol in viper, under 1% of 656 µs.
  **Training a block's three starts takes 315-434 ms** [measured-v2,
  2026-10-12], not the 50-100 ms a training estimated from the unix port.
- **One badge on the mirror (v2, 2026-10-12)** ran the transfer's own
  sender and a Follower's own receiver: the burst from its slot boundary
  48-77 µs late, the PWM divider and duty put back every time, the ring
  healthy after a 0.82 s burst with the loop blocked [measured-v2].
  Decoding as the spec has it (14.2 step 4, feeding bins as the ring
  fills) lost about half the blocks: a training mid-burst outlasts the
  ring's 317.7 ms of history, and the readings not yet binned are
  overwritten. Held back until the bins covered the longest burst, the
  same decoder took 36 of 36, dark, dimly lit and lit by LED room light,
  every rung [measured-v2]. The dark edge lag read 473-1043 µs, mostly
  800-920 (`EDGE_LAG_FAST_DARK_US` is 750), and the quarter-symbol starts
  absorbed it; lit it read 400-457 µs (`EDGE_LAG_FAST_LIT_US` is 400). So a Follower feeds its decoder nothing until the burst is
  binned (spec 14.2 step 4). On the badge that code decoded 24 of 24, lit
  and dark, every rung, replying 355-481 ms after each burst ended
  (`FAST_REPLY_MS` allows 800) [measured-v2].
- **Training once a rung** (spec Appendix A, 2026-10-12, simulation): a
  retune took 54-70 ms on the badge against a training's 300-403, and the
  Follower replied 89-123 ms after the burst's end against 347-481
  [measured-v2, 2026-10-12: 40 blocks on mirror B, every one decoded; 16
  of 16 retunes accepted lit, 12 of 16 dark]. On a 2 KB payload a lit
  pair goes from 86 to 110 B/s, with 47% flicker from 85 to 107, dark from
  40 to 47 [simulation, those costs]. Searching the start more finely in a
  retune mattered: with the training's three starts, dark retunes failed
  12% against trainings' 7%, and pairs stepped down to F2; with five, 4.7%
  against 5.4%.
- **Stepping back up** (spec 13.1, 2026-10-13, simulation): a demotion
  is often passing -- a hand across the badges, a light switched on --
  and a file left on a slower rung pays for it to the end. After two
  failed bursts at the start of a lit pair's 2 KB payload, stepping back
  up after `FAST_UP_ACKS` (4) ACKs gave 85 B/s against 58 without, 108
  against 64 on 8 KB; dark, 42 against 28 and 49 against 30 (medians of 8
  seeds). Only the Leader moves (the rung travels in each Begin); a rung
  stepped up to that fails doubles the wait, so a light that really has
  changed costs a few probes, not a ping-pong. Credentials, one block,
  never step: the 24-row sweep was unchanged.
- **One-period symbols** (2-level, 0.33 ms, 3 kbit/s raw; mirror B, v2,
  2026-10-12, 8,124 symbols a capture) [measured-v2]: dark, the linear
  equaliser erred 0.31-0.65% with the start searched, the errors isolated;
  lit with a steady LED light and the flicker regressors on, 0-0.54%, 0.16%
  pooled -- without them, the flicker clustered the errors at up to half
  the symbols. Capping runs of the low made no difference at one period.
  MLSE failed outright on most captures. A Reed-Solomon code of 64-byte
  codewords correcting 8 byte errors would have failed 0 of 60 dark and 1
  of 60 lit. Not built: a code needs a frame without bit stuffing, whose
  one error shifts every bit after it.
- **Not covered yet**: real mains flicker fitted, a second badge's clock,
  v3's fractional divider on hardware, mirror A, and whole transfers
  between two badges.

## Open questions

- A peer badge's swing, speed and clock offset: needs a second badge.
- How much of a badge's own screen reaches its own sensor through the
  peer's glass [unmeasured].
- Whether 10 ms dark is safe on a real peer.
- Why a collection took 52 ms at 449 KB on 10-09f [unexplained].
- v3: the ring, slot costs, the dim low under its 12.5% floor
  [unmeasured-v3].
- Whether a ring reader decodes gaps of 16-30 counts on hardware.
- Whether the Leader reads the Follower's staircase while its own Announce
  flashes: the sensor faces the peer, but how much of a badge's own light
  comes back off the peer's glass is the self-coupling above
  [unmeasured]; `self5` (5%) delivers 432 of 432 [simulation].
- Two badges in different light: the Follower decides the pair's light
  alone, so a dark Leader facing a lit Follower starts on 8 ms
  [unmeasured].
- A once-a-cycle 50 Hz light (no room measured has one) lost 5 of 1296
  `half50` runs with slot blocks; with fast blocks it loses none
  [simulation]. In one traced run a drift estimate had gone to -17 steps
  a second against the true -6.4; over 48 runs in 50 and 60 Hz lights
  both badges learnt -5.5 to -7.6 [simulation], so that was one run, not
  a bias.
