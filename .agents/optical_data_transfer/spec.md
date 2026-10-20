# Optical Data Transfer Protocol: specification

Two badges pass a list of byte strings to each other with light. One badge
holds a white screen and steps its backlight between three levels; the
other reads its light sensor through a DMA ring and decodes the levels.

- **Why** each value is what it is: [context](context.md).
- **How** to check an implementation: [tests](tests.md#L1).
- **Interface**: section 19 names the modules, functions and classes the
  tests call. Every name below is one of them.

Every statement here is normative. Figures in the context carry a
confidence marker ([confidence markers](context.md)); no
transfer has yet run between two badges.

## 1. Terms

| Term | Meaning |
|---|---|
| Leader | the badge sending the payload. It owns the slot clock, sends every block and judges every CRC. |
| Follower | the badge receiving it. It sends the one staircase, aligns to the Leader, and otherwise sends only replies. |
| Symbol | one of three light levels: `-` high, `_` middle, ` ` low (black). |
| Slot | the time one symbol is shown. |
| Rung | a slot duration from `SLOT_LADDER`. |
| Epoch | the time slot 0 starts. Both badges compute it. |
| Count | one `light_level()` unit; one ADC step is 16 counts. |

The caller assigns roles before any light is exchanged. The badges never
negotiate them.

## 2. Hardware requirements

- **Light out**: a `display.backlight(v)` call, live at the next PWM wrap.
  The screen stays white. [Context](context.md).
- **Light in**: the light sensor's ADC, free-running into a DMA ring.
  [Context](context.md), [ring on the badge](context.md).
- The sensor must average the backlight's PWM. If it follows it, the
  middle level is unreadable and calibration fails `ERR_CONTRAST`.
- No other signal path exists. No pixel signalling, no polled reader, no
  fallback.

## 3. Constants

Times in ms unless the name ends `_US`. Values are fixed; rationale in the
context. Test: [T-constants-match](tests.md#L128).

**Sensor and levels**

| Name | Value | Meaning |
|---|---|---|
| `SENSOR_STEP` | 16 | one ADC step, in counts |
| `QUANTISATION_SD` | 4.62 | `SENSOR_STEP / sqrt(12)`: the least noise a level is given |
| `MIN_SWING` | 48 | least swing that counts as a signal |
| `MIN_GAP_EXACT` | 16 | least gap between adjacent levels |
| `GAP_SIGMAS` | 4 | a gap must be this many standard deviations of the noisier level |
| `LIT_BLACK_COUNTS` | 64 | a badge whose black reads at least this is LIT |
| `LOW_BACKLIGHT`, `MID_BACKLIGHT` | 0.50, 0.82 | data low and middle; high is 1.0 |
| `BEACON_LOW_BACKLIGHT` | 0.00 | beacon low |

**Timing and rungs**

| Name | Value | Meaning |
|---|---|---|
| `SLOT_LADDER` | 32, 25, 20, 16, 13, 10, 8 | the control rungs, slowest first |
| `EXACT_LIT_INDEX`, `EXACT_INDEX` | 6, 3 | start rung lit (8 ms) and dark (16 ms) |
| `SLOT_MIN_MS` | 6 | no slot shorter |
| `EXACT_DARK_START_MS` | 16 | dark pairs start no faster than this |
| `SETTLE_LIT_US`, `SETTLE_DARK_US` | 2500, 5000 | a window opens no sooner than this into a slot |
| `GUARD_LIT_US`, `GUARD_DARK_US` | 1000, 2000 | a window closes at least this before the slot ends |
| `OPEN_PERCENT`, `BACKLIGHT_GUARD_PERCENT` | 40, 15 | the same, as shares of the slot |
| `WINDOW_MIN_US` | 2000 | least window a starting rung must have |
| `PART_MAX_US`, `PARTS_MIN`, `PARTS_MAX` | 4000, 5, 32 | a window's parts |
| `EDGE_LAG_EXACT_LIT_US`, `EDGE_LAG_EXACT_DARK_US` | 950, 1650 | boundary to `T_low` crossing |
| `FINE_MS` | 2 | the sample length before the data phase, fine and coarse alike |
| `FINE_US` | 2000 | `FINE_MS` in µs |
| `DUE_MARGIN_US` | 100 | a wait runs this far past its due time |
| `TRANSFER_MAX_US` | 1006632960 | a transfer this long ends `ERR_LOST_PEER` |

**Discovery and calibration**

| Name | Value | Meaning |
|---|---|---|
| `ANNOUNCE_FAST_HALF_MS` | 14 | the Leader's beacon half period; there is no other beacon |
| `BEACON_TOLERANCE` | 0.20 | a measured half period may differ by this share |
| `BEACON_FAST_HALVES` | 6 | halves a beacon is matched over |
| `BEACON_WINDOW_MS` | 1000 | window for a beacon's black and full |
| `ANNOUNCE_TIMEOUT_MS` | 60000 | how long either badge looks for a peer |
| `PLATEAU_FAST_MS` | 20 | staircase step |
| `CAL_REPS_FAST_LIT`, `CAL_REPS_FAST` | 1, 2 | repetitions, lit and dark |
| `CAL_OPEN_FAST_MS` | 40 | the staircase's opening gap |
| `CAL_CLOSE_FAST_MS`, `CAL_CLOSE_FAST_SPARE_MS` | 70, 20 | a closing gap is found at the difference, 50 ms in |
| `CAL_GAP_FAST_MS` | 110 | the staircase's closing gap, and the shared gap |
| `LEADER_CAL_LIMIT` | 160 | samples the Leader's staircase reader keeps |
| `LEADER_CAL_SMOOTH` | 10 | samples the Leader's closing-gap search averages |
| `LEADER_SETTLE_MS` | 100 | the Leader fits and takes its levels this far into the gap |
| `LEADER_FIT_SKIP_MS` | 10 | the start of the gap the Leader's fit leaves out |
| `FOLLOWER_FIT_START_MS` | 70 | the Follower's fit window starts this far into the gap |
| `FOLLOWER_CHECK_MS` | 60 | the Follower's steady check |
| `FLICKER_FAST_FIT_MS` | 100 | the Follower's fit, if not steady |
| `CAL_GAP_FAST_FLICKER_MS` | 500 | the first block waits this long after the gap began when the Leader's fit found flicker |
| `FAST_START_GRACE_MS` | 100 | the Follower's extra wait for the first block |
| `HOLD_MARGIN_SLOTS` | 4 | a Follower that found flicker takes no light for a lock until this many slots before the hold ends |
| `PROVISIONAL_GREY_PERCENT` | 45 | the Follower's middle until a Fast Begin trains it |
| `CAL_RESTARTS` | 1 | refused staircases allowed before `ERR_CONTRAST` |
| `LIT_STEPS_MAX_FAST` | 1 | high steps at most for LIT |
| `MIN_HELD_FAST` | 5 | settled samples a level needs |
| `CAL_FAST_MIN_SAMPLES` | 24 | samples a staircase body needs |
| `STEP_SAMPLES` | 3 | samples a level must hold to count as settled |
| `CAL_CAUGHT_UP_US`, `CAL_PENDING_MAX_MS` | 4000, 250 | when the staircase may start |
| `LOCK_SKIP_US`, `LOCK_HOLD_US` | 500, 3000 | a phase lock's edge must stay lit from the first to the second after it |
| `RATE_CHECK_SLOTS`, `RATE_MATCH_PERCENT` | 7, 8 | the Follower's check of the Leader's rate (section 14) |

**Data phase**

| Name | Value | Meaning |
|---|---|---|
| `RESYNC_LEAD_IN` | 4 | black slots before every block |
| `SYNC_LEAD_IN` | 9 | extra black before a block resent after the peer's Data Sync Error |
| `MAX_BLOCK_ATTEMPTS` | 48 | transmissions of one block at most |
| `LOST_PEER_SLOTS` | 16 | black slots in a row that end a transfer |
| `STALL_SLOTS` | 64 | slots without progress that end a transfer |
| `RESEND_QUIET_SLOTS` | 3 | black slots a Follower waits before asking again |
| `BLOCK_TIMEOUT_MS` | 300000 | deadline per block |
| `COMPLETE_RETRY_SLOTS` | 12 | silent slots before Complete is repeated |
| `COMPLETE_REPEAT_MAX_SLOTS` | 18 | latest repeat after the last Complete |
| `MAX_COMPLETE_RETRIES` | 3 | repeats of Complete |
| `LINGER_SLOTS` | 20 | Follower's linger after its Ack |
| `WATCH_SLOTS` | 2 | lead-in slots a Leader reads for the flicker after a stall |
| `MAX_PATTERN` | 16 | longest symbol history any match uses |

**Fast blocks** (sections 13.1 and 14.2)

| Name | Value | Meaning |
|---|---|---|
| `FAST_GAP_SLOTS` | 2 | low slots between the rung's bits and the burst |
| `FAST_RUNG_BITS` | 2 | the fast rung's index after `FAST_BEGIN` |
| `FAST_START_LIT`, `FAST_START_DARK` | 0, 1 | the fast rung a LIT and a DARK pair start on |
| `FAST_TRIES` | 2 | NAKs of one block before the next fast rung |
| `FAST_UP_ACKS` | 4 | ACKs in a row below the start rung before the Leader steps back up one; doubled each time a rung stepped up to steps down before holding this many |
| `UNFRAMED_LIT_SLOTS` | 16 | lit slots, framing nothing, after which a fast-capable Follower asks again (14.2) |
| `FAST_SEARCH_US` | 3000 | the Follower looks for the burst's first rise this far either side of when it is due |
| `EDGE_LAG_FAST_LIT_US`, `EDGE_LAG_FAST_DARK_US` | 400, 750 | the low-to-high edge's lag to `T_low`, by the Follower's light |
| `FAST_REPLY_MS` | 800 | the Leader's wait for the reply, beyond the longest burst: the burst's end found, the decoder's trainings (315-434 ms on v2) and the decode |
| `FAST_BIN_US` | 100 | a decoder bin |
| `FAST_BINS` | 12000 | bins a Follower holds for one burst |

Flicker constants are in section 15.

## 4. Symbols and levels

| Symbol | Beacon table (`SCHEME_BEACON`) | Data table (`SCHEME_BACKLIGHT`) | Meaning |
|---|---|---|---|
| `-` high | 1.0 | 1.0 | bit 1 |
| `_` middle | not sent | 0.82 | bit 0 |
| ` ` low | 0.0 (off) | 0.5 (dim) | pattern framing; never a data bit |

- Beacons use the beacon table; the staircase and everything after it use
  the data table. The Follower switches to the data table when it detects
  the Announce, as it starts its staircase. The Leader switches when it
  finds the staircase's closing gap, and back to the beacon table if it
  refuses the staircase and announces again.
- Payload bits are only high and middle, so any pattern holding low cannot
  come from payload.

[Context](context.md). Test: [T-level-tables](tests.md#L218).

## 5. The screen

1. Before the transfer: switch `run()`'s clear off, set the backlight to
   0.0, fill the whole screen rgb(255, 255, 255), present once.
2. During: set the backlight only when the symbol changes. Draw nothing.
   Present nothing. No screen-mode switch.
3. After: restore the clear and the normal backlight.

Report progress some other way (share_wifi uses the case lights).
[Context](context.md). Test: [T-no-pixels](tests.md#L233).

## 6. The reader

A `RingReader` over the DMA ring. The ADC free-runs at 50 kHz into a
16,384-reading ring (327.7 ms). [Context](context.md).

- **Time** is µs since the transfer began, accumulated so it never wraps.
  A reading's time is `anchor + 20 µs x index`.
- **History** usable: the ring less 10 ms kept clear of the write pointer
  (317.7 ms).
- **Queries**:
  - `parts(t0, t1, n, out)`: means of `n` equal parts of `[t0, t1)`, in
    counts; -1 for a part that has left the ring.
  - `mean(t0, t1)`.
  - `rise(...)`: the first crossing of a threshold, confirmed by two lit
    100 µs bins in a row, interpolated between the last bin below and the
    first above. Each bin is held to the threshold of the step it falls in.
  - `bins(...)`: raw 100 µs sums, a fast block's input (section 14.2).
- **`check()`**, once per block on each side -- the Leader before it sends
  each block, the Follower before it waits for each, the first included:
  on lost readings, re-anchor
  and go on; on any other fault, end the transfer `ERR_RING`. A fresh
  anchor is compared, and adopted only if it differs by more than 100 µs.
- **`verify()`**, once a launch, before the first transfer. If it fails,
  every transfer that launch refuses with the reason on screen.
- Nothing else reads the ADC while the ring runs.

Tests: [T-reader-ring](tests.md#L250), [T-reader-check](tests.md#L302),
[T-ring-verify](tests.md#L313), [T-ring-refusal](tests.md#L323).

## 7. The transfer loop

```
loop:
    if transfer.collect_due: gc.collect()
    reader.pump(transfer.due_us())        # wait; the ring keeps reading
    t = reader.now_us()
    set backlight for transfer.field_at(t), if changed
    transfer.work(t)                      # decide everything complete by t
    if transfer.fast_burst: play it from its start (section 13.1)
    every 50 ms: poll buttons; B cancels with fail(ERR_CANCELLED)
```

- `due_us()` is, in the data phase: the next slot boundary while sending
  (now, while a queued message waits for its first slot);
  a fast burst's start, then its end, while one is on air; otherwise the
  close of the next undecided window. Before the data phase, the end of
  the next 2 ms sample. Each but the boundary and now is `DUE_MARGIN_US`
  late. It does not look at `collect_due`: the loop collects first.
- The protocol never blocks, sleeps or collects garbage. It sets
  `collect_due`, only while its badge shows low or only listens.
- Every timeout is a deadline on the reader's clock.

Test: [T-loop](tests.md#L349).

## 8. Discovery

**Leader**: from the start, shows the Announce beacon: low and high
alternating every `ANNOUNCE_FAST_HALF_MS`, low first, in the beacon table.
While it does, it reads for the Follower's staircase (9.2). It stops on
finding the staircase's closing gap.

**Follower**: shows low (beacon table) until it detects the Announce. Then,
from the samples of the last `BEACON_WINDOW_MS` (not the match's shorter
window), `mid` = the midpoint of their range:

- `black` = the median of samples at or below `mid`, the upper median of
  an even count; its
  light is LIT when `black ≥ LIT_BLACK_COUNTS`, and that is the pair's;
- `white` = the median of samples above `mid`, likewise, the Leader's high;
- provisional levels: `black`, `white`, and a middle at
  `black + (white - black) x PROVISIONAL_GREY_PERCENT / 100`.

It then switches to the data table and sends its staircase (9.1) at once.
There is no answering beacon.

Either badge ends `ERR_NO_PEER` after `ANNOUNCE_TIMEOUT_MS`.

**Beacon detection.** Samples are 2 ms means (`FINE_MS`). A beacon of
half period `h` is matched over the last `BEACON_FAST_HALVES x h` ms when
all hold:

- at least 8 samples, and `max - min ≥ MIN_SWING`;
- at least a quarter of the samples on each side of the midpoint;
- at least 3 midpoint crossings, each interval within `h / 2` of `h`;
- the mean interval within `BEACON_TOLERANCE` of `h` (11.2-16.8 ms).

[Context](context.md). Tests: [T-beacon-field](tests.md#L366),
[T-beacon-detect](tests.md#L377), [T-discovery-roles](tests.md#L420).

## 9. Calibration

One staircase, the Follower's. [Context](context.md).

### 9.1 The staircase

```
opening gap (low) | reps x (low, high, middle) | closing gap (low)
```

- Opening `CAL_OPEN_FAST_MS`; steps `PLATEAU_FAST_MS`; closing gap
  `CAL_GAP_FAST_MS`, the shared gap.
- `reps` is `CAL_REPS_FAST_LIT` (1) if the Follower is LIT, else
  `CAL_REPS_FAST` (2). The order is `STAIRCASE`: low, high, middle.
- It starts on the first pass caught up with the ring (less than
  `CAL_CAUGHT_UP_US` unread), or the first pass more than
  `CAL_PENDING_MAX_MS` after the pass that detected the Announce, whichever
  is first. Until then the Follower shows
  low.
- At the end of the body the Follower sets `collect_due`.

Test: [T-staircase-shape](tests.md#L453).

### 9.2 Reading the staircase (Leader)

The Leader samples every `FINE_MS` while it announces. Each
sample is the median of itself and its two neighbours. It keeps the last
`LEADER_CAL_LIMIT`.

- **End**: once it holds `CAL_FAST_MIN_SAMPLES`, `max - min ≥ MIN_SWING`,
  and the means of `LEADER_CAL_SMOOTH` samples kept over the last
  `LEADER_CAL_LIMIT` swing by `MIN_SWING` too, with `line = min + (max -
  min) // 3`: the latest such mean has stayed at or below `line` for
  `CAL_CLOSE_FAST_MS - CAL_CLOSE_FAST_SPARE_MS` (50 ms). One mean above the
  line does not break the run. The gap's start, `ended_at`, is fixed on
  the sample whose mean first goes at or below `line`: the first raw
  sample of the trailing run at or below `line` among the last
  `LEADER_CAL_SMOOTH` raw samples; if the newest raw sample is above it,
  that sample's time less half the mean's window.
- **Then**: the Leader shows low, and at `ended_at + LEADER_SETTLE_MS`
  **settles**: it fits the flicker (section 15) on the samples from
  `ended_at + LEADER_FIT_SKIP_MS` to `ended_at + LEADER_SETTLE_MS`; if the
  fit found flicker, it feeds a fresh reader -- `StaircaseReader()`, its
  defaults (512 samples, no smoothing) -- the kept samples from
  `ended_at - (staircase_ms(2, CAL_OPEN_FAST_MS, PLATEAU_FAST_MS, 0) +
  PLATEAU_FAST_MS)` on, corrected, and uses its levels if they pass. If
  they do not, the first reading's levels stand, and the light flag (9.5)
  is read from the fresh reader's samples against them.
- **Body**: from 12 samples before the first rise past a quarter of the
  swing, to the closing gap.
- **Levels**, over the body:

  ```
  L_black = 10th percentile      L_white = 90th percentile
  span    = L_white - L_black
  L_grey  = median of samples in [L_black + span/8, L_black + 7*span/8]
  ```

- **Accept** only if all hold:
  - at least `CAL_FAST_MIN_SAMPLES` samples; `span ≥ MIN_SWING`;
  - at least 4 samples in the middle band;
  - `held x 2 ≥ above`, where `held` counts band samples within `span/8` of
    `L_grey` and `above` counts samples above `L_black + 7*span/8`;
  - `L_grey - L_black ≥ span/4` and `L_white - L_grey ≥ span/4`;
  - each level has `MIN_HELD_FAST` settled samples;
  - each gap passes the separability check (9.3).
- **Refused**: the Leader counts a refusal; past `CAL_RESTARTS` it ends
  `ERR_CONTRAST`; otherwise it goes back to announcing, with a fresh reader
  and flicker filter.

Tests: [T-staircase-levels](tests.md#L473),
[T-leader-reading](tests.md#L628), [T-refusal](tests.md#L642).

### 9.3 Separability

A level's noise is the standard deviation of its **settled** samples: a
sample counts once the reading has stayed in that level's class for
`STEP_SAMPLES` samples. Each of the two gaps must satisfy:

```
gap ≥ MIN_GAP_EXACT
gap ≥ GAP_SIGMAS x max(noise either side, QUANTISATION_SD)
```

[Context](context.md). Test: [T-separability](tests.md#L521).

### 9.4 Thresholds

```
T_low  = (L_black + L_grey) // 2      v <  T_low           -> low
T_high = (L_grey + L_white) // 2      T_low <= v < T_high  -> middle
band   = span x 5 // 100              v >= T_high          -> high
```

A value within `band` of `T_low` keeps the last level if it was low or
middle; within `band` of `T_high`, if it was middle or high. Otherwise
the plain rule decides: a value at `T_high` is never low.
Test: [T-thresholds](tests.md#L545).

### 9.5 The light flag

The staircase's repetitions carry the pair's light: `LIT_STEPS_MAX_FAST`
(1) high steps or fewer read as LIT. A step counts once a level holds for
`STEP_SAMPLES` samples.

- **The Follower decides**: `pair_lit` = its own light, from the
  Announce's black (section 8).
- **The Leader adopts it as read**, whatever its own light. Its own light
  (`is_lit(L_black)`) sets its window's settle and guard (10.2) and the
  edge lags its phase locks and fast-burst search allow (10.4, 14.2): never
  the rung.
- No flicker flag travels. The Leader's own fit decides the hold (9.6).

Every badge sends and decodes fast blocks (sections 13.1 and 14.2): a
badge not given the fast block's module (section 19) cannot transfer.

[Context](context.md). Test: [T-flags](tests.md#L563).

### 9.6 Epoch and start

- **Epoch** = the start of the closing gap + `CAL_GAP_FAST_MS`. The
  Follower knows that start exactly; the Leader uses `ended_at`.
- Both start the data phase on `EXACT_LIT_INDEX` (8 ms) if `pair_lit`, else
  `EXACT_INDEX` (16 ms).
- **Flicker hold**: if the Leader's fit found flicker, its first block is
  preceded by `hold = ceil((CAL_GAP_FAST_FLICKER_MS - CAL_GAP_FAST_MS) /
  slot)` extra low slots, counted from the epoch: a Leader that starts the
  data phase late drops as many of them as slots have passed. The epoch
  never moves for it.
- The Follower waits `hold + ceil(FAST_START_GRACE_MS / slot)` slots for
  the first block, whatever its own fit found, before counting silence
  against `LOST_PEER_SLOTS`, and as many before counting stall slots. The
  silence grace ends at the first lit slot it decides; the stall credit
  lasts until the transfer first makes progress (a Begin framed).

[Context](context.md). Tests: [T-calibration-timing](tests.md#L578),
[T-epoch-and-hold](tests.md#L597), [T-calibration-end-to-end](tests.md#L615).

### 9.7 The Follower's fit and provisional black

- The Follower decides no slot before its fit is done.
- From `fit_start = closing gap start + FOLLOWER_FIT_START_MS`: at
  `fit_start + FOLLOWER_CHECK_MS` it takes the ring's 2 ms means over that
  window. Their median is its new provisional black (if the swing to
  `white` stays `≥ MIN_SWING`; the middle moves with it). If the window is
  steady (section 15), done.
- Otherwise at `fit_start + FLICKER_FAST_FIT_MS` it fits the flicker on
  the 2 ms means of `[fit_start, fit_start + FLICKER_FAST_FIT_MS)` (refused:
  the later half), and if it found
  flicker, takes no slot as lit before `start_of(RESYNC_LEAD_IN + hold -
  HOLD_MARGIN_SLOTS)`, and allows `first_grace` more slots of silence
  until it frames a Begin: `ceil((FAST_REPLY_MS + burst_max_us(fast,
  RUNGS[start])) / slot) + RESYNC_LEAD_IN + len(FAST_BEGIN) +
  FAST_RUNG_BITS + FAST_GAP_SLOTS`, `start` its pair's fast start rung --
  a Leader that did not hold sends its first block again after its reply
  wait (13.1 step 4). Not until its first light, which may be the end of
  the burst it missed. The stall budget gets as much.
- Each badge fits while it only reads, so the ring covers the stall.

[Context](context.md). Test: [T-calibration-timing](tests.md#L578).

## 10. Rungs and slots

### 10.1 Rungs

A rung `d` qualifies as a start when its window (10.2) is at least
`WINDOW_MIN_US` and `d ≥ SLOT_MIN_MS`, and every slower rung qualifies
too: the ladder is scanned slowest first and the scan stops at the first
rung that fails. Starts are 8 ms lit and 16 ms dark
(dark is held at `EXACT_DARK_START_MS`). The ladder carries control
patterns only; the payload goes in fast blocks (13.1). [Context](context.md). Test: [T-start-rung](tests.md#L661).

### 10.2 The window

The receiver decides slot `k` from a window inside it, by its own light:

```
open  = max(SETTLE, OPEN_PERCENT% of d)
close = d - max(GUARD, BACKLIGHT_GUARD_PERCENT% of d)
```

- If `close - open < WINDOW_MIN_US / 2`, use `open = close - d/4`.
- Split the window into `n = clamp(ceil(window / PART_MAX_US), 5, 32)`
  equal parts; correct each for flicker; the slot's value is their median
  (an even count's median is the mean of the middle two). With no parts,
  the whole slot's mean; with nothing, repeat the last level.
- Decide slots in order once the window has closed. Skip none, except
  where a section stands slots aside: the Follower's fit (9.7), a re-base
  (10.4), a fast block's reply (14.2), and a move to the
  Leader's rung (14, "Checking the Leader's rate").
- Never decide a slot this badge transmits on.

Test: [T-window](tests.md#L681).

### 10.3 Sending

- Strictly half duplex: one badge sends; the other shows low.
- Index each symbol by slot number, never by call count.
- The sender sets the backlight on the first pass after each boundary.
- A message queued by a decision starts two slots after the deciding slot
  (one silent slot), or on the next boundary if the badge is late. A message
  queued by no decision starts on the next boundary.

Test: [T-slot-decisions](tests.md#L704).

### 10.4 Phase locking

At the start of every message the receiver re-bases its grid on the
sender's first edge out of black.

- Only while **acquiring**: Leader in `STEP_FAST_REPLY` and
  `STEP_FINISH`; Follower in `STEP_BLOCK` outside a block and
  `STEP_FINISH`.
- Requires 1.5 slots of silence, counted from the end of the peer's last
  light (or this badge's own sending) to the start of the slot holding the
  crossing.
- When a slot's value (before hysteresis) is at least `T_low`, `rise()`
  scans back up to 2.5 slots, and no further than the end of the last
  light, for the first crossing of `T_low` plus the flicker (fitted, or the
  learner's).
- **The crossing must stay lit**: the ring's mean from `LOCK_SKIP_US` to
  `min(LOCK_HOLD_US, close - crossing)` after it, less the fitted flicker
  over that span, must be at least `T_low`; if not, search on from the next
  threshold step. A span under `LOCK_HOLD_US / 2` is accepted as it stands.
- A Follower in its flicker hold (9.7) takes no slot as lit, so cannot lock.
- New epoch = the crossing - `EDGE_LAG_EXACT_LIT_US` or `_DARK_US`, by
  this badge's light. A sensor slower than measured crosses later on a
  rise to the middle than to high, so a lock on a message that opens with
  a middle (CRC Bad) sits late; the next message's lock corrects it. The decided slot is discarded; slot 0 of the new grid
  is decided next. One re-base a message.
- Re-basing moves the epoch, never the duration. A rate change moves the
  duration, never the epoch.

[Context](context.md). Tests: [T-phase-lock](tests.md#L721),
[T-lock-hold](tests.md#L735).

## 11. Control patterns

One symbol per slot.

| Pattern | Symbols | Sent by | Meaning |
|---|---|---|---|
| Fast Begin | `-_ -_ _- ` | Leader | a fast block follows (13.1) |
| CRC Good | `-- -- -- ` | Follower | ACK: block accepted |
| CRC Bad | `__ __ __ ` | Follower | NAK: block rejected |
| Data Sync Error | `  _  _  _` | Follower | step one rung slower and send again |
| Complete | `- - - ` | Leader | transfer finished |
| Complete Ack | `__- __- ` | Follower | Complete received |

- Receivers match CRC Good, CRC Bad, Complete, Complete Ack and Data Sync
  Error **without their trailing low**. Fast Begin is matched whole.
- The Leader accepts CRC Good, CRC Bad and the Complete Ack less their
  first symbol as well (`- -- --`, `_ __ __`, `_- __-`).

[Context](context.md). Test: [T-patterns](tests.md#L748).

## 12. Payload

- **Payload**: a list of byte strings. The protocol attaches no meaning to
  them.
- **Fast stream**: the field count, then each field's length and bytes,
  every number an unsigned LEB128 varint. No separator. An empty payload
  (`[]`, `None`) is the stream `00` and arrives as `[]`; `[b""]` is
  `01 00` and arrives as `[b""]`.
- The stream goes in fast blocks (13.1, appendix A), each checked by its
  own CRC-32.

Test: [T-payload](tests.md#L774).

## 13. Data phase: the Leader

### 13.1 Fast blocks (Leader)

- **Block**: from chunk `c` (32-byte chunks, first 0), the next
  `block_bytes(periods)` bytes of the stream, or what is left.
- **Rung**: start on `FAST_START_LIT` if `pair_lit`, else
  `FAST_START_DARK`, an index into F0-F3.
- **Sending**, at the slot rung:
  1. Before each block, call `reader.check()`; on failure end `ERR_RING`.
     Past `MAX_BLOCK_ATTEMPTS` transmissions of one block, end `ERR_CRC`.
  2. `RESYNC_LEAD_IN` low, `FAST_BEGIN`, the rung index as
     `FAST_RUNG_BITS` bits (`bits_symbols`; high 1, middle 0),
     `FAST_GAP_SLOTS` low. While the lead-in goes out the Leader reads its
     slots for the flicker learner (section 15); after a stall only the
     last `WATCH_SLOTS`.
  3. The burst from the next slot boundary: `fast_burst` = `(start_us,
     symbols, periods)` for the shell, which plays it and calls
     `burst_started(t_us)`. The Leader shows low until it ends.
  4. Listen from the slot after the burst for the reply, for
     `FAST_REPLY_MS` plus the longest burst on this rung less this one
     (`burst_max_us`), as silence grace before `LOST_PEER_SLOTS` counts.
     No reply by then ends `ERR_LOST_PEER` -- except on the first block's
     first attempt from a Leader that did not hold (9.6), which is sent
     again: a Follower whose own fit found flicker took none of it as
     light (9.7). The second silence ends `ERR_LOST_PEER`.
- **ACK** (CRC Good): move `c` past the block's chunks and zero the
  attempts; at the stream's end send Complete, else the next block.
  **Stepping back up**: on a rung below the start rung, after `up_after`
  ACKs in a row (`FAST_UP_ACKS` at first), the next block goes one rung
  faster, never past the start rung; the new rung travels in its Begin,
  and the Follower trains on it (its decoder's taps are another width's).
  A rung stepped up to is on trial until it has `FAST_UP_ACKS` ACKs; if it
  steps down first, `up_after` doubles. A NAK zeroes the count.
- **NAK** (CRC Bad): resend. After `FAST_TRIES` NAKs on one rung, the next
  slower; past F3, F3 again, until the attempts are spent.
- **Data Sync Error**: step the slot rung one slower (10.4: the duration,
  never the epoch), zero the NAK count, and resend with `SYNC_LEAD_IN`
  extra low: the Follower is still sending the rest of its error at the
  old rate.
- **Collect** (`collect_due`) before the block that follows an ACK or a
  NAK; not before a resend a Data Sync Error asked for.

The slot rung does not move for a NAK; the fast rung does not move for a
Data Sync Error.

**After the last ACK**: send Complete and wait for the Ack.

- Repeat Complete after more than `COMPLETE_RETRY_SLOTS` black slots in a
  row (a lit slot resets the count), at most `MAX_COMPLETE_RETRIES` times,
  and never later than `COMPLETE_REPEAT_MAX_SLOTS` after the last Complete.
- On the Ack: end `OK`. Budget spent: `ERR_LOST_PEER`.
- `LOST_PEER_SLOTS` does not apply while waiting for the Ack.

[Context](context.md). Tests: [T-transfer-end-to-end](tests.md#L830),
[T-complete](tests.md#L882).

## 14. Data phase: the Follower

The Follower shows low except when replying.

1. Before each block wait, the first included and after each reply, call
   `reader.check()`; on failure end `ERR_RING`.
2. Wait for Fast Begin; ignore low. It is matched on the decided symbols,
   or **trained**: when a slot decides low, if the last nine slots' values
   have the Begin's shape, the Follower takes its levels from them:

   ```
   values v0..v8, oldest first: highs v0 v3 v7, middles v1 v4 v6, lows v2 v5 v8
   black = the current black (the gap's, or the last training's)
   accept if: every low ≥ 0
              min(highs) - max(middles) ≥ MIN_GAP_EXACT
              min(middles) - black ≥ MIN_GAP_EXACT
              max(lows) < min(middles)
              span = median(highs) - black ≥ MIN_SWING
              median(middles) - black ≥ span/4 and median(highs) - median(middles) ≥ span/4
   levels  = Levels.fit(black, median(middles), median(highs))
   ```

   Every Begin trains.
3. Read `FAST_RUNG_BITS` slots, high 1, anything else 0: a low is not a
   framing failure. Then receive the burst (14.2) and reply ACK or NAK.
4. **Complete**, matched once a block is kept and outside a block (before
   that, `- - -` is the Announce): send the Complete Ack, keep answering
   repeats, and end once more than `LINGER_SLOTS` slots pass after the
   last Ack. End `OK` if the payload was received whole (14.2 step 4),
   else `ERR_DECODE`. The Leader sends Complete only once every chunk is
   ACKed.

**Refused.** Until it frames its first Begin, a Follower that saw the
Announce feeds each decided slot's 2 ms means to its beacon detector. A
match at `ANNOUNCE_FAST_HALF_MS` with a swing of at least half the
Announce's own, held for `2 x BEACON_FAST_HALVES x ANNOUNCE_FAST_HALF_MS`,
means the Leader refused the staircase: count a refusal; past
`CAL_RESTARTS` end `ERR_CONTRAST`, else send the staircase again (9.1). A
refused Follower that later ends `ERR_LOST_PEER` before any Begin ends
`ERR_CONTRAST` instead. Test: [T-refusal](tests.md#L642).

**Checking the Leader's rate.** After it sends a Data Sync Error, the
Follower measures the first message it then locks on, at crossing `c0`,
once the ring holds `RATE_CHECK_SLOTS` of its own slots past `c0`:

```
1 ms parts from c0, each lit if part - flicker ≥ T_low:
  fall = start of the first dark part; r1, r2 = starts of the next two lit parts after a dark one
skip if r2 not found, or |(r1 - c0) - (r2 - r1)| > 15% of (r2 - r1)
skip if 40% < (fall - c0) / (r1 - c0) < 60%        (Complete: lit half of every 2 slots)
slot = (r2 - r1) / 3
rung = the first in SLOT_LADDER within RATE_MATCH_PERCENT of slot
if rung exists and differs from its own: move to it, drop the block in progress,
   decide on from the next slot, and wait for the next message to lock
```

Only the Follower moves. Test: [T-rate-check](tests.md#L871).

**Asking again.** A Follower asks with a Data Sync Error, once
`RESEND_QUIET_SLOTS` slots in a row decide low, when more than
`STALL_SLOTS` pass without progress in `STEP_BLOCK` outside a block, or
when it missed a Begin (14.2 step 6). It steps slower once the error is
sent. A waiting request is re-armed, not repeated. A Begin matched while
it waits cancels it. At most `MAX_BLOCK_ATTEMPTS` requests before Complete; past
that a stall ends `ERR_LOST_PEER`.

[Context](context.md). Tests: [T-recovery](tests.md#L854),
[T-begin-training](tests.md#L796).

### 14.2 Fast blocks (Follower)

1. The burst is due at the start of slot `b + 1 + FAST_GAP_SLOTS`, `b`
   the last rung bit's slot. Find its first rise within `FAST_SEARCH_US`
   either side: `rise()` (section 6), against `T_low` plus the flicker,
   the threshold stepped every 300 µs. None: NAK (step 5).
2. Start the decoder at the rise less `EDGE_LAG_FAST_LIT_US` or
   `EDGE_LAG_FAST_DARK_US` by its light, trying that start and a quarter
   symbol either side. The flicker regressors run at the mains when the
   Follower's fit found the first harmonic stronger than the second, else
   at twice the mains; with no fitted harmonics (a steady room, or only
   the learner), there are none.
3. Pull `FAST_BIN_US` bins as the ring fills, at most `FAST_BINS`, and
   feed the decoder **nothing until the burst is over**: until step 5's
   test finds its end dark in the ring, or the bins reach the longest
   burst the rung allows past the anchor. Then feed it all of them; the
   first feed trains it. (A training outlasts the ring: fed mid-burst, the
   readings not yet binned are overwritten.) It trains or retunes
   (Appendix A); after a NAK the next block trains afresh.
4. On a block that passes: keep it by chunk (a chunk kept twice is kept
   once); once the chunks from 0 parse as a fast stream, that is the
   payload received. ACK no sooner than one slot after the burst's end,
   its length now known: from the first slot boundary after `end + slot`.
5. On a failure, NAK once the burst is over: from when it was due, 1 ms
   parts, flicker corrected, are lit at or above `black + (T_low -
   black) / 2`; the burst is over when the last lit part is followed by
   `2 x (MAX_LOWS + 2)` symbols of dark, or one slot after the longest
   burst could have ended, whichever is first. The NAK then goes as the
   ACK does, from that end.
6. **Missed Begins.** Count the lit slots decided in `STEP_BLOCK` outside
   a block; `advance()` (any progress: a Begin framed, a block kept, a
   reply sent) and asking reset the count. When a slot makes
   `RESEND_QUIET_SLOTS` low in a row with no part of their windows at or
   above `T_low` -- a burst still on air can decide low at the median --
   and the count is at least `UNFRAMED_LIT_SLOTS`, ask again (section 14,
   "Asking again").

The Follower decides no slot while a burst is due or on air.

Test: [T-fast-transfer](tests.md#L1095).

## 15. Mains flicker

LED room lights flicker at twice the mains (100 or 120 Hz) with
harmonics. [Context](context.md).

| Name | Value | Meaning |
|---|---|---|
| `FLICKER_MAINS` | 50, 60 | mains frequencies tried |
| `FLICKER_BASE_HARMONICS` | 4 | harmonics of the mains a fit takes first; also the steady check's threshold's |
| `FLICKER_HARMONICS` | 6 | harmonics a fit takes when the base ones leave more than `FLICKER_FIT_MAX_NOISE`: an LED's third, at 6x the mains, was 114 counts under a hard-flickering light |
| `FLICKER_FIT_MAX_NOISE` | 12 | counts a fit may leave unexplained |
| `FLICKER_FIT_SIGMAS` | 4 | a harmonic must clear its standard error by this |
| `FLICKER_FIT_PASSES` | 2 | refinement passes |
| `FLICKER_MIN_COUNTS` | 4 | smaller flicker is left alone: a fit whose strongest harmonic is under it finds the room steady |
| `FLICKER_KEEP_COUNTS` | 2 | in a room that flickers, the least harmonic a fit keeps |
| `FLICKER_MIN_SEEN` | 0.30 | a harmonic is fitted only if the samples keep this share of it |
| `FLICKER_STEADY_RMS` | 1.87 | under this rms about a line, the room is steady |
| `FLICKER_STEPS`, `FLICKER_ONE` | 320, 1024 | sine table steps per period, and its scale |
| `FLICKER_SPAN_US` | 250 | span rounding for the sin(x)/x table |
| `FLICKER_BUFFER` | 640 | fine samples kept |
| `FLICKER_TRACK_POINTS`, `FLICKER_TRACK_GAIN`, `FLICKER_TRACK_MAX_STEPS` | 8, 0.25, 16 | phase-lock update |
| `FLICKER_RATE_MEMORY`, `FLICKER_RATE_MIN_S`, `FLICKER_RATE_MIN_UPDATES`, `FLICKER_MAX_RATE` | 0.99, 0.30, 10, 32 | drift rate |
| `FLICKER_HZ`, `FLICKER_TAU_MS`, `FLICKER_MIN_POINTS` | (100, 120), 250, 2 | fallback learner |

- **Steady check first**: fit a straight line to the window; rms under
  `FLICKER_STEADY_RMS` means steady, and no fit runs.
- **The fit**: a straight line plus `FLICKER_BASE_HARMONICS` harmonics of
  each mains -- or, if that leaves more than `FLICKER_FIT_MAX_NOISE`,
  `FLICKER_HARMONICS` -- projected from the sine table and refined `FLICKER_FIT_PASSES`
  times, the line refitted in each pass on what the harmonics leave (over
  100 ms a sine is not independent of a ramp); the mains that explains
  more wins.
- **Refusals**: a residual over `FLICKER_FIT_MAX_NOISE` retries at half
  the window, then falls back to the learner. Drop a harmonic under
  `FLICKER_MIN_SEEN` gain, under `FLICKER_FIT_SIGMAS` standard errors, or
  under `FLICKER_KEEP_COUNTS`. If the strongest kept is under
  `FLICKER_MIN_COUNTS` (or none is kept): steady.
- **Correcting**: subtract the fitted flicker from every part, by table
  look-up. Applies to every sample from the fit on, calibration's included.
  The phase-lock threshold is `T_low` plus the flicker.
- **Tracking**: a phase lock on the table's offset. Each update fits the
  timing error that best explains the points the peer was dark for since
  the last, at least `FLICKER_MIN_POINTS` of them, and steps toward it.
  Updates come every `FLICKER_TRACK_POINTS` 2 ms samples on the Leader in
  the shared gap, and once per slot decided dark in the data phase, from
  that slot's window parts; a slot decided lit clears its points. The
  Leader also tracks while it sends, from the Follower's dark slots.
- **Drift rate**: a line through the updates' timings, each weighted by
  `FLICKER_RATE_MEMORY` per later update. It is used once their total
  weight is at least `FLICKER_RATE_MIN_UPDATES` and their weighted
  variance in time at least `(FLICKER_RATE_MIN_S / 2)²` -- a standard
  deviation of 0.15 s, about 0.52 s of evenly spaced updates.
- **Fallback learner**: sines at `FLICKER_HZ`, learnt from slots decided
  low.
- **Per sample: whole numbers from tables; no allocation.** Floats only in
  the fit. On MicroPython keep each per-sample function's frame at 11 words
  or fewer, or each call allocates.

Tests: [T-flicker-fit](tests.md#L953), [T-flicker-steady-skip](tests.md#L1004),
[T-flicker-correct](tests.md#L1016).

## 16. Giving up

- **Silence**: more than `LOST_PEER_SLOTS` low slots in a row ends
  `ERR_LOST_PEER`. Exempt: the Leader awaiting the Ack; the Follower
  lingering. Extra slots before it counts, ending at the next light: a
  Follower awaiting its first block, its start grace
  (9.6); a Leader awaiting a fast block's reply, its reply wait (13.1).
  And, ending at its first Begin, a Follower that found flicker, its
  `first_grace` (9.7).
- **Stalling**: more than `STALL_SLOTS` slots listening without progress (a
  pattern matched, a block kept, a reply acted on, something queued)
  ends `ERR_LOST_PEER`, except where a Follower may ask again (14).
- **Attempts**: one block sent `MAX_BLOCK_ATTEMPTS` times ends `ERR_CRC`.
- **Deadline**: `BLOCK_TIMEOUT_MS` per block, restarted on each kept block,
  checked always.
- **Clock**: `TRANSFER_MAX_US` ends `ERR_LOST_PEER`.

Every state has a way out. [Context](context.md). Test: [T-budgets](tests.md#L911).

## 17. Exit values

| Value | Meaning |
|---|---|
| `OK` | completed and verified |
| `ERR_NO_PEER` | no peer before `ANNOUNCE_TIMEOUT_MS` |
| `ERR_CONTRAST` | the staircase was refused more than `CAL_RESTARTS` times |
| `ERR_LOST_PEER` | peer silent or stalled, block deadline passed, or Complete never acknowledged |
| `ERR_CRC` | one block sent `MAX_BLOCK_ATTEMPTS` times |
| `ERR_DECODE` | Follower ended without the whole payload |
| `ERR_CANCELLED` | the caller called `fail(ERR_CANCELLED)` |
| `ERR_RING` | the ring failed its once-a-block check |

## 18. Implementation constraints

- MicroPython, no threads. The protocol is a state machine driven by
  `work(now)`; it never blocks.
- Phases: `ANNOUNCE` (Leader), `LISTEN` (Follower), `CAL_SEND`
  (Follower), `CAL_GAP`, `DATA`, `DONE`. Data sub-steps: `STEP_BLOCK`,
  `STEP_FINISH`, `STEP_FAST` (Leader, on air),
  `STEP_FAST_REPLY` (Leader, awaiting the reply), `STEP_FAST_RX` (Follower,
  reading a burst).
- Nothing per reading runs in Python: the ring's readings are summed by
  viper kernels. Per decided slot: one `parts()` query for its window,
  corrections and a median; a phase-lock scan, the Leader's lead-in watch
  and, before the first Begin, the Follower's refusal watch add their own
  queries.
- The protocol module references no badge globals; the reader is injected.
- Both badges must run the same version of this protocol. Nothing in it
  negotiates a version.

Test: [T-pure-module](tests.md#L184). Simulation: [context](context.md),
[T-sweep](tests.md#L1033).

## 19. Interface

Three pure Python modules, importable under CPython and MicroPython. None
references a badge global. `protocol` imports `math`, `time` and `array`; `reader` imports
`time` and `array`; `fastblock` (appendix A) imports `binascii`, `math`,
`array`, and `micropython` where it exists. The tests import them as `P`,
`R` and `FB`.

Clock arithmetic uses MicroPython's `time.ticks_us()`, `time.ticks_diff(a,
b)` (a - b, wrap-safe) and `time.ticks_add(a, b)`. Under CPython the tests
add plain-integer versions to `time` before importing.

### 19.1 Module `protocol`

**Names and values**

- Every constant in sections 3 and 15, by those names.
- Symbols: `HIGH = "-"`, `MID = "_"`, `LOW = " "`.
- `SCHEME_BEACON`, `SCHEME_BACKLIGHT`, and `LEVEL_SCHEMES`: a dict from
  scheme to a dict from symbol to backlight value (section 4).
- Patterns as strings (section 11): `FAST_BEGIN`, `CRC_GOOD`, `CRC_BAD`,
  `SYNC_ERROR`, `COMPLETE`, `COMPLETE_ACK`, and
  `COMPLETE_ACK_LATE_TAIL = "_- __-"`.
- `STAIRCASE = (LOW, HIGH, MID)`, the staircase's order (9.1).
- Exit values (section 17): strings equal to their names, `OK = "OK"`.
- Phases: `PHASE_ANNOUNCE` 0, `PHASE_LISTEN` 1, `PHASE_CAL_SEND` 2,
  `PHASE_CAL_GAP` 3, `PHASE_DATA` 4, `PHASE_DONE` 5.

**Functions**

| Call | Returns |
|---|---|
| `beacon_field(elapsed_ms, half_ms)` | `LOW` for the first half period, then `HIGH`, alternating |
| `staircase_field(elapsed_ms, order, reps, open_ms, plateau_ms)` | the staircase's symbol at that time; `LOW` in both gaps |
| `staircase_ms(reps, open_ms, plateau_ms, close_ms)` | `open + reps x 3 x plateau + close` |
| `gap_separable(gap, noise_a, noise_b, min_gap=MIN_GAP_EXACT)` | section 9.3's check |
| `exact_window(duration_us, settle_us, guard_us)` | `(open, close)` in µs from the slot's start, section 10.2's rule before its quarter-slot fallback |
| `staircase_highs(values, levels)` | high steps in a measured staircase (9.5) |
| `bits_symbols(value, width)` | `width` bits of `value`, MSB first, as `HIGH`/`MID` |
| `fast_stream(fields)` | a fast stream (12) |
| `parse_fast_stream(data)` | its fields, or `None` if cut short or with bytes left over |
| `fast_block(fast, stream, chunk, periods)` | the block from `chunk` (13.1); `fast` is the fast block's module |
| `burst_max_us(fast, periods)` | the longest burst on that rung, worst-case stuffing, in µs |

**Classes**

- `BeaconDetector()`: `push(now_ms, value)` adds a coarse sample;
  `matches(half_ms, window_ms=None)` applies section 8 over the last
  `window_ms` (default `BEACON_FAST_HALVES x half_ms`); `low()` and
  `high()` are section 8's black and white; `lo`, `hi` the last window's
  range.
- `Levels()`: `fit(black, grey, white)` sets `black`, `grey`, `white`,
  `t_low`, `t_high` and `band` (section 9.4); `classify(value)` returns a
  symbol, with hysteresis.
- `StaircaseReader(limit=512, smooth=0)`: `feed(now_ms, value)` adds a 2 ms
  sample and returns `True` once the closing gap is found; `ended_at` is
  its start (ms); `levels()` returns a `Levels` with `noise_black`,
  `noise_grey`, `noise_white` set, or `None` if section 9.2 refuses. The
  Leader's is `StaircaseReader(LEADER_CAL_LIMIT, LEADER_CAL_SMOOTH)`.
- `SlotClock()`: in µs. `start(now, index)`,
  `set_index(index)` (clamped to the ladder), `slow_down()` (one rung
  slower, stops at index 0), `start_of(n)`, `number(now)`; attributes
  `epoch`, `duration`, `index`.
- `FlickerFilter()`: `fit(times_us, values, spans_us)` returns
  `"flicker"`, `"steady"` or `None` (refused); `mains` is 50 or 60 once
  fitted; `amount(now_us, span_us)` is the flicker to subtract from a mean
  over `span_us` centred at `now_us`, in whole counts.
- `Transfer(leader, payload=None, reader=None, fast=None)`: one badge's
  transfer. `leader` is `True` or `False`; `payload` a list of `bytes`
  (Leader only); `reader` the badge's `RingReader`, which the caller
  starts. `fast` is the
  fast block's module (appendix A), which every transfer needs; a listener
  that only decides slots, as Dev Mode's tools run one, can do without.
  - `field_at(t_us)`: the symbol to show; `scheme`: the table to show it in.
  - `due_us()`, `work(t_us)`: section 7.
  - `fail(code)`: ends the transfer with `code`, except that a Follower
    refused before its first Begin ends `ERR_CONTRAST` for
    `ERR_LOST_PEER` (section 14, "Refused").
  - Attributes: `result` (`None` until done), `phase`, `scheme`, `clock`
    (a `SlotClock`), `levels`, `lit`, `pair_lit`, `pair_flicker` (the
    Leader's hold), `first_grace` (the Follower's, 9.7), `received` (the Follower's payload, a list of `bytes`, once whole;
    else `None`), `collect_due`, `cal_refusals`, `fast_rung`, `fast_periods`,
    `fast_burst` (13.1), `fast_chunks` (the Follower's, by chunk).
  - `burst_started(t_us)`: the shell started the burst at `t_us`.
  - Steps the tests call directly: `step_announce(now_ms, value)`,
    `step_listen(now_ms, value)`, `step_cal_gap(now_ms, value)`,
    `start_data(epoch_ms, index)`, `begin_levels()` (14 step 2, from 9
    values in `rx_values`, a ring indexed by `rx_n`), `leader_rx()`,
    `follower_rx()`, `data_field(t_us)`, `rebase(boundary_us,
    crossing_us)`, `check_leader_rate(newest_us)` (with `verify_rate`,
    `rate_from` and `rate_heals`), `fast_accept(chunk, payload)` (14.2
    step 4), `fast_flicker_mhz()` (14.2 step 2; 0 before a fit).

### 19.2 Module `reader`

Times are µs since `start()`. The ring holds raw readings (counts / 16);
queries return counts.

- `HEALTH_ADC = 1`, `HEALTH_LOST = 2`, `HEALTH_DMA = 4`: health bits.
  `RING_SAFE_US = 10000`, `RING_DRIFT_US = 100`, `EDGE_BIN_US = 100`,
  `COUNTS_PER_RAW = 16`.
- A ring is what `RingReader` calls, the badge's DMA ring (`fastadc.Ring`)
  or a test's: `samples` (a power of two), `period_us`, `start()`,
  `stop()`, `position()` (entries written), `health()` (returns and clears
  `HEALTH_LOST`), `sum(start, n)`, `parts(start, part, n, out)` (sums of
  `n` runs of `part` entries), `rise(start, bin_len, bins, thr, thr_step)`
  (the first bin `b` where bins `b` and `b + 1` each reach their own
  threshold, `thr[b // thr_step]` and `thr[(b + 1) // thr_step]`, or -1).
  `ref_sum`, `ref_parts` and `ref_rise` are the kernels' pure twins.
- `RingReader(ring, ticks_us=None, per_entry=1, usable_us=None)`:
  `ticks_us` is the clock (default `time.ticks_us`); `per_entry` the
  readings one entry sums; `usable_us` the history (default the ring less
  `RING_SAFE_US`). `start()`, `stop()`, `now_us()`, `pump(until_us)`,
  `check()` (section 6; `lost` counts re-anchors), `newest_us()`,
  `parts(t0, t1, n, out, times=None)`, `mean(t0, t1)`,
  `rise(t0, t1, thr, n_thr, step_us)` (`thr` a list of `n_thr` thresholds
  in counts, one per `step_us`), `bins(t0, n, out, at)` (`out[at + j]` = the sum
  of the raw readings, counts / 16, in the `j`-th `EDGE_BIN_US` from `t0`,
  for `j < n`: the fast decoder's input).

## Appendix A: the fast block

The block sections 13.1 and 14.2 send, as a third module, `fastblock`
(pure, like the other two).
 [Context](context.md).

- **Symbol clock**: a common 327.68 µs PWM period. Each badge sets its PWM
  divider to `sys_hz / 200 MHz` (v2 1.0, v3 1.25); a clock no divider
  reaches within 0.1% declares none. DMA paced by the PWM wrap writes one
  duty word per period. Restore divider and duty after every block.
- **F0**: 2-level PAM, 2 periods a symbol (0.66 ms). F1-F3: 3, 4 and 6
  periods. The Leader picks the rung (13.1) and sends its index.
- **Block** (after the slow rung's lead-in and Begin):

  | Part | Content |
  |---|---|
  | preamble | 66 known symbols (xorshift32 from 0x2545F493, runs of the low ≤ 4, last high); rows 2-65 train the equaliser |
  | frame | chunk index (u16) · length (u16) · payload · CRC-32 (u32), big-endian, MSB first; a 1 stuffed after every 4 lows when another frame bit follows |
  | tail | 2 high |

- **CRC-32**: IEEE 802.3 (`binascii.crc32`) over header and payload.
- **Payload per block**: F0 128 bytes, F1 64, F2 and F3 32 (1 s on air,
  worst-case stuffing). `chunk` is the block's first 32-byte chunk.
- **Receiver**: bins the ring at 100 µs; a linear equaliser over four
  symbols' parts plus sin/cos regressors at the light's flicker frequency
  (14.2 step 3) and its harmonic, tracked by LMS (μ = 41/2048), trained on
  the preamble. A preamble whose two levels sit less than half a level
  apart is refused.
- **Training once a rung**: the first block on a rung **trains** -- all 17
  weights fitted by least squares to the preamble at each of the given
  starts, the best kept. Each later block on the same rung, with the same
  flicker setting, **retunes**: the 12 part weights kept, the start
  searched at twice the given resolution with those weights and a constant
  alone, then the 4 regressors and the constant refitted at that start.
  A retune is refused, and the block trained, when its fit's squared error
  exceeds the training's by more than `RETUNE_SSE_SLACK` (0.6), or its
  levels do not separate. A block the Follower NAKs makes the next one
  train (`Decoder.forget()`).

Test: [T-fast-block](tests.md#L1069).
