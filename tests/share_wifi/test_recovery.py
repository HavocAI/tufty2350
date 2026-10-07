"""Getting a block through after something went wrong with it.

Three ways a block can go astray that the happy path never exercises: the
Follower loses framing part way through it, the Leader has no bytes to put in
it, and the peer demotes while a CRC retry is already spent. Each one used to
leave the two badges disagreeing about something -- a rung, an exit value, a
retry count -- so these run both sides and check that they agree.
"""

import random

import simulator
import test_integration as T

from harness import load_module

protocol = load_module("share_wifi", "protocol")
Transfer = protocol.Transfer


# --- a framing failure part way through a block -----------------------------
# The Leader does not listen while it transmits. A Follower that answered a
# framing failure at once sent its Data Sync Error into a block still going
# out, demoted on its own, and then -- stalled, with the Leader waiting on a
# rung it no longer read -- asked again and demoted a second time. The two
# walked down the ladder one rung apart until the block deadline: 50/65, 65/80
# and on, both ERR_LOST_PEER at about 306s.


def on_air(side, now):
    """The symbol `side` is showing from its queue, or None if it is not."""
    if not side.tx or side.tx_slot is None:
        return None
    index = side.clock.number(now) - side.tx_slot
    if 0 <= index < len(side.tx):
        return side.tx[index]
    return None


