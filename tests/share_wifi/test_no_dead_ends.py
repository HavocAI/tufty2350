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
import test_integration as T

protocol = load_module("share_wifi", "protocol")
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
          ("leader", Transfer.STEP_CRC, False),
          ("leader", Transfer.STEP_VERDICT, False),
          ("leader", Transfer.STEP_FINISH, False),
          ("follower", Transfer.STEP_BLOCK, False),
          ("follower", Transfer.STEP_BLOCK, True),
          ("follower", Transfer.STEP_VERDICT, False),
          ("follower", Transfer.STEP_FINISH, False)]

NAMES = {Transfer.STEP_BLOCK: "STEP_BLOCK", Transfer.STEP_CRC: "STEP_CRC",
         Transfer.STEP_VERDICT: "STEP_VERDICT",
         Transfer.STEP_FINISH: "STEP_FINISH"}


def parked(side_name, sub, in_block):
    """A badge already in the data phase, parked in one sub-state."""
    leader, follower = T.paired(7)               # the 50ms rung
    side = leader if side_name == "leader" else follower
    side.start_data(0)
    side.sub = sub
    if side_name == "leader":
        side.tx = []                             # stop it mid-conversation
        if sub == Transfer.STEP_CRC:
            side.crc_started = False
    else:
        side.in_block = in_block
        if in_block:
            side.tokens = [65]
            side.bits = []
        if sub == Transfer.STEP_VERDICT:
            side.pending_tokens = [65]
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
    # MAX_BLOCK_RETRIES bounds CRC failures, but the Data Sync Error path resets
    # it on purpose, so sync-error resends were bounded by nothing. A Leader
    # reading noise as a sync error demoted, resent, and once at the slowest
    # rung resent for ever -- every loop looked like progress.
    assert protocol.MAX_BLOCK_ATTEMPTS >= (protocol.MAX_BLOCK_RETRIES + 1), \
        "the ceiling must leave room for the retries below it"
    leader, _ = T.paired(7)
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
    leader, _ = T.paired(7)
    leader.start_data(0)
    assert leader.result is None
    leader.step(protocol.BLOCK_TIMEOUT_MS + 1, T.light_from("_", random.Random(1)))
    assert leader.result == protocol.ERR_LOST_PEER, leader.result


def test_the_deadline_restarts_on_every_accepted_block():
    # Counted over the whole transfer it was a cap on payload length wearing a
    # backstop's clothes -- 300s is about 750 bytes at the 50ms rung. The
    # protocol has no opinion about how long a payload may be.
    follower = T.Transfer(False)
    follower.levels = T.levels()
    follower.now = 0
    follower.start_data(0)
    follower.pending_tokens = [65, 66]
    follower.now = protocol.BLOCK_TIMEOUT_MS - 1000
    follower.accept_pending()
    assert follower.block_started == protocol.BLOCK_TIMEOUT_MS - 1000, \
        "accepting a block must restart the deadline"
    # and the badge survives well past what a whole-transfer deadline allowed
    follower.step(protocol.BLOCK_TIMEOUT_MS + 1000, T.light_from(" ", random.Random(1)))
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
    leader, _ = T.paired(7)
    leader.start_data(0)
    assert leader.tx, "the leader should be mid-transmission here"
    leader.step(protocol.BLOCK_TIMEOUT_MS + 1, T.light_from(" ", random.Random(1)))
    assert leader.result == protocol.ERR_LOST_PEER, leader.result


# --- asking for a block again ------------------------------------------------
# The gap every failing run used to fall into: a Follower that misses a Begin
# Block Pattern never enters the block, so nothing trips the framing check and
# it has nothing to send. The Leader waits in STEP_CRC for a reply that was
# never coming, and both badges stay on the rung they started on because the
# only route to demotion is a framing failure that cannot happen.


def test_a_follower_that_missed_the_block_asks_for_it_again():
    follower = parked("follower", Transfer.STEP_BLOCK, False)
    follower.levels = T.levels()
    rng = random.Random(3)
    now = 0
    # A Leader mid-block: symbols streaming past that the Follower cannot frame,
    # so it never matches Begin Block and never makes progress.
    for _ in range(protocol.STALL_SLOTS * 3):
        for _ in range(4):
            follower.step(now, T.light_from(rng.choice("-_"), rng))
            now += FRAME
    assert follower.want_resend or follower.resend_requests, \
        "a Follower getting nowhere should have decided to ask"

    # Then the Leader stops. The request goes out only now: a badge that is
    # transmitting does not listen, and two on air at once is what half duplex
    # exists to prevent.
    for _ in range(protocol.RESEND_QUIET_SLOTS * 8):
        follower.step(now, T.light_from(" ", rng))
        now += FRAME
    assert follower.resend_requests >= 1, "the request was never sent"
    assert "".join(follower.tx) == protocol.SYNC_ERROR, \
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


def test_a_follower_holding_an_unjudged_block_never_asks():
    # It cannot tell an accepted block from a rejected one. Asking would either
    # drop a block the Leader has moved past or duplicate one it has not, and
    # both corrupt the payload while every CRC still passes.
    follower = parked("follower", Transfer.STEP_BLOCK, False)
    follower.pending_tokens = [65, 66]
    assert not follower.request_resend(), \
        "a Follower holding uncommitted tokens must not ask for a resend"
    follower.pending_tokens = []
    assert follower.request_resend()


def test_asking_is_bounded():
    follower = parked("follower", Transfer.STEP_BLOCK, False)
    follower.resend_requests = protocol.MAX_BLOCK_ATTEMPTS
    assert not follower.request_resend(), \
        "requests must run out, or a dead peer is asked for ever"


def test_the_leader_already_understands_the_request():
    # Nothing on the Leader changed for this. The handling of a Data Sync Error
    # in STEP_CRC was always there; nothing was ever able to trigger it.
    leader, _ = T.paired(7, (b"HomeNet", b"hunter2!!"))
    leader.start_data(0)
    leader.tx = []
    leader.sub = Transfer.STEP_CRC
    leader.crc_started = False
    before = leader.clock.duration
    for symbol in protocol.SYNC_ERROR:
        leader.rx.append(symbol)
        leader.leader_rx()
        if leader.tx:
            break
    assert leader.tx, "the Leader should have requeued the block"
    assert leader.clock.duration > before, "and stepped down a rung"
