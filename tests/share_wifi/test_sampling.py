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


# --- end to end -------------------------------------------------------------

def test_the_run_that_found_it_no_longer_rejects_its_first_block():
    link, rejected, seconds = run_counting_crc_bad(150)
    assert rejected == 0, "CRC Bad %d times" % rejected
    assert link.leader.transfer.result == P.OK
    assert link.follower.transfer.received == list(simulator.TYPICAL)
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


