"""Two badges, pointed at each other, driven frame by frame.

This is the test that was missing. test_framing.py feeds symbol lists straight
into the receiver, which validates the codec and completely skips the frame
loop -- so it never noticed that the transmitter was emitting one symbol per
*frame* into a receiver decoding one symbol per *slot*. Every pattern went out
inside a single slot and no transfer could ever have completed.

Anything that spans the frame loop belongs here, not in the codec tests.
"""

import random

from harness import load_module

protocol = load_module("share_wifi", "protocol")
Transfer = protocol.Transfer
Levels = protocol.Levels
build_tokens = protocol.build_tokens
parse_payload = protocol.parse_payload
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
    leader = Transfer(True, payload)
    follower = Transfer(False)
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

        slot = leader.clock.duration // 1000
        budget = (len(build_tokens(ssid, key)) * 8 + 400) * slot // FRAME_MS
        link(leader, follower, budget, seed=index,
             stop_when=both_done(leader, follower))

        assert leader.result == protocol.OK, "leader: %s" % leader.result
        assert follower.result == protocol.OK, "follower: %s" % follower.result
        assert parse_payload(follower.received) == [ssid, key]


def test_transfer_survives_sensor_noise():
    # Noise up to a third of the gap between levels, read once a frame: one
    # seed is not evidence, so sixteen, and all but one deliver.
    delivered = 0
    for seed in range(1, 17):
        leader, follower = paired(3)
        leader.start_data(0)
        follower.start_data(0)
        slot = leader.clock.duration // 1000
        link(leader, follower, 900 * slot // FRAME_MS, noise=1200, seed=seed,
             stop_when=both_done(leader, follower))
        if leader.result == protocol.OK:
            delivered += 1
            assert parse_payload(follower.received) == [b"HomeNet",
                                                        b"hunter2!!"]
    assert delivered >= 15, delivered


def test_transfer_works_at_every_rung_of_the_ladder():
    for index in range(len(SLOT_LADDER)):
        slot = SLOT_LADDER[index]
        if slot < FRAME_MS * 2.5 or slot < 50:
            continue                # the badge could not meet this rung anyway
        leader, follower = paired(index)
        leader.start_data(0)
        follower.start_data(0)
        link(leader, follower, 700 * slot // FRAME_MS, seed=index,
             stop_when=both_done(leader, follower))
        assert leader.result == protocol.OK, "%dms rung: %s" % (slot, leader.result)


def test_leader_reports_lost_peer_when_nobody_answers():
    leader, _ = paired(3)
    leader.start_data(0)
    now = 0
    for _ in range(4000):
        leader.step(now, light_from(" ", random.Random(1)))
        now += FRAME_MS
        if leader.result:
            break
    assert leader.result == protocol.ERR_LOST_PEER, leader.result


def test_complete_pattern_is_actually_transmitted():
    # The Leader must not declare success while the Complete Pattern is still
    # queued, or the Follower waits for an ending that never arrives.
    leader, follower = paired(3)
    leader.start_data(0)
    follower.start_data(0)
    slot = leader.clock.duration // 1000
    link(leader, follower, 700 * slot // FRAME_MS,
         stop_when=lambda: follower.result)
    assert follower.result == protocol.OK, "follower never saw Complete"


def test_follower_waits_to_see_the_leader_before_beaconing():
    # Beaconing at a badge that is not there wastes power, and puts a field in
    # front of a Leader that is not yet looking for one.
    import random as _r
    follower = Transfer(False)
    follower.started = 0
    follower.phase_started = 0
    rng = _r.Random(1)

    now = 0
    for _ in range(80):                       # nothing out there
        field = follower.step(now, light_from(" ", rng))
        assert field == " ", "should hold black until it has seen an Announce"
        now += FRAME_MS
    assert not follower.saw_announce

    for _ in range(120):                      # a Leader starts announcing
        level = "-" if (now // protocol.ANNOUNCE_HALF_MS) % 2 else " "
        follower.step(now, light_from(level, rng))
        now += FRAME_MS
    assert follower.saw_announce, "should have locked on to the Leader's beacon"


def test_no_allocation_of_the_progress_string_per_frame():
    # The header of the app forbids allocating in the per-frame path; this text
    # only changes once a block.
    leader, follower = paired(3)
    leader.start_data(0)
    before = leader.progress
    for i in range(10):
        leader.field_at(i * FRAME_MS * 1000)
        leader.work(i * FRAME_MS * 1000)
    assert leader.progress is before, "progress should not be rebuilt per frame"


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


# --- rate selection and demotion --------------------------------------------
# The once-a-frame tests that lived here drove a lagging pixel panel at frame
# rate and watched both badges demote. That reader is gone: a transfer reads
# through the DMA ring or a light_level() loop, on the backlight. A demotion's
# mechanics -- one request, one rung, both badges together -- are in
# test_recovery.py; where a pair starts, in test_tier.py.
#
# The Leader's own demotion, after CRC failures on a rung faster than the
# proven one, still goes out as a Data Sync Error the Follower cannot match
# in STEP_VERDICT -- known defect F-B1, left as it was: in simulation, a pair
# forced onto a rung too fast for its sensor splits that way.


def test_a_demoted_resend_does_not_need_separator_state():
    # This used to matter: the receiver tracked "have I seen the separator" to
    # reject a second one, and a discarded block had to rewind that flag or the
    # resend failed forever. Allowing any number of fields removed the flag and
    # the whole failure mode with it.
    follower = Transfer(False)
    follower.levels = levels()
    follower.now = 0
    follower.start_data(0, follower.conservative_index())
    assert not hasattr(follower, "seen_separator")
    follower.reset_block()
    assert follower.tokens == [] and not follower.in_block
