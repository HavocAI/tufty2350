"""Every state must have a way out, against a peer that never co-operates.

A transfer can fail. What it must not do is wait for ever: the badge shows
"Receiving" and nothing else happens, and the only escape is the reset button.

These drive each side into each state and then feed it a peer that has stopped
playing its part -- stuck on one level, emitting noise, or repeating a pattern
out of context -- and assert a result appears. Driving these combinations is how
the dead ends were found: 18 of 56 never terminated, and every one of them
traced to the same thing.

**LOST_PEER_SLOTS counts silence, and silence is not the only way a link dies.**
A peer stuck showing grey is not silent; every slot decodes to something, so the
counter resets every slot and never fires. That is what STALL_SLOTS is for --
see the comment on it in protocol.py.
"""

import random

from harness import load_module
from held import HeldTransfer
import test_integration as T

protocol = load_module("share_wifi", "protocol")
FB = load_module("share_wifi", "fastblock")
Transfer = protocol.Transfer

FRAME = 16
LIMIT_MS = 600000          # ten simulated minutes: far past every budget

# A peer that has stopped co-operating, in the ways that cost real time. Black
# is the easy case and was never broken; the others are the ones that hung.
STIMULI = {
    "silent": lambda i, rng: " ",
    "stuck grey": lambda i, rng: "_",
    "stuck white": lambda i, rng: "-",
    "random data": lambda i, rng: rng.choice("-_"),
    "random all": lambda i, rng: rng.choice("-_ "),
    "a pattern out of context":
        lambda i, rng: protocol.CRC_BAD[i % len(protocol.CRC_BAD)],
}

STATES = [("leader", Transfer.STEP_BLOCK, False),
          ("leader", Transfer.STEP_FAST_REPLY, False),
          ("leader", Transfer.STEP_FINISH, False),
          ("follower", Transfer.STEP_BLOCK, False),
          ("follower", Transfer.STEP_BLOCK, True),
          ("follower", Transfer.STEP_FAST_RX, False),
          ("follower", Transfer.STEP_FINISH, False)]

NAMES = {Transfer.STEP_BLOCK: "STEP_BLOCK",
         Transfer.STEP_FAST_REPLY: "STEP_FAST_REPLY",
         Transfer.STEP_FAST_RX: "STEP_FAST_RX",
         Transfer.STEP_FINISH: "STEP_FINISH"}


def parked(side_name, sub, in_block):
    """A badge already in the data phase, parked in one sub-state."""
    leader, follower = T.paired(3)               # the 16 ms rung
    side = leader if side_name == "leader" else follower
    side.start_data(0)
    side.sub = sub
    if side_name == "leader":
        side.tx = []                             # stop it mid-conversation
        side.fast_pending = False
        side.fast_burst = None
    else:
        side.in_block = in_block
        if in_block:
            side.fast_bits = []
        if sub == Transfer.STEP_FAST_RX:
            side.deciding = 0
            side.start_fast_rx(FB.RUNGS[0])
            side.deciding = None
    return side


