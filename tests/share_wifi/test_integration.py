"""Two badges, pointed at each other, driven through time.

This is the test that was once missing: codec tests fed symbol lists
straight into the receiver and never noticed a transmitter emitting one
symbol per *frame* into a receiver decoding one per *slot*. Anything that
spans the loop belongs here: the pair runs on held.run_pair(), every 100 us,
its bursts played as the pacer plays them.
"""

import random

import held
from harness import load_module
from held import HeldTransfer

protocol = load_module("share_wifi", "protocol")
Transfer = protocol.Transfer
Levels = protocol.Levels
SLOT_LADDER = protocol.SLOT_LADDER

FRAME_MS = 16
AMBIENT, CONTRAST = 200, 9000
BRIGHTNESS = {" ": 0.0, "_": 0.45, "-": 1.0}


def levels():
    out = Levels()
    out.fit(AMBIENT, int(AMBIENT + 0.45 * CONTRAST), AMBIENT + CONTRAST)
    return out


def light_from(field, rng, noise=80):
    return int(AMBIENT + BRIGHTNESS[field] * CONTRAST + rng.uniform(-noise, noise))


def seen_with(noise, seed):
    """Counts for a backlight level, at the paired levels: low 0.5 reads
    AMBIENT and full AMBIENT + CONTRAST, the middle between."""
    rng = random.Random(seed)

    def seen(level):
        lit = max(0.0, level - 0.5) / 0.5
        return int(AMBIENT + lit * CONTRAST + rng.uniform(-noise, noise))
    return seen


def both_done(leader, follower):
    """Stop only when BOTH badges have finished.

    Not `leader.result or follower.result`. The Leader finishes the moment the
    Follower acknowledges the Complete Pattern, while the Follower lingers a few
    slots afterwards so that it is still on air to answer a repeat if that
    acknowledgement went missing. Stopping at the first result therefore reads
    the Follower's result before it has one. The frame budget, not this, is what
    bounds a test that genuinely hangs.
    """
    return lambda: leader.result and follower.result


def link(leader, follower, frames, frame_ms=FRAME_MS, noise=80, seed=1,
         start=0, stop_when=None):
    """Run both badges for `frames`, each seeing what the other displays."""
    rng = random.Random(seed)
    now = start
    leader_field = follower_field = " "
    for _ in range(frames):
        # Each badge samples the light the other is emitting this frame.
        seen_by_leader = light_from(follower_field, rng, noise)
        seen_by_follower = light_from(leader_field, rng, noise)
        leader_field = leader.step(now, seen_by_leader)
        follower_field = follower.step(now, seen_by_follower)
        if stop_when and stop_when():
            return now
        now += frame_ms
    return now


def paired(index, payload=(b"HomeNet", b"hunter2!!")):
    """A Leader and Follower already calibrated and clocked to the same epoch."""
    leader = HeldTransfer(True, payload)
    follower = HeldTransfer(False)
    for badge_side in (leader, follower):
        badge_side.levels = levels()
        badge_side.now = 0
        badge_side.clock.set_index(index)
    return leader, follower


