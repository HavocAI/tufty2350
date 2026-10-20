"""Dev Mode measurement engine: the tests, the readings, the results.

Measures the backlight and the light sensor on real hardware, as specified
under "Dev Mode" in .agents/share_wifi/app-spec.md. Every signal is the
backlight, on white pixels drawn once before a run begins -- as a transfer
signals -- and every reading comes through the DMA ring (reader.RingReader
over fastadc.Ring), started when a condition begins and stopped when it
ends. Pure Python -- no `screen`, no `badge` -- so the tests and every
calculation can be tested under CPython. What only the badge has is handed
in: the reader, the backlight, the temperature sensor.

One run object measures one condition (dark or lit). The app drives it once
a frame, the screen white and run()'s clear off:

    run = MirrorRun(ring_reader(), ring_note(), show_backlight, temp)
    # or SecondBadgeRun(first, ring_reader(), ring_note(), show_backlight,
    #                   temp)
    run.begin()                 # temperature, then the ring starts
    while not run.done:         # one update() of run(update)
        run.frame()             # blocks for a test when one is due
    condition = run.results()   # the ring stopped, temperature again

`stop()` stops the ring early, on a cancel. Nothing may read the ADC or
write USB while the ring runs (fastadc.py), so the chip's temperature is
read only before the ring starts and after it stops.

Every test runs on an absolute timeline -- its events at fixed times from
its origin, whatever the analysis between them costs -- so two badges that
share a time zero run the same test in lockstep: one sends, the other only
reads. That is the 2nd-badge tool; the mirror is one badge doing both.
"""

import math

from protocol import (
    HIGH, LEVEL_SCHEMES, LIT_BLACK_COUNTS, LOW, MID, MIN_GAP_EXACT,
    SCHEME_BACKLIGHT, SENSOR_STEP, Levels, Transfer, gap_separable,
)

# The backlight off: a run's idle, the handshake's between pulses, ambient.
BACKLIGHT_OFF = 0.0
BACKLIGHT_FULL = 1.0


def backlight_duty():
    """The backlight scheme's display.backlight() value per symbol. A new
    dict every call: never hoist it to a constant."""
    return dict(LEVEL_SCHEMES[SCHEME_BACKLIGHT])


def _show(show, value):
    """`show(value)` on a badge that sends; a reading badge passes None."""
    if show is not None:
        show(value)


def _error(error):
    return {"error": "%s: %s" % (type(error).__name__, error)}


# --- de Bruijn sequences ------------------------------------------------------

def de_bruijn(k, n):
    """The lexicographically least de Bruijn sequence B(k, n), as digits.

    The standard construction: concatenate, in order, every Lyndon word whose
    length divides n. Cyclic, so every length-n word appears exactly once when
    the sequence is read round the end.
    """
    a = [0] * (k * n)
    sequence = []

    def db(t, p):
        if t > n:
            if n % p == 0:
                sequence.extend(a[1:p + 1])
        else:
            a[t] = a[t - p]
            db(t + 1, p)
            for j in range(a[t - p] + 1, k):
                a[t] = j
                db(t + 1, t)

    db(1, 1)
    return sequence


# B(3, 3) over low, middle and high, opened out: the 27-symbol cycle with its
# first two symbols repeated on the end, so every one of the 27 triples appears
# in a straight line. 29 symbols.
_ALPHABET = (LOW, MID, HIGH)
_CYCLE = "".join([_ALPHABET[d] for d in de_bruijn(3, 3)])
DE_BRUIJN = _CYCLE + _CYCLE[:2]
# Low slots before the sync symbol. The same 4 as protocol.RESYNC_LEAD_IN, and
# for the same reason: enough silence to find an edge out of.
SYNC_LEAD_SLOTS = 4


def count_symbol_errors(decided, expected):
    """Slots decided wrongly, or not decided at all, against `expected`."""
    errors = 0
    for i in range(len(expected)):
        if i >= len(decided) or decided[i] != expected[i]:
            errors += 1
    return errors


# --- statistics -----------------------------------------------------------------
# Each takes plain lists so it can be tested on synthetic data. `count` limits
# a preallocated buffer to the part that was filled.

