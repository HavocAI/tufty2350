"""Dev Mode's room-flicker tool: how much room lights flicker, as the sensor sees.

Lights on the mains -- LED drivers, fluorescent tubes -- can flicker at twice
the mains frequency, 100 Hz or 120 Hz, and some much faster. To a link whose
slots are a few milliseconds long that is noise in the very band it signals
in, and nothing has measured it. This takes one 250 ms capture each time A is
pressed, and finds the strongest frequency in it and how big it is.

Pure Python, like measure.py and probe.py: no `screen`, no `badge`. The app
hands in the sensor and the clock. Specified under "measure room flicker" in
.agents/docs/share_wifi_spec.md.
"""

import math
import time

TOOL_FLICKER = "room_flicker"   # its key in the measurements file

# One capture: the sensor read back to back for CAPTURE_MS, each reading
# added to the bin of BIN_US it fell in. Binned as it goes, so nothing is
# allocated per reading and a capture of ~9,000 reads needs two arrays of
# BINS. A bin's mean is a sample every 0.5 ms: up to 1 kHz can be seen.
CAPTURE_MS = 250
BIN_US = 500
BINS = CAPTURE_MS * 1000 // BIN_US

# The frequencies searched for the strongest, and those always reported:
# mains and twice mains, for 50 Hz and 60 Hz countries.
SCAN_FROM_HZ = 20
SCAN_TO_HZ = 1000
SCAN_STEP_HZ = 5
MAINS_HZ = (50, 60, 100, 120)


class Capture:
    """Buffers for captures, allocated once."""

    def __init__(self):
        self.sums = [0] * BINS
        self.counts = [0] * BINS
        self.reads = 0
        self.elapsed_us = 0

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
        self.elapsed_us = time.ticks_diff(ticks_us(), start)

    def series(self):
        """Each bin's mean, an empty bin taking the one before's. None if no
        bin was read."""
        out = [0.0] * BINS
        last = None
        for i in range(BINS):
            if self.counts[i]:
                last = self.sums[i] / self.counts[i]
            out[i] = last
        if last is None:
            return None
        # Leading empties take the first reading.
        first = None
        for v in out:
            if v is not None:
                first = v
                break
        for i in range(BINS):
            if out[i] is None:
                out[i] = first
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
    lo = hi = series[0]
    for x in series:
        var += (x - mean) * (x - mean)
        if x < lo:
            lo = x
        if x > hi:
            hi = x
    peak_hz = 0
    peak = 0.0
    for hz in range(SCAN_FROM_HZ, SCAN_TO_HZ + 1, SCAN_STEP_HZ):
        a = amplitude(series, mean, hz)
        if a > peak:
            peak = a
            peak_hz = hz
    mains = {}
    for hz in MAINS_HZ:
        mains[str(hz)] = round(amplitude(series, mean, hz), 1)
    return {"reads": capture.reads,
            "read_us": round(capture.elapsed_us / capture.reads, 1),
            "mean": round(mean, 1),
            "sd": round(math.sqrt(var / n), 1),
            "p2p": round(hi - lo, 1),
            "peak_hz": peak_hz,
            "peak_counts": round(peak, 1),
            "hz": mains}


def summary(result):
    """One line for the screen: what the last measurement found."""
    if "error" in result:
        return result["error"]
    return "%d Hz, %.1f counts on %.0f" % (
        result["peak_hz"], result["peak_counts"], result["mean"])
