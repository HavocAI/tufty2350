"""The flicker fitted before calibration (option A in the protocol): the
mains and the light's shape from a dark window, a table to correct with, and
the phase lock that follows the mains' drift.

Samples here are what the app's loop hands the protocol: the mean of 2 ms of
readings, every ~3 ms, at their mean time.
"""

import math
import random

import simulator as sim
from harness import load_module

P = load_module("share_wifi", "protocol")


def light(mains, shape, size=22.0, drift=0.0, mean=88.0, seed=1):
    """A room light: `shape` ((harmonic, weight), ...) of `mains`, the
    largest component `size` counts."""
    rng = random.Random(seed)
    phases = [rng.uniform(0, 2 * math.pi) for _ in shape]
    hz = mains + drift

    def at(t_us):
        cycle = 2 * math.pi * hz * t_us / 1e6
        wave = 0.0
        for (h, w), ph in zip(shape, phases):
            wave += w * math.sin(h * cycle + ph)
        return mean + size * wave
    return at


def samples(source, start_us, ms, noise=1.0, seed=2, span=2000, every=3000):
    """(times, values, spans) of samples of `source` for `ms`."""
    rng = random.Random(seed)
    ts, vs, ss = [], [], []
    t = start_us
    while t < start_us + ms * 1000:
        mean = sum(source(t - span // 2 + k * span // 20)
                   for k in range(20)) / 20
        ts.append(t)
        vs.append(int(round(mean + rng.gauss(0, noise))))
        ss.append(span)
        t += every + rng.randrange(-200, 200)
    return ts, vs, ss


def residual(f, source, start_us, ms=200):
    ts, vs, ss = samples(source, start_us, ms, noise=0.0, seed=9)
    return max(abs(f.correct(t, v, s) - 88) for t, v, s in zip(ts, vs, ss))


def test_the_fit_finds_the_mains_and_the_shape():
    for mains, shape in ((60, ((2, 1.0), (4, 0.3))),
                         (50, ((2, 1.0), (4, 0.3))),
                         (60, ((1, 1.0), (2, 0.5), (3, 0.2))),
                         (50, ((1, 1.0), (2, 0.5), (3, 0.2)))):
        source = light(mains, shape)
        f = P.FlickerFilter()
        assert f.fit(*samples(source, 123456, 200)) == "flicker"
        assert f.mains == mains, (mains, shape, f.mains)
        assert abs(f.size() - 22) < 2, f.size()
        # Taken off, a sample is the room's own light within a count or two.
        assert residual(f, source, 123456 + 210000) <= 2


def test_a_room_with_no_flicker_is_steady():
    f = P.FlickerFilter()
    ts, vs, ss = samples(lambda t: 40.0, 5000, 200, noise=6.0)
    vs = [v // 16 * 16 for v in vs]         # the ADC's 16-count steps
    assert f.fit(ts, vs, ss) == "steady"
    assert f.correct(77777, 40, 2000) == 40


def test_noise_in_long_samples_is_not_taken_for_a_harmonic():
    # Samples 4 ms long see 240 Hz at 4% of its size: dividing by that once
    # turned noise into 8-10 counts of flicker in a dark room.
    f = P.FlickerFilter()
    ts, vs, ss = samples(lambda t: 32.0, 5000, 200, noise=6.0, span=4000,
                         every=3500)
    assert f.fit(ts, vs, ss) == "steady"


def test_a_sensor_still_settling_is_not_flicker():
    f = P.FlickerFilter()
    decay = lambda t: 40.0 + 30.0 * math.exp(-(t - 5000) / 60000.0)
    assert f.fit(*samples(decay, 5000, 200, noise=2.0)) == "steady"


def test_a_window_with_a_step_in_it_is_refused():
    f = P.FlickerFilter()
    step = lambda t: 120.0 if t < 80000 else 60.0
    assert f.fit(*samples(step, 5000, 200)) is None
    assert not f.fitted


def test_the_phase_lock_follows_the_mains_drift():
    # 0.02 Hz off nominal: the table's timing moves 6.4 steps a second, and
    # 120 Hz by 14 degrees. Tracked from dark stretches, the rate is learnt
    # and the correction stays good after five seconds.
    source = light(60, ((2, 1.0), (4, 0.3)), drift=0.02)
    f = P.FlickerFilter()
    assert f.fit(*samples(source, 0, 200)) == "flicker"
    t = 200000
    while t < 5000000:
        ts, vs, ss = samples(source, t, 24, seed=t)
        for args in zip(ts, vs, ss):
            f.track(*args)
        f.update()
        t += 60000
    assert abs(f.rate - 6.4) < 1.0, f.rate
    assert residual(f, source, t) <= 3


def test_without_the_lock_the_drift_ruins_the_table():
    # What the lock is for: the same light, fitted and left.
    source = light(60, ((2, 1.0), (4, 0.3)), drift=0.02)
    f = P.FlickerFilter()
    assert f.fit(*samples(source, 0, 200)) == "flicker"
    assert residual(f, source, 5000000) > 10


# --- end to end ----------------------------------------------------------------

def run(light_name, seed, drift=0.02, flicker=0.47):
    kw = dict(sim.SCENARIOS["realistic"])
    kw.update(sim.LOOP_V2)
    kw.update(sim.LOOP_SENSOR_V2)
    kw.update(contrast=sim.SWING_MIRROR, room_light=48, flicker=flicker,
              light=light_name, mains_drift_hz=drift)
    link = sim.build_loop(P, sim.TYPICAL, seed=seed,
                          schemes=(P.SCHEME_FIELDS, P.SCHEME_BACKLIGHT), **kw)
    link.run(300)
    return link


def test_a_light_flickering_once_a_cycle_no_longer_defeats_calibration():
    # 0 of 144 before the fit: calibration's 8 ms samples cancel 120 Hz by
    # luck, and leave three quarters of 50 or 60.
    for light_name, mains in (("half60", 60), ("half50", 50)):
        link = run(light_name, 1, drift=0.02 if mains == 60 else -0.02)
        for transfer in (link.leader.transfer, link.follower.transfer):
            assert transfer.result == P.OK, (light_name, transfer.result)
            assert transfer.fit_result == "flicker"
            assert transfer.flicker.mains == mains
        assert P.SLOT_LADDER[link.leader.transfer.clock.index] == 16


def test_both_badges_lock_onto_the_drift():
    link = run("led60", 2)
    for transfer in (link.leader.transfer, link.follower.transfer):
        assert transfer.result == P.OK
        assert abs(transfer.flicker.rate - 6.4) < 1.5, transfer.flicker.rate


def test_a_dark_room_without_flicker_fits_steady():
    kw = dict(sim.SCENARIOS["realistic"])
    kw.update(sim.LOOP_V2)
    kw.update(sim.LOOP_SENSOR_V2)
    kw.update(contrast=sim.SWING_MIRROR)
    link = sim.build_loop(P, sim.TYPICAL, seed=3,
                          schemes=(P.SCHEME_FIELDS, P.SCHEME_BACKLIGHT), **kw)
    link.run(300)
    for transfer in (link.leader.transfer, link.follower.transfer):
        assert transfer.result == P.OK
        assert transfer.fit_result == "steady", transfer.fit_result
