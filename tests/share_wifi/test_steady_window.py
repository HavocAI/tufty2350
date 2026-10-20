"""The flicker fit is skipped in a steady room.

A badge fits the room's flicker once, from a window its peer was dark for,
and the fit stalls its loop 130-153 ms on v2. In a room with no flicker the
fit only ever says "steady". FlickerFilter.steady_window() says so first,
from a straight line through the window: an rms about the line under
FLICKER_STEADY_RMS leaves no room for any harmonic the fit would keep.
See section 15 of the protocol spec.
"""

import math
import random

import simulator as sim
from harness import load_module
from held import HeldTransfer

P = load_module("share_wifi", "protocol")


def window(seed, sd=1.0, flicker=(), drift=0.0, n=50):
    """Fine samples every 2 ms: a level, a drift in counts a sample, noise
    of `sd`, and harmonics ((hz, counts), ...) as a 2 ms mean sees them."""
    rng = random.Random(seed)
    ts, vs = [], []
    phase = rng.uniform(0, 2 * math.pi)
    for k in range(n):
        t = 1000000 + k * P.FINE_US
        v = 300.0 + drift * k + rng.gauss(0, sd)
        for hz, counts in flicker:
            x = math.pi * hz * P.FINE_US / 1e6
            v += counts * math.sin(x) / x * math.sin(
                2 * math.pi * hz * t / 1e6 + phase)
        ts.append(t)
        vs.append(int(round(v)))
    return ts, vs, [P.FINE_US] * n


def test_the_threshold_is_the_weakest_harmonic_a_fit_keeps():
    # 240 Hz in a 2 ms mean keeps 0.66 of itself: a harmonic of
    # FLICKER_MIN_COUNTS there has an rms of 1.87 counts.
    assert 1.8 < P.FLICKER_STEADY_RMS < 1.95


def test_a_dark_ring_window_is_steady():
    f = P.FlickerFilter()
    for seed in range(20):
        ts, vs, _ = window(seed, sd=1.0, drift=0.05)
        assert f.steady_window(ts, vs), seed


def test_flicker_the_fit_would_keep_is_never_steady():
    f = P.FlickerFilter()
    for hz in (100, 120, 200, 240):
        for seed in range(10):
            ts, vs, _ = window(seed, sd=0.0,
                               flicker=((hz, P.FLICKER_MIN_COUNTS),))
            assert not f.steady_window(ts, vs), (hz, seed)


def test_steady_agrees_with_the_fit():
    """Wherever the check says steady, the fit says so too: over noise of
    0.5-2 counts and flicker of up to FLICKER_MIN_COUNTS."""
    agreed = 0
    for seed in range(200):
        rng = random.Random(seed)
        sd = rng.uniform(0.5, 2.0)
        hz = rng.choice((100, 120, 200, 240))
        counts = rng.uniform(0, P.FLICKER_MIN_COUNTS)
        ts, vs, spans = window(seed, sd=sd, flicker=((hz, counts),))
        if P.FlickerFilter().steady_window(ts, vs):
            assert P.FlickerFilter().fit(ts, vs, spans) == "steady", seed
            agreed += 1
    assert agreed > 20


def test_a_transfer_skips_the_fit_in_a_steady_room():
    t = HeldTransfer(False)
    ts, vs, spans = window(1, sd=1.0)
    for k in range(len(ts)):
        i = t.fit_n % P.FLICKER_BUFFER
        t.fit_t[i], t.fit_v[i], t.fit_s[i] = ts[k], vs[k], spans[k]
        t.fit_ms[i] = ts[k] // 1000
        t.fit_n += 1
    t.fit_flicker(100)
    assert t.fit_result == "steady" and t.flicker.steady
    assert t.fit_tried and t.fit_checked and not t.fit_ran


def test_a_transfer_fits_a_flickering_room():
    t = HeldTransfer(False)
    ts, vs, spans = window(1, sd=1.0, flicker=((120, 12),))
    for k in range(len(ts)):
        i = t.fit_n % P.FLICKER_BUFFER
        t.fit_t[i], t.fit_v[i], t.fit_s[i] = ts[k], vs[k], spans[k]
        t.fit_ms[i] = ts[k] // 1000
        t.fit_n += 1
    t.fit_flicker(100)
    assert t.fit_result == "flicker" and t.fit_ran


def test_the_simulator_charges_the_check_not_the_stall():
    """A dark pair never runs the fit; each badge pays the check."""
    link = sim.build_loop(P, sim.TYPICAL, seed=1, **dict(
        sim.SCENARIOS["realistic"], **sim.LOOP_V2,
        **dict(sim.LOOP_SENSOR_V2, contrast=sim.SWING_MIRROR)))
    link.run(30)
    for badge in (link.leader, link.follower):
        assert badge.transfer.fit_tried and not badge.transfer.fit_ran
    assert link.leader.transfer.result == P.OK
