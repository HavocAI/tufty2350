"""fasttx.Pacer, which plays a fast block on the backlight: what it writes
to the PWM slice and the DMA channel, and that it puts DIV and CC back on
every path. The block crossing a virtual mirror through it is in
test_fastblock.py; the registers themselves are pam_link's, run on the v2
badge [measured-v2, 2026-10-10]."""

from harness import load_module

TX = load_module("share_wifi", "fasttx")


class Mem(dict):
    """machine.mem32 as a dict, with every write logged."""

    def __init__(self, csr=1):
        super().__init__({TX.PWM_CSR: csr, TX.PWM_DIV: 16,
                          TX.PWM_CC: 0x00050000 | 30000, TX.PWM_TOP: 65535})
        self.writes = []

    def __setitem__(self, addr, value):
        self.writes.append((addr, value))
        super().__setitem__(addr, value)


class DMA:
    def __init__(self):
        self.configs = []
        self.running = False
        self.closed = False

    def pack_ctrl(self, size, inc_read, inc_write, treq_sel):
        return {"size": size, "inc_read": inc_read, "inc_write": inc_write,
                "treq_sel": treq_sel}

    def config(self, read, write, count, ctrl, trigger):
        self.configs.append((read, write, count, ctrl, trigger))
        self.running = True

    def active(self, value=None):
        if value is None:
            return self.running
        self.running = bool(value)
        return None

    def close(self):
        self.closed = True


def test_open_sets_the_divider_and_close_puts_div_and_cc_back():
    mem, dma = Mem(), DMA()
    p = TX.Pacer(mem, dma)
    p.open(20)
    assert mem[TX.PWM_DIV] == 20
    p.hold(p.word(5000))
    assert mem[TX.PWM_CC] == 0x00050000 | 5000         # channel B kept
    p.close()
    assert mem[TX.PWM_DIV] == 16 and mem[TX.PWM_CC] == 0x00050000 | 30000
    writes = len(mem.writes)
    p.close()                                          # twice: nothing more
    assert len(mem.writes) == writes and not dma.closed
    p.release()
    assert dma.closed


def test_play_is_word_wide_paced_by_the_wrap_into_cc():
    mem, dma = Mem(), DMA()
    p = TX.Pacer(mem, dma)
    p.open(16)
    p.play("words", 300)
    read, write, count, ctrl, trigger = dma.configs[0]
    assert (read, write, count, trigger) == ("words", TX.PWM_CC, 300, True)
    assert ctrl == {"size": 2, "inc_read": True, "inc_write": False,
                    "treq_sel": 37}
    assert p.busy()
    p.close()
    assert not p.busy() and mem[TX.PWM_DIV] == 16


def test_a_slice_not_running_is_refused_and_nothing_written():
    mem = Mem(csr=0)
    p = TX.Pacer(mem, DMA())
    try:
        p.open(16)
    except RuntimeError:
        pass
    else:
        raise AssertionError("open() took a stopped slice")
    assert not mem.writes
    p.close()
    assert not mem.writes


def test_cc_for_reads_the_firmwares_curve_and_puts_cc_back():
    mem = Mem()
    p = TX.Pacer(mem, DMA())
    p.open(16)

    def backlight(value):
        mem[TX.PWM_CC] = 0x00050000 | int(value ** 2.8 * 65535 + 0.5)
    assert p.cc_for(backlight, 0.5) == int(0.5 ** 2.8 * 65535 + 0.5)
    assert mem[TX.PWM_CC] == 0x00050000 | 30000


def test_a_second_open_keeps_what_the_first_saved():
    mem = Mem()
    p = TX.Pacer(mem, DMA())
    p.open(20)
    p.open(20)
    p.close()
    assert mem[TX.PWM_DIV] == 16


def test_transmit_plays_waits_and_restores():
    mem, dma = Mem(), DMA()
    p = TX.Pacer(mem, dma)
    polls = []

    def wait():
        polls.append(1)
        if len(polls) == 3:
            dma.running = False         # the block is out
    p.transmit(20, "words", 300, wait)
    assert len(polls) == 3 and dma.configs[0][2] == 300
    assert mem[TX.PWM_DIV] == 16 and mem[TX.PWM_CC] == 0x00050000 | 30000


def test_a_cancel_mid_block_puts_div_and_cc_back():
    # the dev link's hard cancel is a KeyboardInterrupt from anywhere:
    # inside busy()'s wait, or inside play() itself
    for where in ("wait", "play"):
        mem, dma = Mem(), DMA()
        p = TX.Pacer(mem, dma)

        def wait():
            mem[TX.PWM_CC] = 0x00050000 | 777           # mid-block
            raise KeyboardInterrupt

        if where == "play":
            def play(read, write, count, ctrl, trigger):
                mem[TX.PWM_CC] = 0x00050000 | 777
                raise KeyboardInterrupt
            dma.config = play
        try:
            p.transmit(20, "words", 300, wait)
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError("the cancel was swallowed")
        assert mem[TX.PWM_DIV] == 16, where
        assert mem[TX.PWM_CC] == 0x00050000 | 30000, where
        assert not dma.running


def test_opened_is_a_context_manager_that_closes_on_an_exception():
    mem, dma = Mem(), DMA()
    p = TX.Pacer(mem, dma)
    try:
        with p.opened(20):
            assert mem[TX.PWM_DIV] == 20
            p.play("w", 4)
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert mem[TX.PWM_DIV] == 16 and not dma.running and p.saved is None


def test_release_twice_closes_the_channel_once():
    closes = []
    dma = DMA()
    dma.close = lambda: closes.append(1)
    p = TX.Pacer(Mem(), dma)
    p.release()
    p.release()
    assert closes == [1]


def test_opened_restores_div_when_open_is_interrupted_after_its_write():
    class Interrupting(Mem):
        def __setitem__(self, addr, value):
            super().__setitem__(addr, value)
            if addr == TX.PWM_DIV and value == 20:
                raise KeyboardInterrupt     # after the write, inside open()
    mem, dma = Interrupting(), DMA()
    p = TX.Pacer(mem, dma)
    try:
        with p.opened(20):
            raise AssertionError("the with block ran")
    except KeyboardInterrupt:
        pass
    assert mem[TX.PWM_DIV] == 16 and mem[TX.PWM_CC] == 0x00050000 | 30000


def test_words_are_built_once_and_outlive_close():
    mem = Mem()
    p = TX.Pacer(mem, DMA())
    try:
        p.word(5)
    except RuntimeError:
        pass
    else:
        raise AssertionError("word() before any open()")
    with p.opened(20):
        low = p.word(5000)
    assert p.saved is None and p.word(5000) == low == 0x00050000 | 5000
    p.cc_for(lambda v: mem.__setitem__(TX.PWM_CC, 0x00050000 | 77), 0.5)
    assert mem[TX.PWM_CC] == 0x00050000 | 30000     # put back, closed too
