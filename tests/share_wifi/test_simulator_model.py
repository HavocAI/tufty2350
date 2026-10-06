"""The simulator's model of the badge, where it was calibrated against one.

simulator.py is the evidence most of the protocol's figures rest on, so what
it assumes about the hardware is pinned here: the present delay measured on
2026-10-04, and that leaving it at zero reproduces every earlier result.
"""

import random

import simulator as sim
from harness import load_module

P = load_module("share_wifi", "protocol")


class Scripted:
    """A stand-in Transfer that shows a fixed sequence of fields."""

    def __init__(self, fields):
        self.fields = list(fields)
        self.scheme = None
        self.started = 0
        self.phase_started = 0

    def step(self, now, light):
        return self.fields.pop(0) if self.fields else self.fields_last

    fields_last = " "


def badge(fields, present_ms):
    return sim.SimBadge(Scripted(fields), sim.Panel(1), None, 16.7,
                        random.Random(1), present_ms=present_ms)


class NoSensor:
    def read(self, level):
        return 0


def test_without_a_present_delay_the_field_is_shown_at_once():
    b = badge(["-"], 0.0)
    b.sensor = NoSensor()
    b.tick(100, 0.0)
    assert b.shown == "-"


def test_a_field_reaches_the_panel_present_ms_after_its_frame():
    b = badge(["-", "_"], 19.0)
    b.sensor = NoSensor()
    b.tick(100, 0.0)                    # decides "-" at 100 ms
    b.present(118.9)
    assert b.shown == " ", "shown before its present completed"
    b.tick(116.7, 0.0)                  # the next frame decides "_"
    b.present(119.0)
    assert b.shown == "-"
    b.present(135.7)
    assert b.shown == "_", "a delay longer than a frame must queue, not drop"


def test_the_v2_profile_is_what_was_measured():
    assert sim.V2_MEASURED == dict(frame_ms=16.7, present_ms=19.0, settle_ms=24)
    # 90% of a swing in 24 ms is a time constant of about 10.4 ms: the
    # measured rise was 10.5.
    assert abs(sim.Panel(sim.SETTLE_MS_V2).tau - 10.5) < 0.2


def test_the_v2_profile_still_delivers():
    # As the app runs it: reading continuously, presenting only to change the
    # pixels. The once-a-frame loop against v2's measured present delay does
    # not: its edge correction leaves out the ~19 ms the delay adds, and on
    # the 80 ms rung the Follower's replies land on the Leader's slot
    # boundaries -- see "What the measurement found" in the protocol.
    for timing in (sim.LOOP_V2, sim.LOOP_V2_SLOW_PANEL, sim.LOOP_V3):
        link = sim.build_loop(P, sim.TYPICAL, seed=1,
                              **dict(sim.SCENARIOS["realistic"], **timing))
        link.run(300)
        assert link.leader.transfer.result == P.OK, timing
        assert P.parse_payload(link.follower.transfer.received) == \
            list(sim.TYPICAL)
        assert not link.violation, link.violation


def test_the_measured_backlight_runs_on_its_fast_rung():
    # Continuous badges on the measured mirror's sensor, offered both schemes,
    # choose the backlight and finish on BACKLIGHT_INDEX: no demotion.
    both = (P.SCHEME_FIELDS, P.SCHEME_BACKLIGHT)
    for seed in (1, 2, 3):
        link = sim.build_loop(P, sim.TYPICAL, seed=seed, schemes=both,
                              **dict(sim.SCENARIOS["realistic"], **dict(
                                  sim.LOOP_V2, contrast=sim.SWING_MIRROR,
                                  **sim.LOOP_SENSOR_V2)))
        link.run(300)
        leader = link.leader.transfer
        assert leader.result == P.OK, (seed, leader.result)
        assert leader.scheme == P.SCHEME_BACKLIGHT
        assert leader.clock.index == P.BACKLIGHT_INDEX, \
            P.SLOT_LADDER[leader.clock.index]


def test_a_continuous_badge_never_presents_in_the_backlight_data_phase():
    # Presents only change pixels, and the backlight scheme's are white for
    # every symbol: once both badges are in the data phase, nothing is
    # presented by either, so neither is ever blind to the other.
    both = (P.SCHEME_FIELDS, P.SCHEME_BACKLIGHT)
    link = sim.build_loop(P, sim.TYPICAL, seed=1, schemes=both,
                          **dict(sim.SCENARIOS["realistic"], **sim.LOOP_V2))
    changes = []
    for badge in (link.leader, link.follower):
        def schedule(when, kind, value, _badge=badge,
                     _real=badge.panel.schedule):
            changes.append((_badge.transfer.phase, kind))
            _real(when, kind, value)
        badge.panel.schedule = schedule
    link.run(300)
    assert link.leader.transfer.result == P.OK
    assert link.leader.transfer.scheme == P.SCHEME_BACKLIGHT
    in_data = [kind for phase, kind in changes if phase == P.PHASE_DATA]
    assert "duty" in in_data, "the data phase changed no backlight"
    assert "share" not in in_data, "a pixel change in the data phase"


def test_the_measured_sensor_reads_in_whole_steps_around_its_swing():
    rng = random.Random(3)
    sensor = sim.Sensor(sim.SENSOR_V2["ambient"][0], 80, 0, rng,
                        sd=sim.SENSOR_V2["sensor_sd"], quantum=16, reads=3)
    black = [sensor.read(0.0) for _ in range(400)]
    white = [sensor.read(1.0) for _ in range(400)]
    assert all(v % 16 == 0 for v in black + white)
    # Floored steps around 32 and 112, scattered by a step at most -- as the
    # mirror runs read them.
    assert set(black) <= {16, 32, 48} and set(white) <= {96, 112, 128}
    assert sorted(black)[200] == 32 and sorted(white)[200] == 112


def test_the_default_sensor_is_the_idealised_one():
    # Every result quoted before the measured model must still reproduce.
    sensor = sim.Sensor(200, 9000, 0, random.Random(1))
    assert sensor.read(0.5) == 4700


def test_the_peer_swings_follow_the_geometry():
    import optics
    mirror = optics.screen(5.3, lit=optics.BANDS)
    aligned = optics.screen(4.0)
    overlap = optics.screen(4.0, edge=3)
    reflectance = 0.85
    assert 0.9 < aligned / (reflectance * mirror) * sim.SWING_MIRROR \
        / sim.SWING_PEER_ALIGNED < 1.1
    assert 0.8 < overlap / (reflectance * mirror) * sim.SWING_MIRROR \
        / sim.SWING_PEER_OVERLAP < 1.2
    assert optics.screen(4.0, edge=40) < 3.1416      # never more than pi
