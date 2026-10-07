# Optical Data Transfer Protocol

Sends data between two badges using nothing but the screen and the light sensor. One
badge displays flat fields at three light levels and the other reads them back
through its **reader** ([reader.py](../../firmware/apps/share_wifi/reader.py)): the
ADC written by DMA into a ring where the badge can (EXACT), `badge.light_level()`
polled where it cannot (POLLED). See "Reading".

Implemented in [firmware/apps/share_wifi/protocol.py](../../firmware/apps/share_wifi/protocol.py) — pure Python,
no badge globals. Its first and only user is the `share_wifi` app, specified in
[share_wifi_spec.md](share_wifi_spec.md).

## How to read this

Bullets are normative: they say what the implementation does. Indented quotes give
the reasoning, and most of them exist because the obvious alternative was tried and
broke something.

> No transfer has run on hardware; the only contact with a badge is the panel
> measurements under "What the measurement found". Figures described as simulated
> come from a model of the panel as a first-order lag, driven through the real code. Confidence markers
> follow [README.md](README.md).

Sections up to and including [Exit values](#exit-values) are the protocol; the rest
records how its constants were measured and what it costs. How `share_wifi` uses
it -- the screens before and after a transfer, the payload it carries, the menus -- is in
[share_wifi_spec.md](share_wifi_spec.md).

---

## Constants

Names match [firmware/apps/share_wifi/protocol.py](../../firmware/apps/share_wifi/protocol.py).

| Name | Value | Meaning |
|---|---|---|
| `SENSOR_STEP` | 16 | **[measured-all]** one ADC step — `light_level()` is 12-bit in a 16-bit frame |
| `MIN_SWING` | 48 | three ADC steps: the least swing that counts as a peer at all -- beacon detection, the staircase reader -- and the least span calibration accepts. Held-level noise measured one or two steps peak to peak **[measured-all]** |
| `MIN_GAP` | 32 | two ADC steps: the least gap between adjacent calibrated levels |
| `GAP_SIGMAS` | 4 | each gap must be at least this many standard deviations of the noisier level beside it, so a threshold sits two from each. Chosen in simulation -- see "Separability" **[inferred-all]** |
| `QUANTISATION_SD` | 4.62 | `SENSOR_STEP / sqrt(12)`: the floor on a level's noise, since readings that all land on one ADC code still carry up to half a step of error |
| `MIN_HELD_SAMPLES` | 8 | settled samples a level needs before its noise is trusted |
| `LCD_SETTLE_MS` | 24 | the panel's settle, 16.1-24.1 ms to 90% [measured-all]: beacons and Dev Mode |
| `BACKLIGHT_SETTLE_MS` | 10 | a POLLED pair's backlight settle, dark: 7.8 ms worst [measured-all], with margin |
| `BACKLIGHT_LIT_SETTLE_MS` | 5 | ... and LIT |
| `LIT_BLACK_COUNTS` | 64 | a badge is LIT when the black it reads is at least this, four ADC steps: 32 in the dark, 80-96 lit on the mirror [measured-all] |
| `BACKLIGHT_PROVEN_MS` | 50 | the rung below which every rung is a gamble: a bad CRC there demotes |
| `FINE_MS` | 2 | a fine sample: the reader's mean over this long, pulled back to back before the data phase -- the flicker fit's input, pooled into coarse samples |
| `FINE_US` | 2000 | `FINE_MS` in µs |
| `COARSE_MS` | 8 | a coarse sample in discovery: the beacon detector's, sized for about one sample a frame |
| `DUE_MARGIN_US` | 100 | a loop waiting for a window or a sample pumps this far past it, so the readings it waits for are complete |
| `CAL_SAMPLE_MS` | 4 | a coarse sample in calibration: ten a 40 ms step, `STEP_SAMPLES` of them 12 ms held |
| `HANDSHAKE_GAP_MS` | 7 | a Follower in Peer Present drops the coarse sample in progress once its fine samples have been empty -- no readings: a POLLED badge presenting -- longer than this, unless that sample already follows a drop. See "Peer present" |
| `BACKLIGHT_EDGE_LAG_MS` | 1 | the same edge delay in ms, for Dev Mode's polled `loopback` |
| `EDGE_LAG_LIT_US` | 100 | the rise lock takes this off a crossing, LIT: from the backlight call to `T_low`, ~0 at 2 ms resolution [measured-all; **unmeasured** sub-ms: M4, M5] |
| `EDGE_LAG_DARK_US` | 300 | ... and dark, from the dim low: 0.3 ms [measured-all, 10-06 loopback; **unmeasured** sub-ms: M4, M5] |
| `FLICKER_HZ` | 100, 120 | the mains flicker frequencies a continuous badge estimates and takes off its readings |
| `FLICKER_TAU_MS` | 250 | how much dark time the flicker estimate averages over |
| `FLICKER_MIN_COUNTS` | 4 | flicker smaller than this, a quarter of an ADC step, is left alone |
| `FLICKER_MIN_POINTS` | 2 | a dark slot teaches the flicker only if its back half held this many samples |
| `FLICKER_STEPS` | 320 | steps of one period in the filter's sine table: 1.1 degrees, 3125 us per step per Hz |
| `FLICKER_ONE` | 1024 | the table's scale: the filter's per-sample arithmetic is whole numbers |
| `FLICKER_SPAN_US` | 250 | a sample's span is rounded to this for the table of sin(x)/x |
| `FLICKER_MAINS` | 50, 60 | the mains a fit tries: the better explains the window |
| `FLICKER_HARMONICS` | 4 | harmonics of the mains fitted: 50-200 or 60-240 Hz |
| `FLICKER_FIT_MS` | 160 | the Leader's fit window: the last this long of its staircase's body, fitted as its closing gap begins |
| `FLICKER_FOLLOWER_FIT_START_MS` | 160 | the Follower's fit window starts this long after the Leader's last beacon crossing |
| `FLICKER_FOLLOWER_FIT_MS` | 100 | ... and is this long, fitted at its end: inside the Leader's dark with 17 ms to spare at the start and 30 at the end |
| `FLICKER_FIT_MAX_NOISE` | 12 | counts a fit may leave unexplained: more, and the window was not steady -- the filter learns as before |
| `FLICKER_FIT_SIGMAS` | 4 | a fitted harmonic counts only this many standard errors clear of the fit's noise |
| `FLICKER_FIT_PASSES` | 2 | refinements of the fit's projections, each on what the others leave |
| `FLICKER_MIN_SEEN` | 0.30 | a harmonic is fitted only if the window's samples keep this much of it through their averaging |
| `FLICKER_BUFFER` | 640 | fine samples kept for the fit and the Follower's replay: 1.28 s of them |
| `FLICKER_TRACK_POINTS` | 8 | samples a phase-lock update while a badge sends its staircase or waits out the gap |
| `FLICKER_TRACK_GAIN` | 0.25 | the share of an update's timing error that moves the table |
| `FLICKER_TRACK_MAX_STEPS` | 16 | an update's error is clipped to this many steps |
| `FLICKER_RATE_MEMORY` | 0.99 | the drift's line keeps this much of its past an update |
| `FLICKER_RATE_MIN_S` | 0.30 | the line's points must span this long before its slope is the rate |
| `FLICKER_RATE_MIN_UPDATES` | 10 | ... and carry this much weight of updates; until then the rate is 0 |
| `FLICKER_MAX_RATE` | 32 | steps a second the rate is bounded to: 0.1 Hz of mains drift |
| `BACKLIGHT_TX_MS` | 3 | how coarsely a POLLED reader takes a transmitter to place a symbol, for its end guard |
| `BACKLIGHT_GUARD_PERCENT` | 15 | the end-of-slot guard, at least, as a share of the slot: both tiers |
| `SETTLE_SLOTS` | 3 | a POLLED rung must be worth this many settle times |
| `END_GUARD_PERCENT` | 65 | a POLLED window's end guard, as a percentage of `BACKLIGHT_TX_MS`, capped -- the read loop's rule, which the POLLED rungs were found with |
| `SLOT_LADDER` | 250, 200, 160, 130, 100, 80, 65, 50, 40, 32, 25, 20, 16, 13, 10, 8 | slot durations in ms, slowest first. 13, 10 and 8 for EXACT pairs |
| `SETTLE_LIT_US` | 2500 | an EXACT reader's window opens no sooner than this into a slot, LIT: the sensor's 90% in 1.0-1.5 ms from the call [measured-all], x ~2 [**gated**: M4] |
| `SETTLE_DARK_US` | 10000 | ... and dark: 7.75 ms worst, off to middle [measured-all]; the dim low only faster [inferred-all; **unmeasured** at sub-ms: M4]. 5000 would start dark pairs on 10 ms |
| `GUARD_LIT_US` | 1000 | an EXACT reader's window closes at least this short of the next boundary, LIT |
| `GUARD_DARK_US` | 2000 | ... and dark: a rise to the middle crosses `T_low` later than one to high, ~3 ms from off [measured-all], less from the dim low [**unmeasured**: M4] |
| `OPEN_PERCENT` | 40 | an EXACT window opens no sooner than this share of the slot |
| `WINDOW_MIN_US` | 2000 | an EXACT window at least this long, or the rung is not one a pair starts on: 100 readings |
| `SLOT_MIN_MS` | 6 | no slot shorter: twice the worst per-slot work assumed on v2 [**unmeasured**: M3 `slot_work`] |
| `PART_MAX_US` | 4000 | a window is decided from the median of parts of it, each at most this long -- inside the flicker table's spans |
| `PARTS_MIN` | 5 | ... and at least this many parts |
| `PARTS_MAX` | 32 | ... and at most this many |
| `POLL_PART_READINGS` | 2 | a POLLED window's parts are merged until each holds this many readings |
| `OPTIMISTIC_RUNGS` | 0 | rungs past the proven floor a transfer will gamble on |
| `ANNOUNCE_HALF_MS` | 100 | Leader beacon half period. 250 until 2026-10: every reader now reads continuously |
| `PRESENT_HALF_MS` | 160 | Follower beacon half period. 400 until 2026-10 |
| `BEACON_TOLERANCE` | 0.20 | how far a measured half period may sit from nominal |
| `PLATEAU_MS` | 40 | one calibration step, both tiers [**gated** on the POLLED sweep: 40, 60, 80 tried, 60 and 80 no better] |
| `CAL_REPS` | 3 | staircase repetitions for DARK |
| `CAL_OPEN_LEADER_MS` | 400 | the Leader's opening gap: long enough that the Follower's flicker fit, run inside it, ends there too |
| `CAL_OPEN_MS` | 250 | the Follower's opening gap |
| `CAL_CLOSE_MS` | 250 | closing gaps: found `CAL_CLOSE_MS - 60` in, and longer than a beacon's quiet |
| `CAL_RESTARTS` | 1 | times a staircase that does not separate is read afresh before `ERR_CONTRAST` |
| `FLICKER_FOLLOWER_LATE_FIT_MS` | 880 | a POLLED Follower fits by this long after the quiet, when the Leader's staircase has ended |
| `CAL_REPS_LIT` | 2 | staircase repetitions for LIT. The Leader's staircase carries its own light; the Follower's the pair's |
| `LIT_STEPS_MAX` | 2 | high steps at most for a reader to take a staircase for LIT |
| `BACKLIGHT_LIT_INDEX` | 12 | derived: where a POLLED pair starts LIT (16 ms): three `BACKLIGHT_LIT_SETTLE_MS` |
| `BACKLIGHT_INDEX` | 9 | derived: where a POLLED pair starts in the dark (32 ms): 2.5 `FINE_MS` samples and three `BACKLIGHT_SETTLE_MS` |
| `EXACT_INDEX` | 12 | derived: where an EXACT pair starts in the dark (16 ms): the fastest rung whose window, by `SETTLE_DARK_US` and `GUARD_DARK_US`, is `WINDOW_MIN_US` |
| `EXACT_LIT_INDEX` | 15 | derived: where an EXACT pair starts LIT (8 ms): the same by `SETTLE_LIT_US` and `GUARD_LIT_US`; the rule allows 6, which is not on the ladder [**gated**: Dev Mode M5] |
| `PROVEN_INDEX` | 7 | derived: `BACKLIGHT_PROVEN_MS`'s rung. CRC failures faster than it demote |
| `CAL_GAP_MS` | 250 | the shared gap after calibration, before the slot clock starts: `CAL_CLOSE_MS` |
| `ANNOUNCE_TIMEOUT_MS` | 60000 | how long either badge looks for a peer |
| `TRANSFER_MAX_US` | 1006632960 | a transfer this long ends `ERR_LOST_PEER`: its relative µs stay a small int |
| `BLOCK_TIMEOUT_MS` | 300000 | outer deadline on a single block |
| `LOST_PEER_SLOTS` | 16 | more than this many consecutive black slots end a transfer |
| `STALL_SLOTS` | 64 | more than this many slots spent listening without the transfer advancing end it, or prompt a Follower's resend request |
| `RESEND_QUIET_SLOTS` | 3 | consecutive black slots a Follower waits for before asking for a block again -- after a stall or a framing failure -- so the Leader has finished transmitting |
| `MAX_BLOCK_RETRIES` | 2 | CRC failures tolerated per block |
| `MAX_BLOCK_ATTEMPTS` | 48 | absolute ceiling on transmissions of one block: `(MAX_BLOCK_RETRIES + 1)` per rung |
| `BLOCK_BYTES` | 64 | payload bytes per block |
| `RESYNC_LEAD_IN` | 4 | black slots before every block |
| `MAX_PATTERN` | 16 | longest symbol run any match looks back over |
| `BREAK_DETECT_SLOTS` | 2 | a continuous Leader that reads high this long while it sends a block takes it as the Follower breaking in, and stops |
| `BREAK_ACK_SLOTS` | 2 | a breaking Follower that reads dark this long takes the Leader as stopped, and sends its Data Sync Error |
| `BREAK_MAX_SLOTS` | 24 | a break unanswered this long is dropped, and the block waited out as before |
| `VERDICT_LOST_SYMBOLS` | 12 | a continuous Follower that has read more lit symbols than this awaiting a verdict, with none matched, has missed it and breaks in |
| `SYNC_LEAD_IN` | 9 | extra black slots before a block resent after the peer's Data Sync Error |
| `COMPLETE_RETRY_SLOTS` | 12 | the Leader repeats Complete after more than this many slots unanswered |
| `MAX_COMPLETE_RETRIES` | 3 | repetitions of Complete before giving up |
| `LINGER_SLOTS` | 20 | the Follower finishes after more than this many slots without a repeat of Complete |
| `SCHEME_FIELDS` | fields | light-level scheme: pixels carry the level, backlight at full. Always available |
| `SCHEME_BACKLIGHT` | backlight | light-level scheme: backlight carries the level. Offered, never assumed |
| `MID_FIELD_RGB` | 173, 173, 173 | the fields scheme's middle pixels: 68% of white per channel. As rgb(151, 162, 146) against the tinted palette white it measured 0.400 of the swing **[measured-all]**; against full white, the same share **[inferred-all]** |
| `MID_BACKLIGHT` | 0.82 | the backlight scheme's middle `display.backlight()`: 57% PWM duty on v2 **[source-v2]**, 61% on v3 **[source-v3]**. Was 0.74, which mirror runs read at 0.25-0.5 of the swing **[measured-all]**, then 0.80 |
| `LOW_BACKLIGHT` | 0.50 | the backlight scheme's low: dim, not off -- 14% duty on v2, just above the driver's threshold. Under one ADC step above off, and it cut the dark edge delay from 1.2-1.9 ms to 0.3 **[measured-all]** |
| `STEP_SAMPLES` | 3 | samples a level must hold to count as a staircase step when reading its order |

---

## What the measurement found

One badge has measured its own panel eight times, held screen-first against
a mirror, in a dark room and a lit one, and timed its frames and reads once
more with no mirror at all:

- **2026-09-23**, [hardware-measurement-2026-09-23.json](hardware-measurement-2026-09-23.json),
  with the measurement tooling described under "How the constants were
  measured";
- **2026-10-03**, [hardware-measurement-2026-10-03.json](hardware-measurement-2026-10-03.json),
  with `share_wifi`'s Dev Mode -- the mirror tool, then *probe frame timing*.
  It was taken with the transmitter still drawing HIRES bands, so its
  `frame_timing` and `edge_latency` describe that screen, not this one.
- **2026-10-04**, [hardware-measurement-2026-10-04.json](hardware-measurement-2026-10-04.json),
  the same tools after the change to `LORES` and the whole screen. **Two
  things changed at once**: the field, and the hold -- the sensor's side about
  6 mm (0.25 in) off the mirror with the other side touching it, where the
  earlier runs were held flat. Its optical figures cannot separate the two.
- **2026-10-05**, [hardware-measurement-2026-10-05.json](hardware-measurement-2026-10-05.json),
  the same tools as 2026-10-04, **held flat** again, and with the
  **full-range** black and white of "Light levels and symbols" -- the earlier
  runs drew the palette's tinted ones. So against 10-04 the hold and the
  colours changed; against 10-03 the field (bands, then the whole screen) and
  the colours. The first file copied off the badge with `mpremote` rather
  than typed.
- **2026-10-05b**, [hardware-measurement-2026-10-05b.json](hardware-measurement-2026-10-05b.json),
  *probe frame timing* alone, the first run with its `reads` step. Its
  `mirror` section is 2026-10-05's, carried over unchanged: a probe run
  replaces only `frame_probe`.
- **2026-10-05c**, [hardware-measurement-2026-10-05c.json](hardware-measurement-2026-10-05c.json),
  the mirror tool held flat, as 2026-10-05, and *probe frame timing*: the
  first run with `backlight_step`.
- **2026-10-05d**, [hardware-measurement-2026-10-05d.json](hardware-measurement-2026-10-05d.json),
  the same again, with `backlight_step` recording why it left a capture out.
  It agrees with 10-05c to within an ADC step everywhere bar the two noted in
  the table, which share a column with it.
- **2026-10-05e**, [hardware-measurement-2026-10-05e.json](hardware-measurement-2026-10-05e.json),
  the same again, the first with `backlight_step`'s capture no longer blind
  after the change: every capture kept. Its lit room is the one run whose
  edge latency matched the dark -- see below.
- **2026-10-06**, [hardware-measurement-2026-10-06.json](hardware-measurement-2026-10-06.json),
  the mirror tool, *probe frame timing*, and nine *measure room flicker*
  captures. The first run with the continuous `loopback`, `reads.transfer`
  and `temperature_c`.

Together they settle most of the constants this document had been guessing at,
and the news for one of them is bad.

| | Was | 2026-09-23 | 2026-10-03 (dark / lit) | 2026-10-04 (dark / lit) | 2026-10-05 (dark / lit) | 2026-10-05c, -05d (dark / lit) | 2026-10-05e (dark / lit) | Verdict |
|---|---|---|---|---|---|---|---|---|
| sensor resolution | assumed 16-bit | **12-bit, one step = 16 counts** | 16 / 16 | 16 / 16 | 16 / 16 | 16 / 16 | 16 / 16 | every reading a multiple of 16 |
| full swing, fields | `MIN_CONTRAST` (since replaced) guessed at 1500, then 750 | **80 counts, five ADC steps** | **80 / 64** | **64 / 64** | **80 / 80** | **80 / 96** | 80 / 80 | five steps held flat, four tilted; six once, lit |
| full swing, backlight | -- | -- | **80 / 80** | 64 / 64 | 80 / **96** | 80 / 96 | 80 / 80 | at best one step more than fields |
| noise, peak to peak | unknown | **32 counts, two steps** | 0-16 / 16-32 | 16 / 16-32 | 16-32 / 16-48 | 16-32 / 16-32 | 16-32 / 16-32 | two steps, three once; standard deviation 2-10 counts |
| middle field | chosen to hold across gamma 1.0-2.5 | **0.400 of the swing** | 0.4 / 0.5 | 0.5 / 0.5 | 0.4 / 0.4 | 0.4 / 0.5 | 0.4 / 0.4 | right, and confirmed |
| backlight middle (0.74) | 50% duty | -- | 0.4 / 0.4 of the swing | **0.25 / 0.25** | 0.4 / 0.5 | 0.4 / 0.5, then 0.4 / 0.33 | 0.4 / 0.4 | moves with the hold |
| panel gamma | assumed 2.2 | **1.95** (2.32 dark, 1.58 lit) | 2.01 / 1.50 | 1.99 / 1.83 | 2.26 / 1.41 | 2.26 / 1.38, then 2.26 / 2.38 | 2.26 / 1.37 | close enough that the field choice holds |
| PWM flicker | **[unmeasured]**; argued against | -- | **averages**, at every backlight from 0.25 to 1.0 | averages | averages | averages | averages | the sensor does not see the ~3.8kHz PWM |
| `LCD_SETTLE_MS` | 16, from the datasheet | **under 22 ms**, not resolved further | not fitted | lit: rise tau 10.5 ms, fall 8.7 ms, **90% at 24.1 ms**; dark: not fitted | dark: rise 5.5, fall 7.6, **90% at 17.4 ms**; lit: rise 7.3, fall 8.7, **20.1 ms** | dark: rise 4.6-4.8, fall 7.1, **16.4 ms**; lit: rise 7.4, fall 7.7-8.1, **17.7-18.6 ms** | dark: rise 5.0, **16.1 ms**; lit: rise 5.0, **17.7 ms** | **16-24 ms**: at or a little over the budget -- see below |
| panel refresh | assumed 90 Hz | -- | **59.8 Hz** (v2 firmware) | 59.8 Hz | 59.8 Hz | 59.7 Hz | 59.8 Hz | see "Screen as a transmitter" |
| frame, mirror run | assumed 11.1 ms | -- | 53.9 ms (HIRES bands) | **16.8 ms** (`LORES`, whole screen) | 16.8 / 16.7 ms | 16.8 / 16.8 ms | 16.8 / 16.8 ms | one refresh: a FAST badge |
| edge latency, from the present call to `T_low`, dark / lit | -- | -- | 30.2 ms (an artefact) | 13.0 / 18.6 ms | 12.9 / 23.1 ms | 12.7-13.3 / 22.8-23.0 ms | 12.9 / **14.1 ms** | **13-23 ms, moving with the panel's rise time constant**, not with the room -- see below |
| backlight step, 10/50/90% from the call | -- | -- | -- | -- | -- | 10-05d, dark: rise 2.05/2.79/3.89, fall 0.06/0.62/3.19 (8 of 10), middle 4.18/5.53/7.49 ms; lit: rise 0.11/0.68/1.54 (9), fall 0.18/0.55/1.20 (6), middle 0.08/0.62/1.07. The first 0.3-0.5 ms after the call was blind in both runs -- see below | dark: rise 1.92/2.76/3.87, fall 0.19/0.63/3.24, middle 4.06/5.18/7.75; lit: rise 0.32/0.57/1.20, fall 0.32/0.52/1.25, middle 0.29/0.61/0.97 -- all 10 of 10, nothing blind | **about 1.2-1.5 ms lit, up to 7.8 dark**: the sensor, slower in less light -- see below |

**The panel settles at or a little over the protocol's budget.** Nine fits
now, from five runs: 90% of a transition in **16.1-16.4 ms** in the dark
(10-05c, -05d, -05e), **17.4 ms** (10-05 dark), **17.7 ms** (10-05c and -05e
lit), **18.6 ms** (10-05d lit), **20.1 ms** (10-05 lit) and **24.1 ms**
(10-04 lit), against the 16 ms `LCD_SETTLE_MS` budget and the 22 ms the
2026-09-23 run bounded it under. Rising time constants run 4.6-10.5 ms,
falling 7.1-8.7. Each fit comes from a
four- or five-step swing, checked against simulated captures of such a swing
with the sensor's noise to land within a sixth of the true value
**[inferred-all]**. `SETTLE_SLOTS` x 16-24 ms is 49-72 ms, so the fastest safe
rung would be 65 or 80 ms rather than 50. The simulator's `slow panel` and
`cold panel` scenarios are 16 and 24 ms. **Acted on**: `LCD_SETTLE_MS` is 24,
so pixels started on the 80 ms rung, until the data phase left pixels
altogether -- see "Why the fields staircase went".

**The hold costs a step; the whole screen and full-range colours together
gain a step at most.** Held flat, 10-03 (bands, tinted colours) and 10-05
(whole screen, full range) both read 80 counts in the dark, and 64 against 80
in the light; tilted, 10-04 read 64 in both. The geometry in
[optical-coupling.md](optical-coupling.md) puts the whole screen under 10%
above the bands and the tilt a little below both **[inferred-all]**, so most
of the light-room step is the colours -- and full white was predicted to add
about a fifth, where it measured nothing in the dark and one step in the
light **[measured-all]**.

**10-06: the continuous path works, and temperature is not the cause.**

- **Loopback**: no errors in 29 symbols at every rung from 50 to 16 ms, in
  both rooms, low off and dim. The edge came 1.2-1.9 ms after the backlight
  call in the dark with low off, 0.3 ms with it dim, and about 0 lit
  **[measured-all]**. 29 symbols rules out a high error rate, not a rare one,
  and one badge on its own clock tests no clock recovery.
- **The transfer loop** runs 2,629 passes a second, 380 µs each, the
  slowest 2.7 ms **[measured-v2]**: faster than the 0.43 ms simulation assumes.
- **Room flicker**: 120 Hz from every LED light, by 1% (room A), 30-47%
  (rooms B and C, one open area) and 0.1% (room D) of the light the sensor
  saw. See "Mains flicker is filtered".
- **Temperature**: the chip read 26.1-26.6 C through the dark run and 26.6
  through the lit one **[measured-v2]**, while edge latency was 14.4 and 23.1
  ms. So it is not the cause below.

**Edge latency moves with the panel's own speed, not with the room.** From
the present call starting to the rise crossing `T_low`, with the present
itself blocking 11.9-12.4 ms each time:

| Run | Dark: latency, rise tau | Lit: latency, rise tau |
|---|---|---|
| 10-04 | 13.0 ms, not fitted | 18.6 ms (13.6-22.4), 10.5 ms |
| 10-05 | 12.9, 5.5 | 23.1, 7.3 |
| 10-05c | 12.7, 4.6 | 22.8, 7.4 |
| 10-05d | 13.3, 4.8 | 23.0, 7.4 |
| 10-05e | 12.9, 5.0 | **14.1, 5.0** |

For three runs the lit room read 9-10 ms later than the dark, and this
document called that a consistent dark/lit difference. 10-05e's lit room
matched its dark. What the late runs share is a **slower panel**: every run
whose rise time constant was 4.6-5.5 ms crossed `T_low` at 13-14 ms, and every
one at 7.3-7.4 ms at about 23. So it is the liquid crystal responding more
slowly in those runs **[inferred-all]** -- not the room light, and not the
sensor, which `backlight_step` shows getting faster when the room is lit.
Liquid-crystal response slows as the panel cools, so temperature was the
obvious candidate, but 10-06 read the same chip temperature in both rooms
with the same 9 ms difference. What does track it, in five of six lit runs,
is where `T_low` -- half way between black and the middle -- lands: about
23 ms when the middle read three ADC steps above black, putting `T_low` two
steps up, and about 14 ms when it read two, as in every dark run. 10-05 lit
is the exception. One step more of threshold should cost a millisecond or
two on a first-order rise, not ten, so this suggests the liquid crystal
starts very slowly, before the part the settle fits see. It matters only on
pixels.

It matters for phase locking, which places a slot's edge at that crossing
and corrects for a fixed delay: on one badge the delay has moved by 9 ms
between runs. Two explanations put forward earlier are out:

- *Quantisation* -- where black sits in its 16-count step deciding how far a
  rise must go to cross `T_low`. 10-05 lit and 10-05e lit both had `T_low`
  one ADC step above black, 20% of the swing, and read 23.1 and 14.1 ms.
- *Room-light flicker.* The lit rises were tightly grouped (22.5-23.1 ms over
  ten rises 600 ms apart) where a 59.7 Hz panel against flickering lights
  would drift through every phase; and the lights did not make 10-05e late.

**And a field reaches the panel about 19 ms after the frame that decided
it.** The simulator used to put it there at once. On the badge the frame
draws for ~4 ms, `display.update()` waits ~4.7 ms for vertical blank and
writes for 7.8 ms, and the measured rise crossed a tenth of its swing 13-19 ms
after the present began: 16-22 ms from the frame's start. `tests/share_wifi/
simulator.py --v2` now models the badge as measured -- 60 Hz frames, that
present delay (19 ms), and a panel settling 90% in 24 ms -- and the protocol
does markedly worse in it. 8 seeds x 6 scenarios x 3 payloads, 144 runs each,
the swing still the model's 9000 rather than the measured 64:

| model | delivered | median | ended on |
|---|---|---|---|
| 60 Hz, no present delay, scenario settle (as before) | 136 | 26.7 s | mostly 50 ms |
| + 19 ms present delay | 111 | 19.2 s | mostly 50 |
| + 24 ms panel | 118 | 38.3 s | 65 and 80 |
| **v2 as measured: both** | **99** | 23.9 s | 50 and 65 |
| v2 with the delay at 16 / 22 ms | 89 / 113 | 23.9 / 27.4 s | |
| v2, and `edge_lag` including the delay | 103 | 62.1 s | demoted to 100 |
| v2, and `LCD_SETTLE_MS` 24 (start at 80 ms) | 106 | 41.3 s | 80 to 130, no invariant broken |

> **Neither one-line fix is enough, and neither has been made.** Correcting
> `edge_lag` for the delay puts the receiver's grid on the transmitter's,
> and so its read window back on the transition the delay has moved there --
> it demotes to 100 ms. Raising `LCD_SETTLE_MS` to the measured 24 ms starts
> at 80 ms and breaks no invariant, but delivers 106 of 144. At 60 Hz a
> 50 ms slot is three frames, and its read window -- the back half, less the
> end guard -- holds about one sample; the 136 at 60 Hz without any delay was
> already below 90 Hz's 141. The sampling window, not the rung, looks like
> the lever. That is a protocol change still to make, and all of it is
> secondary while the measured swing (64 counts) fails calibration outright.

> **I2 trips in almost every run with a present delay** (142 of 144), and
> mostly does not once `edge_lag` includes it (17). The check compares the
> two badges' epochs, and with a delay the receiver's lands later by the
> delay `edge_lag` does not correct -- by design of the phase lock, and not
> by itself a failure. With a present delay I2 measures that offset rather
> than a broken link; see the invariants file.

**A receiver that stopped presenting would read about 40 times a slot.**
**[measured-v2]** (2026-10-05b) A listener loop that never presents -- the
clock, `sample_light()`, `SlotReceiver.feed()`, the button poll -- ran 2,166
passes a second, ~416 µs each net of the loop, with none over 782 µs. A 50 ms
slot is then ~108 readings and its read window ~40, where presenting every
frame leaves about one, because the present blocks reading for 12.4 ms of each
16.7. The listening badge's screen never changes while it listens, so nothing
requires it to present; edges would be timed to half a millisecond rather
than somewhere in a 12.3 ms blind stretch. **Acted on**: the app's transfer
ran exactly that loop, and now reads through a DMA ring -- see "Reading".
See "Reading without presenting" in [badgeware-hardware.md](badgeware-hardware.md).

**The backlight scheme skips the panel as well as the present.**
`display.backlight()` writes the PWM duty directly **[source-v2]**, so a
symbol carried by the backlight alone, on white pixels, is live within one
~263 µs PWM period: no 19 ms present delay and no 16-24 ms liquid-crystal
settle. The scheme's low level was black pixels until 2026-10-05, which made
every low a pixel change; it is now white at backlight 0.0, which reads the
same, so the scheme never changes a pixel (see "The backlight scheme").

What bounds it then is the sensor, and `backlight_step` (the mirror) has
measured it three times **[measured-all]**, the 90% times agreeing to within a
few tenths of a millisecond. 2026-10-05e, the first run with nothing blind,
every capture kept:

| ms from the call, 10/50/90%, 10-05e | Off to full | Full to off | Off to middle |
|---|---|---|---|
| dark | 1.92 / 2.76 / 3.87 | 0.19 / 0.63 / 3.24 | 4.06 / 5.18 / **7.75** |
| lit | 0.32 / 0.57 / 1.20 | 0.32 / 0.52 / 1.25 | 0.29 / 0.61 / 0.97 |

Up to 0.26 ms of each lit 10% time is the backlight itself: a new PWM duty
takes effect at the next wrap, one ~263 µs period at most **[inferred-v2]**.

> **10-05c and -05d were blind for the first few hundred µs after the change**,
> and that was the test, not the sensor. `changed()` re-timed its ~80
> lead-in reads between making the change and reading again: a few hundred
> µs of Python with no reads, exactly where a fast step crosses half way.
> Every capture that crossed inside it was left out as a `gap` -- 4 of 10 lit
> falls and 2 of 10 dark ones on 10-05d, which is what its new `skipped`
> counts showed, and all ten lit falls on 10-05c. Fixed: the re-timing now
> waits until the capture is over. So **any 10% or 50% time under about
> 0.5 ms in those two runs was interpolated across that blind stretch** --
> their lit 10% times of 0.1 ms against 10-05e's 0.3 -- while the dark rises
> and middles, and every 90% time, were clear of it and agree with 10-05e.

- **The phototransistor is slower the less light it has** **[inferred-all]**:
  from off in the dark it waits 1.9 ms to start, 4.1 ms when only going to the
  middle, and a fall starts in 0.2 ms but trails to 3.2 as it dims; in a lit
  room, 1.0-1.3 ms to 90% for everything. The backlight cannot know the room was lit, so this is the
  sensor.
- **So the floor on a backlight-scheme slot is ~7.8 ms to 90% in the dark,
  ~1.3 ms lit, where the fields scheme's is ~19 ms of present and 16-24 ms
  of settle.** At `SETTLE_SLOTS` (3) that would allow slots of about 23 ms in
  the dark and 4 ms lit, against today's 50 ms floor. A peer badge's screen
  over the sensor is predicted to give ~2.5x the mirror's light, and so to be
  faster still **[inferred-all]**. The middle level is both the slowest step
  and the one that moves with the hold, so two-level on/off signalling would
  be the fastest the scheme can go.
- **Now used.** The transfer loop places each backlight symbol within a
  reading of its boundary and reads continuously, the simulator models the
  backlight and the sensor apart from the pixels, and a pair on the
  backlight starts on 8-32 ms by its readers and light -- see "Reading". The 2nd-badge
  `eye` run that would show a peer's swing at speed has still not happened.
- **The middle level was two ADC steps.** Every capture's swing, off to
  0.74, was 32 counts against 80-96 for off to full -- the 0.33-0.4 share
  `levels` reports. `MID_BACKLIGHT` is 0.80 since, aiming for half.

**The middle field is vindicated, and the contrast is thin but not hopeless.**
This paragraph once said the opposite: that a five-step swing with two steps
of noise put adjacent levels "1.2x the noise apart, where three-level
signalling wants about 3x", and set `MIN_CONTRAST` at six times a peak-to-peak
noise of 32 -- **192 counts**, which no measured swing has come near. That
confused two measures of noise. Peak to peak is the widest spread over a
hundred-odd readings and grows with how many are taken; a decision is made
against the spread of one reading, a standard deviation of 2-10 counts. At 32
and 48 counts, the measured gaps are four to eight standard deviations
**[measured-all]**, and every run's medians separated all three levels. So
the fixed floor is gone: calibration now measures each level's noise on the
link it is calibrating and accepts the gaps that clear it -- see
"Separability".

> **The backlight scheme is measured now, and buys nothing here.** The second
> run gave it the same 80-count swing as fields in the dark and 80 against 64
> in the light, with its middle at 0.4 of the swing; the third, 64 in both,
> with its middle one step above black where the fields middle sat in the
> centre. Calibration would choose fields. The sensor averages the
> PWM at every duty measured, which is what the simulator assumes by default;
> `--flicker` models the case that did not happen. Its curve is far from
> linear: in the dark, backlight 0.0 to 0.4 reads the same as off, 0.5 to 0.7
> one step above, and the whole swing arrives between 0.7 and 1.0.

**The simulator agrees.** Re-running the sweep with its coupling set to the
measured swing and everything else unchanged:

| swing the sensor sees | 8 seeds x 6 scenarios x 3 payloads |
|---|---|
| 9000, as the model assumed | 141 of 144 |
| 2400 | 138 of 144 |
| 400, five times the measurement | 31 of 144 |
| **80, as measured** | **0 of 144** |

The link does not degrade gracefully into this, it stops: between 2400 and 400
it falls off a cliff, and the measured figure is well below the bottom of it.

> **This is one run, through a mirror, and the coupling looked poor.** Room
> light alone contributed sixteen ADC steps where the badge's own white screen
> contributed five. A reflection travels twice the standoff, and the sensor
> sits ~1mm outside the active area so it views the screen at a glancing angle
> -- all of which a second badge held squarely against the first improves on.
> Before concluding the link cannot work, re-measure: flat, close, clean
> mirror. If the swing stays near 80, it cannot.

**What would have to change**, in rough order of what each would buy:

- **Optical coupling.** Everything else is downstream of five steps of swing.
- **Two levels instead of three.** Dropping the middle field puts the whole
  swing between the two symbols -- 2.5x the noise instead of 1.2x -- at the
  cost of the Field Separator's unforgeability, which the framing rests on.
  Not a small change, and the only one that helps at this swing.
- **More samples per slot.** Noise falls as the root of the count and
  `SlotReceiver` already takes a median of what it has, so longer slots buy
  some of it back at a direct cost in throughput.

---

## Reading

**A transfer reads the sensor through a reader for its whole length, and the
protocol is called once a slot.** Two readers, in
[`reader.py`](../../firmware/apps/share_wifi/reader.py), which is pure:

- **EXACT**, `RingReader` over `fastadc.Ring`: the ADC converting
  free-running at `RING_RATE_HZ` (50 kHz) and one DMA channel writing every
  reading into a 32 KB ring -- 16,384 readings, 327.7 ms -- with no CPU at
  all. A reading's time is its index: `anchor + 20 µs x index`. A protocol
  call, a present, a collection or a fit no longer blinds the receiver; it
  only delays when the readings are looked at.
- **POLLED**, `PolledReader`: `light_level()` read in a loop whenever the
  protocol has nothing due, into `POLL_BINS` (1,024) bins of `POLL_BIN_US`
  (250 µs), 256 ms. Blind while the badge does anything else. The fallback
  where the ring is refused.

Both answer the same queries -- `parts(t0, t1, n, out)`, the means of n equal
parts of a span; `mean()`; `rise()`, the first crossing of a threshold -- in
`light_level()` counts, with -1 for a part that has no readings or has left
the history. The tier is carried in the staircase's order (see "The flags");
it sets only the start rung and where this badge puts its own window.

Before 2026-10-07 the app read `light_level()` back to back for 2 ms
samples and called the protocol once a sample -- "reading continuously". The
history of that, and why each reading's cost mattered, is under "What a
reading costs" below; the loop is gone.

### The ring

Made once a launch, on the first transfer, by `transfer_reader()`; the
reason it was refused, if it was, is `ring_note()`, and Dev Mode records it.

- **The buffer** is a 64 KB `bytearray`, allocated once, whose address must
  lie in PSRAM behind the XIP cache, 0x11000000-0x117FFFFF **[source-v3:
  `mpconfigboard.cmake`]**. The ring is its 32 KB-aligned half, used only
  through the **uncached alias**, 0x15000000 + (address - 0x11000000) --
  `XIP_NOCACHE_NOALLOC_BASE` 0x14000000 plus chip select 1's 16 MB **[Pico
  SDK `addressmap.h`; `st7789.hpp`'s `XIP_PSRAM_NOCACHE`]**. The DMA writes
  there and every read is there, so no cache line holds a stale or dirty copy.
  Before first use the cached alias's lines are cleaned and invalidated by
  byte writes to `0x18000000 + (address - 0x10000000) + 3`, one per 8-byte
  line **[Pico SDK `xip_cache.c`, `cache_op_t`]**, so the allocator's
  zero-fill cannot be written back over DMA data later.
- **Hardware**: `ADC.DIV` = 959 (960 cycles of the 48 MHz clock, 50 kHz
  exactly), `FCS` = `EN | DREQ_EN | THRESH(1)`, a channel paced by `DREQ_ADC`
  (48) with `pack_ctrl(size=1, inc_write=True, ring_size=15, ring_sel=True,
  high_pri=True)` and a count of `COUNT0` (0x0FFFFFFF: `TRANS_COUNT` is 28
  bits on RP2350, 5,369 s at 50 kHz), then `CS` = `AINSEL(3) | EN |
  START_MANY`. `high_pri` lets the ring win arbitration over the panel's DMA
  during a present. The ring fields and `high_pri` have never run on the
  badge **[unverified]**.
- **Position** is `COUNT0 - dma.count`: the next index to be written. The
  newest reading trusted is two short of it, since the write may be in
  flight.
- **Health**, checked before every block: the ADC still converting input 3
  many; `FCS.OVER` (bit 11) and `FCS.UNDER` (bit 10), write-1-to-clear
  **[Pico SDK `adc.h`, RP2350]**, cleared by writing the ring's own `FCS`
  value with them set, never a read-modify-write; the channel still busy. A
  lost reading re-anchors and carries on. Anything else ends the transfer
  `ERR_LOST_PEER`: there is no switch to POLLED mid-transfer.
- **The anchor**, the time of index 0, is the best of `ANCHOR_TRIES` (4)
  brackets of `ticks_us()` around a position read. At every block a fresh one
  is compared, not adopted -- one crystal clocks both -- and a difference past
  `RING_DRIFT_US` (100, five readings) is counted as lost readings and
  adopted.
- **The history** a query may use is the ring less `RING_SAFE_US` (10 ms)
  kept clear of the write pointer: 317.7 ms.
- **`verify()`**, once a launch, about 0.4 s: checks the cache clean wrote
  back the marker `Ring()` dirtied the cached alias with; fills the ring
  with 0xFFFF, which no 12-bit reading is; checks after 2 ms that the DMA
  has written what it should and not past it, and again after a wrap; then
  churns 256 KB of heap and checks at once that no cached line -- the
  marker -- landed over the readings; and runs each viper kernel
  against its pure twin on 1,024 readings. Any failure stops the ring and leaves the
  launch on POLLED. This is where the uncached alias, the cache maintenance
  and the ring fields are established on a given firmware -- Dev Mode M1 and
  M3 record the outcome.
- **Stopping**: `finish_transfer()` stops the reader, and the app's
  `on_exit()` stops it too, inside `try`, however the app is left: HOME runs
  `on_exit()` and then resets **[source-v2]**, and a raise there would skip
  the reset. Nothing else may touch the ADC while the ring runs -- not
  `light_level()`, not the temperature; Dev Mode reads `temperature_c` before
  the ring starts and after it stops.

**The kernels** -- `ring_sum`, `ring_parts`, `ring_rise`, `ring_fill`,
`ring_count` -- are `@micropython.viper`, read with `ptr16` through the
alias, index `& mask`, and allocate nothing. Each has a pure twin in
`reader.py`; `tests/share_wifi/test_fastadc_kernels.py` checks kernel against
twin under CPython, compiles `fastadc.py` with `mpy-cross` and runs the
kernels under the unix port's real viper when those are found. A kernel's
cost over the uncached alias is **[unmeasured -- M3
`kernel_us_per_1000`]**.

### The loop

```
while True:                               # do_transfer()
    if transfer.collect_due: gc.collect()  # asked for, never inside a call
    reader.pump(transfer.due_us())         # wait; POLLED reads meanwhile
    t = reader.now_us()
    show transfer.field_at(t)              # backlight now; pixels -> run()
    transfer.work(t)                       # everything complete by t
    every POLL_MS (50 ms): poll, B, case lights
```

`due_us()` is the next slot boundary while transmitting, the close of the
next undecided slot's window while listening, the end of the next 2 ms
sample before the data phase, and now when a collection is due. So a
transmitter sets its backlight one pass of the spin after the boundary, then
works. Only a change of pixels -- the beacons -- returns to `run()` to be
presented; the ring reads through it.

**Time is µs since the transfer began**, accumulated from `ticks_us()` so it
never wraps; `TRANSFER_MAX_US` (2^30 - 2^26) ends a transfer `ERR_LOST_PEER`
long before it would leave a small int. The phases before the data phase
keep their ms logic, fed `t // 1000`.

**Before the data phase** the protocol pulls the 2 ms means since its last
call from the reader and pools them: `COARSE_MS` (8) in discovery,
`CAL_SAMPLE_MS` (4) in calibration.

**In the data phase** it decides each slot once the slot's window has closed
-- see "Slots and sampling" -- from the median of 5-32 (`PARTS_MIN`,
`PARTS_MAX`) flicker-corrected parts of at most `PART_MAX_US` (4 ms); an
even count's median is the mean of the middle two. A POLLED window's parts
are merged in order until each holds `POLL_PART_READINGS` (2) readings
(`merge_parts()`), each then flicker-corrected over the span it covers
(`part_span`) -- at the nominal span a 6 ms merged part had 2.5 times too
much 120 Hz taken off: at a reading every 2 ms a part held one or none, and the
median of two parts -- the upper one, before the even-count rule -- read a
dark slot after a lit one inside the hysteresis band. Slots
are decided in order; after a stall the protocol catches up, losing time but
no readings while the stall is shorter than the ring's history. Slots this
badge transmits in, or breaks in on, are never decided. A re-base makes slot
0 of the new grid the next decided; a demotion skips only the slot it
straddles.

### Rungs

`exact_index()` is the fastest rung whose window -- from `max(settle, 40%)`
to `max(guard, 15%)` short of the end -- is at least `WINDOW_MIN_US` (2 ms,
100 readings), and at least `SLOT_MIN_MS` (6):

| Pair | settle / guard | starts on | proven |
|---|---|---|---|
| EXACT, both LIT | `SETTLE_LIT_US` 2.5 ms / `GUARD_LIT_US` 1 ms | **8 ms** | 50 ms |
| EXACT, dark | `SETTLE_DARK_US` 10 ms / `GUARD_DARK_US` 2 ms | **16 ms** | 50 ms |
| POLLED, both LIT | the old rule | 16 ms | 50 ms |
| POLLED, dark | the old rule | 32 ms | 50 ms |

A pair is EXACT only if both are, and LIT only if both are: the Follower
decides and the Leader adopts (see "The flags"). The ladder gained 13, 10 and
8 ms. Settle and guard are the sensor's measured speed -- 1.0-1.3 ms to 90%
lit, up to 7.8 ms from off in the dark -- with margin, measured from off and
at 2 ms resolution; from the dim low it is only faster
**[inferred-all; unmeasured -- M4 `backlight_step_ring`]**. Whether a dark
pair can start on 10 ms, and a lit one on 6, waits on M4 and M5.

**Edges.** A crossing is found in the reader's history (see "Phase
locking") and the boundary put `EDGE_LAG_LIT_US` (100) or
`EDGE_LAG_DARK_US` (300) before it: the PWM wrap, up to ~263 µs
**[source-v2]**, and the sensor's climb to `T_low`, 0.3 ms in the dark from
the dim low on the 10-06 loopback at 2 ms resolution **[measured-all;
unmeasured at sub-ms -- M4, M5 `lag_us`]**.

### What a reading costs

On the badge each Python operation costs microseconds and a protocol call
about a millisecond **[measured-v2]**. Called once a reading, the old loop's
`reads.transfer` ran:

| Run | Pass | |
|---|---|---|
| 2026-10-06 | 380 µs | `step()` per reading, flicker trigonometry per reading |
| 2026-10-06a | 705 µs | `step()` per reading, with a cheap path for a second reading in the same ms |
| 2026-10-06b | 1,073 µs | the same, in a run 1.5 times slower throughout |

The cheap path never ran: a first reading cost ~1.6 ms, so the next was always
in a new ms. Under MicroPython's unix port the same code ran six to fifteen
times faster: the port is no guide to the badge's cost, only to its shape.
That led to reading 2 ms samples in a tight loop and calling once a sample;
the ring takes the readings off the CPU altogether, and the protocol is now
called once a slot. What a decided slot costs on the badge is
**[unmeasured -- M3 `slot_work`]**; the simulator charges `SLOT_WORK_MS`
(1.2 ms) a slot and `PASS_WORK_MS` (0.15) a pass, and `work3` triples both.

### Breaking in

A Leader reads while it transmits, so a Follower that has lost the block need
not wait for it to end -- up to ~520 slots of a 64-byte block:

- The Follower shows high and holds it: when it loses framing (`frame_error`),
  and when it has read more than `VERDICT_LOST_SYMBOLS` (12) lit symbols
  awaiting a verdict with none matched -- it missed the verdict and the
  Leader has moved on.
- The Leader, sending a block, takes `BREAK_DETECT_SLOTS` (2) of high -- above
  `T_high`, so neither its own light nor a middle level does it -- as a break.
  It reads them with `parts()` on the slots it is sending in, and stops and
  listens, exactly as if the block had ended.
- The Follower takes `BREAK_ACK_SLOTS` (2) of dark as the Leader stopped, and
  sends the Data Sync Error it always would have. Both demote on that,
  through the path they already had; and a Leader that never heard the break,
  finishing the block, lands on the same path. A break unanswered for
  `BREAK_MAX_SLOTS` (24) is dropped and the block waited out.
- **The resend repeats the last verdict.** A Leader resending after a Data
  Sync Error sends its last verdict first, then the lead-in. A Follower that
  holds a block it never heard the verdict on settles it from that, and one
  that holds none ignores it. A Follower holding an unsettled block will not
  take a new one: it breaks in again rather than overwrite it.

### Low is dim, not off

Black at backlight 0.5 -- 14% duty on v2, just above the driver's ~11%
threshold -- keeps the sensor faintly lit, so a rise starts from a little
current rather than none. On the badge (2026-10-06) it cut the dark room's
edge delay from 1.2-1.9 ms to 0.3, read under one ADC step above off, and
carried the loopback block at 25 and 16 ms with no errors
**[measured-all]**. The middle moved to 0.82 to keep the gaps even. The
simulator takes light as PWM duty past the driver's threshold, which fits the
mirror's `backlight_curve` to an ADC step at every value measured. The mirror
`loopback` keeps an `off_25` and `off_16` block, low off, for comparison.

**`MID_BACKLIGHT` is 0.82.** At 0.74 -- 43% duty on v2 -- mirror runs read the
middle at 0.25-0.5 of the swing, 0.4 typically: two ADC steps, on the
32-count `MIN_GAP`.

### Mains flicker is fitted before calibration, then tracked

Every LED light measured flickered at 120 Hz, some by 30-47% of their light
(`room_flicker`, 2026-10-06), and not as a sine: the brightest rooms' spread
implies harmonics at about half the 120 Hz component's size. At the lit
mirror hold's 48 counts of room light, 47% is ±22 counts -- more than an ADC
step, against gaps of 32-48. On the lit 8 ms rung, a window is 3.6 ms: under
half a cycle at 120 Hz, so uncorrected flicker moves a window's median by
most of its amplitude.

*The simulator's lights* (`FLICKER_LIGHTS`): "led60"/"led50" flicker at twice
the mains with a 30% fourth harmonic, as measured; "half60"/"half50" once a
cycle, as no light measured did; any of them on a mains off nominal. Grids
hold within about 0.02 Hz, which moves 120 Hz flicker 14 degrees a second
**[unmeasured here]**. A filter that learnt flicker only from dark data
slots delivered 77 of 144 with the mains 0.02 Hz off, and 0 under a
once-a-cycle light.

So each badge fits the room's flicker once, early, from a window in which its
peer is known dark, and keeps the fit in step with the mains:

- **The windows** are in calibration, where each badge's stall falls while it
  only holds LOW or only reads dark (see "Calibration"). The Follower fits
  `FLICKER_FOLLOWER_FIT_MS` (100) of 2 ms samples starting
  `FLICKER_FOLLOWER_FIT_START_MS` (160) after it saw the Leader's beacon go
  quiet, inside the Leader's long opening gap (`CAL_OPEN_LEADER_MS`, 400) --
  an EXACT one. A POLLED Follower is blind for the stall, and one longer
  than about 180 ms ran into the Leader's first steps (simulation: 57 of 144
  at a 300 ms stall, 126 at three times the work): it fits the same window
  from its kept samples (`FLICKER_BUFFER`, 1.28 s) once its reader ends or by
  `FLICKER_FOLLOWER_LATE_FIT_MS` at the latest, reads the staircase again
  corrected, and decides on its next call, after the stall. The replay
  re-feeds up to 640 samples: the simulator charges `REPLAY_MS_PER_SAMPLE`
  (0.25 ms) each, about 160 ms in all, on top of the fit **[unmeasured --
  M7 `flicker_filter.replay`]**.
  The Leader fits the last `FLICKER_FIT_MS` (160) of its own staircase's
  body, which the Follower watches dark, as its closing gap begins.
