"""The light sensor, read straight from the RP2350's ADC by DMA.

`Ring` is what a transfer reads through: the ADC free-running at 50 kHz
into a 32 KB DMA ring, summed by viper kernels; reader.RingReader turns it
into times and means. No CPU time a reading, and a sample clock as exact as
the crystal, where light_level() costs ~14 us a call [measured-v2].

Every address and bit is from the Pico SDK's RP2350 register headers
(hardware/regs/addressmap.h, adc.h, dreq.h) [datasheet]. A wrong one would
fault the chip, so `check()` first confirms the registers hold what a
`read_u16()` just left there -- the one light_level() a launch makes, before
the ring exists.

Badge-only, like __init__.py: it reaches for `machine` and `rp2`. The app
imports it inside a try, so a firmware without viper or rp2.DMA records
why, and does not transfer; the app still runs.
"""

import array
import time

import machine
import micropython

ADC_BASE = 0x400A0000
CS = 0x00
RESULT = 0x04
FCS = 0x08
FIFO = 0x0C
DIV = 0x10
ATOMIC_CLR = 0x3000
# The addresses, summed once: ADC_BASE is over the small-int limit on this
# build (2**30), so each sum would allocate a big int -- and health() runs
# before every block of a transfer.
ADC_CS = ADC_BASE + CS
ADC_RESULT = ADC_BASE + RESULT
ADC_FCS = ADC_BASE + FCS
ADC_FIFO = ADC_BASE + FIFO
ADC_DIV = ADC_BASE + DIV
ADC_CS_CLR = ADC_BASE + ATOMIC_CLR + CS

CS_EN = 0x1
CS_START_ONCE = 0x4
CS_START_MANY = 0x8
CS_READY = 0x100
CS_AINSEL_SHIFT = 12
CS_AINSEL = 0xF000
CS_RROBIN = 0x1FF0000
# What starting a conversion or choosing an input touches: everything else
# in CS -- the temperature sensor's enable among it -- is kept as found.
CS_OURS = CS_AINSEL | CS_RROBIN | CS_START_ONCE | CS_START_MANY

FCS_EN = 0x1
FCS_DREQ_EN = 0x8
FCS_EMPTY = 0x100
FCS_THRESH_SHIFT = 24

DREQ_ADC = 48
ADC_CLOCK_HZ = 48000000

# LIGHT_SENSE is GPIO43, and an RP2350B's ADC inputs are GPIO40-47.
LIGHT_CHANNEL = 3

def check(read_u16):
    """None if the ADC's registers are where this module thinks, else why
    not. `read_u16` is the sensor's ADC.read_u16: it selects the sensor's
    input and converts, so its input, its enable and its result must be
    there to read."""
    value = read_u16()
    cs = machine.mem32[ADC_CS]
    result = machine.mem32[ADC_RESULT]
    if not cs & CS_EN:
        return "ADC CS 0x%08x: not enabled" % cs
    ain = (cs & CS_AINSEL) >> CS_AINSEL_SHIFT
    if ain != LIGHT_CHANNEL:
        return "ADC CS 0x%08x: input %d, not %d" % (cs, ain, LIGHT_CHANNEL)
    if cs & CS_RROBIN:
        return "ADC CS 0x%08x: round robin on" % cs
    if result > 0xFFF:
        return "ADC RESULT 0x%08x: not 12-bit" % result
    # read_u16 is raw << 4 | raw >> 8.
    if value >> 4 != result:
        return "ADC RESULT %d where read_u16 gave %d" % (result, value)
    return None


# Masks over 30 bits are big ints on the badge, which viper code cannot hold
# as constants: they come in as arguments, converted to machine words.
_KEEP = ~CS_OURS & 0xFFFFFFFF
_RESTORE = ~(CS_START_ONCE | CS_START_MANY) & 0xFFFFFFFF


def _drain():
    mem = machine.mem32
    for _ in range(16):
        if mem[ADC_FCS] & FCS_EMPTY:
            return
        mem[ADC_FIFO]


# --- the ring ---------------------------------------------------------------
# A transfer's reader: the ADC converting free-running into its FIFO, and one
# DMA channel writing every result into a 32 KB ring for as long as a
# transfer lasts. A reading's time is its index, at the crystal's rate. See
# section 6 of .agents/optical_data_transfer/spec.md, and
# section 4 of data.md beside it.
#
# Nothing else may touch the ADC while the ring runs: not light_level(), not
# the battery, and not the chip's temperature.
#
# Every address and bit below is cited. Two were new ground on v2: the DMA
# ring fields of pack_ctrl, and reading PSRAM around the cache while DMA
# writes it. verify() establishes both on each launch [verified-v2,
# 2026-10-07a]; a failure is recorded, and the badge does not transfer.

