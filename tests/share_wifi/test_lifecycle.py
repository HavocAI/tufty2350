"""The whole protocol, two badges, first beacon to last pattern.

Everything else in this directory starts part way in: the integration tests hand
both badges the same epoch and the same thresholds and begin at the data phase.
Those are exactly the two things the real handshake has to derive, so they were
never actually exercised. These tests run discovery and calibration for real.

    python3 tests/share_wifi/simulator.py

prints how each scenario ends.
"""

import simulator

from harness import load_module

protocol = load_module("share_wifi", "protocol")
PAYLOAD = [b"HomeNet", b"hunter2!!"]


def run(**kw):
    link = simulator.build(protocol, PAYLOAD, **kw)
    link.run(kw.pop("seconds", 60))
    return link


def phases_reached(link):
    return {phase for _, pair in link.log for phase in pair}


def test_discovery_and_calibration_complete():
    # Announce -> peer present -> both staircases -> shared epoch -> data.
    link = run(settle_ms=8, noise=80, start_skew_ms=1500)
    reached = phases_reached(link)
    for phase, name in ((protocol.PHASE_ANNOUNCE, "announce"),
                        (protocol.PHASE_PRESENT, "peer present"),
                        (protocol.PHASE_CAL_SEND, "calibration send"),
                        (protocol.PHASE_CAL_RECV, "calibration receive"),
                        (protocol.PHASE_DATA, "data")):
        assert phase in reached, "never reached %s" % name


def test_both_badges_derive_usable_thresholds():
    link = run(settle_ms=8, noise=80, start_skew_ms=1500)
    for side in (link.leader.transfer, link.follower.transfer):
        levels = side.levels
        assert levels is not None, "calibration produced no levels"
        assert levels.white - levels.black >= protocol.MIN_SWING
        assert levels.black < levels.grey < levels.white


def test_the_follower_measures_the_staircase_not_the_silence_after_it():
    # The staircase alternates every PLATEAU_MS, which to the pre-calibration
    # detector looks like another beacon. Waiting for the beacon to "disappear"
    # waited until the staircase had finished, and the Follower then calibrated
    # against 512 samples of ambient.
    link = run(settle_ms=8, noise=80, start_skew_ms=0)
    reader = link.follower.transfer.reader
    span = max(reader.values) - min(reader.values)
    assert span >= protocol.MIN_SWING, \
        "follower saw a span of %d: it measured silence, not the staircase" % span


def test_the_reader_does_not_go_blind_when_its_buffer_fills():
    # It used to stop appending at LIMIT, so a reader that started early filled
    # with ambient and never saw anything again.
    reader = protocol.StaircaseReader()
    for i in range(protocol.StaircaseReader.LIMIT + 200):
        reader.feed(i * 10, 200)
    assert len(reader.values) <= protocol.StaircaseReader.LIMIT
    for i in range(50):
        reader.feed(10000 + i * 10, 9200)
    assert max(reader.values) >= 9200, "new samples are being ignored"


def slot_phase_error(transfer_a, transfer_b):
    """How far the two slot GRIDS are out of step, in ms.

    Not `abs(epoch_a - epoch_b)`. Once receivers phase-lock, the two epochs walk
    apart by whole slots -- each badge re-bases onto the other's real edges, so
    the absolute values drift together while the grids stay aligned. What
    matters, and what invariant I2 is about, is the offset WITHIN a slot.
    """
    duration = transfer_a.clock.duration
    skew = (transfer_a.clock.epoch - transfer_b.clock.epoch) % duration
    return min(skew, duration - skew)