def median(values, count=None):
    """The upper median, so a median of readings is itself a reading."""
    if count is None:
        count = len(values)
    if count <= 0:
        return None
    ordered = sorted(values[:count])
    return ordered[count // 2]


def sd(values, count=None):
    """Population standard deviation, two-pass so float32 holds its precision."""
    if count is None:
        count = len(values)
    if count <= 0:
        return None
    mean = 0.0
    for i in range(count):
        mean += values[i]
    mean /= count
    total = 0.0
    for i in range(count):
        total += (values[i] - mean) * (values[i] - mean)
    return math.sqrt(total / count)


# Noise per level: NOISE_READS ring readings from the end of its hold, their
# spread one by one and as the means of NOISE_DEPTHS of them. Noise spread
# over neighbouring ADC steps falls as the square root of the depth, and the
# mean then resolves a level finer than a step; noise narrower than a step
# stalls at it. What `averaging` and the probe's `fast_reads` measured before
# the ring, on the levels a transfer uses.
NOISE_READS = 4096          # 81.92 ms at 50 kHz
NOISE_DEPTHS = (4, 16, 64, 256)
NOISE_MIN_GROUPS = 8        # fewer means than this give no spread


def noise_of(values, count=None):
    """{"sd", "p2p", "means": {depth: sd of the means of that many}}: the
    readings' spread, in counts, sd to 1 dp and the means' to 2."""
    if count is None:
        count = len(values)
    if count <= 0:
        return {"error": "no readings"}
    lo = hi = values[0]
    total = 0
    for i in range(count):
        v = values[i]
        total += v
        if v < lo:
            lo = v
        elif v > hi:
            hi = v
    mean = total / count
    means = {}
    for depth in NOISE_DEPTHS:
        groups = count // depth
        if groups < NOISE_MIN_GROUPS:
            break
        acc = 0.0
        for g in range(groups):
            part = 0
            for i in range(g * depth, g * depth + depth):
                part += values[i]
            d = part / depth - mean
            acc += d * d
        means[str(depth)] = round(math.sqrt(acc / groups), 2)
    return {"sd": round(sd(values, count), 1), "p2p": hi - lo,
            "means": means}


# PWM: whether the sensor averages the backlight's ~3.8 kHz PWM (v2) or
# follows it. FLICKER_READS ring readings, 5.12 ms -- about 20 PWM periods --
# at each backlight value; "averages" when FLICKER_AVERAGES_PERCENT of them
# fall within FLICKER_SPREAD_STEPS ADC steps of their median. Keys are spelled
# out rather than formatted from the floats: a single-precision build need not
# print 0.74 the way the spec does.
FLICKER_READS = 256
FLICKER_BACKLIGHTS = (("0.25", 0.25), ("0.5", 0.5), ("0.74", 0.74),
                      ("0.9", 0.9), ("1.0", 1.0))
FLICKER_SPREAD_STEPS = 2
FLICKER_AVERAGES_PERCENT = 90


def flicker_verdict(values, step=SENSOR_STEP, count=None):
    """"averages" if the sensor smooths the backlight's PWM, else "follows_pwm"."""
    if count is None:
        count = len(values)
    mid = median(values, count)
    spread = FLICKER_SPREAD_STEPS * step
    within = 0
    for i in range(count):
        if abs(values[i] - mid) <= spread:
            within += 1
    if within * 100 >= FLICKER_AVERAGES_PERCENT * count:
        return "averages"
    return "follows_pwm"


def flicker_result(values, count=None):
    if count is None:
        count = len(values)
    window = values[:count]
    return {"min": min(window), "max": max(window),
            "sd": round(sd(window), 1),
            "verdict": flicker_verdict(window, SENSOR_STEP)}


# --- reading the ring -----------------------------------------------------------

class Buffers:
    """What the tests read into, allocated once per run: never per test."""

    def __init__(self):
        self.values = [0] * NOISE_READS
        self.part = [0] * 32            # reader.PARTS_MAX


# Readings up to a time are all in the ring this long after it: the newest
# entry may still be in flight (reader.newest_us()). A test that reads a
# window ending at `t` pumps to `t + READ_MARGIN_US` first.
READ_MARGIN_US = 500


def copy_readings(reader, t_end, n, out, part):
    """The `n` ring readings that end at `t_end`, each in counts, into `out`.
    Returns how many were still there: a reading overwritten, or not yet
    taken, ends the copy."""
    period = reader.period
    t = t_end - n * period
    k = 0
    while k < n:
        m = n - k
        if m > len(part):
            m = len(part)
        reader.parts(t, t + m * period, m, part)
        for j in range(m):
            if part[j] < 0:
                return k + j
            out[k + j] = part[j]
        k += m
        t += m * period
    return n


# --- the tests ------------------------------------------------------------------
# Each is fn(reader, show, t0, buffers) -> {result key: result}: its events at
# fixed times from `t0` on the reader's clock, `show` the backlight -- None
# on a badge that only reads -- and it returns by its ms in TESTS, unless its
# analysis overruns.

AMBIENT_MS = 300
AMBIENT_SETTLE_MS = 100     # the sensor's fall to the room: up to ~8 ms dark
LEVEL_MS = 300              # each backlight level held
LEVEL_ANALYSE_MS = 200      # after the last hold: the last level's statistics
CURVE_MS = 100              # each backlight_curve value held, back half read
CURVE_TENTHS = 11           # 0.0, 0.1 ... 1.0
FLICKER_MS = 100            # each PWM value held; its readings end the hold
LEVEL_NAMES = ((LOW, "low"), (MID, "mid"), (HIGH, "high"))


def curve_key(tenths):
    return "%d.%d" % (tenths // 10, tenths % 10)


def ambient_test(reader, show, t0, _buffers):
    """The room: the backlight off, the mean once the sensor has fallen."""
    _show(show, BACKLIGHT_OFF)
    end = t0 + AMBIENT_MS * 1000
    reader.pump(end + READ_MARGIN_US)
    value = reader.mean(t0 + AMBIENT_SETTLE_MS * 1000, end)
    return {"ambient": value if value >= 0 else {"error": "no readings"}}


def levels_test(reader, show, t0, buffers):
    """The backlight scheme's low, middle and high, each held LEVEL_MS: the
    mean, and the noise, of its last NOISE_READS readings. Each level's
    readings are copied out while the next is held."""
    duty = backlight_duty()
    values = buffers.values
    found = {}
    noise = {}
    for k in range(len(LEVEL_NAMES) + 1):
        at = t0 + k * LEVEL_MS * 1000
        reader.pump(at)
        _show(show, duty[LEVEL_NAMES[k][0]] if k < len(LEVEL_NAMES)
              else duty[LOW])
        if not k:
            continue
        reader.pump(at + READ_MARGIN_US)
        name = LEVEL_NAMES[k - 1][1]
        n = copy_readings(reader, at, NOISE_READS, values, buffers.part)
        if n < NOISE_READS:
            found[name] = None
            noise[name] = {"error": "no readings"}
            continue
        total = 0
        for i in range(n):
            total += values[i]
        found[name] = total // n
        noise[name] = noise_of(values, n)
    reader.pump(t0 + len(LEVEL_NAMES) * LEVEL_MS * 1000)
    if None in (found["low"], found["mid"], found["high"]):
        return {"levels": {"error": "no readings"}, "noise": noise}
    low, mid, high = found["low"], found["mid"], found["high"]
    span = high - low
    if span <= 0:
        return {"levels": {"error": "high not above low",
                           "low": low, "mid": mid, "high": high},
                "noise": noise}
    return {"levels": {"low": low, "mid": mid, "high": high, "span": span,
                       "mid_share": round((mid - low) / span, 3)},
            "noise": noise}


def curve_test(reader, show, t0, _buffers):
    """The backlight at 0.0, 0.1 ... 1.0, CURVE_MS each: each one's mean over
    the back half of its hold. Whether light is proportional to duty."""
    out = {}
    for tenths in range(CURVE_TENTHS + 1):
        at = t0 + tenths * CURVE_MS * 1000
        reader.pump(at)
        if tenths < CURVE_TENTHS:
            _show(show, tenths / 10)
        if tenths:
            reader.pump(at + READ_MARGIN_US)
            value = reader.mean(at - CURVE_MS * 500, at)
            out[curve_key(tenths - 1)] = value if value >= 0 else None
    if None in out.values():
        return {"backlight_curve": {"error": "no readings"}}
    return {"backlight_curve": out}


def flicker_test(reader, show, t0, buffers):
    """Each of FLICKER_BACKLIGHTS held FLICKER_MS; the FLICKER_READS readings
    that end it, summarised."""
    values = buffers.values
    out = {}
    count = len(FLICKER_BACKLIGHTS)
    for k in range(count + 1):
        at = t0 + k * FLICKER_MS * 1000
        reader.pump(at)
        if k < count:
            _show(show, FLICKER_BACKLIGHTS[k][1])
        if not k:
            continue
        reader.pump(at + READ_MARGIN_US)
        key = FLICKER_BACKLIGHTS[k - 1][0]
        n = copy_readings(reader, at, FLICKER_READS, values, buffers.part)
        if n < FLICKER_READS:
            return {"flicker": {"error": "no readings at %s" % key}}
        out[key] = flicker_result(values, n)
    return {"flicker": out}


# --- the DMA ring on the backlight's transitions (Dev Mode M4, M5) -------------
# backlight_step_ring (M4): every transition the backlight scheme makes, from
# the display.backlight() call, read at the ring's 20 us: 10/50/90% times and
# when a rise crosses T_low. Settles SETTLE_DARK_US and SETTLE_LIT_US,
# GUARD_DARK_US (a rise to the middle against one to high), and EDGE_LAG_*_US;
# `tx_late_us` is how long the call itself takes. A badge that only reads
# times each transition from when it was due.
#
# loopback_ring (M5): a symbol block sent on the backlight at the transfer
# loop's timing and decided by the protocol's own data phase -- SlotWindow,
# the rise lock -- at every rung in RING_LOOP_RUNGS. Settles which rungs go on
# the ladder and where pairs start.
RING_STEP_TRANSITIONS = 10
RING_STEP_HOLD_MS = 120
RING_STEP_LEAD_US = 2000
RING_STEP_AFTER_US = 20000
RING_STEP_BIN_US = 100
RING_STEP_KINDS = (("rise", LOW, HIGH), ("mid_rise", LOW, MID),
                   ("fall", HIGH, LOW), ("mid_fall", MID, LOW),
                   ("mid_high", MID, HIGH), ("high_mid", HIGH, MID))
# One transition every RING_STEP_PERIOD_MS: its hold, the capture after the
# call, and 2 ms. The analysis runs in the next hold, which it shortens.
RING_STEP_PERIOD_MS = RING_STEP_HOLD_MS + RING_STEP_AFTER_US // 1000 + 2
RING_LOOP_RUNGS = (16, 13, 10, 8, 6)
RING_BLOCK = LOW * SYNC_LEAD_SLOTS + HIGH + DE_BRUIJN * 3
RING_LOOP_LEAD_MS = 30
RING_LEVEL_MS = 150
RING_MARGIN_MS = 100
RING_STEP_MS = (3 * RING_LEVEL_MS + len(RING_STEP_KINDS)
                * RING_STEP_TRANSITIONS * RING_STEP_PERIOD_MS
                + RING_MARGIN_MS)


def ring_loop_span_ms(rung):
    """One loopback block's time: its lead, the block and three slots more."""
    return RING_LOOP_LEAD_MS + (len(RING_BLOCK) + 3) * rung


RING_LOOP_MS = (3 * RING_LEVEL_MS
                + sum(ring_loop_span_ms(rung) for rung in RING_LOOP_RUNGS)
                + RING_MARGIN_MS)


def ring_levels(reader, show, duty, t0):
    """The backlight scheme's three levels: each held RING_LEVEL_MS from `t0`
    on and its back half's mean taken. (low, mid, high), -1 for none."""
    out = []
    for k in range(4):
        at = t0 + k * RING_LEVEL_MS * 1000
        reader.pump(at)
        _show(show, duty[_ALPHABET[k]] if k < 3 else duty[LOW])
        if k:
            reader.pump(at + READ_MARGIN_US)
            out.append(reader.mean(at - RING_LEVEL_MS * 500, at))
    return out[0], out[1], out[2]


def _curve_crossing(curve, start, level, rising):
    """The first bin from `start` at or past `level`, interpolated, in bins;
    None if none."""
    for b in range(max(1, start), len(curve)):
        v = curve[b]
        if v < 0:
            continue
        if (v >= level) if rising else (v <= level):
            a = curve[b - 1]
            if a < 0 or a == v:
                return float(b)
            return b - 1 + (level - a) / float(v - a)
    return None


def backlight_step_ring(reader, show, t0, _buffers=None):
    """Dev Mode M4: see above. ~9 s."""
    duty = backlight_duty()
    bins = (RING_STEP_LEAD_US + RING_STEP_AFTER_US) // RING_STEP_BIN_US
    lead = RING_STEP_LEAD_US // RING_STEP_BIN_US
    curve = [0] * bins
    part = [0] * 32
    low, mid, high = ring_levels(reader, show, duty, t0)
    if min(low, mid, high) < 0:
        return {"backlight_step_ring": {"error": "no readings"}}
    out = {"levels": [low, mid, high]}
    t_low = (low + mid) // 2
    late = []
    base = t0 + 3 * RING_LEVEL_MS * 1000
    k = 0
    for name, first, second in RING_STEP_KINDS:
        found = {"t10_ms": [], "t50_ms": [], "t90_ms": [], "tlow_ms": []}
        for _ in range(RING_STEP_TRANSITIONS):
            start = base + k * RING_STEP_PERIOD_MS * 1000
            k += 1
            reader.pump(start)
            _show(show, duty[first])
            reader.pump(start + RING_STEP_HOLD_MS * 1000)
            call = reader.now_us()
            if show is not None:
                show(duty[second])
                late.append(reader.now_us() - call)
            reader.pump(call + RING_STEP_AFTER_US + READ_MARGIN_US)
            origin = call - RING_STEP_LEAD_US
            for b0 in range(0, bins, 32):
                n = min(32, bins - b0)
                reader.parts(origin + b0 * RING_STEP_BIN_US,
                             origin + (b0 + n) * RING_STEP_BIN_US, n, part)
                for i in range(n):
                    curve[b0 + i] = part[i]
            initial = [v for v in curve[:lead] if v >= 0]
            final = [v for v in curve[-lead:] if v >= 0]
            if not initial or not final:
                continue
            v0 = sum(initial) / len(initial)
            v1 = sum(final) / len(final)
            if abs(v1 - v0) < SENSOR_STEP:
                continue
            rising = v1 > v0
            targets = [("t10_ms", v0 + 0.1 * (v1 - v0)),
                       ("t50_ms", v0 + 0.5 * (v1 - v0)),
                       ("t90_ms", v0 + 0.9 * (v1 - v0))]
            if rising and first == LOW:
                targets.append(("tlow_ms", t_low))
            for key, level in targets:
                b = _curve_crossing(curve, lead, level, rising)
                if b is not None:
                    found[key].append((b - lead) * RING_STEP_BIN_US / 1000.0)
        result = {"count": len(found["t50_ms"])}
        for key in found:
            if found[key]:
                result[key] = round(sum(found[key]) / len(found[key]), 2)
        out[name] = result
    if late:
        out["tx_late_us"] = {"mean": sum(late) // len(late), "max": max(late)}
    _show(show, duty[LOW])
    return {"backlight_step_ring": out}


def _ring_loop_block(reader, show, levels, lit, rung, duty, start):
    """RING_BLOCK at `rung` ms from `start`, sent if `show` is given, and
    decided through a listening Transfer's data phase if `levels` are: a
    sender whose own sensor cannot tell the levels apart -- the 2nd badge,
    which sees only its own light off the other's glass -- still sends."""
    transfer = None
    decided = []
    lags = []
    duration = rung * 1000
    if levels is not None:
        transfer = Transfer(False, reader=reader)
        transfer.levels = levels
        transfer.lit = lit
        transfer.scheme = SCHEME_BACKLIGHT
        transfer.start_data(start // 1000, 0)
        transfer.clock.duration = duration
        transfer.clock.epoch = start
        transfer.lit_end = start
        transfer.set_window()

        def heard():
            decided.append(transfer.rx[-1])

        def rebase(boundary, crossing):
            # From the nearest boundary: a reading badge's timeline can run
            # a little early or late of the sender's, and its lag with it.
            sent = start + (crossing - start + duration // 2) // duration \
                * duration
            lags.append(crossing - sent)
            Transfer.rebase(transfer, boundary, crossing)

        transfer.follower_rx = heard
        transfer.rebase = rebase
    late = []
    count = len(RING_BLOCK)
    for j in range(count + 2):
        boundary = start + j * duration
        reader.pump(boundary)
        if show is not None:
            show(duty[RING_BLOCK[j] if j < count else LOW])
            late.append(reader.now_us() - boundary)
        if transfer is not None:
            transfer.work(reader.now_us())
            transfer.idle_slots = 0
            transfer.stall_slots = 0
    if transfer is None:
        return None
    reader.pump(start + (count + 2) * duration + transfer.w_close)
    transfer.work(reader.now_us())
    try:
        first = decided.index(HIGH)
    except ValueError:
        return {"error": "no sync symbol"}
    expected = DE_BRUIJN * 3
    out = {"symbols": len(expected),
           "errors": count_symbol_errors(decided[first + 1:], expected),
           "locks": len(lags), "parts": transfer.w_parts}
    if late:
        out["tx_late_us"] = {"mean": sum(late) // len(late), "max": max(late)}
    if lags:
        out["lag_us"] = {"min": min(lags), "mean": sum(lags) // len(lags),
                         "max": max(lags)}
    return out


def loopback_ring(reader, show, t0, _buffers=None):
    """Dev Mode M5: see above. ~5.5 s."""
    duty = backlight_duty()
    low, mid, high = ring_levels(reader, show, duty, t0)
    # A transfer's gate, as its calibration applies it: MIN_GAP_EXACT and
    # the quantisation floor (its levels are means, with no noise of their
    # own measured here). Levels it refuses are not decided -- but a sender
    # sends the blocks all the same, for the badge reading them.
    separable = (min(low, mid, high) >= 0
                 and gap_separable(mid - low, 0.0, 0.0, MIN_GAP_EXACT)
                 and gap_separable(high - mid, 0.0, 0.0, MIN_GAP_EXACT))
    levels = None
    lit = low >= LIT_BLACK_COUNTS
    if separable:
        levels = Levels()
        levels.fit(low, mid, high)
    elif show is None:
        return {"loopback_ring": {"error": "levels too close",
                                  "levels": [low, mid, high]}}
    out = {"levels": [low, mid, high], "lit": lit}
    at = t0 + 3 * RING_LEVEL_MS * 1000
    for rung in RING_LOOP_RUNGS:
        try:
            block = _ring_loop_block(reader, show, levels, lit, rung, duty,
                                     at + RING_LOOP_LEAD_MS * 1000)
        except Exception as error:     # noqa: BLE001 - recorded, not fatal
            block = _error(error)
        if block is not None:
            out[str(rung)] = block
        at += ring_loop_span_ms(rung) * 1000
    _show(show, duty[LOW])
    if not separable:
        return {"loopback_ring": {"error": "levels too close",
                                  "levels": [low, mid, high]}}
    return {"loopback_ring": out}


# The tests in order: (name, ms, fn). A run gives each TEST_GAP_MS after the
# one before -- a frame, in which the app draws nothing and B can cancel.
TESTS = (("ambient", AMBIENT_MS, ambient_test),
         ("levels", len(LEVEL_NAMES) * LEVEL_MS + LEVEL_ANALYSE_MS,
          levels_test),
         ("backlight_curve", CURVE_TENTHS * CURVE_MS, curve_test),
         ("flicker", len(FLICKER_BACKLIGHTS) * FLICKER_MS, flicker_test),
         ("backlight_step_ring", RING_STEP_MS, backlight_step_ring),
         ("loopback_ring", RING_LOOP_MS, loopback_ring))
TEST_GAP_MS = 100
# Every result key, in the order the tests make them: `levels` makes `noise`.
RESULT_KEYS = ("ambient", "levels", "noise", "backlight_curve", "flicker",
               "backlight_step_ring", "loopback_ring")
TEST_KEYS = {"levels": ("levels", "noise")}


def test_offsets():
    """Each test's origin, in ms from the schedule's."""
    out = []
    at = 0
    for _name, ms, _fn in TESTS:
        out.append(at)
        at += ms + TEST_GAP_MS
    return out


TEST_OFFSETS = test_offsets()
SCHEDULE_MS = TEST_OFFSETS[-1] + TESTS[-1][1]
# From the ring starting to the first test's origin: the first frame after
# begin(), and the ring's anchor.
MIRROR_LEAD_MS = 100


def run_test(index, reader, show, t0, buffers):
    """TESTS[index] from `t0`, its result keys: a test that raises, or a ring
    that has failed, is an error under each of its keys, not a crash."""
    name, _ms, fn = TESTS[index]
    keys = TEST_KEYS.get(name, (name,))
    if not reader.check():
        return dict.fromkeys(keys, {"error": "ring failed"})
    try:
        out = fn(reader, show, t0, buffers)
    except Exception as error:     # noqa: BLE001 - recorded, not fatal
        return dict.fromkeys(keys, _error(error))
    return out


# --- runs -------------------------------------------------------------------------

class _Run:
    def __init__(self, reader, note, show, temp=None):
        self.reader = reader
        self.note = note
        self.show = show
        # Degrees C, or None where the firmware has no sensor to read.
        self.temp = temp
        self.temps = {}
        self.buffers = Buffers()
        self.started = False
        self.done = False

    def _note_temp(self, key):
        if self.temp is not None:
            self.temps[key] = self.temp()

    def begin(self):
        """Starts a condition: the temperature, then the ring. Without a
        ring there is nothing to measure, and the run is done at once."""
        self.temps = {}
        self.done = False
        self._note_temp("start")
        if self.reader is None:
            self.done = True
            return
        self.reader.start()
        self.started = True

    def stop(self):
        """Stops the ring, if it runs: at the end, or on a cancel."""
        if self.started:
            self.started = False
            self.reader.stop()

    def temperatures(self):
        """{"start": C, "end": C}, the end read now: after the ring stops."""
        self.stop()
        if self.temp is None:
            return {"error": "no temperature sensor"}
        out = dict(self.temps)
        out["end"] = self.temp()
        return out

    def no_ring(self):
        return {"error": self.note or "no ring"}

    def _all(self, result):
        return dict.fromkeys(RESULT_KEYS, result)


class MirrorRun(_Run):
    """One condition on a mirror: the badge sends every test and reads it
    back, one test a frame."""

    def __init__(self, reader, note, show, temp=None):
        _Run.__init__(self, reader, note, show, temp)
        self.index = 0
        self.found = {}
        self.epoch = 0

    def begin(self):
        _Run.begin(self)
        self.index = 0
        self.found = {}
        if self.started:
            self.epoch = self.reader.now_us() + MIRROR_LEAD_MS * 1000

    @property
    def test_name(self):
        return TESTS[self.index][0] if self.index < len(TESTS) else None

    def frame(self):
        """Runs the next test, blocking until it is over."""
        if self.done:
            return
        if self.index < len(TESTS):
            self.found.update(run_test(
                self.index, self.reader, self.show,
                self.epoch + TEST_OFFSETS[self.index] * 1000, self.buffers))
            self.index += 1
        if self.index >= len(TESTS):
            _show(self.show, BACKLIGHT_OFF)
            self.done = True

    def results(self):
        if self.reader is None:
            out = self._all(self.no_ring())
        else:
            out = {}
            for key in RESULT_KEYS:
                out[key] = self.found.get(key, {"error": "not run"})
        out["temperature_c"] = self.temperatures()
        return out


# --- the 2nd-badge handshake ----------------------------------------------------
# Every 2nd-badge turn starts with a handshake: the badge about to send CALLS
# with short backlight pulses until the other ANSWERS half way between them,
# then says GO. Nothing is timed against the other badge until both have seen
# each other, so neither user has to press A at any particular moment. See
# "The handshake" in the spec. Between pulses both backlights are off: the
# largest swing the backlight has, for a detector that must find it across
# two badges.

PULSE_HIGH_MS = 60
PULSE_PERIOD_MS = 360
# Half a period: a badge's own pulse can echo off the other's glass into its
# own sensor, and that echo arrives with the pulse, 0-90 ms into the cycle
# (ECHO_MS). A real answer arrives at 180 ms plus a frame each side, so the
# accepted window cannot mistake one for the other, and it ends before the
# caller's next pulse at 360 ms.
REPLY_DELAY_MS = 180
ECHO_MS = 90
REPLY_EARLIEST_MS = 120
REPLY_LATEST_MS = 270
# Three answers in a row rather than one, so a noise spike cannot start a run.
REPLIES_NEEDED = 3
# A go rather than simply stopping, so the answerer never has to judge silence
# by a timeout; its fall is a single sharp edge both badges time. That fall is
# time zero: both hold the backlight off for GO_GAP_MS and the sender's
# schedule starts then.
GO_MS = 600
GO_GAP_MS = 1000
HANDSHAKE_TIMEOUT_MS = 60000
# The dark after each turn's schedule: the sender's last level has gone and
# the measurer's last read is done before anyone calls again.
TURN_END_MS = 1000

# What counts as a pulse: a rise of at least the threshold above the baseline
# (the lowest sample of the last BASELINE_MS) that falls back below half the
# threshold within PULSE_MIN_MS-PULSE_MAX_MS.
BASELINE_MS = 1000
PULSE_MIN_MS = 20
PULSE_MAX_MS = 150
# What counts as a go: a rise held for more than three pulse widths -- and
# within a quarter of GO_MS either way. Unbounded, any long hold at a lit
# level in the schedule is a go, and a badge that missed the real one would
# lock onto the peer's schedule and measure it out of step.
GO_MIN_MS = GO_MS - GO_MS // 4
GO_MAX_MS = GO_MS + GO_MS // 4
# ...and the answerer takes a go only once it has seen REPLIES_NEEDED call
# pulses a period apart, the last of them a period before the go -- the caller
# puts its go where its next pulse would have been, and sends none without
# three answers in a row. The schedule's backlight steps are pulse-shaped
# (a rise held 22 ms or more, every 142 ms): a width window alone lets them
# pass for calls.
PERIOD_MIN_MS = PULSE_PERIOD_MS - PULSE_PERIOD_MS // 4
PERIOD_MAX_MS = PULSE_PERIOD_MS + PULSE_PERIOD_MS // 4

# The detector is fed SAMPLE_US means of the ring, timed at their centres;
# a measurer then finds the go's fall again in FALL_BIN_US bins, within
# FALL_SEARCH_US of the detector's estimate, so its timeline is the
# sender's to about a bin and the sensor's fall delay.
SAMPLE_US = 4000
FALL_BIN_US = 100
FALL_SEARCH_US = 6000

# The threshold comes from the run's NOISE_MS with both backlights off, after
# the 3 s start delay rather than during it: the hand that pressed A may still
# be moving the badge then. Half again the noise, and never less than
# PULSE_MIN_SWING (three ADC steps), so a still screen cannot trigger on a
# single step.
NOISE_MS = 1000
NOISE_CAPACITY = NOISE_MS * 1000 // SAMPLE_US + 1
NOISE_MIN_READS = 8
# The noise is twice the spread from the lowest sample to the median: for
# noise spread evenly about a level that is its peak-to-peak, and the median
# cannot be reached by the other badge's call pulses if it started calling
# first -- a sixth of the time.
PULSE_MIN_SWING = 3 * SENSOR_STEP

# After a handshake times out the run holds its last screen this long before
# it ends, so "No answer from the other badge" is on the screen to be read
# rather than replaced at once by the next prompt.
NO_ANSWER_HOLD_MS = 3000


def noise_spread(values, count):
    """Twice the lowest sample to the (lower) median, or None under 8."""
    if count < NOISE_MIN_READS:
        return None
    ordered = sorted(values[:count])
    return 2 * (ordered[(count - 1) // 2] - ordered[0])


def pulse_threshold(noise):
    """The rise a pulse must show, given the noise second's spread."""
    if noise is None:
        return PULSE_MIN_SWING
    return max(PULSE_MIN_SWING, noise * 3 // 2)


def _crossing(t0, v0, t1, v1, level):
    """When the line from (t0, v0) to (t1, v1) passes `level`, clamped."""
    dt = t1 - t0
    span = v1 - v0
    if span == 0 or dt <= 0:
        return t1
    offset = (level - v0) * dt // span
    if offset < 0:
        offset = 0
    elif offset > dt:
        offset = dt
    return t0 + offset


PULSE_NONE = 0
PULSE_SEEN = 1      # a call or an answer: `rise` says when it began
PULSE_GO = 2        # a go: `rise` and `fall`, the fall being time zero


class PulseDetector:
    """Finds the handshake's pulses and gos in samples of the ring, times in
    ms on the reader's clock.

    The baseline is the lowest sample of the last BASELINE_MS; a rise of
    `threshold` above it starts a pulse, and a fall back below half the
    threshold above the same baseline ends it. The rise is interpolated where
    the samples cross half the threshold, the fall where they cross the middle
    of the swing the pulse reached. Nothing is allocated per sample.
    """

    CAPACITY = BASELINE_MS * 1000 // SAMPLE_US + 1

    def __init__(self):
        self.times = [0] * self.CAPACITY
        self.values = [0] * self.CAPACITY
        self.threshold = PULSE_MIN_SWING
        self.reset()

    def reset(self):
        self.head = 0
        self.count = 0
        self.high = False
        self.have_prev = False
        self.prev_t = 0
        self.prev_v = 0
        self.base = 0
        self.peak = 0
        self.rise = 0
        self.fall = 0

    def baseline(self, now):
        cap = self.CAPACITY
        lo = None
        for n in range(self.count):
            idx = (self.head - 1 - n) % cap
            if now - self.times[idx] > BASELINE_MS:
                break
            if lo is None or self.values[idx] < lo:
                lo = self.values[idx]
        return lo

    def push(self, now, value):
        """One sample. PULSE_NONE, PULSE_SEEN or PULSE_GO."""
        self.times[self.head] = now
        self.values[self.head] = value
        self.head = (self.head + 1) % self.CAPACITY
        if self.count < self.CAPACITY:
            self.count += 1

        event = PULSE_NONE
        half = self.threshold // 2
        if not self.high:
            base = self.baseline(now)
            if value >= base + self.threshold:
                self.high = True
                self.base = base
                self.peak = value
                if self.have_prev:
                    self.rise = _crossing(self.prev_t, self.prev_v, now,
                                          value, base + half)
                else:
                    self.rise = now
        else:
            if value > self.peak:
                self.peak = value
            if value < self.base + half:
                self.high = False
                self.fall = _crossing(self.prev_t, self.prev_v, now, value,
                                      (self.base + self.peak) // 2)
                width = self.fall - self.rise
                if PULSE_MIN_MS <= width <= PULSE_MAX_MS:
                    event = PULSE_SEEN
                elif GO_MIN_MS <= width <= GO_MAX_MS:
                    event = PULSE_GO
        self.prev_t = now
        self.prev_v = value
        self.have_prev = True
        return event


PHASE_NOISE = "noise"           # first second: measuring a still room
PHASE_CALLING = "calling"       # calling the peer, then the go
PHASE_ANSWERING = "answering"   # answering the peer's call, until its go
PHASE_SENDING = "sending"       # sending the schedule, then its closing dark
PHASE_MEASURING = "measuring"   # reading the peer's schedule
PHASE_DONE = "done"


class SecondBadgeRun(_Run):
    """One condition in 2nd-badge mode: both turns, from one badge's side.

    The noise second, then two turns. In the first the first badge calls and
    the second answers; after the go the first sends and the second measures.
    In the second turn the roles swap: the second badge calls once the first
    turn's closing dark has ended, which it knows from the first go, and the
    first -- listening since its own schedule ended -- answers. Both finish
    when the second turn's closing dark ends. A handshake with no go within
    HANDSHAKE_TIMEOUT_MS ends the condition for this badge.

    A sender runs every test as the mirror does, reading its own sensor --
    its own light off the other badge's glass, the other's backlight off:
    the self-coupling, kept as `self`. A measurer runs the same tests in
    step, reading only, its backlight off.
    """

    def __init__(self, first, reader, note, show, temp=None):
        _Run.__init__(self, reader, note, show, temp)
        self.first = first
        self.role = "first" if first else "second"
        self.detector = PulseDetector()
        self.noise = [0] * NOISE_CAPACITY
        self.noise_count = 0
        # The noise second's spread and the threshold set from it: kept for
        # the app to show, and so a failed handshake can be explained.
        self.pulse_noise = None
        self.pulse_threshold = PULSE_MIN_SWING
        self.shown = None
        self.measured = {}
        self.sent = {}
        self.did_measure = False
        self.did_send = False
        self.no_handshake = False
        self.turn = 1
        self.begin_at = 0
        self.epoch = 0
        self.index = 0
        self.fed = 0
        self.ended_at = 0
        self.phase = PHASE_DONE
        self._handshake(0)
        self.phase = PHASE_DONE

    @property
    def sending(self):
        return self.phase == PHASE_SENDING

    def begin(self):
        _Run.begin(self)
        self.no_handshake = False
        self.did_measure = self.did_send = False
        self.measured = {}
        self.sent = {}
        self.turn = 1
        self.shown = None
        if not self.started:
            self.phase = PHASE_DONE
            return
        # Every run opens on its noise second; see NOISE_MS.
        self.phase = PHASE_NOISE
        now_us = self.reader.now_us()
        self.begin_at = now_us // 1000
        self.fed = now_us
        self.noise_count = 0
        self._backlight(BACKLIGHT_OFF)

    def _backlight(self, value):
        if value != self.shown:
            self.shown = value
            _show(self.show, value)

    def _calls(self):
        return self.first == (self.turn == 1)

    def _handshake(self, now):
        """Starts a handshake at `now` ms, both roles' state reset."""
        self.detector.reset()
        self.handshake_at = now
        self.go_start = None
        # Calling.
        self.cycle = self.pulse_cycle = self.prev_pulse_cycle = -1
        self.pulse_start = self.prev_pulse_start = None
        self.replies = 0
        self.answered_cycle = -2
        # Answering.
        self.answer_at = self.answer_start = self.last_call = None
        self.answer_shown = False
        self.calls = 0          # call pulses seen a period apart, in a row
        self.phase = PHASE_CALLING if self._calls() else PHASE_ANSWERING
        if self.started:
            # The turn's own readings are not the handshake's.
            newest = self.reader.newest_us()
            if newest > self.fed:
                self.fed = newest

    def _set_threshold(self):
        self.pulse_noise = noise_spread(self.noise, self.noise_count)
        self.pulse_threshold = pulse_threshold(self.pulse_noise)
        self.detector.threshold = self.pulse_threshold

    def _feed(self):
        """Every SAMPLE_US of the ring since the last frame, in order: to the
        noise second, or to the detector."""
        reader = self.reader
        t = self.fed
        newest = reader.newest_us()
        while t + SAMPLE_US <= newest:
            value = reader.mean(t, t + SAMPLE_US)
            at = (t + SAMPLE_US // 2) // 1000
            t += SAMPLE_US
            if value < 0:
                continue
            phase = self.phase
            if phase == PHASE_NOISE:
                if self.noise_count < NOISE_CAPACITY:
                    self.noise[self.noise_count] = value
                    self.noise_count += 1
                if at - self.begin_at >= NOISE_MS:
                    self._set_threshold()
                    self._handshake(at)
                    t = self.fed if self.fed > t else t
                continue
            event = self.detector.push(at, value)
            if phase == PHASE_ANSWERING:
                self._heard(event)
                if self.phase == PHASE_MEASURING:
                    break
            elif event == PULSE_SEEN:
                self._answered(self.detector.rise)
        if t > self.fed:
            self.fed = t

    def _next_cycle(self, now):
        """At each period's start: the next call pulse, or the go instead."""
        cycle = (now - self.handshake_at) // PULSE_PERIOD_MS
        if cycle == self.cycle:
            return
        self.cycle = cycle
        if self.replies >= REPLIES_NEEDED and self.answered_cycle == cycle - 1:
            # The go goes where the next pulse would have.
            self.go_start = now
            return
        # Timed from when this badge showed it, not from the cycle's ideal
        # start: a late frame moves the pulse.
        self.prev_pulse_start = self.pulse_start
        self.prev_pulse_cycle = self.pulse_cycle
        self.pulse_start = now
        self.pulse_cycle = cycle

    def _answered(self, rise):
        """Counts a pulse seen at `rise` if it answers one of ours in time."""
        start = self.pulse_start
        cycle = self.pulse_cycle
        if start is None:
            return
        if rise - start < 0:
            # Seen after our next pulse had started: it answers the one before.
            start = self.prev_pulse_start
            cycle = self.prev_pulse_cycle
            if start is None:
                return
        offset = rise - start
        if not REPLY_EARLIEST_MS <= offset <= REPLY_LATEST_MS:
            return
        if cycle == self.answered_cycle:
            return
        if cycle == self.answered_cycle + 1:
            self.replies += 1
        else:
            # A cycle went by without an answer: count again from this one.
            self.replies = 1
        self.answered_cycle = cycle

    def _heard(self, event):
        """The answerer's sample: an answer to every call pulse, until a go."""
        detector = self.detector
        if event == PULSE_GO:
            if (self.calls >= REPLIES_NEEDED
                    and self._a_period_after_the_last_call(detector.rise)):
                self._measure(detector.fall)
        elif event == PULSE_SEEN:
            rise = detector.rise
            # Our own answer, echoed off the other badge's glass, is no call.
            if (self.answer_start is not None and -PULSE_HIGH_MS
                    <= rise - self.answer_start <= ECHO_MS):
                return
            if self._a_period_after_the_last_call(rise):
                self.calls += 1
            else:
                self.calls = 1
            self.last_call = rise
            self.answer_at = rise + REPLY_DELAY_MS
            self.answer_shown = False

    def _a_period_after_the_last_call(self, rise):
        return (self.last_call is not None and PERIOD_MIN_MS
                <= rise - self.last_call <= PERIOD_MAX_MS)

    def _start_turn(self, phase, zero_us):
        self.phase = phase
        self.epoch = zero_us + GO_GAP_MS * 1000
        self.index = 0

    def _measure(self, fall):
        self._backlight(BACKLIGHT_OFF)
        self._start_turn(PHASE_MEASURING, self._fall_us(fall))

    def _fall_us(self, fall):
        """The go's fall, `fall` ms as the detector saw it, found again in
        the ring: the first FALL_BIN_US bin at or below the middle of the
        go's swing after one above it, interpolated. `fall` itself if
        none is found."""
        detector = self.detector
        level = (detector.base + detector.peak) // 2
        part = self.buffers.part
        n = len(part)
        t0 = fall * 1000 - FALL_SEARCH_US
        prev = -1
        for k in range(0, 2 * FALL_SEARCH_US // FALL_BIN_US, n):
            a = t0 + k * FALL_BIN_US
            self.reader.parts(a, a + n * FALL_BIN_US, n, part)
            for i in range(n):
                v = part[i]
                if v < 0:
                    continue
                if prev > level >= v:
                    t = a + i * FALL_BIN_US - FALL_BIN_US // 2
                    return t + (prev - level) * FALL_BIN_US // (prev - v)
                prev = v
        return fall * 1000

    def _turn_ended(self, now):
        if self.turn == 1:
            self.turn = 2
            self._handshake(now)
        else:
            self._finish()

    def _finish(self):
        self.phase = PHASE_DONE
        self._backlight(BACKLIGHT_OFF)
        self.done = True

    def frame(self):
        if self.done:
            return
        now_us = self.reader.now_us()
        now = now_us // 1000
        if self.no_handshake:
            # Gave up: hold the screen until the message can have been read.
            if now - self.ended_at >= NO_ANSWER_HOLD_MS:
                self._finish()
            return
        if self.phase in (PHASE_SENDING, PHASE_MEASURING):
            self._turn(now_us)
            return
        self._feed()
        phase = self.phase
        if phase == PHASE_MEASURING or phase == PHASE_NOISE:
            return
        if self.go_start is None and now - self.handshake_at \
                >= HANDSHAKE_TIMEOUT_MS:
            # The condition is over for this badge: the ring stops, and the
            # app may draw what happened.
            self.no_handshake = True
            self.ended_at = now
            self._backlight(BACKLIGHT_OFF)
            self.stop()
            return
        if phase == PHASE_CALLING:
            if self.go_start is None:
                self._next_cycle(now)
            if self.go_start is not None and now - self.go_start >= GO_MS:
                # The go's fall, as this badge made it: time zero.
                self._backlight(BACKLIGHT_OFF)
                self._start_turn(PHASE_SENDING, self.reader.now_us())
                return
            on = (self.go_start is not None
                  or (now - self.handshake_at) % PULSE_PERIOD_MS
                  < PULSE_HIGH_MS)
            self._backlight(BACKLIGHT_FULL if on else BACKLIGHT_OFF)
        else:
            on = (self.answer_at is not None
                  and 0 <= now - self.answer_at < PULSE_HIGH_MS)
            if on and not self.answer_shown:
                self.answer_shown = True
                self.answer_start = now
            self._backlight(BACKLIGHT_FULL if on else BACKLIGHT_OFF)

    def _turn(self, now_us):
        """One test a frame from the turn's time zero; then its end."""
        sending = self.phase == PHASE_SENDING
        if self.index < len(TESTS):
            self.shown = None       # the tests set the backlight themselves
            out = run_test(self.index, self.reader,
                           self.show if sending else None,
                           self.epoch + TEST_OFFSETS[self.index] * 1000,
                           self.buffers)
            if sending:
                self.sent.update(out)
            else:
                self.measured.update(out)
            self.index += 1
            if self.index >= len(TESTS):
                self._backlight(BACKLIGHT_OFF)
                if sending:
                    self.did_send = True
                else:
                    self.did_measure = True
            return
        # The first badge listens from the end of its own schedule; the
        # second, sending last, and every measurer finish when the closing
        # dark ends.
        end = SCHEDULE_MS
        if not (sending and self.first):
            end += TURN_END_MS
        if now_us - self.epoch >= end * 1000:
            self._turn_ended(now_us // 1000)

    def results(self):
        if self.reader is None:
            out = self._all(self.no_ring())
        else:
            out = {}
            for key in RESULT_KEYS:
                out[key] = self.measured.get(
                    key, {"error": "no handshake" if self.no_handshake
                          else "not run"})
            if self.did_send:
                out["self"] = dict(self.sent)
            else:
                out["self"] = {"error": "no handshake" if self.no_handshake
                               else "not run"}
        out["temperature_c"] = self.temperatures()
        return out
