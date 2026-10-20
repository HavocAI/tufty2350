"""What a transfer asks of the light sensor, answered after the fact: the
means of stretches of time, and where a rise crossed a threshold, from
every reading the ADC makes -- written by DMA into a ring (fastadc.Ring)
and timed by its index, so nothing a pass does blinds it.

Times are us since the transfer began: `now_us()` accumulates the ticks, so
it never wraps. Values are light_level() counts (a raw reading x 16), means
keeping their sub-step resolution; -1 is "no readings there". Nothing
allocates once built.

Pure: the registers and viper kernels are fastadc.py's. Their twins here --
ref_sum, ref_parts, ref_rise -- are fastadc's own check on the badge, and
the simulator's ring.

Specification: section 6 of .agents/optical_data_transfer/spec.md.
"""

import time
from array import array

COUNTS_PER_RAW = 16

# Readings kept clear of the write pointer, so none is overwritten while
# it is summed.
RING_SAFE_US = 10000
# An anchor this far from the one in use means readings were lost: beyond
# the +-30 us two best-of-four anchors can differ by.
RING_DRIFT_US = 100
ANCHOR_TRIES = 4
# A rise is looked for in bins this long: 5 readings at 50 kHz.
EDGE_BIN_US = 100
# The most window parts, and edge thresholds, one query takes.
PARTS_MAX = 32
THR_MAX = 128

# fastadc.Ring.health() bits.
HEALTH_ADC = 1
HEALTH_LOST = 2
HEALTH_DMA = 4


# --- the kernels' twins -----------------------------------------------------
# What fastadc's viper kernels do, in plain Python, over any indexable
# `buf` whose length is a power of two (`mask` is that length less one).

def ref_sum(buf, mask, start, n):
    total = 0
    for i in range(n):
        total += buf[(start + i) & mask]
    return total


def ref_parts(buf, mask, start, part, n, out):
    idx = start
    for j in range(n):
        total = 0
        for _ in range(part):
            total += buf[idx & mask]
            idx += 1
        out[j] = total