RING_RATE_HZ = 50000
RING_DIV = ADC_CLOCK_HZ // RING_RATE_HZ - 1     # 959: 960 cycles a reading
RING_PERIOD_US = 1000000 // RING_RATE_HZ        # 20
RING_BITS = 15                  # DMA write ring of 2**15 bytes: the largest
RING_BYTES = 1 << RING_BITS
RING_SAMPLES = RING_BYTES // 2  # 16,384 readings, 327.7 ms
RING_MASK = RING_SAMPLES - 1
# The channel's count. RP2350's TRANS_COUNT is 28 bits with MODE (31:28) 0,
# "normal" [datasheet: RP2350 DMA CH0_TRANS_COUNT]: 5,369 s at 50 kHz, and
# re-armed every transfer.
COUNT0 = 0x0FFFFFFF

# The heap is PSRAM on chip select 1, behind the XIP cache [source-v3:
# board/mpconfigboard.cmake]. The same memory without the cache is
# XIP_NOCACHE_NOALLOC_BASE (0x14000000) plus CS1's 16 MB [Pico SDK
# hardware/regs/addressmap.h; st7789.hpp's XIP_PSRAM_NOCACHE]. Every access
# to the ring -- the DMA's writes, the kernels, verify() -- uses that alias,
# so no cache line can hold a stale or dirty copy of it.
XIP_BASE = 0x10000000
XIP_CACHED = 0x11000000
XIP_NOCACHE = 0x15000000
XIP_PSRAM_BYTES = 8 * 1024 * 1024
# Cache maintenance: a byte write to XIP_MAINTENANCE_BASE + (address -
# XIP_BASE) + op, one per 8-byte line. Op 3 is clean by address, which on
# RP2350 also invalidates the line [Pico SDK hardware_xip_cache: xip_cache.c
# xip_cache_maintain(), cache_op_t; XIP_CACHE_LINE_SIZE 8].
XIP_MAINTENANCE = 0x18000000
XIP_LINE = 8
XIP_CLEAN_BY_ADDRESS = 3

# FCS.UNDER (10) and FCS.OVER (11) are sticky, write 1 to clear ("WC");
# EMPTY, FULL and LEVEL are read-only [Pico SDK hardware/regs/adc.h, RP2350].
# They are cleared by writing the ring's own FCS value with them set: a known
# constant, never a read-modify-write of a register that changes under us.
FCS_UNDER = 0x400
FCS_OVER = 0x800
RING_FCS = FCS_EN | FCS_DREQ_EN | (1 << FCS_THRESH_SHIFT)

SENTINEL = 0xFFFF               # no 12-bit reading is this
MARKER = 0xA5C3                 # nor this: verify()'s cache-maintenance check
# health() bits.
HEALTH_ADC = 1                  # the ADC no longer converts input 3, many
HEALTH_LOST = 2                 # FIFO over- or underflow: readings lost
HEALTH_DMA = 4                  # the channel stopped
# verify(): readings it expects the DMA to be short of after VERIFY_WAIT_MS.
VERIFY_WAIT_MS = 2
VERIFY_AHEAD = 50
VERIFY_SPAN = 25
VERIFY_CHURN = 256              # 1 KB blocks allocated and dropped
VERIFY_CHECK = 1024             # readings the kernels are checked on


@micropython.viper
def ring_sum(addr: uint, mask: int, start: int, n: int) -> int:
    buf = ptr16(addr)
    total = 0
    i = 0
    while i < n:
        total += buf[(start + i) & mask]
        i += 1
    return total


@micropython.viper
def ring_parts(addr: uint, mask: int, start: int, part: int, n: int,
               out: ptr32):
    buf = ptr16(addr)
    idx = start
    j = 0
    while j < n:
        total = 0
        k = 0
        while k < part:
            total += buf[idx & mask]
            idx += 1
            k += 1
        out[j] = total
        j += 1


@micropython.viper
def ring_rise(addr: uint, mask: int, start: int, bin_len: int, bins: int,
              thr: ptr32, thr_step: int) -> int:
    # The first bin b such that b and b + 1 both sum to their thresholds,
    # thr[b // thr_step]; -1 if none. No division: viper has none.
    buf = ptr16(addr)
    idx = start
    b = 0
    ti = 0
    tc = 0
    prev = 0
    while b < bins:
        total = 0
        k = 0
        while k < bin_len:
            total += buf[idx & mask]
            idx += 1
            k += 1
        if total >= thr[ti]:
            if prev:
                return b - 1
            prev = 1
        else:
            prev = 0
        b += 1
        tc += 1
        if tc == thr_step:
            tc = 0
            ti += 1
    return -1


