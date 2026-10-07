"""Dev Mode's room-flicker tool: how much room lights flicker, as the sensor sees.

Lights on the mains -- LED drivers, fluorescent tubes -- can flicker at twice
the mains frequency, 100 Hz or 120 Hz, and some much faster. To a link whose
slots are a few milliseconds long that is noise in the very band it signals
in, and nothing has measured it. This takes one 250 ms capture each time A is
pressed, and finds the strongest frequency in it and how big it is.

Pure Python, like measure.py and probe.py: no `screen`, no `badge`. The app
hands in the sensor and the clock. Specified under "measure room flicker" in
.agents/docs/share_wifi_spec.md.

Each measurement also runs the transfer's own flicker fit on the capture --
FlickerFilter, from the protocol -- and then measures the mains' drift: the
flicker's phase, window after window, for about 20 s.
"""

import math
import time

# Absolute, as in __init__.py: the launcher puts the app folder on sys.path.
# The sine table and its step are the filter's own, so the drift's phase is
# reckoned exactly as the phase lock reckons it.
from protocol import (
    _QUARTER, _SINE, _US_PER_STEP_HZ, FINE_MS, FLICKER_FIT_MS,
    FLICKER_HARMONICS, FLICKER_MAINS, FLICKER_STEPS,
    FlickerFilter,
)

TOOL_FLICKER = "room_flicker"   # its key in the measurements file

# One capture: the sensor read back to back for CAPTURE_MS, each reading
# added to the bin of BIN_US it fell in. Binned as it goes, so nothing is
# allocated per reading and a capture of ~9,000 reads needs two arrays of
# BINS. A bin's mean is a sample every 0.5 ms: up to 1 kHz can be seen.
CAPTURE_MS = 250
BIN_US = 500
BINS = CAPTURE_MS * 1000 // BIN_US

# The frequencies searched for the strongest, and those always reported:
# every harmonic the flicker fit uses, 1-4 x 50 and 60 Hz.
SCAN_FROM_HZ = 20
SCAN_TO_HZ = 1000
SCAN_STEP_HZ = 5
HARMONIC_HZ = tuple(sorted({m * h for m in FLICKER_MAINS
                            for h in range(1, FLICKER_HARMONICS + 1)}))

# The fit is fed what the transfer feeds it: FINE_MS means, one every
# SAMPLE_EVERY_US -- the rate a transfer keeps them at -- from the first
# FLICKER_FIT_MS of the capture. The rest, about 90 ms, is held out: the
# flicker left after correction is measured there, on samples the fit never
# saw.
SAMPLE_BINS = FINE_MS * 1000 // BIN_US
SAMPLE_EVERY_BINS = 3000 // BIN_US
SAMPLE_SPAN_US = SAMPLE_BINS * BIN_US

# The drift: after the capture, DRIFT_WINDOWS more of CAPTURE_MS, one a
# frame, each giving the flicker's phase at DRIFT_HZ -- twice the fitted
# mains, or the stronger of 100 and 120 Hz in the spectrum. A line through
# the phases is the flicker's offset from nominal. Measured against the
# badge's own clock, so a crystal off by x ppm reads as x ppm of the mains.
DRIFT_WINDOWS = 80
# Dev Mode M6: the flicker left in an EXACT slot's window at the lit 8 ms
# rung -- 3.5 ms, every 8 ms -- over the held-out part, before and after the
# fit's correction. Whether that rung survives a flickering room.
RESIDUAL_WINDOW_BINS = 7
RESIDUAL_EVERY_BINS = 16
DRIFT_HZ = (100, 120)
# Flicker smaller than this at DRIFT_HZ, in counts, has no phase to track.
DRIFT_MIN_COUNTS = 2
# A window whose phase lands more than this many cycles from the prediction
# could have been unwrapped either way: the result is marked ambiguous.
DRIFT_AMBIGUOUS_TURNS = 0.25


