"""The light levels: every symbol a backlight level behind a white screen.

Two tables in protocol.LEVEL_SCHEMES, each display.backlight() per symbol:
SCHEME_BEACON for the beacons -- off and full -- and SCHEME_BACKLIGHT, with
its dim low, from the staircases on. There are no pixel levels: the screen
is white from before a transfer to after it. See section 4 of the protocol spec.
"""

import math
import random

import simulator
from harness import load_module

P = load_module("share_wifi", "protocol")
BACKLIGHT = P.SCHEME_BACKLIGHT


def measured_staircase(settle_ms=4, noise=80, seed=1, lit=False):
    """A StaircaseReader fed a Follower's staircase through a lagging
    sensor, a sample every FINE_MS."""
    rng = random.Random(seed)
    sample_ms = P.FINE_MS
    tau = settle_ms / 2.3
    reps = P.CAL_REPS_FAST_LIT if lit else P.CAL_REPS_FAST
    length = P.staircase_ms(reps, P.CAL_OPEN_FAST_MS, P.PLATEAU_FAST_MS,
                            P.CAL_GAP_FAST_MS)
    level, t = 0.0, 0
    shares = {P.LOW: 0.0, P.MID: 0.45, P.HIGH: 1.0}
    reader = P.StaircaseReader()
    while t < length + 600:
        field = P.staircase_field(t, P.STAIRCASE, reps, P.CAL_OPEN_FAST_MS,
                                  P.PLATEAU_FAST_MS) \
            if t < length else P.LOW
        level += (shares[field] - level) * (1 - math.exp(-sample_ms / tau))
        light = int(200 + level * 9000 + rng.uniform(-noise, noise))
        if reader.feed(t, light):
            break
        t += sample_ms
    return reader


# --- the staircase's levels through a lagging sensor ------------------------

def test_a_staircase_through_a_lagging_sensor_reads_back_three_levels():
    """The order is the fast flag, tested in test_handshake.py: here what
    matters is that the levels and the light come back out of the samples,
    lit and dark."""
    for lit in (True, False):
        for seed in (1, 2, 3):
            reader = measured_staircase(seed=seed, lit=lit)
            levels = reader.levels()
            assert levels is not None, (lit, seed)
            assert abs(levels.black - 200) < 200
            assert abs(levels.grey - (200 + 0.45 * 9000)) < 400
            assert abs(levels.white - 9200) < 400
            highs = P.staircase_highs(reader.values, levels)
            assert (highs <= P.LIT_STEPS_MAX_FAST) == lit, (lit, seed)


# --- the tables -----------------------------------------------------------------

def test_every_level_is_a_backlight_value_and_nothing_else():
    """No pixel signalling: each table maps a symbol to one
    display.backlight() value, and there are only the two tables."""
    assert set(P.LEVEL_SCHEMES) == {P.SCHEME_BACKLIGHT, P.SCHEME_BEACON}
    for table in P.LEVEL_SCHEMES.values():
        assert set(table) == {P.LOW, P.MID, P.HIGH}
        for value in table.values():
            assert isinstance(value, float) and 0.0 <= value <= 1.0, value
    for name in ("SCHEME_FIELDS", "MID_FIELD_RGB", "LCD_SETTLE_MS",
                 "BACKLIGHT_SETTLE_MS", "END_GUARD_PERCENT"):
        assert not hasattr(P, name), name


def test_the_data_table():
    table = P.LEVEL_SCHEMES[BACKLIGHT]
    assert table == {P.LOW: P.LOW_BACKLIGHT, P.MID: P.MID_BACKLIGHT,
                     P.HIGH: 1.0}
    assert P.LOW_BACKLIGHT < P.MID_BACKLIGHT < 1.0


def test_the_beacon_table_is_the_backlight_off_or_full():
    beacon = P.LEVEL_SCHEMES[P.SCHEME_BEACON]
    assert beacon[P.LOW] == P.BEACON_LOW_BACKLIGHT == 0.0
    assert beacon[P.HIGH] == 1.0
    # Off is off on both firmwares, unlike the dim low.
    assert simulator.backlight_duty_v2(0.0) == simulator.backlight_duty(0.0) == 0


def test_beacons_are_beacons_and_everything_after_is_the_backlight():
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
    assert seen[P.PHASE_ANNOUNCE] == {P.SCHEME_BEACON}
    assert seen[P.PHASE_LISTEN] == {P.SCHEME_BEACON}
    assert seen[P.PHASE_DATA] == {BACKLIGHT}
    assert BACKLIGHT in seen[P.PHASE_CAL_SEND]
