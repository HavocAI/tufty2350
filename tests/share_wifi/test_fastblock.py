"""fastblock.py, the fast block (protocol spec Appendix A): the encoder,
the decoder (fastmodel.py's linear receiver in whole numbers, a viper
kernel) and the rungs.

Four runs of the decoder, from weakest to strongest:

- round trips on fastmodel.py's channel model, under CPython, where the
  kernel runs as Python (fastblock's own stubs);
- the transmitter (fasttx.Pacer) through optic_fake's virtual badge -- PWM
  slice, wrap-paced DMA, ring, sensor -- binned as it runs by RingReader.bins()'s
  twin, the grid anchored by the trigger's ring index;
- the kernel under MicroPython's unix port, whose x64 viper is real: the
  same output, bit for bit, and nothing allocated a call (MICROPYTHON);
- the mirror captures of 2026-10-10
  (.agents/optical_data_transfer/captures/2026-10-10/, local, not in git:
  skipped where absent), against fastmodel.py's own decode.
"""

import binascii
import glob
import os
import random
import shutil
import subprocess
import tempfile
import zlib
from array import array

import fakeviper
import fastmodel as tp
from harness import APPS, REPO, Skipped, load_module

FB = load_module("share_wifi", "fastblock")
APP = os.path.join(APPS, "share_wifi")
CAPTURES = os.path.join(REPO, ".agents", "optical_data_transfer", "captures",
                        "2026-10-10")


def _tool(env, name):
    path = os.environ.get(env) or shutil.which(name)
    return path if path and os.path.exists(path) else None


def dark(noise=0.3):
    """The dark room's two-level fit, rounded (as fitted on 2026-10-09f)."""
    return tp.Model([2.74, 8.26], y0=1.6, K=2.3, r_min=0.02, delay_us=40.0,
                    noise=noise, low=0.5)


def lit(noise=0.3):
    """Faster at the low: the lit room's shape, roughly."""
    return tp.Model([3.2, 8.5], y0=0.5, K=2.3, r_min=0.3, delay_us=40.0,
                    noise=noise, low=0.5)


def channel(symbols, rng, model=None, flicker=None, periods=2):
    """(bins, start in Q16 bins): symbols through the model, binned as the
    ring is (sums of 5 readings)."""
    burst = tp.pam_burst(model or dark(), list(symbols), periods, rng,
                         noise=(model or dark()).noise, flicker=flicker)
    bins = array("H", [max(0, int(round(v * 5))) for v in burst.y])
    return bins, int(round(burst.edges[0] * 65536))


def decode(bins, start, avail=None, step=0, **kw):
    d = FB.Decoder()
    d.begin(start, FB.sym_q16(kw.pop("periods", 2)), **kw)
    n = len(bins) if avail is None else avail
    if not step:
        return d, d.feed(bins, n)
    got = 0
    while True:
        got = min(n, got + step)
        status = d.feed(bins, got)
        if status != FB.NEED or got == n:
            return d, status


def payload_of(rng, n):
    return bytes(rng.randrange(256) for _ in range(n))


def ref_bins(buf, mask, start, n, out, at, width=FB.BIN_READINGS):
    """out[at + j] = the sum of `width` ring readings from start + j * width,
    wrapping at mask: the virtual mirror's binning, as the badge's
    RingReader.bins() sums it."""
    idx = start
    for j in range(n):
        total = 0
        for _ in range(width):
            total += buf[idx & mask]
            idx += 1
        out[at + j] = total


# --- the clock, the rungs, the frame ---------------------------------------------


def test_common_div_on_either_clock_and_refused_where_none_reaches():
    assert FB.common_div(200000000) == (16, None)        # v2: the driver's 1.0
    assert FB.common_div(250000000) == (20, None)        # v3: 1.25
    div, why = FB.common_div(266000000)
    assert div is None and "266000000 Hz" in why and "323" in why


def test_symbol_widths_in_q16_bins():
    assert FB.sym_q16(2) == round(6.5536 * 65536)
    assert FB.sym_q16(3) == round(9.8304 * 65536)


