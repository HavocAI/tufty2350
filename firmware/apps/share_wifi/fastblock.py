"""The fast block: a transfer sends its payload in these when both badges
can (protocol.py; sections 13.1 and 14.2 and Appendix A of the spec).

A block sits inside the slot framing. At the slow rung: lead-in, FAST_BEGIN
and the rung's bits. Then, from the next slot boundary, two-level PAM paced
by DMA on the backlight's PWM wrap, a symbol `periods` PWM periods long on a
common COMMON_NS period; the receiver anchors on the burst's own first rise:

    PREAMBLE (known; its last symbol high) | frame, bit stuffed | TAIL (high)
    frame = chunk (2 bytes) | length (2) | payload | CRC-32 (4)

All big-endian, every byte most significant bit first. The CRC-32 is IEEE
802.3's (binascii.crc32: reflected, init and final XOR 0xFFFFFFFF) over the
header and payload. A 1 is stuffed after every MAX_LOWS 0s in a row when
another frame bit follows, so no run of the low is longer than MAX_LOWS. The
receiver judges the CRC itself and answers at the slow rung.

The receiver bins the ring (reader.RingReader.bins: 100 us bins, sums of 5
readings) and hands the bins to a Decoder: a linear equaliser over three
part sums of each of symbols i - 2 .. i + 1, the mains and its harmonic as
sin/cos regressors when a frequency is known, and a constant -- trained by
least squares on the preamble, then the regressors and the constant tracked
by LMS on the decisions. That is throughput.py's `linear` receiver, in whole
numbers, with a viper kernel, `_run`, for everything done once a symbol
(tests/share_wifi/test_fastblock.py holds it to throughput.py on the mirror
captures).

Also here: the symbol clock's divider (`common_div`), the rungs and their
block sizes under the 1 s cap, and the CC words a DMA channel plays.

Pure: no badge globals, no machine. `_run` is viper on MicroPython and plain
Python under CPython (the stubs below), from one source.
"""

import binascii
import math
from array import array

try:
    import micropython
    from micropython import const
except ImportError:                     # CPython: the kernel runs as Python
    class micropython:                  # noqa: N801
        @staticmethod
        def viper(f):
            return f

    def _cast(x):
        return x

    ptr8 = ptr16 = ptr32 = _cast

    def const(x):
        return x

# --- the symbol clock ----------------------------------------------------------

# A PWM period on both firmwares once the divider is set: v2's own at 200 MHz
# and TOP 65535 [measured-v2], v3's at 250 MHz with DIV 1.25 [inferred-v3:
# arithmetic; the fractional divider is unrun on the badge].
COMMON_NS = 327680
DIV_TOL_PPM = 1000              # a divider further off than this is refused
BIN_NS = 100000                 # a bin: 5 ring readings at 50 kHz
BIN_READINGS = 5


def common_div(sys_hz, top=65535):
    """(DIV register value, None) for a COMMON_NS period at this clock and
    TOP -- INT.FRAC/16, at least 1.0 -- or (None, why not)."""
    den = 1000000000 * (top + 1)
    div16 = (2 * COMMON_NS * sys_hz * 16 + den) // (2 * den)
    if div16 < 16 or div16 > 0xFFF:
        return None, "no divider for %d Hz" % sys_hz
    got = div16 * den // (16 * sys_hz)
    if abs(got - COMMON_NS) * 1000000 > DIV_TOL_PPM * COMMON_NS:
        return None, "%d Hz: the nearest divider gives %d ns, not %d" % (
            sys_hz, got, COMMON_NS)
    return div16, None


# --- rungs, and a block's size under the cap -------------------------------------

RUNGS = (2, 3, 4, 6)            # F0-F3: PWM periods a symbol
MAX_LOWS = 4                    # the line code, on every rung
PREAMBLE_LEN = 66               # rows 2..65: 64 training rows
PREAMBLE_SEED = 0x2545F493
TAIL = 2                        # high symbols after the frame: the last
#                                 decision needs its successor's parts
#                                 (the last PWM period is fill_words()'s)
HEADER = 4
CRC = 4
CHUNK = 32                      # bytes: a block's chunk counts these
MAX_AIR_NS = 1000000000         # 1 s on air from the first fast symbol
MAX_LEN = 512                   # the most payload a Decoder takes