@micropython.viper
def ring_fill(addr: uint, mask: int, start: int, n: int, value: int):
    buf = ptr16(addr)
    i = 0
    while i < n:
        buf[(start + i) & mask] = value
        i += 1


@micropython.viper
def ring_count(addr: uint, mask: int, start: int, n: int, value: int) -> int:
    buf = ptr16(addr)
    count = 0
    i = 0
    while i < n:
        if buf[(start + i) & mask] == value:
            count += 1
        i += 1
    return count


@micropython.viper
def _xip_maintain(addr: uint, lines: int):
    p = ptr8(addr)
    i = 0
    while i < lines:
        p[i * 8] = 0
        i += 1


class Ring:
    """The DMA ring, made once per launch. Raises where it cannot be: no
    uctypes or rp2.DMA, the heap not where this module assumes, a refused
    pack_ctrl. Its methods are what reader.RingReader asks of a ring."""

    def __init__(self):
        import rp2
        import uctypes
        # Twice the ring, so a ring-aligned stretch of it exists. Kept for
        # the launch: nothing indexes it -- only its uncached alias is used.
        self.buf = bytearray(2 * RING_BYTES)
        base = uctypes.addressof(self.buf)
        if not XIP_CACHED <= base < XIP_CACHED + XIP_PSRAM_BYTES - 2 * RING_BYTES:
            raise RuntimeError("heap at 0x%08x, not cached PSRAM" % base)
        cached = (base + RING_BYTES - 1) & ~(RING_BYTES - 1)
        self.cached = cached
        self.addr = cached - XIP_CACHED + XIP_NOCACHE
        self.mask = RING_MASK
        self.samples = RING_SAMPLES
        self.period_us = RING_PERIOD_US
        # Dirty every line of it on purpose, through the cached alias, with
        # a value no reading can be: the thing the clean below must write
        # back and drop. verify() then checks the clean wrote it back, and
        # that it never comes back over the DMA's data once the cache has
        # churned -- a direct test of _xip_maintain(), whose op encoding is
        # read from the SDK, not run [unverified].
        ring_fill(cached, RING_MASK, 0, RING_SAMPLES, MARKER)
        # Write back and drop whatever the cache holds of it, so no dirty
        # line lands over the DMA's data.
        _xip_maintain(XIP_MAINTENANCE + cached - XIP_BASE + XIP_CLEAN_BY_ADDRESS,
                      RING_BYTES // XIP_LINE)
        self.dma = rp2.DMA()
        try:
            # The ring fields and high_pri are new API surface even on v2:
            # if pack_ctrl refuses them, give the channel back.
            self.ctrl = self.dma.pack_ctrl(size=1, inc_read=False,
                                           inc_write=True, ring_size=RING_BITS,
                                           ring_sel=True, treq_sel=DREQ_ADC,
                                           high_pri=True)
        except Exception:
            self.dma.close()
            raise
        self.running = False
        self.cs = self.fcs = self.div = 0

    def start(self):
        """Arms the channel with a fresh count and starts the ADC."""
        if self.running:
            self.stop()
        mem = machine.mem32
        self.cs = mem[ADC_CS]
        self.fcs = mem[ADC_FCS]
        self.div = mem[ADC_DIV]
        mem[ADC_DIV] = RING_DIV << 8
        mem[ADC_FCS] = RING_FCS | FCS_OVER | FCS_UNDER
        _drain()
        self.dma.config(read=ADC_FIFO, write=self.addr, count=COUNT0,
                        ctrl=self.ctrl, trigger=True)
        self.running = True
        mem[ADC_CS] = ((self.cs & _KEEP)
                              | (LIGHT_CHANNEL << CS_AINSEL_SHIFT)
                              | CS_EN | CS_START_MANY)

    def stop(self):
        """Puts the ADC back as start() found it. Safe to call twice."""
        if not self.running:
            return
        self.running = False
        mem = machine.mem32
        mem[ADC_CS_CLR] = CS_START_MANY
        for _ in range(100):
            if mem[ADC_CS] & CS_READY:
                break
        self.dma.active(0)
        mem[ADC_FCS] = 0
        _drain()
        mem[ADC_FCS] = self.fcs
        mem[ADC_DIV] = self.div
        mem[ADC_CS] = self.cs & _RESTORE

    def close(self):
        self.stop()
        self.dma.close()

    def position(self):
        """The index the next reading goes to: monotonic, < 2**28."""
        return COUNT0 - self.dma.count

    def health(self):
        """HEALTH_* bits, 0 when well. Clears the FIFO's lost-reading flags."""
        mem = machine.mem32
        out = 0
        cs = mem[ADC_CS]
        if (cs & CS_AINSEL) >> CS_AINSEL_SHIFT != LIGHT_CHANNEL \
                or not cs & CS_START_MANY or not cs & CS_EN:
            out |= HEALTH_ADC
        if mem[ADC_FCS] & (FCS_OVER | FCS_UNDER):
            mem[ADC_FCS] = RING_FCS | FCS_OVER | FCS_UNDER
            out |= HEALTH_LOST
        if not self.dma.active() or not self.dma.count:
            out |= HEALTH_DMA
        return out

    # The kernels, bound to this ring.

    def sum(self, start, n):
        return ring_sum(self.addr, RING_MASK, start, n)

    def parts(self, start, part, n, out):
        ring_parts(self.addr, RING_MASK, start, part, n, out)

    def rise(self, start, bin_len, bins, thr, thr_step):
        return ring_rise(self.addr, RING_MASK, start, bin_len, bins, thr,
                         thr_step)

    def read(self, index):
        """One reading, by the uncached alias. For verify() and Dev Mode."""
        return machine.mem16[self.addr + 2 * (index & RING_MASK)]

    def verify(self, twins):
        """None if the ring works on this badge, else why not. ~0.4 s.

        Checks the cache clean wrote back the marker __init__ dirtied the
        cached alias with; fills the ring with SENTINEL through the uncached
        alias, runs it, and checks that the DMA writes where its count says
        and the CPU sees what it wrote -- at once, and after a wrap; then
        churns the cache and checks the marker has not landed over the
        readings. Then checks each kernel
        against its pure twin in `twins` (reader.py) on readings copied out.
        Leaves the ring stopped."""
        import gc
        addr = self.addr
        # The clean wrote the cached alias's marker back to memory.
        if ring_count(addr, RING_MASK, 0, RING_SAMPLES, MARKER) != RING_SAMPLES:
            return "cache clean did not write back"
        ring_fill(addr, RING_MASK, 0, RING_SAMPLES, SENTINEL)
        try:
            self.start()
            time.sleep_ms(VERIFY_WAIT_MS)
            p = self.position()
            if p < VERIFY_AHEAD or p > RING_SAMPLES // 2:
                return "position %d after %d ms" % (p, VERIFY_WAIT_MS)
            if ring_count(addr, RING_MASK, 0, p, SENTINEL):
                return "stale readings behind the write pointer"
            if ring_count(addr, RING_MASK, p + VERIFY_AHEAD, VERIFY_SPAN,
                          SENTINEL) != VERIFY_SPAN:
                return "writes ahead of the channel's count"
            time.sleep_ms(RING_SAMPLES * RING_PERIOD_US // 1000 + 20)
            p = self.position()
            if p < RING_SAMPLES:
                return "position %d after a wrap" % p
            if ring_count(addr, RING_MASK, 0, RING_SAMPLES, SENTINEL):
                return "stale readings after a wrap"
            # Churn the cache, then look at once -- before the DMA, a wrap
            # every 328 ms, has written over what the churn left: a dirty
            # line the clean missed, evicted over the readings, is the
            # marker, which every line held before the clean. Not zeros: a
            # dark room reads black about raw 2 with two raw steps of noise,
            # so a real reading can be 0.
            gc.collect()
            for _ in range(VERIFY_CHURN):
                bytearray(1024)
            if ring_count(addr, RING_MASK, 0, RING_SAMPLES, MARKER):
                return "a cached line written back over the ring"
            if self.health() & (HEALTH_ADC | HEALTH_DMA):
                return "health %d" % self.health()
            # The kernels against their twins, on a stretch a wrap away from
            # being overwritten.
            start = self.position() - RING_SAMPLES // 2
            values = [self.read(start + i) for i in range(VERIFY_CHECK)]
            got = self.sum(start, VERIFY_CHECK)
            if got != twins.ref_sum(values, VERIFY_CHECK - 1, 0,
                                    VERIFY_CHECK):
                return "ring_sum disagrees"
            out = array.array("i", bytes(4 * 10))
            ref = [0] * 10
            self.parts(start, 100, 10, out)
            twins.ref_parts(values, VERIFY_CHECK - 1, 0, 100, 10, ref)
            if list(out) != ref:
                return "ring_parts disagrees"
            mean = got // VERIFY_CHECK
            thr = array.array("i", [mean * 5] * 200)
            if self.rise(start, 5, 200, thr, 1) != twins.ref_rise(
                    values, VERIFY_CHECK - 1, 0, 5, 200, thr, 1):
                return "ring_rise disagrees"
            return None
        finally:
            self.stop()