def test_every_rung_keeps_its_worst_block_under_a_second():
    assert [FB.block_bytes(p) for p in FB.RUNGS] == [128, 64, 32, 32]
    for periods in FB.RUNGS:
        size = FB.block_bytes(periods)
        worst = FB.encode(65535, bytes(size))           # all lows: most stuffing
        assert len(worst) * periods * FB.COMMON_NS <= FB.MAX_AIR_NS, periods
        bits = FB.frame_bits_max(periods)
        room = FB.MAX_AIR_NS // (periods * FB.COMMON_NS) - FB.PREAMBLE_LEN \
            - FB.TAIL
        assert bits + (bits - 1) // 4 <= room < bits + 1 + bits // 4


def test_prbs_is_the_mirror_captures_sequence():
    # pam_link's sequence (xorshift32 from 0x2545F493, runs of the low
    # capped), as the captures of 2026-10-10 were recorded with: the
    # subtool went on 2026-10-19, its first 64 symbols pinned here.
    pinned = (((1, 4), "10111101101011000011101011100001"
                       "01011011111110110100110111001110"),
              ((3, 0), "00001111111100101111101011101100"
                       "11101111001010101101100000001000"))
    for (seed, lows), want in pinned:
        got = FB.prbs(64, 0x2545F491 + 2 + 1000 * seed, lows)
        assert "".join(str(int(x)) for x in got) == want, (seed, lows)


def test_the_frame_is_big_endian_with_ieee_crc32():
    f = FB.frame(0x0102, b"abc")
    assert f[:4] == b"\x01\x02\x00\x03" and f[4:7] == b"abc"
    assert int.from_bytes(f[7:], "big") == zlib.crc32(f[:7])


def test_encoding_bounds_the_lows_and_destuffs_to_the_frame():
    rng = random.Random(1)
    for n in (0, 1, 31, 128):
        for data in (payload_of(rng, n), bytes(n), b"\xff" * n):
            syms = FB.encode(9, data)
            known = len(FB.PREAMBLE)
            assert syms[:known] == FB.PREAMBLE and FB.PREAMBLE[-1] == 1
            assert syms[-FB.TAIL:] == b"\x01" * FB.TAIL
            run = longest = 0
            for s in syms:
                run = 0 if s else run + 1
                longest = max(longest, run)
            assert longest <= FB.MAX_LOWS
            bits, run = [], 0
            for s in syms[known:len(syms) - FB.TAIL]:
                if run == FB.MAX_LOWS:
                    assert s == 1
                    run = 0
                    continue
                bits.append(s)
                run = 0 if s else run + 1
            want = FB.frame(9, data)
            assert len(bits) == 8 * len(want)
            assert bytes(int("".join(map(str, bits[i:i + 8])), 2)
                         for i in range(0, len(bits), 8)) == want
            assert len(syms) == known + FB.stuffed_len(len(want), want) \
                + FB.TAIL


def test_fill_words_plays_each_symbol_for_its_periods():
    words = array("I", [0] * 12)
    assert FB.fill_words(words, b"\x01\x00\x01", 3, 0x50007, 0x5FFFF) == 10
    assert list(words[:10]) == [0x5FFFF] * 3 + [0x50007] * 3 \
        + [0x5FFFF] * 4                 # the last again: see fill_words()


def test_flicker_mhz_is_twice_the_filters_line_and_its_drift():
    assert FB.flicker_mhz(0, 12345) == 0
    assert FB.flicker_mhz(60, 0) == 120000
    assert FB.flicker_mhz(50, 0) == 100000
    # 320 steps a line cycle: 32000 thousandths of a step a second is a
    # line 0.1 Hz fast, the flicker 0.2
    assert FB.flicker_mhz(50, 32000) == 100200
    assert FB.flicker_mhz(50, -32000) == 99800


def test_flicker_mhz_from_a_real_flicker_filter_fit():
    # A room flickering at twice the line and four times it, as
    # test_flicker_frames.py's; the filter fits the line, the regressors
    # get the light's fundamental.
    import math
    P = load_module("share_wifi", "protocol")
    for line, want in ((60, 120000), (50, 100000)):
        ts = list(range(1000, 161000, 2000))
        vs = []
        for t in ts:
            c = 2 * math.pi * line * t / 1e6
            vs.append(int(round(88 + 22 * math.sin(2 * c + 0.7)
                                + 9 * math.sin(4 * c + 2.1))))
        f = P.FlickerFilter()
        assert f.fit(ts, vs, [2000] * len(ts)) == "flicker"
        assert f.mains == line
        assert FB.flicker_mhz(f.mains, f.rate_m) == want


