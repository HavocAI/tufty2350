"""What a transfer asks of the light sensor, answered after the fact: the
means of stretches of time, and where a rise crossed a threshold.

Two readers with one interface, so the protocol never knows which it has:

    RingReader     EXACT. Every reading the ADC makes, written by DMA into a
                   ring (fastadc.Ring) and timed by its index, so nothing --
                   the protocol's call, a present, a collection -- blinds it.
    PolledReader   POLLED. light_level() read into 250 us bins while the loop
                   has nothing else to do: today's reading, blind while the
                   protocol or a present runs.

Times are microseconds since the transfer began: `now_us()` accumulates the
ticks, so it never wraps, and stays a small int for 17.9 minutes. Values are
light_level() counts (a raw reading x 16), means keeping their sub-step
resolution. -1 is "no readings there". Nothing here allocates once built.

Pure, like protocol.py: the ring's registers and viper kernels are in
fastadc.py, which hands this module a ring whose kernels it calls. Their pure
twins are here -- ref_sum, ref_parts, ref_rise -- for the simulator's ring,
for the tests, and for fastadc's own check on the badge.

Specification: "Reading" in .agents/docs/optical-data-transfer-protocol.md.
"""

import time
from array import array

COUNTS_PER_RAW = 16

# The ring: readings kept clear of the write pointer when the oldest are
# read, so a reading is not overwritten while it is summed.
RING_SAFE_US = 10000
# An anchor measured at a block that differs from the one in use by more
# than this means readings were lost: five readings at 50 kHz, beyond the
# +-30 us two best-of-four anchors can differ by.
RING_DRIFT_US = 100
ANCHOR_TRIES = 4
# A rise is looked for in bins this long: 5 readings at 50 kHz.
EDGE_BIN_US = 100
# The most window parts, and edge thresholds, one query takes.
PARTS_MAX = 32
THR_MAX = 128

# The polled reader's bins: 1,024 of 250 us, 256 ms. A rise is believed
# when its bins stay lit this long: a polled bin holds two or three readings,
# each a whole 16-count step, so one lit bin -- or two -- is often noise.
POLL_BIN_US = 250
POLL_BINS = 1024
POLL_CONFIRM_US = 1000
# And only if the last dark reading before it is this close: a rise that
# came while the loop was busy -- a collection is 20-46 ms on v2, a present
# 8-12 -- is somewhere in the gap, and locking on the first reading after
# would place the grid as late as the gap is long. Wide enough for a loop
# reading only every 2 ms: at 1 ms such a loop refused every rise, never
# locked, and its grid drifted off the peer's [simulation, veryslowread].
# The most it may be: the protocol narrows it per rung (gap_for()), since a
# crossing interpolated across a 5 ms gap can be 2-3 ms off, past a 16 ms
# rung's guard.
POLL_GAP_US = 5000


def gap_for(slot_us, read_us):
    """The gap a POLLED lock may interpolate across on a slot of `slot_us`,
    by a loop reading every `read_us`: an eighth of the slot -- inside its
    guard -- but never under three reads, or it would never lock, nor under
    1 ms or over POLL_GAP_US."""
    gap = slot_us // 8
    if gap < 3 * read_us:
        gap = 3 * read_us
    if gap < 1000:
        gap = 1000
    return gap if gap < POLL_GAP_US else POLL_GAP_US

# fastadc.Ring.health() bits, which ListRing shares.
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


class ListRing:
    """A ring over an array, its kernels the twins: what the simulator and
    the tests hand a RingReader in place of fastadc.Ring. `push()` writes
    the next entry, as the DMA would."""

    def __init__(self, samples, period_us):
        assert samples & (samples - 1) == 0, "a ring is a power of two"
        self.buf = array("H", [0] * samples)
        self.samples = samples
        self.mask = samples - 1
        self.period_us = period_us
        self.written = 0
        self.fault = 0
        self.running = False

    def push(self, value):
        self.buf[self.written & self.mask] = value
        self.written += 1

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def position(self):
        return self.written

    def health(self):
        out = self.fault
        self.fault &= ~HEALTH_LOST
        return out

    def sum(self, start, n):
        return ref_sum(self.buf, self.mask, start, n)

    def parts(self, start, part, n, out):
        ref_parts(self.buf, self.mask, start, part, n, out)

    def rise(self, start, bin_len, bins, thr, thr_step):
        return ref_rise(self.buf, self.mask, start, bin_len, bins, thr,
                        thr_step)


def _clock(ticks_us):
    return ticks_us if ticks_us is not None else time.ticks_us


class RingReader:
    """The EXACT reader, over a ring (fastadc.Ring on the badge, ListRing in
    simulation). `per_entry` is how many readings one entry sums -- 1 on the
    badge -- and `usable_us` how much history is safe to read; by default
    the ring's length less RING_SAFE_US."""

    exact = True

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


