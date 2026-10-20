"""The flicker correction's hoisted path is bit for bit the per-part one.

On the badge a call of FlickerFilter.amount() cost ~0.6 ms [measured-v2,
2026-10-07a]: five a decided slot, dozens a phase-lock scan. So an EXACT
window's parts, and try_lock's thresholds, look the fitted table up once
and the phase lock's shift once (FlickerFilter.amounts()), and work each
part's own base out exactly. That may change nothing a slot decides: these
pin it, for a fitted filter -- with and without a shift that moves inside a
window -- a learner and a steady one, for both readers' windows and for
try_lock's thresholds.
"""

import math
import random

from harness import load_module
from held import HeldTransfer
import held

P = load_module("share_wifi", "protocol")
R = load_module("share_wifi", "reader")


def room(t_us):
    cycle = 2 * math.pi * 60 * t_us / 1e6
    return 88 + 22 * math.sin(2 * cycle + 0.7) + 9 * math.sin(4 * cycle + 2.1)


def fitted(rate=0.0):
    f = P.FlickerFilter()
    rng = random.Random(3)
    ts, vs, ss = [], [], []
    t = 1000
    while t < 161000:
        mean = sum(room(t - 1000 + k * 100) for k in range(20)) / 20
        ts.append(t)
        vs.append(int(round(mean + rng.gauss(0, 1))))
        ss.append(2000)
        t += 2000
    assert f.fit(ts, vs, ss) == "flicker"
    if rate:
        # A phase lock running fast enough that its shift steps inside a
        # window: the hoist must fall back, not round.
        f.rate = rate
        f.rate_m = int(rate * 1000)
    return f


def learner():
    f = P.FlickerFilter()
    t = 0
    for _ in range(160):
        for _ in range(8):
            t += 1000
            f.collect(t, int(88 + 22 * math.sin(2 * math.pi * 120 * t / 1e6)),
                      1000)
        t += 8000
        f.close(True)
    assert f.harmonics is None and f.active_now()
    return f


def steady():
    f = P.FlickerFilter()
    ts = list(range(1000, 161000, 2000))
    assert f.fit(ts, [88] * len(ts), [2000] * len(ts)) == "steady"
    return f


def offset(off_m, rate):
    f = fitted(rate)
    f.off_m = off_m
    f.offset = off_m / 1000.0
    return f


FILTERS = (("fitted", fitted), ("fitted, shifting", lambda: fitted(31.0)),
           ("fitted, shifting back", lambda: fitted(-31.0)),
           ("fitted, offset -1500", lambda: offset(-1500, -7.0)),
           ("fitted, offset +2500", lambda: offset(2500, 9.0)),
           ("learner", learner), ("steady", steady))


def test_amounts_is_amount_for_every_filter():
    for name, make in FILTERS:
        f = make()
        for start in (0, 999000, 5000000, 29999000):
            for span in (100, 720, 2000):
                times = [start + j * span + span // 2 for j in range(32)]
                got = [0] * 32
                f.amounts(times, 32, span, got)
                want = [f.amount(t, span) for t in times]
                assert got == want, (name, start, span)


def listener(flicker):
    reader = held.HeldReader()
    reader.start()
    t = HeldTransfer(False, None, reader)
    t.levels = P.Levels()
    t.levels.fit(32, 64, 112)
    t.start_data(0, P.EXACT_INDEX)
    t.flicker = flicker
    rng = random.Random(5)
    for us in range(0, 400000, 100):
        reader.add(us, int(room(us)) + rng.randrange(-8, 9))
    return t


def reference(t, k):
    """window_value() as it was: every part corrected on its own."""
    start = t.clock.start_of(k)
    opens = start + t.w_open
    n = t.w_parts
    span = (t.w_close - t.w_open) // n
    raw = [0] * n
    times = [0] * n
    t.reader.parts(opens, opens + span * n, n, raw, times)
    values = sorted(t.flicker.correct(times[j], raw[j], span)
                    for j in range(n) if raw[j] >= 0)
    if not values:
        return None
    half = len(values) // 2
    return values[half] if len(values) & 1 else \
        (values[half - 1] + values[half]) // 2


def test_window_values_are_unchanged():
    for name, make in FILTERS:
        t = listener(make())
        for k in range(1, 18):
            want = reference(t, k)
            got = t.window_value(k)
            if want is not None:
                assert got == want, (name, k, got, want)


def test_try_locks_thresholds_are_unchanged():
    for name, make in FILTERS:
        t = listener(make())
        seen = []
        real = t.reader.rise

        def rise(t0, t1, thr, n, step):
            seen.append((t0, step, list(thr[:n])))
            return real(t0, t1, thr, n, step)
        t.reader.rise = rise
        t.lit_end = 0
        t.try_lock(20)
        assert seen, name
        t0, step, thr = seen[0]
        want = [t.levels.t_low + t.flicker.amount(t0 + i * step + step // 2,
                                                  100)
                if t.flicker.active_now() else t.levels.t_low
                for i in range(len(thr))]
        assert thr == want, name