def run_until_result(side, stimulus, seed=4):
    rng = random.Random(seed)
    now = 0
    for i in range(LIMIT_MS // FRAME):
        side.step(now, T.light_from(stimulus(i, rng), rng))
        now += FRAME
        if side.result:
            return now
    return None


def test_no_state_waits_for_ever():
    stuck = []
    for side_name, sub, in_block in STATES:
        for label, stimulus in STIMULI.items():
            side = parked(side_name, sub, in_block)
            if run_until_result(side, stimulus) is None:
                stuck.append("%s in %s%s against %s"
                             % (side_name, NAMES[sub],
                                " (in a block)" if in_block else "", label))
    assert not stuck, \
        "%d states never produced a result:\n  %s" % (len(stuck),
                                                      "\n  ".join(stuck))


def test_a_stuck_screen_is_not_mistaken_for_a_live_link():
    # The specific hole. A Follower waiting for a Begin Block Pattern against a
    # peer stuck on grey decodes a symbol every slot, so the silence budget is
    # reset every slot and never fires. Before STALL_SLOTS this waited for ever.
    side = parked("follower", Transfer.STEP_BLOCK, False)
    finished = run_until_result(side, lambda i, rng: "_")
    assert finished is not None, "a stuck grey screen must not hang the Follower"
    assert side.result == protocol.ERR_LOST_PEER, side.result


def test_the_stall_budget_leaves_room_for_an_honest_wait():
    # It must not fire on a transfer that is merely slow. Measured over 16
    # seeds x 6 scenarios x 2 payloads, a transfer that succeeds stalls for at
    # most 24 slots -- median 21 -- except one cold-panel run at 42. Anything
    # near that would make the budget a new failure mode.
    assert protocol.STALL_SLOTS >= 1.5 * 42, \
        "STALL_SLOTS of %d is too close to the 42 slots an honest transfer " \
        "has been measured to need" % protocol.STALL_SLOTS
    assert protocol.STALL_SLOTS > protocol.LOST_PEER_SLOTS, \
        "a progress budget shorter than the silence budget would pre-empt it"


def test_a_block_cannot_be_resent_for_ever():
    # NAKs step the fast rung, a Data Sync Error the slot rung, and a pair on
    # the slowest of both would resend for ever -- every loop looks like
    # progress. MAX_BLOCK_ATTEMPTS bounds them all.
    assert protocol.MAX_BLOCK_ATTEMPTS > protocol.FAST_TRIES * len(FB.RUNGS), \
        "the ceiling must leave room for every fast rung's tries"
    leader, _ = T.paired(3)
    leader.start_data(0)
    assert leader.attempts == 1, "the first transmission counts as an attempt"
    leader.attempts = protocol.MAX_BLOCK_ATTEMPTS
    leader.send_block()
    assert leader.result == protocol.ERR_CRC, \
        "past the ceiling, send_block must give up rather than queue another"


def test_finishing_the_ending_still_resets_the_stall_budget():
    # The closing handshake is deliberately quiet for a while -- the Follower
    # lingers to answer a repeat. That must not read as a stall.
    assert protocol.LINGER_SLOTS < protocol.STALL_SLOTS, \
        "the Follower's linger would trip the stall budget"
    retry_cycle = protocol.COMPLETE_RETRY_SLOTS + 1
    assert retry_cycle < protocol.STALL_SLOTS, \
        "the Leader's Complete retry interval would trip the stall budget"


def test_a_block_has_an_outer_deadline():
    # The backstop for the cases nobody predicted. The other two budgets bound
    # particular ways of getting stuck; this one bounds a block.
    leader, _ = T.paired(3)
    leader.start_data(0)
    assert leader.result is None
    leader.step(protocol.BLOCK_TIMEOUT_MS + 1, T.light_from("_", random.Random(1)))
    assert leader.result == protocol.ERR_LOST_PEER, leader.result


def test_the_deadline_restarts_on_every_accepted_block():
    # Counted over the whole transfer it was a cap on payload length wearing a
    # backstop's clothes. The protocol has no opinion about how long a
    # payload may be.
    follower = HeldTransfer(False)
    follower.levels = T.levels()
    follower.now = 0
    follower.start_data(0)
    follower.now = protocol.BLOCK_TIMEOUT_MS - 1000
    follower.fast_accept(0, b"\x02" + bytes(FB.CHUNK - 1))
    assert follower.block_started == protocol.BLOCK_TIMEOUT_MS - 1000, \
        "accepting a block must restart the deadline"
    follower.step(protocol.BLOCK_TIMEOUT_MS + 1000,
                  T.light_from(" ", random.Random(1)))
    assert follower.result is None, \
        "a transfer longer than one block's deadline must not be cut off"


def test_the_deadline_leaves_room_for_the_slowest_honest_block():
    # Measured: the slowest transfer that has succeeded end to end is 141s --
    # the worst-case credential, two blocks, on a 32ms panel that needed
    # demoting. A deadline near that would turn a slow success into a failure.
    # The slowest single BLOCK measured in a transfer that succeeded is 108s on
    # that panel and 64s across the standard scenarios; a full 64-byte block at
    # the slowest rung would take 140s.
    slowest_block_ms = 140000
    assert protocol.BLOCK_TIMEOUT_MS >= 2 * slowest_block_ms, \
        "a %ds deadline is too close to the %ds a slow block can need" \
        % (protocol.BLOCK_TIMEOUT_MS // 1000, slowest_block_ms // 1000)


def test_the_deadline_applies_even_mid_transmission():
    # A badge part way through sending a block is still stuck if the clock has
    # run out; a backstop that only applies while listening is not a backstop.
    leader, _ = T.paired(3)
    leader.start_data(0)
    assert leader.tx, "the leader should be mid-transmission here"
    leader.step(protocol.BLOCK_TIMEOUT_MS + 1, T.light_from(" ", random.Random(1)))
    assert leader.result == protocol.ERR_LOST_PEER, leader.result


# --- asking for a block again ------------------------------------------------
# A Follower that misses a block's Begin never enters it, so nothing trips
# the framing check and it has nothing to send; the Leader waits for a reply
# that was never coming. So it asks.


def test_a_follower_that_missed_the_block_asks_for_it_again():
    follower = parked("follower", Transfer.STEP_BLOCK, False)
    follower.levels = T.levels()
    rng = random.Random(3)
    now = 0
    # A Leader mid-block: symbols streaming past that the Follower cannot frame,
    # so it never matches a Begin and never makes progress.
    for _ in range(protocol.STALL_SLOTS * 3):
        for _ in range(4):
            follower.step(now, T.light_from(rng.choice("-_"), rng))
            now += FRAME
    assert follower.want_resend or follower.resend_requests, \
        "a Follower getting nowhere should have decided to ask"

    # Then the Leader stops. The request goes out only now: a badge that is
    # transmitting does not listen, and two on air at once is what half duplex
    # exists to prevent.
    sent = None
    for _ in range(protocol.RESEND_QUIET_SLOTS * 8):
        follower.step(now, T.light_from(" ", rng))
        now += FRAME
        if follower.resend_requests and sent is None:
            sent = "".join(follower.tx)
    assert follower.resend_requests >= 1, "the request was never sent"
    assert sent == protocol.SYNC_ERROR, \
        "it should ask with the Data Sync Error Pattern, not something new"


def test_the_request_waits_for_the_peer_to_stop_talking():
    follower = parked("follower", Transfer.STEP_BLOCK, False)
    follower.levels = T.levels()
    rng = random.Random(4)
    now = 0
    for _ in range(protocol.STALL_SLOTS * 4):
        follower.step(now, T.light_from(rng.choice("-_"), rng))
        now += FRAME
    assert not follower.tx, \
        "it must not transmit over a peer that is still sending (I6)"


def test_asking_is_bounded():
    follower = parked("follower", Transfer.STEP_BLOCK, False)
    follower.resend_requests = protocol.MAX_BLOCK_ATTEMPTS
    assert not follower.request_resend(), \
        "requests must run out, or a dead peer is asked for ever"


def test_the_leader_already_understands_the_request():
    # A Leader waiting for a reply hears a Data Sync Error as one.
    leader, _ = T.paired(3, (b"HomeNet", b"hunter2!!"))
    leader.start_data(0)
    leader.tx = []
    leader.fast_pending = False
    leader.sub = Transfer.STEP_FAST_REPLY
    before = leader.clock.duration
    for symbol in protocol.SYNC_ERROR:
        leader.rx.append(symbol)
        leader.leader_rx()
        if leader.tx:
            break
    assert leader.tx, "the Leader should have requeued the block"
    assert leader.clock.duration > before, "and stepped down a rung"