- **The fit.** A straight line -- a sensor still settling -- then each of
  `FLICKER_HARMONICS` (4) harmonics of each mains in `FLICKER_MAINS` (50,
  60), projected in whole numbers from the sine table and refined
  `FLICKER_FIT_PASSES` (2) times; the mains that explains more is the region.
  It takes 3.3 ms under the unix port; on the badge it is
  **[unmeasured-v2 -- M7 `flicker_filter`]**, and the simulator charges a
  pessimistic 150 ms stall for it (`fit300` doubles that).
- **What it refuses.** A window it cannot explain to `FLICKER_FIT_MAX_NOISE`
  (12 counts) was not dark and steady, and is tried again at half the length,
  then left to the old filter. A harmonic its samples barely saw
  (`FLICKER_MIN_SEEN`, 0.3) is not fitted. A harmonic under
  `FLICKER_FIT_SIGMAS` (4) of its standard error, or `FLICKER_MIN_COUNTS`, is
  dropped; with none left the room is steady and nothing is taken off.
- **Correcting** is one look-up a part: a table per span step of the fitted
  flicker as a part that long sees it. The table for a rung's part span is
  built when the rung is set, not on the first slot. Every sample is
  corrected from the fit on, calibration's included; the Follower, which fits
  after its staircase reader has begun, reads what it had again, corrected.
  The phase-lock scan's thresholds are `T_low` plus the fitted flicker, one
  per step of bins.
