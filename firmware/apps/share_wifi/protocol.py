"""Optical Data Transfer Protocol: framing, timing and the state machine.

Sends data between two badges using nothing but the screen and the light sensor.
One badge displays flat fields at three light levels and the other reads them
back with the light sensor. LEVEL_SCHEMES says how a transmitter may produce
the three; a receiver needs no setting, because calibration measures whatever
the peer actually sends.

A payload is a list of byte strings. The protocol carries them and nothing else:
how many fields there should be, how long they may be and whether they are text
all belong to the caller.

Pure Python -- no `screen`, no `badge` -- so the wire format can be imported and
tested under CPython. The one thing it cannot know by itself is how fast the
badge is drawing, which sets the shortest slot the display can hold:

    transfer = Transfer(leader=True, payload=[b"first", b"second"],
                        frame_ms=lambda: badge.ticks_delta)

Specification: .agents/docs/optical-data-transfer-protocol.md
"""

import gc
import math
import time

# Measured figures come from .agents/docs/hardware-measurement-*.json; the
# reasoning behind each constant is in the spec. Markers such as [measured-all]
# name the firmware a claim holds for: legend in .agents/docs/README.md.

# light_level() is a 12-bit conversion in a 16-bit frame, so 16 counts is the
# smallest difference the sensor can express. [measured-all]
SENSOR_STEP = 16

# Whether a peer is there at all, before there are levels: a swing of at least
# MIN_SWING, three ADC steps. It gates beacon detection and the staircase
# reader, and is the least span calibration accepts. Noise on a held field
# measured one or two steps peak to peak, so three is above it.
MIN_SWING = 3 * SENSOR_STEP

# Whether calibrated levels can be told apart: each gap between adjacent
# levels must be at least MIN_GAP, and at least GAP_SIGMAS times the noise of
# the two levels either side of it -- the standard deviation of the staircase
# samples between their thresholds, measured on this link, in this light. A
# threshold sits half way across a gap, so GAP_SIGMAS / 2 standard deviations
# from each level. QUANTISATION_SD floors the noise: a level whose readings
# all land on one ADC code still has up to half a step of error in it.
# See "Calibration" in the spec.
MIN_GAP = 2 * SENSOR_STEP
GAP_SIGMAS = 4
QUANTISATION_SD = SENSOR_STEP / math.sqrt(12)

# Settling budget for one pixel transition: the slowest 90% the badge has
# measured, 24 ms, across nine fits of 16.1-24.1 ms in five mirror runs
# [measured-all]. The datasheet's 16 ms round trip had suggested half that.
LCD_SETTLE_MS = 24

# The same for a symbol the backlight carries alone, on white pixels: no
# liquid crystal moves, so what settles is the light sensor, which is slower
# the less light it has -- 1.0-1.3 ms to 90% in a lit room, up to 7.8 ms from
# off to the middle level in the dark [measured-all]. 10 rather than 8 for
# margin: it puts the start on 32 ms rather than 25, which in simulation kept
# every run against a sensor half as fast again as measured, where 25 lost 4
# in 144, and three in four against one twice as slow, where 25 kept one in
# fourteen. It costs about 1.5 s a transfer. Only a badge reading
# continuously can use it: one that samples once a frame is still bound by
# its frames.
BACKLIGHT_SETTLE_MS = 10

# The same in a lit room, where the sensor is fastest: 1.0-1.3 ms to 90% for
# every backlight step [measured-all], with margin. Both badges must be LIT
# for it -- see LIT_BLACK_COUNTS -- and it puts the start on 16 ms.
BACKLIGHT_LIT_SETTLE_MS = 5

# A continuous badge is LIT when the black it reads -- the room's light on
# its sensor, the peer's screen dark -- is at least this: four ADC steps. The
# mirror runs read 32 in the dark and 80-96 in a lit room [measured-all], and
# the sensor's speed follows its light.
LIT_BLACK_COUNTS = 64

# How much of that a continuous backlight badge treats as proven. The sensor's
# speed rests on one mirror's measurements and slows with less light, so the
# rungs BACKLIGHT_SETTLE_MS allows below this one are a gamble: CRC failures
# there demote rather than end the transfer. 50 ms is where pixels ran before
# their settle was measured.
BACKLIGHT_PROVEN_MS = 50

# A badge that reads continuously -- the app's listen loop, which never
# presents while it listens -- calls step() for every reading: ~2,300 a second
# on the v2 badge [measured-v2]. Its readings are pooled into two streams:
# FINE_MS samples for the data phase, where a slot's window and an edge's time
# are decided, and COARSE_MS ones for discovery and calibration, whose
# detectors and buffers were sized for about one sample a frame. A caller that
# steps once a frame gets one of each per call, as before: both are shorter
# than any frame.
FINE_MS = 2
COARSE_MS = 8
# A Follower in the handshake drops the coarse sample it is pooling when its
# readings stop for longer than this -- dead time, from the end of one fine
# sample to the start of the next. A present stops them for 7.8 ms or more
# (vertical blank, then the write); a protocol call for about 1 [measured-v2].
# A coarse sample cut short by a present holds a few ms of readings, which
# leave most of 120 Hz flicker in: at three times the lit room's light, one
# such sample a beacon half lifted BeaconDetector's midpoint over the
# Leader's rising edge, and the Follower took the missed crossing for quiet
# -- 30 of 144 failed calibration or the handshake. Never twice in a row: a
# badge whose every call outlasted this would otherwise never close one.
HANDSHAKE_GAP_MS = 7

# Room lights flicker at twice the mains frequency: every LED light measured
# did, at 120 Hz, by up to 47% of its light, and none at the mains frequency
# itself by more than 2% of that [measured-all, 2026-10-06]. That is noise
# inside a slot, and at the lit room's level it is bigger than an ADC step.
# It is also steady -- locked to the mains -- so a continuous badge in the
# data phase estimates it and takes it off each fine sample. Only from samples
# it knows were the peer dark: the back half of each slot it decides LOW. Not
# from every reading: beacons and staircases are periodic, 100 and 120 Hz are
# harmonics of them, and an estimate from everything picked up 15% of the
# swing where there was no flicker at all.
#
# Any whole number of Hz can be listed, and 50 and 60 -- the mains itself, a
# light flickering once a cycle -- were tried and left out. No light measured
# does it. In simulation such a light defeats calibration before this filter
# runs: calibration's 8 ms samples are nearly one 120 Hz period, which
# averages 120 Hz flicker away to 4%, and leave 66-76% of 50 or 60 Hz, so
# 47% flicker at either delivered 0 of 144 with or without them. And learning
# 50 Hz from 8 ms of a slot takes part of its own harmonic, 100 Hz: 126 of
# 144 in a 100 Hz room, against 143 without.
FLICKER_HZ = (100, 120)
# How much each dark slot moves the estimate, as the time it covers against
# this: a few hundred ms of dark settles it.
FLICKER_TAU_MS = 250
# Only flicker at least this big, in counts, is taken off: a quarter of an
# ADC step.
FLICKER_MIN_COUNTS = 4
# A dark slot teaches only if its back half held this many samples: a 16 ms
# slot's holds two or three, one every ~3 ms with the protocol's call.
FLICKER_MIN_POINTS = 2

# The filter's per-sample arithmetic is whole numbers from tables, never
# math.sin: on the badge a float is a heap allocation and every operation
# microseconds [measured-v2]. Sine is FLICKER_STEPS steps a period, scaled
# by FLICKER_ONE: 1.1 degrees a step, and a whole number of us a step per Hz
# (3125), so a time's step is exact. 64 steps, 5.6 degrees, did as well in
# simulation; the table is cheap.
FLICKER_STEPS = 320
FLICKER_ONE = 1024
# A sample's span, for the shrink below, in steps of this many us.
FLICKER_SPAN_US = 250
_SINE = tuple(int(round(FLICKER_ONE * math.sin(2 * math.pi * i / FLICKER_STEPS)))
              for i in range(FLICKER_STEPS))
_QUARTER = FLICKER_STEPS // 4
# One second is a whole number of periods of every FLICKER_HZ, so a time's
# phase is its microseconds into the second: step = us x hz x STEPS / 1e6,
# and STEPS / 1e6 is 1 / 15625. (ticks_us wraps at 2**30 us, not a whole
# second: one phase jump every 17.9 minutes.)
_US_PER_STEP_HZ = 1000000 // FLICKER_STEPS
# Span steps in a table: up to two samples' length.
_SPAN_STEPS = 4 * FINE_MS * 1000 // FLICKER_SPAN_US + 1
_SPAN_LAST = _SPAN_STEPS - 1


def _span_table(hz, grow):
    """A sample's mean shrinks a flicker by sin(x)/x, x = pi x span x hz:
    per FLICKER_SPAN_US step of span up to two samples' length, that or its
    inverse, scaled by FLICKER_ONE."""
    out = []
    for k in range(_SPAN_STEPS):
        x = math.pi * k * FLICKER_SPAN_US * hz / 1000000.0
        shrink = math.sin(x) / x if x else 1.0
        out.append(int(round(FLICKER_ONE * (1 / shrink if grow else shrink))))
    return tuple(out)


_SHRINK = tuple(_span_table(hz, False) for hz in FLICKER_HZ)
_GROW = tuple(_span_table(hz, True) for hz in FLICKER_HZ)


# The mains measured before calibration (option A): each badge fits the
# room's flicker from FLICKER_FIT_MS of samples taken while its peer is
# known dark -- the Follower from the Leader's opening gap, the Leader from
# its own staircase, which the Follower watches dark -- as one cycle of a
# mains in FLICKER_MAINS with FLICKER_HARMONICS harmonics. That gives the
# region, the light's own shape, and a table to correct every sample with
# from then on, calibration included. A window whose fit leaves more than
# FLICKER_FIT_MAX_NOISE counts unexplained was not dark and steady, and the
# filter learns as before; a fit whose largest harmonic is under
# FLICKER_MIN_COUNTS is a steady room, and nothing is taken off.
FLICKER_MAINS = (50, 60)
FLICKER_HARMONICS = 4
FLICKER_FIT_MS = 200
# How far into the Leader's staircase its window starts: past the opening
# gap, which the Follower spends seeing it quiet and going dark, and a
# margin -- and so that the fit, and the stall it is, falls in the middle of
# a 150 ms step rather than on a boundary.
FLICKER_FIT_LEAD_MS = 175
# The Follower cannot fit when it sees the Leader quiet: quiet is timed from
# the beacon's last edge, so the Leader may have been dark only 30 ms of
# that. It is dark for its opening gap and its staircase's first step, low,
# 450 ms, which the Follower sees 30-300 ms in: so it fits this long after,
# from this long of samples, inside it with 50 ms to spare -- 150 ran into
# the step's end when the quiet was seen late, and the fit was refused.
# 100 ms is five cycles of 50 Hz and six of 60.
FLICKER_FOLLOWER_FIT_MS = 100
FLICKER_FIT_MAX_NOISE = 12
# A fitted harmonic counts only this many of its standard errors above the
# fit's noise. Without it, noise in 16-count steps fitted 8.5 counts of 50 Hz
# in a dark room with no flicker at all.
FLICKER_FIT_SIGMAS = 4
# A harmonic is fitted only if the window's samples, averaged over their
# spans, keep this much of it on average.
FLICKER_MIN_SEEN = 0.3
# Refinements of the fit's projections, each on what the others leave.
FLICKER_FIT_PASSES = 2
# Samples kept for the fit: ~290 ms of them, one every ~3 ms.
FLICKER_BUFFER = 96

# The mains drifts -- grids hold within about 0.02 Hz, which moves 120 Hz
# flicker 14 degrees a second -- so the table's timing is tracked: a phase
# lock on its offset, in steps, and the offset's rate. Each update is a
# least-squares phase error from samples the peer was dark for: every
# FLICKER_TRACK_POINTS while this badge sends its staircase or waits out the
# gap, and the back half of each slot decided dark. FLICKER_TRACK_GAIN of
# the error moves the offset. The rate is the slope of a line fitted to
# the timing each update measured against time, forgetting
# FLICKER_RATE_MEMORY of the past an update, and used once its points span
# FLICKER_RATE_MIN_S: it carries the timing through calibration's
# stretches with nothing dark to see. A rate stepped by each update's own
# error, divided by the time since the last, was noise -- 30 times the
# drift's rate, either sign -- since updates can come 24 ms apart.
FLICKER_TRACK_POINTS = 8
FLICKER_TRACK_GAIN = 0.25
FLICKER_RATE_MEMORY = 0.99
FLICKER_RATE_MIN_S = 0.3
# The rate's bound, in steps a second: 0.1 Hz of mains drift, five times
# what grids allow.
FLICKER_MAX_RATE = 32
# A single update's error, in steps, is clipped to this: one noisy slot
# cannot throw the lock.
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


