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
        badge_side.clock.start(0, index)
        badge_side.begin_slots(0, index)
    return leader, follower


def test_a_symbol_is_held_for_a_whole_slot():
    # The regression. One symbol per slot, not one per frame.
    leader, _ = paired(0)
    leader.start_data(0)

    slot = leader.clock.duration
    fields = []
    now = 0
    for _ in range(slot * 3 // FRAME_MS):
        fields.append((now, leader.step_data(now, 0)))
        now += FRAME_MS

    by_slot = {}
    for ms, field in fields:
        by_slot.setdefault(ms // slot, set()).add(field)
    for number, seen in by_slot.items():
        assert len(seen) == 1, "slot %d showed %r, should hold one field" % (number, seen)
    assert len(by_slot) >= 2, "test should span several slots"


def test_the_queue_starts_on_a_slot_boundary():
    leader, _ = paired(0)
    leader.start_data(0)
    assert leader.tx_slot == leader.clock.number(0) + 1


def test_data_transfer_completes_and_verifies():
    for index, (ssid, key) in enumerate([(b"HomeNet", b"hunter2!!"),
                                         (b"A" * 32, b"k" * 63)]):
        leader, follower = paired(3, (ssid, key))
        leader.start_data(0)
        follower.start_data(0)

        slot = leader.clock.duration
        budget = (len(build_tokens(ssid, key)) * 8 + 400) * slot // FRAME_MS
        link(leader, follower, budget, seed=index,
             stop_when=both_done(leader, follower))

        assert leader.result == protocol.OK, "leader: %s" % leader.result
        assert follower.result == protocol.OK, "follower: %s" % follower.result
        assert parse_payload(follower.received) == [ssid, key]


def test_transfer_survives_sensor_noise():
    # Noise up to a third of the gap between levels, which hysteresis and the
    # middle-50% sampling should absorb.
    leader, follower = paired(3)
    leader.start_data(0)
    follower.start_data(0)
    slot = leader.clock.duration
    link(leader, follower, 900 * slot // FRAME_MS, noise=1200, seed=7,
         stop_when=both_done(leader, follower))
    assert leader.result == protocol.OK, leader.result
    assert parse_payload(follower.received) == [b"HomeNet", b"hunter2!!"]


def test_transfer_works_at_every_rung_of_the_ladder():
    for index in range(len(SLOT_LADDER)):
        slot = SLOT_LADDER[index]
        if slot < FRAME_MS * 2.5:
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
    slot = leader.clock.duration
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
        level = "-" if (now // 250) % 2 else "_"
        follower.step(now, light_from(level, rng))
        now += FRAME_MS
    assert follower.saw_announce, "should have locked on to the 250ms beacon"


def test_no_allocation_of_the_progress_string_per_frame():
    # The header of the app forbids allocating in the per-frame path; this text
    # only changes once a block.
    leader, follower = paired(3)
    leader.start_data(0)
    before = leader.progress
    for i in range(10):
        leader.step_data(i * FRAME_MS, 0)
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
# A transfer starts on the fastest PROVEN rung and steps down when the panel
# turns out to be slower than the conservative figure. There used to be one
# optimistic rung above proven ground, and these tests covered both outcomes of
# that gamble; it was removed because its demotion is announced by a pattern the
# peer has to read at the very rate that is too fast to read, so a failed gamble
# could leave the two badges a rung apart instead of stepping down together.
#
# Demotion itself still matters and is still tested, at a settle time where it
# is genuinely required: SETTLE_FOR_DEMOTION below.

import math

FRAME_90FPS = 11.1


class LaggingPanel:
    """Luminance chases the commanded field, as a real LCD does.

    tau is set so a full swing reaches ~90% in `settle_ms`. This is what the
    instant-field model in the rest of this file leaves out, and it is the
    difference between a rung looking fine and being unusable.
    """

    def __init__(self, settle_ms):
        self.tau = settle_ms / 2.3
        self.level = 0.0

    def sample(self, field, dt, rng, noise=80):
        target = BRIGHTNESS[field]
        self.level += (target - self.level) * (1 - math.exp(-dt / self.tau))
        return int(AMBIENT + self.level * CONTRAST + rng.uniform(-noise, noise))


def link_through_panels(leader, follower, settle_ms, seconds, seed=1):
    rng = random.Random(seed)
    lp, fp = LaggingPanel(settle_ms), LaggingPanel(settle_ms)
    now = 0.0
    lf = ff = " "
    while now < seconds * 1000:
        seen_by_leader = fp.sample(ff, FRAME_90FPS, rng)
        seen_by_follower = lp.sample(lf, FRAME_90FPS, rng)
        lf = leader.step(int(now), seen_by_leader)
        ff = follower.step(int(now), seen_by_follower)
        if leader.result and follower.result:
            break
        now += FRAME_90FPS
    return now / 1000


# The panel must be this slow before the starting rung fails and the Data Sync
# Error path has to step both badges down. It is above the 16ms LCD_SETTLE_MS
# the ladder is built around, which is the point: with the gamble rung gone, a
# 16ms panel no longer needs to demote at all, so asserting a demotion there
# would assert nothing. Measured across 8 seeds, 32ms demotes to 65ms every
# time and completes every time; 24ms never needs to.
SETTLE_FOR_DEMOTION = 32
# The rung the demotion tests start on. Not the one the badge picks: that is
# 80 ms now, three of the panel's measured 24 ms settles, and from 80 no panel
# in this harness both fails and recovers -- 56 ms needs no demotion and 60 or
# slower cannot read the Data Sync Error announcing one, exactly as the
# protocol before this change did from 80 ms. The mechanism is the same on
# every rung, so it is tested where it has room: 50 ms, against a 32 ms panel.
DEMOTION_START_MS = 50


def started_pair(payload=(b"HomeNet", b"hunter2!!"), start_ms=None):
    """Two badges starting a real transfer on the rung the badge would pick,
    or on `start_ms`."""
    leader = Transfer(True, payload, frame_ms=lambda: 11)
    follower = Transfer(False, frame_ms=lambda: 11)
    for side in (leader, follower):
        side.levels = levels()
        side.now = 0
        index = side.optimistic_index() if start_ms is None \
            else protocol.SLOT_LADDER.index(start_ms)
        side.start_data(0, index)
    return leader, follower


def test_transfer_starts_on_proven_ground_and_stays_there():
    # A panel at or better than the conservative figure never has to demote.
    leader, follower = started_pair()
    assert protocol.SLOT_LADDER[leader.clock.index] == 80
    link_through_panels(leader, follower, settle_ms=24, seconds=120)
    assert leader.result == protocol.OK, leader.result
    assert parse_payload(follower.received) == [b"HomeNet", b"hunter2!!"]
    assert protocol.SLOT_LADDER[leader.clock.index] == 80, "nothing should have demoted"


def test_slow_panel_demotes_and_still_completes():
    # The starting rung fails, the Data Sync Error path steps both badges down,
    # and the credential still arrives. Every seed, so that a demotion which
    # only works on the lucky one cannot pass.
    for seed in range(1, 9):
        leader, follower = started_pair(start_ms=DEMOTION_START_MS)
        link_through_panels(leader, follower, settle_ms=SETTLE_FOR_DEMOTION,
                            seconds=200, seed=seed)
        assert leader.result == protocol.OK, "seed %d: %s" % (seed, leader.result)
        assert parse_payload(follower.received) == [b"HomeNet", b"hunter2!!"]
        assert protocol.SLOT_LADDER[leader.clock.index] == 65, \
            "seed %d: should have demoted, on %dms" % (
                seed, protocol.SLOT_LADDER[leader.clock.index])


def test_both_badges_demote_together():
    # A rate change only ever alters slot duration, never the shared epoch (I3).
    # A Leader on 65ms against a Follower on 50ms has happened, and neither can
    # hear the other.
    for seed in range(1, 9):
        leader, follower = started_pair(start_ms=DEMOTION_START_MS)
        link_through_panels(leader, follower, settle_ms=SETTLE_FOR_DEMOTION,
                            seconds=200, seed=seed)
        assert leader.clock.index == follower.clock.index, \
            "seed %d: leader on %dms, follower on %dms" % (
                seed, protocol.SLOT_LADDER[leader.clock.index],
                protocol.SLOT_LADDER[follower.clock.index])


def test_demotion_does_not_spend_the_crc_retry_budget():
    leader, follower = started_pair(start_ms=DEMOTION_START_MS)
    link_through_panels(leader, follower, settle_ms=SETTLE_FOR_DEMOTION, seconds=200)
    assert leader.result == protocol.OK
    assert leader.retries == 0, "a rate demotion must not leave retries spent"


def test_worst_case_credential_survives_a_demotion():
    # 32 and 63 byte fields, which cross a block boundary: the multi-block case
    # and the demotion case at once.
    payload = (b"A" * 32, b"k" * 63)
    leader, follower = started_pair(payload, start_ms=DEMOTION_START_MS)
    link_through_panels(leader, follower, settle_ms=SETTLE_FOR_DEMOTION, seconds=300)
    assert leader.result == protocol.OK, leader.result
    assert parse_payload(follower.received) == list(payload)


def test_a_demoted_resend_does_not_need_separator_state():
    # This used to matter: the receiver tracked "have I seen the separator" to
    # reject a second one, and a discarded block had to rewind that flag or the
    # resend failed forever. Allowing any number of fields removed the flag and
    # the whole failure mode with it.
    follower = Transfer(False, frame_ms=lambda: 11)
    follower.levels = levels()
    follower.now = 0
    follower.start_data(0, follower.conservative_index())
    assert not hasattr(follower, "seen_separator")
    follower.reset_block()
    assert follower.tokens == [] and not follower.in_block