def ref_rise(buf, mask, start, bin_len, bins, thr, thr_step):
    """The first bin b such that b and b + 1 both sum to at least their
    thresholds, thr[b // thr_step]; -1 if none."""
    idx = start
    prev = False
    for b in range(bins):
        total = 0
        for _ in range(bin_len):
            total += buf[idx & mask]
            idx += 1
        if total >= thr[b // thr_step]:
            if prev:
                return b - 1
            prev = True
        else:
            prev = False
    return -1


def _clock(ticks_us):
    return ticks_us if ticks_us is not None else time.ticks_us


class RingReader:
    """The reader, over a ring (fastadc.Ring on the badge). `per_entry` is
    how many readings one entry sums -- 1 on the badge -- and `usable_us`
    how much history is safe to read: by default the ring less
    RING_SAFE_US."""

    def __init__(self, ring, ticks_us=None, per_entry=1, usable_us=None):
        samples = ring.samples
        assert samples & (samples - 1) == 0, "a ring is a power of two"
        self.ring = ring
        self.period = ring.period_us
        self.samples = samples
        self.per_entry = per_entry
        self.usable = usable_us or samples * self.period - RING_SAFE_US
        self.usable_n = self.usable // self.period
        self.ticks_us = _clock(ticks_us)
        self.bin_len = max(1, EDGE_BIN_US // self.period)
        self.sums = array("i", [0] * PARTS_MAX)
        self.thr = array("i", [0] * THR_MAX)
        self.one = [0]
        self.now = 0
        self.last = 0
        self.anchor = 0
        self.anchor_width = 0   # the bracket the anchor was read in, us
        self.lost = 0           # FIFO over- or underflows seen
        self.drift = 0          # anchors that moved over RING_DRIFT_US
        self.faults = 0

    # -- the clock

    def start(self):
        self.ring.start()
        self.last = self.ticks_us()
        self.now = 0
        self.lost = self.drift = self.faults = 0
        self.anchor = self.measure_anchor()

    def stop(self):
        self.ring.stop()

    def now_us(self):
        t = self.ticks_us()
        self.now += time.ticks_diff(t, self.last)
        self.last = t
        return self.now

    def pump(self, until):
        """Waits for `until`: the DMA is reading meanwhile."""
        while self.now_us() < until:
            pass

    def measure_anchor(self):
        """The time of index 0's end, from the best of ANCHOR_TRIES reads of
        the position bracketed by the clock: the narrowest bracket wins."""
        ticks_us = self.ticks_us
        position = self.ring.position
        best = -1
        mid = p = 0
        for _ in range(ANCHOR_TRIES):
            a = ticks_us()
            q = position()
            b = ticks_us()
            width = time.ticks_diff(b, a)
            if best < 0 or width < best:
                best = width
                p = q
                mid = self.now + time.ticks_diff(a, self.last) + width // 2
        self.anchor_width = best
        return mid - p * self.period

    def check(self):
        """Once a block. False if the ring has failed -- the ADC moved off
        the sensor, the channel stopped -- which ends the transfer. Lost
        readings re-anchor; otherwise a fresh anchor is only compared."""
        health = self.ring.health()
        if health & HEALTH_LOST:
            self.lost += 1
            self.anchor = self.measure_anchor()
            health &= ~HEALTH_LOST
        if health:
            self.faults += 1
            return False
        anchor = self.measure_anchor()
        if abs(anchor - self.anchor) > RING_DRIFT_US:
            self.drift += 1
            self.anchor = anchor
        return True

    # -- what is there

    def _index(self, t):
        return (t - self.anchor) // self.period

    def newest_us(self):
        """Readings are complete up to here: the entry before the one being
        written may still be in flight."""
        return self.anchor + (self.ring.position() - 1) * self.period

    def oldest_us(self):
        return self.newest_us() - self.usable

    # -- queries

    def parts(self, t0, t1, n, out, times=None):
        """`out[j]` the mean of the j-th of `n` equal parts of [t0, t1), or
        -1 for a part not in the ring. Returns the readings used. Every
        reading is there, so `times` -- if given -- gets each part's
        centre."""
        i0 = self._index(t0)
        part = (self._index(t1) - i0) // n
        if part < 1:
            for j in range(n):
                out[j] = -1
            return 0
        p = self.ring.position()
        lo = p - 1 - self.usable_n
        hi = p - 1
        first = 0 if i0 >= lo else (lo - i0 + part - 1) // part
        last = n if i0 + n * part <= hi else max(0, (hi - i0) // part)
        if last > n:
            last = n
        for j in range(n):
            if j < first or j >= last:
                out[j] = -1
        if last <= first:
            return 0
        if times is not None:
            for j in range(n):
                times[j] = self.anchor + (i0 + j * part) * self.period \
                    + part * self.period // 2
        sums = self.sums
        self.ring.parts(i0 + first * part, part, last - first, sums)
        # Overwritten while it was summed: older than a whole ring now.
        gone = self.ring.position() - self.samples
        scale = part * self.per_entry
        used = 0
        for j in range(first, last):
            if i0 + j * part < gone:
                out[j] = -1
            else:
                out[j] = sums[j - first] * COUNTS_PER_RAW // scale
                used += scale
        return used

    def bins(self, t0, n, out, at):
        """out[at + j] = the sum of the readings in the j-th EDGE_BIN_US
        from t0, raw (light_level() / 16): a fast block's bins
        (fastblock.Decoder). The caller keeps them inside the history."""
        i0 = self._index(t0)
        part = self.bin_len
        sums = self.sums
        j = 0
        while j < n:
            m = n - j
            if m > PARTS_MAX:
                m = PARTS_MAX
            self.ring.parts(i0 + j * part, part, m, sums)
            for k in range(m):
                out[at + j + k] = sums[k]
            j += m

    def mean(self, t0, t1):
        self.parts(t0, t1, 1, self.one)
        return self.one[0]

    def rise(self, t0, t1, thr, n_thr, step_us):
        """Where readings first rose through `thr` -- a threshold in counts
        for each `step_us` from t0, n_thr of them -- confirmed by the next
        EDGE_BIN_US bin, interpolated between bin centres. -1 if none."""
        bl = self.bin_len
        bin_us = bl * self.period
        p = self.ring.position()
        i0 = self._index(t0)
        lo = p - 1 - self.usable_n
        skip = 0
        if i0 < lo:
            skip = (lo - i0 + bl - 1) // bl
            i0 += skip * bl
        i1 = min(self._index(t1), p - 1)
        bins = (i1 - i0) // bl
        step = max(1, step_us // bin_us)
        offset = skip // step
        n = min(THR_MAX, n_thr - offset)
        if bins > n * step:
            bins = n * step
        if bins < 2:
            return -1
        readings = bl * self.per_entry
        raw = self.thr
        for k in range(n):
            raw[k] = (thr[k + offset] * readings + COUNTS_PER_RAW - 1) \
                // COUNTS_PER_RAW
        ring = self.ring
        b = ring.rise(i0, bl, bins, raw, step)
        if b < 0:
            return -1
        start_b = self.anchor + (i0 + b * bl) * self.period
        if b == 0 and i0 - bl < lo:
            return start_b
        v_b = ring.sum(i0 + b * bl, bl) * COUNTS_PER_RAW // readings
        v_a = ring.sum(i0 + (b - 1) * bl, bl) * COUNTS_PER_RAW // readings
        level = thr[b // step + offset]
        centre_a = start_b - bin_us + bin_us // 2
        if v_b <= v_a:
            return start_b
        off = (level - v_a) * bin_us // (v_b - v_a)
        if off < 0:
            off = 0
        elif off > bin_us:
            off = bin_us
        return centre_a + off
