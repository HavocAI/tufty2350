"""Plays a fast block on the backlight: a DMA channel paced by the PWM
slice's wrap writes one CC word a period, so every symbol boundary falls on
this badge's crystal, with the slice's divider set for the common period
(fastblock.COMMON_NS) first. v3's fractional divider (1.25) has not run on
a badge [unverified].

Registers: PWM slice 5, channel A, the CC's high half channel B kept as
found; its wrap is DREQ 37 [datasheet]. The driver writes DIV only at init,
so DIV and CC are saved by open() and put back by close(): use it as a
context manager (`with pacer.opened(div16):`) or through transmit(), since
the dev link's hard cancel can land anywhere and only a `finally` restores
them. A sleep mid-transfer resets the badge, DIV included.

The DMA reads the words through the cached XIP alias the CPU wrote them
through: coherent, no maintenance [inferred-v2].

Handed `machine.mem32` and an `rp2.DMA()`, so its logic is tested under
CPython (tests/share_wifi/test_fasttx.py).
"""

PWM_SLICE = 0x400A8000 + 5 * 0x14
PWM_CSR = PWM_SLICE
PWM_DIV = PWM_SLICE + 0x04
PWM_CC = PWM_SLICE + 0x0C
PWM_TOP = PWM_SLICE + 0x10
DREQ_PWM_WRAP5 = 37


class Pacer:
    """open(div16), then play() a block's words, busy() until it is out,
    and close() however it ends -- `with pacer.opened(div16):`, or
    transmit(), which does all four."""

    def __init__(self, mem, dma):
        self.mem = mem
        self.dma = dma
        self.ctrl = dma.pack_ctrl(size=2, inc_read=True, inc_write=False,
                                  treq_sel=DREQ_PWM_WRAP5)
        self.saved = None
        self.high_half = None       # channel B's half of CC, read once
        self.released = False

    def opened(self, div16):
        """open(div16), as a context manager that closes however its
        block ends -- open() itself included: interrupted after its DIV
        write, it is closed here before the `with` takes over."""
        try:
            self.open(div16)
        except BaseException:
            self.close()
            raise
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def transmit(self, div16, words, n, wait=None):
        """Plays n words and waits until they are out, calling wait()
        between polls (the receiver's binning, say); DIV and CC put back
        whatever happens, Ctrl-C included -- and after every block, so the
        slow rung between blocks (its ACK or NAK, the next Begin) runs on
        the firmware's own divider. The words must end with fill_words()'s
        extra copy of the last: busy() clears when the DMA writes its last
        word, which CC only latches at the next wrap, so without the copy
        close() would overwrite the block's last period unplayed."""
        with self.opened(div16):
            self.play(words, n)
            while self.busy():
                if wait is not None:
                    wait()

    def open(self, div16):
        """Saves DIV and CC and sets DIV. Raises, touching nothing, if the
        slice is not running."""
        mem = self.mem
        if not mem[PWM_CSR] & 1:
            raise RuntimeError("PWM slice 5 not enabled")
        if self.saved is None:
            self.saved = (mem[PWM_DIV] & 0xFFF, mem[PWM_CC])
            if self.high_half is None:
                self.high_half = self.saved[1] & 0xFFFF0000
        mem[PWM_DIV] = div16

    def top(self):
        return self.mem[PWM_TOP] & 0xFFFF

    def word(self, cc):
        """A whole CC word for channel A's `cc`, channel B as the first
        open() found it -- kept after close(), so a transfer builds its low
        and high words once, as ints, and every later block reuses them.
        Raises before the first open()."""
        if self.high_half is None:
            raise RuntimeError("open() first: it reads channel B's half")
        return self.high_half | (cc & 0xFFFF)

    def cc_for(self, set_backlight, value):
        """The CC display.backlight(value) sets: the firmware's own curve.
        CC is put back after. Works open or not."""
        before = self.mem[PWM_CC]
        set_backlight(value)
        cc = self.mem[PWM_CC] & 0xFFFF
        self.mem[PWM_CC] = before
        return cc

    def play(self, words, n):
        self.dma.config(read=words, write=PWM_CC, count=n, ctrl=self.ctrl,
                        trigger=True)

    def busy(self):
        return bool(self.dma.active())

    def hold(self, word):
        """Holds the backlight at a word: between blocks."""
        self.mem[PWM_CC] = word

    def close(self):
        """Stops the channel and puts DIV and CC back. Safe to call twice."""
        self.dma.active(0)
        if self.saved is not None:
            self.mem[PWM_DIV] = self.saved[0]
            self.mem[PWM_CC] = self.saved[1]
            self.saved = None

    def release(self):
        """close(), and the channel given back: the end of a transfer.
        Safe to call twice."""
        self.close()
        if not self.released:
            self.released = True
            self.dma.close()