class PolledReader:
    """The POLLED reader: `light()` read into bins of POLL_BIN_US while the
    loop waits. Built once per launch; `start()` clears it for a transfer.
    `add(t, value)` takes a reading at relative time `t` -- what `pump()`
    does, and what the simulator and the protocol's step() shim call."""

    exact = False

    def __init__(self, light=None, ticks_us=None, bins=POLL_BINS,
                 bin_us=POLL_BIN_US):
        assert bins & (bins - 1) == 0, "a ring is a power of two"
        self.light = light
        self.ticks_us = _clock(ticks_us)
        self.bins = bins
        self.mask = bins - 1
        self.bin_us = bin_us
        self.sums = array("i", [0] * bins)
        self.counts = array("H", [0] * bins)
        self.one = [0]
        self.now = 0
        self.last = 0
        self.head = -1          # the bin of the newest reading
        self.newest = 0
        self.lost = self.drift = self.faults = 0
        self.gap_us = POLL_GAP_US
        self.reads = 0          # readings taken, and when the first was
        self.first_at = 0

    def start(self):
        for i in range(self.bins):
            self.sums[i] = 0
            self.counts[i] = 0
        self.head = -1
        self.newest = 0
        self.now = 0
        self.last = self.ticks_us()
        self.gap_us = POLL_GAP_US
        self.reads = self.first_at = 0

    def read_us(self):
        """The mean time between readings so far, us; 0 before two."""
        if self.reads < 2:
            return 0
        return (self.newest - self.first_at) // (self.reads - 1)

    def set_slot(self, slot_us):
        """The rung changed: narrow the lock's gap to suit it."""
        self.gap_us = gap_for(slot_us, self.read_us())

    def stop(self):
        pass

    def check(self):
        return True

    def now_us(self):
        t = self.ticks_us()
        self.now += time.ticks_diff(t, self.last)
        self.last = t
        return self.now

    def add(self, t, value):
        if not self.reads:
            self.first_at = t
        self.reads += 1
        b = t // self.bin_us
        head = self.head
        if b != head:
            if b > head:
                gap = b - head
                if gap > self.bins:
                    gap = self.bins
                for k in range(1, gap + 1):
                    i = (head + k) & self.mask
                    self.sums[i] = 0
                    self.counts[i] = 0
                self.head = b
            elif head - b >= self.bins:
                return
        i = b & self.mask
        self.sums[i] += value
        self.counts[i] += 1
        if t > self.newest:
            self.newest = t

    def pump(self, until):
        """Reads the sensor back to back until `until`, at least once.

        The loop is locals only: a reading in the bin of the one before --
        nearly all of them -- is a call to light(), a clock read and two
        adds; add() runs only when a bin starts. A method call a reading
        would have cost more than the reading [measured-v2: a protocol call
        a reading took the whole pass]."""
        light = self.light
        ticks_us = self.ticks_us
        diff = time.ticks_diff
        bin_us = self.bin_us
        sums = self.sums
        counts = self.counts
        mask = self.mask
        now = self.now
        last = self.last
        head = self.head
        n = 0
        while True:
            value = light()
            t = ticks_us()
            now += diff(t, last)
            last = t
            b = now // bin_us
            if b == head:
                i = b & mask
                sums[i] += value
                counts[i] += 1
                n += 1
            else:
                self.add(now, value)
                head = self.head
            if now >= until:
                break
        self.now = now
        self.last = last
        self.reads += n
        if now > self.newest:
            self.newest = now

    def newest_us(self):
        return self.newest

    def oldest_us(self):
        return (self.head - self.bins + 1) * self.bin_us

    def _range(self, t0, t1):
        """The bins whose centres lie in [t0, t1), clipped to what is kept."""
        half = self.bin_us // 2
        k0 = (t0 - half + self.bin_us - 1) // self.bin_us
        k1 = (t1 - half + self.bin_us - 1) // self.bin_us
        first = self.head - self.bins + 1
        if k0 < first:
            k0 = first
        if k1 > self.head + 1:
            k1 = self.head + 1
        return k0, k1

    def parts(self, t0, t1, n, out, times=None, held=None):
        """As RingReader.parts. A polled part may hold readings from only
        some of its span -- the loop was busy for the rest -- so `times`, if
        given, gets each part's readings' mean time: what the flicker filter
        should correct a part at; and `held`, if given, how many readings
        each part holds."""
        span = (t1 - t0) // n
        used = 0
        sums = self.sums
        counts = self.counts
        mask = self.mask
        bin_us = self.bin_us
        half = bin_us // 2
        for j in range(n):
            a = t0 + j * span
            k0, k1 = self._range(a, a + span)
            s = c = w = 0
            for k in range(k0, k1):
                count = counts[k & mask]
                s += sums[k & mask]
                c += count
                w += count * (k - k0)
            out[j] = s // c if c else -1
            if held is not None:
                held[j] = c
            if times is not None:
                times[j] = (k0 * bin_us + half + w * bin_us // c) if c \
                    else a + span // 2
            used += c
        return used

    def mean(self, t0, t1):
        self.parts(t0, t1, 1, self.one)
        return self.one[0]

    def rise(self, t0, t1, thr, n_thr, step_us):
        """As RingReader.rise, over the bins that hold readings: lit for
        POLL_CONFIRM_US running, the crossing interpolated from the last
        below it to the first at or above -- if that last is within
        `gap_us` of it; otherwise -1, as when there is no rise."""
        k0, k1 = self._range(t0, t1)
        bin_us = self.bin_us
        half = bin_us // 2
        sums = self.sums
        counts = self.counts
        mask = self.mask
        have_dark = False
        dark_c = dark_v = 0
        lit = False
        lit_c = lit_v = lit_thr = 0
        for k in range(k0, k1):
            c = counts[k & mask]
            if not c:
                continue
            v = sums[k & mask] // c
            centre = k * bin_us + half
            i = (centre - t0) // step_us
            if i >= n_thr:
                i = n_thr - 1
            if v >= thr[i]:
                if lit and centre - lit_c >= POLL_CONFIRM_US:
                    if not have_dark or lit_c - dark_c > self.gap_us:
                        return -1
                    if lit_v <= dark_v:
                        return lit_c - half
                    span = lit_c - dark_c
                    off = (lit_thr - dark_v) * span // (lit_v - dark_v)
                    if off < 0:
                        off = 0
                    elif off > span:
                        off = span
                    return dark_c + off
                if not lit:
                    lit = True
                    lit_c = centre
                    lit_v = v
                    lit_thr = thr[i]
            else:
                lit = False
                have_dark = True
                dark_c = centre
                dark_v = v
        return -1