def test_a_symbol_is_held_for_a_whole_slot():
    # The regression. One symbol per slot, not one per frame.
    leader, _ = paired(0)
    leader.start_data(0)

    slot = leader.clock.duration // 1000
    fields = []
    now = 0
    for _ in range(slot * 3 // FRAME_MS):
        fields.append((now, leader.field_at(now * 1000)))
        now += FRAME_MS

    by_slot = {}
    for ms, field in fields:
        by_slot.setdefault(ms // slot, set()).add(field)
    for number, seen in by_slot.items():
        assert len(seen) == 1, "slot %d showed %r, should hold one field" % (number, seen)
    assert len(by_slot) >= 2, "test should span several slots"


def test_the_queue_starts_on_a_slot_boundary():
    # Fixed by the first field shown after the queue: the next boundary.
    leader, _ = paired(0)
    leader.start_data(0)
    assert leader.tx_slot is None
    leader.field_at(0)
    assert leader.tx_slot == leader.clock.number(0) + 1


def test_data_transfer_completes_and_verifies():
    for index, (ssid, key) in enumerate([(b"HomeNet", b"hunter2!!"),
                                         (b"A" * 32, b"k" * 63)]):
        leader, follower = paired(3, (ssid, key))
        leader.start_data(0)
        follower.start_data(0)
        held.run_pair(leader, follower, seen_with(80, index))
        assert leader.result == protocol.OK, "leader: %s" % leader.result
        assert follower.result == protocol.OK, "follower: %s" % follower.result
        assert follower.received == [ssid, key]


def test_transfer_survives_sensor_noise():
    # Noise up to a tenth of the swing on every reading: one seed is not
    # evidence, so eight, and all deliver.
    for seed in range(1, 9):
        leader, follower = paired(3)
        leader.start_data(0)
        follower.start_data(0)
        held.run_pair(leader, follower, seen_with(900, seed))
        assert leader.result == protocol.OK, (seed, leader.result)
        assert follower.received == [b"HomeNet", b"hunter2!!"]


def test_transfer_works_at_every_rung_of_the_ladder():
    for index in range(len(SLOT_LADDER)):
        leader, follower = paired(index)
        leader.start_data(0)
        follower.start_data(0)
        held.run_pair(leader, follower, seen_with(80, index))
        assert leader.result == protocol.OK, \
            "%dms rung: %s" % (SLOT_LADDER[index], leader.result)


def test_leader_reports_lost_peer_when_nobody_answers():
    leader, _ = paired(3)
    leader.start_data(0)
    held.run_pair(leader, None, seen_with(80, 1), limit_ms=120000)
    assert leader.result == protocol.ERR_LOST_PEER, leader.result


def test_a_leader_that_did_not_hold_sends_its_first_block_twice():
    # No reply at all to the first block: a Follower that found flicker
    # where the Leader did not took none of it as light. A Leader that did
    # not hold sends it once more before giving up; one that held, never.
    for flicker, sent in ((False, 2), (True, 1)):
        leader, _ = paired(3)
        leader.pair_flicker = flicker
        leader.start_data(0)
        held.run_pair(leader, None, seen_with(80, 1), limit_ms=120000)
        assert leader.result == protocol.ERR_LOST_PEER, leader.result
        assert leader.attempts == sent, (flicker, leader.attempts)


def test_complete_pattern_is_actually_transmitted():
    # The Leader must not declare success while the Complete Pattern is still
    # queued, or the Follower waits for an ending that never arrives.
    leader, follower = paired(3)
    leader.start_data(0)
    follower.start_data(0)
    held.run_pair(leader, follower, seen_with(80, 1))
    assert follower.result == protocol.OK, "follower never saw Complete"


def test_follower_waits_to_see_the_leader_before_its_staircase():
    # Signalling at a badge that is not there wastes power, and puts a field
    # in front of a Leader that is not yet looking for one.
    import random as _r
    follower = HeldTransfer(False)
    follower.started = 0
    follower.phase_started = 0
    rng = _r.Random(1)

    # A reading every 2 ms, each its own noise, as the ring gives them: held
    # for a 16 ms frame, noise past MIN_SWING is a square wave with 16 ms
    # halves, which a 14 ms Announce accepts.
    now = 0
    for _ in range(640):                      # nothing out there
        field = follower.step(now, light_from(" ", rng))
        assert field == " ", "should hold black until it has seen an Announce"
        now += protocol.FINE_MS
    assert follower.phase == protocol.PHASE_LISTEN

    for _ in range(300):                      # a Leader starts announcing
        level = "-" if (now // protocol.ANNOUNCE_FAST_HALF_MS) % 2 else " "
        follower.step(now, light_from(level, rng))
        now += 2
    assert follower.phase != protocol.PHASE_LISTEN, \
        "should have locked on to the Leader's beacon"


def test_receive_buffer_stays_bounded_while_waiting():
    # It used to grow for the whole wait, and get sliced and joined every slot.
    leader, follower = paired(3)
    follower.start_data(0)
    rng = __import__("random").Random(2)
    now = 0
    for _ in range(600):
        follower.step(now, light_from("_", rng))
        now += FRAME_MS
        if follower.result:
            break
    assert len(follower.rx) <= protocol.MAX_PATTERN * 2 + 2, len(follower.rx)


# A demotion's mechanics -- one request, one rung, both badges together --
# are in test_recovery.py; where a pair starts, in test_handshake.py.
