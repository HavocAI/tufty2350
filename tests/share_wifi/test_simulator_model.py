"""The simulator's model of the badge, where it was calibrated against one.

simulator.py is the evidence most of the protocol's figures rest on, so what
it assumes about the hardware is pinned here -- and that it runs the
production readers.
"""

import random

import simulator as sim
from harness import load_module

P = load_module("share_wifi", "protocol")
R = load_module("share_wifi", "reader")


def test_the_v2_and_v3_profiles_deliver():
    for timing in (sim.LOOP_V2, sim.LOOP_V2_SLOW_PANEL, sim.LOOP_V3):
        link = sim.build_loop(P, sim.TYPICAL, seed=1,
                              **dict(sim.SCENARIOS["realistic"], **timing))
        link.run(300)
        assert link.leader.transfer.result == P.OK, timing
        assert P.parse_payload(link.follower.transfer.received) == \
            list(sim.TYPICAL)
        assert not link.violation, link.violation


def test_the_simulator_runs_the_production_readers():
    link = sim.build_loop(P, sim.TYPICAL, seed=1)
    assert type(link.leader.reader) is R.RingReader
    assert type(link.leader.reader.ring) is R.ListRing
    # The ring is a power of two, its history the badge's.
    ring = link.leader.reader.ring
    assert ring.samples == sim.RING_ENTRIES == 4096
    assert link.leader.reader.usable == sim.RING_USABLE_US
    polled = sim.build_loop(P, sim.TYPICAL, seed=1, reader="polled")
    assert type(polled.leader.reader) is R.PolledReader


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


def test_nothing_is_presented_in_the_data_phase():
    # Presents only change pixels, and the backlight scheme's are white for
    # every symbol: once both badges are in the data phase, nothing is
    # presented by either.
    link = sim.build_loop(P, sim.TYPICAL, seed=1,
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
    in_data = [kind for phase, kind in changes if phase == P.PHASE_DATA]
    assert "duty" in in_data, "the data phase changed no backlight"
    assert "share" not in in_data, "a pixel change in the data phase"


def test_a_collection_the_protocol_asks_for_stalls_the_badge():
    link = sim.build_loop(P, sim.TYPICAL, seed=1)
    link.run(300)
    assert link.leader.transfer.result == P.OK
    # The Leader on finding the closing gap; the Follower at its own and on
    # accepting the block.
    assert link.leader.collections >= 1 and link.follower.collections >= 2


def test_the_measured_sensor_reads_in_whole_steps_around_its_swing():
    rng = random.Random(3)
    sensor = sim.LoopSensor(40, 0, 80, rng, sd=6.0, quantum=16)
    black = [sensor.read() for _ in range(400)]
    sensor.settle(1.0, 1.0)
    white = [sensor.read() for _ in range(400)]
    assert all(v % 16 == 0 for v in black + white)
    assert sorted(black)[200] in (32, 48) and sorted(white)[200] in (112, 128)


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