def sym_q16(periods, period_ns=COMMON_NS):
    """A symbol's width in bins, Q16."""
    return (periods * period_ns * 65536 * 2 + BIN_NS) // (2 * BIN_NS)


def frame_bits_max(periods, preamble=PREAMBLE_LEN):
    """The most frame bits a block can carry and stay under MAX_AIR_NS,
    stuffed at worst (all 0s: a stuffed 1 before every fifth bit)."""
    room = MAX_AIR_NS // (periods * COMMON_NS) - preamble - TAIL
    n = room * MAX_LOWS // (MAX_LOWS + 1) + 1
    while n > 0 and n + (n - 1) // MAX_LOWS > room:
        n -= 1
    return n


def block_bytes(periods, preamble=PREAMBLE_LEN):
    """A rung's payload: whole chunks under the cap. F0 128, F1 64, F2-F3
    32, with the 66-symbol preamble."""
    payload = frame_bits_max(periods, preamble) // 8 - HEADER - CRC
    return payload // CHUNK * CHUNK


def stuffed_len(frame_len, data=None):
    """Symbols a frame of `frame_len` bytes takes, stuffed: exact given
    the frame, else the worst case."""
    n = frame_len * 8
    if data is None:
        return n + (n - 1) // MAX_LOWS if n else 0
    zeros = extra = 0
    for i in range(n):
        if zeros == MAX_LOWS:
            extra += 1
            zeros = 0
        zeros = 0 if data[i >> 3] & (128 >> (i & 7)) else zeros + 1
    return n + extra


# --- the transmitter ----------------------------------------------------------------


def prbs(n, seed, max_lows=MAX_LOWS):
    """n two-level symbols from xorshift32's top bit, a 1 stuffed after
    max_lows 0s (0: none): pam_link's symbols_for(2, ...), so the mirror
    captures' sequences are prbs(count, 0x2545F491 + 2 + 1000 * seed)."""
    out = bytearray(n)
    x = seed
    zeros = 0
    for i in range(n):
        if max_lows and zeros == max_lows:
            out[i] = 1
            zeros = 0
            continue
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= x >> 17
        x ^= (x << 5) & 0xFFFFFFFF
        out[i] = x >> 31
        zeros = zeros + 1 if out[i] == 0 else 0
    return out


def preamble(n=PREAMBLE_LEN):
    """The known training sequence, its last symbol high so the frame's
    stuffing starts from no run."""
    out = prbs(n, PREAMBLE_SEED)
    out[n - 1] = 1
    return out


PREAMBLE = preamble()


def frame(chunk, payload):
    """Header, payload and CRC-32, as bytes."""
    n = len(payload)
    head = bytes((chunk >> 8, chunk & 255, n >> 8, n & 255))
    crc = binascii.crc32(payload, binascii.crc32(head)) & 0xFFFFFFFF
    return head + bytes(payload) + bytes(
        (crc >> 24, (crc >> 16) & 255, (crc >> 8) & 255, crc & 255))


def encode(chunk, payload, known=PREAMBLE):
    """The block's symbols, 0 low and 1 high, one a byte: the preamble,
    the frame stuffed, the tail."""
    data = frame(chunk, payload)
    n = len(data) * 8
    out = bytearray(len(known) + stuffed_len(len(data), data) + TAIL)
    out[:len(known)] = known
    at = len(known)
    zeros = 0
    for i in range(n):
        if zeros == MAX_LOWS:
            out[at] = 1
            at += 1
            zeros = 0
        bit = 1 if data[i >> 3] & (128 >> (i & 7)) else 0
        out[at] = bit
        at += 1
        zeros = 0 if bit else zeros + 1
    for i in range(TAIL):
        out[at + i] = 1
    return out


def fill_words(words, symbols, periods, low_word, high_word):
    """The CC words a DMA channel paced by the wrap plays: `periods` a
    symbol, then one more copy of the last. Returns how many: periods x
    symbols + 1, which `words` must hold. low_word and high_word are whole
    CC values, channel B's half kept as found. The copy: CC is double
    buffered, so the DMA's last word only takes effect at the next wrap,
    while the channel reads as idle as soon as it is written -- the copy
    keeps it busy until the real last word has latched, so closing then
    loses nothing of the block."""
    w = 0
    word = low_word
    for s in symbols:
        word = high_word if s else low_word
        for _ in range(periods):
            words[w] = word
            w += 1
    words[w] = word
    return w + 1


