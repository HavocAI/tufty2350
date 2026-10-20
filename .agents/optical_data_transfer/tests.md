# Optical Data Transfer Protocol: tests

Verification for the specification
([spec](spec.md#L1)). Each test
says what it checks, then gives a script, a list of cases to write, or
both. Scripts call the interface in spec section 19
([interface](spec.md#L883)) and nothing else.

A script that passes shows the implementation agrees with the spec on that
point. None of these tests says whether the hardware constants hold
between two real badges; the context records what has been measured
([open questions](context.md)).

## Running the tests

### Setup

- CPython 3.8 or later. No other packages.
- Put `protocol.py`, `reader.py` and `fastblock.py` in one directory and
  run from it, or add it to `sys.path`.
- Run each script on its own. Each prints `pass` or raises.
- Optional: the MicroPython unix port, for T-flicker-correct's allocation
  check.

### Script prelude

Every script starts with this.

```python
import sys
import time
sys.dont_write_bytecode = True
if not hasattr(time, "ticks_us"):          # CPython: MicroPython's clock calls
    time.ticks_ms = lambda: int(time.monotonic() * 1000)
    time.ticks_us = lambda: int(time.monotonic() * 1000000)
    time.ticks_diff = lambda a, b: a - b
    time.ticks_add = lambda a, b: a + b
import protocol as P
import reader as R
```

### Pair driver

Scripts marked "with the pair driver" also need this. It steps two
`HeldTransfer`s (T-reader-ring) on one clock every 100 µs, each reading
the other's backlight through a simple model of the light (a dark room's
black of 32 counts, an 80-count swing, the v2 backlight curve and the
LED's threshold, and ~2 counts of noise), and plays the Leader's fast
bursts at their own symbol rate, as the shell's pacer does. It has no
sensor lag and no processing costs; the link simulator below has both.

```python
import random
import fastblock as FB

def seen(value, rng, black=32, swing=80, sd=2):
    """Counts one badge reads for the other's backlight `value`."""
    duty = max(0.0, value ** 2.8 - 0.11) / 0.89
    return max(0, int(black + swing * duty + rng.gauss(0, sd)))

def burst_level(t, us):
    """What a Leader shows at `us` while its burst is on air, else None."""
    if t.fast_symbols is None or not t.fast_start <= us < t.fast_end:
        return None
    i = (us - t.fast_start) * 1000 // (t.fast_periods * FB.COMMON_NS)
    return P.HIGH if t.fast_symbols[i] else P.LOW

def run_pair(payload, seed=1, black=32, limit_ms=400000, tamper=None,
             leader=True, follower=True):
    """Two badges facing. `tamper(us, lead, follow, to_leader,
    to_follower)` may replace what each reads. An absent badge (False) leaves its peer reading the
    room's black."""
    rng = random.Random(seed)
    lead = HeldTransfer(True, payload) if leader else None
    follow = HeldTransfer(False) if follower else None
    us = 0
    while us < limit_ms * 1000:
        shown = []
        for me in (lead, follow):
            if me is None or me.result:
                shown.append(0.0)
                continue
            if me.fast_burst is not None and us >= me.fast_burst[0]:
                me.burst_started(me.fast_burst[0])
            level = burst_level(me, us) if me.leader else None
            shown.append(P.LEVEL_SCHEMES[me.scheme][level or me.field_at(us)])
        to_l, to_f = seen(shown[1], rng, black), seen(shown[0], rng, black)
        if tamper:
            to_l, to_f = tamper(us, lead, follow, to_l, to_f)
        for me, light in ((lead, to_l), (follow, to_f)):
            if me is not None and not me.result:
                me.reader.add(us, light)
                if us >= me.due_us() or us % 1000 == 0:
                    me.work(us)
        if all(b.result for b in (lead, follow) if b):
            break
        us += 100
    # I7: the Leader reports OK only if the Follower has the payload.
    if lead and follow and lead.result == P.OK:
        assert follow.received == list(payload)
    return lead, follow, us // 1000
```

## Link simulator

The end-to-end tests marked "in the link simulator" need more than the pair
driver: sensor lag, processing costs, collections, flicker and clock error.
Build it once:

1. One shared timeline in µs. Each badge has its own clock (start offset,
   crystal error) and its own `RingReader` over a test ring (`ListRing`,
   below) of 4096 entries of 100 µs, `per_entry = 5`.
2. Each badge runs section 7's loop. Its `pump()` advances the shared
   timeline, filling both rings from the light model, and charges the
   badge's costs as time it cannot act in.
3. The light model, its sensor and its costs are those in the context's
   simulation table ([simulation](context.md)).
4. After every tick, assert I1, I2, I4 and I7, and on finishing I8
   ([link invariants](context.md)). Record the first
   violation with the time and both badges' state.
5. A run ends when both badges have a result, or after 300 s.

A **row** is 8 seeds x 6 scenarios x 3 payloads (144 runs). Scenarios and
payloads are in the context's simulation section.

## Constants and boundaries

### T-constants-match

Checks: every constant in spec sections 3 and 15 has the spec's value.

```python
SPEC = {
    "SENSOR_STEP": 16, "MIN_SWING": 48, "MIN_GAP_EXACT": 16,
    "GAP_SIGMAS": 4, "LIT_BLACK_COUNTS": 64, "LOW_BACKLIGHT": 0.5,
    "MID_BACKLIGHT": 0.82, "BEACON_LOW_BACKLIGHT": 0.0,
    "SLOT_LADDER": (32, 25, 20, 16, 13, 10, 8),
    "EXACT_LIT_INDEX": 6, "EXACT_INDEX": 3,
    "SLOT_MIN_MS": 6, "EXACT_DARK_START_MS": 16, "SETTLE_LIT_US": 2500,
    "SETTLE_DARK_US": 5000, "GUARD_LIT_US": 1000, "GUARD_DARK_US": 2000,
    "OPEN_PERCENT": 40, "BACKLIGHT_GUARD_PERCENT": 15,
    "WINDOW_MIN_US": 2000, "PART_MAX_US": 4000, "PARTS_MIN": 5,
    "PARTS_MAX": 32, "EDGE_LAG_EXACT_LIT_US": 950,
    "EDGE_LAG_EXACT_DARK_US": 1650, "FINE_MS": 2,
    "DUE_MARGIN_US": 100, "TRANSFER_MAX_US": 1006632960,
    "ANNOUNCE_FAST_HALF_MS": 14, "BEACON_TOLERANCE": 0.2,
    "BEACON_FAST_HALVES": 6, "BEACON_WINDOW_MS": 1000,
    "ANNOUNCE_TIMEOUT_MS": 60000, "PLATEAU_FAST_MS": 20,
    "CAL_REPS_FAST_LIT": 1, "CAL_REPS_FAST": 2,
    "CAL_OPEN_FAST_MS": 40, "CAL_CLOSE_FAST_MS": 70,
    "CAL_CLOSE_FAST_SPARE_MS": 20, "CAL_GAP_FAST_MS": 110,
    "LEADER_CAL_LIMIT": 160, "LEADER_CAL_SMOOTH": 10,
    "LEADER_SETTLE_MS": 100, "LEADER_FIT_SKIP_MS": 10,
    "FOLLOWER_FIT_START_MS": 70, "FOLLOWER_CHECK_MS": 60,
    "FLICKER_FAST_FIT_MS": 100, "CAL_GAP_FAST_FLICKER_MS": 500,
    "FAST_START_GRACE_MS": 100, "HOLD_MARGIN_SLOTS": 4,
    "PROVISIONAL_GREY_PERCENT": 45, "CAL_RESTARTS": 1,
    "LIT_STEPS_MAX_FAST": 1, "MIN_HELD_FAST": 5, "CAL_FAST_MIN_SAMPLES": 24,
    "STEP_SAMPLES": 3, "CAL_CAUGHT_UP_US": 4000, "CAL_PENDING_MAX_MS": 250,
    "LOCK_SKIP_US": 500, "LOCK_HOLD_US": 3000,
    "RESYNC_LEAD_IN": 4, "SYNC_LEAD_IN": 9,
    "MAX_BLOCK_ATTEMPTS": 48, "LOST_PEER_SLOTS": 16, "STALL_SLOTS": 64,
    "RESEND_QUIET_SLOTS": 3, "BLOCK_TIMEOUT_MS": 300000,
    "COMPLETE_RETRY_SLOTS": 12, "COMPLETE_REPEAT_MAX_SLOTS": 18,
    "MAX_COMPLETE_RETRIES": 3, "LINGER_SLOTS": 20, "WATCH_SLOTS": 2,
    "MAX_PATTERN": 16, "FLICKER_MAINS": (50, 60), "FLICKER_HARMONICS": 6, "FLICKER_BASE_HARMONICS": 4,
    "FLICKER_FIT_MAX_NOISE": 12, "FLICKER_FIT_SIGMAS": 4,
    "FLICKER_FIT_PASSES": 2, "FLICKER_MIN_COUNTS": 4, "FLICKER_KEEP_COUNTS": 2,
    "FLICKER_MIN_SEEN": 0.3, "FLICKER_STEPS": 320, "FLICKER_ONE": 1024,
    "FLICKER_SPAN_US": 250, "FLICKER_BUFFER": 640,
    "FLICKER_TRACK_POINTS": 8, "FLICKER_TRACK_GAIN": 0.25,
    "FLICKER_TRACK_MAX_STEPS": 16, "FLICKER_RATE_MEMORY": 0.99,
    "FLICKER_RATE_MIN_S": 0.3, "FLICKER_RATE_MIN_UPDATES": 10,
    "FLICKER_MAX_RATE": 32, "FLICKER_HZ": (100, 120), "FLICKER_TAU_MS": 250,
    "FLICKER_MIN_POINTS": 2,
}
for name, value in SPEC.items():
    assert getattr(P, name) == value, (name, getattr(P, name), value)
assert abs(P.QUANTISATION_SD - 16 / 12 ** 0.5) < 1e-9
assert abs(P.FLICKER_STEADY_RMS - 1.87) < 0.01
print("pass")
```

### T-pure-module

Checks: no module names a badge global or imports a badge module.
`fastblock` may import `micropython`, inside a `try`: on CPython it falls
back to plain Python.

```python
import ast
BADGE = {"badge", "screen", "display", "color", "font", "rtc", "State",
         "image", "io", "machine", "rp2"}
ALLOWED = {"protocol": {"math", "time", "array", "reader"},
           "reader": {"time", "array"},
           "fastblock": {"binascii", "math", "array", "micropython"}}
for name, allowed in ALLOWED.items():
    tree = ast.parse(open(name + ".py").read())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not names & BADGE, (name, names & BADGE)
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                for a in n.names}
    imported |= {n.module for n in ast.walk(tree)
                 if isinstance(n, ast.ImportFrom)}
    assert imported <= allowed, (name, imported - allowed)
print("pass")
```

Cases:

- All three modules import in a fresh interpreter with only that
  directory on the path.
- A payload of any number of fields of any bytes round-trips: the
  protocol attaches no meaning to them.

## Symbols and levels

### T-level-tables

Checks: every symbol maps to a backlight value; beacons use off and full;
data uses 0.5, 0.82 and 1.0.

```python
B = P.LEVEL_SCHEMES[P.SCHEME_BEACON]
D = P.LEVEL_SCHEMES[P.SCHEME_BACKLIGHT]
assert (B[P.HIGH], B[P.LOW]) == (1.0, 0.0)
assert (D[P.HIGH], D[P.MID], D[P.LOW]) == (1.0, 0.82, 0.5)
for table in (B, D):
    assert all(0.0 <= v <= 1.0 for v in table.values())
print("pass")
```

### T-no-pixels

Checks the caller (spec section 5), with the badge's display stubbed to
count calls.

Cases:

- Before the first `work()`: one fill of the whole screen in
  rgb(255, 255, 255) (not a tinted palette white) and one present.
- During the transfer: no draw, no present, no screen-mode switch,
  whatever symbols are shown.
- The backlight is set only when the shown value changes.
- `run()`'s clear is off during the transfer; on leaving, the clear and
  the normal backlight are restored.

## Reader

### T-reader-ring

Checks: times are µs from `start()`; queries return means in counts; a
part outside the history reads -1; `rise()` finds and interpolates an
edge; `check()` re-anchors on lost readings and fails on any other fault.

`ListRing(samples, period_us)` is the tests' ring, not the protocol's: an
array whose entries the test `push()`es, as the DMA writes them, with the
ring methods of spec section 19.2 over `reader`'s `ref_*` kernels.
`HeldReader` is a `RingReader` over one that `add(t_us, value)` fills, each
value held until the next, one entry per 100 µs; a `HeldTransfer` is a
`Transfer` given `fastblock`, that makes one when given no reader, and its
`step(now_ms, light)` adds the reading, calls `work` and returns
`field_at` -- the tests' way to feed a transfer one reading at a time.

```python
clock = [0]
ring = ListRing(1024, 20)                     # 1024 entries, 20 us each
r = R.RingReader(ring, lambda: clock[0])
r.start()

def write_until(t_us, value):                 # the "DMA": an entry per 20 us
    while ring.written * 20 < t_us:
        ring.push(value)
    clock[0] = t_us

write_until(4000, 2)                          # raw 2 = 32 counts
write_until(8100, 7)                          # raw 7 = 112 counts
out = [0] * 4
r.parts(0, 8000, 4, out)
assert out == [32, 32, 112, 112], out
assert r.mean(2000, 6000) == 72
assert r.now_us() == 8100
write_until(40000, 2)                         # 0-8000 has left the history
r.parts(0, 8000, 2, out)
assert out[:2] == [-1, -1]
write_until(41010, 7)                         # a rise at 40000
t = r.rise(39000, 41000, [72], 1, 2000)
assert 39950 <= t <= 40100, t
ring.fault = R.HEALTH_LOST
assert r.check() and r.lost == 1              # lost readings: re-anchor, go on
ring.fault = R.HEALTH_DMA
assert not r.check()                          # any other fault: fail
print("pass")
```

Cases:

- `now_us()` keeps counting across a wrap of the underlying tick counter.
- `rise()` ignores a single lit bin; it needs two in a row.
- `HeldReader.add()` holds each value until the next.

### T-reader-check

Checks the anchor rule (spec section 6).

Cases:

- A fresh anchor within `RING_DRIFT_US` of the one in use is not adopted.
- One that differs by more is adopted and counted.
- A health fault other than lost readings makes `check()` return `False`,
  and the transfer ends `ERR_RING` (T-ring-refusal).

### T-ring-verify

Checks the badge's ring, on the badge. No host test can.

Cases:

- `verify()` passes on a ring whose cache clean and uncached reads work.
- It fails if the clean does nothing, or leaves lines dirty.
- Record the result and the firmware version (Dev Mode M1).

### T-ring-refusal

Checks: no ring, no transfer; a ring fault ends the transfer.

```python
class BrokenRing(ListRing):
    def health(self):
        return R.HEALTH_DMA
for leader in (True, False):
    clock = [0]
    t = P.Transfer(leader, [b"x"], R.RingReader(BrokenRing(4096, 100),
                                                lambda: clock[0]))
    t.reader.start()
    assert t.reader.check() is False
print("pass")
```

Cases:

- A badge whose ring cannot be made or verified refuses to start a
  transfer, says why on screen, and offers no retry.
- A Leader whose `check()` fails before a block ends `ERR_RING`.
- A Follower whose `check()` fails before a block wait ends `ERR_RING`.

## The transfer loop

### T-loop

Checks the caller's loop (spec section 7).

Cases:

- The reader starts at 0 µs when the transfer starts and runs until it
  ends; the ring is stopped on every exit, and stopping never raises.
- `gc.collect()` runs only between protocol calls, and only when
  `collect_due` is set.
- B, read every 50 ms inside the loop, ends the transfer
  `ERR_CANCELLED`.
- `due_us()` never moves backwards between two calls with no `work()`
  between them.

## Discovery

### T-beacon-field

Checks: a beacon is low for the first half period, then high,
alternating.

```python
assert [P.beacon_field(t, 40) for t in (0, 39, 40, 79, 80)] == \
    [P.LOW, P.LOW, P.HIGH, P.HIGH, P.LOW]
print("pass")
```

### T-beacon-detect

Checks: the Announce is found from 2 ms samples; other periods, weak
swings, steady light and noise are not.

```python
import random

def feed(fn, ms=600, step=P.FINE_MS):
    d = P.BeaconDetector()
    for t in range(0, ms, step):
        d.push(t, fn(t))
    return d

def square(half, swing=80):
    return lambda t: 40 + (swing if (t // half) % 2 else 0)

A = P.ANNOUNCE_FAST_HALF_MS
assert feed(square(A)).matches(A)
for half in (5, 8, 10, 22, 40):                        # flicker, old beacons
    assert not feed(square(half)).matches(A), half
assert not feed(square(A, swing=32)).matches(A)        # under MIN_SWING
assert not feed(lambda t: 100).matches(A)              # steady
d = feed(square(A))
assert (d.low(), d.high()) == (40, 120)                # black and full
rng = random.Random(1)
hits = total = 0
for _ in range(100):
    d = P.BeaconDetector()
    for k in range(300):
        d.push(k * P.FINE_MS, 40 + rng.randrange(161))
        total += 1
        hits += d.matches(A)
assert hits < total * 0.002, hits                      # noise rarely
print("pass")
```

Cases:

- The tolerance band, 11.2-16.8 ms, is clear of a once-a-cycle light's
  8.3-10 ms halves.
- A quiet window with one outlier sample is not a beacon.

### T-discovery-roles

Checks the roles (spec section 8). With the pair driver.

```python
l, _, ms = run_pair([b"x"], follower=False)
assert l.result == P.ERR_NO_PEER
assert P.ANNOUNCE_TIMEOUT_MS <= ms < P.ANNOUNCE_TIMEOUT_MS + 1000
_, f, ms = run_pair([b"x"], leader=False)
assert f.result == P.ERR_NO_PEER

f = HeldTransfer(False)                 # holds black, then answers
assert f.phase == P.PHASE_LISTEN and f.field_at(0) == P.LOW
now = 0
while f.phase == P.PHASE_LISTEN:
    f.step_listen(now, 120 if (now // P.ANNOUNCE_FAST_HALF_MS) % 2 else 40)
    now += P.FINE_MS
assert f.phase == P.PHASE_CAL_SEND and f.scheme == P.SCHEME_BACKLIGHT
assert (f.levels.black, f.levels.white) == (40, 120)
assert not f.lit and not f.pair_lit                  # black 40 < 64
print("pass")
```

Cases:

- The Leader shows the Announce from its first call, in the beacon table.
- The Follower answers no other period (22, 40, 100 ms halves).
- A Follower whose Announce black reads 64 or more is LIT, and so is the
  pair; 63 is dark.
- The provisional middle is `black + (white - black) x 45 / 100`.

## Calibration

### T-staircase-shape

Checks: the Follower's staircase is a 40 ms opening, `reps` x (low, high,
middle) in 20 ms steps, then the 110 ms shared gap.

```python
f = [P.staircase_field(t, P.STAIRCASE, 1, P.CAL_OPEN_FAST_MS, 20)
     for t in (0, 39, 40, 60, 80, 99, 100)]
assert f == [P.LOW, P.LOW, P.LOW, P.HIGH, P.MID, P.MID, P.LOW]
assert P.staircase_ms(1, P.CAL_OPEN_FAST_MS, 20, P.CAL_GAP_FAST_MS) == 210
assert P.staircase_ms(2, P.CAL_OPEN_FAST_MS, 20, P.CAL_GAP_FAST_MS) == 270
print("pass")
```

Cases:

- `reps` is 1 when the Follower is LIT, else 2.
- The order is always `STAIRCASE`; nothing travels in it.
- The Follower sets `collect_due` when the body ends.

### T-staircase-levels

Checks: the Leader's reader recovers black, middle and white, and times
the closing gap to the sample; refuses too little swing, two levels, and a
middle near either end.

```python
import random

def read(counts, reps=2, lead_ms=150, seed=1, sd=2):
    """A Follower's staircase as the Leader's reader sees it: 2 ms samples,
    read from long before the staircase."""
    rng = random.Random(seed)
    b, m, w = counts
    level = {P.LOW: b, P.MID: m, P.HIGH: w}
    s = P.StaircaseReader(P.LEADER_CAL_LIMIT, P.LEADER_CAL_SMOOTH)
    total = lead_ms + P.staircase_ms(reps, P.CAL_OPEN_FAST_MS, 20,
                                     P.CAL_GAP_FAST_MS)
    for t in range(0, total, 2):
        f = P.LOW if t < lead_ms else \
            P.staircase_field(t - lead_ms, P.STAIRCASE, reps,
                              P.CAL_OPEN_FAST_MS, 20)
        if s.feed(t, level[f] + rng.gauss(0, sd)):
            break
    return s

s = read((32, 75, 112))
L = s.levels()
assert abs(L.black - 32) <= 4 and abs(L.grey - 75) <= 4
assert abs(L.white - 112) <= 4
gap = 150 + P.CAL_OPEN_FAST_MS + 120                  # the gap's start
assert abs(s.ended_at - gap) <= 2, s.ended_at
for reps in (1, 2):
    assert read((32, 75, 112), reps=reps).levels() is not None
assert read((32, 60, 72)).levels() is None             # under MIN_SWING
assert read((32, 32, 112)).levels() is None            # two levels
assert read((32, 44, 112)).levels() is None            # middle near black
assert read((32, 102, 112)).levels() is None           # middle near white
print("pass")
```

Cases:

- A once-a-cycle 50 Hz flicker of 44 counts peak to peak on a steady room,
  with no staircase, never ends a reading: the means do not swing
  `MIN_SWING`.
- With that flicker on a staircase, the gap is still found.

### T-separability

Checks: a gap passes only at `MIN_GAP_EXACT` or more and at 4 x the
noisier side's noise, floored at `QUANTISATION_SD`.

```python
assert not P.gap_separable(16, 1, 1)     # floor: 4 x 4.62 = 18.5
assert P.gap_separable(19, 1, 1)
assert not P.gap_separable(30, 8, 1)     # 4 x 8 = 32
assert P.gap_separable(32, 8, 1)
assert not P.gap_separable(15, 0, 0)     # MIN_GAP_EXACT
print("pass")
```

Cases:

- Noise counts only settled samples: a staircase with fast transitions
  measures the same noise as one with none.
- A level with too few settled samples cannot be judged: `levels()` is
  `None`.
- A five-step swing (80 counts) with 2-8 counts of noise calibrates.
- A swing of 64 with gaps of 27-30 (an obstructed sensor's) calibrates.
- A swing drowned in noise is refused.

### T-thresholds

Checks: thresholds at the midpoints; a value within 5% of the span of a
threshold keeps the last level only if that level is one of the
threshold's two (spec 9.4).

```python
L = P.Levels()
L.fit(32, 64, 112)
assert (L.t_low, L.t_high, L.band) == (48, 88, 4)
assert [L.classify(v) for v in (40, 80, 100)] == [P.LOW, P.MID, P.HIGH]
assert L.classify(86) == P.HIGH          # inside the band: keeps HIGH
assert L.classify(40) == P.LOW
assert L.classify(86) == P.MID           # T_high's band never keeps LOW
assert L.classify(50) == P.MID           # T_low's band keeps MID
print("pass")
```

### T-flags

Checks the light flag (spec section 9.5).

Cases:

- A Follower whose Announce black reads 64 or more sends one repetition,
  else two; each reads back as sent (`LIT_STEPS_MAX_FAST`).
- The Leader adopts the Follower's light whatever its own: a Leader whose
  staircase black reads 100 (LIT) reading a two-repetition staircase
  starts on 16 ms.
- A staircase that loses its only high step reads as dark, never as lit
  with a missing level.
- No tier travels.

### T-calibration-timing

Checks the timing rules (spec sections 9.1, 9.2, 9.6 and 9.7).

Cases:

- The closing gap is found 50 ms in, before 70 ms.
- Neither a 20 ms step of black nor the Announce's half (16.8 ms at most)
  is taken for the closing gap.
- The Leader settles `LEADER_SETTLE_MS` into the gap, before the epoch,
  fitting on `[gap + 10, gap + 100]`.
- The Follower's window starts after the Leader has stopped announcing
  (`FOLLOWER_FIT_START_MS ≥ 50 + 10 + 5`); its steady check ends before the
  earliest Begin (`110 + 4 x 8` ms); its fit fits inside the hold.
- The Follower decides no slot before its fit is done.
- The check's median becomes the provisional black.
- The staircase starts on the first caught-up pass, or 250 ms after
  entering the phase.

### T-epoch-and-hold

Checks the epoch and the flicker hold (spec section 9.6).

Cases:

- The Leader's epoch is the gap's start plus 110 ms, to the sample in
  steady light.
- With its own fit finding flicker, the Leader's first block starts
  `ceil((500 - 110) / slot)` slots late; the epoch does not move.
- A Leader that starts the data phase 10 slots late still puts its Begin
  on slot `1 + RESYNC_LEAD_IN + hold`: the hold is trimmed.
- A Follower always waits the hold plus 100 ms before counting silence.
- A hold the Follower did not expect costs a wait, not the transfer.
- None where the Follower found flicker costs the Leader's reply wait:
  the Leader, which did not hold, sends its first block again after a
  silent reply wait, once (spec 13.1 step 4); one that held never does.
  In the link simulator, `lit47` with the Leader's hold forced off
  delivers on seeds 1-8.
- A Follower that found flicker keeps `first_grace` (spec 9.7), more than
  the Leader's reply wait, until it frames a Begin; one that found the
  room steady has none.
- The start's silence grace ends at the first lit slot decided; a split
  after that is caught within `LOST_PEER_SLOTS`.

### T-calibration-end-to-end

Checks: a pair calibrates, agrees its light and starts on its rung. With
the pair driver.

```python
for black, rung in ((32, 16000), (96, 8000)):          # dark, lit
    l, f, _ = run_pair([b"HomeNet", b"hunter2!!"], black=black)
    assert (l.result, f.result) == (P.OK, P.OK)
    assert l.clock.duration == f.clock.duration == rung
print("pass")
```

### T-leader-reading

Checks the Leader reading the staircase while it announces (spec section
9.2).

Cases:

- Announcing, the Leader finds the Follower's staircase, enters the gap,
  stops announcing, and at `LEADER_SETTLE_MS` takes levels within 4 counts
  of the staircase's and the pair's light as sent.
- What came before the staircase ages out of its 160 samples.
- In 47% flicker the uncorrected staircase is refused, and the corrected
  re-read after the fit is accepted.

### T-refusal

Checks refusals (spec sections 9.2 and 14).

Cases:

- A staircase with two levels is refused: the Leader announces again,
  with a fresh reader and filter; a second refusal ends `ERR_CONTRAST`.
- A Follower in its data phase before any Begin that reads the Announce
  for 168 ms sends its staircase again; a second time, `ERR_CONTRAST`.
- A Fast Begin on 8 ms slots, which one 84 ms window can match, is not
  taken for the Announce.
- A refused Follower that then hears nothing ends `ERR_CONTRAST`, not
  `ERR_LOST_PEER`.
- In the link simulator, with the data's middle at backlight 0.52, both
  badges end `ERR_CONTRAST` within 10 s.

## Rungs and the slot window

### T-start-rung

Checks: lit starts on 8 ms, dark on 16; demotion steps one rung slower
and stops at 32 ms.

```python
assert P.SLOT_LADDER[P.EXACT_LIT_INDEX] == 8
assert P.SLOT_LADDER[P.EXACT_INDEX] == 16
c = P.SlotClock()
c.start(0, P.EXACT_INDEX)
c.slow_down()
assert c.duration == 20000 and c.epoch == 0
c.set_index(0)
c.slow_down()
assert c.duration == 32000
c.set_index(99)
assert c.index == len(P.SLOT_LADDER) - 1
print("pass")
```

### T-window

Checks: windows open at `max(settle, 40%)` and close at
`d - max(guard, 15%)`; every rung from the start rung slower has at least
2 ms.

```python
assert P.exact_window(8000, 2500, 1000) == (3200, 6800)
assert P.exact_window(16000, 5000, 2000) == (6400, 13600)
for idx, settle, guard in ((P.EXACT_LIT_INDEX, 2500, 1000),
                           (P.EXACT_INDEX, 5000, 2000)):
    for d in P.SLOT_LADDER[:idx + 1]:
        o, c = P.exact_window(d * 1000, settle, guard)
        assert c - o >= P.WINDOW_MIN_US, d
print("pass")
```

Cases:

- A rung too fast for the settle (window under 1 ms) reads the last
  quarter slot before the guard instead.
- Parts: `ceil(window / 4000)` clamped to 5-32.

### T-slot-decisions

Checks the slot rules (spec sections 10.2 and 10.3).

Cases:

- Slots are decided in order, each once its window has closed, never
  skipped.
- No slot is decided while this badge transmits.
- A demotion renumbers the grid and skips only the slot it straddles.
- A symbol is held for its whole slot, and a message starts on a slot
  boundary.
- A reply starts two slots after the deciding slot: exactly one silent
  slot between.

## Phase locking

### T-phase-lock

Checks the phase lock (spec section 10.4).

Cases:

- A lock needs 1.5 slots of silence before the crossing's slot.
- A re-base discards the deciding slot and decides slot 0 of the new grid
  next.
- One re-base a message.
- The Leader locks only on a reply (`STEP_FAST_REPLY`) and on the
  Complete Ack (`STEP_FINISH`); the Follower only outside a block.
- In the ideal channel (no skew, no noise) no block is NAKed.

### T-lock-hold

Checks the lock's edge check and the hold rule (spec section 10.4).

Cases:

- A crossing whose light falls back within 3 ms is skipped for the next.
- A real edge, lit for 3 ms, locks.
- A Follower whose fit found flicker takes a lit-reading slot before
  `hold_until` as low, and does not lock on it; at `hold_until`, it does.

## Control patterns

### T-patterns

Checks: the six patterns are exact; each holds low in what is compared,
so the rung's bits (only high and middle) cannot forge one; the Leader's
late tails hold low too.

```python
assert P.FAST_BEGIN == "-_ -_ _- "
assert P.CRC_GOOD == "-- -- -- " and P.CRC_BAD == "__ __ __ "
assert P.SYNC_ERROR == "  _  _  _" and P.COMPLETE == "- - - "
assert P.COMPLETE_ACK == "__- __- " and P.COMPLETE_ACK_LATE_TAIL == "_- __-"
for p in (P.FAST_BEGIN, P.CRC_GOOD_TAIL, P.CRC_BAD_TAIL, P.SYNC_ERROR_TAIL,
          P.COMPLETE_TAIL, P.COMPLETE_ACK_TAIL, P.COMPLETE_ACK_LATE_TAIL,
          P.CRC_GOOD_TAIL[1:], P.CRC_BAD_TAIL[1:]):
    assert P.LOW in p
assert P.COMPLETE_ACK.endswith(P.COMPLETE_ACK_LATE_TAIL + " ")
print("pass")
```

Cases:

- No pattern keeps transmitting more than one slot after the receiver
  would have acted on it.

## Payload

### T-payload

Checks: a fast stream round-trips any list of fields, empty ones
included, and refuses one cut short or with bytes over.

```python
for fields in ([b"Home", b"pw"], [b"", b"", b""], [b""], [], [b"x" * 300]):
    assert P.parse_fast_stream(P.fast_stream(fields)) == fields
assert P.fast_stream([]) == b"\x00" and P.fast_stream([b""]) == b"\x01\x00"
s = P.fast_stream([b"Home", b"pw"])
assert P.parse_fast_stream(s[:-1]) is None
assert P.parse_fast_stream(s + b"\x00") is None
assert P.bits_symbols(0b10, 2) == [P.HIGH, P.MID]
print("pass")
```

Cases:

- An empty payload (`[]`, `None`) is the stream `00`, one block,
  delivered as `[]`; `[b""]` is delivered as `[b""]`.
- A Follower whose stream never parses ends `ERR_DECODE`.

### T-begin-training

Checks training on a Fast Begin (spec section 14, step 2).

```python
t = HeldTransfer(False)
t.start_data(0, P.EXACT_LIT_INDEX)
t.levels = P.Levels()
t.levels.fit(100, 190, 300)                 # provisional: the middle a guess
def push(values):
    for v in values:
        t.rx_values[t.rx_n % 9] = v
        t.rx_n += 1
push([500, 302, 104, 498, 298, 101, 301, 503, 99])   # "-_ -_ _- "
L = t.begin_levels()
assert (L.black, L.grey, L.white) == (100, 301, 500)   # black kept
for bad in ([500, 500, 104, 500, 500, 101, 500, 500, 99],   # no middle
            [500, 300, 300, 500, 300, 300, 300, 500, 300],  # no low
            [300, 500, 104, 300, 500, 101, 500, 300, 99],   # order
            [500, 110, 104, 500, 110, 101, 110, 500, 99],   # middle at black
            [500, 480, 104, 500, 480, 101, 480, 500, 99]):  # middle at high
    push(bad)
    assert t.begin_levels() is None, bad
print("pass")
```

Cases:

- Training runs only when a slot decides low outside a block, and
  allocates nothing.
- Every Begin trains, so the levels follow a drift.

## Data phase

### T-transfer-end-to-end

Checks: a transfer completes and delivers the payload, dark and lit, for
each of the three payloads, over 8 seeds. With the pair driver.

```python
PAYLOADS = ([b"HomeNet", b"hunter2!!"], [b"OpenCafe", b""],
            [b"A" * 32, b"k" * 63])
for black, rung in ((32, 16000), (96, 8000)):
    for seed in range(1, 9):
        for payload in PAYLOADS:
            l, f, _ = run_pair(payload, seed, black)
            assert (l.result, f.result) == (P.OK, P.OK), (black, seed)
            assert f.received == payload
            assert l.clock.duration == f.clock.duration == rung
print("pass")
```

Cases, in the link simulator:

- A multi-block payload (300 bytes) delivers with no invariant broken.
- Started on each rung of the ladder, a transfer completes.
- The invariant checker fails a run built to break one (a false `OK`).

### T-recovery

Checks: a missed Begin costs one slot rung; a corrupted burst costs a
resend on the same rung (spec sections 13.1 and 14). With the pair driver.

Cases:

- A Follower that missed a Fast Begin asks for it once the Leader has
  stopped (`RESEND_QUIET_SLOTS` of low, no part of their windows lit) and
  after `UNFRAMED_LIT_SLOTS` lit slots; both end one slot rung slower.
- A burst the Follower cannot decode is NAKed; `FAST_TRIES` NAKs step the
  fast rung down; past F3 the Leader stays on F3 until
  `MAX_BLOCK_ATTEMPTS`, then ends `ERR_CRC`.
- A low rung bit reads as 0.
- Asking is bounded by `MAX_BLOCK_ATTEMPTS`.
- A demotion the Follower asks for restores the Leader's NAK budget.

### T-rate-check

Checks the Follower's check of the Leader's rate (spec section 14).

Cases:

- A Follower on 13 ms that demoted on its own, hearing a Fast Begin on
  10 ms, moves to 10 ms.
- Complete on 10 ms, or a Begin on its own rung, leaves it where it is.
- A Follower that did not demote on its own does not check.

### T-complete

Checks the ending (spec section 13).

Cases:

- The Leader repeats Complete only after more than
  `COMPLETE_RETRY_SLOTS` silent slots in a row; a lit slot resets the
  count.
- Stray light cannot hold a repeat past `COMPLETE_REPEAT_MAX_SLOTS` after
  the last Complete, inside the Follower's linger.
- A late Ack is not talked over.
- A silent Follower still gets Complete repeated, at most
  `MAX_COMPLETE_RETRIES` times.
- The Leader ends `OK` only on the Ack, whole or less its first symbol.
- A Follower holding no block does not take `- - -` for Complete.

### T-lead-in-watch

Checks the Leader's lead-in watch (spec section 13.1, step 2).

Cases:

- A sending Leader whose next pass comes ten slots late reads at most
  `WATCH_SLOTS` windows of its lead-in, and teaches the flicker learner
  only from slots that read dark.

## Giving up

### T-budgets

Checks: every wait ends. With the pair driver.

```python
PAY = [b"HomeNet", b"hunter2!!"]
mark = {}

def vanish(now, l, f, to_l, to_f):
    """The peer is taken away 300 ms into the data phase."""
    if l.phase == P.PHASE_DATA and f.phase == P.PHASE_DATA:
        mark.setdefault("t", now)
    if "t" in mark and now > mark["t"] + 300000:
        return 32, 32
    return to_l, to_f

l, f, _ = run_pair(PAY, tamper=vanish)
assert (l.result, f.result) == (P.ERR_LOST_PEER, P.ERR_LOST_PEER)

def grey(now, l, f, to_l, to_f):
    """The Leader reads a peer stuck on grey once data starts."""
    return (72, to_f) if l.phase == P.PHASE_DATA else (to_l, to_f)

l, f, _ = run_pair(PAY, tamper=grey)
assert l.result in (P.ERR_LOST_PEER, P.ERR_CRC) and f.result is not None
print("pass")
```

Cases, each badge driven into each phase and step against a peer that
stops (black), sticks (grey or white) or sends noise:

- It reaches a result within `BLOCK_TIMEOUT_MS` of its last kept block.
- A stuck screen is not a live link: the stall budget ends it.
- The stall budget does not end an honest wait (a successful transfer
  stalls at most ~42 slots).
- A block cannot be resent more than `MAX_BLOCK_ATTEMPTS` times.
- The block deadline restarts on every kept block, applies
  mid-transmission, and does not cap payload length (a 512-byte payload
  delivers).

## Mains flicker

### T-flicker-fit

Checks: a flickering room is fitted at its mains and corrected on samples
the fit never saw; a steady room is steady; 50 Hz is told from 60. In a
room that flickers, a harmonic under `FLICKER_MIN_COUNTS` but at least
`FLICKER_KEEP_COUNTS` is kept (a once-a-cycle light's third, 3.3 counts);
a room whose strongest harmonic is under `FLICKER_MIN_COUNTS` is steady.
A hard-flickering LED -- 120 / 240 / 360 Hz at 1,312 / 765 / 114 counts on
60 Hz mains, 2 ms samples over 100 ms -- fits as flicker with its 360 Hz
term near 114 and its leftover under `FLICKER_FIT_MAX_NOISE`.

```python
import math, random

def room(t_us, mains=60, a2=12.0, a4=4.0, mean=700.0):
    """A lit room: flicker at 2x and 4x the mains, in counts."""
    w = 2 * math.pi * mains * t_us / 1e6
    return mean + a2 * math.sin(2 * w) + a4 * math.sin(4 * w + 1.0)

def window(fn, n=80, every=2000, seed=1):
    rng = random.Random(seed)
    ts = [i * every + every // 2 for i in range(n)]
    vs = [int(fn(t) + rng.gauss(0, 1)) for t in ts]
    return ts, vs, [every] * n

F = P.FlickerFilter()
assert F.fit(*window(room)) == "flicker" and F.mains == 60
before = after = 0.0
for t in range(200000, 300000, 2000):
    v = room(t)
    before = max(before, abs(v - 700))
    after = max(after, abs(v - F.amount(t, 2000) - 700))
assert before > 12 and after < 3, (before, after)
F = P.FlickerFilter()
assert F.fit(*window(lambda t: 700.0)) == "steady"
assert F.amount(123456, 2000) == 0
F = P.FlickerFilter()
assert F.fit(*window(lambda t: room(t, mains=50))) == "flicker"
assert F.mains == 50
print("pass")
```

Cases:

- Noise alone, or a settling sensor (a slow exponential), is not
  flicker.
- A window holding a step is refused (`None`) after the half-window
  retry.
- The phase lock follows a mains drift of 0.02 Hz.
- A Leader stays fitted through a block.

### T-flicker-steady-skip

Checks the steady check (spec section 15).

Cases:

- `FLICKER_STEADY_RMS` equals the weakest harmonic a fit keeps:
  4 counts x 240 Hz's 0.66 gain through a 2 ms mean / sqrt(2).
- A dark ring window is steady.
- Flicker the fit would keep is never called steady (200 random windows).
- A transfer skips the fit in a steady room and fits in a flickering one.

### T-flicker-correct

Checks correcting (spec section 15).

Cases:

- The batched correction of a window's parts equals `amount()` part by
  part, bit for bit, for a fitted, a steady and a learnt filter.
- Under the MicroPython unix port, `amount()` and every other per-sample
  call allocate nothing: measure `gc.mem_alloc()` before and after 1000
  calls, outside any timed path.
- The fallback learner learns only from slots decided low, takes the
  flicker off, and leaves flicker under `FLICKER_MIN_COUNTS` alone.
- A lit room with 47% flicker runs on 8 ms.

## Simulation

### T-sweep

Checks: delivery holds across seeds, scenarios and payloads, in the link
simulator ([link simulator](tests.md#L87)).

Rows to run, with the expected delivery out of 144 (2026-10-19, fast
blocks only [simulation]; every figure in section 11 of
[data.md](data.md)):

| Row | Room | Expect |
|---|---|---|
| `mirror` | dark, 80-count swing | 144 |
| `aligned`, `overlap` | dark, 95 and 210 | 144 |
| `lit` | room light 48, steady | 144 |
| `lit47` | lit, 47% flicker at 120 Hz | 144 |
| `led50`, `led60` | lit, LED flicker, mains drifting 0.02 Hz | 144 |
| `half60` | lit, flicker once a cycle at 60 Hz | 144; over 72 seeds 1296 |
| `half50` | the same at 50 Hz | 144; over 72 seeds 1296 |
| `k3.0` | sensor 3x slower than measured | 144, on 16 ms; over 72 seeds 1296; I2 trips in about a third of runs |
| `gc70`, `lit47gc70` | collections 65-81 ms, dark and in flicker | 144 |
| `half60gc70` | both | 144; over 72 seeds 1296 |
| `v2v3`, `v3v2` | one v2 badge, one v3 | 144 |
| `self5` | 5% of a badge's own light on its own sensor | 144 |

Rules:

- No invariant but I2 is broken in any run. I2 is a diagnostic: a slow
  sensor trips it in runs that deliver, so delivery is the figure.
- One seed is not evidence. Compare 8 or more; a change that moves a row
  by 1-2 runs is inside seed noise.
- Skew-free medians: 1.4 s steady lit, 1.8 s in 47% flicker, 2.1 s
  dark.
- No run reaches the 300 s block deadline.

## Fast block

### T-fast-block

Checks the fast block's wire format (spec appendix A).

Cases:

- CRC-32 of `b"123456789"` is 0xCBF43926.
- The frame is index (u16), length (u16), payload, CRC-32 (u32), all
  big-endian, bits MSB first.
- Stuffing: a 1 after every 4 lows when another frame bit follows; no run
  of lows longer than 4; destuffing returns the frame exactly.
- The preamble: 66 symbols from xorshift32 seeded 0x2545F493, runs of the
  low at most 4, the last one high.
- A block round-trips through a model of the dark and the lit channel.
- A corrupted block fails the CRC or the stuffing check; a header claiming
  more than the rung's payload is refused before the payload is read.
- The decoder allocates nothing per symbol.
- The file announcement and the missing-block runs (LEB128, alternating,
  starting with received chunks) round-trip, and runs that do not sum to
  the chunk count are refused.
- One decoder over five blocks on one rung, dark and lit, steady and with
  1.0 raw of 120 Hz flicker at a random phase each block, each block's
  start up to a quarter of a symbol off: the first trains, the other four
  retune, and every block decodes. After `forget()`, and on another rung,
  a block trains. A block at half the swing is not retuned.

### T-fast-transfer

Checks fast blocks inside a transfer (spec 13.1, 14.2). Import
`fastblock as FB`.

Cases:

- `parse_fast_stream(fast_stream(f)) == f` for `[]`, `[b""]`, a WPA2
  credential, an open one, and fields of 127, 128 and 300 bytes; every
  proper prefix of a stream, and a stream with a byte appended, parse to
  `None`.
- `fast_block(FB, s, c, p)` from chunk 0, stepping `c` by the block's
  chunks, joins back to `s` on every rung; every block's burst is within
  `burst_max_us(FB, p)`.
- A Follower given a stream's chunks in reverse order has `received` only
  once chunk 0 is in, then the payload; a chunk given again changes
  nothing.
- `begin_levels()` trains on `FAST_BEGIN`'s nine values and refuses
  them with its third high and middle swapped.
- A Leader on rung 2 with start rung 0 steps to 1 on its `FAST_UP_ACKS`th
  ACK, not before; if rung 1 then takes `FAST_TRIES` NAKs it is back on 2
  and the next step up waits `2 x FAST_UP_ACKS`. A dark pair (start rung
  1) never steps above 1. A stepped-up rung that has held `FAST_UP_ACKS`
  ACKs is no longer on trial.
- `bits_symbols(r, FAST_RUNG_BITS)` is in highs and middles only, and a
  Follower reads each rung back from it. A Follower in a block with `fast_bits = []`
  that decides a low appends 0.
- `fast_flicker_mhz()` is 0 before a fit, `mains x 1000` when the first
  harmonic is the stronger, `FB.flicker_mhz(mains, rate_m)` otherwise.
- **In the link simulator**, both badges with `fast=FB`:
  - lit, every payload: the Leader's first burst is on `RUNGS[FAST_START_LIT]`;
    dark: `RUNGS[FAST_START_DARK]`;
  - a Follower whose decoder always returns a CRC error: bursts on F0,
    F0, F1, F1, F2, F2, F3, F3, then F3 until `MAX_BLOCK_ATTEMPTS`
    bursts in all, and the Leader ends `ERR_CRC`;
  - a lit pair with a multi-block payload whose Follower fails its first
    two bursts: F0, F0, then `FAST_UP_ACKS` bursts on F1, then F0 to the
    end;
  - a once-a-cycle 60 Hz light that misreads the fast Begin (seed 13 in
    this repo's simulator): the Follower sends a Data Sync Error once the
    burst is over -- every part of its quiet slots dark -- and the payload
    is delivered.
  - with the simulator's ring the badge's length (each push overwriting
    the entry 327.7 ms back) and the trainings as measured: lit and with
    47% flicker, every payload goes in one burst, none NAKed, and the
    decoder is first fed no sooner after the anchor than the burst lasted.
