"""Getting a block through after something went wrong with it: the Leader
has no bytes to put in it, the peer demotes while a NAK is already counted,
the ring fails, the Follower ends a rung off the Leader. Each used to leave
the two badges disagreeing about something -- a rung, an exit value, a retry
count -- so these run both sides and check that they agree. A missed Begin
is test_fast_transfer.py's.
"""

import random

import simulator
import test_integration as T

from harness import load_module
from held import HeldTransfer
import held

protocol = load_module("share_wifi", "protocol")
Transfer = protocol.Transfer


# --- an empty payload --------------------------------------------------------
# No bytes at all is still a stream -- its field count, 0 -- in one block.


def test_a_leader_with_no_bytes_sends_one_block():
    for payload, stream in (([b""], b"\x01\x00"), ([], b"\x00"),
                            (None, b"\x00"), ((), b"\x00")):
        leader = HeldTransfer(True, payload)
        leader.levels = T.levels()
        leader.now = 0
        leader.start_data(0)
        assert leader.fast_data == stream, (payload, leader.fast_data)
        assert "".join(leader.tx).strip(protocol.LOW).startswith(
            protocol.FAST_BEGIN.strip(protocol.LOW)), payload


def test_an_empty_payload_is_delivered_end_to_end():
    for name in ("ideal", "realistic"):
        link = simulator.build(protocol, [b""], seed=1, **simulator.SCENARIOS[name])
        link.run(120)
        leader, follower = link.leader.transfer, link.follower.transfer
        assert leader.result == protocol.OK, "%s: leader %s" % (name, leader.result)
        assert follower.result == protocol.OK, "%s: follower %s" % (name, follower.result)
        assert follower.received == [b""], name
        assert follower.blocks_accepted == 1, name
        assert link.violation is None, "%s:\n%s" % (name, link.violation)


# --- the peer's demotion and the NAK count ------------------------------------


def test_a_demotion_the_follower_asks_for_restores_the_retry_budget():
    # A block NAKed once arrives at the new slot rate with its whole NAK
    # budget, whichever side asked.
    leader, _ = T.paired(4, simulator.TYPICAL)
    leader.start_data(0)
    leader.tx = []
    leader.fast_pending = False
    leader.sub = Transfer.STEP_FAST_REPLY
    leader.retries = protocol.FAST_TRIES - 1
    before = leader.clock.index
    for symbol in protocol.SYNC_ERROR:
        leader.rx.append(symbol)
        leader.leader_rx()
        if leader.tx:
            break
    assert leader.clock.index == before - 1, "it should have stepped down"
    assert leader.retries == 0, \
        "a demotion must not leave NAKs counted, whichever side asked"


# --- a ring that fails part way --------------------------------------------
# The ring is the only reader, so a fault in it ends the transfer with its own
# exit value, on whichever badge has it: the Leader checks once a block as it
# sends, the Follower once a block as it waits. Lost-peer would blame the
# other badge.

def run_with_ring_fault(which):
    link = simulator.build(protocol, simulator.TYPICAL, seed=1,
                           **simulator.SCENARIOS["ideal"])
    side = getattr(link, which)
    readers = __import__("reader")
    side.reader.ring.fault = readers.HEALTH_ADC
    link.run(120)
    return link.leader.transfer.result, link.follower.transfer.result


def test_a_ring_fault_on_the_leader_ends_err_ring():
    leader, _ = run_with_ring_fault("leader")
    assert leader == protocol.ERR_RING, leader


def test_a_ring_fault_on_the_follower_ends_err_ring():
    _, follower = run_with_ring_fault("follower")
    assert follower == protocol.ERR_RING, follower


def _follower_hearing(pattern, leader_ms, own_ms):
    """A Follower on `own_ms` whose ring holds the Leader sending `pattern`
    on `leader_ms` slots, locked on its first edge, as after its own
    unheard Data Sync Error."""
    t = HeldTransfer(False)
    t.start_data(0, protocol.SLOT_LADDER.index(own_ms))
    t.levels = protocol.Levels()
    t.levels.fit(32, 70, 112)
    level = {protocol.LOW: 32, protocol.MID: 70, protocol.HIGH: 112}
    start = 50000
    end = start + protocol.RATE_CHECK_SLOTS * own_ms * 1000 + 20000
    for us in range(0, end, 100):
        k = (us - start) // (leader_ms * 1000)
        field = pattern[k % len(pattern)] if us >= start else protocol.LOW
        t.reader.add(us, level[field])
    t.verify_rate = True
    t.rebase(start - 950, start)
    t.check_leader_rate(end)
    return t


def test_a_follower_a_rung_off_the_leader_moves_to_its_rung():
    """A Data Sync Error the Leader never heard left the Follower a rung
    slower; every exchange after demoted both, and they walked the ladder
    apart to the 300 s block deadline [simulation, half50]. The Follower
    measures the Leader's next message and moves."""
    for pattern in (protocol.FAST_BEGIN, protocol.CRC_GOOD, protocol.CRC_BAD):
        t = _follower_hearing(pattern, 10, 13)
        assert protocol.SLOT_LADDER[t.clock.index] == 10, pattern
        assert t.rate_heals == 1


def test_the_rate_check_leaves_complete_and_a_matching_rung_alone():
    t = _follower_hearing(protocol.COMPLETE, 10, 13)
    assert protocol.SLOT_LADDER[t.clock.index] == 13 and not t.rate_heals
    t = _follower_hearing(protocol.FAST_BEGIN, 13, 13)
    assert protocol.SLOT_LADDER[t.clock.index] == 13 and not t.rate_heals


def test_only_a_follower_that_demoted_on_its_own_checks():
    t = HeldTransfer(False)
    t.start_data(0, protocol.EXACT_LIT_INDEX)
    t.rebase(1000, 2000)
    assert t.rate_from is None
    t.reader.check = lambda: True
    t.send_sync_error()
    t.rebase(1000, 2000)
    assert t.rate_from == 2000