# --- the receiver -------------------------------------------------------------------

# Status, from feed().
NEED = const(0)                 # more bins, please
END = const(1)                  # raw mode: every symbol asked for decided
OK = const(2)                   # a block, and its CRC passed
ERR_STUFF = const(3)            # a 0 where a stuffed 1 belonged: NAK now
ERR_LENGTH = const(4)           # the header's length is past the rung's
ERR_CRC = const(5)
ERR_TRAIN = const(6)            # the preamble gave no usable equaliser
ERR_OVERRUN = const(7)          # out of symbols before the frame ended

# The state the kernel keeps, by index into Decoder.st.
_I = const(0)                   # the next symbol to decide
_END = const(1)                 # decide no symbol at or past this
_HAVE = const(2)                # symbols whose parts are in xs
_FILL = const(3)                # fill parts at least this far
_AVAIL = const(4)               # bins valid
_SB = const(5)                  # symbol _HAVE's start: its bin, and
_SF = const(23)                 # the fraction past it, Q16
_WB = const(24)                 # a symbol's width: whole bins, and
_WF = const(25)                 # the fraction past them, Q16
_PART = const(6)                # a third of a symbol, Q16 bins
_C = const(7)                   # the parts' centre, and shift, and clamp
_SH = const(8)
_LIM = const(9)
_G = const(10)                  # the part weights' extra shift
_THR = const(11)                # the decision threshold, Q16 levels
_PH = const(12)                 # symbol _I's mains phase, 24 bits a cycle
_INC = const(13)
_FLK = const(14)                # 1: mains regressors on
_MU = const(15)                 # the LMS step, /2048: 0 = no tracking
_RUN = const(16)                # lows in a row
_NBITS = const(17)              # frame bits decided
_NEED = const(18)               # frame bits in all, 0 until the header
_MAXL = const(19)
_RAW = const(20)                # 1: out[i] = symbol i, no frame
_MAXLEN = const(21)
_V = const(22)                  # the last decision's value, for the tests
_NST = 26                       # (_SF, _WB, _WF are with _SB above)

TAPS = 12                       # 3 parts x symbols i - 2 .. i + 1
NW = TAPS + 5                   # + sin, cos of the mains and its harmonic, 1
ONE = const(512)                # a regressor's 1.0, and the constant
SPAN = 512                      # the preamble's parts span this, after shift
LIM = 1024                      # and are clamped here
W_MAX = 1 << 13                 # the part weights, after their shift
T_MAX = const(524288)           # the tracked weights: |w| < 8 levels
E_MAX = const(131072)           # an LMS error: 2 levels
MIN_SEPARATION = 1 << 15        # the trained levels' means: half a level
TRACK_MU = 41                   # / 2048 = 0.020: throughput.TRACK_MU
# A retuned block (Decoder.retune) is refused, and trained afresh, when its
# preamble fit leaves more squared error than the training's by this much:
# the light or the timing has moved too far for the old taps. In simulation
# (the dark F1 rung, 2 KB payloads) a retune failed 6-7% of the time up to
# an excess of 0.25, 11% at 0.5, 25% at 0.75 and half beyond 1.0, against
# 5% for a training.
RETUNE_SSE_SLACK = 0.6
SINE = array("i", [int(round(ONE * math.sin(2 * math.pi * k / 256)))
                   for k in range(256)])