class FlickerFilter:
    """Mains flicker at each of FLICKER_HZ: learnt from dark slots a sample
    at a time, taken off every fine sample.

    Each frequency is a sine and a cosine part, in counts. Learning sums
    each dark sample against the sine table, in whole numbers; a slot's
    sums become an estimate once, when it is decided, and only that step
    uses floats. Correcting is a table look-up and a multiply a frequency,
    for the frequencies big enough to take off."""

    def __init__(self):
        n = len(FLICKER_HZ)
        self.amp = [0.0] * (2 * n)      # sine, cosine amplitude per frequency
        # The same, in quarter counts, and which frequencies are taken off:
        # what correct() uses, refreshed when the estimate moves.
        self.amp4 = [0] * (2 * n)
        self.active = []
        # The slot being collected: sums of value x sine/cosine and of sine
        # and cosine, in table units; of value, less the slot's first, to
        # keep the products small ints; the count and the time covered.
        self.xs = [0] * (2 * n)
        self.basis = [0] * (2 * n)
        self.ref = 0
        self.sum = 0
        self.count = 0
        self.covered_us = 0
        # Fitted before calibration (see FLICKER_FIT_MS): the mains, each
        # harmonic's sine and cosine part in counts, and tables per span
        # step of the flicker and its slope, in quarter counts. `steady`: the
        # fit found nothing to take off.
        self.mains = 0
        self.harmonics = None
        self.steady = False
        self.base = None
        self.tables = {}
        # What the last fit() saw, whatever it returned: why it refused
        # ("few" samples, "unfit" -- no mains fitted, "dof" -- too few left
        # over the harmonics, "noise", or None), the mains it preferred, the noise left,
        # and each harmonic's size in counts before the significance test.
        # For Dev Mode's room-flicker tool; the transfer does not read it.
        self.fit_info = None
        # The phase lock: the table's offset in thousandths of a step at
        # anchor_us, its rate in thousandths of a step a second; the same as
        # floats; and the tracker's sums for the points since its last
        # update -- count, residual, slope, their products, in quarter counts.
        self.off_m = 0
        self.rate_m = 0
        self.offset = 0.0
        self.rate = 0.0
        self.anchor_us = None
        self.last_us = 0
        # The rate's line: time since the fit in s, the timing measured, in
        # steps and unwrapped; weighted sums of 1, t, m, t*t, t*m.
        self.fit_us = 0
        self.lw = self.lt = self.lm = self.ltt = self.ltm = 0.0
        self.clear_track()

    def clear_track(self):
        """Clears the tracker's sums toward the next phase-lock update."""
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

    @property
    def fitted(self):
        return self.harmonics is not None or self.steady

    def fit(self, ts, vs, spans):
        """Fits the samples -- times in us, mean values, spans in us -- as
        one cycle of each mains in FLICKER_MAINS with up to FLICKER_HARMONICS
        harmonics on a straight line, and keeps the mains that explains
        more. Returns what it found: "flicker", "steady" (nothing worth
        taking off), or None -- too few samples, or a window that was not
        steady, when the filter goes on learning as before.

        Not a full least squares: over 100-200 ms the harmonics are near
        independent, so each is projected on its own, in whole numbers from
        the sine table, and the projections are refined FLICKER_FIT_PASSES
        times on what the others leave. It runs in the middle of
        calibration, where a stall stretches a staircase step."""
        n = len(ts)
        self.fit_info = {"reason": "few", "mains": None, "noise": None,
                         "sizes": None}
        if n < 12:
            return None
        # The line: a sensor still settling from the last thing it saw,
        # which the harmonics would otherwise take for 50 Hz.
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
        level = (sy - slope * sx) / n
        flat = [0] * n
        for k in range(n):
            x = time.ticks_diff(ts[k], middle) // 1000
            flat[k] = int(round(vs[k] - level - slope * x))
        best = None
        for mains in FLICKER_MAINS:
            found = self.fit_mains(ts, flat, spans, mains)
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
        # A harmonic counts if the samples saw it -- its amplitude as their
        # spans shrank it -- FLICKER_FIT_SIGMAS of its standard error clear;
        # one that does not is dropped rather than taken off.
        error = noise * math.sqrt(2.0 / n)
        kept = []
        strongest = 0.0
        for h in range(FLICKER_HARMONICS):
            a, b = harmonics[h]
            size = sizes[h]
            if (size * seen[h] >= FLICKER_FIT_SIGMAS * error
                    and size >= FLICKER_MIN_COUNTS):
                kept.append((a, b))
                strongest = max(strongest, size)
            else:
                kept.append((0.0, 0.0))
        if not strongest:
            self.steady = True
            return "steady"
        self.mains = mains
        self.harmonics = kept
        self.base = None
        self.tables = {}
        self.anchor_us = ts[-1]
        self.fit_us = ts[-1]
        return "flicker"

    def fit_mains(self, ts, flat, spans, mains):
        """One mains' projections: (what is left unexplained, the mains, how
        many harmonics were fitted, [(a, b) per harmonic in counts], how much
        of each the samples saw). A harmonic whose samples' spans shrink it
        below FLICKER_MIN_SEEN on average is left out: dividing by that
        shrink magnified noise into flicker -- 25 times, for 240 Hz in
        samples 4 ms long."""
        n = len(ts)
        left = flat[:]
        # Per harmonic: each sample's sine and cosine, shrunk by its span,
        # scaled by FLICKER_ONE; and what the fit takes off it so far.
        sines = []
        cosines = []
        parts = []
        seen = [0.0] * FLICKER_HARMONICS
        used = []
        for h in range(1, FLICKER_HARMONICS + 1):
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
        remaining = 0
        for k in range(n):
            remaining += left[k] * left[k]
        harmonics = [(0.0, 0.0)] * FLICKER_HARMONICS
        for u in range(len(used)):
            a, b = coeffs[u]
            harmonics[used[u] - 1] = (a * FLICKER_ONE, b * FLICKER_ONE)
        return (remaining, mains, len(used), harmonics, seen)

    def table(self, k):
        """The fitted flicker and its slope per step, in quarter counts, as
        a sample of span step `k` sees them. Built once per span step, from
        one base table per harmonic made once a fit, in whole numbers: a
        span step costs a pass of FLICKER_STEPS, not trigonometry."""
        out = self.tables.get(k)
        if out is not None:
            return out
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
        out = (flicker, slope)
        self.tables[k] = out
        return out

    def step_at(self, now_us):
        """Where `now_us` falls in the fitted table: the mains' step, moved
        by the phase lock's offset and its rate since the anchor."""
        base = (now_us % 1000000) * self.mains // _US_PER_STEP_HZ
        elapsed_ms = time.ticks_diff(now_us, self.anchor_us) // 1000
        if elapsed_ms > 30000:
            elapsed_ms = 30000
        shift = (self.off_m + elapsed_ms * self.rate_m // 1000) // 1000
        return (base + shift) % FLICKER_STEPS

    def track(self, now_us, value, span_us):
        """A sample the peer was dark for, toward the next phase-lock
        update: its residual against the table, and the table's slope."""
        flicker, slope = self.table(_span_step(span_us))
        i = self.step_at(now_us)
        r = 4 * value - flicker[i]
        d = slope[i]
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
                # Where step_at() put the table now, and where the points
                # say it is: unwrapped, so the line through them is too.
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
                if spread >= FLICKER_RATE_MIN_S * FLICKER_RATE_MIN_S / 4:
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

    def correct(self, now_us, value, span_us=0):
        """`value` less the flicker estimated at `now_us`, as a whole count.
        `value` is a mean over `span_us` centred there, which shrinks the
        flicker by sin(x)/x: so is what is taken off."""
        if self.harmonics is not None:
            flicker, _ = self.table(_span_step(span_us))
            corrected = value - (flicker[self.step_at(now_us)] + 2) // 4
            return corrected if corrected > 0 else 0
        if self.steady:
            return value
        active = self.active
        if not active:
            return value
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
        corrected = value - (flicker + 2) // 4
        return corrected if corrected > 0 else 0

    def collect(self, now_us, value, span_us):
        """A mean raw reading over `span_us` centred on `now_us` -- a fine
        sample's, from the back half of the slot in progress. The mean has
        the flicker shrunk by sin(x)/x, so each point is scaled back up:
        what is learnt is the flicker itself, and correct() shrinks it again
        for the sample it corrects. Learnt shrunk, it was taken off shrunk
        twice."""
        if self.harmonics is not None:
            self.track(now_us, value, span_us)
            return
        if self.steady:
            return
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
            # Slowly, on purpose: weighting the first dark slots fully let a
            # slot misjudged dark set a wrong estimate, which then corrupted
            # the readings that taught it -- 0 of 3 delivered against 47%
            # flicker, where this delivers 143 of 144.
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

    def size(self):
        """The largest flicker amplitude estimated, in counts."""
        out = 0.0
        if self.harmonics is not None:
            for a, b in self.harmonics:
                out = max(out, math.sqrt(a * a + b * b))
            return out
        for i in range(len(FLICKER_HZ)):
            out = max(out, math.sqrt(self.amp[2 * i] ** 2
                                     + self.amp[2 * i + 1] ** 2))
        return out


# How late a continuous receiver sees an edge, against the peer's slot
# boundary. The backlight: up to one ~263 us PWM period for the new duty to
# take effect, then the sensor's climb to T_low -- about 0 lit and 0.3 ms in
# the dark from the dim low, 1.2-1.9 ms from off [measured-all]. Pixels: the transmitter presents at a random point
# in the refresh, so half a refresh waiting for vertical blank on average, and
# then the write and the liquid crystal -- a rise crossed T_low 8.5-18.5 ms
# after the write began, varying with how fast the panel was that run
# [measured-v2]; PIXEL_EDGE_MS is the middle of that.
BACKLIGHT_EDGE_LAG_MS = 1
PIXEL_EDGE_MS = 13

# How coarsely a continuous transmitter places a backlight symbol: the loop
# changes it within a pass of the boundary, the duty takes up to a PWM period,
# and `now` is whole ms. Stands in for a frame in the end-of-slot guard.
BACKLIGHT_TX_MS = 3

# Where a continuous pixel receiver's window opens, as a share of the slot.
# Its peer presents at a random point in the refresh, so a symbol arrives
# anything from the dead time to a refresh later than the last, 7-33 ms on a
# slow panel [inferred-v2 from the measured range], and then settles for up
# to 24 ms: half way through an 80 ms slot is too soon.
PIXEL_WINDOW_PERCENT = 60

# The backlight's end-of-slot guard as a share of the slot, at least. Its
# transmitter is precise, but the sensor is not: a rise out of black to the
# dim middle level crosses T_low 3 ms later than one to high in the dark, and
# more with less light, so a grid locked on one sits that far from the other
# [inferred-all]. Proportional, so a demotion widens it.
BACKLIGHT_GUARD_PERCENT = 15

# A slot must be worth this many settle times. 2x permits 32ms rungs, which
# simulation showed cannot carry a block.
SETTLE_SLOTS = 3

# How far short of a slot's end its samples stop counting, as a percentage of a
# frame. The peer shows its next symbol on the first frame after its slot
# begins, and our grid -- set from one edge per message -- can sit a few ms
# late of the peer's, so the last few ms of a slot can already hold the next
# symbol. 65 covers half a frame plus its jitter: 7ms at 11ms frames.
END_GUARD_PERCENT = 65

# Slowest first. A badge starts on the fastest rung its sampling and the
# scheme's settle allow -- see rung_for() -- and only ever steps down. Rungs
# below 50 ms are reachable only by a continuous badge on the backlight
# scheme; for pixels, 3 x 24 ms puts the floor at 80. Rungs faster than a
# scheme's floor are never started on: a rate too fast to carry a block is
# also too fast to carry the pattern announcing the demotion, which leaves the
# two badges a rung apart and deaf to each other.
SLOT_LADDER = (250, 200, 160, 130, 100, 80, 65, 50, 40, 32, 25, 20, 16)

# Rungs past the proven floor a transfer will gamble on. Zero: see SLOT_LADDER.
OPTIMISTIC_RUNGS = 0

ANNOUNCE_HALF_MS = 250   # Leader beacon half period
PRESENT_HALF_MS = 400    # Follower beacon half period
BEACON_TOLERANCE = 0.20

# One calibration step. 200 ms, sized for a sample a frame at frames up to
# 40 ms; a continuous badge samples every COARSE_MS whatever its frames, and
# PLATEAU_CONTINUOUS_MS still gives it eighteen samples a step. The length
# follows how the *reader* samples, and a Leader sends before it has read
# anything of its peer: so it is the sender's own way of reading that sets
# it, which is the peer's too -- both badges run the same app. 150 rather
# than 100: in simulation 100 cost 20 of 144 against everything worse than
# measured at once, and 150 cost 4 for about a second more a transfer.
PLATEAU_MS = 200
PLATEAU_CONTINUOUS_MS = 150
CAL_REPS = 3
CAL_GAP_MS = 300

# The frame-rate class, carried by how many repetitions a badge's staircase
# has: CAL_REPS for a FAST badge, CAL_REPS_SLOW for a SLOW one. A reader
# counting SLOW_STEPS_MIN or more high steps takes its peer for SLOW. Two
# missed steps would read a SLOW badge as FAST; one spurious step reads a FAST
# one as SLOW, which only costs speed. See "Agreeing a rung" in the spec.
CAL_REPS_SLOW = 5
SLOW_STEPS_MIN = 4
# And LIT, a continuous badge in a lit room, sends CAL_REPS_LIT; a reader
# counting LIT_STEPS_MAX or fewer high steps takes its peer for LIT. A missed
# high step reads a FAST badge as LIT, which splits the rungs only if the
# reader is LIT too and on the backlight. See "Agreeing a rung" in the spec.
CAL_REPS_LIT = 2
LIT_STEPS_MAX = 2
# A badge is FAST when the median of its last FRAME_SAMPLES frame times lets
# it sample the fastest rung the panel allows (2.5 frames a slot); otherwise
# SLOW. Both badges then start on the slower class's rung: the fastest rung
# for two FAST badges, SLOW_RUNG_MS when either is SLOW -- 2.5 frames of up
# to 40 ms.
FRAME_SAMPLES = 31
SLOW_RUNG_MS = 100

ANNOUNCE_TIMEOUT_MS = 60000

# Outer deadline on a single block, and the backstop for the ways of getting
# stuck the other two budgets do not describe. Counted per block rather than
# per transfer, so payload length stays the caller's business.
BLOCK_TIMEOUT_MS = 300000

LOST_PEER_SLOTS = 16
MAX_BLOCK_RETRIES = 2

# Slots spent listening without the transfer advancing.
#
# LOST_PEER_SLOTS counts silence, and silence is not the only way a link dies:
# a peer stuck on grey, or one whose grid no longer matches ours, decodes to
# something every slot and resets the silence counter for ever. This second
# budget counts progress instead -- advance() restarts it -- and exceeding it
# is ERR_LOST_PEER.
STALL_SLOTS = 4 * LOST_PEER_SLOTS

# Black slots a Follower waits out before asking for a block again, so the
# request cannot land while the Leader is still transmitting. Longer than any
# gap inside a block, where the only black is a single-slot Field Separator.
# A framing failure waits for the same thing: the Leader is part way through a
# block and listens to nothing until it has sent the rest.
RESEND_QUIET_SLOTS = 3

# Complete is acknowledged rather than assumed: it is six slots with no lead-in
# and the easiest message to miss, and a Leader that reported OK on transmitting
# it could be wrong. The Leader repeats until answered; the Follower lingers
# afterwards so it is still there to answer a repeat.
COMPLETE_RETRY_SLOTS = 12       # > len(COMPLETE_ACK) + 2
MAX_COMPLETE_RETRIES = 3
LINGER_SLOTS = 20               # > COMPLETE_RETRY_SLOTS, so a repeat is answered

# Ceiling on transmissions of one block. MAX_BLOCK_RETRIES bounds CRC failures
# but the sync-error path resets it, which otherwise leaves a demote-and-resend
# loop bounded by nothing. Derived from the ladder so it cannot drift.
MAX_BLOCK_ATTEMPTS = (MAX_BLOCK_RETRIES + 1) * len(SLOT_LADDER)

BLOCK_BYTES = 64
RESYNC_LEAD_IN = 4    # black slots before each block, so a resend survives a rate change
MAX_PATTERN = 16      # longest run of symbols any match looks back over


# Breaking in. A continuous Leader reads while it transmits, so a continuous
# Follower that has lost the block -- its framing, or the verdict on the last
# one -- need not wait for it to end: it shows high and holds it. The Leader
# takes BREAK_DETECT_SLOTS of high, while it sends a block, as a break, stops,
# and listens. The Follower takes BREAK_ACK_SLOTS of dark as the Leader having
# stopped, and sends the Data Sync Error it always would have: both demote on
# that, as before, so a break cannot split the rungs. Unanswered after
# BREAK_MAX_SLOTS, it waits the block out as before. A Follower in STEP_VERDICT
# that has seen more than VERDICT_LOST_SYMBOLS lit symbols without a verdict
# among them has missed it, and breaks too.
BREAK_DETECT_SLOTS = 2
BREAK_ACK_SLOTS = 2
BREAK_MAX_SLOTS = 24
VERDICT_LOST_SYMBOLS = 12

# Extra black before a block resent after a rate change. The Leader acts on a
# Data Sync Error at its fourth symbol -- the leading black is discarded while
# it waits for a reply to begin, so the first '_' starts one and the black after
# it ends it -- so the peer is still transmitting the other five at the old
# rate while the resend's lead-in goes out. They eat it, and the receiver never
# gets the silence it needs to phase-lock. A whole pattern's worth at the new
# rate more than covers the remainder sent at the old one.
SYNC_LEAD_IN = 9


# --- symbols ----------------------------------------------------------------
# '-' high, binary 1
# '_' middle, binary 0
# ' ' low, black, a break. Never appears inside payload data, which is what
#     makes every control pattern unforgeable by the payload.

HIGH = "-"
MID = "_"
LOW = " "

BEGIN_BLOCK = "-_ -_ -_ "
END_BLOCK = "_- _- _- "
CRC_GOOD = "-- -- -- "
CRC_BAD = "__ __ __ "
SYNC_ERROR = "  _  _  _"
COMPLETE = "- - - "


# --- light levels -----------------------------------------------------------
# How a transmitter produces each symbol: (pixels, backlight). Pixels are
# "black", "white" or an (r, g, b) tuple -- black and white being the panel's
# full rgb(0, 0, 0) and rgb(255, 255, 255), not the Tufty palette's tinted
# color.black and color.white; backlight is the display.backlight()
# value set with the field. A receiver never needs to know which scheme is in
# use -- calibration measures the levels that actually arrive -- but the
# scheme is still a joint decision: calibration chooses one, and from then on
# both badges transmit in it. See "Light levels and symbols" and "Choosing a
# scheme" in the spec.

# The original: pixels carry the level, the backlight is held at full. The
# middle field is 68% of white per channel, chosen to hold across gamma
# 1.0-2.5. Against the tinted palette white it was rgb(151, 162, 146) and
# measured at 0.400 of the swing [measured-all]; against full white it is a
# neutral rgb(173, 173, 173), the same share of the swing [inferred-all]. The
# default.
SCHEME_FIELDS = "fields"

# The backlight carries the level, and the pixels are white at every level,
# low included: low is the backlight off, which is as dark as black pixels
# (bleed reads the same, [measured-all]) and leaves the pixels alone, so the
# scheme is carried by display.backlight() alone -- a PWM duty, live at once,
# with no present and no liquid crystal settle in the way ([source-v2]; 0 is
# off on v3 too, despite its duty floor [source-v3]).
#
# Low is dim, not off: 0.5, 14% duty on v2, just above the backlight
# driver's ~11% threshold [source-v3], so the sensor stays faintly lit and a
# rise starts from a little current rather than none. On the badge it cut
# the dark room's edge delay from 1.2-1.9 ms to 0.3, and read under one ADC
# step above off, so it costs no gap [measured-all, 2026-10-06]. The middle
# is 0.82 to match: 57% duty on v2 [source-v2], 61% on v3 [source-v3]. It
# was 0.74, which six mirror runs read only two ADC steps above black, then
# 0.80. 0.25 and 0.33 failed calibration every time in simulation.
# It only works if the light sensor averages the ~3.8kHz PWM rather than
# following it, which the badge's does [measured-v2]. Nothing depends on that
# holding: a badge offering it has calibration measure both schemes, and
# choose_scheme() falls back to fields when the middle step does not hold.
SCHEME_BACKLIGHT = "backlight"

MID_FIELD_RGB = (173, 173, 173)
MID_BACKLIGHT = 0.82
LOW_BACKLIGHT = 0.5

LEVEL_SCHEMES = {
    SCHEME_FIELDS: {
        HIGH: ("white", 1.0),
        MID: (MID_FIELD_RGB, 1.0),
        LOW: ("black", 1.0),
    },
    SCHEME_BACKLIGHT: {
        HIGH: ("white", 1.0),
        MID: ("white", MID_BACKLIGHT),
        LOW: ("white", LOW_BACKLIGHT),
    },
}


# A square wave against a resting level. The two beacons are told apart by
# period alone, since before calibration there are no thresholds to tell levels
# apart by.
def beacon_field(elapsed, half_ms, low=MID):
    return HIGH if (elapsed // half_ms) % 2 else low

# The Follower's answer to Complete, and the only thing that lets the Leader
# report OK.
COMPLETE_ACK = "__- __- "


# A pattern's trailing black is a separator rather than information, and it is
# the hardest symbol to read: it follows white, so it is the panel's full swing,
# and the peer can still be part way down it when sampling opens. Receivers
# match without it. They stay unforgeable because of the black INSIDE them.
#
# Begin Block is deliberately absent: its trailing black is the boundary with
# the payload, and matching a slot early would take it for a Field Separator.
CRC_GOOD_TAIL = CRC_GOOD.rstrip(LOW)
CRC_BAD_TAIL = CRC_BAD.rstrip(LOW)
COMPLETE_TAIL = COMPLETE.rstrip(LOW)
COMPLETE_ACK_TAIL = COMPLETE_ACK.rstrip(LOW)
SYNC_ERROR_TAIL = SYNC_ERROR.rstrip(LOW)


# --- exit values ------------------------------------------------------------

OK = "OK"
ERR_NO_PEER = "ERR_NO_PEER"
ERR_CONTRAST = "ERR_CONTRAST"
ERR_LOST_PEER = "ERR_LOST_PEER"
ERR_CRC = "ERR_CRC"
ERR_DECODE = "ERR_DECODE"
ERR_CANCELLED = "ERR_CANCELLED"


# --- CRC-16/CCITT-FALSE -----------------------------------------------------
# poly 0x1021, init 0xFFFF, no reflection, no final XOR.

def crc16(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc



# --- beacon detection -------------------------------------------------------

# Detects the two pre-calibration beacons. Before calibration there are no
# thresholds, so presence is relative -- a swing of MIN_SWING or more -- and
# the two beacons are told apart by period alone, never by brightness.
class BeaconDetector:
    WINDOW_MS = 1000
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

    # Indices of the samples inside the window, oldest first, and their
    # range in `lo` and `hi`. Fills a preallocated list rather than building a
    # new one every frame.
    def _fill_window(self):
        newest = self.times[(self.head - 1) % self.CAPACITY]
        found = lo = hi = 0
        for i in range(self.count):
            idx = (self.head - 1 - i) % self.CAPACITY
            if time.ticks_diff(newest, self.times[idx]) > self.WINDOW_MS:
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

    # Returns the measured half period in ms, or None if there is no beacon.
    def half_period(self):
        count = self._fill_window()
        if count < 8:
            return None

        if self.hi - self.lo < MIN_SWING:
            return None

        mid = (self.lo + self.hi) // 2
        # A beacon spends about half its time either side of its middle. A
        # quiet window with an outlier in it has the swing but not that:
        # without this, a few such crossings once averaged to a beacon's half
        # period, and a Leader "found" a Follower that had not started.
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
                if first is None:
                    first = self.times[idx]
                last = self.times[idx]
                crossings += 1
                above = now_above
        if crossings < 2:
            return None
        return time.ticks_diff(last, first) // (crossings - 1)

    # Milliseconds since the last midpoint crossing, or since the window began
    # if nothing has crossed. A beacon crosses constantly; a held field does not.
    def quiet_ms(self, now):
        count = self._fill_window()
        if count < 4:
            return 0
        if self.hi - self.lo < MIN_SWING:
            return time.ticks_diff(now, self.times[self.window[0]])
        mid = (self.lo + self.hi) // 2
        above = self.values[self.window[0]] >= mid
        last = self.times[self.window[0]]
        for i in range(1, count):
            idx = self.window[i]
            now_above = self.values[idx] >= mid
            if now_above != above:
                last = self.times[idx]
                above = now_above
        return time.ticks_diff(now, last)

    def low(self):
        """The beacon's black -- the room's light on the sensor -- or None if
        the window is empty: the median of the readings in the darker half of
        the window's range. Not the darkest reading, which one outlier
        decides."""
        count = self._fill_window()
        if not count:
            return None
        mid = (self.lo + self.hi) // 2
        dark = sorted(self.values[self.window[i]] for i in range(count)
                      if self.values[self.window[i]] <= mid)
        return dark[len(dark) // 2]

    def matches(self, target_ms):
        measured = self.half_period()
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
        # How many calibration samples landed in the middle and top quarters.
        self.middle_samples = 0
        self.high_samples = 0
        # Each level's noise on this link, from calibration (standard
        # deviation, counts).
        self.noise_black = 0.0
        self.noise_grey = 0.0
        self.noise_white = 0.0

    def fit(self, black, grey, white):
        self.black, self.grey, self.white = black, grey, white
        self.t_low = (black + grey) // 2
        self.t_high = (grey + white) // 2
        self.band = (white - black) * 5 // 100

    # Hysteresis: a sample inside a band keeps the level decided last time.
    def classify(self, value):
        if abs(value - self.t_low) <= self.band or abs(value - self.t_high) <= self.band:
            return self.last
        if value < self.t_low:
            self.last = LOW
        elif value < self.t_high:
            self.last = MID
        else:
            self.last = HIGH
        return self.last


# The staircase: CAL_REPS passes of low, middle and high, bracketed by
# longer black gaps. Self identifying without thresholds, because it is the only
# signal that steps monotonically through three levels, and its closing gap is a
# shared time reference both badges can find.
def staircase_ms(reps=CAL_REPS, plateau=PLATEAU_MS):
    return 2 * CAL_GAP_MS + reps * 3 * plateau


# A FAST badge's staircase, the shortest there is.
STAIRCASE_MS = staircase_ms()


# The order of the three steps carries one flag, which is how calibration
# chooses a light-level scheme without a message of its own: the levels are
# read from the distribution of samples, which order does not change, and once
# they are known the order can be read back out of the same samples. What the
# flag means depends on who sends it -- see "Choosing a scheme" in the spec.
ASCENDING = (LOW, MID, HIGH)
REVERSED = (LOW, HIGH, MID)

# Samples a level must hold to count as a step rather than a transition passing
# through it. A step is PLATEAU_MS, about 18 frames; the panel's settle is one
# or two.
STEP_SAMPLES = 3


def staircase_field(elapsed, order=ASCENDING, reps=CAL_REPS,
                    plateau=PLATEAU_MS):
    if elapsed < CAL_GAP_MS:
        return LOW
    body = elapsed - CAL_GAP_MS
    if body >= reps * 3 * plateau:
        return LOW
    return order[(body // plateau) % 3]


def staircase_order(values, levels):
    """ASCENDING or REVERSED: which order a measured staircase stepped in.

    Counts middle-to-high steps against high-to-middle ones, which the two
    orders produce once a repetition each and never the other way round.
    Plain thresholds, no hysteresis, and nothing allocated: it runs once.
    """
    rising, falling, _ = staircase_steps(values, levels)
    return REVERSED if falling > rising else ASCENDING


def staircase_slow(values, levels):
    """True if a measured staircase has SLOW_STEPS_MIN or more high steps."""
    return staircase_steps(values, levels)[2] >= SLOW_STEPS_MIN


def staircase_lit(values, levels):
    """True if a measured staircase has LIT_STEPS_MAX or fewer high steps."""
    return staircase_steps(values, levels)[2] <= LIT_STEPS_MAX


def staircase_steps(values, levels):
    """(middle-to-high, high-to-middle, high) steps in a measured staircase.

    A step is a level held for STEP_SAMPLES samples; anything shorter is a
    transition passing through. Each repetition has one high step, in either
    order.
    """
    rising = falling = highs = held = 0
    current = previous = None
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
        if held == STEP_SAMPLES:
            if previous == MID and level == HIGH:
                rising += 1
            elif previous == HIGH and level == MID:
                falling += 1
            if level == HIGH:
                highs += 1
            previous = level
    return rising, falling, highs


def separation(levels):
    """How well a staircase separated its levels, for choosing a scheme.

    The narrower of the two gaps between adjacent levels, in counts -- what
    decides whether a symbol can be told from its neighbours -- or 0 if the
    middle step did not hold steady. A middle and a high step last equally
    long, so a clean staircase puts about as many samples in one as the other.
    A backlight whose PWM the sensor follows scatters the middle step's samples
    to either end, and the few left over can still fit a plausible grey; this
    is what catches that.
    """
    if levels.middle_samples * 2 < levels.high_samples:
        return 0
    return min(levels.grey - levels.black, levels.white - levels.grey)


def quantile(sorted_values, fraction):
    index = int(len(sorted_values) * fraction)
    return sorted_values[min(index, len(sorted_values) - 1)]


# A level needs this many settled samples before its noise means anything.
MIN_HELD_SAMPLES = 8


def held_spreads(values, t_low, t_high):
    """Each level's noise from a staircase: (black, grey, white) standard
    deviations, or None if a level has fewer than MIN_HELD_SAMPLES settled
    samples.

    `values` in the order they were read. A sample counts only once the
    reading has stayed in its level's class -- below `t_low`, between, above
    `t_high` -- for STEP_SAMPLES readings: a transition passing through, or
    a panel still settling just after one, would otherwise count as noise,
    and on a 24 ms panel at 8 ms frames it swamps the real thing. Welford's
    running variance, so float32 holds its precision.
    """
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
    if min(n) < MIN_HELD_SAMPLES:
        return None
    return (math.sqrt(m2[0] / n[0]), math.sqrt(m2[1] / n[1]),
            math.sqrt(m2[2] / n[2]))


def gap_separable(gap, noise_a, noise_b):
    """Whether two adjacent levels `gap` apart, with these noises, can be told
    apart: MIN_GAP, and GAP_SIGMAS of the noisier side's noise."""
    noise = max(noise_a, noise_b, QUANTISATION_SD)
    return gap >= MIN_GAP and gap >= GAP_SIGMAS * noise


# Measures a staircase without knowing when it started. Aligning to plateau
# boundaries is impossible before thresholds exist, so it keeps every sample and
# reads the levels out of their distribution -- percentiles rather than equal
# thirds, since how much leading black is captured depends on when it started.
class StaircaseReader:
    LIMIT = 512

    def __init__(self):
        self.values = []
        self.low_since = None
        self.ended_at = None

    def feed(self, now, value):
        if self.ended_at is not None:
            return True
        # A rolling buffer, not a hard stop. Freezing at LIMIT meant a reader
        # that started early filled up with ambient and never saw the staircase
        # at all -- min and max stayed equal and calibration always failed.
        self.values.append(value)
        if len(self.values) > self.LIMIT:
            del self.values[0]
        if len(self.values) < 24:
            return False

        lo = min(self.values)
        hi = max(self.values)
        if hi - lo < MIN_SWING:
            return False

        # The closing gap: a sustained return to the darkest level ends it, and
        # its leading edge is the reference both badges time the clock from.
        if value <= lo + (hi - lo) // 4:
            if self.low_since is None:
                self.low_since = now
            elif time.ticks_diff(now, self.low_since) >= CAL_GAP_MS - 60:
                self.ended_at = self.low_since
                return True
        else:
            self.low_since = None
        return False

    def levels(self):
        """The three levels, or None if they cannot be separated."""
        if len(self.values) < 32:
            return None
        ordered = sorted(self.values)
        black = quantile(ordered, 0.10)
        white = quantile(ordered, 0.90)
        span = white - black
        if span < MIN_SWING:
            return None

        # The middle cluster, isolated by value rather than by count, so uneven
        # amounts of captured black do not drag the estimate around.
        #
        # From everything an eighth of the swing clear of either end, and only
        # then held to the quarter rule. Taken from the middle half, as it once
        # was, the rule could never fail: whatever landed there had its median
        # inside the middle half by construction. An eighth so that black and
        # white stay out of the band and a middle level anywhere in it is
        # counted whole; one between an eighth and a quarter from an end is
        # then measured where it is, and fails.
        lo = black + span // 8
        hi = black + 7 * span // 8
        middle = [v for v in ordered if lo <= v <= hi]
        above = sum(1 for v in ordered if v > hi)

        if len(middle) < 4:
            return None
        grey = middle[len(middle) // 2]

        # Nearer an end than an eighth -- color.grey is 7% of the swing -- the
        # middle step falls outside the band, and what is left in it is the
        # panel's transitions passing through, whose median can sit anywhere.
        # A step is held as long as the high one, a transition for a frame or
        # two, so a middle level is real only if about as many samples as the
        # top end holds sit close around it.
        near = span // 8
        held = sum(1 for v in middle if grey - near <= v <= grey + near)
        if held * 2 < above:
            return None

        step = span // 4
        if grey - black < step or white - grey < step:
            return None
        out = Levels()
        out.fit(black, grey, white)
        # Noise per level, from the samples settled in it -- this link's, in
        # this light -- and each gap against the noisier side.
        spreads = held_spreads(self.values, out.t_low, out.t_high)
        if spreads is None:
            return None
        out.noise_black, out.noise_grey, out.noise_white = spreads
        if not (gap_separable(grey - black, out.noise_black, out.noise_grey)
                and gap_separable(white - grey, out.noise_grey,
                                  out.noise_white)):
            return None
        # Still counted over the middle half and the top quarter, which is
        # what separation() was tuned against.
        top = black + 3 * span // 4
        out.middle_samples = sum(1 for v in middle
                                 if black + span // 4 <= v <= top)
        out.high_samples = sum(1 for v in ordered if v > top)
        return out



# --- payload ----------------------------------------------------------------
# A payload is a list of byte strings, joined by a black slot. No envelope: the
# separator is a symbol payload data can never contain, so it costs one slot per
# boundary instead of a length prefix's eight.
#
# The protocol attaches no meaning to the fields. It does not know how many
# there should be, how long they may be, or whether they are text. That is the
# application's business -- see valid_credential() in __init__.py.

SEPARATOR = -1   # token standing in for the Field Separator


def build_tokens(*fields):
    tokens = []
    for i, field in enumerate(fields):
        if i:
            tokens.append(SEPARATOR)
        tokens.extend(field)
    return tokens


# Blocks are transport only: up to BLOCK_BYTES byte tokens each. The separator
# rides along and does not count toward the limit.
def split_blocks(tokens):
    blocks = []
    current = []
    count = 0
    for token in tokens:
        if token == SEPARATOR:
            current.append(token)
            continue
        if count == BLOCK_BYTES:
            blocks.append(current)
            current = []
            count = 0
        current.append(token)
        count += 1
    if current:
        blocks.append(current)
    return blocks


# The separator contributes a 0x00 to the CRC at its position but is never
# transmitted as a byte. Without it a dropped separator passes CRC and only
# surfaces later as a decode failure, reporting the wrong error.
def block_crc(tokens):
    return crc16(bytes(0 if t == SEPARATOR else t for t in tokens))


def block_symbols(tokens):
    out = []
    for token in tokens:
        if token == SEPARATOR:
            out.append(LOW)
        else:
            out.extend(bits_symbols(token, 8))  # most significant bit first
    return out


def bits_symbols(value, width):
    return [HIGH if (value >> shift) & 1 else MID
            for shift in range(width - 1, -1, -1)]


def symbols_bits(symbols):
    value = 0
    for symbol in symbols:
        value = (value << 1) | (1 if symbol == HIGH else 0)
    return value



# --- slot clock -------------------------------------------------------------
# Slots are measured against time.ticks_ms(), never badge.ticks: badge.ticks is
# a per-frame snapshot that cannot resolve anything shorter than a frame, and a
# slot is only a few frames long.

class SlotClock:
    def __init__(self):
        self.epoch = 0
        self.duration = SLOT_LADDER[0]
        self.index = 0

    def start(self, now, ladder_index):
        self.epoch = now
        self.set_index(ladder_index)

    def slow_down(self):
        # Index 0 is the slowest rung, so stepping slower means stepping down.
        self.set_index(self.index - 1)

    def set_index(self, index):
        self.index = max(0, min(len(SLOT_LADDER) - 1, index))
        self.duration = SLOT_LADDER[self.index]

    def number(self, now):
        return time.ticks_diff(now, self.epoch) // self.duration

    def offset(self, now):
        return time.ticks_diff(now, self.epoch) % self.duration


# Collects samples for the slot in progress and decides it from the back of the
# slot, once the peer's panel has settled and the pipeline has caught up.
#
# Each sample goes into a preallocated buffer kept sorted as it fills, so the
# median is an index and a continuous receiver -- dozens of samples a slot --
# allocates nothing per sample. A slot holds at most SLOT_SAMPLES; past that
# its samples stop counting.
SLOT_SAMPLES = 128


def _insert_sorted(buf, count, value):
    """Insert `value` into the first `count` of `buf`, kept ascending."""
    i = count
    while i > 0 and buf[i - 1] > value:
        buf[i] = buf[i - 1]
        i -= 1
    buf[i] = value


class SlotReceiver:
    def __init__(self, clock, levels, frame_ms=0, guard_percent=0,
                 start_percent=50):
        self.clock = clock
        self.levels = levels
        # The peer's transmit granularity: a frame for one that presents
        # every symbol, a few ms for a continuous one on the backlight. Sizes
        # the end-of-slot guard, which is also at least `guard_percent` of
        # the slot.
        self.frame_ms = frame_ms
        self.guard_percent = guard_percent
        # Where the window opens: half way, unless told later.
        self.start_percent = start_percent
        self.current = -1
        self.settled = [0] * SLOT_SAMPLES
        self.all = [0] * SLOT_SAMPLES
        self.n_settled = 0
        self.n_all = 0

    def clear(self):
        self.n_settled = 0
        self.n_all = 0

    def decide(self):
        # Prefer settled samples, but never drop a slot for want of one. At the
        # fastest rungs a slot is barely three frames long and the window can
        # catch none of them; dropping the symbol desynchronises every symbol
        # after it, which is far worse than deciding from an early sample.
        if self.n_settled:
            return self.levels.classify(self.settled[self.n_settled // 2])
        if self.n_all:
            return self.levels.classify(self.all[self.n_all // 2])
        return self.levels.last     # keep the stream aligned, let CRC judge

    def feed(self, now, value):
        number = self.clock.number(now)
        decided = None
        if number != self.current:
            if self.current >= 0:
                decided = self.decide()
            self.current = number
            self.clear()

        if self.n_all < SLOT_SAMPLES:
            _insert_sorted(self.all, self.n_all, value)
            self.n_all += 1

        # The second half, not the middle: everything that delays a symbol
        # happens at the start of a slot, so the back half is the only part
        # guaranteed to hold this symbol rather than the last one.
        #
        # And not the very end of it, which can hold the NEXT symbol: see
        # END_GUARD_PERCENT. At 50ms with 11ms frames the back half holds two
        # or three samples, the median of two is the higher, and one sample of
        # a brighter next symbol was enough to flip a bit. The guard never
        # leaves the window shorter than a frame, or it could catch nothing.
        offset = self.clock.offset(now)
        duration = self.clock.duration
        guard = min(self.frame_ms * END_GUARD_PERCENT // 100,
                    duration // 2 - self.frame_ms * 6 // 5)
        guard = max(guard, duration * self.guard_percent // 100)
        if (duration * self.start_percent // 100 <= offset
                < duration - max(0, guard)
                and self.n_settled < SLOT_SAMPLES):
            _insert_sorted(self.settled, self.n_settled, value)
            self.n_settled += 1
        return decided



# --- transfer engine --------------------------------------------------------
# One per-frame step function per phase. step() is called once a frame with the
# current time and the latest light sample; it returns the field to display and
# stores an exit value when it is finished.

PHASE_ANNOUNCE = 0
PHASE_PRESENT = 1
PHASE_CAL_SEND = 2
PHASE_CAL_RECV = 3
PHASE_CAL_GAP = 4
PHASE_DATA = 5
PHASE_DONE = 6


def _default_frame_ms():
    # Only used when a caller does not supply one, as the tests do not. A real
    # app passes `lambda: badge.ticks_delta` so the rate adapts to the badge.
    return 16


def rung_for(sample_ms, settle_ms):
    """The fastest rung a receiver sampling every `sample_ms` can meet against
    a `settle_ms` settle: at least 2.5 samples a slot, and SETTLE_SLOTS
    settle times."""
    best = 0
    for i, duration in enumerate(SLOT_LADDER):
        if duration >= sample_ms * 2.5 and duration >= settle_ms * SETTLE_SLOTS:
            best = i
    return best


def rung_for_frame(frame):
    """The fastest rung a badge sampling once per `frame` ms frame can meet,
    on pixels."""
    return rung_for(frame, LCD_SETTLE_MS)


# The fastest rung pixels allow -- the panel's limit -- the rung a SLOW
# badge's class stands for, and the fastest of all: a continuous badge on the
# backlight scheme.
FAST_INDEX = rung_for_frame(1)
SLOW_INDEX = SLOT_LADDER.index(SLOW_RUNG_MS)
BACKLIGHT_INDEX = rung_for(FINE_MS, BACKLIGHT_SETTLE_MS)
BACKLIGHT_LIT_INDEX = rung_for(FINE_MS, BACKLIGHT_LIT_SETTLE_MS)


class Transfer:
    # `schemes` are the light-level schemes this badge may transmit with, from
    # LEVEL_SCHEMES. SCHEME_FIELDS is always among them: it is what beacons use
    # and what every badge can fall back to. Adding SCHEME_BACKLIGHT lets
    # calibration try both and keep whichever separates the levels better.
    # `continuous`: this badge calls step() for every reading rather than once
    # a frame -- the app's listen loop. See FINE_MS. Both badges must agree:
    # it decides which rung they start on.
    def __init__(self, leader, payload=None, frame_ms=_default_frame_ms,
                 schemes=(SCHEME_FIELDS,), continuous=False):
        self.leader = leader
        self.payload = payload
        self.frame_ms = frame_ms
        self.continuous = continuous
        # Readings pooled since each stream last gave a sample: count, sum,
        # and the sum of their times after `*_base`. The sample is the mean
        # value at the mean time. Scalars: this runs for every reading.
        self.fine_n = self.fine_sum = self.fine_tsum = 0
        self.fine_base = None
        self.fine_t = self.fine_v = 0
        self.coarse_n = self.coarse_sum = self.coarse_tsum = 0
        self.coarse_base = None
        self.coarse_t = self.coarse_v = 0
        # The handshake's gap check: where the last fine sample ended, and
        # whether the coarse sample in progress follows a drop.
        self.sample_end_us = None
        self.coarse_dropped = False
        # The last FLICKER_BUFFER raw fine samples before the data phase, for
        # the flicker's fit: time and span in us, value. A ring.
        self.fit_t = [0] * FLICKER_BUFFER
        self.fit_v = [0] * FLICKER_BUFFER
        self.fit_s = [0] * FLICKER_BUFFER
        self.fit_ms = [0] * FLICKER_BUFFER
        self.fit_n = 0
        self.fit_result = None      # what fit() found, once it has run
        self.fit_tried = False
        self.track_points = 0
        self.field = LOW        # what the pre-data phases last decided
        self.flicker = FlickerFilter()
        # A continuous receiver's phase lock waits for a second lit sample
        # before believing an edge: one noisy reading is not a transmission.
        self.lit_time = 0
        self.lit_light = 0
        self.lit_pending = False
        # Breaking in: the Follower's state while it holds high, the Leader's
        # watch for one while it transmits, and the verdict a resend repeats.
        self.breaking = False
        self.break_since = 0
        self.break_dark_since = None
        self.high_since = None
        self.verdict_lit = 0
        self.last_verdict = None
        self.breaks = 0         # breaks made (Follower) or heard (Leader)
        self.offers_backlight = SCHEME_BACKLIGHT in schemes
        # The scheme the field returned by step() is to be shown in. Read it
        # every frame: calibration changes it.
        self.scheme = SCHEME_FIELDS
        self.measured = {}      # Follower: scheme -> Levels, one per staircase
        # The frame-rate class: this badge's, fixed when its staircase starts,
        # and the peer's, read from the peer's staircase. None until known.
        self.slow = None
        self.peer_slow = None
        # LIT: a continuous badge in a lit room, whose sensor is fast enough
        # for the backlight's shortest rungs. This badge's, from the black it
        # reads before its staircase, and the peer's, from the peer's
        # staircase. False until known.
        self.lit = False
        self.peer_lit = False
        self.frames = [0] * FRAME_SAMPLES   # recent frame times, a ring
        self.frame_count = 0
        self.cal_index = 0      # which of the Leader's staircases is current
        self.more_coming = False
        self.result = None
        self.progress = ""
        self.detector = BeaconDetector()
        self.clock = SlotClock()
        self.levels = None
        self.receiver = None
        self.started = 0
        self.phase_started = 0
        self.blocks = []
        self.tx = []            # symbols still to transmit
        self.rx = []            # symbols decoded this phase
        self.reader = StaircaseReader()
        self.pending_tokens = []
        self.expected_crc = 0
        self.saw_announce = False
        self.finishing = False
        self.epoch = 0
        self.now = 0
        # The phase lock's last sample. Scalars, so the per-frame path
        # allocates nothing.
        self.prev_time = 0
        self.prev_light = 0
        self.tx_slot = 0
        self.clear_data(0)
        self.phase = PHASE_ANNOUNCE if leader else PHASE_PRESENT
        # No bytes at all is still one block, an empty one: it is what tells
        # the Follower the payload is a single empty field, and a Leader with
        # no block to send would have nothing to wait for an answer to.
        if leader:
            self.blocks = split_blocks(build_tokens(*(payload or ()))) or [[]]

    # -- helpers

    def enter(self, phase, now):
        self.phase = phase
        self.phase_started = now
        self.rx = []
        self.tx = []
        self.detector.reset()
        if phase == PHASE_CAL_RECV:
            self.reader = StaircaseReader()
        # Points collected toward a flicker update belong to the phase they
        # were read in: the peer may not be dark in the next.
        self.flicker.close(False)
        self.track_points = 0

    def fail(self, code):
        self.result = code
        self.phase = PHASE_DONE

    def elapsed(self, now):
        return time.ticks_diff(now, self.phase_started)

    def begin_slots(self, now, ladder_index):
        self.clock.start(now, ladder_index)
        backlight = self.continuous and self.scheme == SCHEME_BACKLIGHT
        pixels = self.continuous and not backlight
        self.receiver = SlotReceiver(
            self.clock, self.levels, self.tx_granularity(),
            BACKLIGHT_GUARD_PERCENT if backlight else 0,
            PIXEL_WINDOW_PERCENT if pixels else 50)

    # The transfer got somewhere. Restarts the stall budget -- see step_data.
    def advance(self):
        self.stall_slots = 0

    # Queue symbols, starting on the next slot boundary.
    def send(self, symbols):
        self.tx = list(symbols)
        self.tx_slot = self.clock.number(self.now) + 1
        self.advance()          # having something to say is progress

    # The field to display while transmitting, or None once the queue is sent.
    # Indexed by slot number, never by call count: this runs once a frame and a
    # slot is several frames long, so popping per call fires the whole queue
    # inside one slot.
    def tx_field(self, now):
        if not self.tx:
            return None
        index = self.clock.number(now) - self.tx_slot
        if index < 0:
            return LOW              # holding black until the boundary
        if index >= len(self.tx):
            self.tx = []
            return None
        return self.tx[index]

    # -- the entry point: once a frame, or once a reading

    def step(self, now, light):
        """The field to show now. Call once a frame with that frame's reading,
        or -- `continuous` -- for every reading: they are pooled into FINE_MS
        and COARSE_MS samples, and only a finished sample moves anything but
        the transmitted symbol. The app does not call this: it pools its own
        readings and calls sample(), the only way in that takes the room's
        mains flicker off."""
        self.now = now
        fine, coarse = self.pool(now, light)
        return self.decide(now, fine, coarse)

    def sample(self, now, t, value, t_us=None, span_us=0):
        """The field to show now, for a continuous badge that pools its own
        readings: `value` is their mean, `t` their mean time in ms, and
        `t_us` and `span_us` the same in microseconds, and how long they
        were read for. Each call is one fine sample, FINE_MS or so long.

        This is the app's way in. On the badge each Python operation costs
        microseconds, and a protocol call per reading cost about a ms
        [measured-v2, 2026-10-06b], more than the readings between calls:
        the app reads in a tight loop of its own and calls this once a
        sample. See "What a reading costs" in the protocol."""
        self.now = now
        self.fine_t = t
        self.fine_v = value
        if t_us is not None:
            if self.phase == PHASE_PRESENT:
                self.drop_after_gap(t_us, span_us)
            self.filter_sample(t_us, span_us)
        # Corrected, so calibration's samples are too, once the flicker has
        # been fitted.
        coarse = self.pool_coarse(t, self.fine_v)
        if coarse:
            self.coarse_dropped = False
        return self.decide(now, True, coarse)

    def drop_after_gap(self, t_us, span_us):
        """A Follower in the handshake: drops the coarse sample in progress
        if this fine sample -- `span_us` of readings centred on `t_us` --
        started more than HANDSHAKE_GAP_MS after the last one ended, unless
        the one in progress already follows a drop."""
        half = span_us // 2
        end = self.sample_end_us
        self.sample_end_us = time.ticks_add(t_us, span_us - half)
        if end is None or self.coarse_dropped:
            return
        start = time.ticks_add(t_us, -half)
        if time.ticks_diff(start, end) > HANDSHAKE_GAP_MS * 1000:
            self.coarse_n = self.coarse_sum = self.coarse_tsum = 0
            self.coarse_base = None
            self.coarse_dropped = True

    def deadline(self):
        """The ms tick by which a continuous caller must hand in its next
        sample, or None for whenever: while it transmits in the data phase,
        the next slot boundary, where its symbol changes. Elsewhere symbols
        last tens of ms or more, and a sample late is nothing to them."""
        if self.phase != PHASE_DATA or not self.tx:
            return None
        clock = self.clock
        return time.ticks_add(
            clock.epoch, (clock.number(self.now) + 1) * clock.duration)

    def decide(self, now, fine, coarse):
        if self.phase == PHASE_DATA:
            return self.step_data(now, fine)
        if not coarse:
            return self.field
        t = self.coarse_t
        value = self.coarse_v
        if self.slow is None:
            self.note_frame()
        if self.phase == PHASE_ANNOUNCE:
            self.field = self.step_announce(t, value)
        elif self.phase == PHASE_PRESENT:
            self.field = self.step_present(t, value)
        elif self.phase == PHASE_CAL_SEND:
            self.field = self.step_cal_send(t, value)
        elif self.phase == PHASE_CAL_RECV:
            self.field = self.step_cal_recv(t, value)
        elif self.phase == PHASE_CAL_GAP:
            self.field = self.step_cal_gap(t, value)
        else:
            self.field = LOW
        if self.phase == PHASE_DATA:
            # Calibration has just handed over: the data phase decides from
            # here, starting with this very call.
            return self.step_data(now, fine)
        return self.field

    def pool(self, now, light):
        """Adds a reading to both streams. Returns (fine, coarse): whether
        each has a finished sample, now in fine_t/fine_v, coarse_t/coarse_v.

        A stream finishes a sample on the first reading at least its interval
        after the last one it finished -- so a caller stepping once a frame
        gets one per call, holding just that reading at just that time."""
        fine = False
        if self.fine_base is None:
            self.fine_base = now
        dt = time.ticks_diff(now, self.fine_base)
        self.fine_n += 1
        self.fine_sum += light
        self.fine_tsum += dt
        if dt >= FINE_MS or self.fine_n == 1 and not self.continuous:
            n = self.fine_n
            self.fine_t = time.ticks_add(self.fine_base, self.fine_tsum // n)
            self.fine_v = self.fine_sum // n
            self.fine_base = now
            self.fine_n = self.fine_sum = self.fine_tsum = 0
            fine = True
        return fine, self.pool_coarse(now, light)

    def pool_coarse(self, t, value):
        """Adds a reading, or a fine sample, to the coarse stream. Returns
        whether it finished a sample, now in coarse_t/coarse_v."""
        if self.coarse_base is None:
            self.coarse_base = t
        dt = time.ticks_diff(t, self.coarse_base)
        self.coarse_n += 1
        self.coarse_sum += value
        self.coarse_tsum += dt
        if dt >= COARSE_MS or self.coarse_n == 1 and not self.continuous:
            n = self.coarse_n
            self.coarse_t = time.ticks_add(self.coarse_base,
                                           self.coarse_tsum // n)
            self.coarse_v = self.coarse_sum // n
            self.coarse_base = t
            self.coarse_n = self.coarse_sum = self.coarse_tsum = 0
            return True
        return False

    def filter_sample(self, t_us, span_us):
        """A continuous badge: the flicker, for the fine sample just
        finished, a mean over `span_us` centred on `t_us`. Before the data
        phase the sample is kept for the fit, and once the fit has a table
        it is corrected, and tracked while the peer is known dark. In the
        data phase it is learnt from -- or tracked -- in the back half of
        its slot, and corrected. Once a sample, never once a reading."""
        if not self.continuous:
            return
        raw = self.fine_v
        flicker = self.flicker
        if self.phase != PHASE_DATA:
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
            return
        if self.clock.offset(self.fine_t) >= self.clock.duration // 2:
            flicker.collect(t_us, raw, span_us)
        self.fine_v = flicker.correct(t_us, raw, span_us)

    def peer_dark(self):
        """Whether the peer is known to be dark before the data phase: it
        is listening while this badge sends its staircase -- the Leader's
        from FLICKER_FIT_LEAD_MS past the opening gap, by when the Follower
        has seen it and gone dark -- and both are in the shared gap."""
        if self.phase == PHASE_CAL_GAP:
            return True
        if self.phase != PHASE_CAL_SEND:
            return False
        return (not self.leader or
                self.elapsed(self.now) >= CAL_GAP_MS + FLICKER_FIT_LEAD_MS)

    def fit_flicker(self, window_ms=FLICKER_FIT_MS):
        """Fits the flicker, once, from the last FLICKER_FIT_MS of kept
        samples -- taken while the peer was dark. See FLICKER_FIT_MS. A
        window the fit refuses is tried again at half the length: the
        Follower's quiet is timed from the Leader's last edge, and can start
        in a steady half of its beacon."""
        self.fit_tried = True
        self.fit_result = self.fit_window(window_ms)
        if self.fit_result is None:
            self.fit_result = self.fit_window(window_ms // 2)
        self.track_points = 0

    def replay_staircase(self):
        """A fresh staircase reader, fed the kept samples since this phase
        began, corrected, through a fresh coarse stream."""
        self.reader = StaircaseReader()
        self.coarse_n = self.coarse_sum = self.coarse_tsum = 0
        self.coarse_base = None
        count = min(self.fit_n, FLICKER_BUFFER)
        first = self.fit_n - count
        for n in range(first, self.fit_n):
            i = n % FLICKER_BUFFER
            if time.ticks_diff(self.fit_ms[i], self.phase_started) < 0:
                continue
            value = self.flicker.correct(self.fit_t[i], self.fit_v[i],
                                         self.fit_s[i])
            if self.pool_coarse(self.fit_ms[i], value):
                self.reader.feed(self.coarse_t, self.coarse_v)

    def fit_window(self, window_ms):
        count = min(self.fit_n, FLICKER_BUFFER)
        if not count:
            return None
        newest = self.fit_n - 1
        last = self.fit_t[newest % FLICKER_BUFFER]
        back = 0
        while back < count and time.ticks_diff(
                last, self.fit_t[(newest - back) % FLICKER_BUFFER]) \
                <= window_ms * 1000:
            back += 1
        ts = []
        vs = []
        spans = []
        for n in range(self.fit_n - back, self.fit_n):
            i = n % FLICKER_BUFFER
            ts.append(self.fit_t[i])
            vs.append(self.fit_v[i])
            spans.append(self.fit_s[i])
        return self.flicker.fit(ts, vs, spans)

    # -- phase: announce (Leader beacons, waits for Peer Present)

    def step_announce(self, now, light):
        self.progress = "Looking for a badge"
        self.detector.push(now, light)
        if self.detector.matches(PRESENT_HALF_MS):
            # The Follower's beacon alternates black and high: its black is
            # the room's light here, the only measure of it a Leader has
            # before it must send its staircase.
            self.lit = self.is_lit(self.detector.low())
            self.enter(PHASE_CAL_SEND, now)
            return LOW
        if time.ticks_diff(now, self.started) > ANNOUNCE_TIMEOUT_MS:
            self.fail(ERR_NO_PEER)
            return LOW
        # Alternating middle and high, the beacon the Follower is looking for.
        return beacon_field(self.elapsed(now), ANNOUNCE_HALF_MS, MID)

    # -- phase: peer present (Follower beacons, waits for the staircase)

    def step_present(self, now, light):
        self.detector.push(now, light)
        if time.ticks_diff(now, self.started) > ANNOUNCE_TIMEOUT_MS:
            self.fail(ERR_NO_PEER)
            return LOW

        # Until the Leader has been seen, hold black and say so on screen. Only
        # then start beaconing. Text is drawn over this field, which is exactly
        # why it must stop once we are transmitting something the Leader
        # measures: text changes the average brightness of the screen.
        if not self.saw_announce:
            self.progress = "Looking for a badge"
            if self.detector.matches(ANNOUNCE_HALF_MS):
                self.saw_announce = True
                self.phase_started = now
            return LOW

        self.progress = "Found a badge"

        # Quiet, not "the beacon disappeared": a staircase alternating every
        # PLATEAU_MS looks like another beacon to a detector that only knows
        # about periodic swings. The opening CAL_GAP_MS of black is longer than
        # any beacon half period, so silence is the unambiguous cue.
        if self.detector.quiet_ms(now) > ANNOUNCE_HALF_MS + 30:
            self.enter(PHASE_CAL_RECV, now)
            return LOW
        return beacon_field(self.elapsed(now), PRESENT_HALF_MS, LOW)

    # -- phase: transmit the calibration staircase

    # The Leader sends one staircase in SCHEME_FIELDS, or -- when it may use
    # the backlight -- that one REVERSED, as notice that a second follows, and
    # then the same staircase in SCHEME_BACKLIGHT. The Follower sends one, in
    # the scheme it chose, and its order says which.
    def step_cal_send(self, now, light):     # noqa: ARG002 - every step_* takes (now, light)
        self.progress = "Calibrating"
        if self.slow is None:
            # Fixed for the rest of the transfer, before the first step of
            # the staircase that carries it.
            self.slow = self.frame_class_slow()
        reps = self.cal_reps()
        plateau = self.plateau()
        length = staircase_ms(reps, plateau)
        elapsed = self.elapsed(now)
        if (self.leader and self.continuous and not self.fit_tried and
                elapsed >= CAL_GAP_MS + FLICKER_FIT_LEAD_MS + FLICKER_FIT_MS):
            # The Follower has been dark, receiving, since the opening gap.
            self.fit_flicker()
        if self.leader:
            count = 2 if self.offers_backlight else 1
            if elapsed >= length * count:
                self.scheme = SCHEME_FIELDS     # until the Follower says
                self.enter(PHASE_CAL_RECV, now)
                return LOW
            if elapsed < length:
                self.scheme = SCHEME_FIELDS
                order = REVERSED if count == 2 else ASCENDING
                return staircase_field(elapsed, order, reps, plateau)
            self.scheme = SCHEME_BACKLIGHT
            return staircase_field(elapsed - length, ASCENDING, reps, plateau)

        if elapsed >= length:
            # The Follower transmitted second, so it already knows exactly
            # when the body ended. That instant is the shared reference.
            self.epoch = time.ticks_add(self.phase_started, length)
            self.enter(PHASE_CAL_GAP, now)
            return LOW
        order = REVERSED if self.scheme == SCHEME_BACKLIGHT else ASCENDING
        return staircase_field(elapsed, order, reps, plateau)

    # -- phase: measure the peer's staircase

    def step_cal_recv(self, now, light):
        self.progress = "Calibrating"
        elapsed = self.elapsed(now)
        # The Follower, reading the Leader's second staircase.
        second = not self.leader and self.cal_index == 1
        if (not self.leader and self.continuous and not self.fit_tried
                and elapsed >= FLICKER_FOLLOWER_FIT_MS):
            # Inside the Leader's dark: see FLICKER_FOLLOWER_FIT_MS.
            self.fit_flicker(FLICKER_FOLLOWER_FIT_MS)
            if self.flicker.harmonics is not None:
                # What has been read of the staircase so far was not
                # corrected: read it again, from the kept samples, as if the
                # flicker had been known from the start. Uncorrected, 50 or
                # 60 Hz flicker in its black failed calibration in 15 of 144.
                self.replay_staircase()
                return LOW
        ended = self.reader.feed(now, light)
        if second:
            peer_ms = staircase_ms(self.peer_reps(), self.plateau())
            if ended and elapsed < peer_ms - CAL_GAP_MS:
                # The Leader's second staircase cannot have ended yet: what
                # read as its closing gap was its opening one, flickering -- a
                # sensor that follows the PWM sees the dim low do that. Half
                # duplex: keep listening rather than answer over it.
                self.reader = StaircaseReader()
                ended = False
        if ended:
            levels = self.reader.levels()
            if self.leader:
                if levels is None:
                    self.fail(ERR_CONTRAST)
                    return LOW
                self.levels = levels
                order = self.read_peer_class(levels)
                # The Follower's order is its choice -- but only a Leader that
                # offered the backlight can be told to use it.
                if self.offers_backlight and order == REVERSED:
                    self.scheme = SCHEME_BACKLIGHT
                # Both badges are calibrated now. The Follower's closing gap is
                # the reference; the clock starts one gap after it began.
                self.epoch = time.ticks_add(self.reader.ended_at, CAL_GAP_MS)
                self.enter(PHASE_CAL_GAP, now)
                return LOW
            return self.follower_measured(now, levels)

        # The Leader's second staircase is as long as its first -- its class
        # was read from that -- and began within a gap of this reader
        # starting. Past both it is over whether or not it could be measured,
        # and the first one is still an answer.
        if second and elapsed > peer_ms + CAL_GAP_MS:
            return self.follower_measured(now, None)

        if elapsed > staircase_ms(CAL_REPS_SLOW) * 3:
            self.fail(ERR_CONTRAST)
        return LOW

    # One of the Leader's staircases has ended. The first must calibrate --
    # without levels its order cannot be read, so whether a second follows is
    # unknown -- and a second that fails simply is not chosen.
    def follower_measured(self, now, levels):
        if self.cal_index == 0:
            if levels is None:
                self.fail(ERR_CONTRAST)
                return LOW
            self.measured[SCHEME_FIELDS] = levels
            self.more_coming = self.read_peer_class(levels) == REVERSED
            # This badge's own light, from the black of the staircase it just
            # measured, fixed before its own staircase carries it.
            self.lit = self.is_lit(levels.black)
        elif levels is not None:
            self.measured[SCHEME_BACKLIGHT] = levels

        if self.more_coming and self.cal_index == 0:
            # Half duplex: wait the second one out even if this badge will not
            # use it, because the Leader is still transmitting.
            self.cal_index = 1
            self.enter(PHASE_CAL_RECV, now)
            return LOW

        self.scheme = self.choose_scheme()
        self.levels = self.measured[self.scheme]
        self.enter(PHASE_CAL_SEND, now)
        return LOW

    def read_peer_class(self, levels):
        """The peer's class, SLOW and LIT, from the staircase just read, and
        the order it stepped in: one pass over its samples, not three."""
        rising, falling, highs = staircase_steps(self.reader.values, levels)
        self.peer_slow = highs >= SLOW_STEPS_MIN
        self.peer_lit = highs <= LIT_STEPS_MAX
        return REVERSED if falling > rising else ASCENDING

    # Whichever separates adjacent levels by more, among those this badge may
    # use. Ties go to SCHEME_FIELDS, the proven one. A choice made from one
    # direction is applied to both: the badges are the same hardware, which the
    # rate selection already relies on.
    #
    # A continuous badge takes the backlight whenever it calibrated at all:
    # there its slots can be three times shorter (BACKLIGHT_SETTLE_MS against
    # LCD_SETTLE_MS and the present), and calibration has already held each
    # gap to the noise. separation() is 0 when the middle step did not hold,
    # as on a sensor that follows the PWM, so that one still falls back.
    def choose_scheme(self):
        backlight = self.measured.get(SCHEME_BACKLIGHT)
        if not self.offers_backlight or backlight is None:
            return SCHEME_FIELDS
        rival = 0 if self.continuous else separation(
            self.measured[SCHEME_FIELDS])
        return SCHEME_BACKLIGHT if separation(backlight) > rival \
            else SCHEME_FIELDS

    # -- phase: wait out the shared gap, then start the slot clock

    def step_cal_gap(self, now, light):     # noqa: ARG002 - every step_* takes (now, light)
        self.progress = "Syncing"
        if time.ticks_diff(self.epoch, now) > 0:
            return LOW
        self.start_data(self.epoch, self.start_index())
        return LOW

    # -- the frame-rate class

    def note_frame(self):
        """Keep this frame's time, until the class is fixed. No allocation."""
        self.frames[self.frame_count % FRAME_SAMPLES] = self.frame_ms()
        self.frame_count += 1

    def frame_class_slow(self):
        """True if the median of the recent frame times cannot sample the
        fastest rung the panel allows. One sort, once, at calibration. A
        continuous badge samples between frames, so its frames do not bound
        it: always FAST."""
        if self.continuous:
            return False
        count = min(self.frame_count, FRAME_SAMPLES)
        frame = sorted(self.frames[:count])[count // 2] if count \
            else self.frame_ms()
        return rung_for_frame(max(1, frame)) < FAST_INDEX

    def is_lit(self, black):
        """Whether a continuous badge reading `black` is in a lit room."""
        return (self.continuous and black is not None
                and black >= LIT_BLACK_COUNTS)

    def plateau(self):
        """This badge's staircase step, and so its peer's: see PLATEAU_MS."""
        return PLATEAU_CONTINUOUS_MS if self.continuous else PLATEAU_MS

    def cal_reps(self):
        if self.slow:
            return CAL_REPS_SLOW
        return CAL_REPS_LIT if self.lit else CAL_REPS

    def peer_reps(self):
        if self.peer_slow:
            return CAL_REPS_SLOW
        return CAL_REPS_LIT if self.peer_lit else CAL_REPS

    def start_index(self):
        """The rung both badges start on: the slower of the two classes'. Each
        computes it from the same two flags and the scheme both settled on, so
        they agree without a message. A class not yet known counts as FAST, as
        a badge that sent CAL_REPS before this existed would read. A FAST
        class stands for the fastest rung the scheme allows this badge's
        sampling: continuous on the backlight, BACKLIGHT_INDEX."""
        fast = self.fastest_index(1)
        if (self.continuous and self.scheme == SCHEME_BACKLIGHT
                and self.lit and self.peer_lit):
            fast = BACKLIGHT_LIT_INDEX
        index = min(SLOW_INDEX if self.slow else fast,
                    SLOW_INDEX if self.peer_slow else fast)
        return min(len(SLOT_LADDER) - 1, index + OPTIMISTIC_RUNGS)

    def settle_ms(self):
        """The settle a slot must allow for: the sensor's, for a continuous
        badge on the backlight, and the panel's otherwise."""
        if self.continuous and self.scheme == SCHEME_BACKLIGHT:
            return BACKLIGHT_SETTLE_MS
        return LCD_SETTLE_MS

    def fastest_index(self, frame):
        """The fastest rung this badge can receive at with `frame` ms frames:
        one a frame, or FINE_MS samples if continuous."""
        return rung_for(FINE_MS if self.continuous else frame, self.settle_ms())

    # The rung this badge's live frame time can meet. Not where a transfer
    # starts -- start_index() is, from both badges' classes -- but the proven
    # floor the CRC route measures against: frame times are the badge's own,
    # and v2 and v3 firmware, or a slow frame, give two badges different ones.
    # A continuous badge on the backlight starts faster than it has proven --
    # see BACKLIGHT_PROVEN_MS -- so bad CRCs step it down towards that.
    def conservative_index(self):
        index = self.fastest_index(max(1, self.frame_ms()))
        if self.continuous and self.scheme == SCHEME_BACKLIGHT:
            index = min(index, SLOT_LADDER.index(BACKLIGHT_PROVEN_MS))
        return index

    # There is no rate-proving phase: both badges start on the rung their
    # staircases agreed, and a test burst cannot be trusted anyway -- a rate
    # too fast for a payload still carries 48 symbols of alternation. The first block is the test. A Follower that cannot frame
    # one sends the Data Sync Error Pattern and steps down; the Leader follows.
    # Both change slot duration and leave the shared epoch alone, so the grids
    # stay aligned.

    # -- phase: data
    # Half duplex. Exactly one badge transmits at a time and the other holds
    # black, which also gives the transmitter a continuous black reference.

    STEP_BLOCK = 0      # Leader sends a block / Follower collects one
    STEP_CRC = 1        # Follower sends 16 raw CRC bits / Leader listens
    STEP_VERDICT = 2    # Leader sends CRC Good or Bad / Follower listens
    STEP_FINISH = 3     # Complete sent, and being acknowledged

    def start_data(self, now, index=None):
        self.enter(PHASE_DATA, now)
        self.begin_slots(now, self.clock.index if index is None else index)
        self.clear_data(now)
        self.progress = "Sending" if self.leader else "Receiving"
        gc.collect()        # deliberately, while nothing is time critical
        if self.leader:
            self.send_block()

    # The data phase's state, as it starts.
    def clear_data(self, now):
        self.block_index = self.retries = self.blocks_accepted = 0
        self.idle_slots = self.complete_tries = self.wait_slots = 0
        self.stall_slots = self.attempts = self.resend_requests = 0
        self.sub = self.STEP_BLOCK
        self.received = []
        # A framed block awaiting its verdict. Not `pending_tokens`, which is
        # empty for an empty block as well as for no block at all.
        self.pending_block = False
        self.crc_started = self.pending_slow_down = self.want_resend = False
        # Phase-lock tracking. Scalars, deliberately: this is touched every
        # frame and nothing in the per-frame path may allocate.
        self.dark_since = None
        self.have_prev = False
        self.block_started = now
        self.reset_block()

    def send_block(self, extra_lead_in=0):
        # Sending it again cannot be the answer for ever.
        if self.attempts >= MAX_BLOCK_ATTEMPTS:
            self.fail(ERR_CRC)
            return
        # Built here, not per frame: the text only changes once a block.
        self.attempts += 1
        self.progress = "Sending %d/%d" % (self.block_index + 1, len(self.blocks))
        tokens = self.blocks[self.block_index]
        self.sub = self.STEP_BLOCK
        # Black before the Begin Block Pattern: after a rate change the two
        # badges do not switch on the same slot, and without it the pattern goes
        # out while the peer is still sampling at the old rate.
        # Resent after a Data Sync Error, a continuous Leader says its last
        # verdict again first: the error may have been a Follower that missed
        # it, holding a block it cannot tell accepted from rejected. One that
        # holds none ignores it.
        verdict = []
        if extra_lead_in and self.continuous and self.last_verdict:
            verdict = list(self.last_verdict) + [LOW] * RESYNC_LEAD_IN
        self.send([LOW] * (RESYNC_LEAD_IN + extra_lead_in) + verdict
                  + list(BEGIN_BLOCK) + block_symbols(tokens) + list(END_BLOCK))
        self.expected_crc = block_crc(tokens)

    # -- phase locking
    # The badge that DETECTS the shared instant reads it late by the panel's
    # settling time plus a frame -- about half a slot at the fastest rung -- so
    # a receiver re-bases its grid onto the peer's real first edge out of black
    # at the start of every message. rebase() touches epoch and never duration;
    # slow_down() touches duration and never epoch.

    # Only while ACQUIRING -- waiting for a message to begin, never part way
    # through one. Re-basing mid-message would re-frame the rest of a pattern
    # against a grid the first half was not decoded on.
    def can_phase_lock(self):
        # STEP_FINISH is an acquiring state on both sides -- one is waiting for
        # an acknowledgement, the other for a repeat of Complete -- so the same
        # rule applies: re-base between messages, never inside one.
        if self.sub == self.STEP_FINISH:
            return True
        if self.leader:
            return self.sub == self.STEP_CRC and not self.crc_started
        # STEP_VERDICT counts: having sent its CRC the Follower is between
        # messages exactly as it is in STEP_BLOCK. Without it the grid locked at
        # the start of a block had to survive the whole block -- 500+ slots --
        # before the verdict arrived on it.
        return ((self.sub == self.STEP_BLOCK and not self.in_block)
                or self.sub == self.STEP_VERDICT)

    # How much black must precede an edge before it is taken for the start of a
    # transmission rather than a symbol inside one. No gap within a pattern is
    # that long. Not every message clears it: a block has its lead-in, but the
    # CRC reply and the Complete Ack follow with at most the one slot the peer
    # spent deciding, so they are read on the grid as it stands. Deliberately:
    # see "The Leader's grid is the reference" in the spec.
    def silence_slots(self):
        return (self.clock.duration * 3) // 2

    def phase_lock(self, now, light):
        lit = light >= self.levels.t_low

        if not lit:
            if self.lit_pending:
                # A lone lit sample in the black: noise, not an edge. The
                # silence it interrupted carries on.
                self.lit_pending = False
            if self.dark_since is None:
                self.dark_since = now
            self.prev_time = now
            self.prev_light = light
            self.have_prev = True
            return

        if self.continuous and self.dark_since is not None:
            # Samples are 2 ms apart and a reading's noise can reach T_low on
            # its own, so an edge is believed on its second lit sample and
            # timed from its first.
            if not self.lit_pending:
                self.lit_pending = True
                self.lit_time = now
                self.lit_light = light
                return
            self.lit_pending = False
            now = self.lit_time
            light = self.lit_light

        dark_since = self.dark_since
        self.dark_since = None
        if dark_since is None or not self.have_prev:
            return

        if time.ticks_diff(now, dark_since) < self.silence_slots():
            return
        if not self.can_phase_lock():
            return

        # Interpolate the crossing rather than rounding it to this frame: a
        # frame is a third of a slot at the fastest rung. T_low sits low on the
        # panel's rise, so the crossing tracks the true edge on a fast panel
        # and a slow one alike.
        span = light - self.prev_light
        elapsed = time.ticks_diff(now, self.prev_time)
        if span <= 0 or elapsed <= 0:
            boundary = now
        elif light >= self.levels.t_high:
            # Already off the top of the rise: a chord to this sample has the
            # slope of the sampling interval rather than of the panel, so
            # interpolating along it is a standing error. All the pair
            # establishes is that the edge fell between them.
            boundary = time.ticks_add(self.prev_time, elapsed // 2)
        else:
            offset = ((self.levels.t_low - self.prev_light) * elapsed) // span
            if offset < 0:
                offset = 0
            elif offset > elapsed:
                offset = elapsed
            boundary = time.ticks_add(self.prev_time, offset)

        self.rebase(time.ticks_add(boundary, -self.edge_lag()), now)
        self.have_prev = False

    # How late a measured edge is against the peer's actual slot boundary. Both
    # terms are biases rather than noise, so leaving them in parks the grid
    # permanently late. The peer can only change its panel on one of its own
    # frames, so a symbol goes up somewhere in the frame after its slot began:
    # mean half a frame, and the larger term. Then the panel climbs to T_low,
    # about a tenth of its settling time.
    #
    # A continuous transmitter changes symbol within a reading of the boundary
    # rather than on a frame, so for it the lag is what follows the decision:
    # the backlight's PWM and the sensor, or the wait for vertical blank and
    # the panel. See BACKLIGHT_EDGE_LAG_MS and PIXEL_EDGE_MS.
    def edge_lag(self):
        if self.continuous:
            if self.scheme == SCHEME_BACKLIGHT:
                return BACKLIGHT_EDGE_LAG_MS
            return max(1, self.frame_ms()) // 2 + PIXEL_EDGE_MS
        return max(1, self.frame_ms()) // 2 + LCD_SETTLE_MS // 10

    # How coarsely the peer places a symbol in time, for the end-of-slot
    # guard: a frame, unless it is continuous and on the backlight.
    def tx_granularity(self):
        if self.continuous and self.scheme == SCHEME_BACKLIGHT:
            return BACKLIGHT_TX_MS
        return max(1, self.frame_ms())

    def rebase(self, boundary, now):
        self.clock.epoch = boundary
        # Re-frame the slot in progress rather than deciding it. Its samples
        # were taken before the edge, so they belong to the silence and not to
        # the symbol now arriving -- and deciding it here would push a spurious
        # symbol into a pattern match that may already be part way through. The
        # Complete Pattern has no lead-in, so that is the case this protects.
        self.receiver.current = self.clock.number(now)
        self.receiver.clear()

    def step_data(self, now, fine):
        # Checked first and unconditionally, including part way through a
        # transmission. A backstop that only applies while listening is not a
        # backstop -- the case it exists for is the one whose shape we did not
        # predict.
        if time.ticks_diff(now, self.block_started) > BLOCK_TIMEOUT_MS:
            self.fail(ERR_LOST_PEER)
            return LOW

        if self.breaking:
            return self.step_break(now, fine)

        # Every call, so a continuous transmitter changes symbol within a
        # reading of the boundary.
        field = self.tx_field(now)
        if field is not None:
            if (fine and self.continuous and self.leader
                    and self.sub == self.STEP_BLOCK and self.heard_break()):
                self.stop_for_break()
                return LOW
            if self.rx:
                self.rx = []
            self.idle_slots = 0
            # Our own transmission is not the peer's silence. Restart the
            # silence clock when we stop talking, not before.
            self.dark_since = None
            self.have_prev = False
            self.lit_pending = False
            return field

        # Listening moves only on a finished sample.
        if not fine:
            return LOW
        now = self.fine_t
        light = self.fine_v

        # A rate change always takes effect AFTER the pattern announcing it has
        # been transmitted. Changing first would send the announcement at a rate
        # the peer is not listening at, so it would never arrive and the two
        # badges would end up on different rungs.
        if self.pending_slow_down:
            self.pending_slow_down = False
            self.clock.slow_down()

        # The Complete Pattern has finished going out. Wait to be told it
        # arrived rather than assuming it did.
        if self.finishing:
            self.finishing = False
            self.sub = self.STEP_FINISH
            self.wait_slots = 0

        # Before feeding the slot, not after: a rebase re-frames the slot this
        # sample belongs to.
        self.phase_lock(now, light)

        decided = self.receiver.feed(now, light)
        if decided is not None:
            self.flicker.close(decided == LOW)
            # Trim: while waiting for a pattern this list would otherwise grow
            # for the whole wait, and it is sliced and joined every slot.
            if len(self.rx) > MAX_PATTERN * 2:
                del self.rx[:-MAX_PATTERN]
            # Two budgets, because a link dies in two ways. This one is not
            # exempted in STEP_FINISH the way the silence budget is: the
            # closing handshake's own retry and linger counts are well inside
            # it, so it stands as a backstop if either of those is ever wrong.
            self.stall_slots += 1
            if self.stall_slots > STALL_SLOTS and not self.request_resend():
                self.fail(ERR_LOST_PEER)
                return LOW

            if decided == LOW:
                self.idle_slots += 1
                # The closing handshake keeps its own time. A Follower that is
                # slow to pick Complete out of the air is legitimately silent
                # for longer than LOST_PEER_SLOTS, and failing here would end
                # the transfer before the first retry had even gone out. The
                # retry budget is the timeout for this state.
                if (self.idle_slots > LOST_PEER_SLOTS
                        and self.sub != self.STEP_FINISH):
                    self.fail(ERR_LOST_PEER)
                    return LOW
            else:
                self.idle_slots = 0

            if self.want_resend and self.idle_slots >= RESEND_QUIET_SLOTS:
                self.want_resend = False
                self.resend_requests += 1
                # One signal for a framing failure and for a block never seen,
                # because they mean the same thing: this block did not arrive,
                # send it again slower.
                self.send_sync_error()
                return LOW

            self.rx.append(decided)
            if self.leader:
                self.leader_rx()
            else:
                self.follower_rx()
        return LOW

    # -- breaking in

    def heard_break(self):
        """Leader, transmitting: whether the Follower has held high for
        BREAK_DETECT_SLOTS. Its own screen's light is not in the reading --
        the sensor faces the Follower's -- and the Follower's screen is dark
        while it listens."""
        if self.fine_v < self.levels.t_high:
            self.high_since = None
            return False
        if self.high_since is None:
            self.high_since = self.fine_t
            return False
        return time.ticks_diff(self.fine_t, self.high_since) >= \
            BREAK_DETECT_SLOTS * self.clock.duration

    def stop_for_break(self):
        """Leader: stop the block and listen, as if it had ended. What the
        Follower sends next -- its Data Sync Error -- demotes and resends."""
        self.tx = []
        self.sub = self.STEP_CRC
        self.crc_started = False
        self.rx = []
        self.high_since = None
        self.breaks += 1

    def start_break(self, now):
        """Follower: show high until the Leader stops."""
        self.breaking = True
        self.break_since = now
        self.break_dark_since = None
        self.tx = []
        self.await_block()
        self.breaks += 1

    def step_break(self, now, fine):
        duration = self.clock.duration
        if time.ticks_diff(now, self.break_since) > BREAK_MAX_SLOTS * duration:
            # Not heard. Wait the block out and ask afterwards, as a Follower
            # that cannot break does.
            self.breaking = False
            self.want_resend = True
            self.idle_slots = 0
            return LOW
        if fine:
            if self.fine_v < self.levels.t_low:
                if self.break_dark_since is None:
                    self.break_dark_since = self.fine_t
                elif time.ticks_diff(self.fine_t, self.break_dark_since) >= \
                        BREAK_ACK_SLOTS * duration:
                    # The Leader has stopped, or finished, and is listening.
                    self.breaking = False
                    self.dark_since = None
                    self.have_prev = False
                    self.lit_pending = False
                    self.send_sync_error()
                    return LOW
            else:
                self.break_dark_since = None
        return HIGH

    # -- Leader side

    def leader_rx(self):
        if self.sub == self.STEP_BLOCK:
            self.sub = self.STEP_CRC
            self.rx = []
            self.crc_started = False
            return

        if self.sub == self.STEP_CRC:
            symbol = self.rx[-1]

            # The reply is 16 pure data symbols. A black among them can only be
            # the Data Sync Error Pattern, which is how the Follower says it
            # could not frame the block.
            if not self.crc_started:
                if symbol == LOW:
                    self.rx = []
                    return
                self.crc_started = True
                self.advance()
                self.rx = [symbol]
            elif symbol == LOW:
                # The peer's demotion, so the same rule as our own below: a
                # new rate starts with the whole CRC retry budget.
                self.clock.slow_down()
                self.retries = 0
                self.rx = []
                self.send_block(SYNC_LEAD_IN)
                return

            if len(self.rx) >= 16:
                received = symbols_bits(self.rx[:16])
                self.rx = []
                self.sub = self.STEP_VERDICT
                if received == self.expected_crc:
                    self.last_verdict = CRC_GOOD
                    self.send(CRC_GOOD)
                    self.block_index += 1
                    self.retries = 0
                    self.attempts = 0
                    self.block_started = self.now
                else:
                    self.retries += 1
                    if self.retries > MAX_BLOCK_RETRIES:
                        # A rate can frame a block and still get its bits
                        # wrong. Step down, but only from ground that was a
                        # gamble. Demoting on the first bad CRC splits the
                        # rungs: the announcement has to be read at the very
                        # rate that is too fast to read, so it arrives
                        # corrupted and the badges end up a rung apart.
                        if self.clock.index > self.conservative_index():
                            self.pending_slow_down = True
                            self.retries = 0
                            self.send(SYNC_ERROR)
                            return
                        self.fail(ERR_CRC)
                        return
                    self.last_verdict = CRC_BAD
                    self.send(CRC_BAD)
            return

        if self.sub == self.STEP_VERDICT:
            self.rx = []
            if self.block_index >= len(self.blocks):
                self.send_complete()
            else:
                gc.collect()        # between blocks, nothing is time critical
                self.send_block()
            return

        if self.sub == self.STEP_FINISH:
            if "".join(self.rx[-len(COMPLETE_ACK_TAIL):]) == COMPLETE_ACK_TAIL:
                # The only evidence the Leader ever gets that the Follower has
                # the payload, and the only thing entitled to report success.
                self.result = OK
                self.phase = PHASE_DONE
                return
            self.wait_slots += 1
            if self.wait_slots > COMPLETE_RETRY_SLOTS:
                self.wait_slots = 0
                if self.complete_tries > MAX_COMPLETE_RETRIES:
                    # Every block was acknowledged, but the ending was not, so
                    # we do not know whether the Follower has it. Saying so is
                    # the honest answer; saying OK is the one that matters most
                    # not to say.
                    self.fail(ERR_LOST_PEER)
                else:
                    self.rx = []
                    self.send_complete()

    def send_complete(self):
        self.complete_tries += 1
        # Queued, then acknowledged. `finishing` marks the handover from
        # transmitting to waiting; it is no longer a licence to report OK.
        self.send(COMPLETE)
        self.finishing = True

    # A Follower that misses a Begin Block Pattern never enters the block, so
    # nothing trips the framing check and it has nothing to say -- leaving the
    # Leader waiting in STEP_CRC for a reply that was never coming. Asking again
    # triggers the demotion path the Leader already has. A request already
    # waiting -- a framing failure's -- is left as it is: re-arming it makes no
    # second request and no second demotion.
    #
    # Returns whether the request was made; if not, the caller gives up.
    def request_resend(self):
        if self.leader or self.sub != self.STEP_BLOCK or self.in_block:
            return False
        # Only with nothing uncommitted. Holding a block whose verdict never
        # arrived, the Follower cannot tell an accepted one from a rejected one,
        # and asking for a resend would either drop a block the Leader has moved
        # past or duplicate one it has not.
        if self.pending_tokens or self.pending_block:
            return False
        if self.resend_requests >= MAX_BLOCK_ATTEMPTS:
            return False
        self.want_resend = True
        self.stall_slots = 0
        return True

    # -- Follower side

    def follower_rx(self):
        if self.sub == self.STEP_BLOCK:
            # Complete arrives here, after the last block's verdict returned us
            # to waiting for another Begin Block Pattern that never comes.
            if "".join(self.rx[-len(COMPLETE_TAIL):]) == COMPLETE_TAIL and not self.in_block:
                self.accept_pending()
                self.acknowledge_complete()
                return
            self.collect_block()
            return

        if self.sub == self.STEP_FINISH:
            # A repeat means the acknowledgement did not arrive. Answer again
            # rather than leaving the Leader to time out over a payload it
            # successfully delivered.
            if "".join(self.rx[-len(COMPLETE_TAIL):]) == COMPLETE_TAIL:
                self.acknowledge_complete()
                return
            self.wait_slots += 1
            if self.wait_slots > LINGER_SLOTS:
                self.finish_follower()
            return

        if self.sub == self.STEP_VERDICT:
            if self.rx[-1] != LOW:
                self.verdict_lit += 1
            tail = "".join(self.rx[-len(CRC_GOOD_TAIL):])
            if tail == CRC_GOOD_TAIL:
                self.advance()
                self.accept_pending()
                self.await_block()
                gc.collect()
            elif tail == CRC_BAD_TAIL:
                self.advance()
                self.discard_pending()
                self.await_block()
            elif tail == SYNC_ERROR_TAIL:
                self.advance()
                # The Leader is stepping down a rung. Follow it, changing only
                # the slot duration so the shared epoch keeps both grids aligned.
                self.clock.slow_down()
                self.discard_pending()
                self.await_block()
            elif "".join(self.rx[-len(COMPLETE_TAIL):]) == COMPLETE_TAIL:
                # Complete is only sent after the Leader matched the last
                # block's CRC, so it carries the same guarantee CRC Good does.
                # Arriving here means the verdict went missing and this is the
                # proof of it: commit before finishing, or the badge reports
                # success while holding nothing.
                self.accept_pending()
                self.acknowledge_complete()
            elif self.continuous and self.verdict_lit > VERDICT_LOST_SYMBOLS:
                # The Leader has moved on and the verdict went by unread.
                self.start_break(self.now)

    # Commit the block whose CRC has just been acknowledged, once. Clearing what
    # is pending is what makes it once: a block may be acknowledged twice -- by
    # its verdict and again by the Complete Pattern -- and appending it twice
    # would corrupt the payload while every CRC still passed (I4). The flag as
    # well as the tokens, because an empty block adds none and still has to
    # count as accepted.
    def accept_pending(self):
        self.resend_requests = 0
        self.block_started = self.now
        if self.pending_block or self.pending_tokens:
            self.received.extend(self.pending_tokens)
            self.blocks_accepted += 1
        self.discard_pending()

    def discard_pending(self):
        self.pending_tokens = []
        self.pending_block = False

    # Answer Complete, then stay on air long enough to answer it again. A
    # Follower that finished the instant it acknowledged would be gone before
    # the Leader's first retry, turning one lost acknowledgement into a failure
    # on a transfer that actually worked.
    def acknowledge_complete(self):
        self.send(COMPLETE_ACK)
        self.sub = self.STEP_FINISH
        self.wait_slots = 0
        self.idle_slots = 0
        self.rx = []

    # The Follower only succeeds if a block actually landed. A Complete Pattern
    # that arrives with none accepted means the blocks never did, and saying OK
    # to that is the failure this protocol most needs not to have. Counted in
    # blocks, not tokens: an empty payload is one accepted block holding none.
    def finish_follower(self):
        self.result = OK if self.blocks_accepted else ERR_DECODE
        self.phase = PHASE_DONE

    def await_block(self):
        self.rx = []
        self.reset_block()
        self.sub = self.STEP_BLOCK

    def reset_block(self):
        self.tokens = []
        self.bits = []
        self.in_block = False
        self.end_match = []

    def collect_block(self):
        symbol = self.rx[-1]

        if not self.in_block:
            if self.pending_block or self.pending_tokens:
                # Holding a block whose verdict never came: a resend repeats
                # it first. Settle that before any new block.
                tail = "".join(self.rx[-len(CRC_GOOD_TAIL):])
                if tail == CRC_GOOD_TAIL:
                    self.accept_pending()
                    self.rx = []
                    return
                if tail == CRC_BAD_TAIL:
                    self.discard_pending()
                    self.rx = []
                    return
            if "".join(self.rx[-len(BEGIN_BLOCK):]) == BEGIN_BLOCK:
                if self.pending_block or self.pending_tokens:
                    # Still unsettled: taking this block would overwrite the
                    # one held. Ask again.
                    self.frame_error()
                    return
                self.advance()
                self.want_resend = False
                self.in_block = True
                self.tokens = []
                self.bits = []
                self.end_match = []
                self.rx = []
            return

        # Part way through matching the End Block Pattern's tail.
        if self.end_match:
            self.end_match.append(symbol)
            if len(self.end_match) == len(END_BLOCK):
                if "".join(self.end_match) == END_BLOCK:
                    self.finish_block()
                else:
                    self.frame_error()
            return

        if symbol == LOW:
            # The End Block Pattern opens with two data symbols and then a
            # black, so a black arriving two bits into a byte after '_-' is the
            # pattern starting, not a framing error. A black on a byte boundary
            # is the Field Separator.
            if len(self.bits) == 2 and self.bits == [0, 1]:
                self.bits = []
                self.end_match = [MID, HIGH, LOW]
                return
            if self.bits:
                self.frame_error()
                return
            self.tokens.append(SEPARATOR)
            self.advance()
            return

        self.bits.append(1 if symbol == HIGH else 0)
        if len(self.bits) == 8:
            value = 0
            for bit in self.bits:
                value = (value << 1) | bit
            self.tokens.append(value)
            self.bits = []
            self.advance()
            if sum(1 for t in self.tokens if t != SEPARATOR) > BLOCK_BYTES:
                self.frame_error()

    def finish_block(self):
        self.pending_tokens = self.tokens
        self.pending_block = True
        self.verdict_lit = 0
        self.send(bits_symbols(block_crc(self.tokens), 16))
        self.rx = []
        self.sub = self.STEP_VERDICT

    # The block cannot be framed. Stop decoding it, and say so only once the
    # Leader has finished sending it: it does not listen while it transmits,
    # so a Data Sync Error sent now would never be heard -- and this badge
    # would demote on its own, a rung away from a Leader it could no longer
    # read. The answer goes out by the same route as a request for a block
    # never seen, so one failure is one request and one demotion, however
    # long the rest of the block keeps the stall budget running.
    def frame_error(self):
        if self.continuous:
            # The Leader hears a break while it transmits: no need to wait
            # for the rest of the block.
            self.start_break(self.now)
            return
        self.want_resend = True
        self.idle_slots = 0         # consecutive black from here, not before
        self.await_block()

    def send_sync_error(self):
        self.pending_slow_down = True
        self.send(SYNC_ERROR)
        self.await_block()


# Splits a received token stream back into its fields. Structure only: the
# protocol has no opinion about what the fields should contain, so anything that
# arrived intact parses. The caller decides whether it is acceptable.
def parse_payload(tokens):
    fields = []
    current = []
    for token in tokens:
        if token == SEPARATOR:
            fields.append(bytes(current))
            current = []
        else:
            current.append(token)
    fields.append(bytes(current))
    return fields
