"""Agreeing a rung: each badge's frame-rate class, carried by its staircase.

Each badge picks the rung it starts on, with no message. It used to take it
from its own frame time alone, and two badges whose frame times differed --
v2 firmware at 60 Hz against v3 at 90, or a frame that slipped to two
refreshes -- started on different rungs and almost never delivered: 35 and
0 of 144 in simulation. Now each sends its class in how many repetitions its
staircase has, reads the other's, and both start on the slower class's rung.
See "Agreeing a rung" in the protocol.
"""

import simulator as sim
from harness import load_module

P = load_module("share_wifi", "protocol")


def staircase_samples(order=P.ASCENDING, reps=P.CAL_REPS, frame_ms=11,
                      drop_highs=0):
    """The values a reader would take from a clean staircase, one a frame.

    `drop_highs` removes that many high steps, as a noisy read could miss
    them, by holding the middle through them instead.
    """
    level = {P.LOW: 100, P.MID: 400, P.HIGH: 900}
    values = []
    dropped = 0
    last = None
    t = 0
    while t < P.staircase_ms(reps):
        field = P.staircase_field(t, order, reps)
        if field == P.HIGH and last != P.HIGH:
            dropping = dropped < drop_highs
            if dropping:
                dropped += 1
        if field == P.HIGH and dropping:
            field = P.MID
        values.append(level[field])
        last = P.staircase_field(t, order, reps)
        t += frame_ms
    return values


def levels_for(values):
    reader = P.StaircaseReader()
    reader.values = list(values)
    levels = reader.levels()
    assert levels is not None
    return levels


def test_a_staircase_has_one_high_step_a_repetition_in_either_order():
    for order in (P.ASCENDING, P.REVERSED):
        for reps in (P.CAL_REPS, P.CAL_REPS_SLOW):
            values = staircase_samples(order, reps)
            rising, falling, highs = P.staircase_steps(values, levels_for(values))
            assert highs == reps, (order, reps, highs)
            assert P.staircase_order(values, levels_for(values)) == order


def test_the_class_reads_back_from_the_staircase():
    for order in (P.ASCENDING, P.REVERSED):
        fast = staircase_samples(order, P.CAL_REPS)
        slow = staircase_samples(order, P.CAL_REPS_SLOW)
        assert not P.staircase_slow(fast, levels_for(fast))
        assert P.staircase_slow(slow, levels_for(slow))


def test_a_slow_staircase_survives_one_missed_step_and_not_two():
    # The margin is deliberate: misreading SLOW as FAST splits the rungs,
    # misreading FAST as SLOW only costs speed.
    one = staircase_samples(P.ASCENDING, P.CAL_REPS_SLOW, drop_highs=1)
    assert P.staircase_slow(one, levels_for(one))
    two = staircase_samples(P.ASCENDING, P.CAL_REPS_SLOW, drop_highs=2)
    assert not P.staircase_slow(two, levels_for(two))


def test_the_slow_staircase_is_longer_by_its_extra_repetitions():
    assert P.staircase_ms(P.CAL_REPS) == P.STAIRCASE_MS == 2400
    assert P.staircase_ms(P.CAL_REPS_SLOW) == (
        P.STAIRCASE_MS + (P.CAL_REPS_SLOW - P.CAL_REPS) * 3 * P.PLATEAU_MS)
    assert P.CAL_REPS < P.SLOW_STEPS_MIN <= P.CAL_REPS_SLOW - 1


def transfer_with_frames(frames, live=None):
    """A Transfer whose recent frame times were `frames`, and whose live
    frame time is now `live`."""
    current = [0]
    t = P.Transfer(True, (b"n", b"k"), frame_ms=lambda: current[0])
    for frame in frames:
        current[0] = frame
        t.note_frame()
    if live is not None:
        current[0] = live
    return t


def test_the_class_comes_from_the_median_not_the_last_frame():
    # One slow frame at the moment calibration starts must not make a badge
    # SLOW, and one fast frame must not make it FAST.
    assert not transfer_with_frames([16] * 30 + [50], live=50).frame_class_slow()
    assert transfer_with_frames([33] * 30 + [16], live=16).frame_class_slow()


