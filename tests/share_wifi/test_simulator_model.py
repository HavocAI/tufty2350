"""The simulator's model of the badge, where it was calibrated against one.

simulator.py is the evidence most of the protocol's figures rest on, so what
it assumes about the hardware is pinned here -- and that it runs the
production readers.
"""

import random

import simulator as sim
from harness import load_module
import held

P = load_module("share_wifi", "protocol")
R = load_module("share_wifi", "reader")


def test_the_v2_and_v3_profiles_deliver():
    for timing in (sim.LOOP_V2, sim.LOOP_V3):
        link = sim.build_loop(P, sim.TYPICAL, seed=1,
                              **dict(sim.SCENARIOS["realistic"], **timing))
        link.run(300)
        assert link.leader.transfer.result == P.OK, timing
        assert link.follower.transfer.received == \
            list(sim.TYPICAL)
        assert not link.violation, link.violation


def test_each_badge_gets_its_own_firmwares_pwm_period_and_curve():
    link = sim.build_loop(P, sim.TYPICAL, seed=1)
    assert link.leader.pwm_ms == link.follower.pwm_ms == sim.PWM_PERIOD_MS
    link = sim.build_loop(P, sim.TYPICAL, seed=1, **sim.LOOP_V3)
    assert link.leader.pwm_ms == link.follower.pwm_ms == sim.PWM_PERIOD_MS_V3
    assert link.leader.duty is sim.backlight_duty_v3
    link = sim.build_loop(P, sim.TYPICAL, seed=1,
                          **sim.mixed_firmware(sim.LOOP_V2, sim.LOOP_V3))
    lead, follow = link.leader, link.follower
    assert (lead.pwm_ms, follow.pwm_ms) == (sim.PWM_PERIOD_MS,
                                            sim.PWM_PERIOD_MS_V3)
    assert lead.duty is sim.backlight_duty_v2
    assert follow.duty is sim.backlight_duty_v3


def test_a_v2_and_a_v3_badge_pair():
    for lead, follow in ((sim.LOOP_V2, sim.LOOP_V3),
                         (sim.LOOP_V3, sim.LOOP_V2)):
        link = sim.build_loop(P, sim.TYPICAL, seed=1,
                              **dict(sim.SCENARIOS["realistic"],
                                     **sim.mixed_firmware(lead, follow)))
        link.run(300)
        assert link.leader.transfer.result == P.OK
        assert link.follower.transfer.received == \
            list(sim.TYPICAL)
        assert not link.violation, link.violation


def test_the_simulator_runs_the_production_readers():
    link = sim.build_loop(P, sim.TYPICAL, seed=1)
    assert type(link.leader.reader) is R.RingReader
    assert type(link.leader.reader.ring) is held.ListRing
    # The ring is a power of two, its history the badge's.
    ring = link.leader.reader.ring
    assert ring.samples == sim.RING_ENTRIES == 4096
    assert link.leader.reader.usable == sim.RING_USABLE_US


def test_the_measured_sensor_runs_on_the_exact_rung_in_the_dark():
    for seed in (1, 2, 3):
        link = sim.build_loop(P, sim.TYPICAL, seed=seed,
                              **dict(sim.SCENARIOS["realistic"], **dict(
                                  sim.LOOP_V2, contrast=sim.SWING_MIRROR,
                                  **sim.LOOP_SENSOR_V2)))
        link.run(300)
        leader = link.leader.transfer
        assert leader.result == P.OK, (seed, leader.result)
        assert leader.scheme == P.SCHEME_BACKLIGHT
        assert leader.clock.index == P.EXACT_INDEX, \
            P.SLOT_LADDER[leader.clock.index]


def test_every_field_a_transfer_shows_is_a_backlight_level():
    # The simulator models a backlight and nothing else: every field of
    # every phase is a duty from the protocol's tables.
    link = sim.build_loop(P, sim.TYPICAL, seed=1,
                          **dict(sim.SCENARIOS["realistic"], **sim.LOOP_V2))
    duties = set()
    phases = set()
    for badge in (link.leader, link.follower):
        def schedule(when, duty, _badge=badge, _real=badge.panel.schedule):
            duties.add(duty)
            phases.add(_badge.transfer.phase)
            _real(when, duty)
        badge.panel.schedule = schedule
    link.run(300)
    assert link.leader.transfer.result == P.OK
    levels = {sim.backlight_duty_v2(v) for table in P.LEVEL_SCHEMES.values()
              for v in table.values()}
    assert duties <= levels and len(duties) >= 3, duties
    assert P.PHASE_DATA in phases and P.PHASE_ANNOUNCE in phases


def test_a_collection_the_protocol_asks_for_stalls_the_badge():
    link = sim.build_loop(P, sim.TYPICAL, seed=1)
    link.run(300)
    assert link.leader.transfer.result == P.OK
    # Each at its staircase's closing gap.
    assert link.leader.collections >= 1 and link.follower.collections >= 1


def test_the_measured_sensor_reads_in_whole_steps_around_its_swing():
    rng = random.Random(3)
    sensor = sim.LoopSensor(40, 0, 80, rng, sd=6.0, quantum=16)
    black = [sensor.read() for _ in range(400)]
    sensor.settle(1.0, 1.0)
    white = [sensor.read() for _ in range(400)]
    assert all(v % 16 == 0 for v in black + white)
    assert sorted(black)[200] in (32, 48) and sorted(white)[200] in (112, 128)


def test_the_peer_swings_are_the_predicted_ones():
    # The geometry (optical-coupling.md) predicted a peer's swing from the
    # solid angle its screen fills; data.md records the result, which the
    # simulator's aligned and overlap rows use.
    import os
    from harness import REPO
    data = open(os.path.join(REPO, ".agents", "optical_data_transfer",
                             "data.md")).read()
    assert "~%d aligned edge to edge, ~%d overlapping" % (
        sim.SWING_PEER_ALIGNED, sim.SWING_PEER_OVERLAP) in data
    assert "mirror A %d counts dark" % sim.SWING_MIRROR in data
