"""Continuous badges: the LIT class, the shorter staircase, and breaking in.

See "Reading continuously" and "Breaking in" in the protocol. The unit tests
drive Transfers directly; the end-to-end ones run the continuous simulator.
"""

import simulator as sim
from harness import load_module

P = load_module("share_wifi", "protocol")
BOTH = (P.SCHEME_FIELDS, P.SCHEME_BACKLIGHT)


def continuous(leader, scheme=P.SCHEME_BACKLIGHT, payload=(b"net", b"key1")):
    t = P.Transfer(leader, payload if leader else None, frame_ms=lambda: 17,
                   schemes=BOTH, continuous=True)
    t.scheme = scheme
    return t


def levels():
    out = P.Levels()
    out.fit(32, 64, 112)
    return out


# --- the LIT class ------------------------------------------------------------

def test_a_continuous_badge_is_lit_on_four_adc_steps_of_black():
    t = continuous(True)
    assert not t.is_lit(48) and t.is_lit(64) and t.is_lit(96)
    assert not t.is_lit(None)
    once = P.Transfer(True, (b"n", b"k"))
    assert not once.is_lit(200), "a once-a-frame badge is never LIT"


def test_lit_sends_two_repetitions_and_slow_still_five():
    t = continuous(True)
    t.slow, t.lit = False, True
    assert t.cal_reps() == P.CAL_REPS_LIT == 2
    t.lit = False
    assert t.cal_reps() == P.CAL_REPS == 3
    t.slow = True
    assert t.cal_reps() == P.CAL_REPS_SLOW


def test_two_lit_badges_on_the_backlight_start_on_16ms():
    t = continuous(True)
    t.slow = t.peer_slow = False
    for mine, theirs, rung in ((True, True, 16), (True, False, 32),
                               (False, True, 32), (False, False, 32)):
        t.lit, t.peer_lit = mine, theirs
        assert P.SLOT_LADDER[t.start_index()] == rung, (mine, theirs)
    # On pixels LIT means nothing: the panel, not the sensor, is the limit.
    t.scheme = P.SCHEME_FIELDS
    t.lit = t.peer_lit = True
    assert P.SLOT_LADDER[t.start_index()] == 80


def test_a_lit_staircase_reads_back_as_lit_and_a_fast_one_does_not():
    for reps, lit, slow in ((P.CAL_REPS_LIT, True, False),
                            (P.CAL_REPS, False, False),
                            (P.CAL_REPS_SLOW, False, True)):
        values = []
        for t in range(0, P.staircase_ms(reps, P.PLATEAU_CONTINUOUS_MS), 8):
            symbol = P.staircase_field(t, P.ASCENDING, reps,
                                       P.PLATEAU_CONTINUOUS_MS)
            values.append({P.LOW: 32, P.MID: 64, P.HIGH: 112}[symbol])
        assert P.staircase_lit(values, levels()) == lit, reps
        assert P.staircase_slow(values, levels()) == slow, reps


def test_a_continuous_staircase_steps_in_150ms_and_a_framed_one_in_200():
    assert continuous(True).plateau() == P.PLATEAU_CONTINUOUS_MS == 150
    assert P.Transfer(True, (b"n", b"k")).plateau() == P.PLATEAU_MS == 200
    # 300 + 3 x 3 x 150 + 300, against 2400 once a frame.
    assert P.staircase_ms(P.CAL_REPS, 150) == 1950
    assert P.staircase_ms(P.CAL_REPS_LIT, 150) == 1500


