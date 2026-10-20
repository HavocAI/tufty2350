"""Where a slot is decided, and which slot is decided next.

A receiver decides slot k from its window once the readings are in, after
the fact; `next` is the slot it decides next. Every event that renumbers the
grid -- a re-base, a demotion -- or puts this badge on air moves `next`, and
these pin how. See section 10.2 of the protocol spec.
"""

from harness import load_module
from held import HeldTransfer
import held

P = load_module("share_wifi", "protocol")
R = load_module("share_wifi", "reader")


def listener(index, lit=False, leader=False):
    reader = held.HeldReader()
    reader.start()
    t = HeldTransfer(leader, (b"net", b"key1") if leader else None, reader)
    t.lit = lit
    t.levels = P.Levels()
    t.levels.fit(32, 64, 112)
    t.start_data(0, index)
    if leader:
        t.tx = []
        t.sub = t.STEP_CRC
    return t


def feed(t, start, end, value, every=100, budgets=False):
    """Readings of `value` (or value(us)) from `start` to `end` us, and the
    protocol's work every ms. `budgets`: reset the silence and stall
    budgets, for a listener nobody is sending to."""
    for us in range(start, end, every):
        t.reader.add(us, value(us) if callable(value) else value)
        if us % 1000 == 0:
            t.work(us)
            if budgets:
                t.idle_slots = t.stall_slots = 0
    t.work(end)


def test_the_exact_windows_are_where_the_rule_puts_them():
    lit = listener(P.EXACT_LIT_INDEX, lit=True)
    assert (lit.w_open, lit.w_close, lit.w_parts) == (3200, 6800, 5)
    # Dark on 16 ms: 40% of the slot outweighs SETTLE_DARK_US's 5 ms.
    dark = listener(P.EXACT_INDEX)
    assert (dark.w_open, dark.w_close) == (6400, 13600)


def test_a_rung_too_fast_for_the_settle_keeps_a_quarter_slot_to_read():
    # A dark badge put on the lit 8 ms rung -- a misread flag -- still reads:
    # its own rule leaves [5000, 6000], half WINDOW_MIN_US, and a window any
    # shorter would take a quarter of the slot instead.
    t = listener(P.EXACT_LIT_INDEX, lit=False)
    assert t.w_close - t.w_open >= P.WINDOW_MIN_US // 2
    assert t.w_close - t.w_open >= min(8000 // 4, P.WINDOW_MIN_US // 2)


def test_slots_are_decided_in_order_once_their_windows_close():
    t = listener(P.EXACT_INDEX)
    decided = []
    t.follower_rx = lambda: decided.append(t.rx[-1])
    feed(t, 0, 100000, 32)
    # 0..99 ms is six whole 16 ms slots, and the seventh's window closed at
    # 96 + 13.6 ms: not yet.
    assert t.next == 6, t.next
    assert decided == [P.LOW] * 6


def test_a_demotion_renumbers_and_skips_only_the_slot_it_straddles():
    t = listener(P.EXACT_LIT_INDEX, lit=True)
    feed(t, 0, 5000000, 32, every=250, budgets=True)
    assert t.next > 600, t.next
    when = 5000000
    t.demote(when)
    assert t.clock.duration == 10000
    first = t.clock.start_of(t.next)
    assert 0 < first - when <= t.clock.duration, (first, when)
    before = t.next
    feed(t, when, when + 50000, 32, every=250, budgets=True)
    assert t.next > before, "it should go on deciding after a demotion"


def test_a_rebase_decides_slot_zero_of_the_new_grid():
    t = listener(P.EXACT_INDEX)
    t.sub = t.STEP_BLOCK
    decided = []
    t.follower_rx = lambda: decided.append(t.rx[-1])
    edge = 100000 + 3700            # not on this grid's boundaries
    feed(t, 0, 140000, lambda us: 112 if us >= edge else 32)
    lag = P.EDGE_LAG_EXACT_DARK_US
    assert abs(t.clock.epoch - (edge - lag)) <= 200, t.clock.epoch - edge
    # The first slot of the new grid starts at the boundary, and is lit.
    assert decided[-2:] == [P.HIGH, P.HIGH], decided[-4:]
    assert t.clock.number(t.clock.epoch) == 0


def test_exactly_one_rebase_a_message():
    t = listener(P.EXACT_INDEX)
    t.sub = t.STEP_BLOCK
    rebases = []
    real = t.rebase

    def rebase(boundary, crossing):
        rebases.append(boundary)
        real(boundary, crossing)
    t.rebase = rebase
    t.follower_rx = lambda: None
    edge = 100000
    feed(t, 0, 300000, lambda us: 112 if us >= edge else 32)
    assert len(rebases) == 1, rebases


def test_no_slot_is_decided_while_this_badge_is_on_air():
    t = listener(P.EXACT_INDEX)
    decided = []
    t.follower_rx = lambda: decided.append(t.rx[-1])
    feed(t, 0, 50000, 32)
    t.idle_slots = 3
    t.send([P.HIGH, P.MID] * 75 + [P.HIGH])        # 151 slots
    field = t.field_at(50000)
    first_tx = t.tx_slot
    feed(t, 50000, t.clock.start_of(first_tx + 151), 32)
    assert decided == [P.LOW] * 3, "decided while transmitting"
    assert t.idle_slots == 0
    # The first slot after the last transmitted is the first decided.
    end = first_tx + 151
    feed(t, t.clock.start_of(end), t.clock.start_of(end + 3), 32)
    assert len(decided) >= 4 and t.next >= end + 1
    assert field == P.LOW


def test_every_rung_down_to_the_start_qualifies():
    # exact_index stops at the first rung that fails, so the rung it picks
    # and every slower one have a WINDOW_MIN_US window.
    # The dark start is held slower than its rule allows (EXACT_DARK_START_MS).
    assert P.EXACT_INDEX <= P.exact_index(P.SETTLE_DARK_US, P.GUARD_DARK_US)
    assert P.SLOT_LADDER[P.EXACT_INDEX] == P.EXACT_DARK_START_MS
    for settle, guard, index in ((P.SETTLE_LIT_US, P.GUARD_LIT_US,
                                  P.EXACT_LIT_INDEX),
                                 (P.SETTLE_DARK_US, P.GUARD_DARK_US,
                                  P.exact_index(P.SETTLE_DARK_US,
                                                P.GUARD_DARK_US))):
        assert P.exact_index(settle, guard) == index
        for duration in P.SLOT_LADDER[:index + 1]:
            opens, closes = P.exact_window(duration * 1000, settle, guard)
            assert closes - opens >= P.WINDOW_MIN_US, (duration, settle)
