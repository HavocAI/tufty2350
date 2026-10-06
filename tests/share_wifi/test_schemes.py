"""Choosing a light-level scheme during calibration.

The Leader offers SCHEME_BACKLIGHT by sending a second staircase in it, and
says so by sending the first one REVERSED. The Follower measures both, keeps
whichever separates the levels better, and answers with its own staircase in
that scheme -- REVERSED for backlight. See "Choosing a scheme" in the spec.
"""

import math
import random

import simulator
from harness import load_module

P = load_module("share_wifi", "protocol")
FIELDS, BACKLIGHT = P.SCHEME_FIELDS, P.SCHEME_BACKLIGHT
AUTO = (FIELDS, BACKLIGHT)


def measured_staircase(order, frame_ms=11, settle_ms=16, noise=80, seed=1):
    """A StaircaseReader fed one staircase through a lagging panel."""
    rng = random.Random(seed)
    tau = settle_ms / 2.3
    level, t = 0.0, 0
    shares = {P.LOW: 0.0, P.MID: 0.45, P.HIGH: 1.0}
    reader = P.StaircaseReader()
    while t < P.STAIRCASE_MS + 600:
        field = P.staircase_field(t, order) if t < P.STAIRCASE_MS else P.LOW
        level += (shares[field] - level) * (1 - math.exp(-frame_ms / tau))
        light = int(200 + level * 9000 + rng.uniform(-noise, noise))
        if reader.feed(t, light):
            break
        t += frame_ms
    return reader


def run(schemes, follower_schemes=None, pwm_ripple=0.0, seed=1,
        scenario="realistic"):
    link = simulator.build(P, simulator.TYPICAL, seed=seed, schemes=schemes,
                           follower_schemes=follower_schemes,
                           pwm_ripple=pwm_ripple, **simulator.SCENARIOS[scenario])
    link.run(200)
    link.assert_invariants()
    return link.leader.transfer, link.follower.transfer


def delivered(leader, follower):
    return (leader.result == P.OK and
            P.parse_payload(follower.received) == list(simulator.TYPICAL))


# --- the order flag ---------------------------------------------------------

def test_the_order_of_a_staircase_can_be_read_back_out_of_its_samples():
    for order in (P.ASCENDING, P.REVERSED):
        for seed in (1, 2, 3):
            reader = measured_staircase(order, seed=seed)
            levels = reader.levels()
            assert levels is not None
            assert P.staircase_order(reader.values, levels) == order, \
                "seed %d: read %r back as the other order" % (seed, order)


def test_order_does_not_change_the_levels_calibration_measures():
    up = measured_staircase(P.ASCENDING).levels()
    down = measured_staircase(P.REVERSED).levels()
    for name in ("black", "grey", "white"):
        assert abs(getattr(up, name) - getattr(down, name)) < 200, name


def test_a_middle_step_that_did_not_hold_steady_scores_nothing():
    # What a sensor following the backlight's PWM does to the middle step:
    # its samples scatter to either end, leaving too few in the middle.
    levels = P.Levels()
    levels.fit(1000, 5000, 9000)
    levels.middle_samples, levels.high_samples = 50, 50
    assert P.separation(levels) == 4000
    levels.middle_samples = 10
    assert P.separation(levels) == 0


# --- the choice, end to end -------------------------------------------------

def test_fields_only_sends_a_single_staircase_and_never_leaves_fields():
    leader, follower = run((FIELDS,))
    assert follower.cal_index == 0 and not follower.more_coming
    assert leader.scheme == follower.scheme == FIELDS
    assert delivered(leader, follower)


def test_both_badges_offering_it_choose_the_backlight_when_it_measures_better():
    # An averaging sensor, where white at 50% duty sits at the middle of the
    # swing and the grey field at 45% of it.
    for seed in (1, 2, 3):
        leader, follower = run(AUTO, seed=seed)
        assert follower.more_coming, "the Follower missed the second staircase"
        assert P.separation(follower.measured[BACKLIGHT]) > \
            P.separation(follower.measured[FIELDS])
        assert leader.scheme == follower.scheme == BACKLIGHT, \
            "seed %d: %s / %s" % (seed, leader.scheme, follower.scheme)
        assert delivered(leader, follower), "seed %d" % seed


def test_a_sensor_that_follows_the_pwm_falls_back_to_fields():
    # The case the backlight scheme cannot survive, and the reason the choice
    # exists: choosing must not make a working link fail.
    for seed in (1, 2, 3):
        leader, follower = run(AUTO, pwm_ripple=1.0, seed=seed)
        assert leader.scheme == follower.scheme == FIELDS, \
            "seed %d: %s / %s" % (seed, leader.scheme, follower.scheme)
        assert delivered(leader, follower), "seed %d" % seed


def test_a_badge_set_to_fields_only_pairs_with_one_set_to_choose():
    for leader_schemes, follower_schemes in (((FIELDS,), AUTO), (AUTO, (FIELDS,))):
        leader, follower = run(leader_schemes, follower_schemes)
        assert leader.scheme == follower.scheme == FIELDS
        assert delivered(leader, follower)


def test_a_second_staircase_that_never_arrives_does_not_stall_the_follower():
    # Feed a REVERSED staircase -- "a second follows" -- and then black for
    # ever. The Follower must give up on the second at its fixed length and
    # answer in fields, not time out of calibration.
    follower = P.Transfer(False, schemes=AUTO, frame_ms=lambda: 11)
    follower.phase = P.PHASE_CAL_RECV
    follower.phase_started = 0
    follower.reader = P.StaircaseReader()
    rng = random.Random(1)
    t, level = 0, 0.0
    shares = {P.LOW: 0.0, P.MID: 0.45, P.HIGH: 1.0}
    while t < 3 * P.STAIRCASE_MS and follower.phase == P.PHASE_CAL_RECV:
        field = P.staircase_field(t, P.REVERSED) if t < P.STAIRCASE_MS else P.LOW
        level += (shares[field] - level) * 0.8
        follower.step(t, int(200 + level * 9000 + rng.uniform(-80, 80)))
        t += 11
    assert follower.result is None, "gave up: %s" % follower.result
    assert follower.phase == P.PHASE_CAL_SEND
    assert follower.more_coming and BACKLIGHT not in follower.measured
    assert follower.scheme == FIELDS
