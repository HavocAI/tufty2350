"""fastadc.Ring.verify() against a model of PSRAM behind the XIP cache.

verify() is the one check the badge makes that the ring's memory works:
the uncached alias, the cache maintenance that cleans the buffer's cached
lines before the DMA writes under them, and the kernels. Here it runs under
CPython over a model: physical memory, a write-back cache in front of the
cached alias, a maintenance window that cleans by address, and a DMA
channel that writes a reading every 20 us of fake time. The point is that
it catches a maintenance op that does not do what the SDK says -- the
failure it exists for -- and passes one that does.
"""

import gc
import sys
from types import SimpleNamespace

import fakeviper
from harness import load_module

R = load_module("share_wifi", "reader")

CACHED = 0x11000000
NOCACHE = 0x15000000
MAINT = 0x18000000
BUF_AT = 0x11012340          # where the "heap" puts the 64 KB buffer


class Memory:
    """PSRAM by halfword, with a write-back cache in front of CACHED.
    `clean` is what a maintenance write does: "good" writes the line back
    and drops it, "none" ignores it, "keep" writes it back but leaves it
    in the cache dirty -- an op that is not clean-and-invalidate."""

    def __init__(self, clean="good", reading=lambda n: 100 + n % 7):
        self.reading = reading
        self.phys = {}
        self.cache = {}          # cached offset -> value, all dirty
        self.clean = clean

    def read16(self, addr):
        if CACHED <= addr < NOCACHE:
            off = addr - CACHED
            if off in self.cache:
                return self.cache[off]
            return self.phys.get(off, 0)
        return self.phys.get(addr - NOCACHE, 0)

    def write16(self, addr, value):
        if CACHED <= addr < NOCACHE:
            self.cache[addr - CACHED] = value
        else:
            self.phys[addr - NOCACHE] = value

    def maintain(self, addr):
        op = addr & 7
        line = (addr - MAINT - 0x01000000 - op) & ~7    # cached offset
        if op != 3 or self.clean == "none":
            return
        for off in (line, line + 2, line + 4, line + 6):
            if off in self.cache:
                self.phys[off] = self.cache[off]
                if self.clean == "good":
                    del self.cache[off]

    def evict_all(self):
        """The cache churned: every dirty line written back."""
        for off, value in self.cache.items():
            self.phys[off] = value
        self.cache.clear()


class View16:
    def __init__(self, memory, addr):
        self.memory = memory
        self.addr = addr

    def __getitem__(self, i):
        return self.memory.read16(self.addr + 2 * i)

    def __setitem__(self, i, value):
        self.memory.write16(self.addr + 2 * i, value)


class View8:
    def __init__(self, memory, addr):
        self.memory = memory
        self.addr = addr

    def __setitem__(self, i, value):
        self.memory.maintain(self.addr + i)


class FakeDMA:
    def __init__(self, memory, clock):
        self.memory = memory
        self.clock = clock
        self.write = None
        self.written = 0
        self.on = False
        clock.dma = self

    def pack_ctrl(self, **kw):
        return 0

    def config(self, read, write, count, ctrl, trigger):
        self.write = write
        self.count0 = count
        self.written = 0
        self.on = trigger

    def run(self, readings):
        for _ in range(readings):
            i = self.written & 0x3FFF
            self.memory.write16(self.write + 2 * i,
                                self.memory.reading(self.written))
            self.written += 1

    @property
    def count(self):
        return self.count0 - self.written

    def active(self, on=None):
        if on is None:
            return self.on
        self.on = bool(on)

    def close(self):
        pass


class Clock:
    def __init__(self):
        self.us = 0
        self.dma = None

    def sleep_ms(self, ms):
        self.us += ms * 1000
        if self.dma is not None and self.dma.on:
            self.dma.run(ms * 50)

    def ticks_us(self):
        return self.us

    def ticks_diff(self, a, b):
        return a - b


def ring_on(clean, reading=None):
    F = fakeviper.load_fastadc()
    memory = Memory(clean) if reading is None else Memory(clean, reading)
    clock = Clock()
    F.ptr16 = lambda addr: View16(memory, addr)
    F.ptr8 = lambda addr: View8(memory, addr)
    F.time = clock
    regs = {}
    F.machine = SimpleNamespace(mem32=_Regs(regs), mem16=_Mem16(memory))
    sys.modules["uctypes"] = SimpleNamespace(addressof=lambda buf: BUF_AT)
    sys.modules["rp2"] = SimpleNamespace(DMA=lambda: FakeDMA(memory, clock))
    try:
        ring = F.Ring()
    finally:
        sys.modules.pop("uctypes", None)
        sys.modules.pop("rp2", None)
    return F, ring, memory


class _Regs(dict):
    def __missing__(self, key):
        return 0


class _Mem16:
    def __init__(self, memory):
        self.memory = memory

    def __getitem__(self, addr):
        return self.memory.read16(addr)


def verify(clean, reading=None):
    F, ring, memory = ring_on(clean, reading)
    real = gc.collect

    def collect(*args):
        memory.evict_all()
        return real(*args)
    gc.collect = collect
    try:
        return ring.verify(R)
    finally:
        gc.collect = real


def test_a_ring_whose_cache_is_cleaned_passes():
    assert verify("good") is None


def test_a_clean_that_does_nothing_is_caught():
    assert verify("none") == "cache clean did not write back"


def test_a_clean_that_leaves_lines_dirty_is_caught_after_the_churn():
    # Written back, so the first check passes; still dirty, so the churn
    # writes the marker over the DMA's readings.
    assert verify("keep") == "a cached line written back over the ring"


def test_a_dark_room_reading_raw_0_passes():
    # Black in the dark is about raw 2 with two raw steps of noise, so a
    # real reading can be 0: verify() must not take zeros for the cache.
    assert verify("good", lambda n: (0, 2, 4, 1, 3)[n % 5]) is None