- **Tracking** is a phase lock on the table's offset, from
  `FLICKER_TRACK_POINTS` (8) parts the peer was dark for at a time, moving
  the offset by `FLICKER_TRACK_GAIN` (0.25) of the error; the rate is the
  slope of a line through the measured timings, used once its points span
  `FLICKER_RATE_MIN_S` (0.3 s) **and** carry `FLICKER_RATE_MIN_UPDATES` (10)
  updates of weight. A Leader also tracks **while it sends a block**, from
  the Follower's dark slots it reads to watch for a break: a block is its
  longest dark stretch. That assumes its own screen barely reaches its own
  sensor, off the Follower's glass **[unmeasured]**; the simulator's
  `self_coupling` puts 5% of it there (`self5` row), and if that costs
  deliveries the Leader should learn only from slots it sent at one level. Without both, a POLLED Leader -- its fit stall
  blinding most of its closing gap, its only tracking window before the
  block -- learnt -9.8 steps a second from 5.85 updates in a room with no
  drift, extrapolated it through an 8.6 s block, and misread the CRC reply:
  132 of 144 at 47% flicker, now 144.
  `test_a_polled_leaders_flicker_stays_fitted_through_a_block` holds it.
- **The fallback** is the earlier filter: sines at `FLICKER_HZ` (100, 120),
  learnt from the parts of slots decided dark.
