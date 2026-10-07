"""A Follower in the handshake drops a coarse sample cut short by a gap.

The regression these guard: at three times the lit room's light, with 47%
flicker, 30 of 144 transfers failed. A present stops a POLLED badge's
readings for 8-25 ms, and the coarse sample it interrupts then closes on a
few ms of readings that leave most of 120 Hz flicker in. One such sample
lifted BeaconDetector's (min + max) / 2 midpoint over the Leader's rising
edge, the Follower took the missed crossing for quiet, and it started
reading a staircase the Leader had not begun to send. See HANDSHAKE_GAP_MS.

The rule is now in fine samples: a gap is a run of fine samples with no
readings in them -- which an EXACT badge's ring never has, short of an
overrun.
"""

import simulator as sim
from harness import load_module

P = load_module("share_wifi", "protocol")
EMPTY = -1
GAP_SAMPLES = P.HANDSHAKE_GAP_MS // P.FINE_MS + 1      # first run past the gap


def follower():
    t = P.Transfer(False)
    t.phase = P.PHASE_PRESENT
    return t


def feed(transfer, n, value=200, start=[0]):
    for _ in range(n):
        start[0] += P.FINE_US
        transfer.take_fine(start[0], value)


def test_a_gap_drops_the_coarse_sample_in_progress():
    t = follower()
    feed(t, 2)
    assert t.coarse_n == 2
    feed(t, GAP_SAMPLES, EMPTY)                 # a present: no readings
    assert t.coarse_n == 0 and t.coarse_dropped, "the two before the gap"
    feed(t, 1)
    assert t.coarse_n == 1


def test_a_short_gap_is_not_a_gap():
    t = follower()
    feed(t, 2)
    feed(t, GAP_SAMPLES - 1, EMPTY)             # HANDSHAKE_GAP_MS, not over
    assert t.coarse_n == 2 and not t.coarse_dropped


def test_never_twice_running():
    # A badge whose every pass outlasted the gap would otherwise never close
    # a coarse sample, and never get out of the handshake.
    t = follower()
    pushed = 0
    for _ in range(40):
        before = t.detector.count
        feed(t, 1)
        feed(t, GAP_SAMPLES, EMPTY)
        pushed += t.detector.count > before
    assert pushed >= 10, pushed


def test_only_the_follower_handshake_drops():
    leader = P.Transfer(True, (b"a", b"b"))
    receiving = P.Transfer(False)
    receiving.enter(P.PHASE_CAL_RECV, 0)
    for t in (leader, receiving):
        feed(t, 2)
        feed(t, GAP_SAMPLES, EMPTY)
        assert t.coarse_n == 2 and not t.coarse_dropped


def test_the_step_shim_never_drops():
    # One reading a frame is all gaps; it is not a present.
    t = follower()
    t.sparse = True
    feed(t, 1)
    feed(t, GAP_SAMPLES, EMPTY)
    assert not t.coarse_dropped


# --- end to end ----------------------------------------------------------------

def bright(scenario, seed, reader):
    kw = dict(sim.SCENARIOS[scenario])
    kw.update(sim.LOOP_V2)
    kw.update(sim.LOOP_SENSOR_V2)
    kw.update(contrast=sim.SWING_MIRROR, room_light=150, flicker=0.47)
    return sim.build_loop(P, sim.TYPICAL, seed=seed, reader=reader, **kw)


def test_the_bright_flickering_runs_that_failed_now_get_through_the_handshake():
    # Each failed the handshake before. Both tiers now reach the data phase;
    # an EXACT pair delivers every one. A POLLED pair in this room delivers
    # 122 of 144 in the sweep, its failures in the data phase -- rung splits
    # through known defect F-B1 -- not here.
    for reader in ("polled", "ring"):
        for scenario, seed in (("realistic", 5), ("datasheet", 6),
                               ("slow panel", 7)):
            link = bright(scenario, seed, reader)
            link.run(300)
            leader, follower_ = link.leader.transfer, link.follower.transfer
            times = link.phase_times()
            assert P.PHASE_DATA in times, (reader, scenario, seed,
                                           leader.result)
            assert times[P.PHASE_CAL_SEND] < times[P.PHASE_DATA]
            if reader == "ring":
                assert leader.result == follower_.result == P.OK, \
                    (scenario, seed, leader.result, follower_.result)