def run_with_framing_failure(payload, after_bytes, scenario="ideal", seed=1):
    """A real transfer whose Follower misreads one symbol as black mid-byte.

    Injected once, three bits into byte `after_bytes` of the first block, by
    rewriting what the Follower decoded rather than what the Leader sent -- so
    the Leader carries on transmitting exactly as it would on hardware.
    """
    link = simulator.build(protocol, payload, seed=seed,
                           **simulator.SCENARIOS[scenario])
    leader, follower = link.leader.transfer, link.follower.transfer
    collect = follower.collect_block
    state = {"injected": False, "block_done": False, "early_tx": [],
             "both_lit": []}

    def misread():
        if (not state["injected"] and follower.in_block
                and not follower.end_match
                and len(follower.tokens) == after_bytes
                and len(follower.bits) == 3):
            follower.rx[-1] = protocol.LOW
            state["injected"] = True
        return collect()
    follower.collect_block = misread

    check = link.invariants.check

    def check_half_duplex(now):
        check(now)
        if not (leader.phase == follower.phase == protocol.PHASE_DATA):
            return
        now = int(now * 1000)
        # The literal rule, over the window the defect lived in: from the
        # failure until the Leader has sent the whole block, the Follower
        # queues nothing at all.
        if state["injected"] and not state["block_done"]:
            if leader.sub == leader.STEP_BLOCK and leader.tx:
                if follower.tx:
                    state["early_tx"].append(now)
            else:
                state["block_done"] = True
        # And everywhere: never both lit at once. Not "never both queued" --
        # the Leader queues a resend, lead-in first, while the Follower is
        # still finishing its Data Sync Error, which is what SYNC_LEAD_IN is
        # for; and a pattern's trailing black is often still showing when the
        # peer, having matched without it, starts its answer.
        mine, theirs = on_air(leader, now), on_air(follower, now)
        if (mine not in (None, protocol.LOW)
                and theirs not in (None, protocol.LOW)):
            state["both_lit"].append(now)
    link.invariants.check = check_half_duplex

    elapsed = link.run(protocol.BLOCK_TIMEOUT_MS // 1000 + 100)
    return link, elapsed, state


def assert_recovers(payload, after_bytes, scenario="ideal", seed=1):
    link, elapsed, state = run_with_framing_failure(payload, after_bytes,
                                                    scenario, seed)
    leader, follower = link.leader.transfer, link.follower.transfer
    where = "%s, seed %d, failure after %d bytes" % (scenario, seed, after_bytes)
    assert state["injected"], "%s: the failure was never injected" % where
    assert not state["early_tx"], \
        "%s: the Follower transmitted while the Leader was still sending the " \
        "block, from %dms" % (where, state["early_tx"][0])
    assert not state["both_lit"], \
        "%s: both badges lit at once from %dms" % (where, state["both_lit"][0])
    assert leader.clock.index == follower.clock.index, \
        "%s: leader on %dms, follower on %dms" % (
            where, leader.clock.duration, follower.clock.duration)
    assert leader.result == protocol.OK, "%s: leader %s" % (where, leader.result)
    assert follower.result == protocol.OK, \
        "%s: follower %s" % (where, follower.result)
    assert protocol.parse_payload(follower.received) == list(payload), where
    # Well inside, not merely inside: the broken version reached the deadline.
    assert elapsed * 1000 < protocol.BLOCK_TIMEOUT_MS // 2, \
        "%s: took %.0fs" % (where, elapsed)
    assert link.violation is None, "%s:\n%s" % (where, link.violation)
    return leader


def test_a_framing_failure_early_in_a_block_recovers_on_one_rung():
    # The ideal scenario's ambient is a lit room's: the pair starts on the
    # EXACT lit rung.
    leader = assert_recovers(simulator.TYPICAL, after_bytes=3)
    assert leader.clock.index == protocol.EXACT_LIT_INDEX - 1, \
        "one failure should cost exactly one rung, not %d" % (
            protocol.EXACT_LIT_INDEX - leader.clock.index)


def test_a_framing_failure_late_in_a_block_recovers_on_one_rung():
    # Late in a full block: the Follower has the least time left to wait, and
    # the stall budget has run out more than once while it waited.
    assert_recovers(simulator.WORST, after_bytes=60)
    assert_recovers(simulator.TYPICAL, after_bytes=15)


def test_a_framing_failure_early_in_a_full_block_recovers():
    # The longest wait for the Leader to finish: sixty-odd bytes still to go.
    assert_recovers(simulator.WORST, after_bytes=3)


def test_a_framing_failure_recovers_on_a_noisy_link_too():
    for seed in (1, 2, 3, 4):
        for after in (3, 15):
            assert_recovers(simulator.TYPICAL, after, "realistic", seed)


def test_a_framing_failure_breaks_in_and_then_asks_once():
    # Reading by reading on one badge: the Follower that loses framing holds
    # high -- breaking in -- for as long as the Leader is lit, sends nothing,
    # and once the Leader has been dark BREAK_ACK_SLOTS sends the one Data
    # Sync Error, then demotes one rung.
    follower = T.Transfer(False)
    follower.levels = T.levels()
    follower.now = 0
    follower.start_data(0, 5)
    rng = random.Random(1)
    slot = follower.clock.duration // 1000
    now = 0
    shown = []

    def hold(symbol, slots):
        nonlocal now
        for _ in range(slots * slot // T.FRAME_MS):
            shown.append(follower.step(now, T.light_from(symbol, rng)))
            now += T.FRAME_MS

    hold(" ", 6)
    for symbol in protocol.BEGIN_BLOCK + "-_- ":         # black after 3 bits
        hold(symbol, 1)
    hold("-", 1)            # a slot is decided once its window has closed
    assert follower.breaking and not follower.in_block
    del shown[:]
    hold("-", 1)
    for _ in range(10):                                  # the Leader goes on
        hold(rng.choice("-_"), 1)
    assert set(shown) == {protocol.HIGH}, "it should hold high, breaking in"
    assert not follower.tx and not follower.pending_slow_down
    hold(" ", protocol.BREAK_ACK_SLOTS + 2)
    assert "".join(follower.tx) == protocol.SYNC_ERROR, \
        "it should ask with the Data Sync Error Pattern once the Leader stops"
    hold(" ", len(protocol.SYNC_ERROR) + 2)
    assert follower.clock.index == 4, "one failure, one rung"


# --- an empty payload --------------------------------------------------------
# No bytes at all used to give a Leader no block, and its first send_block()
# raised IndexError. It now sends one empty block, which the Follower accepts
# as a single empty field.


def test_a_leader_with_no_bytes_sends_one_empty_block():
    for payload in ([b""], [], None, ()):
        leader = Transfer(True, payload)
        assert leader.blocks == [[]], "%r: %r" % (payload, leader.blocks)
        leader.levels = T.levels()
        leader.now = 0
        leader.start_data(0)                    # used to raise IndexError
        sent = "".join(leader.tx).strip(protocol.LOW)
        assert sent == (protocol.BEGIN_BLOCK + protocol.END_BLOCK).strip(protocol.LOW), \
            "%r: should be Begin Block straight into End Block, got %r" % (payload, sent)
        assert leader.expected_crc == protocol.crc16(b"")


def test_an_empty_payload_is_delivered_end_to_end():
    for name in ("ideal", "realistic"):
        link = simulator.build(protocol, [b""], seed=1, **simulator.SCENARIOS[name])
        link.run(120)
        leader, follower = link.leader.transfer, link.follower.transfer
        assert leader.result == protocol.OK, "%s: leader %s" % (name, leader.result)
        assert follower.result == protocol.OK, "%s: follower %s" % (name, follower.result)
        assert protocol.parse_payload(follower.received) == [b""], name
        assert follower.blocks_accepted == 1, name
        assert link.violation is None, "%s:\n%s" % (name, link.violation)


def test_an_accepted_empty_block_is_not_nothing_received():
    # Both hold no tokens. Only one of them holds a block.
    nothing = T.Transfer(False)
    nothing.levels = T.levels()
    nothing.now = 0
    nothing.start_data(0)
    nothing.accept_pending()                    # a stray acknowledgement
    nothing.finish_follower()
    assert nothing.result == protocol.ERR_DECODE, nothing.result

    empty = T.Transfer(False)
    empty.levels = T.levels()
    empty.now = 0
    empty.start_data(0)
    empty.tx = []
    for symbol in protocol.BEGIN_BLOCK + protocol.END_BLOCK:
        empty.rx.append(symbol)
        empty.collect_block()
    assert empty.sub == empty.STEP_VERDICT, "an empty block should frame"
    assert empty.pending_block and empty.pending_tokens == []
    assert not empty.request_resend(), \
        "an empty block awaiting its verdict is still uncommitted"
    empty.accept_pending()
    empty.accept_pending()                      # and again, by Complete
    assert empty.blocks_accepted == 1, "accepted twice"
    empty.finish_follower()
    assert empty.result == protocol.OK, empty.result


def test_a_rejected_empty_block_is_not_accepted():
    follower = T.Transfer(False)
    follower.levels = T.levels()
    follower.now = 0
    follower.start_data(0)
    follower.tx = []
    for symbol in protocol.BEGIN_BLOCK + protocol.END_BLOCK:
        follower.rx.append(symbol)
        follower.collect_block()
    for symbol in protocol.CRC_BAD:
        follower.rx.append(symbol)
        follower.follower_rx()
    assert not follower.pending_block
    follower.accept_pending()
    follower.finish_follower()
    assert follower.result == protocol.ERR_DECODE, follower.result


# --- the peer's demotion and the CRC retry budget ----------------------------


def test_a_demotion_the_follower_asks_for_restores_the_retry_budget():
    # The Leader's own demotion resets `retries`; the one the Follower triggers
    # with a Data Sync Error must too, or a block that has already been
    # rejected once arrives at the new rate with less budget than it should.
    leader, _ = T.paired(7, simulator.TYPICAL)
    leader.start_data(0)
    leader.tx = []
    leader.sub = Transfer.STEP_CRC
    leader.crc_started = False
    leader.retries = protocol.MAX_BLOCK_RETRIES
    before = leader.clock.index
    for symbol in protocol.SYNC_ERROR:
        leader.rx.append(symbol)
        leader.leader_rx()
        if leader.tx:
            break
    assert leader.clock.index == before - 1, "it should have stepped down"
    assert leader.retries == 0, \
        "a demotion must not leave CRC retries spent, whichever side asked"
