"""The fast block's channel and reference receiver, for test_fastblock.py:
the measured sensor as a model (v2 badge, mirror B at ~1 cm, 2026-10-09f),
a PAM burst through it, the floating-point linear equaliser fastblock's
whole-number one is checked against, and the reader of pam_link's .bin
captures. Kept from the stage 2 research tools (throughput.py, capfile.py,
sensor_fit.py) when they were removed on 2026-10-19; not a test.
"""

import json
import math
import sys
from array import array


def read(path_or_bytes):
    """(magic, [(header, {blob name: array})])."""
    if isinstance(path_or_bytes, (bytes, bytearray)):
        data = bytes(path_or_bytes)
    else:
        with open(path_or_bytes, "rb") as handle:
            data = handle.read()
    end = data.index(b"\n")
    magic = data[:end].decode()
    at = end + 1
    captures = []
    while at < len(data):
        end = data.index(b"\n", at)
        head = json.loads(data[at:end].decode())
        at = end + 1
        blobs = {}
        for name, code, count in head["blobs"]:
            values = array(code)
            size = values.itemsize * count
            if at + size > len(data):
                raise ValueError("capture %d: %s cut short"
                                 % (len(captures), name))
            values.frombytes(data[at:at + size])
            if sys.byteorder != "little":
                values.byteswap()
            blobs[name] = values
            at += size
        captures.append((head, blobs))
    return magic, captures


def pwm_tick_us(head):
    """One count of the PWM slice's counter, in us, from DIV and sys_hz."""
    div = head["div"]
    whole = (div >> 4) or 256
    return (whole + (div & 0xF) / 16.0) * 1e6 / head["sys_hz"]


def solve(a, b, ridge=1e-9):
    """a x = b, a symmetric, by Gaussian elimination with a little ridge."""
    n = len(b)
    scale = max(abs(a[i][i]) for i in range(n)) or 1.0
    m = [list(a[i]) + [b[i]] for i in range(n)]
    for i in range(n):
        m[i][i] += ridge * scale
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
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
            x[c] = (m[c][n] - sum(m[c][j] * x[j]
                                  for j in range(c + 1, n))) / m[c][c]
    return x


def least_squares(rows, targets):
    """The coefficients minimising |rows . x - targets|."""
    n = len(rows[0])
    ata = [[0.0] * n for _ in range(n)]
    atb = [0.0] * n
    for row, t in zip(rows, targets):
        for i in range(n):
            ri = row[i]
            if ri:
                ai = ata[i]
                for j in range(i, n):
                    ai[j] += ri * row[j]
                atb[i] += ri * t
    for i in range(n):
        for j in range(i):
            ata[i][j] = ata[j][i]
    return solve(ata, atb)


SYS_HZ = 200000000          # v2's clock [measured-v2, channel.py's sys_hz]


PWM_TOP = 65535             # the driver's, kept [source-v2]


PWM_US = (PWM_TOP + 1) * 1e6 / SYS_HZ      # 327.68 us, 3052 Hz


BIN_US = 100.0              # sensor_id's bin: 5 ring readings


BACKLIGHT_GAMMA = 2.8       # v2's duty = v ** 2.8 [source-v2]


class Model:
    """Model A of one (room, low, k) group, and the noise it is run with."""

    def __init__(self, targets, y0, K, r_min, delay_us=40.0, noise=0.2,
                 low=0.5, resid=None):
        self.targets = list(targets)
        self.y0 = y0
        self.K = K
        self.r_min = r_min
        self.delay_us = delay_us
        self.noise = noise
        self.resid = resid if resid is not None else noise
        self.low = low

    @classmethod
    def from_fit(cls, fit, low, k):
        group = [g for g in fit["groups"] if g["low"] == low and g["k"] == k]
        if not group:
            raise KeyError("no group low %s k %s" % (low, k))
        a = group[0]["A"]
        caps = [c for c in fit["captures"] if c["low"] == low and c["k"] == k]
        noise = sum(c["noise"] for c in caps) / len(caps)
        resid = sum(c["rms_A"] for c in caps) / len(caps)
        return cls(a["targets"], a["y0"], a["K"], a["r_min"],
                   fit.get("delay_us", 40), noise, low, resid)

    def duties(self):
        """Each level's PWM duty: even in duty from the low's (v ** 2.8) to
        1, as sensor_id spaced them (to within a brightness step)."""
        lo = self.low ** BACKLIGHT_GAMMA
        k = len(self.targets)
        return [lo + (1 - lo) * i / (k - 1) for i in range(k)]

    def target_at(self, duty):
        """The level a duty settles to, interpolated between the fitted
        levels' (an 8-level group's for anything but PAM)."""
        ds = self.duties()
        if duty <= ds[0]:
            return self.targets[0]
        for i in range(1, len(ds)):
            if duty <= ds[i]:
                f = (duty - ds[i - 1]) / (ds[i] - ds[i - 1])
                return self.targets[i - 1] + f * (self.targets[i]
                                                  - self.targets[i - 1])
        return self.targets[-1]