def test_epochs_are_close_enough_to_decode():
    """The defect the simulator was built to find, now fixed.

    The Leader derives the shared epoch by DETECTING the end of the Follower's
    staircase; the Follower knows it exactly, having transmitted it. Detection
    lags by the panel's settling time plus a frame, which measured ~18ms -- half
    a slot at the rung in use then. A receiver sampling the back half of its own
    slot landed on the transmitter's transition and nothing decoded.

    The fix is not a better estimate of that instant but refusing to depend on
    it: a receiver phase-locks onto the peer's first edge out of black at the
    start of every message. See "Phase locking" in the protocol spec.

    Asserted across seeds because a single seed proved almost nothing here --
    seed 1 passed while half the others failed, at more than one point during
    this work.
    """
    for seed in range(1, 9):
        link = run(settle_ms=8, noise=80, start_skew_ms=0, seed=seed)
        leader, follower = link.leader.transfer, link.follower.transfer
        assert leader.levels is not None and follower.levels is not None, \
            "seed %d: calibration did not complete" % seed
        error = slot_phase_error(leader, follower)
        slots = error / float(leader.clock.duration)
        assert slots < 0.25, \
            "seed %d: grids %dms apart, %.2f slots -- over the quarter-slot budget" % (
                seed, error, slots)


# --- the invariants, asserted continuously ----------------------------------
# tests/share_wifi/simulator.py checks I1, I2, I4 and I7 on every simulated tick
# and records the FIRST violation with the time and both badges' state. These
# tests are what make that checking part of the suite rather than something you
# only see if you run the simulator by hand.
#
# Checking at the end would not have found any of what it found: every bug here
# was a divergence that surfaced as a timeout seconds later, somewhere else.


def test_no_invariant_is_violated_during_a_transfer():
    for name, kw in simulator.SCENARIOS.items():
        for seed in (1, 2, 3):
            link = simulator.build(protocol, PAYLOAD, seed=seed, **kw)
            link.run(200)
            assert link.violation is None, "%s, seed %d:\n%s" % (
                name, seed, link.violation)


def test_no_invariant_is_violated_carrying_a_multi_block_payload():
    # The worst-case credential: a 32-byte field and a 63-byte one, which cross
    # a block boundary. Multi-block is where two previous attempts broke.
    worst = [b"A" * 32, b"k" * 63]
    for name in ("datasheet", "realistic", "slow panel"):
        link = simulator.build(protocol, worst, seed=1, **simulator.SCENARIOS[name])
        link.run(400)
        assert link.violation is None, "%s:\n%s" % (name, link.violation)
        assert protocol.parse_payload(link.follower.transfer.received) == worst, \
            "%s: payload did not arrive intact" % name


def test_the_checker_actually_fails_when_an_invariant_breaks():
    # A checker that cannot fail is worse than none, because the suite then
    # reports its silence as evidence. Break I2 by hand and watch it fire.
    link = simulator.build(protocol, PAYLOAD, settle_ms=8, noise=80)
    link.run(60)
    leader, follower = link.leader.transfer, link.follower.transfer
    assert leader.phase == protocol.PHASE_DATA or leader.result, "never reached data"

    checker = simulator.Invariants(protocol, leader, follower, PAYLOAD)
    leader.phase = follower.phase = protocol.PHASE_DATA
    leader.clock.set_index(follower.clock.index)
    # Half a slot apart: the exact defect, and fatal because the receiver samples
    # the back half and lands on the transmitter's transition.
    follower.clock.epoch = leader.clock.epoch + leader.clock.duration // 2

    window = protocol.LOST_PEER_SLOTS * leader.clock.duration
    try:
        checker.check(0)
        checker.check(window + 1)
    except simulator.InvariantError as broken:
        assert "I2" in str(broken), broken
        assert "leader[" in str(broken), "the message must carry both states"
        return
    raise AssertionError("I2 went unreported with the grids half a slot apart")


def test_the_checker_catches_a_false_success():
    # I7, the one that matters most: a Leader reporting OK over a Follower that
    # holds nothing means a badge claiming to have shared a credential it did not.
    link = simulator.build(protocol, PAYLOAD, settle_ms=8, noise=80)
    leader, follower = link.leader.transfer, link.follower.transfer
    checker = simulator.Invariants(protocol, leader, follower, PAYLOAD)
    leader.result = protocol.OK
    follower.received = []
    try:
        checker.check(1234)
    except simulator.InvariantError as broken:
        assert "I7" in str(broken), broken
        return
    raise AssertionError("I7 went unreported with the follower holding nothing")