def test_regressors_at_the_lights_fundamental_take_off_its_harmonic():
    # 120 + 240 Hz flicker, 1.5 raw: at the line (60 Hz) the regressors
    # cannot reach 240; at the light's fundamental they do.
    def errors(hz_mhz):
        total = 0
        for seed in range(1, 9):
            rng = random.Random(seed)
            levels, _ = tp.rll_levels(1270, rng, 4)
            burst = tp.pam_burst(dark(), levels, 2, rng, noise=0.3,
                                 flicker=(1.5, 120.0))
            bins = array("H", [max(0, int(round(v * 5))) for v in burst.y])
            d = FB.Decoder(len(levels) + 4)
            d.begin(int(round(burst.edges[0] * 65536)), FB.sym_q16(2),
                    known=bytes(levels[:67]), flicker=hz_mhz,
                    raw_end=len(levels) - 1)
            assert d.feed(bins, len(bins)) == FB.END
            total += sum(d.out[i] != levels[i]
                         for i in range(67, len(levels) - 1))
        return total
    assert errors(FB.flicker_mhz(60, 0)) <= 2
    assert errors(FB.flicker_mhz(60, 0) // 2) > errors(FB.flicker_mhz(60, 0))


# --- round trips on the model -------------------------------------------------------


def test_blocks_round_trip_on_the_dark_and_lit_models_over_eight_seeds():
    for model in (dark(), lit()):
        for seed in range(1, 9):
            rng = random.Random(seed)
            data = payload_of(rng, 128)
            bins, start = channel(FB.encode(seed, data), rng, model)
            d, status = decode(bins, start)
            assert status == FB.OK, (seed, status)
            assert (d.chunk, d.payload()) == (seed, data)


def test_a_block_of_lows_and_an_empty_block_round_trip():
    rng = random.Random(2)
    for data in (bytes(128), b""):
        bins, start = channel(FB.encode(3, data), rng)
        d, status = decode(bins, start)
        assert status == FB.OK and d.payload() == data


def test_decoding_as_bins_arrive_is_decoding_at_the_end():
    rng = random.Random(4)
    data = payload_of(rng, 96)
    bins, start = channel(FB.encode(1, data), rng, flicker=(1.0, 120.0))
    one, s1 = decode(bins, start, flicker=120000)
    many, s2 = decode(bins, start, step=7, flicker=120000)
    assert s1 == s2 == FB.OK
    assert list(one.w) == list(many.w) and one.out == many.out


def test_needs_bins_until_the_frame_is_in():
    rng = random.Random(5)
    bins, start = channel(FB.encode(1, payload_of(rng, 64)), rng)
    d = FB.Decoder()
    d.begin(start, FB.sym_q16(2))
    assert d.feed(bins, d.bins_needed(len(FB.PREAMBLE))) == FB.NEED
    assert not d.trained
    assert d.feed(bins, len(bins) // 2) == FB.NEED and d.trained
    assert d.feed(bins, len(bins)) == FB.OK


def test_a_corrupted_block_fails_its_crc_or_its_stuffing():
    rng = random.Random(6)
    data = payload_of(rng, 128)
    syms = FB.encode(1, data)
    seen = set()
    for flip in range(len(FB.PREAMBLE) + 40, len(syms) - 3, 97):
        bad = bytearray(syms)
        bad[flip] ^= 1
        bins, start = channel(bad, random.Random(flip), dark(0.0))
        _, status = decode(bins, start)
        assert status in (FB.ERR_CRC, FB.ERR_STUFF, FB.ERR_LENGTH), flip
        seen.add(status)
    assert FB.ERR_CRC in seen


def test_a_length_past_the_rungs_is_refused_at_the_header():
    rng = random.Random(7)
    bins, start = channel(FB.encode(1, payload_of(rng, 128)), rng)
    _, status = decode(bins, start, max_len=64)
    assert status == FB.ERR_LENGTH


def test_a_decoder_too_small_for_the_longest_block_refuses_it():
    worst = len(FB.PREAMBLE) + FB.stuffed_len(FB.HEADER + 128 + FB.CRC) \
        + FB.TAIL                                       # every bit a 0
    assert worst >= len(FB.encode(1, bytes(128)))
    small = FB.Decoder(symbols=worst)                  # needs worst + 1
    try:
        small.begin(0, FB.sym_q16(2), max_len=128)
    except ValueError:
        pass
    else:
        raise AssertionError("a decoder short of the worst case")
    FB.Decoder(symbols=worst + 1).begin(0, FB.sym_q16(2), max_len=128)
    small.begin(0, FB.sym_q16(2), max_len=64)


def _first_stuffed_one(syms):
    zeros = 0
    for i in range(len(FB.PREAMBLE), len(syms)):
        if zeros == FB.MAX_LOWS:
            return i
        zeros = 0 if syms[i] else zeros + 1
    raise AssertionError("no stuffed bit")


def test_a_stuffing_violation_is_decided_before_the_block_is_in():
    rng = random.Random(17)
    syms = bytearray(FB.encode(1, bytes(32) + payload_of(rng, 96)))
    at = _first_stuffed_one(syms)
    syms[at] = 0
    bins, start = channel(syms, rng, dark(0.0))
    d, status = decode(bins, start, step=7)
    assert status == FB.ERR_STUFF
    assert d.st[FB._I] < len(syms) // 2 and d.st[FB._AVAIL] < len(bins) // 2
    try:
        d.payload()
    except ValueError:
        pass
    else:
        raise AssertionError("payload() of a failed block")


def test_a_long_header_is_decided_at_the_header():
    rng = random.Random(18)
    bins, start = channel(FB.encode(1, payload_of(rng, 128)), rng)
    d, status = decode(bins, start, step=7, max_len=64)
    assert status == FB.ERR_LENGTH
    assert d.st[FB._AVAIL] < len(bins) // 3


def test_a_crc_failure_leaves_no_payload():
    rng = random.Random(19)
    syms = bytearray(FB.encode(1, payload_of(rng, 64)))
    at = len(syms) - FB.TAIL - 3            # inside the CRC
    syms[at] ^= 1
    bins, start = channel(syms, rng, dark(0.0))
    d, status = decode(bins, start)
    if status == FB.ERR_CRC:
        assert d.chunk is None and d.length is None
        try:
            d.payload()
        except ValueError:
            return
        raise AssertionError("payload() after ERR_CRC")
    assert status == FB.ERR_STUFF, status


def test_flat_bins_cannot_train():
    d = FB.Decoder()
    d.begin(65536 * 10, FB.sym_q16(2))
    assert d.feed(array("H", [20] * 9000), 9000) == FB.ERR_TRAIN


def test_the_slower_rungs_round_trip():
    for periods in FB.RUNGS[1:]:
        rng = random.Random(periods)
        data = payload_of(rng, FB.block_bytes(periods))
        bins, start = channel(FB.encode(2, data), rng, periods=periods)
        d, status = decode(bins, start, periods=periods)
        assert status == FB.OK and d.payload() == data, periods


def test_the_kernels_value_is_the_python_value():
    rng = random.Random(9)
    bins, start = channel(FB.encode(1, payload_of(rng, 32)), rng,
                          flicker=(1.0, 120.0))
    d = FB.Decoder()
    d.begin(start, FB.sym_q16(2), flicker=120000, track=False)
    d.feed(bins, d.bins_needed(len(FB.PREAMBLE) + 1))
    d.st[FB._AVAIL] = len(bins)
    for _ in range(30):
        i = d.st[FB._I]
        d.st[FB._END] = i + 1
        FB._run(d, bins)                # fills i + 1's parts, decides i
        assert d.st[FB._I] == i + 1 and d.st[FB._V] == d.value(i)


# --- mains flicker: fastmodel's tracked regressors -----------------------------------


def _flicker_errors(hz_error, track, seeds=range(1, 9)):
    """(fastblock, fastmodel) errors over seeds of 2000 F0 symbols in 1.0
    raw of 120 Hz flicker, the receivers told 120 + hz_error (None: no
    regressors)."""
    ours = theirs = 0
    for seed in seeds:
        rng = random.Random(seed)
        levels, _ = tp.rll_levels(2070, rng, 4)
        burst = tp.pam_burst(dark(), levels, 2, rng, noise=0.3,
                             flicker=(1.0, 120.0))
        burst.flicker_hz = None if hz_error is None else 120.0 + hz_error
        burst.track = tp.TRACK_MU if track else 0.0
        train, test = tp.symbol_range(burst, 64)
        got, _ = tp.linear(burst, train, test)
        theirs += sum(got[i] != levels[i] for i in test)
        bins = array("H", [max(0, int(round(v * 5))) for v in burst.y])
        d = FB.Decoder(len(levels) + 4)
        hz = 0 if hz_error is None else int(round((120 + hz_error) * 1000))
        d.begin(int(round(burst.edges[0] * 65536)), FB.sym_q16(2),
                known=bytes(levels[:67]), flicker=hz,
                raw_end=len(levels) - 1, track=track)
        assert d.feed(bins, len(bins)) == FB.END
        ours += sum(d.out[i] != levels[i] for i in range(67, len(levels) - 1))
    return ours, theirs


def test_tracked_regressors_hold_a_mains_estimate_0_3_hz_off():
    # fastmodel.py: fixed regressors need the mains to ~0.03 Hz; tracked
    # ones hold to 0.3 Hz and beyond. The kernel's whole-number LMS gives
    # what fastmodel's float one gives, within a couple of symbols.
    ours, theirs = _flicker_errors(0.3, True)
    assert ours <= 2 and theirs <= 2, (ours, theirs)
    fixed, fixed_tp = _flicker_errors(0.3, False)
    assert fixed > 100 and abs(fixed - fixed_tp) <= 0.1 * fixed_tp, \
        (fixed, fixed_tp)
    plain, plain_tp = _flicker_errors(None, False)
    assert plain > 20 and abs(plain - plain_tp) <= 10 + 0.2 * plain_tp, \
        (plain, plain_tp)


def test_tracking_does_no_harm_without_flicker():
    rng = random.Random(11)
    data = payload_of(rng, 128)
    bins, start = channel(FB.encode(1, data), rng)
    d, status = decode(bins, start, flicker=120000)
    assert status == FB.OK and d.payload() == data


# --- the transmitter through the virtual badge ---------------------------------------


class _Divided:
    """optic_fake's slice with a writable, fractional divider, as the
    RP2350's is [datasheet] (as fitted on 2026-10-09f)."""

    def __enter__(self):
        import optic_fake as of
        self.of = of
        self.saved = of.Mem.__setitem__, of.Badge.period
        set_item = self.saved[0]

        def setitem(mem, addr, value):
            if addr == of.PWM_DIV:
                mem.badge.advance(of.COST_US["mem"])
                mem.badge.div = value & 0xFFF
                return
            set_item(mem, addr, value)

        def period(badge):
            return (badge.top + 1) * (badge.div / 16.0) * 1e6 / badge.sys_hz
        of.Mem.__setitem__ = setitem
        of.Badge.period = period
        return self

    def __exit__(self, *exc):
        self.of.Mem.__setitem__, self.of.Badge.period = self.saved


def send_on_virtual_badge(data, room=0.0, sys_hz=200000000, seed=1,
                          flicker=0, firmware="v2"):
    """Plays a block with fasttx through optic_fake's mirror, bins the ring
    as it runs and decodes as the bins arrive. (status, decoder, badge,
    whether the DMA was still busy at the end)."""
    import optic_fake as of
    TX = load_module("share_wifi", "fasttx")
    badge = of.Badge(seed=seed, room=room, sys_hz=sys_hz, firmware=firmware)
    badge.cc_buf = badge.cc = 0x00050000 | 30000
    with _Divided():
        mem = of.Mem(badge)
        of.DMA.badge = badge
        pacer = TX.Pacer(mem, of.DMA())
        div16, _ = FB.common_div(sys_hz, mem[of.PWM_TOP])
        with pacer.opened(div16):
            low = pacer.cc_for(badge.backlight, 0.5)
            syms = FB.encode(4, data)
            words = array("I", [0] * (2 * len(syms) + 1))
            n = FB.fill_words(words, syms, 2, pacer.word(low),
                              pacer.word(65535))
            pacer.hold(pacer.word(low))
            ring = badge.ring
            badge.running = True
            first = ring.position()
            badge.advance(20000)
            before = ring.position() - first
            ctr0 = mem[of.PWM_CTR]
            pacer.play(words, n)
            # the landing, as fastmodel.pam_link_bursts has it: the first
            # word goes out at the first wrap after the trigger, latches at
            # the next
            tick = (div16 / 16.0) * 1e6 / sys_hz
            land = before * 20 + (65536 - ctr0) * tick + 65536 * tick
            start = int(round(land / 100.0 * 65536))
            d = FB.Decoder()
            d.begin(start, FB.sym_q16(2), flicker=flicker)
            bins = array("H", [0] * 15000)
            nb = 0
            done = first
            status = FB.NEED
            while status == FB.NEED and nb < len(bins):
                badge.advance(1000)
                k = (ring.position() - done) // 5
                ref_bins(ring.addr, ring.mask, done, k, bins, nb)
                nb += k
                done += 5 * k
                status = d.feed(bins, nb)
            for _ in range(10):             # the tail's last periods
                if not pacer.busy():
                    break
                badge.advance(1000)
            busy = pacer.busy()
        badge.running = False
    return status, d, badge, busy


def test_a_block_crosses_the_virtual_mirror_dark_and_lit_on_either_clock():
    # Either clock: the timing only, v2's backlight curve on both.
    rng = random.Random(12)
    data = payload_of(rng, 128)
    for room, sys_hz in ((0.0, 200000000), (256.0, 200000000),
                         (0.0, 250000000), (256.0, 250000000)):
        status, d, badge, busy = send_on_virtual_badge(data, room, sys_hz)
        assert status == FB.OK and d.payload() == data, (room, sys_hz, status)
        assert d.chunk == 4 and not busy
        # put back: DIV to the driver's 1.0, CC as found
        assert badge.div == 16 and badge.cc_buf == 0x00050000 | 30000


def test_f0_crosses_the_virtual_mirror_with_v3s_backlight_floor():
    # v3's backlight lifts the low 0.5 from 14.3% duty to 25% (its 12.5%
    # floor): less contrast, a faster sensor at the low. In optic_fake's
    # sensor (simulator.py's) every block of 8 seeds passes dark and lit;
    # v2's curve there passes 12 of 16 dark (stuffing failures, the dark
    # tail), against 0 errors in 32496 on the mirror -- so this is the
    # model's verdict, not the badge's. v3 optics on hardware: unmeasured.
    for room in (0.0, 256.0):
        for seed in range(1, 9):
            data = payload_of(random.Random(seed), 128)
            status, d, badge, _ = send_on_virtual_badge(
                data, room, 250000000, seed=seed, firmware="v3")
            assert status == FB.OK and d.payload() == data, (room, seed)
            assert badge.div == 16


def test_transmit_plays_the_blocks_last_period_whole():
    # CC is double buffered: a word the DMA writes latches at the next
    # wrap. Every latch is logged; the block's last word, a marker, must
    # hold one whole period before CC returns to what was saved -- over
    # two blocks, the second's words built from the first's ints.
    import optic_fake as of
    TX = load_module("share_wifi", "fasttx")
    badge = of.Badge(seed=1)
    badge.cc_buf = badge.cc = 0x00050000 | 30000
    latched = []
    wrap = badge.wrap

    def logged():
        wrap()
        latched.append((badge.t, badge.cc))
    badge.wrap = logged
    with _Divided():
        mem = of.Mem(badge)
        of.DMA.badge = badge
        pacer = TX.Pacer(mem, of.DMA())
        with pacer.opened(16):
            low = pacer.word(pacer.cc_for(badge.backlight, 0.5))
        marker = pacer.word(40000)          # built once, kept as ints
        for _ in range(2):
            words = array("I", [0] * 8)
            n = FB.fill_words(words, b"\x00\x00\x01", 2, low, marker)
            latched.clear()
            pacer.transmit(16, words, n)
            badge.advance(2000)
            assert badge.div == 16 and badge.cc_buf == 0x00050000 | 30000
            ccs = [cc for _, cc in latched]
            held = [i for i, cc in enumerate(ccs) if cc == marker]
            assert len(held) >= 2, ccs      # both periods of the last symbol
            end = held[-1]
            assert ccs[end + 1] == 0x00050000 | 30000, ccs
            period = latched[end + 1][0] - latched[end][0]
            assert abs(period - 327.68) < 1e-6


# --- the kernel under real viper, and nothing allocated --------------------------------

UNIX_SCRIPT = r"""
import sys, micropython
sys.path.insert(0, %(app)r)
import fastblock as fb
from array import array
raw = open(%(data)r, "rb").read()
n = int.from_bytes(raw[:4], "little")
start = int.from_bytes(raw[4:8], "little")
flk = int.from_bytes(raw[8:12], "little")
end = int.from_bytes(raw[12:16], "little")
known = raw[16:16 + 67] if end else fb.PREAMBLE
bins = array("H", raw[16 + (67 if end else 0):])
d = fb.Decoder(%(cap)d)
d.begin(start, fb.sym_q16(2), known=known, flicker=flk, raw_end=end)
got = 0
alloc = 0
calls = 0
while True:
    got = min(n, got + 7)
    trained = d.trained
    before = micropython.mem_total()
    r = d.feed(bins, got)
    if r != fb.NEED:
        break
    if trained:
        alloc += micropython.mem_total() - before
        calls += 1
print(r, calls, alloc)
print(list(d.w))
print(bytes(d.out[:end] if end else d.out[:200]).hex())
"""


def _unix_decode(bins, start, flicker=0, known=None, end=0, cap=1600):
    tool = _tool("MICROPYTHON", "micropython")
    if tool is None:
        raise Skipped("no MicroPython unix port: set MICROPYTHON")
    with tempfile.TemporaryDirectory() as out:
        data = os.path.join(out, "bins")
        with open(data, "wb") as handle:
            handle.write(len(bins).to_bytes(4, "little")
                         + start.to_bytes(4, "little")
                         + flicker.to_bytes(4, "little")
                         + end.to_bytes(4, "little")
                         + (bytes(known) if end else b"") + bytes(bins))
        script = os.path.join(out, "k.py")
        with open(script, "w") as handle:
            handle.write(UNIX_SCRIPT % {"app": APP, "data": data, "cap": cap})
        result = subprocess.run([tool, "-X", "heapsize=8M", script],
                                capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout
    status, calls, alloc = result.stdout.split("\n")[0].split()
    lines = result.stdout.split("\n")
    return int(status), int(calls), int(alloc), lines[1], lines[2]


def test_the_kernel_under_real_viper_matches_and_allocates_nothing():
    rng = random.Random(13)
    data = payload_of(rng, 128)
    bins, start = channel(FB.encode(5, data), rng, flicker=(1.0, 120.0))
    status, calls, alloc, w, out = _unix_decode(bins, start, 120000)
    d, mine = decode(bins, start, flicker=120000)
    assert status == mine == FB.OK
    assert calls > 1000 and alloc == 0, (calls, alloc)
    assert w == str(list(d.w))
    assert out == bytes(d.out[:200]).hex()


def test_fastblock_compiles_for_the_badge():
    tool = _tool("MPY_CROSS", "mpy-cross")
    if tool is None:
        raise Skipped("no mpy-cross: set MPY_CROSS")
    with tempfile.TemporaryDirectory() as out:
        for name in ("fastblock.py", "fasttx.py"):
            result = subprocess.run(
                [tool, "-march=armv7emsp", "-o", os.path.join(out, "f.mpy"),
                 os.path.join(APP, name)], capture_output=True, text=True)
            assert result.returncode == 0, (name, result.stderr)


# --- the measured channel: the mirror captures of 2026-10-10 --------------------------


def _captures():
    paths = sorted(glob.glob(os.path.join(CAPTURES, "*", "pam_link.*.bin")))
    if not paths:
        raise Skipped("no mirror captures in "
                      ".agents/optical_data_transfer/captures/2026-10-10/")
    out = []
    for path in paths:
        for head, burst in tp.pam_link_bursts(path, search=False):
            if head["k"] == 2:
                out.append((os.path.basename(os.path.dirname(path)), head,
                            burst))
    return out


def _decode_capture(burst, shifts=None):
    """Raw decode of a capture: trained on its first 67 symbols, every
    symbol from 67 to n - 2 decided, as fastmodel scores them."""
    n = len(burst.levels)
    syms = bytes(burst.levels)
    bins = array("H", [int(round(v * 5)) for v in burst.y])
    d = FB.Decoder(n + 4)
    d.begin(int(round(burst.edges[0] * 65536)),
            int(round((burst.edges[1] - burst.edges[0]) * 65536)),
            known=syms[:67], raw_end=n - 1, shifts=shifts)
    assert d.feed(bins, len(bins)) == FB.END
    return sum(d.out[i] != syms[i] for i in range(67, n - 1)), n - 68, d, bins


def test_f0_decodes_the_mirror_captures_with_no_errors_as_the_reference_did():
    # 2026-10-10: four 8192-symbol F0 runs a room, 0 errors with the
    # linear EQ [measured-v2]. The grid is the trigger's landing, no search.
    pooled = {}
    for room, head, burst in _captures():
        errors, scored, _, _ = _decode_capture(burst)
        train, test = tp.symbol_range(burst, 64)
        got, _ = tp.linear(burst, train, test)
        theirs = sum(got[i] != burst.levels[i] for i in test)
        key = (room, head["periods"], head["max_zeros"])
        p = pooled.setdefault(key, [0, 0, 0])
        p[0] += errors
        p[1] += theirs
        p[2] += scored
        # one decoder, written twice: within a couple of symbols a capture
        assert abs(errors - theirs) <= 2, (key, head["seed"], errors, theirs)
    for room in ("dark", "lit"):
        ours, theirs, scored = pooled[(room, 2, 4)]
        assert scored > 32000 and ours == 0 and theirs == 0, (room, pooled)


def test_acquisition_finds_what_the_reference_does_and_is_not_needed_at_f0():
    shown = 0
    for _, head, burst in _captures():
        if head["periods"] != 2 or head["symbols"] < 8192:
            continue
        per = int(round(head["pwm_us"] / 100.0 * 65536))
        shifts = [j * per // 4 for j in range(-6, 7)]
        errors, _, d, _ = _decode_capture(burst, shifts)
        assert errors <= 2, (head["seed"], errors)
        assert d.shift in shifts
        shown += 1
    assert shown == 16


def test_a_mirror_capture_decodes_the_same_under_real_viper():
    caps = [c for c in _captures() if c[1]["periods"] == 2
            and c[1]["max_zeros"] == 4]
    room, head, burst = caps[0]
    errors, _, d, bins = _decode_capture(burst)
    n = len(burst.levels)
    status, calls, alloc, w, out = _unix_decode(
        bins, d.start, known=bytes(burst.levels[:67]), end=n - 1,
        cap=n + 4)
    assert status == FB.END and alloc == 0, (status, alloc)
    assert w == str(list(d.w)) and out == bytes(d.out[:n - 1]).hex()


# --- training once a rung: retune() ------------------------------------------------


def _block_on(d, model, rng, chunk, periods=2, flicker=None, gain=1.0,
              jitter=0.25):
    """One block through `model` into decoder `d`, started where the
    receiver would think, its real start up to `jitter` periods off.
    (status, data)."""
    data = payload_of(rng, FB.block_bytes(periods))
    syms = FB.encode(chunk, data)
    off = rng.uniform(-jitter, jitter) * periods
    burst = tp.pam_burst(model, syms, periods, rng, noise=model.noise,
                         offset=off, flicker=flicker)
    bins = array("H", [max(0, int(round(v * 5 * gain))) for v in burst.y])
    start = int(round((burst.edges[0] - off * tp.PWM_US / tp.BIN_US) * 65536))
    w = FB.sym_q16(periods)
    q = w // 4
    d.begin(start, w, flicker=int(flicker[1] * 1000) if flicker else 0,
            max_len=FB.block_bytes(periods), shifts=(-q, 0, q))
    status = d.feed(bins, len(bins))
    return status, (d.payload() if status == FB.OK else None) == data


def test_blocks_after_the_first_retune_and_decode():
    for model in (dark(), lit()):
        for flicker in (None, (1.0, 120.0)):
            for periods in (2, 3):
                for seed in range(1, 5):
                    rng = random.Random(seed)
                    d = FB.Decoder()
                    modes = []
                    for chunk in range(5):
                        status, matched = _block_on(d, model, rng, chunk,
                                                    periods, flicker)
                        assert status == FB.OK and matched, \
                            (flicker, periods, seed, chunk, status)
                        modes.append(d.mode)
                    assert modes == ["train"] + ["retune"] * 4, modes


def test_a_forgotten_decoder_and_another_rung_train_afresh():
    rng = random.Random(3)
    d = FB.Decoder()
    _block_on(d, dark(), rng, 0)
    _block_on(d, dark(), rng, 1)
    assert d.mode == "retune"
    d.forget()
    _block_on(d, dark(), rng, 2)
    assert d.mode == "train", "after forget()"
    status, matched = _block_on(d, dark(), rng, 3, periods=3)
    assert d.mode == "train" and status == FB.OK and matched, "another rung"


def test_a_block_under_other_light_is_not_retuned():
    # Half the swing: the old taps no longer fit, and the block trains.
    rng = random.Random(5)
    d = FB.Decoder()
    _block_on(d, dark(), rng, 0)
    status, matched = _block_on(d, dark(), rng, 1, gain=0.5)
    assert d.mode == "train", d.mode
    assert status == FB.OK and matched