def respond(events, nbins, bin_us, model, start, first_target, rng=None,
            noise=None):
    """Bin means of model A's output. `events` [(t_us, target)] sorted, each
    target held from its time; `first_target` before the first. Each bin is
    stepped in halves, the rate held over a step, as sensor_fit does."""
    y0, kk, rmin = model.y0, model.K, model.r_min
    half = bin_us / 2000.0                          # ms
    y = start
    target = first_target
    out = [0.0] * nbins
    e = 0
    ne = len(events)
    nxt = events[0][0] / 1000.0 if ne else 1e30
    t = 0.0
    for b in range(nbins):
        total = 0.0
        for h in (1, 2):
            end = (2 * b + h) * half
            while True:
                stop = nxt if nxt < end else end
                dt = stop - t
                if dt > 0:
                    rate = (y - y0) / kk
                    if rate < rmin:
                        rate = rmin
                    x = rate * dt
                    d = math.exp(-x)
                    total += (target + (y - target) * (1 - d) / x) * dt
                    y = target + (y - target) * d
                    t = stop
                if nxt <= end:
                    target = events[e][1]
                    e += 1
                    nxt = events[e][0] / 1000.0 if e < ne else 1e30
                else:
                    break
        out[b] = total / (2 * half)
    sigma = model.noise if noise is None else noise
    if rng is not None and sigma:
        out = [v + rng.gauss(0.0, sigma) for v in out]
    return out


class Burst:
    """A received block: bins (raw, BIN_US apart), the receiver's symbol
    grid in bins (n + 1 edges), and the symbols sent."""

    def __init__(self, y, edges, levels, k):
        self.y = y
        self.edges = edges
        self.levels = levels
        self.k = k
        self._prefix = None
        self.flicker_hz = None      # the receiver's mains estimate, if any
        self.track = 0.0            # LMS step on the flicker terms, if any

    def flicker_terms(self, i):
        """sin and cos of the mains and its second harmonic at symbol i's
        middle: regressors for an equaliser that cancels flicker."""
        if not self.flicker_hz:
            return []
        t = (self.edges[i] + self.edges[i + 1]) / 2 * BIN_US * 1e-6
        out = []
        for h in (1, 2):
            a = 2 * math.pi * h * self.flicker_hz * t
            out += [math.sin(a), math.cos(a)]
        return out

    def mean(self, a, b):
        """Mean over [a, b) in bins, fractional bins weighted."""
        if self._prefix is None:
            p = [0.0]
            for v in self.y:
                p.append(p[-1] + v)
            self._prefix = p
        p, y, n = self._prefix, self.y, len(self.y)

        def integral(t):
            if t <= 0:
                return 0.0
            if t >= n:
                return p[n]
            i = int(t)
            return p[i] + (t - i) * y[i]
        if b <= a:
            return y[min(n - 1, max(0, int(a)))]
        return (integral(b) - integral(a)) / (b - a)

    def parts(self, i, n):
        a, b = self.edges[i], self.edges[i + 1]
        w = (b - a) / n
        return [self.mean(a + j * w, a + (j + 1) * w) for j in range(n)]