class Capture:
    """Buffers for captures, allocated once."""

    def __init__(self):
        self.sums = [0] * BINS
        self.counts = [0] * BINS
        self.reads = 0
        self.elapsed_us = 0
        self.start_us = 0

    def take(self, light, ticks_us):
        """Read `light` back to back for CAPTURE_MS. Blocks while it does."""
        sums = self.sums
        counts = self.counts
        for i in range(BINS):
            sums[i] = 0
            counts[i] = 0
        window = BINS * BIN_US
        reads = 0
        start = ticks_us()
        while True:
            t = time.ticks_diff(ticks_us(), start)
            if t >= window:
                break
            value = light()
            b = t // BIN_US
            sums[b] += value
            counts[b] += 1
            reads += 1
        self.reads = reads
        self.start_us = start
        self.elapsed_us = time.ticks_diff(ticks_us(), start)

    def series(self):
        """Each bin's mean, an empty bin taking the one before's. None if no
        bin was read. Leading empties take the first reading."""
        sums = self.sums
        counts = self.counts
        last = None
        for i in range(BINS):
            if counts[i]:
                last = sums[i] / counts[i]
                break
        if last is None:
            return None
        out = [0.0] * BINS
        for i in range(BINS):
            if counts[i]:
                last = sums[i] / counts[i]
            out[i] = last
        return out


def amplitude(series, mean, hz):
    """The amplitude, in counts, of the `hz` component of a series sampled
    every BIN_US: Goertzel's algorithm, so one multiply-add a sample."""
    n = len(series)
    w = 2 * math.pi * hz * BIN_US / 1000000.0
    coeff = 2 * math.cos(w)
    s1 = s2 = 0.0
    for x in series:
        s0 = (x - mean) + coeff * s1 - s2
        s2 = s1
        s1 = s0
    power = s1 * s1 + s2 * s2 - coeff * s1 * s2
    return 2 * math.sqrt(max(0.0, power)) / n


def analyse(capture):
    """One measurement's result, or {"error": ...}."""
    series = capture.series()
    if series is None or capture.reads == 0:
        return {"error": "no readings"}
    n = len(series)
    mean = sum(series) / n
    var = 0.0
    for x in series:
        var += (x - mean) * (x - mean)
    peak_hz = 0
    peak = 0.0
    for hz in range(SCAN_FROM_HZ, SCAN_TO_HZ + 1, SCAN_STEP_HZ):
        a = amplitude(series, mean, hz)
        if a > peak:
            peak = a
            peak_hz = hz
    mains = {}
    for hz in HARMONIC_HZ:
        mains[str(hz)] = round(amplitude(series, mean, hz), 1)
    return {"reads": capture.reads,
            "read_us": round(capture.elapsed_us / capture.reads, 1),
            "mean": round(mean, 1),
            "sd": round(math.sqrt(var / n), 1),
            "p2p": round(max(series) - min(series), 1),
            "peak_hz": peak_hz,
            "peak_counts": round(peak, 1),
            "hz": mains,
            "filter": fit_capture(capture)}


# --- the transfer's fit, on the capture ---------------------------------------

