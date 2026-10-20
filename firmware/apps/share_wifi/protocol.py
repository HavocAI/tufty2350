"""Optical Data Transfer Protocol: framing, timing and the state machine.

Sends a list of byte strings between two badges on the backlight alone,
read back through the DMA ring: control in slots, the payload in fast
blocks (fastblock.py). LEVEL_SCHEMES says which display.backlight()
value each symbol is; calibration measures what actually arrives. What the
fields mean, how many and how long, is the caller's business.

Pure Python -- no `screen`, no `badge`. The caller builds and starts a
reader (reader.py):

    transfer = Transfer(leader=True, payload=[b"first", b"second"],
                        reader=reader)

and each pass shows transfer.field_at(now), calls transfer.work(now), and
waits until transfer.due_us().

Specification: .agents/optical_data_transfer/spec.md, the
authority; every constant's reasons are in context.md beside it, and the
evidence in data.md.
"""

import math
import time
from array import array

# 12-bit conversion in a 16-bit frame: 16 counts is one ADC step.
SENSOR_STEP = 16

# A peer is there at all: a swing of three ADC steps, over held noise.
MIN_SWING = 3 * SENSOR_STEP

# Calibrated levels separate when each gap is at least MIN_GAP_EXACT and
# GAP_SIGMAS of the noisier neighbour's noise, floored at QUANTISATION_SD
# (spec 9.3).
MIN_GAP_EXACT = SENSOR_STEP
GAP_SIGMAS = 4
QUANTISATION_SD = SENSOR_STEP / math.sqrt(12)

# A badge is LIT when the black it reads is at least four ADC steps: the
# sensor's speed follows its light.
LIT_BLACK_COUNTS = 64

# Before the data phase the protocol pulls FINE_MS means from its reader
# and pools them into coarse samples of the same length.
FINE_MS = 2
FINE_US = FINE_MS * 1000

# The learner: room flicker at twice the mains, learnt only from samples
# the peer was dark for -- beacons and staircases are periodic, and
# learning from everything took 15% of the swing for flicker.
FLICKER_HZ = (100, 120)
# A few hundred ms of dark settles the estimate.
FLICKER_TAU_MS = 250
# Smaller than this is not taken off, and a fit whose strongest harmonic
# is smaller finds the room steady.
FLICKER_MIN_COUNTS = 4
# A flickering room's fit keeps every harmonic this big that clears its
# error: a once-a-cycle light's third, dropped at 4, misread slots.
FLICKER_KEEP_COUNTS = 2
# A dark slot teaches only with this many samples.
FLICKER_MIN_POINTS = 2

# Whole numbers from tables on the hot path: a float is a heap allocation
# on the badge. FLICKER_STEPS a period, scaled by FLICKER_ONE, a whole
# number of us a step per Hz.
FLICKER_STEPS = 320
FLICKER_ONE = 1024
# A sample's span, for the shrink below, in steps of this many us.
FLICKER_SPAN_US = 250
_SINE = tuple(int(round(FLICKER_ONE * math.sin(2 * math.pi * i / FLICKER_STEPS)))
              for i in range(FLICKER_STEPS))
_QUARTER = FLICKER_STEPS // 4
# A second is whole periods of every frequency used, so a time's phase is
# its microseconds into the second. (ticks_us wraps at 2**30: one phase jump
# every 17.9 minutes.)
_US_PER_STEP_HZ = 1000000 // FLICKER_STEPS
# Span steps in a table: up to two samples' length.
_SPAN_STEPS = 4 * FINE_MS * 1000 // FLICKER_SPAN_US + 1
_SPAN_LAST = _SPAN_STEPS - 1


def _span_table(hz, grow):
    """A sample's mean shrinks a flicker by sin(x)/x, x = pi x span x hz:
    per FLICKER_SPAN_US step of span, that or its inverse, scaled by
    FLICKER_ONE."""
    out = []
    for k in range(_SPAN_STEPS):
        x = math.pi * k * FLICKER_SPAN_US * hz / 1000000.0
        shrink = math.sin(x) / x if x else 1.0
        out.append(int(round(FLICKER_ONE * (1 / shrink if grow else shrink))))
    return tuple(out)


_SHRINK = tuple(_span_table(hz, False) for hz in FLICKER_HZ)
_GROW = tuple(_span_table(hz, True) for hz in FLICKER_HZ)


# The fit: each badge fits the room's flicker in the shared gap, its peer
# dark, as one cycle of a mains in FLICKER_MAINS -- the region, the light's
# shape, a correction table (spec 15). A window left noisier than
# FLICKER_FIT_MAX_NOISE was not dark and steady, and the learner runs as
# before.
FLICKER_MAINS = (50, 60)
# Four harmonics first; six only if four leave too much unexplained: a
# sharp-edged LED's 360 Hz. Six always fit the sensor's own distortion.
# The steadiness check's threshold comes from the four.
FLICKER_BASE_HARMONICS = 4
FLICKER_HARMONICS = 6
FLICKER_FIT_MAX_NOISE = 12
# A harmonic counts only this many standard errors clear of the noise.
FLICKER_FIT_SIGMAS = 4
# ... and only if the samples' spans keep this much of it.
FLICKER_MIN_SEEN = 0.3
# Refinements of the fit's projections, each on what the others leave.
FLICKER_FIT_PASSES = 2
# Fine samples kept for the Leader's fit and re-read of the staircase.
FLICKER_BUFFER = 640

# The mains drifts, so the table's timing is phase-locked: every
# FLICKER_TRACK_POINTS dark samples a least-squares phase error, of which
# FLICKER_TRACK_GAIN moves the offset. The rate is a line through the
# timings, forgetting FLICKER_RATE_MEMORY an update, used once its points
# span FLICKER_RATE_MIN_S and FLICKER_RATE_MIN_UPDATES support it.
FLICKER_TRACK_POINTS = 8
FLICKER_TRACK_GAIN = 0.25
FLICKER_RATE_MEMORY = 0.99
FLICKER_RATE_MIN_S = 0.3
FLICKER_RATE_MIN_UPDATES = 10
# Steps a second: 0.1 Hz of drift, five times what grids allow.
FLICKER_MAX_RATE = 32
# One update's error is clipped to this, in steps.
FLICKER_TRACK_MAX_STEPS = 16


_GAINS = {}


def _gains(mains, h):
    """sin(x)/x for harmonic `h` of `mains`, per span step, scaled by
    FLICKER_ONE: what a sample's averaging leaves of it. Made once each."""
    key = mains * 16 + h
    out = _GAINS.get(key)
    if out is None:
        out = _GAINS[key] = _span_table(mains * h, False)
    return out