def pam_burst(model, levels, n_periods, rng, ppm=0.0, lead_us=20000.0,
              noise=None, offset=0.0, flicker=None):
    """k-level PAM, a symbol every n_periods PWM periods, paced by DMA: each
    duty lands on a wrap, as exact as the transmitter's crystal. The
    receiver's grid is nominal -- `ppm` is the transmitter's clock against
    it, the differential offset of the pair -- `offset` PWM periods late,
    and does not include the sensor's dead time (model.delay_us).
    `flicker` (amplitude in raw, Hz) adds mains flicker: the fundamental
    and a second harmonic at 0.4 of it, at a random phase."""
    sym_us = n_periods * PWM_US
    tx = sym_us * (1 + ppm * 1e-6)
    events = [(lead_us + i * tx + model.delay_us, model.targets[lv])
              for i, lv in enumerate(levels)]
    total = lead_us + (len(levels) + 1) * sym_us + 2000.0
    nbins = int(total / BIN_US)
    y = respond(events, nbins, BIN_US, model, model.targets[0],
                model.targets[0], rng, noise)
    if flicker is not None:
        amp, hz = flicker
        p1, p2 = rng.uniform(0, 2 * math.pi), rng.uniform(0, 2 * math.pi)
        for b in range(nbins):
            t = (b + 0.5) * BIN_US * 1e-6
            y[b] += amp * (math.sin(2 * math.pi * hz * t + p1)
                           + 0.4 * math.sin(4 * math.pi * hz * t + p2))
    start = lead_us + offset * PWM_US
    edges = [(start + i * sym_us) / BIN_US for i in range(len(levels) + 1)]
    return Burst(y, edges, list(levels), len(model.targets))


def shifted(burst, bins):
    """The same bins on a grid moved by `bins`."""
    out = Burst(burst.y, [e + bins for e in burst.edges], burst.levels,
                burst.k)
    out._prefix = burst._prefix
    out.flicker_hz = burst.flicker_hz
    out.track = burst.track
    return out


def preamble_sse(burst, train, parts=3):
    """The linear equaliser's training error on the preamble."""
    rows = ff_rows(burst, train, parts)
    w = least_squares([rows[i] for i in train],
                                 [float(burst.levels[i]) for i in train])
    return sum((sum(a * b for a, b in zip(w, rows[i])) - burst.levels[i]) ** 2
               for i in train)


def acquire(burst, train, span_periods=3.5, step=0.25, period_us=None):
    """Finds the grid from the preamble: the shift, in PWM periods within
    +-span, whose trained linear equaliser fits the known symbols best.
    Returns (shift in periods, the shifted burst)."""
    per = (period_us or PWM_US) / BIN_US
    best = None
    n = int(span_periods / step)
    for j in range(-n, n + 1):
        cand = shifted(burst, j * step * per)
        sse = preamble_sse(cand, train)
        if best is None or sse < best[0]:
            best = (sse, j * step, cand)
    return best[1], best[2]


def q(x):
    return 0.5 * math.erfc(x / math.sqrt(2.0))


def symbol_range(burst, train):
    """(training indices, test indices): symbols 3 to n - 2, the first
    `train` of them the preamble."""
    usable = list(range(3, len(burst.levels) - 1))
    return usable[:train], usable[train:]


class Sliced:
    """Decisions by the nearest of the levels' training means, and a
    Gaussian estimate of the error rate from the test values' spread."""

    def __init__(self, z, levels, k, train):
        sums = [[] for _ in range(k)]
        for i in train:
            sums[levels[i]].append(z[i])
        self.means = [sum(v) / len(v) if v else float("nan") for v in sums]
        self.k = k

    def decide(self, v):
        best, at = None, 0
        for lv, m in enumerate(self.means):
            if m == m and (best is None or abs(v - m) < best):
                best, at = abs(v - m), lv
        return at

    def gaussian(self, z, levels, test):
        """Mean over test symbols of P(wrong side of a midpoint), each
        level's values taken as Gaussian with their own mean and spread."""
        order = sorted((m, lv) for lv, m in enumerate(self.means) if m == m)
        cuts = {}
        for j, (m, lv) in enumerate(order):
            lo = (order[j - 1][0] + m) / 2 if j else -1e30
            hi = (order[j + 1][0] + m) / 2 if j + 1 < len(order) else 1e30
            cuts[lv] = (lo, hi)
        total, count = 0.0, 0
        for lv in range(self.k):
            v = [z[i] for i in test if levels[i] == lv]
            if len(v) < 2 or lv not in cuts:
                continue
            mu = sum(v) / len(v)
            sd = math.sqrt(sum((x - mu) ** 2 for x in v) / (len(v) - 1)) \
                or 1e-9
            lo, hi = cuts[lv]
            total += len(v) * (q((mu - lo) / sd) + q((hi - mu) / sd))
            count += len(v)
        return total / count if count else None


