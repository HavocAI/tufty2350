"""The Leader hears the Complete Ack without its first symbol.

The Ack follows the Leader's Complete by one silent slot, too few to
phase-lock on, and a sensor slower than measured rises out of that dark too
late for the first middle's window. In simulation (ring/v2/k3.0: a sensor
three times slower, dark, 16 ms) every run that had not demoted read
`_- __-` four times over and ended ERR_LOST_PEER with the Follower holding
the payload -- the same four of 144 at HEAD, and 16 once the fast
handshake left more runs on 16 ms. Matching COMPLETE_ACK_LATE_TAIL instead,
both deliver 432 of 432 over 24 seeds.
"""

import itertools

from harness import load_module
from held import HeldTransfer

P = load_module("share_wifi", "protocol")


def finishing_leader():
    leader = HeldTransfer(True, [b"x"])
    leader.sub = leader.STEP_FINISH
    leader.rx = []
    leader.wait_slots = leader.finish_slots = 0
    return leader


def feed(leader, symbols):
    for symbol in symbols:
        if leader.result is not None:
            break
        leader.rx.append(symbol)
        leader.leader_rx()
    return leader.result


def test_the_heard_ack_is_the_tail_less_its_first_symbol():
    assert P.COMPLETE_ACK_LATE_TAIL == P.COMPLETE_ACK_TAIL[1:] == "_- __-"


def test_an_ack_whose_first_middle_read_black_is_heard():
    assert feed(finishing_leader(), list("  " + " _- __- ")) == P.OK


def test_the_whole_ack_is_still_heard():
    assert feed(finishing_leader(), list("  " + P.COMPLETE_ACK)) == P.OK


def test_crc_bits_can_never_be_taken_for_it():
    """CRC replies carry no black, so no run of them holds the heard Ack:
    every string of 8 data symbols."""
    for bits in itertools.product((P.MID, P.HIGH), repeat=8):
        assert P.COMPLETE_ACK_LATE_TAIL not in "".join(bits)


def test_nothing_else_a_follower_sends_holds_it():
    others = (P.SYNC_ERROR, P.HIGH * 8 + P.LOW * 4 + P.SYNC_ERROR)
    for message in others:
        assert P.COMPLETE_ACK_LATE_TAIL not in P.LOW * 3 + message + P.LOW * 3