def _span_step(span_us):
    k = (span_us + FLICKER_SPAN_US // 2) // FLICKER_SPAN_US
    return k if k < _SPAN_LAST else _SPAN_LAST


def _steady_rms():
    """The rms a harmonic of FLICKER_MIN_COUNTS leaves in fine samples at
    the least gain of any the fit would try: 1.87 counts."""
    k = _span_step(FINE_US)
    least = FLICKER_ONE
    for mains in FLICKER_MAINS:
        for h in range(1, FLICKER_BASE_HARMONICS + 1):
            gain = _gains(mains, h)[k]
            if FLICKER_MIN_SEEN * FLICKER_ONE <= gain < least:
                least = gain
    return FLICKER_MIN_COUNTS * least / FLICKER_ONE / math.sqrt(2)


# A window whose rms about a line is under this is steady.
FLICKER_STEADY_RMS = _steady_rms()


def _line(ts, vs):
    """The least-squares line through the samples, x in whole ms from the
    middle one: (middle, slope, level)."""
    n = len(ts)
    middle = ts[n // 2]
    sx = sy = sxx = sxy = 0
    for k in range(n):
        x = time.ticks_diff(ts[k], middle) // 1000
        sx += x
        sy += vs[k]
        sxx += x * x
        sxy += x * vs[k]
    spread = n * sxx - sx * sx
    slope = (n * sxy - sx * sy) / spread if spread else 0.0
    return middle, slope, (sy - slope * sx) / n


class FlickerFilter:
    """Mains flicker, fitted once (fit()) or learnt from dark slots at
    FLICKER_HZ, and taken off every sample.

    The learner sums each dark sample against the sine table in whole
    numbers; a slot's sums become an estimate once, when it is decided.
    Correcting is a table look-up and a multiply a frequency."""

    def __init__(self):
        n = len(FLICKER_HZ)
        self.amp = [0.0] * (2 * n)      # sine, cosine amplitude per frequency
        # In quarter counts, and which frequencies are taken off.
        self.amp4 = [0] * (2 * n)
        self.active = []
        # The slot being learnt: sums of value x sine/cosine and of each, of
        # value less the first, the count and the time covered.
        self.xs = [0] * (2 * n)
        self.basis = [0] * (2 * n)
        self.ref = 0
        self.sum = 0
        self.count = 0
        self.covered_us = 0
        # The fit: the mains, each harmonic's parts in counts, tables per
        # span step; `steady`, nothing to take off.
        self.mains = 0
        self.harmonics = None
        self.steady = False
        self.base = None
        self.tables = {}
        # The table in use, and the span it is for.
        self.span_us = None
        self.wave = self.wave_slope = None
        # What the last fit saw: why it refused ("few", "unfit", "dof",
        # "noise", or None), the mains, the noise, each harmonic's size.
        self.fit_info = None
        # The phase lock: offset and rate in thousandths (and as floats),
        # its anchor, and the tracker's sums since the last update.
        self.off_m = 0
        self.rate_m = 0
        self.offset = 0.0
        self.rate = 0.0
        self.anchor_us = None
        self.last_us = 0
        # The rate's line: weighted sums of 1, t, m, t*t, t*m.
        self.fit_us = 0
        self.lw = self.lt = self.lm = self.ltt = self.ltm = 0.0
        self.clear_track()

    def clear_track(self):
        self.tn = 0
        self.tr = self.td = self.trd = self.tdd = 0

    def refresh(self):
        """Brings what correct() uses up to date with `amp`."""
        floor = FLICKER_MIN_COUNTS * FLICKER_MIN_COUNTS
        active = []
        for i in range(len(FLICKER_HZ)):
            a = self.amp[2 * i]
            b = self.amp[2 * i + 1]
            self.amp4[2 * i] = int(round(4 * a))
            self.amp4[2 * i + 1] = int(round(4 * b))
            if a * a + b * b >= floor:
                active.append(i)
        self.active = active

    def fit(self, ts, vs, spans):
        """fit_with() FLICKER_BASE_HARMONICS, and FLICKER_HARMONICS if that
        leaves too much unexplained."""
        verdict = self.fit_with(ts, vs, spans, FLICKER_BASE_HARMONICS)
        if verdict is None and self.fit_info["reason"] == "noise":
            verdict = self.fit_with(ts, vs, spans, FLICKER_HARMONICS)
        return verdict

    def fit_with(self, ts, vs, spans, count):
        """Fits samples -- times in us, means, spans in us -- as one cycle of
        each mains with up to `count` harmonics on a line, keeping the mains
        that explains more: "flicker", "steady", or None (the learner goes
        on). Each harmonic is projected on its own in whole numbers, refined
        FLICKER_FIT_PASSES times: it runs mid-calibration, a stall."""
        n = len(ts)
        self.fit_info = {"reason": "few", "mains": None, "noise": None,
                         "sizes": None}
        if n < 12:
            return None
        # The line: a sensor still settling, else taken for 50 Hz.
        middle, slope, level = _line(ts, vs)
        flat = [0] * n
        for k in range(n):
            x = time.ticks_diff(ts[k], middle) // 1000
            flat[k] = int(round(vs[k] - level - slope * x))
        best = None
        for mains in FLICKER_MAINS:
            found = self.fit_mains(ts, flat, spans, mains, count)
            if found is not None and (best is None or found[0] < best[0]):
                best = found
        info = self.fit_info
        if best is None:
            info["reason"] = "unfit"
            return None
        left, mains, used, harmonics, seen = best
        info["mains"] = mains
        sizes = []
        for a, b in harmonics:
            sizes.append(math.sqrt(a * a + b * b))
        info["sizes"] = sizes
        dof = n - 2 - 2 * used
        if dof < 4:
            info["reason"] = "dof"
            return None
        noise = math.sqrt(max(0, left) / dof)
        info["noise"] = noise
        if noise > FLICKER_FIT_MAX_NOISE:
            info["reason"] = "noise"
            return None
        info["reason"] = None
        # A harmonic as the samples saw it, FLICKER_FIT_SIGMAS of its error
        # clear, or dropped.
        error = noise * math.sqrt(2.0 / n)
        kept = []
        strongest = 0.0
        for h in range(len(harmonics)):
            a, b = harmonics[h]
            size = sizes[h]
            if (size * seen[h] >= FLICKER_FIT_SIGMAS * error
                    and size >= FLICKER_KEEP_COUNTS):
                kept.append((a, b))
                strongest = max(strongest, size)
            else:
                kept.append((0.0, 0.0))
        if strongest < FLICKER_MIN_COUNTS:
            self.steady = True
            return "steady"
        self.mains = mains
        self.harmonics = kept
        self.base = None
        self.tables = {}
        self.span_us = None
        self.anchor_us = ts[-1]
        self.fit_us = ts[-1]
        return "flicker"

    def steady_window(self, ts, vs):
        """Whether a window's rms about a line is under FLICKER_STEADY_RMS:
        a window whose fit would find the room steady, without the fit's
        stall."""
        n = len(ts)
        if n < 12:
            return False
        middle, slope, level = _line(ts, vs)
        left = 0.0
        for k in range(n):
            r = vs[k] - level - slope * (time.ticks_diff(ts[k], middle)
                                         // 1000)
            left += r * r
        return left < FLICKER_STEADY_RMS * FLICKER_STEADY_RMS * (n - 2)

    def fit_mains(self, ts, flat, spans, mains, count=FLICKER_BASE_HARMONICS):
        """One mains' projections: (unexplained, mains, harmonics fitted,
        [(a, b) in counts], how much of each the samples saw). One the spans
        shrink below FLICKER_MIN_SEEN is left out: dividing by the shrink
        magnifies noise into flicker."""
        n = len(ts)
        left = flat[:]
        # Per harmonic: each sample's sine and cosine, shrunk by its span;
        # what the fit takes off it so far.
        sines = []
        cosines = []
        parts = []
        seen = [0.0] * count
        used = []
        for h in range(1, count + 1):
            gains = _gains(mains, h)
            total = 0
            for k in range(n):
                total += gains[_span_step(spans[k])]
            seen[h - 1] = total / (n * FLICKER_ONE)
            if seen[h - 1] < FLICKER_MIN_SEEN:
                continue
            si = [0] * n
            co = [0] * n
            for k in range(n):
                g = gains[_span_step(spans[k])]
                j = (ts[k] % 1000000) * mains * h // _US_PER_STEP_HZ \
                    % FLICKER_STEPS
                si[k] = _SINE[j] * g // FLICKER_ONE
                co[k] = _SINE[(j + _QUARTER) % FLICKER_STEPS] * g // FLICKER_ONE
            used.append(h)
            sines.append(si)
            cosines.append(co)
            parts.append([0] * n)
        coeffs = [(0.0, 0.0)] * len(used)
        # The line refitted each pass: over 100 ms a sine is not independent
        # of a ramp.
        middle = ts[n // 2]
        xs = [time.ticks_diff(ts[k], middle) / 1000.0 for k in range(n)]
        sx = sxx = 0.0
        for k in range(n):
            sx += xs[k]
            sxx += xs[k] * xs[k]
        spread = n * sxx - sx * sx
        for _ in range(FLICKER_FIT_PASSES):
            for u in range(len(used)):
                si = sines[u]
                co = cosines[u]
                part = parts[u]
                ss = cc = sc = ys = yc = 0
                for k in range(n):
                    y = left[k] + part[k]
                    s1 = si[k]
                    c1 = co[k]
                    ss += s1 * s1
                    cc += c1 * c1
                    sc += s1 * c1
                    ys += y * s1
                    yc += y * c1
                det = ss * cc - sc * sc
                if not det:
                    continue
                a = (ys * cc - yc * sc) / det
                b = (yc * ss - ys * sc) / det
                coeffs[u] = (a, b)
                for k in range(n):
                    new = int(round(a * si[k] + b * co[k]))
                    left[k] += part[k] - new
                    part[k] = new
            if spread:
                sy = sxy = 0.0
                for k in range(n):
                    sy += left[k]
                    sxy += xs[k] * left[k]
                slope = (n * sxy - sx * sy) / spread
                level = (sy - slope * sx) / n
                for k in range(n):
                    left[k] -= int(round(level + slope * xs[k]))
        remaining = 0
        for k in range(n):
            remaining += left[k] * left[k]
        harmonics = [(0.0, 0.0)] * count
        for u in range(len(used)):
            a, b = coeffs[u]
            harmonics[used[u] - 1] = (a * FLICKER_ONE, b * FLICKER_ONE)
        return (remaining, mains, len(used), harmonics, seen)

    def table(self, k):
        """The fitted flicker and its slope per step, in quarter counts, as
        a sample of span step `k` sees them. Built once per span step."""
        out = self.tables.get(k)
        if out is None:
            out = self.tables[k] = self.build_table(k)
        return out

    def use_span(self, span_us):
        self.wave, self.wave_slope = self.table(_span_step(span_us))
        self.span_us = span_us

    def build_table(self, k):
        """table(k), from one base table per harmonic made once a fit: a
        span step costs a pass of FLICKER_STEPS, not trigonometry."""
        if self.base is None:
            self.base = []
            for h in range(1, len(self.harmonics) + 1):
                a, b = self.harmonics[h - 1]
                if not a and not b:
                    continue
                a4 = int(round(4 * a))
                b4 = int(round(4 * b))
                line = [0] * FLICKER_STEPS
                for j in range(FLICKER_STEPS):
                    line[j] = (a4 * _SINE[j] + b4 * _SINE[
                        (j + _QUARTER) % FLICKER_STEPS]) // FLICKER_ONE
                self.base.append((h, line))
        flicker = [0] * FLICKER_STEPS
        for h, line in self.base:
            g = _gains(self.mains, h)[k]
            for i in range(FLICKER_STEPS):
                flicker[i] += line[h * i % FLICKER_STEPS] * g
        for i in range(FLICKER_STEPS):
            flicker[i] //= FLICKER_ONE
        slope = [0] * FLICKER_STEPS
        for i in range(FLICKER_STEPS):
            slope[i] = (flicker[(i + 1) % FLICKER_STEPS]
                        - flicker[i - 1]) // 2
        return (flicker, slope)

    def step_at(self, now_us):
        """Where `now_us` falls in the fitted table, the phase lock applied:
        shift_at() written out, a call less on the hot path."""
        elapsed_ms = time.ticks_diff(now_us, self.anchor_us) // 1000
        if elapsed_ms > 30000:
            elapsed_ms = 30000
        return ((now_us % 1000000) * self.mains // _US_PER_STEP_HZ
                + (self.off_m + elapsed_ms * self.rate_m // 1000) // 1000
                ) % FLICKER_STEPS

    def shift_at(self, now_us):
        """The phase lock's shift at `now_us`, in steps: monotonic, so equal
        ends of a span hold it constant between."""
        elapsed_ms = time.ticks_diff(now_us, self.anchor_us) // 1000
        if elapsed_ms > 30000:
            elapsed_ms = 30000
        return (self.off_m + elapsed_ms * self.rate_m // 1000) // 1000

    def amounts(self, times, n, span_us, out):
        """out[j] = amount(times[j], span_us) for j < n, bit for bit, with
        `times` ascending: a fitted filter looks its table and shift up
        once a call, not once a time."""
        if self.harmonics is None or n < 1:
            for j in range(n):
                out[j] = self.amount(times[j], span_us)
            return
        flicker, _ = self.table(_span_step(span_us))
        shift = self.shift_at(times[0])
        if shift != self.shift_at(times[n - 1]):
            for j in range(n):
                out[j] = (flicker[self.step_at(times[j])] + 2) // 4
            return
        mains = self.mains
        for j in range(n):
            out[j] = (flicker[((times[j] % 1000000) * mains
                               // _US_PER_STEP_HZ + shift)
                              % FLICKER_STEPS] + 2) // 4

    def track(self, now_us, value, span_us):
        """A sample the peer was dark for, toward the next phase-lock
        update: its residual against the table, and the table's slope."""
        if span_us != self.span_us:
            self.use_span(span_us)
        i = self.step_at(now_us)
        r = 4 * value - self.wave[i]
        d = self.wave_slope[i]
        self.tn += 1
        self.tr += r
        self.td += d
        self.trd += r * d
        self.tdd += d * d
        self.last_us = now_us

    def update(self):
        """The phase lock: the timing error that best explains the points
        since the last update, and a step toward it, of the offset and of
        its rate. Then clears them."""
        n = self.tn
        if n >= FLICKER_MIN_POINTS:
            var = self.tdd - self.td * self.td / n
            if var > 0:
                error = (self.trd - self.tr * self.td / n) / var
                if error > FLICKER_TRACK_MAX_STEPS:
                    error = FLICKER_TRACK_MAX_STEPS
                elif error < -FLICKER_TRACK_MAX_STEPS:
                    error = -FLICKER_TRACK_MAX_STEPS
                now = self.last_us
                # Unwrapped, so the line through the points is too.
                elapsed = time.ticks_diff(now, self.anchor_us) / 1000000.0
                predicted = self.offset + self.rate * min(elapsed, 30.0)
                measured = predicted + error
                t = time.ticks_diff(now, self.fit_us) / 1000000.0
                k = FLICKER_RATE_MEMORY
                self.lw = k * self.lw + 1.0
                self.lt = k * self.lt + t
                self.lm = k * self.lm + measured
                self.ltt = k * self.ltt + t * t
                self.ltm = k * self.ltm + t * measured
                w = self.lw
                spread = self.ltt / w - (self.lt / w) ** 2
                if (w >= FLICKER_RATE_MIN_UPDATES
                        and spread >= FLICKER_RATE_MIN_S * FLICKER_RATE_MIN_S / 4):
                    rate = (self.ltm / w - self.lt / w * self.lm / w) / spread
                    if rate > FLICKER_MAX_RATE:
                        rate = float(FLICKER_MAX_RATE)
                    elif rate < -FLICKER_MAX_RATE:
                        rate = -float(FLICKER_MAX_RATE)
                    self.rate = rate
                self.offset = predicted + FLICKER_TRACK_GAIN * error
                self.anchor_us = now
                self.off_m = int(self.offset * 1000)
                self.rate_m = int(self.rate * 1000)
        self.clear_track()

    def amount(self, now_us, span_us=0):
        """The flicker at `now_us` in a mean over `span_us`, whole counts.

        This and the other per-sample calls keep their frames at 11 words
        or fewer: a larger one is a heap allocation every call
        (VM_MAX_STATE_ON_STACK; test_flicker_frames.py). The learner's paths
        are separate functions so the fitted ones carry none of their
        frame."""
        if self.harmonics is not None:
            if span_us != self.span_us:
                self.use_span(span_us)
            return (self.wave[self.step_at(now_us)] + 2) // 4
        if self.steady or not self.active:
            return 0
        return self.learnt(now_us, span_us)

    def learnt(self, now_us, span_us):
        """amount() from the learner's sines."""
        active = self.active
        t = now_us % 1000000
        k = _span_step(span_us)
        amp4 = self.amp4
        flicker = 0                     # quarter counts
        for i in active:
            j = t * FLICKER_HZ[i] // _US_PER_STEP_HZ % FLICKER_STEPS
            part = (amp4[2 * i] * _SINE[j]
                    + amp4[2 * i + 1] * _SINE[(j + _QUARTER) % FLICKER_STEPS]
                    ) // FLICKER_ONE
            flicker += part * _SHRINK[i][k] // FLICKER_ONE
        return (flicker + 2) // 4

    def active_now(self):
        """Whether amount() can be anything but 0."""
        return self.harmonics is not None or (not self.steady
                                               and bool(self.active))

    def correct(self, now_us, value, span_us=0):
        """`value`, a mean over `span_us` centred on `now_us`, less the
        flicker there, as a whole count."""
        corrected = value - self.amount(now_us, span_us)
        return corrected if corrected > 0 else 0

    def collect(self, now_us, value, span_us):
        """A raw mean over `span_us` at `now_us`, from a slot's dark part:
        tracked if fitted, else learnt -- scaled back up by its span's
        shrink, so correct() shrinks it only once."""
        if self.harmonics is not None:
            self.track(now_us, value, span_us)
        elif not self.steady:
            self.learn(now_us, value, span_us)

    def learn(self, now_us, value, span_us):
        """collect() for the learner: the point's products with each sine
        and cosine, toward close()."""
        if not self.count:
            self.ref = value
        x = value - self.ref
        self.covered_us += span_us
        self.count += 1
        self.sum += x
        t = now_us % 1000000
        k = _span_step(span_us)
        xs = self.xs
        basis = self.basis
        for i in range(len(FLICKER_HZ)):
            j = t * FLICKER_HZ[i] // _US_PER_STEP_HZ % FLICKER_STEPS
            grow = _GROW[i][k]
            s = _SINE[j] * grow // FLICKER_ONE
            c = _SINE[(j + _QUARTER) % FLICKER_STEPS] * grow // FLICKER_ONE
            xs[2 * i] += x * s
            xs[2 * i + 1] += x * c
            basis[2 * i] += s
            basis[2 * i + 1] += c

    def close(self, dark):
        """The slot is decided: learn from it if it was dark, then clear it.
        Once a slot, so floats are affordable here."""
        if self.harmonics is not None:
            if dark:
                self.update()
            else:
                self.clear_track()
            return
        n = self.count
        if dark and n >= FLICKER_MIN_POINTS:
            mean = self.sum / n
            # Slowly: a slot misjudged dark, weighted fully, set a wrong
            # estimate that then corrupted the readings that taught it.
            weight = min(1.0, self.covered_us / (FLICKER_TAU_MS * 1000.0))
            scale = 2.0 / (n * FLICKER_ONE)
            for k in range(len(self.amp)):
                estimate = (self.xs[k] - mean * self.basis[k]) * scale
                self.amp[k] += weight * (estimate - self.amp[k])
            self.refresh()
        self.count = 0
        self.covered_us = 0
        self.sum = 0
        for k in range(len(self.xs)):
            self.xs[k] = 0
            self.basis[k] = 0


# How late a receiver sees a backlight edge after the transmitter's slot
# boundary: its lateness, up to a PWM period, the sensor's climb, and a
# transfer's own path [measured-v2]. A receiver's constant, not the wire's:
# it places only this badge's grid. One lock's scatter is its message's.
EDGE_LAG_EXACT_LIT_US = 950
EDGE_LAG_EXACT_DARK_US = 1650

# The end-of-slot guard as a share of the slot, at least: a rise to the
# middle crosses T_low later than one to high. Proportional, so a demotion
# widens it.
BACKLIGHT_GUARD_PERCENT = 15

# Slowest first: the slot rungs control messages go at. A pair starts on 8
# ms lit or 16 dark and only steps down, on a Data Sync Error; the payload
# goes in fast blocks, whose own rungs step down on NAKs.
SLOT_LADDER = (32, 25, 20, 16, 13, 10, 8)

# Where a slot is decided, in us from its start:
#   opens    max(settle, OPEN_PERCENT of the slot)
#   closes   slot - max(guard, BACKLIGHT_GUARD_PERCENT of the slot)
# Settle, by this badge's light: the transmitter's lateness, the sensor's 90%
# from the dim low, an early lock [measured-v2]. Guard: a rise to the middle
# crosses T_low later than one to high.
SETTLE_LIT_US = 2500
SETTLE_DARK_US = 5000
GUARD_LIT_US = 1000
GUARD_DARK_US = 2000
OPEN_PERCENT = 40
# A window this long at least, or a pair does not start on the rung.
WINDOW_MIN_US = 2000
# No slot shorter than this: twice a slot's worst work on v2.
SLOT_MIN_MS = 6
# A window is the median of PARTS_MIN to PARTS_MAX parts, each at most
# PART_MAX_US, inside the flicker's span table.
PART_MAX_US = 4000
PARTS_MIN = 5
PARTS_MAX = 32

# The Announce: the backlight off and full at ANNOUNCE_FAST_HALF_MS halves,
# matched over BEACON_FAST_HALVES of them within BEACON_TOLERANCE -- clear of
# mains flicker's halves and a once-a-cycle light's. The Follower answers with
# its staircase.
BEACON_TOLERANCE = 0.20
ANNOUNCE_FAST_HALF_MS = 14
BEACON_FAST_HALVES = 6

# Calibration: the Follower's staircase, sent the moment it detects the
# Announce, sampled at FINE_MS; a step needs the sensor's settle
# and MIN_HELD_FAST settled samples a level. Its repetitions are the light
# flag: one LIT, two dark (spec 9.5).
PLATEAU_FAST_MS = 20
CAL_REPS_FAST = 2
CAL_REPS_FAST_LIT = 1
LIT_STEPS_MAX_FAST = 1
MIN_HELD_FAST = 5
CAL_FAST_MIN_SAMPLES = 24
CAL_OPEN_FAST_MS = 40
# The Leader reads the staircase while it announces, over its last
# LEADER_CAL_LIMIT samples, finding the closing gap on a moving mean of one
# 50 Hz cycle: no flicker is fitted yet.
LEADER_CAL_LIMIT = 160
LEADER_CAL_SMOOTH = 10
# Refusals before both end ERR_CONTRAST: one may be hands still moving.
CAL_RESTARTS = 1
# Closing gaps are found CAL_CLOSE_FAST_SPARE_MS before they end.
CAL_CLOSE_FAST_MS = 70
CAL_CLOSE_FAST_SPARE_MS = 20
# The shared gap. The Leader, LEADER_SETTLE_MS in, fits the flicker on it
# (less LEADER_FIT_SKIP_MS for the last fall) and re-reads the staircase
# corrected. The Follower checks for steady from FOLLOWER_FIT_START_MS over
# FOLLOWER_CHECK_MS, and fits over FLICKER_FAST_FIT_MS if not.
CAL_GAP_FAST_MS = CAL_CLOSE_FAST_MS - CAL_CLOSE_FAST_SPARE_MS + 60
LEADER_SETTLE_MS = 100
LEADER_FIT_SKIP_MS = 10
FOLLOWER_FIT_START_MS = 70
FOLLOWER_CHECK_MS = 60
FLICKER_FAST_FIT_MS = 100
# In flicker the Leader holds its first block back to
# CAL_GAP_FAST_FLICKER_MS after the gap began, while both lock onto the
# flicker's drift; the epoch does not move. A Follower waits that and
# FAST_START_GRACE_MS more before calling the Leader lost, and one that found
# flicker takes no light for a lock until HOLD_MARGIN_SLOTS before it ends.
CAL_GAP_FAST_FLICKER_MS = 500
FAST_START_GRACE_MS = 100
HOLD_MARGIN_SLOTS = 4
# A Follower's provisional middle, until a Begin trains its levels.
PROVISIONAL_GREY_PERCENT = 45
# A phase lock's crossing must stay lit from LOCK_SKIP_US to LOCK_HOLD_US
# after it: a flicker peak the fit left falls back.
LOCK_SKIP_US = 500
LOCK_HOLD_US = 3000
# A Follower that demoted on its own measures the Leader's next message over
# RATE_CHECK_SLOTS and moves to its rung, within RATE_MATCH_PERCENT: an error
# the Leader never heard left the two a rung apart.
RATE_CHECK_SLOTS = 7
RATE_MATCH_PERCENT = 8

# The fast block (fastblock.py, spec 13.1): a burst of 2-level symbols, each
# RUNGS[i] PWM periods, after the lead-in, FAST_BEGIN, the rung in
# FAST_RUNG_BITS and FAST_GAP_SLOTS of low; answered ACK or NAK. Lit pairs
# start on F0, dark on F1. FAST_TRIES NAKs step down a rung, and past F3 the
# transfer ends ERR_CRC; FAST_UP_ACKS ACKs step back up, the wait doubling
# after a failed step. The Follower finds the burst's rise within FAST_SEARCH_US, less its
# edge lag, and decodes only once the burst is over -- a training outlasts
# the ring. The Leader waits FAST_REPLY_MS past the longest burst.
FAST_GAP_SLOTS = 2
FAST_RUNG_BITS = 2
FAST_START_LIT, FAST_START_DARK = 0, 1
FAST_TRIES = 2
FAST_UP_ACKS = 4
FAST_SEARCH_US = 3000
EDGE_LAG_FAST_LIT_US = 400
EDGE_LAG_FAST_DARK_US = 750
FAST_REPLY_MS = 800
FAST_BIN_US = 100
FAST_BINS = 12000
# A staircase starts on a pass with less than this of the ring to work
# through (see Transfer.enter)...
CAL_CAUGHT_UP_US = 2 * FINE_US
# ... or this long after the pass that entered it.
CAL_PENDING_MAX_MS = 250

# A transfer this long ends: relative us stay a small int.
TRANSFER_MAX_US = (1 << 30) - (1 << 26)

ANNOUNCE_TIMEOUT_MS = 60000

# Per block, so payload length stays the caller's business.
BLOCK_TIMEOUT_MS = 300000

LOST_PEER_SLOTS = 16

# Slots without progress: a peer stuck on grey decodes something every slot
# and never counts as silent.
STALL_SLOTS = 4 * LOST_PEER_SLOTS

# Lit slots a fast-capable Follower decides framing nothing, after which
# RESEND_QUIET_SLOTS of black ask for the block again: a fast block missed
# at its Begin is too short for STALL_SLOTS.
UNFRAMED_LIT_SLOTS = 16

# Black a Follower waits out before asking again, so the request cannot land
# while the Leader, deaf while it transmits, is still sending.
RESEND_QUIET_SLOTS = 3

# Complete is acknowledged, not assumed: the Leader repeats it until
# answered, the Follower lingers to answer a repeat, and the repeat waits
# out lit slots but no longer than COMPLETE_REPEAT_MAX_SLOTS.
COMPLETE_RETRY_SLOTS = 12       # > len(COMPLETE_ACK) + 2
MAX_COMPLETE_RETRIES = 3
LINGER_SLOTS = 20               # > COMPLETE_RETRY_SLOTS, so a repeat is answered
COMPLETE_REPEAT_MAX_SLOTS = LINGER_SLOTS - 2

# Transmissions of one block, NAKs and Data Sync Errors alike.
MAX_BLOCK_ATTEMPTS = 48

RESYNC_LEAD_IN = 4    # black slots before each block, so a resend survives a rate change
MAX_PATTERN = 16      # longest run of symbols any match looks back over


# A Leader reads the slots of its own lead-in for the flicker; after a stall
# only the last WATCH_SLOTS of them.
WATCH_SLOTS = 2

# Black before a block resent after a rate change: the Follower is still
# sending the rest of its Data Sync Error at the old rate.
SYNC_LEAD_IN = 9


# --- symbols ----------------------------------------------------------------
# '-' high, 1; '_' middle, 0; ' ' low, black. A control pattern is told
# apart by the blacks inside it.

HIGH = "-"
MID = "_"
LOW = " "

CRC_GOOD = "-- -- -- "
CRC_BAD = "__ __ __ "
SYNC_ERROR = "  _  _  _"
COMPLETE = "- - - "
# A fast block's Begin: high, middle and low three times, to train on.
FAST_BEGIN = "-_ -_ _- "


# --- light levels -----------------------------------------------------------
# Every symbol is a display.backlight() value behind a white screen drawn
# once: a PWM duty, live at once. A receiver never needs to know the table;
# calibration measures what arrives (spec 4). The data's low is dim, not
# off, which keeps the sensor faintly lit and fast. It works only because
# the sensor averages the PWM [measured-v2].
SCHEME_BACKLIGHT = "backlight"

# The beacons': full and off, the darkest the screen gets.
SCHEME_BEACON = "beacon"
BEACON_LOW_BACKLIGHT = 0.0

MID_BACKLIGHT = 0.82
LOW_BACKLIGHT = 0.5

# display.backlight() per symbol, per table.
LEVEL_SCHEMES = {
    SCHEME_BACKLIGHT: {HIGH: 1.0, MID: MID_BACKLIGHT, LOW: LOW_BACKLIGHT},
    SCHEME_BEACON: {HIGH: 1.0, MID: MID_BACKLIGHT, LOW: BEACON_LOW_BACKLIGHT},
}


# A square wave against black, told apart by period alone: before
# calibration there are no thresholds.
def beacon_field(elapsed, half_ms):
    return HIGH if (elapsed // half_ms) % 2 else LOW

# The Follower's answer to Complete, the only thing that lets the Leader
# report OK.
COMPLETE_ACK = "__- __- "


# Receivers match without a pattern's trailing black, the hardest symbol to
# read; the black inside keeps them unforgeable.
CRC_GOOD_TAIL = CRC_GOOD.rstrip(LOW)
CRC_BAD_TAIL = CRC_BAD.rstrip(LOW)
COMPLETE_TAIL = COMPLETE.rstrip(LOW)
COMPLETE_ACK_TAIL = COMPLETE_ACK.rstrip(LOW)
# The Leader takes the Ack less its first symbol: it follows Complete by one
# silent slot, and a slow sensor's first middle comes too late. Still
# unforgeable: only the Ack has a black after "-" between lit symbols.
COMPLETE_ACK_LATE_TAIL = COMPLETE_ACK_TAIL[1:]
SYNC_ERROR_TAIL = SYNC_ERROR.rstrip(LOW)


# --- exit values ------------------------------------------------------------

OK = "OK"
ERR_NO_PEER = "ERR_NO_PEER"
ERR_CONTRAST = "ERR_CONTRAST"
ERR_LOST_PEER = "ERR_LOST_PEER"
ERR_CRC = "ERR_CRC"
ERR_DECODE = "ERR_DECODE"
ERR_CANCELLED = "ERR_CANCELLED"
ERR_RING = "ERR_RING"


# --- beacon detection -------------------------------------------------------

# A swing of MIN_SWING or more, told apart by period; its black over
# BEACON_WINDOW_MS.
BEACON_WINDOW_MS = 1000


class BeaconDetector:
    CAPACITY = 128

    def __init__(self):
        self.times = [0] * self.CAPACITY
        self.values = [0] * self.CAPACITY
        self.window = [0] * self.CAPACITY
        self.head = 0
        self.count = 0

    def reset(self):
        self.head = 0
        self.count = 0

    def push(self, now, value):
        self.times[self.head] = now
        self.values[self.head] = value
        self.head = (self.head + 1) % self.CAPACITY
        if self.count < self.CAPACITY:
            self.count += 1

    # The samples inside the window, oldest first, into a preallocated
    # list; their range in `lo` and `hi`.
    def _fill_window(self, window_ms=BEACON_WINDOW_MS):
        newest = self.times[(self.head - 1) % self.CAPACITY]
        found = lo = hi = 0
        for i in range(self.count):
            idx = (self.head - 1 - i) % self.CAPACITY
            if time.ticks_diff(newest, self.times[idx]) > window_ms:
                break
            self.window[found] = idx
            value = self.values[idx]
            if not found or value < lo:
                lo = value
            if not found or value > hi:
                hi = value
            found += 1
        self.lo = lo
        self.hi = hi
        for i in range(found // 2):             # reverse in place, chronological
            self.window[i], self.window[found - 1 - i] = \
                self.window[found - 1 - i], self.window[i]
        return found

    def half_period(self, window_ms, regular_ms):
        """The half period in ms over the last `window_ms`, or None. Every
        interval between crossings within half of `regular_ms`, and three
        crossings at least: noise read every 8-16 ms once averaged to a
        beacon's half."""
        count = self._fill_window(window_ms)
        if count < 8:
            return None

        if self.hi - self.lo < MIN_SWING:
            return None

        mid = (self.lo + self.hi) // 2
        # About half the time either side of the middle: a quiet window with
        # an outlier has the swing but not that.
        high = 0
        for i in range(count):
            if self.values[self.window[i]] >= mid:
                high += 1
        if min(high, count - high) < count // 4:
            return None
        above = self.values[self.window[0]] >= mid
        first = last = None
        crossings = 0
        for i in range(1, count):
            idx = self.window[i]
            now_above = self.values[idx] >= mid
            if now_above != above:
                t = self.times[idx]
                if first is None:
                    first = t
                elif abs(time.ticks_diff(t, last)
                         - regular_ms) * 2 > regular_ms:
                    return None
                last = t
                crossings += 1
                above = now_above
        if crossings < 3:
            return None
        return time.ticks_diff(last, first) // (crossings - 1)

    def low(self):
        """The beacon's black, or None: the median of the window's darker
        half, not its darkest reading, which one outlier decides."""
        return self._half(False)

    def high(self):
        """The beacon's full: the median of the brighter half, or None."""
        return self._half(True)

    def _half(self, upper):
        count = self._fill_window()
        if not count:
            return None
        mid = (self.lo + self.hi) // 2
        half = sorted(self.values[self.window[i]] for i in range(count)
                      if (self.values[self.window[i]] > mid) == upper)
        return half[len(half) // 2] if half else None

    def matches(self, target_ms, window_ms=None):
        """Whether the last `window_ms` -- BEACON_FAST_HALVES halves if None
        -- hold a beacon of half period `target_ms`, crossing regularly."""
        if window_ms is None:
            window_ms = BEACON_FAST_HALVES * target_ms
        measured = self.half_period(window_ms, target_ms)
        if measured is None:
            return False
        return abs(measured - target_ms) <= target_ms * BEACON_TOLERANCE



# --- calibration ------------------------------------------------------------

class Levels:
    def __init__(self):
        self.black = 0
        self.grey = 0
        self.white = 0
        self.t_low = 0
        self.t_high = 0
        self.band = 0
        self.last = MID
        # Each level's noise on this link, from calibration (sd, counts).
        self.noise_black = 0.0
        self.noise_grey = 0.0
        self.noise_white = 0.0

    def fit(self, black, grey, white):
        self.black, self.grey, self.white = black, grey, white
        self.t_low = (black + grey) // 2
        self.t_high = (grey + white) // 2
        self.band = (white - black) * 5 // 100

    def classify(self, value):
        # Hysteresis, but a band holds a level only against the threshold
        # beside it: a value at T_high is never low.
        last = self.last
        if abs(value - self.t_low) <= self.band and last != HIGH or \
                abs(value - self.t_high) <= self.band and last != LOW:
            return last
        if value < self.t_low:
            self.last = LOW
        elif value < self.t_high:
            self.last = MID
        else:
            self.last = HIGH
        return self.last


# The staircase: `reps` passes of three levels between longer black gaps.
# It identifies itself -- nothing else steps through three levels -- and its
# closing gap is a time both badges can find.
def staircase_ms(reps, open_ms, plateau_ms, close_ms):
    return open_ms + reps * 3 * plateau_ms + close_ms


# Its order: low, high, middle. The levels come from the samples'
# distribution, which order does not change.
STAIRCASE = (LOW, HIGH, MID)

# Samples a level holds to count as a step rather than a transition.
STEP_SAMPLES = 3


def staircase_field(elapsed, order, reps, open_ms, plateau_ms):
    if elapsed < open_ms:
        return LOW
    body = elapsed - open_ms
    if body >= reps * 3 * plateau_ms:
        return LOW
    return order[(body // plateau_ms) % 3]


def staircase_highs(values, levels):
    """High steps in a measured staircase -- its repetitions, the light
    flag: a step is a level held STEP_SAMPLES samples."""
    highs = held = 0
    current = None
    for value in values:
        if value < levels.t_low:
            level = LOW
        elif value < levels.t_high:
            level = MID
        else:
            level = HIGH
        if level == current:
            held += 1
        else:
            current, held = level, 1
        if held == STEP_SAMPLES and level == HIGH:
            highs += 1
    return highs


def quantile(sorted_values, fraction):
    index = int(len(sorted_values) * fraction)
    return sorted_values[min(index, len(sorted_values) - 1)]


def held_spreads(values, t_low, t_high, min_held=MIN_HELD_FAST):
    """Each level's noise from a staircase, (black, grey, white) sds, or
    None if a level has fewer than `min_held` settled samples: a sample
    counts once STEP_SAMPLES in a row share its level. Welford's, so
    float32 keeps its precision."""
    n = [0, 0, 0]
    mean = [0.0, 0.0, 0.0]
    m2 = [0.0, 0.0, 0.0]
    previous = -1
    streak = 0
    for v in values:
        level = 0 if v < t_low else (1 if v < t_high else 2)
        streak = streak + 1 if level == previous else 1
        previous = level
        if streak < STEP_SAMPLES:
            continue
        n[level] += 1
        delta = v - mean[level]
        mean[level] += delta / n[level]
        m2[level] += delta * (v - mean[level])
    if min(n) < min_held:
        return None
    return (math.sqrt(m2[0] / n[0]), math.sqrt(m2[1] / n[1]),
            math.sqrt(m2[2] / n[2]))


def gap_separable(gap, noise_a, noise_b, min_gap=MIN_GAP_EXACT):
    """Whether two levels `gap` apart, with these noises, can be told apart."""
    noise = max(noise_a, noise_b, QUANTISATION_SD)
    return gap >= min_gap and gap >= GAP_SIGMAS * noise


# Reads a staircase without knowing when it started: every sample kept, the
# levels read from their distribution by percentiles.
class StaircaseReader:
    LIMIT = 512
    # Dark samples kept before the first rise: all of the gaps would push
    # the high level under the 0.90 quantile.
    LEAD_KEEP = 12

    def __init__(self, limit=None, smooth=0):
        self.close_found_ms = CAL_CLOSE_FAST_MS - CAL_CLOSE_FAST_SPARE_MS
        self.limit = limit or self.LIMIT
        # Samples the closing gap is found on the mean of (a Leader's).
        self.smooth = smooth
        self.recent = []
        self.recent_at = []
        self.smoothed = []
        self.values = []
        self.low_since = None
        self.low_run = 0
        self.high_run = 0
        self.ended_at = None
        self.body_end = None
        # The last two samples fed, for a median of three, one sample late.
        self.older = self.newer = None
        self.newer_at = 0

    def feed(self, now, value):
        if self.ended_at is not None:
            return True
        # A median of three: one outlier made min() too dark for the rest of
        # the staircase, and the closing gap never read dark.
        older, newer = self.older, self.newer
        self.older = newer
        self.newer = value
        if older is None:
            self.newer_at = now
            if newer is None:
                return False
            older = newer
        at = self.newer_at
        self.newer_at = now
        if older > newer:
            older, newer = newer, older
        value = newer if value > newer else (older if value < older
                                             else value)
        now = at
        # Rolling, not frozen at LIMIT: an early start would fill with the
        # room and never see the staircase.
        self.values.append(value)
        if len(self.values) > self.limit:
            del self.values[0]
        if len(self.values) < CAL_FAST_MIN_SAMPLES:
            return False

        if self.smooth:
            recent = self.recent
            recent.append(value)
            self.recent_at.append(now)
            if len(recent) > self.smooth:
                del recent[0]
                del self.recent_at[0]
            value = sum(recent) // len(recent)
            # The means' swing, not the raw: a once-a-cycle light's cycle
            # averages away, a 20 ms step does not.
            smoothed = self.smoothed
            if len(recent) == self.smooth:
                smoothed.append(value)
                if len(smoothed) > self.limit:
                    del smoothed[0]
            if not smoothed or max(smoothed) - min(smoothed) < MIN_SWING:
                return False
        lo = min(self.values)
        hi = max(self.values)
        if hi - lo < MIN_SWING:
            return False
        line = lo + (hi - lo) // 3
        start = now
        if self.smooth:
            # The trailing run's first raw sample under the line, or half
            # the window back if flicker leaves none.
            start = time.ticks_add(now, -self.smooth * FINE_MS // 2)
            recent = self.recent
            i = len(recent) - 1
            while i >= 0 and recent[i] <= line:
                start = self.recent_at[i]
                i -= 1

        # The closing gap: a sustained return to the darkest level; its
        # leading edge is the shared reference. One sample over the line does
        # not end the run.
        if value <= line:
            self.low_run += 1 + self.high_run
            self.high_run = 0
            if self.low_since is None:
                self.low_since = start
            elif time.ticks_diff(now, self.low_since) >= self.close_found_ms:
                self.ended_at = self.low_since
                self.body_end = len(self.values) - self.low_run
                return True
        elif self.low_since is not None and not self.high_run:
            self.high_run = 1
        else:
            self.low_since = None
            self.low_run = 0
            self.high_run = 0
        return False

    def body(self):
        """The staircase's samples, LEAD_KEEP before its first rise to its
        closing gap: the gaps only dilute the levels."""
        values = self.values
        end = len(values) if self.body_end is None else self.body_end
        if not values:
            return values
        lo = min(values)
        rise = lo + (max(values) - lo) // 4
        first = 0
        while first < end and values[first] <= rise:
            first += 1
        return values[max(0, first - self.LEAD_KEEP):end]

    def levels(self):
        """The three levels, or None if they cannot be separated."""
        values = self.body()
        if len(values) < CAL_FAST_MIN_SAMPLES:
            return None
        ordered = sorted(values)
        black = quantile(ordered, 0.10)
        white = quantile(ordered, 0.90)
        span = white - black
        if span < MIN_SWING:
            return None

        # The middle cluster by value, from everything an eighth of the swing
        # clear of either end, then held to the quarter rule -- from the
        # middle half the rule could never fail.
        lo = black + span // 8
        hi = black + 7 * span // 8
        middle = [v for v in ordered if lo <= v <= hi]
        above = sum(1 for v in ordered if v > hi)

        if len(middle) < 4:
            return None
        grey = middle[len(middle) // 2]

        # A middle step is held as long as the high one, a transition a sample
        # or two: real only if about as many samples sit around it.
        near = span // 8
        held = sum(1 for v in middle if grey - near <= v <= grey + near)
        if held * 2 < above:
            return None

        step = span // 4
        if grey - black < step or white - grey < step:
            return None
        out = Levels()
        out.fit(black, grey, white)
        # Each gap against the noisier side's noise, on this link.
        spreads = held_spreads(values, out.t_low, out.t_high)
        if spreads is None:
            return None
        out.noise_black, out.noise_grey, out.noise_white = spreads
        if not (gap_separable(grey - black, out.noise_black, out.noise_grey)
                and gap_separable(white - grey, out.noise_grey,
                                  out.noise_white)):
            return None
        return out



def bits_symbols(value, width):
    """`value`'s `width` bits as symbols, most significant first."""
    return [HIGH if (value >> shift) & 1 else MID
            for shift in range(width - 1, -1, -1)]


# --- slot clock -------------------------------------------------------------
# In us, never badge.ticks, a per-frame snapshot.

class SlotClock:
    def __init__(self):
        self.epoch = 0
        self.duration = SLOT_LADDER[0] * 1000
        self.index = 0

    def start(self, now, ladder_index):
        self.epoch = now
        self.set_index(ladder_index)

    def slow_down(self):
        # Index 0 is the slowest rung.
        self.set_index(self.index - 1)

    def set_index(self, index):
        self.index = max(0, min(len(SLOT_LADDER) - 1, index))
        self.duration = SLOT_LADDER[self.index] * 1000

    def start_of(self, number):
        return time.ticks_add(self.epoch, number * self.duration)

    def number(self, now):
        return time.ticks_diff(now, self.epoch) // self.duration



def _median3(a, b, c):
    if a > b:
        a, b = b, a
    if b > c:
        b = c
    return a if a > b else b


def _insert_sorted(buf, count, value):
    """Insert `value` into the first `count` of `buf`, kept ascending."""
    i = count
    while i > 0 and buf[i - 1] > value:
        buf[i] = buf[i - 1]
        i -= 1
    buf[i] = value


# --- transfer engine --------------------------------------------------------
# Each pass: show field_at(now), call work(now), wait for due_us(). Times
# are us since the transfer began, on the reader's clock.

PHASE_ANNOUNCE = 0     # Leader: announces, reads for the staircase
PHASE_LISTEN = 1       # Follower: holds black until it sees the Announce
PHASE_CAL_SEND = 2     # Follower: sends its staircase
PHASE_CAL_GAP = 3      # both: the shared gap, until the epoch
PHASE_DATA = 4
PHASE_DONE = 5

# A wait pumps a little past its time, so the readings are complete.
DUE_MARGIN_US = 100


def exact_window(duration_us, settle_us, guard_us):
    """Where a slot is decided: (open, close) in us from its start."""
    opens = max(settle_us, duration_us * OPEN_PERCENT // 100)
    closes = duration_us - max(guard_us, duration_us * BACKLIGHT_GUARD_PERCENT
                               // 100)
    return opens, closes


def exact_index(settle_us, guard_us):
    """The fastest rung a pair can start on: a window of WINDOW_MIN_US and
    no slot under SLOT_MIN_MS, on it and every slower rung."""
    best = 0
    for i, duration in enumerate(SLOT_LADDER):
        opens, closes = exact_window(duration * 1000, settle_us, guard_us)
        if closes - opens < WINDOW_MIN_US or duration < SLOT_MIN_MS:
            break
        best = i
    return best


# Where a pair starts, by the light flag. Dark pairs stay on 16 ms until a
# peer's light is measured: 10 ms holds on the measured sensor, not one 2x
# slower.
EXACT_DARK_START_MS = 16
EXACT_INDEX = min(exact_index(SETTLE_DARK_US, GUARD_DARK_US),
                  SLOT_LADDER.index(EXACT_DARK_START_MS))
EXACT_LIT_INDEX = exact_index(SETTLE_LIT_US, GUARD_LIT_US)


class Transfer:
    """One transfer, Leader or Follower. `reader` is how this badge reads its
    sensor -- a RingReader over the DMA ring -- started by the caller;
    `fast` the fast block's module, which every transfer needs (a listener
    that only decides slots, as Dev Mode's tests run one, can do
    without)."""

    def __init__(self, leader, payload=None, reader=None, fast=None):
        self.leader = leader
        self.payload = payload
        self.fast = fast
        self.fast_data = b""        # Leader: the payload's fast_stream()
        self.fast_chunk = 0         # ... the first chunk not yet ACKed
        self.fast_rung = 0          # ... the rung, an index into RUNGS
        self.fast_top = 0           # ... the pair's start rung, its best
        self.fast_acks = 0          # ... ACKs in a row on this rung
        self.fast_up_after = FAST_UP_ACKS   # ... ACKs before stepping up
        self.fast_trial = False     # ... on a rung just stepped up to
        self.fast_block_len = 0     # ... the block on air's length
        self.fast_bits = None       # Follower: the rung's bits, reading
        self.fast_symbols = None    # the block being sent, encoded
        self.fast_pending = False   # its burst follows the queue
        self.fast_burst = None      # (start us, symbols, periods): shell
        self.fast_start = self.fast_end = 0
        self.fast_trainings = 0     # decoder trainings, for the simulator
        self.fast_retunes = 0       # ... and retunes
        self.fast_bins_fed = 0      # ... and bins fed to it
        self.fast_chunks = {}       # Follower: chunk -> bytes received
        self.fast_expect = 0        # Follower: when the burst is due, us
        self.fast_b0 = None         # ... the time of its first bin
        self.fast_have = 0          # ... bins pulled so far
        self.fast_anchor = None
        self.fast_nak_at = None
        self.fast_fed = False       # ... the decoder fed: the burst is over
        self.fast_periods = 0
        self.decoder = None
        self.fast_bins = None
        if fast is not None and not leader:
            self.decoder = fast.Decoder()
            self.fast_bins = array("H", bytes(2 * FAST_BINS))
        self.reader = reader
        # Fine samples pulled so far, and the coarse one being pooled.
        self.fine_end = FINE_US
        self.one_v = [0]
        self.one_t = [0]
        self.fine_t = self.fine_v = 0
        self.coarse_n = self.coarse_sum = self.coarse_tsum = 0
        self.coarse_base = None
        self.coarse_t = self.coarse_v = 0
        # The last FLICKER_BUFFER raw fine samples before the data phase, a
        # ring for the fit: time and span in us, value, time in ms.
        self.fit_t = [0] * FLICKER_BUFFER
        self.fit_v = [0] * FLICKER_BUFFER
        self.fit_s = [0] * FLICKER_BUFFER
        self.fit_ms = [0] * FLICKER_BUFFER
        self.fit_n = 0
        self.fit_result = None      # what fit() found, once it has run
        self.fit_tried = False
        # Whether the fit ran -- the stall -- or only the steady check: the
        # simulator's cost model.
        self.fit_ran = False
        self.fit_checked = False
        self.fit_checks = 0         # steadiness checks made, retries too
        self.track_points = 0
        self.flicker = FlickerFilter()
        self.watched = 0        # Leader: the next lead-in slot to read
        # The table the field is shown in: the beacons', then the data's.
        self.scheme = SCHEME_BEACON
        # This badge's light, and the pair's: the Follower's, adopted.
        self.lit = False
        self.pair_lit = False
        # The Leader's fit found flicker: it holds its first block back.
        self.pair_flicker = False
        # A staircase entered, to be timed from the next pass: see enter().
        self.cal_pending = False
        self.pending_since = 0
        self.start_grace = 0        # slots a Follower waits at first
        self.result = None
        self.detector = BeaconDetector()
        self.clock = SlotClock()
        self.levels = None
        self.started = 0
        self.phase_started = 0
        self.tx = []            # symbols still to transmit
        self.tx_slot = None     # the slot the first goes out on, once known
        # When the slot whose decision queued them ended, in us: a time, so
        # a demotion between cannot move it.
        self.tx_after_us = 0
        self.deciding = None    # the slot being decided, while it is
        self.deciding_end = 0   # ... and when it ends, in us
        self.rx = []            # symbols decided this phase
        self.reader_cal = StaircaseReader(LEADER_CAL_LIMIT if leader else None,
                                          LEADER_CAL_SMOOTH if leader else 0)
        # The last nine decided slots' values, a ring: begin_levels().
        self.rx_values = [0] * 9
        self.rx_n = 0
        self.fit_due = None     # Follower: when its gap fit starts, us
        self.cal_ended = 0      # Leader: where it found the closing gap, ms
        self.tx_anchor = None   # Leader: hold slots its first block may trim
        self.hold_until = 0     # Follower: no light for a lock before, us
        self.cal_refusals = 0   # staircases the Leader refused
        # Follower: after demoting on its own, measure the Leader's next
        # message's rate; the crossing it locked on.
        self.verify_rate = False
        self.rate_from = None
        self.rate_heals = 0
        # Follower: until its first Begin, whether the Leader announces
        # again (watch_for_announce()), and since when.
        self.watch_announce = False
        self.announce_since = None
        self.announce_swing = None  # Follower: the Announce's, as detected
        self.finishing = False
        self.epoch = 0
        self.now = 0            # ms
        self.pass_ms = 0        # ms: the time work() was called with
        self.now_us = 0
        # Asked of the caller: a collection at the top of its next pass,
        # never inside a call here.
        self.collect_due = False
        self.replayed = 0       # samples replay_staircase() re-fed
        self.idle_grace = 0
        self.first_grace = 0    # Follower: slots more, until a Begin frames
        self.collected = False
        # What each work() did, for the simulator's cost model.
        self.work_slots = 0
        self.work_samples = 0
        # The data phase's window, per rung, and buffers for its parts.
        self.w_open = self.w_close = self.w_parts = 0
        self.part_max = -1
        self.part_raw = [0] * PARTS_MAX
        self.part_time = [0] * PARTS_MAX
        self.part_sorted = [0] * PARTS_MAX
        self.part_span = [0] * PARTS_MAX
        self.part_amount = [0] * PARTS_MAX
        self.parts_now = 0
        self.thr = [0] * 128
        self.thr_t = [0] * 128
        self.clear_data(0)
        self.phase = PHASE_ANNOUNCE if leader else PHASE_LISTEN

    # -- helpers

    def enter(self, phase, now):
        # A staircase starts on the first pass not catching up on the ring:
        # every sample worked through holds the field, which once cost a
        # Follower its whole high step. See CAL_CAUGHT_UP_US.
        self.cal_pending = phase == PHASE_CAL_SEND
        self.pending_since = self.pass_ms
        self.phase = phase
        self.phase_started = now
        self.rx = []
        self.tx = []
        self.detector.reset()
        # Flicker points belong to the phase they were read in.
        self.flicker.close(False)
        self.track_points = 0

    def fail(self, code):
        if code == ERR_LOST_PEER and self.watch_announce and self.cal_refusals:
            # Refused before, then neither a Begin nor the Announce: the
            # Leader gave up on the contrast.
            code = ERR_CONTRAST
        self.result = code
        self.phase = PHASE_DONE

    def elapsed(self, now):
        return time.ticks_diff(now, self.phase_started)

    # The transfer got somewhere: restarts the stall budgets.
    def advance(self):
        self.stall_slots = 0
        self.unframed = 0
        self.dark_run = 0

    # Queue symbols. The first goes out on the slot after the one after the
    # slot whose decision queued them -- or later if this badge was held up:
    # fixed on the next field_at(), so a collection delays the message whole.
    def send(self, symbols):
        self.tx = list(symbols)
        self.tx_slot = None
        self.tx_after_us = self.deciding_end if self.deciding is not None \
            else self.now_us
        self.watched = self.clock.number(self.now_us)
        self.advance()          # having something to say is progress

    # -- the entry points

    def field_at(self, t):
        """The field to show at `t` us: cheap, so the caller can change it
        on time before doing any work."""
        phase = self.phase
        if phase == PHASE_DATA:
            return self.data_field(t)
        now = t // 1000
        if phase == PHASE_ANNOUNCE:
            return beacon_field(self.elapsed(now), ANNOUNCE_FAST_HALF_MS)
        if phase == PHASE_CAL_SEND:
            if self.cal_pending:
                return LOW
            return staircase_field(self.elapsed(now), STAIRCASE,
                                   self.cal_reps(), CAL_OPEN_FAST_MS,
                                   PLATEAU_FAST_MS)
        return LOW

    def due_us(self):
        """When the caller next has anything to do: a slot boundary while
        transmitting, else the next window or fine sample to complete."""
        if self.phase == PHASE_DATA:
            if self.sub == self.STEP_FAST and not self.tx:
                if self.fast_burst is not None:
                    return self.fast_burst[0]
                return self.fast_end + DUE_MARGIN_US
            if self.tx:
                if self.tx_slot is None:
                    return self.now_us
                return self.clock.start_of(
                    self.clock.number(self.now_us) + 1)
            return self.clock.start_of(self.next) + self.w_close \
                + DUE_MARGIN_US
        return self.fine_end + DUE_MARGIN_US

    def work(self, t):
        """Everything due by `t` us: the fine samples before the data phase,
        the slots after it, oldest first."""
        self.now_us = t
        self.now = self.pass_ms = t // 1000
        self.work_slots = self.work_samples = 0
        if self.phase == PHASE_DONE:
            return
        if t > TRANSFER_MAX_US:
            self.fail(ERR_LOST_PEER)
            return
        if self.cal_pending and (
                time.ticks_diff(self.reader.newest_us(), self.fine_end)
                < CAL_CAUGHT_UP_US
                or time.ticks_diff(self.pass_ms, self.pending_since)
                > CAL_PENDING_MAX_MS):
            # Caught up, or never will be: CAL_PENDING_MAX_MS late at most.
            self.cal_pending = False
            self.phase_started = self.pass_ms
        if self.phase != PHASE_DATA:
            reader = self.reader
            newest = reader.newest_us()
            one = self.one_v
            when = self.one_t
            while (self.fine_end <= newest and self.phase != PHASE_DATA
                   and self.phase != PHASE_DONE):
                start = self.fine_end - FINE_US
                reader.parts(start, self.fine_end, 1, one, when)
                self.fine_end += FINE_US
                self.work_samples += 1
                self.take_fine(start + FINE_US // 2, one[0], when[0])
            self.now_us = t
            self.now = t // 1000
        if self.phase == PHASE_DATA:
            self.work_data(t)

    def take_fine(self, t_us, value, read_us=None):
        """A fine sample: the mean of FINE_MS of readings centred on `t_us`,
        or -1 for none (an overrun), skipped; `read_us` the readings' mean
        time, for the flicker."""
        if value < 0:
            return
        t = t_us // 1000
        self.now = t
        self.now_us = t_us
        self.fine_t = t
        self.fine_v = value
        self.filter_sample(t_us if read_us is None else read_us, FINE_US)
        # Corrected, once the flicker is fitted.
        if self.pool_coarse(t, self.fine_v):
            self.decide(self.coarse_t, self.coarse_v)

    def decide(self, t, value):
        if self.phase == PHASE_ANNOUNCE:
            self.step_announce(t, value)
        elif self.phase == PHASE_LISTEN:
            self.step_listen(t, value)
        elif self.phase == PHASE_CAL_SEND:
            self.step_cal_send(t, value)
        elif self.phase == PHASE_CAL_GAP:
            self.step_cal_gap(t, value)

    def pool_coarse(self, t, value):
        """Adds a fine sample to the coarse stream; True if that finished a
        sample, now in coarse_t/coarse_v."""
        if self.coarse_base is None:
            self.coarse_base = t
        dt = time.ticks_diff(t, self.coarse_base)
        self.coarse_n += 1
        self.coarse_sum += value
        self.coarse_tsum += dt
        if dt >= FINE_MS:
            n = self.coarse_n
            self.coarse_t = time.ticks_add(self.coarse_base,
                                           self.coarse_tsum // n)
            self.coarse_v = self.coarse_sum // n
            self.coarse_base = t
            self.coarse_n = self.coarse_sum = self.coarse_tsum = 0
            return True
        return False

    def filter_sample(self, t_us, span_us):
        """The fine sample just taken, a mean over `span_us` at `t_us`: kept
        for the fit and, once fitted, corrected -- and tracked while the
        peer is known dark."""
        raw = self.fine_v
        flicker = self.flicker
        i = self.fit_n % FLICKER_BUFFER
        self.fit_t[i] = t_us
        self.fit_v[i] = raw
        self.fit_s[i] = span_us
        self.fit_ms[i] = self.fine_t
        self.fit_n += 1
        if flicker.harmonics is not None:
            if self.peer_dark():
                flicker.track(t_us, raw, span_us)
                self.track_points += 1
                if self.track_points >= FLICKER_TRACK_POINTS:
                    flicker.update()
                    self.track_points = 0
            self.fine_v = flicker.correct(t_us, raw, span_us)

    def peer_dark(self):
        """The peer is known dark before the data phase only for the Leader
        in the shared gap; the Follower tracks in the data phase."""
        return self.leader and self.phase == PHASE_CAL_GAP

    def fit_flicker(self, window_ms, end_us=None):
        """Fits the flicker, once, on the `window_ms` of kept samples ending
        at `end_us` (the newest if None); a refused window is tried again
        at half the length."""
        self.fit_tried = True
        self.fit_result = self.fit_window(window_ms, end_us)
        if self.fit_result is None:
            self.fit_result = self.fit_window(window_ms // 2, end_us)
        self.track_points = 0

    def replay_staircase(self, since):
        """A fresh staircase reader fed the kept samples since `since` ms,
        corrected, through a fresh coarse stream."""
        self.reader_cal = StaircaseReader()
        self.coarse_n = self.coarse_sum = self.coarse_tsum = 0
        self.coarse_base = None
        count = min(self.fit_n, FLICKER_BUFFER)
        first = self.fit_n - count
        for n in range(first, self.fit_n):
            i = n % FLICKER_BUFFER
            if time.ticks_diff(self.fit_ms[i], since) < 0:
                continue
            self.replayed += 1          # its cost, for the simulator
            value = self.flicker.correct(self.fit_t[i], self.fit_v[i],
                                         self.fit_s[i])
            if self.pool_coarse(self.fit_ms[i], value):
                self.reader_cal.feed(self.coarse_t, self.coarse_v)

    def fit_window(self, window_ms, end_us=None):
        count = min(self.fit_n, FLICKER_BUFFER)
        if not count:
            return None
        last = self.fit_t[(self.fit_n - 1) % FLICKER_BUFFER] \
            if end_us is None else end_us
        ts = []
        vs = []
        spans = []
        for n in range(self.fit_n - count, self.fit_n):
            i = n % FLICKER_BUFFER
            age = last - self.fit_t[i]
            if 0 <= age <= window_ms * 1000:
                ts.append(self.fit_t[i])
                vs.append(self.fit_v[i])
                spans.append(self.fit_s[i])
        return self.check_or_fit(ts, vs, spans)

    def check_or_fit(self, ts, vs, spans):
        """A window with no flicker the fit could keep is steady without the
        fit's stall (130-153 ms on v2)."""
        self.fit_checked = True
        self.fit_checks += 1
        if self.flicker.steady_window(ts, vs):
            self.flicker.steady = True
            return "steady"
        self.fit_ran = True
        return self.flicker.fit(ts, vs, spans)

    # -- phase: announce (Leader beacons, reads for the Follower's staircase)

    def step_announce(self, now, light):
        if self.reader_cal.feed(now, light):
            # A closing gap: stop announcing and settle in it, whether or
            # not it ended a staircase -- see settle_staircase().
            self.cal_ended = self.reader_cal.ended_at
            self.levels = None
            self.scheme = SCHEME_BACKLIGHT
            self.epoch = time.ticks_add(self.cal_ended, CAL_GAP_FAST_MS)
            self.enter(PHASE_CAL_GAP, now)
            return LOW
        if time.ticks_diff(now, self.started) > ANNOUNCE_TIMEOUT_MS:
            self.fail(ERR_NO_PEER)
            return LOW
        return beacon_field(self.elapsed(now), ANNOUNCE_FAST_HALF_MS)

    def settle_staircase(self, now):
        """Leader, LEADER_SETTLE_MS into the closing gap: the flicker fitted
        on the gap, then the staircase read again, corrected, from the kept
        samples. Its levels are this badge's thresholds, its repetitions the
        pair's light, the fit the hold. No levels: it announces again."""
        ended = self.cal_ended
        reader = self.reader_cal
        levels = reader.levels()
        self.fit_flicker(LEADER_SETTLE_MS - LEADER_FIT_SKIP_MS,
                         time.ticks_add(ended, LEADER_SETTLE_MS) * 1000)
        if self.flicker.harmonics is not None:
            self.replay_staircase(time.ticks_add(
                ended, -(staircase_ms(CAL_REPS_FAST, CAL_OPEN_FAST_MS,
                                      PLATEAU_FAST_MS, 0)
                         + PLATEAU_FAST_MS)))
            reader = self.reader_cal
            if reader.ended_at is None:
                reader.ended_at = ended
            corrected = reader.levels()
            if corrected is not None:
                levels = corrected
        if levels is None:
            self.cal_refusals += 1
            if self.cal_refusals > CAL_RESTARTS:
                self.fail(ERR_CONTRAST)
                return
            self.flicker = FlickerFilter()
            self.fit_tried = False
            self.reader_cal = StaircaseReader(LEADER_CAL_LIMIT,
                                              LEADER_CAL_SMOOTH)
            self.scheme = SCHEME_BEACON
            self.enter(PHASE_ANNOUNCE, now)
            return
        # The pair's light as the Follower decided it.
        self.pair_lit = staircase_highs(reader.values, levels) \
            <= LIT_STEPS_MAX_FAST
        self.levels = levels
        self.lit = self.is_lit(levels.black)
        self.pair_flicker = self.flicker.harmonics is not None
        self.collect_due = True

    # -- phase: listen (Follower holds black until it sees the Announce)

    def step_listen(self, now, light):
        self.detector.push(now, light)
        if time.ticks_diff(now, self.started) > ANNOUNCE_TIMEOUT_MS:
            self.fail(ERR_NO_PEER)
            return LOW
        if not self.detector.matches(ANNOUNCE_FAST_HALF_MS):
            return LOW
        # The Announce's dark halves are this badge's light, which its
        # staircase carries; its full is the Leader's high; the middle a
        # guess until the first Begin trains it.
        black = self.detector.low()
        white = self.detector.high()
        self.announce_swing = white - black
        self.lit = self.is_lit(black)
        self.pair_lit = self.lit
        levels = Levels()
        levels.fit(black, black + (white - black)
                   * PROVISIONAL_GREY_PERCENT // 100, white)
        levels.last = LOW
        self.levels = levels
        self.scheme = SCHEME_BACKLIGHT
        self.enter(PHASE_CAL_SEND, now)
        return LOW

    # -- phase: the Follower's staircase

    def cal_reps(self):
        """The light flag: the pair's, which the Follower decides."""
        return CAL_REPS_FAST_LIT if self.pair_lit else CAL_REPS_FAST

    def hold_slots(self):
        """Slots a Leader holds its first block back in flicker."""
        d = self.clock.duration
        return ((CAL_GAP_FAST_FLICKER_MS - CAL_GAP_FAST_MS) * 1000
                + d - 1) // d

    def step_cal_send(self, now, light):     # noqa: ARG002 - every step_* takes (now, light)
        if self.cal_pending:
            return LOW              # not begun: see enter()
        reps = self.cal_reps()
        length = staircase_ms(reps, CAL_OPEN_FAST_MS, PLATEAU_FAST_MS,
                              CAL_GAP_FAST_MS)
        body_end = CAL_OPEN_FAST_MS + reps * 3 * PLATEAU_FAST_MS
        elapsed = self.elapsed(now)
        if elapsed >= body_end and not self.collected:
            self.collected = True
            self.collect_due = True
        if elapsed >= length:
            # The Follower sent it, so it knows when its closing gap began:
            # the shared reference.
            self.epoch = time.ticks_add(self.phase_started, length)
            gap = time.ticks_add(self.epoch, -CAL_GAP_FAST_MS)
            self.fit_due = (gap + FOLLOWER_FIT_START_MS) * 1000
            self.enter(PHASE_CAL_GAP, now)
            return LOW
        return staircase_field(elapsed, STAIRCASE, reps,
                               CAL_OPEN_FAST_MS, PLATEAU_FAST_MS)

    # -- phase: wait out the shared gap, then start the slot clock

    def step_cal_gap(self, now, light):     # noqa: ARG002 - every step_* takes (now, light)
        if self.leader and self.levels is None:
            if time.ticks_diff(now, self.cal_ended) < LEADER_SETTLE_MS:
                return LOW
            self.settle_staircase(now)
            if self.phase != PHASE_CAL_GAP:
                return LOW
        if time.ticks_diff(self.epoch, now) > 0:
            return LOW
        self.start_data(self.epoch, self.start_index())
        return LOW

    def is_lit(self, black):
        return black is not None and black >= LIT_BLACK_COUNTS

    def start_index(self):
        """The rung both start on, from the pair's light flag."""
        return EXACT_LIT_INDEX if self.pair_lit else EXACT_INDEX

    # -- phase: data
    # Half duplex: one badge transmits and the other holds black. No rate
    # test: the first block is the test, and a Follower that cannot frame it
    # asks to step down. A demotion changes the slot, never the epoch, so
    # the grids stay aligned.

    STEP_BLOCK = 0      # Leader sends a block's lead-in / Follower waits for one
    STEP_FINISH = 3     # Complete sent, and being acknowledged
    STEP_FAST = 4       # Leader: its burst on air
    STEP_FAST_REPLY = 5     # Leader: waiting for the ACK or NAK
    STEP_FAST_RX = 6    # Follower: decoding a burst

    def start_data(self, now, index=None):
        """The data phase, from `now` ms -- the epoch -- on rung `index`."""
        self.enter(PHASE_DATA, now)
        self.clock.start(now * 1000, self.clock.index if index is None
                         else index)
        self.set_window()
        self.clear_data(now)
        self.next = 0
        self.lit_end = self.clock.epoch
        hold = self.hold_slots()
        if self.leader:
            # In flicker the first block holds back, black, as if the gap
            # were CAL_GAP_FAST_FLICKER_MS; the epoch stays.
            held = hold if self.pair_flicker else 0
            self.fast_data = fast_stream(list(self.payload or ()))
            self.fast_chunk = 0
            self.fast_rung = FAST_START_LIT if self.pair_lit \
                else FAST_START_DARK
            self.fast_top = self.fast_rung
            self.send_block(held)
            # A Leader late to the data phase -- its fit's stall -- trims
            # what is left of the hold.
            self.tx_anchor = held
        else:
            if not self.reader.check():
                self.fail(ERR_RING)
                return
            # Whatever the flag: a Follower always waits out a Leader that
            # holds back, and its collection.
            grace = hold + (FAST_START_GRACE_MS * 1000
                            + self.clock.duration - 1) // self.clock.duration
            self.idle_grace = self.start_grace = grace
            self.stall_slots = -grace
            self.watch_announce = self.announce_swing is not None

    def clear_data(self, now):
        self.retries = self.blocks_accepted = 0
        self.idle_slots = self.complete_tries = self.wait_slots = 0
        self.finish_slots = 0
        self.stall_slots = self.attempts = self.resend_requests = 0
        self.unframed = 0
        self.dark_run = 0
        self.sub = self.STEP_BLOCK
        self.received = None        # Follower: the payload's fields, once whole
        self.pending_slow_down = self.want_resend = False
        self.block_started = now
        # The next slot to decide, and the end of the last light, from which
        # silence is counted.
        self.next = 0
        self.lit_end = 0
        self.reset_block()

    def send_block(self, extra_lead_in=0):
        """Leader: the lead-in -- black, so a rate change has landed on both
        -- FAST_BEGIN, the rung and FAST_GAP_SLOTS of low at the slot rung;
        the burst from the next boundary (fast_burst, for the shell); then
        the Follower's ACK or NAK. Ends the transfer once the block's
        attempts are spent, or the ring has failed."""
        if self.attempts >= MAX_BLOCK_ATTEMPTS:
            self.fail(ERR_CRC)
            return
        if not self.reader.check():
            self.fail(ERR_RING)
            return
        self.attempts += 1
        self.sub = self.STEP_BLOCK
        fast = self.fast
        periods = fast.RUNGS[self.fast_rung]
        data = fast_block(fast, self.fast_data, self.fast_chunk, periods)
        self.fast_block_len = len(data)
        self.fast_symbols = fast.encode(self.fast_chunk, data)
        self.fast_periods = periods
        self.send([LOW] * (RESYNC_LEAD_IN + extra_lead_in) + list(FAST_BEGIN)
                  + bits_symbols(self.fast_rung, FAST_RUNG_BITS)
                  + [LOW] * FAST_GAP_SLOTS)
        self.fast_pending = True

    def burst_us(self):
        """The burst's length on air, in us."""
        return len(self.fast_symbols) * self.fast_periods \
            * self.fast.COMMON_NS // 1000

    def burst_started(self, t_us):
        """The shell started the burst at `t_us`: on air until it ends,
        then listening for the reply."""
        self.fast_burst = None
        self.fast_start = t_us
        self.fast_end = t_us + self.burst_us()

    # -- the window

    def set_window(self):
        """Where a slot is decided on this rung, by this badge's light, and
        its flicker table built now rather than on the first slot."""
        d = self.clock.duration
        settle = SETTLE_LIT_US if self.lit else SETTLE_DARK_US
        guard = GUARD_LIT_US if self.lit else GUARD_DARK_US
        self.w_open, self.w_close = exact_window(d, settle, guard)
        # A rung too fast for this badge's settle keeps a quarter of a slot
        # to read: a badge that cannot decide cannot ask to slow down.
        if self.w_close - self.w_open < WINDOW_MIN_US // 2:
            self.w_open = self.w_close - d // 4
        window = self.w_close - self.w_open
        n = (window + PART_MAX_US - 1) // PART_MAX_US
        self.w_parts = max(PARTS_MIN, min(PARTS_MAX, n))
        if self.flicker.harmonics is not None:
            self.flicker.table(_span_step(window // self.w_parts))

    def window_value(self, k):
        """Slot `k`'s value: the median of its window's parts, each flicker
        corrected; failing any, the whole slot's mean; -1 if no readings.
        The raw parts stay in part_raw, for learn()."""
        start = self.clock.start_of(k)
        opens = start + self.w_open
        n = self.w_parts
        span = (self.w_close - self.w_open) // n
        raw = self.part_raw
        times = self.part_time
        spans = self.part_span
        self.reader.parts(opens, opens + span * n, n, raw, times)
        for j in range(n):
            spans[j] = span
        self.parts_now = n
        # Equal spans, ascending centres: one table and shift a window.
        amount = self.part_amount
        self.flicker.amounts(times, n, span, amount)
        ordered = self.part_sorted
        count = 0
        for j in range(n):
            v = raw[j]
            if v < 0:
                continue
            v -= amount[j]
            if v < 0:
                v = 0
            _insert_sorted(ordered, count, v)
            count += 1
        # The brightest part: a fast burst's short symbols can give a dark
        # median with lit parts in it.
        self.part_max = ordered[count - 1] if count else -1
        if count:
            half = count // 2
            if count & 1:
                return ordered[half]
            # The mean of the middle two: the upper alone put a dark slot
            # after a lit one inside the hysteresis band.
            return (ordered[half - 1] + ordered[half]) // 2
        whole = self.reader.mean(start, start + self.clock.duration)
        if whole < 0:
            return -1
        whole = self.flicker.correct(start + self.clock.duration // 2, whole,
                                     self.clock.duration)
        self.part_max = whole
        return whole

    def learn(self, _k, dark):
        """The flicker learns from a slot decided dark: each part
        window_value() just read for slot `_k`."""
        flicker = self.flicker
        if dark:
            n = self.parts_now
            spans = self.part_span
            raw = self.part_raw
            times = self.part_time
            for j in range(n):
                if raw[j] >= 0:
                    flicker.collect(times[j], raw[j], spans[j])
        flicker.close(dark)

    # -- phase locking
    # The badge that detects the shared instant reads it late, so a receiver
    # re-bases its grid on the peer's first edge out of black at the start
    # of every message: rebase() moves the epoch, never the duration;
    # slow_down() the reverse.

    # Only between messages: re-basing mid-pattern would re-frame its rest.
    def can_phase_lock(self):
        if self.sub == self.STEP_FINISH:
            return True
        if self.leader:
            return self.sub == self.STEP_FAST_REPLY
        return self.sub == self.STEP_BLOCK and not self.in_block

    # Black before an edge for it to start a transmission: no gap inside a
    # pattern is this long. The Complete Ack follows with one slot, so is
    # read on the grid as it stands (spec: "The Leader's grid is the
    # reference").
    def silence_us(self):
        return (self.clock.duration * 3) // 2

    def try_lock(self, k):
        """Slot `k` was decided lit while acquiring. If the readings rose out
        of silence_us() of dark inside it, re-base on that edge and return
        True: `k` is then decided again on the new grid."""
        clock = self.clock
        d = clock.duration
        close = clock.start_of(k) + self.w_close
        silence = self.silence_us()
        if close - self.lit_end < silence:
            return False
        reader = self.reader
        start = max(self.lit_end, close - 5 * d // 2, reader.oldest_us())
        # A threshold per step of whole edge bins, flicker included.
        step = max(3, (d // 32 + 99) // 100) * 100
        n = (close - start) // step + 1
        if n > len(self.thr):
            n = len(self.thr)
        t_low = self.levels.t_low
        flicker = self.flicker
        crossing = reader.rise(start, close, self.thresholds(start, n, step),
                               n, step)
        # A real edge stays lit (LOCK_HOLD_US); past one that does not, the
        # search goes on from the next step, the thresholds moved down.
        while crossing >= 0:
            hold = min(LOCK_HOLD_US, close - crossing)
            if hold < LOCK_HOLD_US // 2:
                break
            level = reader.mean(crossing + LOCK_SKIP_US, crossing + hold) \
                - flicker.amount(crossing + hold // 2, hold)
            if level >= t_low:
                break
            skip = (crossing - start) // step + 1
            if skip < 1:
                skip = 1        # rise() interpolates into the bin before
            if skip >= n:
                crossing = -1
                break
            thr = self.thr
            for i in range(n - skip):
                thr[i] = thr[i + skip]
            start += skip * step
            n -= skip
            crossing = reader.rise(start, close, thr, n, step)
        # Silence to the start of the crossing's slot, not the crossing: a
        # slow sensor crosses well into a reply's first slot, and a lock on
        # it was late by the sensor's delay.
        if crossing < 0 or clock.start_of(clock.number(crossing)) \
                - self.lit_end < silence:
            return False
        lag = EDGE_LAG_EXACT_LIT_US if self.lit else EDGE_LAG_EXACT_DARK_US
        self.rebase(crossing - lag, crossing)
        return True

    def rebase(self, boundary, crossing):
        """Re-frames the grid on `boundary`. The slot in progress is not
        decided on the old grid; the crossing is consumed, so one re-base a
        message."""
        self.clock.epoch = boundary
        self.next = self.clock.number(boundary)
        self.lit_end = crossing
        if self.verify_rate and not self.leader:
            self.verify_rate = False
            self.rate_from = crossing

    def decided_end(self):
        """The end of the slot being decided, or now if none is."""
        if self.deciding is None:
            return self.now_us
        return self.deciding_end

    def demote(self, t_ref):
        """One rung slower from `t_ref` us: the slot straddling it is not
        decided."""
        self.clock.slow_down()
        self.set_window()
        self.next = self.clock.number(t_ref) + 1

    # -- the data phase's pass

    def data_field(self, t):
        """The field for slot number(t): this badge's queue, black
        otherwise. Ends the queue once its last slot is over."""
        tx = self.tx
        if not tx:
            return LOW
        clock = self.clock
        n = clock.number(t)
        if self.tx_slot is None:
            # After the slot after the one decided.
            self.tx_slot = max(clock.number(self.tx_after_us) + 1, n + 1)
            if self.tx_anchor is not None:
                trim = min(self.tx_slot - 1, self.tx_anchor)
                if trim > 0:
                    del tx[:trim]
                self.tx_anchor = None
            if self.fast_pending:
                # The burst plays from the boundary after the queue.
                self.fast_burst = (clock.start_of(self.tx_slot + len(tx)),
                                   self.fast_symbols, self.fast_periods)
        index = n - self.tx_slot
        if index < 0:
            return LOW              # holding black until the boundary
        if index >= len(tx):
            end = self.tx_slot + len(tx)
            self.tx = []
            if self.fast_pending:
                self.fast_pending = False
                self.sub = self.STEP_FAST
            # Silence counts from the end of this badge's own transmission.
            self.lit_end = clock.start_of(end)
            if self.next < end:
                self.next = end
            return LOW
        return tx[index]

    def work_data(self, t):
        # First and unconditionally, mid-transmission too: a backstop for
        # the shape nobody predicted.
        if time.ticks_diff(self.now, self.block_started) > BLOCK_TIMEOUT_MS:
            self.fail(ERR_LOST_PEER)
            return
        self.data_field(t)          # ends a queue whose last slot is over
        clock = self.clock
        if self.tx:
            # Transmitting: no slot is decided, and nothing counts.
            if self.rx:
                self.rx = []
            self.idle_slots = 0
            if self.leader and self.sub == self.STEP_BLOCK:
                self.watch_lead_in()
            following = clock.number(t) + 1
            if self.next < following:
                self.next = following
            return

        # A rate change after the pattern announcing it has gone out, at the
        # rate the peer is listening at.
        if self.pending_slow_down:
            self.pending_slow_down = False
            self.demote(t)

        # Complete has gone out: wait to be told it arrived.
        if self.finishing:
            self.finishing = False
            self.sub = self.STEP_FINISH
            self.wait_slots = 0
            self.finish_slots = 0

        newest = self.reader.newest_us()
        if self.sub == self.STEP_FAST:
            if self.fast_burst is not None or t < self.fast_end:
                following = clock.number(t) + 1
                if self.next < following:
                    self.next = following
                return
            self.fast_listen()
        if self.sub == self.STEP_FAST_RX:
            self.fast_receive(newest)
            return
        if self.rate_from is not None and newest >= self.rate_from \
                + RATE_CHECK_SLOTS * clock.duration:
            self.check_leader_rate(newest)
        if self.fit_due is not None and not self.leader:
            self.follower_fit(newest)
            if self.fit_due is not None:
                # No slot is decided before the fit: the Leader is dark.
                following = clock.number(newest)
                if self.next < following:
                    self.next = following
                return
        while (self.phase == PHASE_DATA and not self.tx
               and clock.start_of(self.next) + self.w_close <= newest):
            k = self.next
            self.next = k + 1
            self.work_slots += 1
            self.deciding = k
            self.deciding_end = clock.start_of(k + 1)
            self.decide_slot(k)
            self.deciding = None
            if self.watch_announce and self.phase == PHASE_DATA:
                self.watch_for_announce(k)

    def follower_fit(self, newest):
        """The Follower's flicker, from the ring, FOLLOWER_FIT_START_MS into
        the gap: a steady check over FOLLOWER_CHECK_MS -- its median the
        Leader's data low, the provisional black -- and a fit over
        FLICKER_FAST_FIT_MS if not steady, refused, its later half. In
        flicker no light before the hold ends is taken for the Leader."""
        start = self.fit_due
        if not self.fit_checked:
            end = start + FOLLOWER_CHECK_MS * 1000
            if newest < end:
                return
            ts, vs, spans = self.ring_samples(start, end)
            if vs:
                ordered = sorted(vs)
                black = ordered[len(ordered) // 2]
                white = self.levels.white
                if white - black >= MIN_SWING:
                    self.levels.fit(black, black + (white - black)
                                    * PROVISIONAL_GREY_PERCENT // 100, white)
            self.fit_checked = True
            self.fit_checks += 1
            if self.flicker.steady_window(ts, vs):
                self.flicker.steady = True
                self.fit_tried = True
                self.fit_result = "steady"
                self.fit_due = None
            return
        end = start + FLICKER_FAST_FIT_MS * 1000
        if newest < end:
            return
        ts, vs, spans = self.ring_samples(start, end)
        self.fit_tried = True
        self.fit_ran = True
        self.fit_result = self.flicker.fit(ts, vs, spans)
        if self.fit_result is None:
            half = len(ts) // 2
            self.fit_result = self.flicker.fit(ts[half:], vs[half:],
                                               spans[half:])
        self.fit_due = None
        self.set_window()
        if self.flicker.harmonics is not None:
            self.hold_until = self.clock.start_of(
                RESYNC_LEAD_IN + self.hold_slots() - HOLD_MARGIN_SLOTS)
            if self.fast is not None:
                # A Leader whose fit found the room steady sends at once,
                # into the hold, and sends again only after its wait for a
                # reply: wait that out too before calling it lost -- until
                # a Begin frames, not the first light, which may be the
                # end of the burst missed.
                d = self.clock.duration
                rung = FAST_START_LIT if self.pair_lit else FAST_START_DARK
                wait = FAST_REPLY_MS * 1000 + burst_max_us(
                    self.fast, self.fast.RUNGS[rung])
                extra = (wait + d - 1) // d + RESYNC_LEAD_IN \
                    + len(FAST_BEGIN) + FAST_RUNG_BITS + FAST_GAP_SLOTS
                self.first_grace = extra
                self.stall_slots -= extra

    def check_leader_rate(self, newest):
        """Follower: the Leader's slot, from the message locked on at
        `rate_from` -- two rises after it, three slots apart. On another
        rung, move to it and wait for its next message."""
        c0 = self.rate_from
        self.rate_from = None
        d = self.clock.duration
        end = c0 + RATE_CHECK_SLOTS * d
        if c0 < self.reader.oldest_us():
            return
        # In 1 ms parts, flicker corrected: the first fall, then two rises.
        t_low = self.levels.t_low
        raw = self.part_raw
        times = self.part_time
        amount = self.part_amount
        fall = r1 = r2 = -1
        lit = True
        t = c0
        while t < end and r2 < 0:
            n = (end - t) // 1000
            if n > PARTS_MAX:
                n = PARTS_MAX
            if n < 1:
                break
            self.reader.parts(t, t + n * 1000, n, raw, times)
            self.flicker.amounts(times, n, 1000, amount)
            for j in range(n):
                if raw[j] < 0:
                    continue
                now_lit = raw[j] - amount[j] >= t_low
                start = t + j * 1000
                if lit and not now_lit:
                    if fall < 0:
                        fall = start
                elif now_lit and not lit and fall >= 0:
                    if r1 < 0:
                        r1 = start
                    elif r2 < 0:
                        r2 = start
                lit = now_lit
            t += n * 1000
        if r2 < 0:
            return
        period = r2 - r1
        if abs((r1 - c0) - period) * 100 > period * 15:
            return                  # not one rate: a pattern ended, or noise
        share = (fall - c0) * 100 // (r1 - c0)
        if 40 < share < 60:
            return                  # lit half of each: Complete
        slot = period // 3
        index = -1
        for i, rung in enumerate(SLOT_LADDER):
            if abs(rung * 1000 - slot) * 100 <= rung * 1000 * RATE_MATCH_PERCENT:
                index = i
                break
        if index < 0 or index == self.clock.index:
            return
        self.clock.set_index(index)
        self.set_window()
        self.rate_heals += 1
        if self.sub == self.STEP_BLOCK:
            self.reset_block()
        self.rx = []
        self.next = self.clock.number(newest) + 1
        self.lit_end = newest

    # -- fast blocks

    def fast_listen(self):
        """Leader: its burst is out. Listen for the ACK or NAK, allowing the
        Follower FAST_REPLY_MS past the longest burst."""
        self.sub = self.STEP_FAST_REPLY
        self.rx = []
        self.next = self.clock.number(self.fast_end) + 1
        self.lit_end = self.fast_end
        self.idle_slots = 0
        d = self.clock.duration
        wait = FAST_REPLY_MS * 1000 + burst_max_us(self.fast,
                                                   self.fast_periods) \
            - (self.fast_end - self.fast_start)
        grace = (wait + d - 1) // d
        self.idle_grace = grace
        self.stall_slots = -grace

    def fast_reply_rx(self):
        """Leader: the Follower's ACK (CRC Good) or NAK (CRC Bad), each heard
        less its first symbol too -- a slow sensor reads the first high out
        of the dark as a middle -- or its Data Sync Error."""
        tail = "".join(self.rx[-len(CRC_GOOD_TAIL):])
        late = tail[1:]
        if tail == CRC_GOOD_TAIL or late == CRC_GOOD_TAIL[1:]:
            self.advance()
            self.rx = []
            size = self.fast.CHUNK
            self.fast_chunk += (self.fast_block_len + size - 1) // size
            self.retries = 0
            self.attempts = 0
            self.block_started = self.now
            self.fast_acked()
            if self.fast_chunk * size >= len(self.fast_data):
                self.send_complete()
            else:
                self.collect_due = True
                self.send_block()
            return
        if tail == CRC_BAD_TAIL or late == CRC_BAD_TAIL[1:]:
            self.advance()
            self.rx = []
            self.retries += 1
            self.fast_acks = 0
            if self.retries >= FAST_TRIES:
                self.retries = 0
                self.fast_rung += 1
                if self.fast_trial:
                    # The rung stepped up to did not hold: wait longer.
                    self.fast_trial = False
                    self.fast_up_after *= 2
                if self.fast_rung >= len(self.fast.RUNGS):
                    # The slowest rung again, until the block's attempts
                    # are spent (send_block ends it ERR_CRC).
                    self.fast_rung = len(self.fast.RUNGS) - 1
            self.collect_due = True
            self.send_block()
            return
        if "".join(self.rx[-len(SYNC_ERROR_TAIL):]) == SYNC_ERROR_TAIL:
            self.advance()
            self.rx = []
            self.demote(self.decided_end())
            self.retries = 0
            self.send_block(SYNC_LEAD_IN)

    def fast_acked(self):
        """Leader: an ACK. fast_up_after in a row below the pair's start rung
        step back up one, on trial until it holds for FAST_UP_ACKS."""
        self.fast_acks += 1
        if self.fast_acks >= FAST_UP_ACKS:
            self.fast_trial = False     # the rung has held
        if self.fast_rung > self.fast_top \
                and self.fast_acks >= self.fast_up_after:
            self.fast_rung -= 1
            self.fast_acks = 0
            self.fast_trial = True

    def start_fast_rx(self, periods):
        """Follower: FAST_BEGIN and the rung decided, the last on slot
        `deciding`. The burst is due FAST_GAP_SLOTS later; no slot is
        decided until it is read."""
        self.sub = self.STEP_FAST_RX
        self.fast_periods = periods
        self.fast_expect = self.clock.start_of(self.deciding + 1
                                               + FAST_GAP_SLOTS)
        self.fast_anchor = None
        self.fast_b0 = None
        self.fast_have = 0
        self.fast_nak_at = None
        self.fast_fed = False
        self.fast_last_lit = self.fast_expect
        self.fast_lit_seen = self.fast_expect
        self.rx = []

    def fast_receive(self, newest):
        """Follower: find the burst's first rise, start the decoder there,
        pull bins as the ring fills, feed them once the burst is over; then
        ACK or NAK."""
        fast = self.fast
        if self.fast_nak_at is not None:
            # Failed early: the Leader may still be on air. A dark longer
            # than MAX_LOWS symbols is the burst's end; failing that, its
            # longest.
            end = self.burst_over(newest)
            if end is not None or newest >= self.fast_nak_at:
                self.fast_reply(False, end if end is not None
                                else self.fast_nak_at)
            return
        if self.fast_anchor is None:
            if newest < self.fast_expect + FAST_SEARCH_US:
                return
            crossing = self.burst_rise()
            if crossing < 0:
                self.fast_nak_after(self.fast_expect)
                return
            lag = EDGE_LAG_FAST_LIT_US if self.lit else EDGE_LAG_FAST_DARK_US
            anchor = crossing - lag
            self.fast_anchor = anchor
            self.fast_b0 = anchor - 10 * FAST_BIN_US
            width = fast.sym_q16(self.fast_periods)
            start = (anchor - self.fast_b0) * 65536 // FAST_BIN_US
            q = width // 4
            mhz = self.fast_flicker_mhz()
            self.decoder.begin(start, width, flicker=mhz,
                               max_len=fast.block_bytes(self.fast_periods),
                               shifts=(-q, 0, q))
        avail = (newest - self.fast_b0) // FAST_BIN_US
        if avail > FAST_BINS:
            avail = FAST_BINS
        if avail > self.fast_have:
            self.reader.bins(self.fast_b0 + self.fast_have * FAST_BIN_US,
                             avail - self.fast_have, self.fast_bins,
                             self.fast_have)
            self.fast_bins_fed += avail - self.fast_have
            self.fast_have = avail
        if not self.fast_fed:
            # The first feed trains, longer than the ring holds on v2: nothing
            # is fed until the whole burst is binned -- its end dark in the
            # ring, or the rung's longest.
            most = (self.fast_anchor - self.fast_b0 + burst_max_us(
                fast, self.fast_periods)) // FAST_BIN_US
            if avail < most and avail < FAST_BINS \
                    and self.burst_over(newest) is None:
                return
            self.fast_fed = True
            status = self.decoder.feed(self.fast_bins, self.fast_have)
            # A rung's first block trains (three starts); later ones retune.
            if self.decoder.mode == "retune":
                self.fast_retunes += 1
            elif self.decoder.mode == "train":
                self.fast_trainings += 3
        else:
            status = self.decoder.feed(self.fast_bins, self.fast_have)
        if status == fast.NEED:
            if avail < FAST_BINS and newest - self.fast_anchor \
                    < fast.MAX_AIR_NS // 1000 + FAST_SEARCH_US:
                return
            self.fast_reply(False, newest)
            return
        if status == fast.OK:
            chunk = self.decoder.chunk
            payload = self.decoder.payload()
            self.fast_accept(chunk, payload)
            # Reply a slot after the burst ends, its length now known: the
            # Leader listens from the slot after.
            symbols = len(fast.encode(chunk, payload))
            self.fast_reply(True, self.fast_anchor + symbols
                            * self.fast_periods * fast.COMMON_NS // 1000)
            return
        self.fast_nak_after(self.fast_anchor)

    def burst_over(self, newest):
        """Follower, after a failed burst: when it ended -- the last lit 1 ms
        part, flicker corrected, before a dark longer than any it holds --
        or None while it may be on air."""
        fast = self.fast
        # Lit: half-way from black to T_low; a slow sensor's single highs
        # do not all reach T_low.
        black = self.levels.black
        line = black + (self.levels.t_low - black) // 2
        raw = self.part_raw
        times = self.part_time
        amount = self.part_amount
        t = self.fast_lit_seen
        while True:
            n = (newest - t) // 1000
            if n > PARTS_MAX:
                n = PARTS_MAX
            if n < 1:
                break
            self.reader.parts(t, t + n * 1000, n, raw, times)
            self.flicker.amounts(times, n, 1000, amount)
            for j in range(n):
                if raw[j] >= 0 and raw[j] - amount[j] >= line:
                    self.fast_last_lit = t + j * 1000 + 1000
            t += n * 1000
        self.fast_lit_seen = t
        quiet = 2 * (fast.MAX_LOWS + 2) * self.fast_periods \
            * fast.COMMON_NS // 1000
        if t - self.fast_last_lit < quiet:
            return None
        return self.fast_last_lit

    def fast_nak_after(self, start):
        """Follower: NAK once the longest burst from `start` could have
        ended."""
        self.fast_nak_at = start + burst_max_us(self.fast, self.fast_periods) \
            + self.clock.duration

    def fast_flicker_mhz(self):
        """The flicker fundamental for the decoder, in mHz: twice the mains,
        or the mains itself where the fit found that harmonic stronger -- a
        once-a-cycle light. 0 if none is fitted."""
        f = self.flicker
        h = f.harmonics
        if h is None:
            return 0
        one = h[0][0] * h[0][0] + h[0][1] * h[0][1]
        two = h[1][0] * h[1][0] + h[1][1] * h[1][1]
        if one > two:
            return f.mains * 1000 + f.rate_m // 320
        return self.fast.flicker_mhz(f.mains, f.rate_m)

    def burst_rise(self):
        """The burst's first rise through T_low plus the flicker, within
        FAST_SEARCH_US of when it is due; -1 if none."""
        start = self.fast_expect - FAST_SEARCH_US
        close = self.fast_expect + FAST_SEARCH_US
        step = 300
        n = (close - start) // step + 1
        return self.reader.rise(start, close, self.thresholds(start, n, step),
                                n, step)

    def thresholds(self, start, n, step):
        """T_low plus the flicker, for each of `n` steps of `step` us from
        `start`, in self.thr."""
        t_low = self.levels.t_low
        thr = self.thr
        if self.flicker.active_now():
            times = self.thr_t
            for i in range(n):
                times[i] = start + i * step + step // 2
            self.flicker.amounts(times, n, 100, thr)
            for i in range(n):
                thr[i] += t_low
        else:
            for i in range(n):
                thr[i] = t_low
        return thr

    def fast_accept(self, chunk, payload):
        """Follower: a burst that passed its CRC-32, kept by chunk -- a
        resend after a lost ACK is not kept twice. Once the stream from
        chunk 0 parses, it is the payload."""
        self.fast_chunks[chunk] = bytes(payload)
        self.blocks_accepted = len(self.fast_chunks)
        self.block_started = self.now
        size = self.fast.CHUNK
        data = bytearray()
        k = 0
        while k in self.fast_chunks:
            piece = self.fast_chunks[k]
            data.extend(piece)
            if not piece or len(piece) % size:
                break
            k += len(piece) // size
        fields = parse_fast_stream(data)
        if fields is not None:
            self.received = fields

    def fast_reply(self, ok, burst_end):
        """Follower: ACK or NAK from the second slot after `burst_end`,
        after the ring check, then wait for the next block. After a NAK the
        next trains afresh: the retuned taps may have failed."""
        if not ok and self.decoder is not None:
            self.decoder.forget()
        if not self.reader.check():
            self.fail(ERR_RING)
            return
        self.sub = self.STEP_BLOCK
        self.reset_block()
        self.rx = []
        self.next = self.clock.number(self.now_us) + 1
        self.lit_end = self.now_us
        self.advance()
        self.send(CRC_GOOD if ok else CRC_BAD)
        after = burst_end + self.clock.duration
        if self.tx_after_us < after:
            self.tx_after_us = after

    def watch_for_announce(self, k):
        """Follower, before its first Begin: slot `k`'s 2 ms means into the
        beacon detector. The Announce again means its staircase was refused:
        send another, or past CAL_RESTARTS end ERR_CONTRAST."""
        start = self.clock.start_of(k)
        end = start + self.clock.duration
        one = self.one_v
        t = start
        while t + FINE_US <= end:
            self.reader.parts(t, t + FINE_US, 1, one)
            if one[0] >= 0:
                self.detector.push(t // 1000, one[0])
            t += FINE_US
        if not self.detector.matches(ANNOUNCE_FAST_HALF_MS) or \
                self.detector.hi - self.detector.lo < self.announce_swing // 2:
            self.announce_since = None
            return
        # Held two windows: a Begin on 8 ms slots can match one, but is
        # framed by then.
        now = self.now_us // 1000
        if self.announce_since is None:
            self.announce_since = now
        if now - self.announce_since < 2 * BEACON_FAST_HALVES \
                * ANNOUNCE_FAST_HALF_MS:
            return
        self.cal_refusals += 1
        if self.cal_refusals > CAL_RESTARTS:
            self.fail(ERR_CONTRAST)
            return
        self.watch_announce = False
        self.announce_since = None
        self.collected = False
        self.fit_due = None
        self.fit_checked = self.fit_tried = False
        self.flicker = FlickerFilter()
        self.hold_until = 0
        self.fine_end = self.now_us + FINE_US
        self.coarse_base = None
        self.coarse_n = self.coarse_sum = self.coarse_tsum = 0
        self.enter(PHASE_CAL_SEND, now)

    def ring_samples(self, start, end):
        """2 ms means of the ring over [start, end) us: times, values,
        spans."""
        ts, vs, spans = [], [], []
        t = start
        while t + FINE_US <= end:
            v = self.reader.mean(t, t + FINE_US)
            if v >= 0:
                ts.append(t + FINE_US // 2)
                vs.append(v)
                spans.append(FINE_US)
            t += FINE_US
        return ts, vs, spans

    def begin_levels(self):
        """If the last nine slots have FAST_BEGIN's shape -- high, middle,
        low, three times -- the levels they show, else None: how a Follower
        learns the Leader's. High and middle are medians of three; black is
        the long dark before, not the pattern's lows, which a slow sensor
        leaves unsettled. Every high clears every middle, and every middle
        black, by MIN_GAP_EXACT; every low is under every middle; the middle
        a quarter of the swing from either end. No allocation."""
        if self.rx_n < 9:
            return None
        v = self.rx_values
        i = self.rx_n
        # FAST_BEGIN, "-_ -_ _- ": high, middle, low twice, then middle,
        # high, low.
        h0 = v[i % 9]
        m0 = v[(i + 1) % 9]
        l0 = v[(i + 2) % 9]
        h1 = v[(i + 3) % 9]
        m1 = v[(i + 4) % 9]
        l1 = v[(i + 5) % 9]
        m2 = v[(i + 6) % 9]
        h2 = v[(i + 7) % 9]
        l2 = v[(i + 8) % 9]
        if l0 < 0 or l1 < 0 or l2 < 0:
            return None
        black = self.levels.black
        m_lo = m0 if m0 < m1 else m1
        m_lo = m_lo if m_lo < m2 else m2
        m_hi = m0 if m0 > m1 else m1
        m_hi = m_hi if m_hi > m2 else m2
        h_lo = h0 if h0 < h1 else h1
        h_lo = h_lo if h_lo < h2 else h2
        l_hi = l0 if l0 > l1 else l1
        l_hi = l_hi if l_hi > l2 else l2
        if h_lo - m_hi < MIN_GAP_EXACT or m_lo - black < MIN_GAP_EXACT \
                or l_hi >= m_lo:
            return None
        grey = _median3(m0, m1, m2)
        white = _median3(h0, h1, h2)
        span = white - black
        if span < MIN_SWING or grey - black < span // 4 \
                or white - grey < span // 4:
            return None
        levels = Levels()
        levels.fit(black, grey, white)
        levels.last = LOW
        return levels

    def decide_slot(self, k):
        value = self.window_value(k)
        if value >= self.levels.t_low and \
                self.clock.start_of(k) < self.hold_until:
            # In the Leader's hold, light is the flicker's.
            value = self.levels.black
        lit = value >= self.levels.t_low
        if lit and self.can_phase_lock() and self.try_lock(k):
            return
        if value < 0:
            decided = self.levels.last      # keep the stream aligned, let CRC judge
        else:
            decided = self.levels.classify(value)
        self.learn(k, decided == LOW and value >= 0)
        if decided != LOW:
            self.lit_end = self.clock.start_of(k) + self.w_close
        # Trimmed: sliced and joined every slot.
        if len(self.rx) > MAX_PATTERN * 2:
            del self.rx[:-MAX_PATTERN]
        # Not exempted in STEP_FINISH: a backstop if the closing handshake's
        # own counts are ever wrong.
        self.stall_slots += 1
        if self.stall_slots > STALL_SLOTS and not self.request_resend():
            self.fail(ERR_LOST_PEER)
            return

        # Dark through and through, no part lit: a missed fast Begin's
        # burst reads dark at the median, and asking during it is unheard.
        if decided == LOW and self.part_max < self.levels.t_low:
            self.dark_run += 1
        else:
            self.dark_run = 0
        if decided == LOW:
            self.idle_slots += 1
            if self.dark_run == RESEND_QUIET_SLOTS \
                    and self.unframed >= UNFRAMED_LIT_SLOTS:
                self.unframed = 0
                self.dark_run = 0
                self.request_resend()
            # The closing handshake keeps its own time: its retry budget is
            # this state's timeout.
            if (self.idle_slots > LOST_PEER_SLOTS + self.idle_grace
                    + self.first_grace and self.sub != self.STEP_FINISH):
                if self.leader and self.sub == self.STEP_FAST_REPLY \
                        and self.fast_chunk == 0 and self.attempts == 1 \
                        and not self.pair_flicker:
                    # Not a word on the first block from a Leader that did
                    # not hold: a Follower that found flicker took none of
                    # it as light. Its hold is long over: once more.
                    self.send_block()
                    return
                self.fail(ERR_LOST_PEER)
                return
        else:
            self.idle_slots = 0
            self.idle_grace = 0
            self.start_grace = 0    # the Leader's first light: no hold left
            if not self.leader and self.sub == self.STEP_BLOCK \
                    and not self.in_block:
                self.unframed += 1

        if self.want_resend and self.idle_slots >= RESEND_QUIET_SLOTS:
            self.want_resend = False
            self.resend_requests += 1
            # One signal for a framing failure and a block never seen: send
            # it again, slower.
            self.send_sync_error()
            return

        self.rx.append(decided)
        self.rx_values[self.rx_n % 9] = value
        self.rx_n += 1
        if self.leader:
            self.leader_rx()
        else:
            self.follower_rx()

    # -- the Leader's lead-in

    def watch_lead_in(self):
        """Leader, sending a block's lead-in: its slots teach the flicker as
        they close -- the Follower is dark while it listens, and this badge's
        own light is not in the reading. After a stall only the last
        WATCH_SLOTS: catching up on every slot ran past the next symbol's
        boundary."""
        clock = self.clock
        newest = self.reader.newest_us()
        t_low = self.levels.t_low
        last = clock.number(newest - self.w_close)
        if clock.start_of(last) + self.w_close > newest:
            last -= 1
        if last - self.watched >= WATCH_SLOTS:
            self.watched = last - WATCH_SLOTS + 1
        while clock.start_of(self.watched) + self.w_close <= newest:
            k = self.watched
            self.watched = k + 1
            value = self.window_value(k)
            self.learn(k, 0 <= value < t_low)

    # -- Leader side

    def leader_rx(self):
        if self.sub == self.STEP_FAST_REPLY:
            self.fast_reply_rx()
            return
        if self.sub == self.STEP_FINISH:
            heard = "".join(self.rx[-len(COMPLETE_ACK_LATE_TAIL):])
            if heard == COMPLETE_ACK_LATE_TAIL:
                # The only evidence the Follower has the payload.
                self.result = OK
                self.phase = PHASE_DONE
                return
            # Unanswered means silent: a lit slot may be the Ack arriving
            # late, and a repeat over it cut it short. But never past
            # COMPLETE_REPEAT_MAX_SLOTS, or it goes out after the linger.
            if self.rx[-1] != LOW:
                self.wait_slots = 0
            else:
                self.wait_slots += 1
            self.finish_slots += 1
            if (self.wait_slots > COMPLETE_RETRY_SLOTS
                    or self.finish_slots >= COMPLETE_REPEAT_MAX_SLOTS):
                self.wait_slots = 0
                self.finish_slots = 0
                if self.complete_tries > MAX_COMPLETE_RETRIES:
                    # Every block acknowledged, the ending not: not OK.
                    self.fail(ERR_LOST_PEER)
                else:
                    self.rx = []
                    self.send_complete()

    def send_complete(self):
        self.complete_tries += 1
        # Queued, then acknowledged: `finishing` is the handover to waiting.
        self.send(COMPLETE)
        self.finishing = True

    # A Follower that missed a Begin never enters the block and has nothing
    # to say: asking again takes the Leader's demotion path. A request
    # already waiting is left as it is. Returns whether it asked.
    def request_resend(self):
        if self.leader or self.sub != self.STEP_BLOCK or self.in_block:
            return False
        if self.resend_requests >= MAX_BLOCK_ATTEMPTS:
            return False
        self.want_resend = True
        self.stall_slots = 0
        return True

    # -- Follower side

    def follower_rx(self):
        if self.sub == self.STEP_BLOCK:
            # Complete arrives here, after the last ACK -- never before a
            # block: that is the Announce again.
            if self.blocks_accepted and not self.in_block \
                    and "".join(self.rx[-len(COMPLETE_TAIL):]) == COMPLETE_TAIL:
                self.acknowledge_complete()
                return
            self.collect_block()
            return

        if self.sub == self.STEP_FINISH:
            # A repeat: the Ack did not arrive. Answer again.
            if "".join(self.rx[-len(COMPLETE_TAIL):]) == COMPLETE_TAIL:
                self.acknowledge_complete()
                return
            self.wait_slots += 1
            if self.wait_slots > LINGER_SLOTS:
                self.finish_follower()

    # Answer Complete, then linger to answer a repeat.
    def acknowledge_complete(self):
        self.resend_requests = 0
        self.send(COMPLETE_ACK)
        self.sub = self.STEP_FINISH
        self.wait_slots = 0
        self.idle_slots = 0
        self.rx = []

    # OK only if a block landed and the stream parsed.
    def finish_follower(self):
        self.result = OK if self.received is not None else ERR_DECODE
        self.phase = PHASE_DONE

    def await_block(self):
        # The Follower's once-a-block ring check, as send_block()'s is the
        # Leader's.
        if not self.reader.check():
            self.fail(ERR_RING)
            return
        self.rx = []
        self.reset_block()
        self.sub = self.STEP_BLOCK

    def reset_block(self):
        self.fast_bits = None
        self.in_block = False

    def collect_block(self):
        symbol = self.rx[-1]
        if not self.in_block:
            trained = self.begin_levels() if symbol == LOW else None
            if trained is not None or \
                    "".join(self.rx[-len(FAST_BEGIN):]) == FAST_BEGIN:
                if trained is not None:
                    self.levels = trained
                self.advance()
                self.watch_announce = False
                self.want_resend = False
                self.first_grace = 0
                self.in_block = True        # the rung's bits follow
                self.fast_bits = []
                self.rx = []
            return
        # The rung, two bits after FAST_BEGIN; then the burst. A low reads
        # as 0: the burst follows whatever the bits said, and an error sent
        # now goes out over it. A wrong rung costs a NAK.
        self.fast_bits.append(1 if symbol == HIGH else 0)
        if len(self.fast_bits) < FAST_RUNG_BITS:
            return
        rung = 0
        for bit in self.fast_bits:
            rung = (rung << 1) | bit
        self.fast_bits = None
        self.in_block = False
        self.start_fast_rx(self.fast.RUNGS[rung])

    def send_sync_error(self):
        self.pending_slow_down = True
        self.verify_rate = True
        self.send(SYNC_ERROR)
        self.await_block()


def fast_stream(fields):
    """A fast transfer's payload: the field count, then each field's length
    and bytes, lengths and count as varints."""
    out = bytearray()
    _put_varint(out, len(fields))
    for field in fields:
        _put_varint(out, len(field))
        out.extend(field)
    return bytes(out)


def burst_max_us(fast, periods):
    """The longest a rung's block can be on air, in us: its most payload,
    stuffed at worst."""
    symbols = fast.PREAMBLE_LEN + fast.stuffed_len(
        fast.HEADER + fast.block_bytes(periods) + fast.CRC) + fast.TAIL
    return symbols * periods * fast.COMMON_NS // 1000


def fast_block(fast, stream, chunk, periods):
    """The fast block from `chunk` of `stream`: block_bytes(periods) of it,
    or what is left."""
    at = chunk * fast.CHUNK
    return stream[at:at + fast.block_bytes(periods)]


def parse_fast_stream(data):
    """fast_stream() undone: the fields, or None if `data` is not one."""
    at = 0
    fields = []
    count, at = _get_varint(data, at)
    if count is None:
        return None
    for _ in range(count):
        n, at = _get_varint(data, at)
        if n is None or at + n > len(data):
            return None
        fields.append(bytes(data[at:at + n]))
        at += n
    return fields if at == len(data) else None


def _put_varint(out, n):
    while n >= 128:
        out.append((n & 127) | 128)
        n >>= 7
    out.append(n)


def _get_varint(data, at):
    n = shift = 0
    while True:
        if at >= len(data):
            return None, at
        byte = data[at]
        at += 1
        n |= (byte & 127) << shift
        shift += 7
        if not byte & 128:
            return n, at
