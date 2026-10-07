"""Fast reads of the light sensor, straight from the RP2350's ADC: Dev Mode's
probe uses them to find how fast the badge can read, and whether averaging
many readings beats the sensor's noise.

`badge.light_level()` is `machine.ADC.read_u16()`, ~14 us a call on the badge
over the loop it sits in [measured-v2], where one conversion takes 96 cycles
of the 48 MHz ADC clock, 2 us [datasheet]. Two ways past the call:

    viper   a @micropython.viper loop starting each conversion and waiting
            for it, one register write and a poll a reading
    dma     the ADC converting free-running into its FIFO at a set rate, and
            a DMA channel moving each result to a buffer: no CPU at all, and
            a sample clock as exact as the crystal

Every address and bit is from the Pico SDK's RP2350 register headers
(hardware/regs/addressmap.h, adc.h, dreq.h) [datasheet]. A wrong one would
fault the chip, so `check()` first confirms the registers hold what a
`read_u16()` just left there.

Badge-only, like __init__.py: it reaches for `machine` and `rp2`. The probe
imports it inside a try, so a firmware without viper or rp2.DMA fails that
step, not the app.
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
ATOMIC_SET = 0x2000         # writing here sets bits, and only those
ATOMIC_CLR = 0x3000

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
CONVERSION_CYCLES = 96      # so 500k conversions a second, back to back

# LIGHT_SENSE is GPIO43, and an RP2350B's ADC inputs are GPIO40-47.
LIGHT_CHANNEL = 3

# How long a DMA capture may take beyond its expected length before it is
# abandoned: a capture that never ends would hang the probe.
DMA_SLACK_MS = 50


def check(read_u16):
    """None if the ADC's registers are where this module thinks, else why
    not. `read_u16` is the sensor's ADC.read_u16: it selects the sensor's
    input and converts, so its input, its enable and its result must be
    there to read."""
    value = read_u16()
    cs = machine.mem32[ADC_BASE + CS]
    result = machine.mem32[ADC_BASE + RESULT]
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


@micropython.viper
def _convert(base: uint, keep: uint, restore: uint, buf: ptr16, n: int,
             ain: int):
    adc = ptr32(base)
    start = ptr32(base + 0x2000)        # ATOMIC_SET alias of CS
    cs = adc[0]
    adc[0] = (cs & keep) | (ain << 12) | 1
    i = 0
    while i < n:
        start[0] = 4                    # START_ONCE
        while not (adc[0] & 0x100):     # READY
            pass
        buf[i] = adc[1] & 0xFFF
        i += 1
    adc[0] = cs & restore


def viper_capture(buf, n):
    """`n` single conversions into `buf`, an array('H'). Returns the time
    they took, in us."""
    t = time.ticks_us()
    _convert(ADC_BASE, _KEEP, _RESTORE, buf, n, LIGHT_CHANNEL)
    return time.ticks_diff(time.ticks_us(), t)


def _drain():
    mem = machine.mem32
    for _ in range(16):
        if mem[ADC_BASE + FCS] & FCS_EMPTY:
            return
        mem[ADC_BASE + FIFO]


def dma_capture(buf, n, rate_hz):
    """`n` free-running conversions at `rate_hz` into `buf`, an array('H'),
    by DMA. Returns the time they took, in us, or raises if they did not
    finish. Puts the ADC back as it found it, whatever happens."""
    import rp2
    mem = machine.mem32
    cs_saved = mem[ADC_BASE + CS]
    fcs_saved = mem[ADC_BASE + FCS]
    div_saved = mem[ADC_BASE + DIV]
    div = ADC_CLOCK_HZ // rate_hz - 1
    if div <= CONVERSION_CYCLES - 1:
        # Back to back. A divider of a conversion's own 96 cycles gave half
        # the rate asked, 249k a second for 500k [measured-v2]: a start
        # falling as the last conversion ends seems to be missed.
        div = 0
    dma = rp2.DMA()
    elapsed = None
    try:
        mem[ADC_BASE + DIV] = div << 8
        mem[ADC_BASE + FCS] = FCS_EN | FCS_DREQ_EN | (1 << FCS_THRESH_SHIFT)
        _drain()
        ctrl = dma.pack_ctrl(size=1, inc_read=False, inc_write=True,
                             treq_sel=DREQ_ADC)
        dma.config(read=ADC_BASE + FIFO, write=buf, count=n, ctrl=ctrl,
                   trigger=True)
        limit_ms = n * 1000 // rate_hz + DMA_SLACK_MS
        t = time.ticks_us()
        mem[ADC_BASE + CS] = ((cs_saved & _KEEP)
                              | (LIGHT_CHANNEL << CS_AINSEL_SHIFT)
                              | CS_EN | CS_START_MANY)
        while dma.active():
            if time.ticks_diff(time.ticks_us(), t) > limit_ms * 1000:
                break
        else:
            elapsed = time.ticks_diff(time.ticks_us(), t)
    finally:
        mem[ADC_BASE + ATOMIC_CLR + CS] = CS_START_MANY
        for _ in range(100):
            if mem[ADC_BASE + CS] & CS_READY:
                break
        dma.active(0)
        dma.close()
        mem[ADC_BASE + FCS] = 0
        _drain()
        mem[ADC_BASE + FCS] = fcs_saved
        mem[ADC_BASE + DIV] = div_saved
        mem[ADC_BASE + CS] = cs_saved & _RESTORE
    if elapsed is None:
        raise RuntimeError("DMA did not finish in %d ms" % limit_ms)
    return elapsed


def buffer(n):
    return array.array("H", bytes(2 * n))