def ff_rows(burst, idx, parts, before=2, after=1):
    """The feed-forward taps: `parts` means of each of symbols i - before
    .. i + after -- fractionally spaced -- and a constant."""
    cache = {}
    rows = {}
    for i in idx:
        row = []
        for j in range(i - before, i + after + 1):
            if j not in cache:
                cache[j] = burst.parts(j, parts)
            row += cache[j]
        rows[i] = row + burst.flicker_terms(i) + [1.0]
    return rows


def tracked_slots(burst, parts, symbols):
    """Indices of the flicker terms and the constant in a feed-forward row
    over `symbols` symbols: what decision-directed tracking adapts."""
    if not burst.track or not burst.flicker_hz:
        return []
    first = symbols * parts
    return list(range(first, first + 5))


def adapt(w, row, slots, error, mu):
    """One LMS step on the tracked weights: the mains' phase and amplitude
    and the baseline follow the block, from the decisions' errors."""
    for j in slots:
        w[j] += mu * error * row[j]


def linear(burst, train, test, parts=3):
    """A linear equaliser: least squares onto the level index, trained on
    the preamble, then sliced at the training outputs' means. With
    burst.track, the flicker terms and the constant then adapt symbol by
    symbol on the decisions (LMS)."""
    rows = ff_rows(burst, train + test, parts)
    w = least_squares([rows[i] for i in train],
                                 [float(burst.levels[i]) for i in train])
    z = {i: sum(a * b for a, b in zip(w, rows[i])) for i in train + test}
    s = Sliced(z, burst.levels, burst.k, train)
    slots = tracked_slots(burst, parts, 4)
    if not slots:
        decided = {i: s.decide(z[i]) for i in test}
        return decided, s.gaussian(z, burst.levels, test)
    decided = {}
    for i in test:
        v = sum(a * b for a, b in zip(w, rows[i]))
        decided[i] = s.decide(v)
        adapt(w, rows[i], slots, decided[i] - v, burst.track)
    return decided, None


def pam_link_bursts(path_or_bytes, search=True):
    """[(head, Burst)] from a pam_link .bin. The trigger is bracketed by
    two reads of the slice's counter: the first wrap after `ctr0` is where
    the DMA writes the first CC word, and it latches at the next wrap, as
    optic_fake models it. If the counter wrapped between the two reads
    (`ctr` < `ctr0`) the trigger may have followed that wrap, putting the
    landing a period later: `ambiguous`. Whether the hardware counts a wrap
    from before the trigger at all is [unverified]. With `search`, the
    preamble settles it: acquire() over +-1.5 periods (`shift`)."""
    magic, caps = read(path_or_bytes)
    if magic != "PAMLINK1":
        raise ValueError("not a pam_link capture: %s" % magic)
    out = []
    for head, blobs in caps:
        per, width = head["period_us"], head["bin"]
        tick = pwm_tick_us(head)
        pwm = (head["top"] + 1) * tick
        land = head["before"] * per + (head["top"] + 1 - head["ctr0"]) * tick \
            + pwm
        y = [v / float(width) for v in blobs["bins"]]
        levels = list(blobs["syms"])
        sym = head["periods"] * pwm
        edges = [(land + i * sym) / (per * width)
                 for i in range(len(levels) + 1)]
        train = min(TRAIN[head["k"]], len(levels) // 2)
        burst = Burst(y, edges, levels, head["k"])
        head = dict(head, train=train, pwm_us=pwm,
                    ambiguous=head["ctr"] < head["ctr0"], shift=0.0)
        if search:
            tr, _ = symbol_range(burst, train)
            head["shift"], burst = acquire(burst, tr, 1.5, period_us=pwm)
        out.append((head, burst))
    return out


def rll_levels(n, rng, max_zeros):
    """Random bits with a 1 stuffed after every `max_zeros` 0s in a row:
    no run of lows longer than that. Returns (levels, data bits carried)."""
    out, run, data = [], 0, 0
    while len(out) < n:
        if run == max_zeros:
            out.append(1)
            run = 0
            continue
        bit = rng.randrange(2)
        out.append(bit)
        data += 1
        run = run + 1 if bit == 0 else 0
    return out, data


TRAIN = {2: 64, 4: 128, 8: 384}


TRACK_MU = 0.02     # the LMS step on the flicker terms and the constant