- Everything per part is whole numbers from tables. Floats only in the fit.

Whether 8 ms survives a flickering room is **[unmeasured -- M6
`window_residual`]**: the room-flicker tool now reports how much flicker the
fit leaves in a 3.5 ms window every 8 ms.

### What simulation says

`simulator.py --loop` (and `tests/share_wifi/sweep.py`, which sweeps it)
runs both badges through the production readers: a ring of entries each
summing five readings at 20 µs (`RING_ENTRY_US` 100) with `RING_SD` 8.5 of
noise a reading, or a polled reader reading every 0.43 ms whenever the badge
is not busy. Each pass costs `PASS_WORK_MS`, each decided slot
`SLOT_WORK_MS`; collections stall a badge 20-46 ms (`GC_MS`), the flicker fit
150 ms. The present waits for vertical blank and writes for 7.8 ms; the
liquid crystal starts 7 ms after the blank on a fast panel and 16 on a slow
one; the backlight changes at the next PWM wrap; and, with `--optics`, the
sensor follows the light at a speed the light sets -- ds/dt = (I - s) * s /
K, fitted to `backlight_step` on 2026-10-05e **[inferred-all]**. 8 seeds x 6
scenarios x 3 payloads a row (`sweep.py`; 2026-10-07). The median is from
the first beacon and includes the 1.5 s the simulator starts the badges
apart in four of its six scenarios; "skew-free" takes it off. Typical
skew-free phases, EXACT: discovery 0.7 s, calibration 1.6 s lit and 1.8
dark, data 1.7 s lit and 3.5 dark.

| Row | EXACT (ring) | POLLED | HEAD (2 ms loop) |
|---|---|---|---|
| dark: mirror, peer aligned, overlapping; v3; slow panel; sensor 1.5-3x slower; noise; reads 2x slower; everything worse at once | **144** each, 7.5 s (6.0 skew-free), 16 ms | **144** each, 11.1 s (9.6), 32 ms | 144 each, 15.3 s, 32-50 ms |
| dark, reads 4x slower (one every 2 ms) | 144, 7.5 s | 144, 11.1 s, 32-50 ms | 144, 15.3 s, 32-40 ms |
| lit 47%, reads 4x slower (`lit47slow`) | 144, 5.5 s, 8 ms | **126**, 10.3 s, 16-250 ms | 121, 13.7 s |
| lit 47%, 5% of a badge's own light on its own sensor (`self5`) | 144, 5.5 s | 144, 7.3 s | -- (not modelled) |
| dark, work 3x the assumed cost; fit stall 300 ms | 144 each, 7.5 s | 144 each, 11.1 / 11.2 s | 144 each, 15.4 / 19.0 s |
| dark, crystals ±60 ppm | 144, 7.5 s | 144, 11.1 s | -- (not modelled) |
| lit (`room_light` 48): no flicker, 30%, 47% at 120 Hz | **144** each, 5.5 s (4.0), 8 ms | **144** each, 7.3 s (5.8), 16 ms | 144 each, 10.6 s, 16 ms |
| lit, LED shape 60 / 50 Hz, mains 0.02 Hz off | 144 / 144, 5.5 s, 8 ms | 144 / 144, 7.3 s, 16-20 ms | 144 / 144, 10.6 s |
| lit, once a cycle at 60 Hz, 0.02 Hz off | 144, 5.5 s, 8-10 ms | **138**, 7.3 s, 16-32 ms | 142, 10.6 s |
| 47% at three times the light (150 counts) | **144**, 5.5 s, 8 ms | **144**, 7.5 s, 16 ms | 144, 10.6 s |
| mixed pairs, either way round: dark, lit, lit 47%, LED 60 Hz, reads 4x slower, fit stall 300 ms, three times the light, 5% self-coupling | -- | 144 each, 10.9-11.2 s dark, 7.2-7.4 s lit | -- |

"HEAD" is the code as of 2026-10-06 (`ef7ce12`) run in its own simulator
over the same 8 seeds, scenarios and payloads, its levels patched to the
dim low and 0.82 middle as shipped: it models no crystal error and no
ring noise, and charges no collection, nor a staircase replay. **Every row
is at least HEAD's but one**: POLLED under a once-a-cycle 60 Hz light,
138 against 142. Its six losses are the 63-byte payload on seeds 6 and 7:
framing failures in the data phase, each answered with a Data Sync Error
both badges act on, until `ERR_DECODE` -- not F-B1. The row moves with the
staircase replay's charge, which is unmeasured (M7): 144 with none, 140 at
0.1 or 0.4 ms a sample, 138 at the 0.25 charged -- not monotonic, so seed
sensitivity rather than a cliff, but a loss all the same. F-B1 costs no
run in these sweeps; in round 2 it cost two of the dark POLLED slow-read
row (2 of 576 POLLED runs where HEAD showed none), which the 2-reading
parts (`POLL_PART_READINGS`) took back. It stays the first open item.

The first round of this change delivered less -- POLLED 132 at 47%
flicker, 122 at three times the light, 136 with slow reads, 127 for a
POLLED Leader -- and blamed F-B1 for it, against a stale baseline. Those
losses were a flicker rate learnt from too little, POLLED windows decided
from single readings, a phase-lock gap rule that refused every rise at slow
reads and a closing-gap line too near flickering black; each is fixed
above. Sweep outputs: `sweep_r2.out`, `baseline_head.out`.

**Not verified on hardware.** Every figure above is the simulator's. Each
assumption below has its Dev Mode measurement (see "Dev Mode" in
[share_wifi_spec.md](share_wifi_spec.md)):

| Assumption | Value assumed | Measured by |
|---|---|---|
| the ring works on this firmware: `rp2`, `uctypes`, `rp2.DMA`, the ring fields of `pack_ctrl`, `high_pri`, a heap in PSRAM | yes | M1, probe `firmware`; M3, probe `ring` |
| the ring keeps 50 kHz with no lost readings, quiet and presenting | yes | M3 `rate_hz`, `fifo_over`, `faults` |
| a kernel's cost over the uncached alias | ≤ 1 µs a reading | M3 `kernel_us_per_1000` |
| a decided slot's work, a pass's | 1.2 ms, 0.15 ms | M3 `slot_work`, `work`, `lag_max_us` |
| a collection's stall, and the ring surviving it | 20-46 ms, no loss | M3 `gc_ms`, `gc_overran` |
| garbage a slot leaves | small | M3 `garbage_per_slot` |
| the sensor's settle from the dim low, and a rise to the middle against one to high | ≤ 2.5 ms lit, ≤ 10 dark; guard 1 / 2 ms | M4 `backlight_step_ring` |
| the edge lag, lit and dark | 100 µs, 300 µs | M4 `tlow_ms`, M5 `lag_us` |
| the backlight call's lateness | well under the guard | M4, M5 `tx_late_us` |
| the EXACT path end to end on the badge, and which rungs decode | 8 lit, 16 dark; 6 never | M5 `loopback_ring` |
| flicker left in an 8 ms rung's window after the fit | small | M6 `window_residual` |
| the fit's stall, on the Follower's 50 samples | 150 ms | M7 `flicker_filter` |
| a POLLED Follower's staircase replay | 0.25 ms a sample, ~160 ms | M7 `flicker_filter.replay` |
| a badge's own screen on its own sensor, off the peer's glass | none (5% in the `self5` row) | the 2nd-badge tool |
| HOME mid-transfer stops the ring and resets | yes | M8, by hand |
| a peer badge's swing and speed | the mirror's | the 2nd-badge tool |

**Open issues:**

- **F-B1: a Follower in `STEP_VERDICT` cannot match the Leader's Data Sync
  Error.** Its tail is shorter than the pattern the Follower looks for, so a
  demotion the *Leader* starts leaves the Follower a rung faster. Not fixed
  in this change. It cost 2 of 576 POLLED runs in round 2's sweep (144 with
  it fixed in simulation) and none in the final one, and it is what turns a
  rung too fast for a slow sensor into a split rather than a demotion. Fix
  it next.
- **POLLED under a once-a-cycle light** delivers 138 against HEAD's 142,
  and POLLED reading every 2 ms in 47% flicker 126 (HEAD 121): the POLLED
  tier's weakest rows, both sensitive to the unmeasured replay stall.
- **The slow-sensor cliff moved.** Starting on 8/16 ms rather than 16/32
  leaves less settle margin: a sensor three times slower than measured
  (`k3.0`) delivers 144, though I2 flags the grids apart in some runs; four
  times did not in round 1's experiments, where the old 32 ms start
  survived four. M4 says how much margin there is.
---

## Light levels and symbols

Three levels. Only two carry data. Two **schemes** produce them, listed in
`LEVEL_SCHEMES`, which maps each scheme's three symbols to the pixels and the
`display.backlight()` value that produce them. Beacons use `SCHEME_FIELDS`;
calibration and the data phase use `SCHEME_BACKLIGHT`, always -- see "Why
the fields staircase went".

| Symbol | Level | `fields` (the default) | `backlight` | Meaning |
|---|---|---|---|---|
| `-` | high | white, rgb(255, 255, 255) at `1.0` | white, rgb(255, 255, 255) at `1.0` | binary 1 |
| `_` | middle | rgb(173, 173, 173) at `1.0` | white, rgb(255, 255, 255) at `0.82` | binary 0 |
| ` ` | low | black, rgb(0, 0, 0) at `1.0` | white, rgb(255, 255, 255) at `0.5` | break, never payload |

Each cell is the field's pixels and the `display.backlight()` value set with
them. A receiver needs to know neither: calibration measures whatever arrives.

- **Black and white are the panel's full range, not the palette's.** On the
  Tufty `color.black` and `color.white` are tinted -- rgb(20, 30, 40) and
  rgb(222, 238, 214) **[source-v2]** -- and every field drew with them until
  2026-10-05, so every swing measured before then was between the tints.
  Full white was predicted to add about a fifth to the swing **[inferred-all]**;
  the first run with it gained nothing in the dark and one ADC step in the
  light **[measured-all]** -- see "What the measurement found".

- Black never appears inside payload data.

### The fields scheme

The original, and the default. The pixels carry the level and the backlight is
held at full, so the peer gets the widest swing the hardware has.

- The middle field is **named, not taken from the palette**, and the reason is
  the acceptance rule under "Reading the levels": a receiver rejects a staircase
  unless the middle level sits at least a quarter of the swing clear of both
  ends. `color.grey` is rgb(78, 74, 78) — a dark UI grey, 7% of the way up the
  swing at gamma 2.2 and 23% at gamma 1.0 — so a transmitter using it fails
  that check at every gamma and every transfer ends `ERR_CONTRAST`: at 23% it
  is measured where it is and fails the quarter rule, and at 7% it is too near
  black to count as a held middle level at all. rgb(151,
  162, 146) is 68% of white per channel, which is 42% of the swing at gamma 2.2
  and 64% at gamma 1.0.

