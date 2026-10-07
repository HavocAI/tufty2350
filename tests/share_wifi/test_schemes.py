"""The light-level schemes: fields for the beacons, the backlight for the rest.

There is one calibration for every pair now: one staircase each way, in the
backlight scheme. Fields was the fallback for a sensor that follows the
backlight's PWM, which the badge's does not [measured-all], and its 80 ms
floor made it five to ten times slower; it is kept for the beacons, which
are drawn before anything is calibrated. See "Light levels and symbols" and
"Calibration" in the protocol.
"""

import math
import random

import simulator
from harness import load_module

P = load_module("share_wifi", "protocol")
FIELDS, BACKLIGHT = P.SCHEME_FIELDS, P.SCHEME_BACKLIGHT


def measured_staircase(order, sample_ms=P.CAL_SAMPLE_MS, settle_ms=4,
                       noise=80, seed=1):
    """A StaircaseReader fed one staircase through a lagging sensor."""
    rng = random.Random(seed)
    tau = settle_ms / 2.3
    level, t = 0.0, 0
    shares = {P.LOW: 0.0, P.MID: 0.45, P.HIGH: 1.0}
    reader = P.StaircaseReader()
    while t < P.STAIRCASE_MS + 600:
        field = P.staircase_field(t, order) if t < P.STAIRCASE_MS else P.LOW
        level += (shares[field] - level) * (1 - math.exp(-sample_ms / tau))
        light = int(200 + level * 9000 + rng.uniform(-noise, noise))
        if reader.feed(t, light):
            break
        t += sample_ms
    return reader


# --- the order flag: the reader tier ------------------------------------------

def test_the_order_of_a_staircase_can_be_read_back_out_of_its_samples():
    for order in (P.ASCENDING, P.REVERSED):
        for seed in (1, 2, 3):
            reader = measured_staircase(order, seed=seed)
            levels = reader.levels()
            assert levels is not None
            rising, falling, _ = P.staircase_steps(reader.values, levels)
            read = P.REVERSED if falling > rising else P.ASCENDING
            assert read == order, \
                "seed %d: read %r back as the other order" % (seed, order)


def test_order_does_not_change_the_levels_calibration_measures():
    up = measured_staircase(P.ASCENDING).levels()
    down = measured_staircase(P.REVERSED).levels()
    for name in ("black", "grey", "white"):
        assert abs(getattr(up, name) - getattr(down, name)) < 200, name


# --- which scheme when -----------------------------------------------------------

def test_the_backlight_scheme_never_changes_a_pixel():
    pixels = set(p for p, _ in P.LEVEL_SCHEMES[BACKLIGHT].values())
    assert pixels == {"white"}
    assert P.LEVEL_SCHEMES[BACKLIGHT][P.LOW][1] == P.LOW_BACKLIGHT
    assert P.LEVEL_SCHEMES[BACKLIGHT][P.MID][1] == P.MID_BACKLIGHT


def test_beacons_are_fields_and_everything_after_is_the_backlight():
    link = simulator.build(P, simulator.TYPICAL, seed=1,
                           **simulator.SCENARIOS["realistic"])
    seen = {}
    for badge in (link.leader, link.follower):
        def show(now, field, _badge=badge, _real=badge.show):
            seen.setdefault(_badge.transfer.phase, set()).add(
                _badge.transfer.scheme)
            _real(now, field)
        badge.show = show
    link.run(200)
    link.assert_invariants()
    assert link.leader.transfer.result == P.OK
    assert seen[P.PHASE_ANNOUNCE] == {FIELDS}
    assert seen[P.PHASE_DATA] == {BACKLIGHT}
    assert BACKLIGHT in seen[P.PHASE_CAL_SEND]