def samples(capture):
    """The capture as a transfer's fit sees it: (times in us, mean values,
    spans in us), a FINE_MS mean every SAMPLE_EVERY_BINS bins. A sample with
    no reading in it is left out."""
    ts = []
    vs = []
    spans = []
    sums = capture.sums
    counts = capture.counts
    start = capture.start_us
    first = 0
    while first + SAMPLE_BINS <= BINS:
        total = 0
        count = 0
        for b in range(first, first + SAMPLE_BINS):
            total += sums[b]
            count += counts[b]
        if count:
            ts.append(time.ticks_add(start, first * BIN_US
                                     + SAMPLE_SPAN_US // 2))
            vs.append(total // count)
            spans.append(SAMPLE_SPAN_US)
        first += SAMPLE_EVERY_BINS
    return ts, vs, spans


def _sd(values):
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    total = 0.0
    for v in values:
        total += (v - mean) * (v - mean)
    return math.sqrt(total / n)


def fit_capture(capture):
    """FlickerFilter's fit on the capture's first FLICKER_FIT_MS, retried on
    its newest half as the transfer retries (Transfer.fit_flicker), and the
    flicker left on the held-out rest before and after correction."""
    ts, vs, spans = samples(capture)
    if not ts:
        return {"verdict": None, "reason": "few"}
    window_end = time.ticks_add(capture.start_us, FLICKER_FIT_MS * 1000)
    held = 0
    while held < len(ts) and \
            time.ticks_diff(window_end, ts[held]) >= SAMPLE_SPAN_US // 2:
        held += 1
    if not held:
        return {"verdict": None, "reason": "few"}
    flicker = FlickerFilter()
    window_ms = FLICKER_FIT_MS
    verdict = None
    while True:
        first = 0
        while first < held and time.ticks_diff(
                ts[held - 1], ts[first]) > window_ms * 1000:
            first += 1
        verdict = flicker.fit(ts[first:held], vs[first:held],
                              spans[first:held])
        if verdict is not None or window_ms < FLICKER_FIT_MS:
            break
        window_ms //= 2
    info = flicker.fit_info or {}
    out = {"verdict": verdict, "reason": info.get("reason"),
           "window_ms": window_ms, "mains": info.get("mains")}
    if info.get("noise") is not None:
        out["noise"] = round(info["noise"], 1)
    sizes = info.get("sizes")
    if sizes is not None:
        harmonics = {}
        for h in range(len(sizes)):
            harmonics[str(info["mains"] * (h + 1))] = round(sizes[h], 1)
        out["harmonics"] = harmonics
    if verdict == "flicker":
        kept = []
        for h in range(len(flicker.harmonics)):
            a, b = flicker.harmonics[h]
            if a or b:
                kept.append(flicker.mains * (h + 1))
        out["kept"] = kept
    before = vs[held:]
    after = []
    for k in range(held, len(ts)):
        after.append(flicker.correct(ts[k], vs[k], spans[k]))
    out["held_out"] = len(before)
    sd = _sd(before)
    if sd is not None:
        out["sd_before"] = round(sd, 1)
        out["sd_after"] = round(_sd(after), 1)
    out["window_residual"] = window_residual(capture, flicker)
    return out


def window_residual(capture, flicker):
    """M6: windows of RESIDUAL_WINDOW_BINS every RESIDUAL_EVERY_BINS over
    the capture's held-out part, their means' spread before and after
    `flicker` corrects them, in counts."""
    sums = capture.sums
    counts = capture.counts
    span = RESIDUAL_WINDOW_BINS * BIN_US
    before = []
    after = []
    b = FLICKER_FIT_MS * 1000 // BIN_US
    while b + RESIDUAL_WINDOW_BINS <= BINS:
        total = 0
        count = 0
        for k in range(b, b + RESIDUAL_WINDOW_BINS):
            total += sums[k]
            count += counts[k]
        if count:
            value = total // count
            t = time.ticks_add(capture.start_us, b * BIN_US + span // 2)
            before.append(value)
            after.append(flicker.correct(t, value, span))
        b += RESIDUAL_EVERY_BINS
    out = {"window_ms": span / 1000.0, "windows": len(before)}
    sd = _sd(before)
    if sd is not None:
        out["sd_before"] = round(sd, 1)
        out["sd_after"] = round(_sd(after), 1)
    return out


# --- mains drift ----------------------------------------------------------

def drift_hz(result):
    """The frequency to track the drift at, or None: twice the fitted mains
    when the fit found flicker, else the stronger of DRIFT_HZ in the
    spectrum; None if that is under DRIFT_MIN_COUNTS."""
    amplitudes = result.get("hz", {})
    found = result.get("filter", {})
    if found.get("verdict") == "flicker" and found.get("mains"):
        hz = 2 * found["mains"]
    else:
        # The first of equals, as max() gives it.
        hz = max(DRIFT_HZ, key=lambda c: amplitudes.get(str(c), 0))
    if amplitudes.get(str(hz), 0) < DRIFT_MIN_COUNTS:
        return None
    return hz


class Drift:
    """The flicker's phase at `hz`, one capture at a time.

    Each window's phase is the bins' projection on the filter's sine table at
    their times since the first window began, so windows may come at any
    gaps. Unwrapped against the line through the first and latest phases,
    which holds while the flicker moves under half a cycle between the first
    two windows: a mains offset below 1 / (4 x their gap) -- 0.9 Hz at one
    window a frame. Past it the answer aliases and looks clean, so any window
    that lands more than DRIFT_AMBIGUOUS_TURNS of a cycle from where it was
    predicted marks the result `ambiguous`: from about half that limit."""

    def __init__(self, hz, windows=DRIFT_WINDOWS):
        self.hz = hz
        self.origin = None
        self.n = 0
        self.attempts = 0               # windows tried, read or not
        self.ambiguous = False
        self.ts = [0.0] * windows
        self.phases = [0.0] * windows

    def add(self, capture):
        """One window's phase, from a capture just taken."""
        self.attempts += 1
        if self.n >= len(self.ts):
            return
        sums = capture.sums
        counts = capture.counts
        total = 0
        used = 0
        for i in range(BINS):
            if counts[i]:
                total += sums[i] // counts[i]
                used += 1
        if not used:
            return
        if self.origin is None:
            self.origin = capture.start_us
        mean = total // used
        dt0 = time.ticks_diff(capture.start_us, self.origin)
        hz = self.hz
        si = co = 0
        for i in range(BINS):
            if not counts[i]:
                continue
            v = sums[i] // counts[i] - mean
            t = dt0 + i * BIN_US + BIN_US // 2
            j = (t % 1000000) * hz // _US_PER_STEP_HZ % FLICKER_STEPS
            si += v * _SINE[j]
            co += v * _SINE[(j + _QUARTER) % FLICKER_STEPS]
        phase = math.atan2(co, si)
        t_s = (dt0 + BINS * BIN_US // 2) / 1000000.0
        n = self.n
        if n:
            predicted = self.phases[n - 1]
            if n >= 2 and self.ts[n - 1] > self.ts[0]:
                rate = (self.phases[n - 1] - self.phases[0]) / \
                    (self.ts[n - 1] - self.ts[0])
                predicted += rate * (t_s - self.ts[n - 1])
            turns = round((predicted - phase) / (2 * math.pi))
            phase += 2 * math.pi * turns
            if abs(phase - predicted) > 2 * math.pi * DRIFT_AMBIGUOUS_TURNS:
                self.ambiguous = True
        self.ts[n] = t_s
        self.phases[n] = phase
        self.n = n + 1

    def result(self):
        """The line through the phases: the mains' offset from nominal in
        mHz, against the badge's clock, and the phases' scatter about it."""
        n = self.n
        if n < 3:
            return {"error": "too few windows"}
        ts = self.ts
        phases = self.phases
        t_mean = 0.0
        p_mean = 0.0
        for k in range(n):
            t_mean += ts[k]
            p_mean += phases[k]
        t_mean /= n
        p_mean /= n
        stt = 0.0
        stp = 0.0
        for k in range(n):
            dt = ts[k] - t_mean
            stt += dt * dt
            stp += dt * (phases[k] - p_mean)
        if stt <= 0:
            return {"error": "too few windows"}
        slope = stp / stt
        left = 0.0
        for k in range(n):
            r = phases[k] - p_mean - slope * (ts[k] - t_mean)
            left += r * r
        # The flicker is twice the mains, so it moves twice as far.
        offset_hz = slope / (2 * math.pi) / 2
        return {"hz": self.hz, "windows": n, "ambiguous": self.ambiguous,
                "seconds": round(ts[n - 1] - ts[0], 1),
                "offset_mhz_vs_clock": round(offset_hz * 1000, 1),
                "wander_deg": round(math.sqrt(left / (n - 2)) * 180 / math.pi,
                                    1)}


def summary(result):
    """One line for the screen: what the last measurement found."""
    if "error" in result:
        return result["error"]
    return "%d Hz, %.1f counts on %.0f" % (
        result["peak_hz"], result["peak_counts"], result["mean"])
