"""Which samples decide a slot.

The regression these guard: a first block that failed its CRC on a timing
alone. The Follower's grid is set from one edge per message and can sit several
ms late of the Leader's, and the Leader shows each symbol on the first frame
after its slot begins -- so the last few ms of a slot could already hold the
NEXT symbol. At 50ms with 11ms frames the back half of a slot holds two or
three samples, the median of two is the higher, and one sample of a brighter
next symbol flipped a bit. The ideal scenario, seed 1, 150ms of start skew,
was the case that showed it: one CRC Bad and a resend that cost nine seconds.
"""

import simulator
from harness import load_module

P = load_module("share_wifi", "protocol")


def levels():
    out = P.Levels()
    out.fit(200, 4250, 9200)
    return out


def run_counting_crc_bad(skew, scenario="ideal", seed=1):
    link = simulator.build(P, simulator.TYPICAL, seed=seed,
                           **dict(simulator.SCENARIOS[scenario],
                                  start_skew_ms=skew))
    leader = link.leader.transfer
    rejected = [0]
    send = leader.send

    def counting(symbols):
        if "".join(symbols) == P.CRC_BAD:
            rejected[0] += 1
        return send(symbols)
    leader.send = counting
    seconds = link.run(200)
    link.assert_invariants()
    return link, rejected[0], seconds


# --- the receiver -----------------------------------------------------------

def test_a_sample_of_the_next_symbol_at_the_very_end_of_a_slot_is_ignored():
    # The failing slot, reduced: two settled samples of the middle level, then
    # one taken 3ms before the boundary that has already caught the high
    # symbol after it. Counted, the median of the two settled samples is the
    # higher, and the slot decodes as high.
    clock = P.SlotClock()
    clock.start(0, P.SLOT_LADDER.index(50))
    receiver = P.SlotReceiver(clock, levels(), frame_ms=11)
    for now, value in ((4, 9200), (15, 4250), (26, 4250), (37, 4250), (47, 9200)):
        assert receiver.feed(now, value) is None
    settled = receiver.settled[:receiver.n_settled]
    assert settled == [4250, 4250], settled
    assert receiver.feed(52, 9200) == P.MID


def test_the_guard_never_leaves_a_slot_without_a_frame_to_decide_from():
    # A badge never picks a slot shorter than 2.5 frames, and the window that
    # decides it must still be at least one frame long, or a slot could catch
    # no settled sample and be decided from its front -- the last symbol's.
    for frame in range(8, 21):
        for duration in P.SLOT_LADDER:
            if duration < frame * 2.5:
                continue
            guard = min(frame * P.END_GUARD_PERCENT // 100,
                        duration // 2 - frame * 6 // 5)
            window = duration - max(0, guard) - duration // 2
            assert window >= frame, \
                "%dms slots at %dms frames: a %dms window" % (duration, frame, window)


# --- end to end -------------------------------------------------------------

def test_the_run_that_found_it_no_longer_rejects_its_first_block():
    link, rejected, seconds = run_counting_crc_bad(150)
    assert rejected == 0, "CRC Bad %d times" % rejected
    assert link.leader.transfer.result == P.OK
    assert P.parse_payload(link.follower.transfer.received) == list(simulator.TYPICAL)
    # 23 s on the 80 ms rung; a resent block adds several more.
    assert seconds < 26, "took %.1fs: something was resent" % seconds


def test_no_start_skew_in_the_ideal_scenario_rejects_a_crc():
    # Before the guard, 12 of these 16 resent the first block. The ideal
    # scenario is the sharpest test: an instant panel turns any sample of the
    # next symbol into a full-height reading.
    rejected = dict((skew, run_counting_crc_bad(skew)[1])
                    for skew in range(0, 2400, 150))
    assert not any(rejected.values()), \
        "CRC Bad at these skews: %s" % dict((k, v) for k, v in rejected.items() if v)


def test_the_leader_keeps_its_own_grid_for_the_crc_reply():
    # The Leader does not re-base onto the Follower's CRC: it hears only one
    # silent slot before the reply, short of the 1.5 that phase locking wants.
    # That looks like an omission and is not. Giving the reply a black lead-in
    # so the Leader could lock cost 16-21 deliveries in 384 simulated runs:
    # with both badges re-basing onto each other every message, their
    # estimates' errors stack. The Leader's grid is the reference; the
    # Follower follows it.
    link = simulator.build(P, simulator.TYPICAL, seed=1,
                           **simulator.SCENARIOS["realistic"])
    leader = link.leader.transfer
    rebased = []
    rebase = leader.rebase

    def recording(boundary, now):
        rebased.append(leader.sub)
        return rebase(boundary, now)
    leader.rebase = recording
    link.run(200)
    assert leader.result == P.OK
    assert leader.STEP_CRC not in rebased, \
        "the Leader re-based onto a CRC reply: %r" % rebased
