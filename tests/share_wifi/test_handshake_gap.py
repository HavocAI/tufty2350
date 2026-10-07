"""A Follower in the handshake drops a coarse sample cut short by a gap.

The regression these guard: at three times the lit room's light, with 47%
flicker, 30 of 144 transfers failed. A present stops a continuous badge's
readings for 8-25 ms, and the coarse sample it interrupts then closes on a few
ms of readings that leave most of 120 Hz flicker in. One such sample lifted
BeaconDetector's (min + max) / 2 midpoint over the Leader's rising edge, the
Follower took the missed crossing for quiet, and it started reading a
staircase the Leader had not begun to send. See HANDSHAKE_GAP_MS.
"""

import time

import simulator as sim
from harness import load_module

P = load_module("share_wifi", "protocol")

SPAN = 2000     # us of readings in a fine sample
EVERY = 3000    # us from one fine sample's start to the next: a 1 ms call


def follower():
    return P.Transfer(False, frame_ms=lambda: 16, continuous=True,
                      schemes=(P.SCHEME_FIELDS, P.SCHEME_BACKLIGHT))


def feed(transfer, start_us, value=200):
    """One fine sample whose readings began at `start_us`. Returns whether
    it closed a coarse sample, by whether the coarse stream was emptied."""
    t_us = time.ticks_add(start_us, SPAN // 2)
    t = t_us // 1000
    transfer.sample(t, t, value, t_us, SPAN)
    return transfer.coarse_n == 0


def test_a_gap_drops_the_coarse_sample_in_progress():
    transfer = follower()
    start = 1000000
    feed(transfer, start)
    feed(transfer, start + EVERY)
    assert transfer.coarse_n == 2
    # A present: 15 ms with no readings.
    late = start + EVERY + SPAN + 15000
    feed(transfer, late)
    assert transfer.coarse_n == 1, "the two before the gap should be gone"
    assert transfer.coarse_base == time.ticks_add(late, SPAN // 2) // 1000


def test_a_protocol_call_is_not_a_gap():
    transfer = follower()
    start = 1000000
    closed = 0
    for k in range(30):
        # Up to 3 ms dead between samples: well over a call's ~1 ms.
        closed += feed(transfer, start + k * (SPAN + 3000))
    assert not transfer.coarse_dropped
    assert closed >= 10, closed


def test_only_the_follower_handshake_drops():
    # Calibration and the Leader keep the samples either side of a gap:
    # only the Follower's quiet test was shown to need it.
    leader = P.Transfer(True, (b"a", b"b"), frame_ms=lambda: 16,
                        continuous=True)
    receiving = follower()
    receiving.enter(P.PHASE_CAL_RECV, 1000)
    for transfer in (leader, receiving):
        start = 1000000
        feed(transfer, start)
        feed(transfer, start + EVERY)
        # The third closes the coarse sample, holding all three.
        assert feed(transfer, start + EVERY + SPAN + 15000)
        assert not transfer.coarse_dropped


def test_samples_each_after_a_long_gap_still_close_coarse_samples():
    # A badge whose every call outlasted HANDSHAKE_GAP_MS: dropping every
    # time would never close a coarse sample, step_present would never run,
    # and not even ANNOUNCE_TIMEOUT_MS would end the transfer.
    transfer = follower()
    start = 1000000
    gap = SPAN + (P.HANDSHAKE_GAP_MS + 5) * 1000
    pushed = 0
    for k in range(40):
        before = transfer.detector.count
        feed(transfer, start + k * gap)
        pushed += transfer.detector.count > before
    assert pushed >= 19, pushed


def test_the_gap_is_measured_across_the_tick_wrap():
    # ticks_us wraps at 2**30 on the badge; the harness's ticks do not.
    period = 1 << 30
    saved = time.ticks_add, time.ticks_diff
    time.ticks_add = lambda a, b: (a + b) % period
    time.ticks_diff = lambda a, b: ((a - b + period // 2) % period) - period // 2
    try:
        transfer = follower()
        # The last sample before the gap ends just short of the wrap, and the
        # next starts 15 ms later, past it: raw subtraction reads the gap as
        # about -2**30 us and drops nothing.
        start = period - SPAN - 1000 - EVERY
        feed(transfer, start)
        feed(transfer, start + EVERY)
        assert not transfer.coarse_dropped
        feed(transfer, (start + EVERY + SPAN + 15000) % period)
        assert transfer.coarse_dropped
    finally:
        time.ticks_add, time.ticks_diff = saved


# --- end to end ----------------------------------------------------------------

def bright(scenario, seed, call_ms=sim.LOOP_CALL_MS):
    kw = dict(sim.SCENARIOS[scenario])
    kw.update(sim.LOOP_V2)
    kw.update(sim.LOOP_SENSOR_V2)
    kw.update(contrast=sim.SWING_MIRROR, room_light=150, flicker=0.47)
    return sim.build_loop(P, sim.TYPICAL, seed=seed, call_ms=call_ms,
                          schemes=(P.SCHEME_FIELDS, P.SCHEME_BACKLIGHT), **kw)


def test_the_bright_flickering_runs_that_failed_now_deliver():
    # Each failed before: the Follower's staircase refused (ERR_CONTRAST on
    # both), or its beacon cut short (ERR_NO_PEER on the Leader).
    for scenario, seed in (("realistic", 5), ("datasheet", 6),
                           ("slow panel", 7)):
        link = bright(scenario, seed)
        leader, follower_ = link.leader.transfer, link.follower.transfer
        entered = {}
        limit = 0.0
        while not (leader.result and follower_.result) and limit < 300:
            limit += 0.05
            link.run(limit)
            if "cal_send" not in entered and leader.phase >= P.PHASE_CAL_SEND:
                entered["cal_send"] = link.now
            if "cal_recv" not in entered and \
                    follower_.phase >= P.PHASE_CAL_RECV:
                entered["cal_recv"] = link.now
        assert leader.result == P.OK, (scenario, seed, leader.result)
        assert follower_.result == P.OK, (scenario, seed, follower_.result)
        # The Follower starts reading only once the Leader has gone quiet.
        assert entered["cal_recv"] > entered["cal_send"], (scenario, seed)


def test_a_badge_whose_calls_outlast_the_gap_still_finishes():
    # 5 ms calls, 3.5-7.5 with the simulator's jitter, some longer than
    # HANDSHAKE_GAP_MS; 10 ms calls, 7-15, nearly all. Without the
    # never-twice rule the 10 ms badges never closed a coarse sample in the
    # handshake and gave up with ERR_NO_PEER.
    kw = dict(sim.SCENARIOS["realistic"])
    kw.update(sim.LOOP_V2)
    kw.update(sim.LOOP_SENSOR_V2)
    kw.update(contrast=sim.SWING_MIRROR)
    for call_ms in (5.0, 10.0):
        link = sim.build_loop(P, sim.TYPICAL, seed=1, call_ms=call_ms,
                              schemes=(P.SCHEME_FIELDS, P.SCHEME_BACKLIGHT),
                              **kw)
        seconds = link.run(300)
        assert seconds < 300, call_ms
        assert link.leader.transfer.result == P.OK, call_ms
        assert link.follower.transfer.result == P.OK, call_ms