@micropython.viper
def _run(dec, bins) -> int:
    # Fills parts and decides symbols until the bins run out, the range
    # ends or the frame does. Every value fits 32 bits signed (see the
    # bounds above). ptr32 loads zero-extend on a 64-bit viper (the unix
    # port's), so a signed load is sign-extended by hand: ((v & msk) ^ m)
    # - m, the identity on the badge's 32 bits and under CPython.
    b = ptr16(bins)
    x = ptr32(dec.xs)
    wt = ptr32(dec.w)
    s = ptr32(dec.st)
    sn = ptr32(SINE)
    o = ptr8(dec.out)
    m = 1
    m = m << 31                 # at run time: a literal 2**31 is an object
    msk = m | (m - 1)
    i = s[_I]
    end = s[_END]
    have = s[_HAVE]
    fill = s[_FILL]
    avail = s[_AVAIL]
    sb = s[_SB]
    sf = s[_SF]
    wb = s[_WB]
    wf = s[_WF]
    part = s[_PART]
    c = ((s[_C] & msk) ^ m) - m
    sh = s[_SH]
    lim = s[_LIM]
    g = s[_G]
    thr = ((s[_THR] & msk) ^ m) - m
    ph = s[_PH]
    inc = s[_INC]
    flk = s[_FLK]
    mu = s[_MU]
    zeros = s[_RUN]
    nbits = s[_NBITS]
    need = s[_NEED]
    maxl = s[_MAXL]
    raw = s[_RAW]
    maxlen = s[_MAXLEN]
    v = 0
    status = 0
    while status == 0:
        more = 0
        if have < fill:
            more = 1
        if i < end:
            if have <= i + 1:
                more = 1
        if more:
            p = have * 3
            e = sf + 3 * part
            last = sb + (e >> 16)
            if (e >> 8) & 255:
                last += 1
            if last > avail:
                break                   # NEED
            k = 0
            while k < 3:
                a = sf + k * part
                e = a + part
                ia = sb + (a >> 16)
                ie = sb + (e >> 16)
                acc = 0
                j = ia
                while j < ie:
                    acc += int(b[j])
                    j += 1
                acc = (acc << 8) - ((a >> 8) & 255) * int(b[ia])
                fe = (e >> 8) & 255
                if fe:
                    acc += fe * int(b[ie])
                acc = (acc - c) >> sh
                if acc > lim:
                    acc = lim
                if acc < 0 - lim:
                    acc = 0 - lim
                x[p + k] = acc
                k += 1
            have += 1
            sf += wf
            sb += wb + (sf >> 16)
            sf &= 0xFFFF
            continue
        if i >= end:
            status = 1                  # END
            break
        base = (i - 2) * 3
        acc = 0
        k = 0
        while k < 12:
            xv = ((x[base + k] & msk) ^ m) - m
            wv = ((wt[k] & msk) ^ m) - m
            acc += xv * wv
            k += 1
        v = acc >> g
        r1 = 0
        r2 = 0
        r3 = 0
        r4 = 0
        if flk:
            idx = (ph >> 16) & 255
            r1 = ((sn[idx] & msk) ^ m) - m
            r2 = ((sn[(idx + 64) & 255] & msk) ^ m) - m
            idx = (idx * 2) & 255
            r3 = ((sn[idx] & msk) ^ m) - m
            r4 = ((sn[(idx + 64) & 255] & msk) ^ m) - m
        t1 = ((wt[12] & msk) ^ m) - m
        t2 = ((wt[13] & msk) ^ m) - m
        t3 = ((wt[14] & msk) ^ m) - m
        t4 = ((wt[15] & msk) ^ m) - m
        t5 = ((wt[16] & msk) ^ m) - m
        t = (t1 >> 4) * r1 + (t2 >> 4) * r2 + (t3 >> 4) * r3 \
            + (t4 >> 4) * r4 + (t5 >> 4) * ONE
        v += t >> 5
        d = 0
        if v >= thr:
            d = 1
        if mu:
            er = (d << 16) - v
            if er > E_MAX:
                er = E_MAX
            if er < 0 - E_MAX:
                er = 0 - E_MAX
            t1 += (((er * r1) >> 9) * mu) >> 11
            t2 += (((er * r2) >> 9) * mu) >> 11
            t3 += (((er * r3) >> 9) * mu) >> 11
            t4 += (((er * r4) >> 9) * mu) >> 11
            t5 += (((er * ONE) >> 9) * mu) >> 11
            if t1 > T_MAX:
                t1 = T_MAX
            if t1 < 0 - T_MAX:
                t1 = 0 - T_MAX
            if t2 > T_MAX:
                t2 = T_MAX
            if t2 < 0 - T_MAX:
                t2 = 0 - T_MAX
            if t3 > T_MAX:
                t3 = T_MAX
            if t3 < 0 - T_MAX:
                t3 = 0 - T_MAX
            if t4 > T_MAX:
                t4 = T_MAX
            if t4 < 0 - T_MAX:
                t4 = 0 - T_MAX
            if t5 > T_MAX:
                t5 = T_MAX
            if t5 < 0 - T_MAX:
                t5 = 0 - T_MAX
            wt[12] = t1
            wt[13] = t2
            wt[14] = t3
            wt[15] = t4
            wt[16] = t5
        ph = (ph + inc) & 0xFFFFFF
        i += 1
        if raw:
            o[i - 1] = d
            continue
        if maxl:
            if zeros == maxl:
                if d == 0:
                    status = 3          # ERR_STUFF
                zeros = 0
                continue
            if d:
                zeros = 0
            else:
                zeros += 1
        if d:
            o[nbits >> 3] = int(o[nbits >> 3]) | (128 >> (nbits & 7))
        nbits += 1
        if nbits == 32:
            ln = (int(o[2]) << 8) | int(o[3])
            if ln > maxlen:
                status = 4              # ERR_LENGTH
            need = (ln + 8) * 8
        if need:
            if nbits == need:
                status = 2              # the frame is in
    s[_I] = i
    s[_HAVE] = have
    s[_SB] = sb
    s[_SF] = sf
    s[_PH] = ph
    s[_RUN] = zeros
    s[_NBITS] = nbits
    s[_NEED] = need
    s[_V] = v
    return status