> **[measured-all]** The panel's gamma came out at 1.95 — 2.32 in a dark room,
> 1.58 in a lit one, the difference being ambient compressing the ramp. The
> field was chosen to hold across gamma 1.0–2.5 rather than optimised for one
> point, and that paid off: on hardware the middle level sits at **0.400 of the
> swing**, inside the window calibration demands, with an ideal fraction of
> 0.701 against the 0.68 in use. See "What the measurement found".

### The backlight scheme

The backlight carries the level, and **the pixels are white at every
level**, low included.

- **Low is the backlight dim, on white pixels.** It was black pixels with
  the backlight off until 2026-10-05, then white with it off, and since
  2026-10-06 white at 0.5, dim -- see "Low is dim, not off". The pixels are
  white at every level, so **the scheme never changes a pixel**. Every symbol is then a `display.backlight()`
  call alone: a PWM duty, live within one ~263 µs PWM period **[source-v2]**,
  with no present delay and no liquid-crystal settle. `backlight(0)` is fully
  off on v3 as well, which skips its duty floor **[source-v3]**. The
  simulator does not model edge bleed, and still delays a backlight change as
  it does a pixel one, so it cannot show this scheme beating the other.

- **No light below about 11% duty.** v3's source says the backlight driver
  does not light below roughly 11% duty, varying between boards
  **[source-v3]**; on v2, whose duty is plain `v^2.8`, that is about 0.45,
  which is where mirror runs found the backlight go dark **[measured-v2]**.
  Just above it the backlight gives a little light: 0.5, the low level, is
  14% duty, under one ADC step on a mirror.

- **The middle level is 0.74**, for the same acceptance rule. `ST7789::set_backlight()`
  maps the value through gamma 2.8 onto a PWM duty with a 12.5% floor (8192 of
  65535) on v3, so **[source-v3]** the duties below are v3's. The badge's v2
  has no floor -- duty is plain `v^2.8`, so its 0.74 is about 43%, and 0.25
  and 0.33 are 2% and 4.5% **[source-v2]**:

  | `backlight()` | PWM duty | simulated delivery, 4 seeds x 6 scenarios |
  |---|---|---|
  | 0.25 | 14% | 0 of 24 -- `ERR_CONTRAST` every time |
  | 0.33 | 16% | 0 of 24 -- `ERR_CONTRAST` every time |
  | 0.62-0.80 | 35-59% | 24 of 24, and 24 of 24 at a swing of 1600 |
  | **0.74** | **50%** | **24 of 24**, dead centre of the window |
  | 0.93 | 84% | 0 of 24 -- too close to high |

  Light is taken as proportional to duty, which is **[inferred-all]**. Grey pixels
  at a reduced backlight compound the two -- the middle grey at 0.74 lands
  at 20% of the swing and fails -- which is why the middle band is white.

> **The backlight scheme can fail on hardware without the simulator
> noticing.** 50% duty is an average: the LED is switched at `sys_clk / 65536`,
> about 3.8kHz, so the light is really full-on or off from one instant to the
> next. The sensor is a phototransistor and `light_level()` is a single ADC
> conversion, so it could have read the instant rather than the average. It
> does not -- its response takes 0.2-7 ms (`backlight_step`), against a 263 µs
> PWM period -- it averages at every duty from 0.25
> to 1.0 **[measured-v2]**. On a sensor that did, every middle-level read
> lands on full or off at random and **no partial backlight value works** --
> the simulator's `--flicker` run delivers 0 of 24 at every value tried, where
> the fields scheme, which never sets a partial backlight, is unaffected.
> That is why this scheme is only ever *offered*: calibration measures it
> against the fields scheme and a flickering middle step loses. To check by
> hand, hold one badge's sensor against another showing white at
> `backlight(0.74)` and look at the spread of `light_level()`: a spread like the
> noise floor means averaging; readings split between black and white mean
> flicker.

> That single property is what makes every control pattern unforgeable: any pattern
> containing a space cannot be produced by payload bits, so no byte stuffing or
> escaping is needed anywhere.

---

## Screen as a transmitter

**The protocol transmits on the whole screen, at `LORES`.** One solid colour,
nothing else drawn, for the whole of a transfer.

- The transmitted field is the **whole screen**, filled with the symbol's
  colour, in `LORES | VSYNC`. Each symbol's backlight is set with its field,
  in the scheme the transfer is in, per "Light levels and symbols".
- **Nothing else is drawn** while a transfer runs: no text, no progress, no
  spinner. Both screens face away from their users, so the caller reports
  progress some other way (`share_wifi` uses the case lights).
- **The field is drawn only when it changes.** `run()`'s own clear is switched
  off (`badge.default_clear = None`), so the framebuffer keeps the last field
  and a frame that changes nothing draws nothing. The first frame after a
  mode change always draws.
- Leaving a transfer restores `HIRES`, `run()`'s clear, and the backlight.

> **Why: a frame has to fit in one refresh, and on v2 firmware drawing was
> what did not fit.** Measured on the badge (v2, 60Hz panel): a HIRES
> full-screen fill costs 11.4 ms and `run()` does one before `update()` is
> called, writing the frame costs 7.8 ms, and a refresh is 16.7 ms. The screen
> this replaced -- black, two bands, a column of text, every frame -- took
> about 33 ms of drawing and so **three refreshes, 50 ms a frame**. That puts
> the rung floor (2.5 x frame) at 125 ms, and a pair of badges whose frame
> times put them on different rungs could not talk at all, which calibration
> now prevents: see "Agreeing a rung".
> At `LORES` a fill touches a quarter of the pixels, and drawing only on a
> change leaves most frames with nothing to draw but the protocol's own work.
> See "What the hardware runs at" in [badgeware-hardware.md](badgeware-hardware.md).
> **[measured-v2]** on 2026-10-04: a `LORES` fill is 3.02 ms, a quarter of the
> HIRES one, and a mirror run's frames averaged 16.8 ms -- one refresh, where
> the band screen took three.

> **Why the bands went, and what they were for.** The protocol used to
> transmit on two bands, `screen.width // 4` each, hard to the left and right
> edges, with the middle half the caller's for text. Two because the sensor is
> not central: it sits about 1mm outside the **right** edge of the active
> area at mid height and reads a patch near itself, and two badges facing
> each other are mirrored -- held both upright, a right-edge sensor looks at
> the peer's *left* edge; turn one end over end and it looks at the peer's
> right. One band would have been a bet on the hold. The whole screen settles
> that bet too: whichever edge the peer's sensor is near, it is over the
> field. What is lost is the text, which only ever showed while both screens
> faced away from the people who could read it.

> **The sensor is outside the screen, not on it.** Held exactly flush, it looks
> at the peer's bezel about a millimetre past the active area, not at the glass.
> It will still couple -- the acceptance cone is wide and the standoff is a few
> millimetres -- but a small lateral offset, putting the sensor over the peer's
> field rather than beside it, is the difference between a comfortable link and
> a marginal one. Filling to the edges, with no margin, is what makes that
> offset small.

- Under the backlight scheme the backlight lights the whole panel, so with the
  whole screen white the backlight alone carries the level.

---

## Slots and sampling

- After calibration, time is divided into slots of the current rung's duration,
  counted in µs from the transfer's start (see "Reading").
- The protocol is strictly half duplex: exactly one badge transmits at a time and the
  other holds solid black.

> Holding black rather than interleaving doubles the payload rate, and gives the
> transmitter a continuous black reference to re-check `L_black` against.

- The transmitting badge changes its backlight one pass of its loop after each slot
  boundary -- the loop waits for the boundary, sets the field, and only then works.
- Each symbol is indexed by **slot number**, never by how many times the transmit
  function has been called.

> That defect once made the app unable to complete a single transfer: called once a
> frame with a slot several frames long, advancing per call fired an entire pattern
> off inside one slot, and the codec tests could not see it because they never ran
> the frame loop.

- **A queued message's first slot is fixed when it is first shown**: the slot after
  the one after the slot whose decision queued it -- one silent slot, as a reply has
  always had -- or the next boundary, if this badge was held up past that. A message
  queued by no decision starts on the next boundary. See "Reading".
- The listening badge decides slot k once its readings are in, from the median of the
  parts of a **window** in it, each flicker-corrected:

  | Reader | Window opens | Window closes |
  |---|---|---|
  | EXACT | `max(settle, OPEN_PERCENT of the slot)` | `slot - max(guard, BACKLIGHT_GUARD_PERCENT of it)` |
  | POLLED | half way | the read loop's guard: `END_GUARD_PERCENT` of `BACKLIGHT_TX_MS`, at least `BACKLIGHT_GUARD_PERCENT` |

  `settle` and `guard` are `SETTLE_LIT_US`/`GUARD_LIT_US` or the `_DARK_` pair, by this
  badge's own light. A window too short for this rung -- a misread flag, a demotion
  still to come -- keeps a quarter of the slot, settled or not: a badge that cannot
  decide cannot ask to slow down either.

> Not the middle: every delay lands at the start of a slot. Not the very end either:
> the receiver's grid is set from one edge per message, so the end of a slot can
> already hold the next symbol. With exact timing on both ends the window can open
> as soon as the sensor has settled, rather than half way -- see "Reading".

- A slot with no reading in its window is decided from the whole slot's mean, and
  failing that repeats the last level. A slot is never skipped.

> Dropping a symbol shifts every symbol after it. One wrong bit is caught by the CRC;
> a shifted stream is not.

- Slot timing is the reader's clock, never `badge.ticks`, which only moves when the
  buttons are polled.
- A badge that reads black for more than `LOST_PEER_SLOTS` consecutive slots in
  the data phase exits with `ERR_LOST_PEER`, except in `STEP_FINISH` -- see
  "Giving up".

---

## Changing rate safely

Three rules. Each was learned by getting it wrong.

- A rate change alters slot **duration** and never the shared epoch.

> Both badges apply the same change to the same epoch, so their slot grids stay
> identical. Re-basing the clock on each badge's own idea of "now" — the obvious thing
> to write — leaves them a slot apart and nothing decodes again.

- A rate change takes effect only **after** the pattern announcing it has been
  transmitted.

> Changing first sends the announcement at a rate the peer is not listening at, so it
> never arrives and the badges end up on different rungs.

- Every block is preceded by `RESYNC_LEAD_IN` slots of black.

> The two badges do not switch rate on precisely the same slot. Without the lead-in a
> resent Begin Block Pattern goes out while the peer is still sampling at the old rate,
> so it never sees the block start and waits until `ERR_LOST_PEER`. Black is ignored
> while waiting for a block, so this costs four slots.

---

## Control patterns

One slot per symbol. Nine symbols each, except Complete (six) and Complete Ack
(eight).

| Pattern | Symbols | Bits | Sent by |
|---|---|---|---|
| Begin Block | `-_ -_ -_ ` | `10 10 10` | Leader, before each block |
| End Block | `_- _- _- ` | `01 01 01` | Leader, after each block |
| CRC Good | `-- -- -- ` | `11 11 11` | Leader, block accepted |
| CRC Bad | `__ __ __ ` | `00 00 00` | Leader, block rejected |
| Data Sync Error | `  _  _  _` | `  0  0  0` | Either, "step one rung slower" |
| Complete | `- - - ` | `1 1 1` | Leader, transfer finished |
| Complete Ack | `__- __- ` | `001 001` | Follower, Complete received |

- Every pattern contains black, so none can be produced by payload data.

---

## Roles

- The badge sending the payload is the **Leader**; the one receiving it is the
  **Follower**.
- The Leader owns the slot clock, transmits every data block, and judges every CRC.
- The Follower aligns to the Leader and transmits only acknowledgements.
- Either badge may initiate a rate change by sending the Data Sync Error Pattern.

> Roles are fixed by the caller, before any light is exchanged, so the badges
> never negotiate for them. That is what makes phase alignment converge: in the
> ordinary exchange only one side moves. The Follower re-bases onto every block;
> the Leader is allowed to re-base onto a reply but rarely gets the 1.5 slots of
> silence it needs -- see "The Leader's grid is the reference".

---

## Announce

- Displayed by the Leader from the moment the transfer starts.
- Alternating black and high, changing every `ANNOUNCE_HALF_MS`, in the fields scheme.
  It was middle and high until both readers averaged many readings a sample: the
  measured middle sits 0.4 of the way up, so the beacon's swing, 0.6 of the panel's,
  read 44-48 counts on the mirror -- at or under `MIN_SWING`.
- It is a beacon, not a state machine step: the Leader keeps displaying it until it
  detects the Peer Present Pattern.
- Nothing seen within `ANNOUNCE_TIMEOUT_MS` exits with `ERR_NO_PEER`.
- That timeout is measured from `transfer.started`, and a beacon's phase from
  `transfer.phase_started`. Both are 0 when a `Transfer` is built, which is
  when the reader's clock starts.

## Peer present

- The Follower holds solid black until it has positively detected the Announce
  Pattern.

> Its absence means nothing until its presence has been seen. Without that rule the
> Follower advances on the silence of a badge that was never there.

- It then beacons alternating black and high every `PRESENT_HALF_MS` until the
  Announce Pattern stops: no crossing of the window's midpoint for more than
  `ANNOUNCE_HALF_MS + 30` ms. It then starts measuring the staircase.
- A Follower drops the coarse sample it is pooling once its fine samples have
  been empty -- no readings in them -- for more than `HANDSHAKE_GAP_MS` (7),
  unless the sample in progress already follows a drop. Only a POLLED reader,
  blinded by its own present, ever has such a gap; an EXACT one only if its
  ring overran.

> Its own presents stop its readings for 8-25 ms, and the coarse sample one
> interrupts then closes on a few ms of readings, which average little of
> 120 Hz flicker away. At 47% flicker on three times the lit room's light,
> ±70 counts against an 80-count swing, one such sample a beacon half read
> 45 counts high, lifted the window's `(min + max) / 2` over the Leader's
> rising edge, and the missed crossing read as quiet: the Follower began
> reading a staircase the Leader had not started, in 19 of 192 runs, and
> 30 of 144 transfers failed -- 24 refusing that staircase, 6 with the
> Leader never seeing the Follower's beacon out (`ERR_NO_PEER`). No fit can
> help here: before the staircase the Leader is never dark. "Never twice
> running" is what keeps a badge whose every call outlasts the threshold
> closing coarse samples; without it a simulated badge with 10 ms calls
> never left the handshake. Only the Follower's handshake drops: the
> Leader's detector sees the Follower's black-to-high beacon, twice the
> swing, and dropping in calibration as well cost the pixels scheme 6 of
> 144 in a lit room. Receiver-side, so it pairs with a badge without it.
- Its only job is to tell the Leader that someone is pointed at it, so the Leader can
  stop announcing and start calibrating.
- The Follower also exits with `ERR_NO_PEER` after `ANNOUNCE_TIMEOUT_MS`.

## Pre-calibration detection

Before calibration a badge has no thresholds, so the two beacons cannot be classified
by level.

- Keep a sliding one-second window of timestamped coarse samples.
- A beacon is present when `max - min` across the window is at least
  `MIN_SWING`, the window holds at least 8 samples, and they cross the
  midpoint between `min` and `max` at least twice. The half period is the mean
  interval between those crossings.
- The two beacons are told apart by **period alone**: a measured half period within
  `BEACON_TOLERANCE` of `ANNOUNCE_HALF_MS` or `PRESENT_HALF_MS` identifies which.

> Telling them apart by brightness would need thresholds, which is precisely what does
> not exist yet. Period is measurable from timestamps alone and survives any ambient
> level.

---

## Calibration

Every threshold in the protocol comes from here, and so does the shared time reference
the slot clock starts from. **One calibration, for every pair**: one staircase each
way, in the backlight scheme.

- Begins when the Leader detects the Peer Present Pattern.
- The **staircase** is: an opening gap of black, then `CAL_REPS` repetitions of
  `PLATEAU_MS` each of low, middle and high -- `CAL_REPS_LIT` when the light flag
  says LIT -- then `CAL_CLOSE_MS` black. The Leader's opening gap is
  `CAL_OPEN_LEADER_MS`, the Follower's `CAL_OPEN_MS`: 1010 and 860 ms DARK, 890 and
  740 LIT. Read in coarse samples of `CAL_SAMPLE_MS`.
- The Leader transmits its staircase while the Follower measures it. The two then
  swap, so the Leader learns its own levels from the Follower's screen.

> Both directions are required: the Leader has to read acknowledgements and sync
> errors, so it needs thresholds of its own.

- The staircase is self-identifying without thresholds: it is the only signal that
  steps monotonically through three levels, bracketed by longer black.
- The reader takes each sample as the median of itself and its neighbours, one
  sample late and timed by the middle one, and a single sample over the closing
  gap's line does not end the gap: one outlier otherwise moved `min()` below the
  darkest level, or broke the run, and the gap was never found (POLLED badges
  under stress, in simulation). Its levels come from the staircase's body --
  `LEAD_KEEP` (12) dark samples before its first rise, to its closing gap -- since
  the long gaps would put the held high under the 0.90 quantile.
