"""The shipped control patterns must obey the rules a receiver depends on.

These are static properties of the wire format, not of any particular run, so
they are cheap and they prove something. The measurements that motivated each
one live in tests/share_wifi/pattern_survey.py, which is where to go when
changing a pattern -- it runs candidate sets through the real protocol.

Each rule below cost real time to find. Two of them were found the expensive
way, by a whole simulator sweep failing, and are checkable in microseconds.
"""

import pattern_survey
from harness import load_module

protocol = load_module("share_wifi", "protocol")

SHIPPED = {name: getattr(protocol, name) for name in pattern_survey.FIELDS}


def test_the_shipped_patterns_pass_the_audit():
    problems = pattern_survey.audit(SHIPPED)
    assert not problems, "\n".join(problems)


def test_the_survey_agrees_with_the_module_about_which_match_is_stripped():
    # pattern_survey models protocol.py's matching. If protocol.py starts or
    # stops stripping a pattern's trailing black, the survey's answers become
    # quietly wrong rather than obviously wrong.
    for name in pattern_survey.STRIPPED:
        assert hasattr(protocol, name + "_TAIL"), \
            "survey strips %s but the module has no %s_TAIL" % (name, name)
        assert getattr(protocol, name + "_TAIL") == \
            pattern_survey.strip_black(SHIPPED[name])
    assert not hasattr(protocol, "BEGIN_BLOCK_TAIL"), \
        "Begin Block must be matched whole: its trailing black separates the " \
        "pattern from the payload, and matching early reads it as a separator"


def test_every_pattern_is_unforgeable_by_payload_data():
    # Payload is only ever grey and white, so black inside the COMPARED string
    # is what makes a pattern impossible to forge. Black that gets stripped
    # before comparison does not count, which is the trap: halving the patterns
    # leaves 'CRC_GOOD' compared as '--', which is just two 1 bits.
    for name in pattern_survey.FIELDS:
        compared = pattern_survey.matched(name, SHIPPED[name])
        assert protocol.LOW in compared, \
            "%s is compared as %r, which payload data could produce" \
            % (name, compared)


def test_no_pattern_keeps_transmitting_after_a_receiver_would_have_acted():
    # A receiver acts when the match fires and replies on the next slot
    # boundary. Whatever the sender still has queued then goes out while the
    # peer is answering -- and a transmitting badge ignores what it sees, so the
    # reply's opening symbols are lost. One slot of overlap survives; two does
    # not. Measured: two trailing blacks lost 144 runs out of 144, the Leader
    # reading the acknowledgement exactly one symbol short every time.
    for name in pattern_survey.FIELDS:
        pattern = SHIPPED[name]
        overlap = len(pattern) - len(pattern_survey.matched(name, pattern))
        assert overlap <= 1, \
            "%s has %d slots left to send once its match fires" % (name, overlap)


def test_end_block_can_be_told_from_a_field_separator():
    # collect_block distinguishes them only by End Block's two opening data
    # symbols landing two bits into a byte. A leading black here is genuinely
    # ambiguous, because payload can carry consecutive separators -- an empty
    # field -- not merely awkward to parse.
    end = protocol.END_BLOCK
    assert end[:2] == protocol.MID + protocol.HIGH, \
        "End Block opens %r, but collect_block keys on '_-'" % end[:2]
    assert end[2] == protocol.LOW


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


def test_a_shortened_pattern_set_is_rejected_for_the_right_reason():
    # The audit has to be able to fail, or its silence is not evidence. One
    # repetition is the shortest anyone would reach for and it breaks two ways.
    problems = pattern_survey.audit(pattern_survey.repeated(1))
    assert problems, "a single-repetition set should not pass"
    assert any("no black" in p for p in problems), problems
    assert any("SYNC_ERROR" in p for p in problems), problems


def test_doubling_a_trailing_black_is_rejected():
    doubled = {k: v + " " for k, v in SHIPPED.items()}
    problems = pattern_survey.audit(doubled)
    assert any("still to send" in p for p in problems), problems