def test_the_leader_judges_its_light_from_the_followers_beacon():
    detector = P.BeaconDetector()
    for i in range(80):
        detector.push(i * 8, 80 if (i * 8 // 400) % 2 == 0 else 160)
    assert detector.low() == 80


# --- breaking in ---------------------------------------------------------------

def sending_leader():
    """A continuous Leader part way through sending its first block."""
    t = continuous(True)
    t.levels = levels()
    t.now = 0
    t.start_data(0, P.BACKLIGHT_INDEX)
    return t


def feed(t, start, end, value, step=1):
    """Step `t` with `value` every `step` ms from `start` to `end`."""
    field = None
    for now in range(start, end, step):
        field = t.step(now, value)
    return field


def test_a_leader_hears_two_slots_of_high_while_it_sends_and_stops():
    t = sending_leader()
    duration = t.clock.duration
    feed(t, 1, 6 * duration, 32)            # the Follower dark: sending on
    assert t.tx and t.sub == t.STEP_BLOCK
    feed(t, 6 * duration, 6 * duration + duration, 112)   # one slot: not yet
    assert t.tx, "stopped on a single slot of high"
    field = feed(t, 7 * duration, 9 * duration, 112)
    assert not t.tx and t.sub == t.STEP_CRC and not t.crc_started
    assert field == P.LOW and t.breaks == 1


def test_a_leader_takes_no_break_from_noise_or_a_middle_level():
    t = sending_leader()
    duration = t.clock.duration
    feed(t, 1, 20 * duration, 64)           # the Follower at its middle
    assert t.sub == t.STEP_BLOCK and t.breaks == 0


def test_a_follower_that_loses_framing_breaks_and_then_asks_slower():
    t = continuous(False)
    t.levels = levels()
    t.now = 0
    t.start_data(0, P.BACKLIGHT_INDEX)
    duration = t.clock.duration
    t.in_block = True
    t.frame_error()
    assert t.breaking and t.breaks == 1
    # High while the Leader is still lit...
    assert feed(t, 1, 3 * duration, 112) == P.HIGH
    assert t.breaking
    # ...and once it has gone dark for BREAK_ACK_SLOTS, the Sync Error.
    feed(t, 3 * duration, 6 * duration, 32)
    assert not t.breaking and t.pending_slow_down
    assert "".join(t.tx) == P.SYNC_ERROR


def test_an_unanswered_break_waits_the_block_out_instead():
    t = continuous(False)
    t.levels = levels()
    t.now = 0
    t.start_data(0, P.BACKLIGHT_INDEX)
    duration = t.clock.duration
    t.frame_error()
    feed(t, 1, (P.BREAK_MAX_SLOTS + 2) * duration, 112, step=2)
    assert not t.breaking and t.want_resend


def test_a_resend_repeats_the_last_verdict_first():
    t = sending_leader()
    t.last_verdict = P.CRC_GOOD
    t.send_block(P.SYNC_LEAD_IN)
    sent = "".join(t.tx).strip(" ")
    assert sent.startswith(P.CRC_GOOD.strip(" ")), sent
    assert P.BEGIN_BLOCK in sent
    # Not on a first sending, which has no verdict to repeat.
    t.send_block()
    assert "".join(t.tx).strip(" ").startswith(P.BEGIN_BLOCK.strip(" "))


def test_a_follower_holding_a_block_settles_it_from_a_repeated_verdict():
    for verdict, accepted in ((P.CRC_GOOD, True), (P.CRC_BAD, False)):
        t = continuous(False)
        t.sub = t.STEP_BLOCK
        t.pending_tokens = [1, 2, 3]
        t.pending_block = True
        for symbol in verdict:
            t.rx.append(symbol)
            t.collect_block()
        assert not t.pending_block and not t.pending_tokens
        assert (t.received == [1, 2, 3]) == accepted, verdict


def test_a_follower_holding_a_block_will_not_take_a_new_one():
    t = continuous(False)
    t.sub = t.STEP_BLOCK
    t.pending_tokens = [1, 2, 3]
    t.pending_block = True
    t.now = 0
    t.levels = levels()
    t.start_data(0, P.BACKLIGHT_INDEX)
    t.pending_tokens = [1, 2, 3]
    t.pending_block = True
    for symbol in P.BEGIN_BLOCK:
        t.rx.append(symbol)
        t.collect_block()
    assert not t.in_block and t.breaking, "a held block would be overwritten"
    assert t.pending_tokens == [1, 2, 3]


def test_a_follower_that_missed_its_verdict_breaks():
    t = continuous(False)
    t.sub = t.STEP_VERDICT
    t.now = 0
    t.levels = levels()
    t.start_data(0, P.BACKLIGHT_INDEX)
    t.sub = t.STEP_VERDICT
    t.verdict_lit = 0
    for _ in range(P.VERDICT_LOST_SYMBOLS + 1):
        t.rx.append(P.HIGH)
        t.follower_rx()
    assert t.breaking


def test_a_once_a_frame_follower_does_not_break():
    t = P.Transfer(False)
    t.levels = levels()
    t.now = 0
    t.start_data(0, P.FAST_INDEX)
    t.frame_error()
    assert not t.breaking and t.want_resend


# --- end to end -------------------------------------------------------------

def run(**kw):
    both = dict(sim.SCENARIOS["realistic"])
    both.update(sim.LOOP_V2)
    both.update(kw)
    link = sim.build_loop(P, sim.TYPICAL, schemes=BOTH, **both)
    link.run(300)
    return link


def test_a_lit_room_runs_on_16ms_and_a_dark_one_on_32():
    for room, rung in ((48, 16), (0, 32)):
        for seed in (1, 2):
            link = run(seed=seed, contrast=sim.SWING_MIRROR, room_light=room,
                       **sim.LOOP_SENSOR_V2)
            leader = link.leader.transfer
            assert leader.result == P.OK, (room, seed, leader.result)
            assert P.SLOT_LADDER[leader.clock.index] == rung, \
                (room, seed, P.SLOT_LADDER[leader.clock.index])
            assert leader.lit == link.follower.transfer.lit == (room > 0)


def test_breaks_rescue_a_link_reading_slowly():
    # Reading every 2 ms: framing fails now and then, and each failure is a
    # break of a few slots rather than the rest of a block.
    breaks = delivered = 0
    for seed in range(1, 7):
        link = run(seed=seed, contrast=sim.SWING_MIRROR, read_ms=2.0,
                   **sim.LOOP_SENSOR_V2)
        delivered += link.leader.transfer.result == P.OK
        breaks += link.follower.transfer.breaks
    assert delivered == 6, delivered
    assert breaks > 0, "nothing to rescue: the test is not testing breaks"


# --- mains flicker ------------------------------------------------------------

import math


def flickering(t_us, mean=88, size=22, hz=120):
    return mean + size * math.sin(2 * math.pi * hz * t_us / 1e6)


def test_the_filter_learns_flicker_from_dark_slots_and_takes_it_off():
    f = P.FlickerFilter()
    t = 0
    for _ in range(160):                    # 160 dark slots of 16 ms: 1.3 s
        for _ in range(20):                 # their back halves, every 0.4 ms
            t += 400
            f.collect(t, flickering(t))
        t += 8000
        f.close(True)
    # A running average over 250 ms of dark, by 8 ms a slot: 99% there.
    assert abs(f.size() - 22) < 2, f.size()
    errors = [abs(f.correct(t + k * 137, flickering(t + k * 137)) - 88)
              for k in range(200)]
    assert max(errors) <= 3, max(errors)


def test_the_filter_learns_nothing_from_lit_slots():
    f = P.FlickerFilter()
    t = 0
    for _ in range(40):
        for _ in range(20):
            t += 400
            f.collect(t, flickering(t))
        f.close(False)
    assert f.size() == 0.0


def test_small_flicker_is_left_alone():
    f = P.FlickerFilter()
    f.amp[2] = 3.0                          # 3 counts at 120 Hz: under the floor
    assert f.correct(12345, 100) == 100


def test_a_continuous_badge_filters_only_in_the_data_phase():
    t = continuous(False)
    t.levels = levels()
    calls = []
    t.flicker.correct = lambda now_us, value: calls.append(now_us) or value
    t.step(0, 32, 0)                        # discovery: not filtered
    assert not calls
    t.now = 0
    t.start_data(0, P.BACKLIGHT_INDEX)
    t.step(1, 32, 1000)
    assert calls == [1000]


def test_a_lit_room_with_47_percent_flicker_runs_on_16ms():
    delivered = sixteen = 0
    for seed in (1, 2, 3):
        link = run(seed=seed, contrast=sim.SWING_MIRROR, room_light=48,
                   flicker=0.47, **sim.LOOP_SENSOR_V2)
        leader = link.leader.transfer
        delivered += leader.result == P.OK
        sixteen += P.SLOT_LADDER[leader.clock.index] == 16
        assert leader.flicker.size() > 10, leader.flicker.size()
    assert delivered == 3 and sixteen >= 2, (delivered, sixteen)


# --- discovery and calibration --------------------------------------------------

def test_a_quiet_window_with_an_outlier_is_not_a_beacon():
    detector = P.BeaconDetector()
    import random
    rng = random.Random(1)
    for i in range(125):
        value = 200 + rng.randint(-20, 20)
        if i in (40, 85):
            value = 280                     # two outliers 360 ms apart
        detector.push(i * 8, value)
    assert detector.half_period() is None
    assert not detector.matches(P.PRESENT_HALF_MS)


def test_a_real_beacon_still_matches():
    detector = P.BeaconDetector()
    for i in range(125):
        t = i * 8
        detector.push(t, 280 if (t // 400) % 2 else 200)
    assert detector.matches(P.PRESENT_HALF_MS)


def test_a_follower_waits_out_the_second_staircase_whatever_its_reader_says():
    t = P.Transfer(False, schemes=BOTH)
    t.levels = levels()
    t.cal_index = 1
    t.peer_slow = False
    t.phase = P.PHASE_CAL_RECV
    t.phase_started = 0
    t.reader.feed = lambda now, light: True     # "ended" at once
    t.step_cal_recv(100, 32)
    assert t.phase == P.PHASE_CAL_RECV, "answered over the Leader"
    assert t.cal_index == 1
