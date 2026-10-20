"""The shipped control patterns must obey the rules a receiver depends on.

These are static properties of the wire format, not of any particular run, so
they are cheap and they prove something. Each rule below cost real time to
find; two were found by a whole simulator sweep failing.
"""

from harness import load_module
from held import HeldTransfer

protocol = load_module("share_wifi", "protocol")

# Every control pattern, and what a receiver compares of it: the tail
# without its trailing black where the module keeps one (`<NAME>_TAIL`),
# else the whole pattern -- FAST_BEGIN, whose black ends the Begin.
PATTERNS = ("FAST_BEGIN", "CRC_GOOD", "CRC_BAD", "SYNC_ERROR", "COMPLETE",
            "COMPLETE_ACK")


def compared(name):
    return getattr(protocol, name + "_TAIL", getattr(protocol, name))


def test_a_stripped_pattern_loses_only_its_trailing_black():
    for name in PATTERNS:
        pattern = getattr(protocol, name)
        tail = compared(name)
        assert pattern.startswith(tail) and \
            set(pattern[len(tail):]) <= {protocol.LOW}, name
    assert not hasattr(protocol, "FAST_BEGIN_TAIL"), \
        "FAST_BEGIN is matched whole: its black ends the Begin"


def test_every_pattern_has_black_in_what_is_compared():
    # The rung's bits are only ever middle and high, so black inside the
    # compared string is what keeps a pattern from being read in them.
    for name in PATTERNS:
        assert protocol.LOW in compared(name), \
            "%s is compared as %r, with no black" % (name, compared(name))


def test_no_pattern_keeps_transmitting_after_a_receiver_would_have_acted():
    # A receiver acts when the match fires and replies on the next slot
    # boundary. Whatever the sender still has queued then goes out while the
    # peer is answering -- and a transmitting badge ignores what it sees, so
    # the reply's opening symbols are lost. One slot of overlap survives; two
    # does not: two trailing blacks lost 144 runs of 144.
    for name in PATTERNS:
        overlap = len(getattr(protocol, name)) - len(compared(name))
        assert overlap <= 1, \
            "%s has %d slots left to send once its match fires" % (name, overlap)


def test_the_retry_budgets_cover_the_closing_round_trip():
    # COMPLETE_RETRY_SLOTS and LINGER_SLOTS are budgets over the pattern
    # lengths, not free constants. A retry that fires before the
    # acknowledgement has finished arriving transmits straight over it, and the
    # Leader then never sees an answer it was sent. Changing COMPLETE_ACK's
    # length without revisiting these silently breaks every transfer's ending.
    round_trip = len(protocol.COMPLETE_ACK) + 2
    assert protocol.COMPLETE_RETRY_SLOTS > round_trip, \
        "a retry at %d slots talks over a %d-slot acknowledgement" \
        % (protocol.COMPLETE_RETRY_SLOTS, round_trip)
    assert protocol.LINGER_SLOTS > protocol.COMPLETE_RETRY_SLOTS, \
        "the Follower must still be on air when the Leader retries"


def test_a_late_acknowledgement_is_not_talked_over():
    # A Follower held up -- a collection of 65-81 ms, the badge's own on
    # 2026-10-09, is ten 8 ms slots -- answers so late that its answer is
    # still arriving when the old fixed retry, COMPLETE_RETRY_SLOTS + 1
    # slots after Complete, went out over it. Unanswered means silent:
    # while lit slots arrive the Leader keeps listening -- up to
    # COMPLETE_REPEAT_MAX_SLOTS -- and hears the answer out.
    tail = list(protocol.COMPLETE_ACK_TAIL)
    late = protocol.COMPLETE_REPEAT_MAX_SLOTS - len(tail)
    assert late + 1 < protocol.COMPLETE_RETRY_SLOTS + 1 < late + len(tail)
    leader = HeldTransfer(True, [b"x"])
    leader.sub = leader.STEP_FINISH
    leader.rx = []
    leader.wait_slots = leader.finish_slots = 0
    for symbol in [protocol.LOW] * late + tail:
        assert leader.result is None
        leader.rx.append(symbol)
        leader.leader_rx()
        assert not leader.tx, "a retry went out over the acknowledgement"
    assert leader.result == protocol.OK


def test_a_silent_follower_still_gets_complete_repeated():
    leader = HeldTransfer(True, [b"x"])
    leader.sub = leader.STEP_FINISH
    leader.rx = []
    leader.wait_slots = leader.finish_slots = 0
    tries = leader.complete_tries
    for _ in range(protocol.COMPLETE_RETRY_SLOTS + 1):
        leader.rx.append(protocol.LOW)
        leader.leader_rx()
    assert leader.tx and leader.complete_tries == tries + 1


def test_stray_light_cannot_hold_the_repeat_past_the_followers_linger():
    # A garbled answer, then noise read as light: the repeat still goes out
    # by COMPLETE_REPEAT_MAX_SLOTS after the last Complete, inside the
    # Follower's LINGER_SLOTS, so there is someone left to answer it.
    assert protocol.COMPLETE_REPEAT_MAX_SLOTS == protocol.LINGER_SLOTS - 2
    leader = HeldTransfer(True, [b"x"])
    leader.sub = leader.STEP_FINISH
    leader.rx = []
    leader.finish_slots = leader.wait_slots = 0
    tries = leader.complete_tries
    heard = list("__-_ ") + [protocol.MID, protocol.LOW] * 20
    for n, symbol in enumerate(heard, 1):
        leader.rx.append(symbol)
        leader.leader_rx()
        if leader.tx:
            break
    assert leader.complete_tries == tries + 1, "no repeat"
    assert n == protocol.COMPLETE_REPEAT_MAX_SLOTS, n
    assert leader.result is None
