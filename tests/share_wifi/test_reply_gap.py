"""The silent slot before a reply, and a message held up whole.

A reply has always had exactly one silent slot before it: the slot the
replying badge spent deciding. The Leader's grid depends on that -- it does
not phase-lock on a reply, which needs 1.5 slots of silence -- so the first
slot a queued message goes out on is fixed by when it was decided, not by
when this badge next got round to showing it. And a collection or a fit
between the queueing and the showing delays the message whole rather than
eating its lead-in. See "Transmitting, and the reply gap" in the protocol.
"""

from harness import load_module

P = load_module("share_wifi", "protocol")
R = load_module("share_wifi", "reader")


def badge(index=None):
    reader = R.PolledReader()
    reader.start()
    reader.exact = True
    t = P.Transfer(False, None, reader)
    t.levels = P.Levels()
    t.levels.fit(32, 64, 112)
    t.start_data(0, P.EXACT_INDEX if index is None else index)
    return t


def decided_send(t, k, symbols):
    """Queue `symbols` as a decision of slot `k` would."""
    t.deciding = k
    t.deciding_end = t.clock.start_of(k + 1)
    t.send(symbols)
    t.deciding = None


def test_a_reply_leaves_exactly_one_silent_slot():
    t = badge()
    d = t.clock.duration
    k = 20
    # Decided as slot k's window closes, inside slot k.
    decided_send(t, k, list("-_-_"))
    t.now_us = t.clock.start_of(k) + t.w_close
    assert t.field_at(t.now_us) == P.LOW
    assert t.tx_slot == k + 2, (t.tx_slot, k)
    assert t.field_at(t.clock.start_of(k + 1) + d // 2) == P.LOW
    assert t.field_at(t.clock.start_of(k + 2)) == P.HIGH


def test_a_late_pass_still_leaves_one_slot_and_no_more():
    # Shown first part way through the silent slot: still k + 2.
    t = badge()
    decided_send(t, 20, list("-_"))
    t.field_at(t.clock.start_of(21) + 3000)
    assert t.tx_slot == 22


def test_a_stall_delays_the_message_whole():
    # A 46 ms collection between queueing and the first showing: the lead-in
    # goes out in full, from the next boundary.
    t = badge()
    k = 20
    decided_send(t, k, [P.LOW] * 4 + list("-_ -_ -_ "))
    after = t.clock.start_of(k) + t.w_close + 46000
    t.field_at(after)
    assert t.tx_slot == t.clock.number(after) + 1
    shown = [t.field_at(t.clock.start_of(t.tx_slot + i) + 10)
             for i in range(5)]
    assert shown == [P.LOW] * 4 + [P.HIGH], shown


def test_a_send_no_decision_queued_starts_on_the_next_boundary():
    t = badge()
    t.now_us = t.clock.start_of(30) + 1234
    t.send(list("-_"))
    t.field_at(t.now_us)
    assert t.tx_slot == 31


def test_a_demotion_between_queueing_and_showing_cannot_push_it_away():
    # The decided slot's end is held as a time: renumbering the grid keeps
    # it where it was. Held as a slot number, a resend after a demotion
    # waited dozens of slots on the slower grid and the peer gave up.
    t = badge(P.SLOT_LADDER.index(8))
    k = 49
    end = t.clock.start_of(k + 1)
    decided_send(t, k, list("-_"))
    t.demote(end)
    t.field_at(end + 100)
    assert t.tx_slot == t.clock.number(end) + 1, (t.tx_slot,
                                                  t.clock.number(end))


def test_a_polled_followers_owed_collection_lengthens_its_first_reply_gap():
    # The one exception to one silent slot: a POLLED Follower that accepted
    # a block owes a collection (collect_owed) and makes it as it queues its
    # next reply, which the reply-gap rule then delays whole -- 20-46 ms,
    # one to three slots more at 16-32 ms. That is 1.5 slots of silence or
    # more, so the Leader may phase-lock on this reply: allowed, and why an
    # EXACT Follower, which reads through a collection, does not owe.
    t = badge(P.BACKLIGHT_LIT_INDEX)
    t.reader.exact = False
    t.collect_owed = True
    d = t.clock.duration
    k = 40
    decided_send(t, k, list("__- __- "))
    assert t.collect_due and not t.collect_owed
    after = t.clock.start_of(k) + t.w_close + 46000      # the collection
    t.field_at(after)
    assert t.tx_slot == t.clock.number(after) + 1
    silent = t.tx_slot - k - 1
    assert silent >= max(1, -(-46000 // d)), (silent, d)


def test_an_exact_follower_collects_at_once():
    t = badge()
    t.sub = t.STEP_VERDICT
    t.pending_block = True
    t.pending_tokens = [1]
    t.rx = list(P.CRC_GOOD_TAIL)
    t.follower_rx()
    assert t.collect_due and not t.collect_owed
