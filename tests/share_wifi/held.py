"""A ring for tests and the simulator, and a Transfer fed a reading at a
time: what the protocol's own reader (reader.RingReader over fastadc.Ring)
reads through, without the badge.

    ListRing      a ring over an array, its kernels reader.py's twins: what
                  the simulator and the tests hand a RingReader in place of
                  fastadc.Ring
    HeldReader    a RingReader over a ListRing that add(t, value) fills
    HeldTransfer  a protocol.Transfer on a HeldReader unless given a reader,
                  with fastblock unless given another, and step(now_ms,
                  light): one reading at a time

Not a test: the runner skips it.
"""

from array import array

from harness import load_module

R = load_module("share_wifi", "reader")
P = load_module("share_wifi", "protocol")
FB = load_module("share_wifi", "fastblock")

# HeldReader's ring: an entry every HELD_ENTRY_US, 409.6 ms of them.
HELD_ENTRY_US = 100
HELD_ENTRIES = 4096


class ListRing:
    """`push()` writes the next entry, as the DMA would. With `length`,
    fewer entries than the array holds, each push also overwrites the entry
    `length` back, so a reading older than the real ring's is gone, as on
    the badge -- whose ring is 16,384 readings, and the simulator's entries
    five of them."""

    def __init__(self, samples, period_us, length=None):
        assert samples & (samples - 1) == 0, "a ring is a power of two"
        assert length is None or 0 < length <= samples
        self.buf = array("H", [0] * samples)
        self.samples = samples
        self.mask = samples - 1
        self.period_us = period_us
        self.length = length
        self.written = 0
        self.fault = 0
        self.running = False

    def push(self, value):
        self.buf[self.written & self.mask] = value
        if self.length is not None and self.written >= self.length:
            self.buf[(self.written - self.length) & self.mask] = value
        self.written += 1

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def position(self):
        return self.written

    def health(self):
        out = self.fault
        self.fault &= ~R.HEALTH_LOST
        return out

    def sum(self, start, n):
        return R.ref_sum(self.buf, self.mask, start, n)

    def parts(self, start, part, n, out):
        R.ref_parts(self.buf, self.mask, start, part, n, out)

    def rise(self, start, bin_len, bins, thr, thr_step):
        return R.ref_rise(self.buf, self.mask, start, bin_len, bins, thr,
                          thr_step)


class HeldReader(R.RingReader):
    """Each value held, an entry every HELD_ENTRY_US, until the next; a
    value is a light_level() count, kept whole (an entry is COUNTS_PER_RAW
    readings of it, in raw units of one count)."""

    def __init__(self):
        self.t = 0
        self.value = 0
        R.RingReader.__init__(self, ListRing(HELD_ENTRIES, HELD_ENTRY_US),
                              self._ticks, per_entry=R.COUNTS_PER_RAW)

    def _ticks(self):
        return self.t

    def add(self, t, value):
        """`value` read at `t` us: the last value fills the entries before
        `t`, and this one the entry at it."""
        ring = self.ring
        while ring.written * HELD_ENTRY_US < t:
            ring.push(self.value)
        self.value = 0 if value < 0 else (0xFFFF if value > 0xFFFF
                                          else value)
        while ring.written * HELD_ENTRY_US <= t:
            ring.push(self.value)
        self.t = t


class HeldTransfer(P.Transfer):
    def __init__(self, leader, payload=None, reader=None, fast=FB):
        if reader is None:
            reader = HeldReader()
            reader.start()
        P.Transfer.__init__(self, leader, payload, reader, fast)

    def step(self, now, light):
        """`light` read at `now` ms, held in the reader's ring until the
        next. Returns the field to show."""
        t = now * 1000
        self.reader.add(t, light)
        self.work(t)
        return self.field_at(t)


def burst_level(transfer, t_us):
    """What a Leader shows at `t_us` while its burst is on air -- the
    backlight scheme's high or low -- or None outside it."""
    if transfer.fast_symbols is None or not transfer.fast_start <= t_us \
            < transfer.fast_end:
        return None
    fast = transfer.fast
    i = (t_us - transfer.fast_start) * 1000 // (transfer.fast_periods
                                                 * fast.COMMON_NS)
    return P.HIGH if transfer.fast_symbols[i] else P.LOW


def run_pair(lead, follow, seen, limit_ms=60000, tamper=None, step_us=100):
    """Two HeldTransfers facing, on one clock, every `step_us`: each reads
    `seen(backlight)` -- counts for the level the other shows, from
    LEVEL_SCHEMES -- and works once its due time has come; the Leader's
    bursts play at their own symbol rate, as the shell's pacer plays them.
    `tamper(t_us, lead_counts, follow_counts)` may change what each reads.
    Either badge may be None: its peer then reads `seen(0.0)`. Returns the
    ms it took."""
    t = 0
    while t < limit_ms * 1000:
        shown = []
        for me in (lead, follow):
            if me is None or me.result:
                shown.append(0.0)
                continue
            burst = me.fast_burst
            if burst is not None and t >= burst[0]:
                me.burst_started(burst[0])
            level = burst_level(me, t) if me.leader else None
            if level is None:
                level = me.field_at(t)
            shown.append(P.LEVEL_SCHEMES[me.scheme][level])
        to_lead, to_follow = seen(shown[1]), seen(shown[0])
        if tamper is not None:
            to_lead, to_follow = tamper(t, to_lead, to_follow)
        for me, light in ((lead, to_lead), (follow, to_follow)):
            if me is None or me.result:
                continue
            me.reader.add(t, light)
            if t >= me.due_us() or t % 1000 == 0:
                me.work(t)
        if all(b.result for b in (lead, follow) if b is not None):
            break
        t += step_us
    return t / 1000.0