- The closing gap is samples within **a third** of the swing of its darkest,
  held `CAL_CLOSE_MS - 60`. It was a quarter: at three times the lit room's
  light, flickering 47%, the corrected dark sat at the line, its noise broke
  every run, and a POLLED Follower read on for two seconds and failed
  `ERR_CONTRAST`. A middle step is 0.4 of the swing and 40 ms long, so it
  cannot hold a run.
- A reader whose staircase does not separate starts afresh once
  (`CAL_RESTARTS`) before `ERR_CONTRAST`: what ended may have been the end of
  the peer's beacon, taken for quiet, and its opening gap, with the real
  staircase still to come.

### Why the fields staircase went

There were two staircases from a Leader that offered the backlight: fields, then
backlight, the Follower keeping whichever separated the levels better. A badge
reading continuously (2026-10-05 to 10-07) already took the backlight whenever it calibrated; fields only
won on a sensor that follows the backlight's PWM, which this one does not
[measured-all]; and its 80 ms floor made a transfer five to ten times slower. So the
fields scheme now draws the beacons only, and calibration and data are the
backlight's. A pair whose backlight staircase fails ends `ERR_CONTRAST`.

### The flags

**The order of a staircase's steps carries the reader tier**: `REVERSED` -- low,
high, middle -- for EXACT, `ASCENDING` for POLLED. **Its repetitions carry the
light**: `CAL_REPS_LIT` for LIT, `CAL_REPS` otherwise. `staircase_steps()` reads
both back out of the same samples: middle-to-high steps against high-to-middle ones
for the order, ignoring any level held for fewer than `STEP_SAMPLES` samples; the
high steps for the light, `LIT_STEPS_MAX` or fewer being LIT.

- **The Leader's staircase carries its own** tier and light. It judges its light
  from the black of the Follower's beacon.
- **The Follower decides the pair**: it reads the Leader's flags, judges its own
  light from the black of the Leader's staircase, and sends `pair_exact = own and
  peer`, `pair_lit = own and peer` in its own staircase.
- **The Leader adopts what it reads**, without combining it with its own.

> Only one side moves. A Follower that misreads the Leader -- a missed high step
> reads DARK as LIT -- moves the pair as a whole: to a slower rung, or to a faster
> one that is a gamble, where CRC failures demote. Only a Leader that misreads the
> Follower can split the rungs. When each badge combined both flags itself, either
> misread split them.

### Agreeing a rung

Both badges start the data phase on the rung `start_index()` gives from the pair's
flags -- the Follower's, which the Leader adopted -- so they agree with no message:

| Pair | Room | Start | Why |
|---|---|---|---|
| EXACT | LIT | **8 ms** (`EXACT_LIT_INDEX`) | lit settle 1.0-1.5 ms [measured-all], budgeted 2.5; the rule allows 6, not on the ladder until M5 says so |
| EXACT | DARK | **16 ms** (`EXACT_INDEX`) | 7.75 ms worst from off [measured-all], budgeted 10; 10 ms if M4 puts the dim low's 90% under 4 |
| POLLED | LIT | 16 ms (`BACKLIGHT_LIT_INDEX`) | the read loop's rung, unchanged |
| POLLED | DARK | 32 ms (`BACKLIGHT_INDEX`) | the same |

plus `OPTIMISTIC_RUNGS` (0). A rung `r` is one an EXACT pair can start on when its
window -- `max(settle, 0.4 r)` to `r - max(guard, 0.15 r)` -- is at least
`WINDOW_MIN_US` and `r >= SLOT_MIN_MS`: settle + window + guard, where the read loop
needed three settle times.

> **Why the frame-rate classes went.** Each badge used to carry FAST or SLOW --
> whether its frames could sample the fastest rung -- in its staircase's
> repetitions, and both started on the slower class's rung: a 90 Hz badge paired
> with a 30 Hz one had delivered 35 and 0 of 144. A badge reading continuously was
> always FAST, its frames bounding nothing, and now every badge does: the class,
> `CAL_REPS_SLOW`, `FRAME_SAMPLES` and the 100 ms SLOW rung are gone. v2 at 60 Hz and
> v3 at 90 still differ, but only in the beacons' presents, which period detection
> with ±20% absorbs.

### Reading the levels

- The measuring badge does **not** align to plateau boundaries. It keeps every sample
  and reads the levels out of their distribution:

      L_black = 10th percentile of all samples
      L_white = 90th percentile of all samples
      span    = L_white - L_black
      L_grey  = median of samples between L_black + span/8 and L_black + 7*span/8
                (bounds inclusive; the upper median of an even count)

> Percentiles rather than equal thirds, because how much leading black a badge captures
> depends on when it started listening. Isolating the middle cluster by value rather
> than by count makes the estimate independent of that. Simulated against late starts
> of up to 1.5 s, bright ambient, heavy noise and 33 ms frames, this recovers the
> levels in every case and correctly rejects insufficient contrast.

- The result is rejected, exiting `ERR_CONTRAST`, unless all hold:

      at least 32 samples in all
      L_white - L_black >= MIN_SWING
      at least 4 samples between L_black + span/8 and L_black + 7*span/8
      held * 2 >= above
          held:  samples in that band within span/8 of L_grey
          above: samples above L_black + 7*span/8
      L_grey  - L_black >= span / 4
      L_white - L_grey  >= span / 4
      every level has MIN_HELD_SAMPLES settled samples
      each gap >= MIN_GAP, and >= GAP_SIGMAS x the noisier side's noise

> The band is wider than the quarter rule on purpose. Taken from the middle
> half, as it once was, the rule could never fail: whatever landed there had
> its median inside the middle half by construction, so a middle level near
> either end was accepted on the transitions passing through. An eighth so
> that black and white stay out of the band and a middle level anywhere in it
> is counted whole. One between an
> eighth and a quarter from an end is then measured where it is and fails the
> quarter rule. Nearer an end than an eighth -- `color.grey` at 7% -- the step
> itself falls outside the band, and what is left is transitions, whose median
> can sit anywhere; the held check catches that, because a middle step is held
> as long as the high one and a transition lasts a frame or two.
>
> Simulated, 384 conditions per middle level (noise 0-700, settle 0-24 ms,
> frames 8-33 ms, starts 0-1500 ms late), the share of staircases accepted:
> middle at 7% and 12%, none; 20%, 12; 30%, 45% and 50%, all 384; 70%, 380;
> 80%, 8; 88% and 93%, none. Before the wider band and the held check the
> same sweep accepted 2 at 7%, 113 at 20%, 138 at 80% and 59 at 93% -- middle
> levels the quarter rule exists to refuse.

- A staircase that never completes within three SLOW staircase durations also
  exits `ERR_CONTRAST`.
- `CAL_REPS` is fixed for a class, never adaptive to how well the peer is
  reading. The count depends only on the sender's own frame time, which the
  sender knows.

> An adaptive count is not implementable. The badge that knows whether the samples were
> good is the one measuring; the badge that decides how many repetitions to display is
> the one transmitting; and before calibration completes there is no channel between
> them to carry that verdict.

### Separability

**Whether three calibrated levels can be told apart is measured on the link,
not assumed.** After the levels are read, `held_spreads()` takes each level's
noise -- the standard deviation of the staircase samples in its class, between
the thresholds -- and `gap_separable()` checks each of the two gaps against the
noisier level beside it:

      gap >= MIN_GAP                                   (two ADC steps)
      gap >= GAP_SIGMAS x max(noise either side, QUANTISATION_SD)

- **Only settled samples count.** A sample counts toward its level's noise once
  the reading has stayed in that level's class for `STEP_SAMPLES` readings.
  Transitions passing through a class, and a panel still settling after one,
  would otherwise count as noise: at a 24 ms settle and 8 ms frames they
  doubled the measured spread and refused good links. A level with fewer than
  `MIN_HELD_SAMPLES` settled samples is refused.
- **`QUANTISATION_SD` floors the noise.** Readings that all land on one ADC
  code have a spread of zero but still carry up to half a step of error.
- **Both badges check their own calibration**, as before; a failure is
  `ERR_CONTRAST`.

> **Why four standard deviations.** A threshold half way across a gap is then
> two from each level. Chosen by simulation with the sensor modelled as
> measured -- 16-count steps, Gaussian noise of standard deviation 4, 6 or 8,
> swings of 80, 95 and 210 -- accepting every link and recording each
> calibration's worst gap over noise against how the transfer ended. Under
> measured timing the ratio barely separates delivered from failed runs,
> because there the failures are timing, not contrast; with ideal timing,
> where contrast is all that is left, runs below about four fail and those
> above deliver. Over all 1296 runs a threshold of 4 refused 57 links, 45 of
> which would have delivered; 5 refused 256 (164); 6, 499 (357).
>
> 8 seeds x 6 scenarios, the typical payload, fields scheme; delivered /
> refused / accepted but failed, out of 48 per cell, for sensor noise of
> standard deviation 4, 6 and 8:
>
> | timing | swing 80 | swing 95 | swing 210 | of 432 |
> |---|---|---|---|---|
> | v2, measured | 20/0/28, 34/0/14, 18/18/12 | 44/0/4, 44/0/4, 38/6/4 | 42/0/6, 42/0/6, 32/0/16 | **314** delivered, 24 refused |
> | v3, modelled **[inferred-v3]** | 26/0/22, 26/0/22, 12/20/16 | 18/0/30, 12/0/36, 24/2/22 | 8/0/40, 22/0/26, 22/0/26 | **170**, 22 |
> | ideal | 44/0/4, 43/1/4, 17/10/21 | 48/0/0, 48/0/0, 46/2/0 | 48/0/0, 48/0/0, 48/0/0 | **390**, 13 |
>
> The old fixed floor of 192 delivered **none** of these 1296: every one
> stopped at beacon detection or calibration. What still fails under real
> timing is the present delay and the panel's settle (see "What the
> measurement found"), not the levels. Whether 4 refuses the right links on
> hardware is **[unverified]**: the 2nd-badge `eye` test measures error rates
> directly.

### Thresholds

      T_low  = (L_black + L_grey) / 2
      T_high = (L_grey + L_white) / 2

      v <  T_low            -> break (black)
      T_low <= v < T_high   -> binary 0 (middle)
      v >= T_high           -> binary 1 (white)

- A hysteresis band of 5% of `span` sits around both thresholds. A sample inside a band
  keeps the previously decided level.

### The shared epoch

- A staircase ends with a sustained return to black. The leading edge of that gap is
  the shared time reference.
- "Sustained" is concrete. The reader keeps a rolling buffer of the last 512
  samples and looks for the end only once it holds at least 24 and their
  `max - min` is at least `MIN_SWING`. The staircase has ended when samples
  at or below `min + (max - min) // 4`, with `min` and `max` taken over the
  buffer, have held for `CAL_GAP_MS - 60` ms; the gap's leading edge is the
  first of them.
- The transmitting badge knows that instant exactly; the measuring badge detects it.
- The slot clock starts one `CAL_GAP_MS` after the closing gap of the **Follower's**
  staircase begins -- the last one sent, whether the Leader sent one or two -- which
  both badges compute independently.

### Phase locking

The epoch calibration produces is an estimate on one side and exact on the other, so
nobody goes on trusting it. **A receiver re-bases its slot grid onto the peer's real
first edge out of black, at the start of every message.**

- Every transmission begins after a run of black and starts on the transmitter's slot
  boundary, so that edge is a shared reference that needs no agreement.
- The edge is found **after the fact, in the reader's history**: when a slot is
  decided lit while acquiring, `reader.rise()` scans back from the slot's window
  close -- no further than 2.5 slots, the end of the peer's last light, or the
  oldest reading kept -- for the first crossing of `T_low` plus the fitted flicker,
  a threshold per step of `max(3, d/32/100 µs)` 100 µs bins. EXACT: two consecutive
  100 µs bins lit confirm it; POLLED: lit 1 ms running (`POLL_CONFIRM_US`), and the
  last dark reading no more than `gap_for(d, read)` before -- an eighth of the slot,
  inside its guard, but never under three of the loop's own read intervals nor
  under 1 ms or over `POLL_GAP_US` (5 ms). A rise that came while the loop was blind
  for a present or a collection is somewhere in the gap, and is not locked on; a
  crossing interpolated across a wider gap could be 2-3 ms off. At 1 ms a loop
  reading every 2 ms refused every rise and never locked.
- The crossing is **interpolated** between the last bin below and the first above.
- The boundary is the crossing less `EDGE_LAG_LIT_US` or `EDGE_LAG_DARK_US`, by this
  badge's own light: the backlight's PWM wait and the sensor's climb to `T_low`.
- Re-basing happens only while **acquiring** — between messages, never inside one.
  The Leader acquires in `STEP_CRC` before the reply starts and in `STEP_FINISH`; the
  Follower in `STEP_BLOCK` outside a block, in `STEP_VERDICT`, and in `STEP_FINISH`.
- It requires **1.5 slots of real silence** first, so the edge is the start of a
  transmission and not a symbol inside one -- counted from the end of the peer's
  last light, or of this badge's own transmission, **to the start of the slot the
  crossing is in**, not to the crossing. A reply comes one silent slot after this
  badge stops, and a slow sensor crosses well into it: counted to the crossing, the
  Leader locked on CRC replies, late by the sensor's delay, read a CRC bit as the
  Data Sync Error, and demoted alone (simulation, a sensor three times slower than
  measured).
- A re-base **renumbers the grid**: the slot decided on the old grid is discarded,
  and slot 0 of the new one -- the symbol whose edge was just found -- is decided
  next, from the readings already there. The crossing counts as the peer's last
  light, so slot 0 cannot lock again: one re-base a message.

Re-basing moves `epoch` and never `duration`; a rate change moves `duration` and never
`epoch` (I3). The two are deliberately separate operations.

> **Residual error, once a frame.** (The figures here are from when every badge
> read once a frame; an EXACT pair's edges are placed to tens of µs -- see
> "Reading".) The transmitter could only raise a symbol on one of its own
> frames, so symbol edges carried up to a frame of quantisation that no receiver could
> remove — about ±5.5 ms at 90 fps and ±8.3 ms at 60, against a quarter-slot budget of 12.5 ms at the
> 50 ms rung. Measured over 506 acquisitions (16 seeds x 6 scenarios x 2
> payloads), lock error after correction is a median of 3 ms, 9 ms at the 95th
> percentile and 13 ms at worst -- 0.26 slots, once, on the cold panel -- from 17 to
> 18 ms before. This is the reason the rung floor is expressed in settle times and frame
> times rather than in settle times alone.

> **What this fixed, and what it cost.** Two earlier attempts were reverted, having
> broken multi-block transfers and split the two badges onto different rungs. Both
> causes turned out to be elsewhere, and both were found by asserting the invariants
> in [../context/optical-link-invariants.md](../context/optical-link-invariants.md)
> on every simulated tick rather than at the end:
>
> - **Multi-block delivered nothing** because a control pattern's *trailing* black —
>   the hardest symbol on the wire, since it follows white and is the panel's full
>   swing — was being read as grey, so the CRC Good Pattern matched nothing and the
>   Follower, which has no timeout while waiting for a verdict, waited until both
>   badges gave up. A receiver now matches these patterns **without** their trailing
>   black, which is a separator and not information. Two patterns are exceptions
>   and are matched whole. The Begin Block Pattern: its trailing black separates the
>   pattern from the payload, so matching a slot early would take it for a Field
>   Separator. And the End Block Pattern, which the Follower collects symbol by
>   symbol from its opening `_- ` to all nine, trailing black included.
> - **A resend after a rate change lost its lead-in.** A Data Sync Error is acted on
>   by its fourth symbol -- the leading black is discarded while the Leader waits
>   for a reply to begin, the first `_` is taken as the reply starting, and the black
>   after it as the reply breaking off --
>   leaving the peer transmitting the remaining five while the Leader has already
>   begun the resend. `SYNC_LEAD_IN` extra black slots cover it.
> - **The Follower could not re-base while waiting for a verdict**, so the grid it
>   locked at the start of a block had to survive the whole block — over 500 slots for
>   a full one. `STEP_VERDICT` is an acquiring state and now counts as one.