def _solve(a, b, ridge=1e-9):
    """a x = b, a symmetric, by Gaussian elimination with a little ridge:
    sensor_fit.solve's."""
    n = len(b)
    scale = max(abs(a[i][i]) for i in range(n)) or 1.0
    m = [[float(v) for v in a[i]] + [float(b[i])] for i in range(n)]
    for i in range(n):
        m[i][i] += ridge * scale
    for c in range(n):
        p = c
        for r in range(c + 1, n):
            if abs(m[r][c]) > abs(m[p][c]):
                p = r
        m[c], m[p] = m[p], m[c]
        if m[c][c] == 0:
            continue
        for r in range(c + 1, n):
            f = m[r][c] / m[c][c]
            if f:
                row, top = m[r], m[c]
                for j in range(c, n + 1):
                    row[j] -= f * top[j]
    x = [0.0] * n
    for c in range(n - 1, -1, -1):
        if m[c][c]:
            acc = m[c][n]
            for j in range(c + 1, n):
                acc -= m[c][j] * x[j]
            x[c] = acc / m[c][c]
    return x


def flicker_mhz(mains, rate_m):
    """The light's flicker fundamental in mHz, from FlickerFilter: twice
    its line frequency `mains` (50 or 60 Hz, FLICKER_MAINS), plus twice the
    phase lock's drift. rate_m is thousandths of a table step a second, the
    table FLICKER_STEPS (320) steps a line cycle (protocol.FlickerFilter.
    step_at), so the line drifts rate_m / 320 mHz and the flicker twice
    that. The regressors are this and its harmonic: 100/200 or 120/240 Hz,
    where a room's flicker sits. 0 when none is fitted."""
    if not mains:
        return 0
    return 2 * mains * 1000 + rate_m // 160