def test_fast_means_the_fastest_rung_and_slow_the_slow_rung():
    # Three of the panel's measured 24 ms settles: 80 ms.
    assert P.SLOT_LADDER[P.FAST_INDEX] == 80
    assert P.SLOT_LADDER[P.SLOW_INDEX] == P.SLOW_RUNG_MS == 100
    # 60 Hz and 90 Hz frames are FAST; a frame of two 60 Hz refreshes is not.
    # FAST is any frame 2.5 of which fit the 80 ms rung: up to 32 ms.
    for frame, slow in ((11, False), (16, False), (20, False), (32, False),
                        (33, True)):
        assert transfer_with_frames([frame] * 31).frame_class_slow() == slow, \
            frame


def test_both_badges_start_on_the_slower_classs_rung():
    for mine, theirs, rung in ((False, False, 80), (False, True, 100),
                               (True, False, 100), (True, True, 100),
                               (False, None, 80), (True, None, 100)):
        t = P.Transfer(True, (b"n", b"k"))
        t.slow, t.peer_slow = mine, theirs
        assert P.SLOT_LADDER[t.start_index()] == rung, (mine, theirs)


def test_the_class_is_fixed_once_the_staircase_starts():
    current = [11]
    t = P.Transfer(True, (b"n", b"k"), frame_ms=lambda: current[0])
    t.phase = P.PHASE_CAL_SEND
    t.phase_started = 0
    for i in range(40):
        t.step(i * 11, 0)
    assert t.slow is False
    current[0] = 40
    for i in range(40, 80):
        t.step(i * 11, 0)
    assert t.slow is False, "the class moved after its staircase began"


def run_pair(leader_ms, follower_ms, seed=1, scenario="realistic",
             schemes=None):
    link = sim.build(P, sim.TYPICAL, frame_ms=leader_ms, seed=seed,
                     schemes=schemes, **sim.SCENARIOS[scenario])
    link.follower.frame_ms = follower_ms
    link.follower.transfer.frame_ms = lambda: int(follower_ms)
    link.run(300)
    return link


def test_a_fast_and_a_slow_badge_agree_on_the_slow_rung_and_deliver():
    for leader_ms, follower_ms in ((11.1, 33.3), (33.3, 11.1)):
        for seed in (1, 2, 3):
            link = run_pair(leader_ms, follower_ms, seed)
            L, F = link.leader.transfer, link.follower.transfer
            assert (L.slow, F.slow) == (leader_ms > 20, follower_ms > 20)
            assert L.peer_slow == F.slow and F.peer_slow == L.slow
            assert L.result == P.OK and F.result == P.OK, (
                leader_ms, follower_ms, seed, L.result, F.result)
            assert P.parse_payload(F.received) == list(sim.TYPICAL)
            assert not link.violation or "I1" not in link.violation, \
                link.violation


def test_a_slow_leader_offering_the_backlight_is_waited_out():
    # Two staircases, each the slow length: the Follower must wait out the
    # second by the length it read from the first.
    both = (P.SCHEME_FIELDS, P.SCHEME_BACKLIGHT)
    link = run_pair(33.3, 11.1, seed=2, schemes=both)
    L, F = link.leader.transfer, link.follower.transfer
    assert L.result == P.OK and F.result == P.OK, (L.result, F.result)
    assert L.scheme == F.scheme == P.SCHEME_BACKLIGHT
    assert F.peer_slow and not L.peer_slow


def test_two_fast_badges_calibrate_exactly_as_before():
    link = run_pair(11.1, 11.1)
    L, F = link.leader.transfer, link.follower.transfer
    assert (L.slow, L.peer_slow, F.slow, F.peer_slow) == (False,) * 4
    assert P.SLOT_LADDER[L.start_index()] == 80
    assert L.result == P.OK and not link.violation, link.violation