> **The Leader's grid is the reference; it does not lock onto the CRC reply.**
> It hears one silent slot before the reply -- the slot the Follower spends
> deciding -- short of the 1.5 that phase locking wants, so its grid stays
> where it was. The Complete Ack follows Complete just as closely. The rule
> itself allows the Leader to re-base in `STEP_CRC` and `STEP_FINISH`, and it
> does when a reply comes after a longer silence -- a Follower's resend request,
> sent only after `RESEND_QUIET_SLOTS` of black, is one **[inferred-all]** -- but
> the ordinary exchange never gives it the chance. That is deliberate rather
> than an omission: giving the reply a
> black lead-in, so the Leader could lock, cost 16-21 deliveries in 384
> simulated runs. With both badges re-basing onto each other every message,
> the error in each estimate adds to the last; with one reference, the
> Follower's errors do not accumulate.
>
> **The POLLED exception.** A POLLED Follower that accepts a block owes a
> collection (`collect_owed`) and makes it as it queues its next message;
> the reply gap delays the message whole, so its CRC reply or Complete Ack
> comes after one silent slot plus the collection's 20-46 ms -- 1.5 slots or
> more on 16-32 ms rungs -- and the Leader may re-base on it. Accepted:
> collected at once, it missed the Complete a slot behind the verdict. The
> re-base lands within the guard in simulation (POLLED rows 144, `k3.0`
> included). `test_reply_gap` pins the gap; an EXACT Follower collects at
> once and keeps the one slot.

### Where this still fails

Measured over 16 seeds x 6 simulator scenarios x 2 payloads: **188 of 192 runs
deliver** (ideal, datasheet, realistic, slow panel and bright room 32/32 each, cold
panel 28/32).
Residual slot-grid phase error after locking is 0-13 ms against a 50 ms slot, worst
case 0.26 slots, from 18 ms and 0.51 slots before. No run in any sweep during this work
violated I4 or I7 — no block was ever accepted twice and no Leader ever reported `OK`
over a Follower that did not have the payload; two runs broke I2, the slot grids
drifting out of phase. Every remaining failure is an honest `ERR_LOST_PEER` on both
badges.

Two named defects remain.

> **A missed Begin Block Pattern used to lose the block, and now does not.** A bad
> symbol *inside* a block trips the framing check, and the Follower has something to
> report. But a Follower that never matched Begin Block never entered the block, so
> nothing tripped and it had nothing to send. The Leader waited in `STEP_CRC` for a
> reply that was never coming, and **both badges stayed on the rung they started on**,
> because the only route to demotion was a framing failure that could not happen.
> Every failing run in the sweep was that one shape.
>
> The Follower now asks. Stalled in `STEP_BLOCK` with nothing uncommitted, it waits
> for `RESEND_QUIET_SLOTS` of black -- a badge that is transmitting does not listen,
> and two on air at once is what half duplex exists to prevent -- and then sends the
> Data Sync Error Pattern. The Leader's handling of that in `STEP_CRC` was already
> there and is unchanged: it steps down a rung and resends. Nothing was ever able to
> trigger it.
>
> It only asks holding nothing uncommitted. A Follower sitting on a block whose
> verdict never arrived cannot tell an accepted one from a rejected one, and asking
> would either drop a block the Leader has moved past or duplicate one it has not --
> corrupting the payload while every CRC still passes. Requests are bounded by
> `MAX_BLOCK_ATTEMPTS`, or a dead peer would be asked for ever.
>
> **A framing failure now takes the same route.** It used to send the Data Sync Error
> Pattern and step down the moment it tripped -- while the Leader was still part way
> through the block, transmitting and listening to nothing. The Leader never heard it,
> the Follower had demoted on its own, and the badges sat a rung apart until
> `BLOCK_TIMEOUT_MS`. Now the Follower stops decoding, waits for the same
> `RESEND_QUIET_SLOTS` of black, and sends the same request, demoting once; a stall
> that comes due while that request waits re-arms it rather than making a second. Both
> cases mean one thing -- this block did not arrive, send it again slower -- and now
> share one signal. In a simulated sweep with framing errors injected (16 seeds x 6
> scenarios x 4 injection cases, 384 runs) delivery went from 96 to **384 of 384**,
> rung splits from 380 to none and overlapping transmissions from 380 to none; the
> invariant checker still flagged I2 four times, all on the cold panel.
>
> Measured over 16 seeds x 6 scenarios x 2 payloads when it went in: **187 of 192,
> against 178 before** -- and the rate came down where it needed to, 31 of those 192
> runs finishing on the 65 ms rung where previously every run stayed at 50 ms. With
> the end-of-slot guard under "Slots and sampling" since added, fewer blocks need
> help: 188 of 192, and 18 finish on 65 ms.

> **A cold panel is not reliably supported.** At a 24 ms settle delivery is 28 of 32,
> against 32 everywhere else. `LCD_SETTLE_MS` is 16 ms, bounded on hardware at under 22 ms but not resolved
> further — see "What the measurement found"; a
> panel half again slower than that figure is outside what the ladder is built for.
> This is a constants problem, not a phase-locking one, and the honest fix is to
> measure the panel rather than to move the constant.

### What has been tried and reverted

Five changes were measured against this. **One was kept**, and it is the only one that
added a missing mechanism rather than tuning an existing one. Keeping the rest of the
list is the point: each looks obviously right, and each was measured and put back.
The figures are as measured at the time, before the end-of-slot guard, and have not
been re-run since.

| change | delivered | cost that sank it |
|---|---|---|
| *asking for a block again* | **187/192** | **kept** |
| three-edge lock averaging | 189/192 | 32 ms panel 8/8 → 0/8, three tests red |
| holding control patterns at half rate | 169/192 | overhead; 2 permanent rung splits |
| demoting on silence | 187/192 | rescues nothing; failures 6x slower |
| all three together | 80/96 | worse than any of them alone |

**Three-edge lock averaging.** The Begin Block Pattern has three rises out of black, at
slot offsets 0, 3 and 6, and only the first is used. Measured in situ over 72
acquisitions it does what it promises: lock error sd 4.80 ms → 3.01 ms, worst 13 ms →
9 ms, and the one acquisition outside the quarter-slot budget came inside it. It still
loses. A better lock makes the payload decodable at a rate whose *control patterns* are
not, so the CRC stops failing, the link stops demoting, and the transfer ends up good
enough to carry data and not good enough to finish. Retried after the resend path
existed, on the theory that it would now supply the missing demotion — it does not,
because a Follower that frames blocks successfully never stalls and never asks.

**Holding control patterns at half rate.** The asymmetry is real: payload is grey and
white only, half the panel's swing, while every control pattern carries black against
white. At 50 ms against a 32 ms panel, payload decodes without error at every phase
offset to 20 ms while the Complete Pattern is failing at 16 ms. Doubling each control
symbol takes a Begin Block match from 9% to 53% at a 20 ms phase error. It still loses,
on overhead: per-block overhead 47 → 65 slots, plus a lead-in on every control message,
because doubling makes each pattern's internal gaps two slots long and the phase-lock
reads that as "a message is starting".

**Demoting on silence.** Silence made evidence in its own right, so a badge hearing
nothing steps down instead of giving up. Tested against panels from 32 ms to 56 ms
settle, slower than anything the scenarios model, where the rate demonstrably stalls at
65 or 80 ms with headroom left. The code fires — 122 calls, able to step down in 101 of
them, 15 runs walking to the slowest rung — and rescues **nothing**: 72/96 on the slow
panels and 187/192 on the sweep, identical with it and without. The only change is that
failures take six times as long. Measured alone it also split the rungs permanently,
because both badges apply the same rule at different moments and once a rung apart
neither can hear the other. `AGENTS.md` has the principle: protocols converge when only
one side is allowed to move.

> **The lesson across all five.** Four tuning changes, none kept; one missing mechanism,
> kept, worth 92.7% → 97.4% for eight lines. The budgets here are balanced against each
> other and a single-axis improvement disturbs that balance — and the failures do not
> cancel when combined, they compound. Measure delivery end to end, across seeds, check
> the invariants, and check the *ending* and not just the payload.

**No transfer in this repository has ever run on a badge.** These are properties of a model
of the hardware — a first-order panel lag, ambient light, sensor noise, frame jitter —
and `LCD_SETTLE_MS` is bounded rather than measured. A pass here means "agrees
with our model", not "works".

---

## Rate selection

There is no negotiation phase and no verification phase. Both existed and both were
deleted.

- Both badges start on the rung their calibration staircases agreed: **8 or 16 ms**
  for an EXACT pair, lit or dark, **16 or 32** for a POLLED one. See "Agreeing a
  rung". No message of its own carries it.
- The **proven rung**, `conservative_index()`, is `BACKLIGHT_PROVEN_MS` (50 ms):
  every start is faster, so every start is a gamble, and a bad CRC there demotes.
  It decides only whether a CRC failure may demote (see "Data send mode"), never
  where a transfer starts.
- **Known defect F-B1, left as it was**: the Leader's own demotion goes out as a
  Data Sync Error in `STEP_VERDICT`, which the Follower compares against a tail one
  symbol too short and so never matches. The two then sit a rung apart. It turns
  a CRC failure into a split rather than causing one, and why a pair forced onto a
  rung too fast for its sensor splits rather than steps down. In the 2026-10-07
  sweeps it costs no run (two of 576 in round 2; see "What simulation says"); the POLLED
  47% losses once put down to it were a flicker rate learnt from too little.

> **Why the start is agreed rather than computed alone.** Starting each badge on
> its own proven rung assumed both badges were the same hardware running at the
> same frame time. Firmware differs -- **v2 runs the panel at 60 Hz** **[measured-v2]**,
> v3 at 90 -- and so does how much a badge draws; and two badges that start on
> different rungs almost never deliver, because a demotion moves both one rung and
> leaves them as far apart as before. The table under "Agreeing a rung" has the
> before and after.

- **The ladder ends on proven ground.** A transfer starts at the fastest rung that has
  been shown to decode, and there is nothing above it to gamble on
  (`OPTIMISTIC_RUNGS` is 0).

> There used to be one more rung, 35 ms, on the optimistic assumption that the panel's
> real response was nearer the datasheet's 8 ms single-transition figure than the 2x
> penalty IPS panels usually carry. A transfer started there and let the first block be
> the test.
>
> It was removed, and the reason generalises: **a gamble rung is only cheap to be wrong
> about if being wrong is unambiguous.** The demotion it depends on is announced by the
> Data Sync Error Pattern, which the peer has to read at the very rate it is being
> demoted from — and a rate too fast to carry a block is often too fast to carry the
> message saying so. When that pattern arrives corrupted the two badges end up a rung
> apart and neither can hear the other. This is the same objection that deleted the
> verification burst, below, and it applies here for the same reason.
>
> Measured over 8 seeds x 6 scenarios x 2 payloads, with the receiver phase-locking:
> starting on the proven rung delivered **93 of 96** runs, against **74 of 96** at
> 35 ms and **62 of 96** at 40 ms. A rung that half works is worse than one that
> plainly does not, because the badge limps along at it and spends its retry budget
> instead of stepping down. 40 ms scored worst of the three for exactly that reason.
>
> If the panel is ever measured and is genuinely fast, the way back is to add the rung
> to `SLOT_LADDER` and set `OPTIMISTIC_RUNGS` to 1. Do not do it on a guess, and do not
> add more than one: each is another block to lose before the rate settles.

- If a block cannot be framed or never arrives, the Follower waits for
  `RESEND_QUIET_SLOTS` of black and sends the Data Sync Error Pattern; if a block keeps
  failing CRC on a rung above the proven one, the Leader sends it. Either way both
  badges step down one rung, once, and the block is resent. On the proven rung CRC
  failures are retried, not demoted (see "Data send mode"), so while
  `OPTIMISTIC_RUNGS` is 0 the CRC route is dormant. A demotion spends none of the CRC
  retry budget, whichever badge asked for it.
- Only a resend answering the **peer's** Data Sync Error carries `SYNC_LEAD_IN` extra
  black slots in front of it, because the Leader acts on that pattern at its fourth
  symbol and the Follower is still transmitting the other five when the resend
  begins. After the Leader's own CRC demotion the pattern has been sent in full, so
  that resend has `RESYNC_LEAD_IN` alone.

> A verification burst — a test pattern sent before the payload — could not be trusted.
> A rate too fast to carry a payload still carries a short high-contrast pattern, so the
> burst passed on links that then failed and the badges disagreed about whether sync had
> succeeded. And a rate too broken to carry the burst is equally too broken to carry any
> message saying so, which left the failure path depending on signalling that could not
> work. Testing with the real payload has neither problem.

---

## Blocks and CRC

- Payload is carried in blocks of up to `BLOCK_BYTES` bytes. A payload with no bytes
  is carried as one empty block -- see "Payload".
- Block length is implicit: the End Block Pattern terminates the block, and because it
  contains black it can never occur inside payload data.
- Bits go out most significant first, one bit per slot.
- A 16-bit CRC is computed for every block: **CRC-16/CCITT-FALSE**, polynomial `0x1021`,
  initial value `0xFFFF`, no input or output reflection, no final XOR, over the block's
  payload bytes.
- If a Field Separator falls inside a block, a single `0x00` is fed into that block's
  CRC at its position. It is never transmitted.

> Without it a dropped or spurious separator passes CRC and only surfaces later, when
> the application finds the wrong number of fields — the wrong error, reported after
> the retry budget has been spent.

- CRCs are sent as 16 raw bits, outside block framing, most significant bit first.
- Only receivers send CRCs. Senders judge them.

---

## Data send mode

Entered by the Leader; the Follower is in receive mode at the same time. For each block:

- Transmit `RESYNC_LEAD_IN` black slots, the Begin Block Pattern, the payload bits, and
  the End Block Pattern.
- Switch to black and listen for 16 slots of raw CRC.
- Compare it against the CRC of the bytes just sent:
  - **Equal** — transmit CRC Good and move to the next block.
  - **Different** — count a retry and transmit CRC Bad, resending the same block.
- Black before the reply's first data symbol is discarded. **Two blacks in a row**
  after it mean the Follower sent the Data Sync Error Pattern instead of a CRC --
  `_  _  _` after its leading black: step one rung slower, reset the retry count, and
  resend the block with `SYNC_LEAD_IN` extra black slots in front, which still
  covers the four symbols the Follower has left to send. One black alone is a
  misread bit and counts as one: a slow sensor reads a middle symbol out of a fall
  as black, and taking that for the pattern demoted the Leader alone (simulation,
  a sensor three to four times slower than measured). The CRC catches it.
- On a CRC failure beyond the first `MAX_BLOCK_RETRIES` for one block:
  - if the current rung is faster than the proven one, transmit the Data Sync Error
    Pattern, step one rung slower once it has been sent, reset the retry count, and
    resend after `RESYNC_LEAD_IN` alone;
  - otherwise -- at or below the proven rung -- exit with `ERR_CRC`.
- So a demotion spends none of the CRC retry budget on either path.
- The proven rung is `conservative_index()`, recomputed from the live frame time each
  time it is asked. While `OPTIMISTIC_RUNGS` is 0 a transfer starts on it and only
  ever steps down, so this demotion is dormant: it can fire only if the frame time has
  grown enough since to put the proven rung below the current one.

> A rate can be fast enough to frame a block and still be too fast to get its bits
> right. Stepping down is only offered from ground above the proven rung; at or below
> it the rate is not the suspect, noise is, and that is what the retries are for.

- After the last block is acknowledged, transmit the Complete Pattern, then **wait to
  be acknowledged**. Repeat it once more than `COMPLETE_RETRY_SLOTS` slots have passed
  unanswered since it finished going out, up to `MAX_COMPLETE_RETRIES` times. Exit `OK` on the Complete Ack Pattern, and
  `ERR_LOST_PEER` if the budget runs out.
- The ordinary `LOST_PEER_SLOTS` timeout does not apply while waiting here: a Follower
  slow to pick Complete out of the air is legitimately silent for longer, and the retry
  budget is this state's timeout.

> **This is the one place the Leader is entitled to claim success, and it is load
> bearing.** It used to exit `OK` the moment it had *transmitted* Complete, which
> asserts something it has no evidence for: the pattern is six slots with no lead-in
> and is the message most likely to be missed. A Follower that missed it sat there
> until `ERR_LOST_PEER` while the Leader reported `OK` — a badge claiming to have
> shared a credential it had not shared. That is invariant I7, and of everything in
> this protocol it is the one that matters most.

### Giving up

Two budgets, because a link dies in two ways, and for a long time only one of
them was watched.

- **Silence.** More than `LOST_PEER_SLOTS` consecutive black slots, in any data
  sub-state, ends the transfer with `ERR_LOST_PEER`. Exempted in `STEP_FINISH` on
  both sides: the Leader waiting to be acknowledged after Complete, where the
  retry budget is the timeout instead, and the Follower lingering after its
  acknowledgement, where `LINGER_SLOTS` is. And a Follower that has just
  accepted a block allows `COMPLETE_RETRY_SLOTS + len(COMPLETE)` more
  (`idle_grace`), until it next reads light: it cannot tell the Leader's
  silence before a next block from a Complete it missed, and the Leader's
  repeat of Complete comes 18 slots after the first, past the 16 it allowed.