class Decoder:
    """Decodes one fast block at a time from bins. Allocates its arrays once,
    for up to `symbols` symbols a block; training allocates (floats, once a
    block), deciding does not.

        d = Decoder()
        d.begin(start_q16, sym_q16(2), flicker=flicker_mhz(f.mains, f.rate_m))
        while (status := d.feed(bins, nb)) == NEED: ...   # as bins arrive
        if status == OK: chunk, payload = d.chunk, d.payload()
    """

    def __init__(self, symbols=1600):
        self.cap = symbols
        self.xs = array("i", bytes(4 * 3 * symbols))
        self.w = array("i", bytes(4 * NW))
        self.st = array("i", bytes(4 * _NST))
        self.out = bytearray(max(symbols, HEADER + MAX_LEN + CRC))
        self.known = PREAMBLE
        self.trained = False
        self.chunk = self.length = None
        self.sse = None
        self.shift = 0
        self.tuned = None       # the last training, for retune(); forget()
        self.mode = None        # how the block began: "train" or "retune"

    def begin(self, start_q16, width_q16, known=PREAMBLE, flicker=0,
              raw_end=0, max_len=128, track=True, shifts=None):
        """A new block: symbol 0 starts `start_q16` into the bins, each
        `width_q16` wide (Q16 bins: sym_q16(periods), or the receiver's
        measure of the transmitter's). `known` is the preamble, `flicker`
        the light's flicker fundamental in mHz, flicker_mhz() (0: none --
        no regressors, no tracking). With
        `raw_end`, decide symbols len(known) .. raw_end - 1 into out[]
        and stop, with no frame (the mirror captures). `shifts`: Q16
        offsets to try, keeping the best preamble fit (acquisition)."""
        st = self.st
        for k in range(_NST):
            st[k] = 0
        if raw_end > self.cap - 1 or max_len > MAX_LEN or not raw_end and \
                len(known) + stuffed_len(HEADER + max_len + CRC) + TAIL > \
                self.cap - 1:
            raise ValueError("more than the decoder holds")
        if start_q16 + min(shifts or (0,)) < 0:
            raise ValueError("a symbol before the first bin")
        st[_WB] = width_q16 >> 16
        st[_WF] = width_q16 & 0xFFFF
        st[_PART] = (width_q16 + 1) // 3
        st[_MAXL] = MAX_LOWS
        st[_MAXLEN] = max_len
        st[_RAW] = 1 if raw_end else 0
        st[_END] = raw_end if raw_end else self.cap - 1
        st[_LIM] = 1 << 30
        if flicker:
            st[_FLK] = 1
            st[_INC] = (flicker * width_q16 * 256 + 5000000) // 10000000
            st[_MU] = TRACK_MU if track else 0
        self.known = known
        self.trained = False
        self.width = width_q16
        self.start = start_q16
        self.shifts = shifts or (0,)
        self.chunk = self.length = None
        self.sse = None
        self.shift = 0
        self.mode = None
        out = self.out
        for k in range(HEADER + max_len + CRC if not raw_end else 0):
            out[k] = 0

    def bins_needed(self, symbols):
        """Bins that must be in before symbols 0 .. symbols - 1 have parts."""
        e = self.start + max(self.shifts) + symbols * self.width
        return (e >> 16) + 1

    def feed(self, bins, avail):
        """Decodes what `avail` bins allow; returns a status. Called as
        bins arrive: its frame stays within 11 words and the kernel
        allocates nothing, so a call that returns NEED allocates nothing."""
        self.st[_AVAIL] = avail
        if self.trained:
            r = _run(self, bins)
            if r == NEED:
                return NEED
            return self._finish(r)
        return self._first(bins, avail)

    def _first(self, bins, avail):
        if avail < self.bins_needed(len(self.known) + 1):
            return NEED
        if not (self.tuned is not None and self.retune(bins)) \
                and not self.train(bins):
            return ERR_TRAIN
        return self.feed(bins, avail)

    def forget(self):
        """The next block trains afresh: after one that failed, or when the
        light has changed."""
        self.tuned = None

    def _finish(self, r):
        if r == OK:
            out = self.out
            n = self.st[_NEED] // 8 - CRC
            got = (out[n] << 24) | (out[n + 1] << 16) | (out[n + 2] << 8) \
                | out[n + 3]
            crc = binascii.crc32(memoryview(out)[:n]) & 0xFFFFFFFF
            if crc != got:
                return ERR_CRC
            self.chunk = (out[0] << 8) | out[1]
            self.length = n - HEADER
            return OK
        if r == END and not self.st[_RAW]:
            return ERR_OVERRUN
        return r

    def payload(self):
        """The payload of a block that returned OK; raises otherwise."""
        if self.length is None:
            raise ValueError("no block has passed its CRC")
        return bytes(self.out[HEADER:HEADER + self.length])

    def _fill(self, bins, start, c, sh, lim, upto):
        st = self.st
        st[_SB] = start >> 16
        st[_SF] = start & 0xFFFF
        st[_C] = c
        st[_SH] = sh
        st[_LIM] = lim
        st[_HAVE] = 0
        st[_FILL] = upto
        keep_i, keep_end = st[_I], st[_END]
        st[_I] = st[_END] = 0
        _run(self, bins)
        st[_I], st[_END] = keep_i, keep_end

    def regressors(self, i):
        """Symbol i's mains terms, as the kernel takes them."""
        if not self.st[_FLK]:
            return 0, 0, 0, 0
        idx = ((i * self.st[_INC]) & 0xFFFFFF) >> 16
        idx2 = (idx * 2) & 255
        return (SINE[idx], SINE[(idx + 64) & 255], SINE[idx2],
                SINE[(idx2 + 64) & 255])

    def value(self, i):
        """The kernel's decision value for symbol i with the weights as they
        stand, in Python: for training and the tests."""
        st, w, xs = self.st, self.w, self.xs
        base = (i - 2) * 3
        acc = 0
        for k in range(TAPS):
            acc += xs[base + k] * w[k]
        v = acc >> st[_G]
        r = self.regressors(i)
        t = 0
        for k in range(4):
            t += (w[TAPS + k] >> 4) * r[k]
        t += (w[TAPS + 4] >> 4) * ONE
        return v + (t >> 5)

    def _fit(self, bins, start):
        """Parts of the preamble at this start, the equaliser fitted to it:
        (float weights, the fit's squared error)."""
        n = len(self.known)
        self._fill(bins, start, 0, 0, 1 << 30, n + 1)
        xs = self.xs
        lo = hi = xs[0]
        for k in range(3 * (n + 1)):
            v = xs[k]
            if v < lo:
                lo = v
            if v > hi:
                hi = v
        sh = 0
        while (hi - lo) >> sh > SPAN:
            sh += 1
        self._fill(bins, start, (lo + hi) >> 1, sh, LIM, n + 1)
        flk = self.st[_FLK]
        ata = [[0] * NW for _ in range(NW)]
        atb = [0] * NW
        rows = []
        for i in range(2, n):
            base = (i - 2) * 3
            row = [xs[base + k] for k in range(TAPS)]
            row += list(self.regressors(i)) if flk else [0, 0, 0, 0]
            row.append(ONE)
            rows.append(row)
            t = self.known[i]
            for a in range(NW):
                ra = row[a]
                if ra:
                    line = ata[a]
                    for b in range(a, NW):
                        line[b] += ra * row[b]
                    if t:
                        atb[a] += ra
        for a in range(NW):
            for b in range(a):
                ata[a][b] = ata[b][a]
        if not flk:                     # no mains terms: keep them out
            for a in range(TAPS, TAPS + 4):
                ata[a][a] = 1
        u = _solve(ata, atb)
        sse = 0.0
        for i in range(2, n):
            row = rows[i - 2]
            z = 0.0
            for a in range(NW):
                z += u[a] * row[a]
            sse += (z - self.known[i]) ** 2
        return u, sse

    def train(self, bins):
        """Fits the equaliser to the preamble (at the best of `shifts`),
        quantises it and sets the threshold. False if it cannot."""
        best = None
        for shift in self.shifts:
            u, sse = self._fit(bins, self.start + shift)
            if best is None or sse < best[1]:
                best = (u, sse, shift)
        u, self.sse, self.shift = best
        if self.shift != self.shifts[-1]:
            self._fit(bins, self.start + self.shift)
        st, w = self.st, self.w
        big = 0.0
        for k in range(TAPS):
            big = max(big, abs(u[k]))
        g = 15
        while g >= 0 and big * (1 << (16 + g)) > W_MAX:
            g -= 1
        if g < 0:
            return False
        st[_G] = g
        for k in range(TAPS):
            w[k] = int(round(u[k] * (1 << (16 + g))))
        if not self._tracked(u[TAPS:]) or not self._arm():
            return False
        self.tuned = (self.width, st[_FLK], st[_C], st[_SH], g,
                      list(u[:TAPS]), self.sse, w[:TAPS])
        self.mode = "train"
        return True

    def _tracked(self, v):
        """The regressors' and the constant's weights, quantised as the
        kernel tracks them. False if one is out of range."""
        w = self.w
        for k in range(NW - TAPS):
            t = int(round(v[k] * (1 << 25)))
            if abs(t) > T_MAX:
                return False
            w[TAPS + k] = t
        return True

    def retune(self, bins):
        """A block after the first on the same rung: the last training's
        taps kept, only the mains terms and the constant fitted to this
        preamble -- the mains' phase differs at every block's start, the
        baseline may have moved -- at the best of `shifts`. About a tenth
        of train()'s work. False, the block untouched, if the taps no
        longer fit (RETUNE_SSE_SLACK) or the levels do not separate."""
        width, flk, c, sh, g, taps, sse0, wtaps = self.tuned
        st, w = self.st, self.w
        if width != self.width or flk != st[_FLK]:
            return False
        n = len(self.known)
        best = None
        shifts = self.shifts
        if len(shifts) > 1:
            # The taps cannot move with the timing as a training's do, so
            # the start is searched twice as finely, between the given
            # ones; and by the taps alone and a constant, a dot product a
            # row, where the full refit is a 5 x 5 system a row.
            lo, hi = min(shifts), max(shifts)
            step = (hi - lo) // (2 * (len(shifts) - 1)) or 1
            shifts = range(lo, hi + 1, step)
            for shift in shifts:
                self._fill(bins, self.start + shift, c, sh, LIM, n + 1)
                sse = self._spread(taps)
                if best is None or sse < best[0]:
                    best = (sse, shift)
            shift = best[1]
        else:
            shift = shifts[0]
        if shift != shifts[-1] or len(shifts) == 1:
            self._fill(bins, self.start + shift, c, sh, LIM, n + 1)
        v, sse = self._refit(taps, flk)
        if sse > sse0 + RETUNE_SSE_SLACK:
            return False
        st[_G] = g
        for k in range(TAPS):
            w[k] = wtaps[k]
        if not self._tracked(v) or not self._arm():
            return False
        self.sse, self.shift = sse, shift
        self.mode = "retune"
        return True

    def _spread(self, taps):
        """The preamble's squared error, as filled, with the taps fixed and
        only a constant fitted: what chooses retune()'s start."""
        n = len(self.known)
        xs = self.xs
        known = self.known
        rs = []
        for i in range(2, n):
            base = (i - 2) * 3
            p = 0.0
            for k in range(TAPS):
                p += taps[k] * xs[base + k]
            rs.append(known[i] - p)
        mean = sum(rs) / len(rs)
        return sum((r - mean) ** 2 for r in rs)

    def _refit(self, taps, flk):
        """The mains terms and the constant that best fit the preamble's
        parts, as filled, with the taps fixed: (weights, squared error)."""
        n = len(self.known)
        xs = self.xs
        m = 5 if flk else 1
        ata = [[0.0] * m for _ in range(m)]
        atb = [0.0] * m
        rows = []
        for i in range(2, n):
            base = (i - 2) * 3
            p = 0.0
            for k in range(TAPS):
                p += taps[k] * xs[base + k]
            row = list(self.regressors(i)) + [ONE] if flk else [ONE]
            r = self.known[i] - p
            rows.append((p, row))
            for a in range(m):
                ra = row[a]
                line = ata[a]
                for b in range(m):
                    line[b] += ra * row[b]
                atb[a] += ra * r
        u = _solve(ata, atb)
        sse = 0.0
        for i in range(2, n):
            p, row = rows[i - 2]
            z = p
            for a in range(m):
                z += u[a] * row[a]
            sse += (z - self.known[i]) ** 2
        v = list(u) if flk else [0.0, 0.0, 0.0, 0.0, u[0]]
        return v, sse

    def _arm(self):
        """The threshold from the preamble's decided values, and the kernel
        set to decide from the first symbol after it."""
        st = self.st
        n = len(self.known)
        sums = [0, 0]
        counts = [0, 0]
        for i in range(2, n):
            lv = self.known[i]
            sums[lv] += self.value(i)
            counts[lv] += 1
        if not counts[0] or not counts[1]:
            return False
        low, high = sums[0] // counts[0], sums[1] // counts[1]
        if high - low < MIN_SEPARATION:
            return False                # the preamble did not separate
        st[_THR] = (low + high) // 2
        st[_I] = n
        st[_FILL] = 0
        st[_PH] = (n * st[_INC]) & 0xFFFFFF
        self.trained = True
        return True