- **Stalling.** More than `STALL_SLOTS` slots spent listening without the
  transfer advancing ends it the same way. Advancing means a pattern matched, a
  token collected, a verdict acted on, or something queued to transmit.
- **Except a Follower that can ask.** In `STEP_BLOCK` outside a block, holding
  nothing uncommitted, a stall becomes a request for the block again instead --
  see "Data receive mode" -- bounded by `MAX_BLOCK_ATTEMPTS` requests between
  accepted blocks. Past that bound it is `ERR_LOST_PEER` after all.

> The second one was missing, and its absence was the largest class of defect in
> this protocol. A peer stuck showing grey is not silent: every slot decodes to
> a symbol, so the silence counter is reset every slot and never fires. A badge
> waiting for a Begin Block Pattern that cannot arrive would sit there
> indefinitely, showing "Receiving", with the reset button as the only way out.
> Driving each side into each state against an uncooperative peer, **18 of 56
> combinations never terminated**. Hearing something is not the same as getting
> somewhere, and only the second is liveness.
>
> A transfer that succeeds stalls for at most 24 slots, median 21, across 16
> seeds x 6 scenarios x 2 payloads -- except one cold-panel run at 42. The
> budget is 2.7x the usual worst and 1.5x that outlier.

- **`MAX_BLOCK_ATTEMPTS`** caps how many times one block may be transmitted,
  whatever the reason, and exhausting it is `ERR_CRC`.
- **`BLOCK_TIMEOUT_MS`** is a wall clock on a single block, restarting each time
  one is accepted, checked unconditionally including part way through a
  transmission.

> Counted over the whole data phase instead, it was a cap on payload length
> wearing a backstop's clothes: 300 seconds is about 750 bytes at the 50 ms rung,
> and a 512-byte payload died with 449 of its 512 tokens already delivered. The
> protocol attaches no meaning to the payload and has no opinion about how long
> it may be -- that is the caller's business -- so its backstop must not smuggle
> one in. Per block it bounds the same stalls and scales with the work.

> `MAX_BLOCK_RETRIES` only counts CRC failures, and the Data Sync Error path
> deliberately resets it so that a rate change does not spend the budget meant
> for noise. That left resends driven by sync errors bounded by nothing: a
> Leader reading noise as a sync error demotes, resends, reads another, demotes
> again, and once it reaches the slowest rung resends for ever -- every loop
> looking like progress, so neither of the other two budgets fires. The ceiling
> is the whole ladder walked one rung at a time spending the CRC retries at
> each, `(MAX_BLOCK_RETRIES + 1) * len(SLOT_LADDER)`, derived rather than
> guessed so it cannot drift if the ladder changes.

> The deadline is the outermost of the three, and it bounds the ways of getting
> stuck that nobody has thought of. The other two each fired correctly against a
> link fed continuous noise and the escape still took 506 seconds: the Leader
> demoted a rung, resent, and did it again, every cycle legitimate and the whole
> sequence far too long to sit through.
>
> It is counted per BLOCK, so it is measured against the slowest block rather
> than the slowest transfer: up to 64 seconds in a run that succeeds across the
> scenarios, 108 on a 32 ms panel, resends and demotions included -- and 140 if a
> full 64-byte block ever ran at the slowest rung. At 300 seconds it sits at
> roughly twice the latter. Set nearer, a backstop becomes a new way to fail;
> counted over the whole transfer instead, it becomes a cap on payload length.

**Every state must have a way out.** A transfer is allowed to fail; it is not
allowed to wait for ever. `tests/share_wifi/test_no_dead_ends.py` drives each
side into each state against a peer that has stopped co-operating and asserts a
result appears.

---

## Data receive mode

Entered by the Follower. It holds black except when transmitting a CRC, a Data Sync
Error or an acknowledgement.

- Wait for the Begin Block Pattern, ignoring black.
- Collect one bit per slot into bytes, most significant bit first.
- A black symbol arriving **at a byte boundary** is a Field Separator.
- A black symbol arriving **two bits into a byte after `_-`** is the End Block Pattern
  starting.

> The End Block Pattern opens with two ordinary data symbols before its first black, so
> a receiver that treats every symbol as a bit eats them as payload and then trips over
> the black. The bit offset is what distinguishes the two cases.

- Any other black mid-byte, more than `BLOCK_BYTES` bytes without an End Block
  Pattern, or an End Block Pattern that does not match in full, is a framing failure.
  Stop decoding the block and wait for `RESEND_QUIET_SLOTS` consecutive black slots --
  the Leader does not listen while it transmits, and that much black means it has
  finished. Then transmit the Data Sync Error Pattern, step one rung slower once it has
  been sent, and wait for the resend.
- Stalled in `STEP_BLOCK` outside a block, holding nothing uncommitted -- more than
  `STALL_SLOTS` slots without advancing -- ask for the block again by the same route:
  `RESEND_QUIET_SLOTS` of black, then the Data Sync Error Pattern and one demotion.
  A request already waiting, a framing failure's, is re-armed rather than repeated, so
  one failure is one request and one demotion. A stall makes no request once
  `MAX_BLOCK_ATTEMPTS` requests of either kind have gone out since the last accepted
  block; then, or holding a block whose verdict never came, the stall is
  `ERR_LOST_PEER`.
- A Begin Block Pattern matched while a request waits cancels it.
- On the End Block Pattern, compute the block CRC -- `0xFFFF` for an empty block -- and
  transmit it as 16 raw bits. The block is now pending.
- Then wait for the Leader's verdict:
  - **CRC Good** — append the block and wait for the next Begin Block Pattern or the
    Complete Pattern.
  - **CRC Bad** — discard the block and wait for it to be resent.
  - **Data Sync Error** — step one rung slower, discard the block, and wait.
  - **Complete** — append the block, then acknowledge as below.
- On the Complete Pattern, transmit the Complete Ack Pattern, stay on air for
  answering any repeat of Complete, and exit once more than `LINGER_SLOTS` slots have
  passed since the last acknowledgement finished going out -- each repeat restarts the
  linger. Exit `OK` if a block was accepted, including an empty one, and `ERR_DECODE`
  if none was: acceptance is counted in blocks (`blocks_accepted`), not bytes. The
  reassembled fields are handed to the caller as they are; the protocol does not
  inspect them.

> Complete is watched for in the same state as Begin Block, because after the last block
> the Follower is waiting for a Begin Block Pattern that never comes. It is also watched
> for while waiting for a verdict: Complete is only ever sent after the Leader has
> matched the last block's CRC, so it carries the same guarantee CRC Good does and is
> the retransmitted proof of a verdict that went missing. **Appending the block before
> finishing is what stops the Follower agreeing that the transfer succeeded while
> holding nothing** (I7). A block is appended at most once — the pending tokens and the
> `pending_block` flag are cleared when they are taken — because a block acknowledged
> twice, by its verdict and again by Complete, would otherwise be appended twice and
> corrupt the payload while every CRC still passed (I4). The flag is there because an
> empty block holds no tokens, so their absence cannot tell "nothing pending" from
> "an empty block pending".

> The linger is why the Follower finishes *after* the Leader. A Follower that exited
> the instant it acknowledged would be gone before the Leader's first retry, turning
> one lost acknowledgement into a failure on a transfer that actually worked.

> Discarding a block used to need care here. The receiver tracked whether it had
> already seen the separator, in order to reject a second one, and a discarded block
> had to rewind that flag or the resend failed forever. Allowing any number of fields
> removed the flag and the failure mode together.

---

## Payload

A payload is a **list of byte strings**, joined by the Field Separator:

      <field> <separator> <field> <separator> <field> ...

- The protocol attaches no meaning to the fields. It does not know how many
  there should be, how long they may be, or whether they are text.
- Any number of fields is valid, including one — a stream with no separator is a
  single field. Two adjacent separators are an empty field.
- A payload with no bytes at all -- `[b""]`, `[]` or `None` -- is sent as one empty
  block: the Begin Block Pattern straight into the End Block Pattern, with the CRC-16
  of no bytes, `0xFFFF`. It arrives as `[b""]`.

> A Leader with no block to send would have nothing to wait for an answer to, and
> the empty block is what tells the Follower the payload is a single empty field.
- The separator must be black, not a data bit.

> Payload bits are only high and middle, so a separator at either data level
> would be indistinguishable from payload. Black is already reserved and
> unforgeable, and it costs one slot per boundary — against eight for a one-byte
> length prefix.

- Separators are part of the payload stream, not of any block, and do not count
  toward the block byte limit. One may fall at a block boundary.
- A black symbol is a separator when it arrives at a byte boundary, and the start
  of the End Block Pattern when it arrives two bits into a byte after `_-`.

> The bit offset is the whole discriminator. An earlier design also required that
> a stream contain exactly one separator, which made a second one a framing
> error; that rule turned out to be doing no work, and removing it is what allows
> any number of fields.

- Blocks are transport only: the receiver concatenates them in order and splits
  the reassembled stream at every separator.

## Exit values

| Value | Meaning |
|---|---|
| `OK` | transfer completed and verified |
| `ERR_NO_PEER` | no peer seen before the announce timeout |
| `ERR_CONTRAST` | calibration could not separate the three light levels |
| `ERR_LOST_PEER` | the peer went dark or stopped making progress mid transfer, a block outran `BLOCK_TIMEOUT_MS`, or Complete was never acknowledged |
| `ERR_CRC` | a block failed CRC more than `MAX_BLOCK_RETRIES` times at or below the proven rung, or was sent `MAX_BLOCK_ATTEMPTS` times |
| `ERR_DECODE` | the Follower reached the end having accepted no block; callers may also use it for a payload they reject |
| `ERR_CANCELLED` | the caller cancelled the transfer, by calling `fail(ERR_CANCELLED)`; the protocol has no cancel API of its own |

---

## Implementation constraints

- MicroPython on this badge has **no threading and no multiprocessing**. The protocol
  runs in the transfer loop or it does not run.
- Every phase is a state machine driven by `work(now)`, not a blocking loop. Each
  pass of the caller's loop waits for `due_us()`, shows `field_at(now)`, and calls
  `work(now)`, which decides whatever its reader has completed since.

> Transmitting means changing the screen. A blocking loop that "sends a block" would
> hold one level for the whole block and transmit nothing at all.

- Every timeout is a deadline compared against the reader's clock -- µs since the
  transfer began, accumulated so it never wraps. None is a sleep. A transfer that
  reaches `TRANSFER_MAX_US` ends `ERR_LOST_PEER`, so the clock stays a small int.
- **The protocol never collects garbage itself.** It sets `collect_due` and the caller
  collects at the top of its next pass, never inside a call: when the Follower's
  closing gap begins, when the Leader finds it, on the Leader after a verdict before
  the next block, and on the Follower when it accepts CRC Good -- each while that
  badge holds LOW or only listens. An EXACT reader goes on reading through it; a
  POLLED one is blind for it [**unmeasured** on v3; ≤ 46 ms on v2 [measured-v2]],
  so a POLLED Follower that accepts a block owes the collection
  (`collect_owed`) until it next queues a message, which the reply gap then
  delays whole: collected at once, it missed the Complete that follows the
  verdict by a slot. Not an EXACT one: the delay is silence the Leader may
  phase-lock after, and on a slow sensor its lock on the reply's rise to the
  middle landed a slot late.
- Nothing per reading is Python's: the ring's readings are summed by viper kernels
  (`fastadc.py`), a POLLED reading is an index and two adds. Per slot: one
  `parts()` query, a few corrections and a median. The per-slot allocations that
  remain are accepted: pattern matches `"".join` the recent symbols, `send()` copies
  its symbols, `collect_block` counts bytes with a generator. With 7.7 MB free
  [measured-v2] an automatic collection inside one transfer is unlikely; Dev Mode's
  M3 measures the garbage a slot leaves.

## How the constants were measured

The debugging tooling that produced the figures in "What the measurement found"
has been removed. It is recorded here because the numbers it produced are still
the basis of `SENSOR_STEP`, the noise figures behind `MIN_SWING` and
`GAP_SIGMAS`, and the middle field, and because anything that measures this hardware again will meet the
same obstacles.

Three tools, all in the `share_wifi` app at the time:

- **A staged walkthrough.** Sending and Receiving stepped the phases of a
  transfer one at a time, driven by a timer rather than by a peer, so one badge
  on a desk showed what each stage puts on screen.
- **A light meter.** It held one of the three levels on the transmit bands and
  reported what its own sensor read.
- **A hardware measurement.** Two passes, dark then lit, with the badge held
  screen-first against a mirror — the only self-contained arrangement there is,
  since the sensor is on the same face as the screen. It measured the three
  levels, a six-point gamma ramp, the noise in a held level, and black-white
  transitions.

Three things learned doing it are worth keeping:

- **A mirror is not a second badge.** Two badges facing each other are
  mirrored, so one's right edge meets the other's left. A reflection preserves
  world position, so a right-edge sensor sees the right edge. Both land on a
  band, since there is one down each edge, but a mirror result says nothing
  about the two-badge geometry.
- **Settling time cannot be measured from Python.** The panel starts changing
  when a frame is presented, and nothing can sample while `display.update()`
  waits for vsync and pushes pixels. That blind spot measured **22.2 ms** on
  hardware, which is larger than the value being measured — so `LCD_SETTLE_MS`
  could only be bounded, never fitted.
- **Fit the time constant, not a threshold crossing.** `ln|final − v|` falls
  linearly at `−1/tau` and the slope does not depend on when sampling began, so
  the blind spot drops out of it. Timing a crossing under-reads instead, and
  under-reporting a settling time is the direction that breaks transfers.

## Throughput

- One bit per slot, half duplex, so the raw rate is `1 / slot`: 125 bit/s on
  the 8 ms rung two LIT EXACT badges start on, 62 bit/s on 16 ms (EXACT in
  the dark, POLLED lit), 31 bit/s on 32 ms (POLLED in the dark).
- A typical credential, in simulation, from the first beacon: **5.5 s** lit
  and **7.5 s** dark for an EXACT pair, 7.3 s and 11.1 s for a POLLED one,
  each including the simulator's 1.5 s start skew in four scenarios of six
  -- see "What simulation says". Before the ring, 10.6 s and 15.3 s.
  Discovery is about 0.7 s and calibration 1.6-1.8 s.
- Per-block overhead is 47 slots: lead-in 4, Begin 9, End 9, CRC 16, verdict 9. Plus 6
  slots for the Complete Pattern.
- Ladder values are what the clock keeps, in µs: a transmitter changes its
  backlight one pass of its loop after each boundary, and a receiver decides
  from its reader's history, so neither rounds a symbol to a frame.
- The Follower finishes `LINGER_SLOTS` slots after the Leader, by design.

**Before 2026-10-05**, stepped once a frame (the figures here are from then):

- Each symbol was on screen for a whole number of frames, so individual
  symbols ran long or short of the slot -- 39 to 62 ms on the 50 ms rung at
  90 fps, and whole 16.7 ms frames -- 33, 50 or 67 ms **[inferred-v2]** -- at
  60 -- but the boundaries were on the clock and did not drift: symbols
  averaged **50.0 ms** apart there, measured across an 825-symbol transfer.
- Setup was 4.8 s of calibration, 2400 ms in each direction, and 7.2 s when
  the Leader offered the backlight scheme.
- Simulated end to end at 90 fps, including discovery and both calibrations. Median
  over 8 seeds, with the delivery rate beside it:

      settles in  8 ms  ends on 50 ms, once 65   typical 24.4 s  8/8   worst 50.9 s  8/8
      settles in 16 ms  ends on 50 ms, twice 65  typical 24.4 s  8/8   worst 51.0 s  8/8
      settles in 32 ms  mostly demotes to 65     typical 46.0 s  7/8   worst 92.0 s  6/8

  Typical is a 15-byte SSID with a 20-byte key; worst case is 32 and 63.
- **At 60 fps**, v2's best, 144 runs deliver 136 against 141 at 90 fps, with
  a median of 27.1 s against 18.1 s. At 30 fps they started on the 100 ms
  rung and took a median 28.9 s.

> These are slower than the figures the optimistic rung used to quote, and that
> comparison was never real: the fast rung's timings assumed a gamble that measurement
> says usually fails. See *Rate selection*. A transfer that completes in 18 s beats one
> that quotes 6.5 s and delivers nothing.

- **Transmit at `LORES`.** It writes the same 153,600 bytes to the panel as `HIRES`
  -- the earlier advice here not to switch was right about that -- but on v2 firmware
  the write was never the cost: drawing was, at 11.4 ms for one HIRES full-screen
  fill **[measured-v2]**. A `LORES` fill touches a quarter of the pixels. See "Screen as
  a transmitter".

---
