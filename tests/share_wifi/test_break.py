"""The LIT class, the staircase, and breaking in.

See "Reading" and "Breaking in" in the protocol. The unit tests drive
Transfers directly, one reading at a time through step(); the end-to-end ones
run the simulator.
"""

import simulator as sim
from harness import load_module

P = load_module("share_wifi", "protocol")


def continuous(leader, scheme=P.SCHEME_BACKLIGHT, payload=(b"net", b"key1")):
    t = P.Transfer(leader, payload if leader else None)
    t.scheme = scheme
    return t


def levels():
    out = P.Levels()
    out.fit(32, 64, 112)
    return out


# --- the LIT class ------------------------------------------------------------

def test_a_badge_is_lit_on_four_adc_steps_of_black():
    t = continuous(True)
    assert not t.is_lit(48) and t.is_lit(64) and t.is_lit(96)
    assert not t.is_lit(None)


def test_lit_sends_two_repetitions_and_dark_three():
    t = continuous(True)
    t.lit = True
    assert t.cal_reps() == P.CAL_REPS_LIT == 2
    t.lit = False
    assert t.cal_reps() == P.CAL_REPS == 3
    # The Follower's staircase carries the pair's light, not its own.
    f = continuous(False)
    f.lit, f.pair_lit = True, False
    assert f.cal_reps() == P.CAL_REPS


def test_the_pair_starts_on_its_tier_and_light():
    t = continuous(True)
    for exact, lit, rung in ((True, True, 8), (True, False, 16),
                             (False, True, 16), (False, False, 32)):
        t.pair_exact, t.pair_lit = exact, lit
        assert P.SLOT_LADDER[t.start_index()] == rung, (exact, lit)


def test_a_lit_staircase_reads_back_as_lit_and_a_dark_one_does_not():
    for reps, lit in ((P.CAL_REPS_LIT, True), (P.CAL_REPS, False)):
        values = []
        for t in range(0, P.staircase_ms(reps), P.CAL_SAMPLE_MS):
            symbol = P.staircase_field(t, P.ASCENDING, reps)
            values.append({P.LOW: 32, P.MID: 64, P.HIGH: 112}[symbol])
        highs = P.staircase_steps(values, levels())[2]
        assert (highs <= P.LIT_STEPS_MAX) == lit, reps


def test_staircases_step_in_40ms():
    assert P.PLATEAU_MS == 40
    # The Leader's: its long opening gap, 3 x 3 x 40, the closing gap.
    assert P.staircase_ms(P.CAL_REPS, P.CAL_OPEN_LEADER_MS) == 1010
    assert P.staircase_ms(P.CAL_REPS_LIT, P.CAL_OPEN_MS) == 740


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
    duration = t.clock.duration // 1000
    feed(t, 1, 6 * duration, 32)            # the Follower dark: sending on
    assert t.tx and t.sub == t.STEP_BLOCK
    feed(t, 6 * duration, 6 * duration + duration, 112)   # one slot: not yet
    assert t.tx, "stopped on a single slot of high"
    field = None
    for now in range(7 * duration, 10 * duration):
        field = t.step(now, 112)
        if t.breaks:
            break
    # Stopped, listening for what the Follower sends next.
    assert not t.tx and t.sub == t.STEP_CRC and not t.crc_started
    assert field == P.LOW and t.breaks == 1


def test_a_leader_takes_no_break_from_noise_or_a_middle_level():
    t = sending_leader()
    duration = t.clock.duration // 1000
    feed(t, 1, 20 * duration, 64)           # the Follower at its middle
    assert t.sub == t.STEP_BLOCK and t.breaks == 0


def test_a_follower_that_loses_framing_breaks_and_then_asks_slower():
    t = continuous(False)
    t.levels = levels()
    t.now = 0
    t.start_data(0, P.BACKLIGHT_INDEX)
    duration = t.clock.duration // 1000
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
    duration = t.clock.duration // 1000
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


# --- end to end -------------------------------------------------------------

def run(**kw):
    both = dict(sim.SCENARIOS["realistic"])
    both.update(sim.LOOP_V2)
    both.update(kw)
    link = sim.build_loop(P, sim.TYPICAL, **both)
    link.run(300)
    return link


def test_a_lit_room_runs_on_8ms_and_a_dark_one_on_16():
    for room, rung in ((48, 8), (0, 16)):
        for seed in (1, 2):
            link = run(seed=seed, contrast=sim.SWING_MIRROR, room_light=room,
                       **sim.LOOP_SENSOR_V2)
            leader = link.leader.transfer
            assert leader.result == P.OK, (room, seed, leader.result)
            assert P.SLOT_LADDER[leader.clock.index] == rung, \
                (room, seed, P.SLOT_LADDER[leader.clock.index])
            assert leader.lit == link.follower.transfer.lit == (room > 0)


def test_a_break_rescues_a_block_lost_part_way():
    # A Follower whose readings go wrong part way through a block -- here
    # one slot read black part way through a byte, as a hand passing over
    # the sensor would -- loses framing, breaks in, and the Leader stops the block there rather
    # than send the rest: both demote and the block is sent again. The
    # stress rows that once broke in on their own (POLLED, reads every 2 ms,
    # three times the work) no longer lose framing in simulation since
    # POLLED windows merge their parts, so the loss is put there by hand.
    delivered = 0
    for seed in (1, 2, 3):
        link = sim.build_loop(P, sim.WORST, seed=seed,
                              contrast=sim.SWING_MIRROR,
                              **dict(sim.SCENARIOS["realistic"],
                                     **dict(sim.LOOP_V2,
                                            **sim.LOOP_SENSOR_V2)))
        follower = link.follower.transfer
        real = follower.window_value
        decided = [0]

        def window_value(k, real=real, follower=follower, decided=decided):
            value = real(k)
            if (follower.sub == follower.STEP_BLOCK and follower.in_block
                    and len(follower.bits) >= 3):
                decided[0] += 1
                if decided[0] == 60:
                    return follower.levels.black     # black mid-byte
            return value
        follower.window_value = window_value
        link.run(300)
        assert follower.breaks >= 1, seed
        assert link.leader.transfer.breaks >= 1, seed
        delivered += link.leader.transfer.result == P.OK
    assert delivered == 3, delivered


# --- mains flicker ------------------------------------------------------------

import math


def flickering(t_us, mean=88, size=22, hz=120):
    return mean + size * math.sin(2 * math.pi * hz * t_us / 1e6)


def test_the_filter_learns_flicker_from_dark_slots_and_takes_it_off():
    f = P.FlickerFilter()
    t = 0
    for _ in range(160):                    # 160 dark slots of 16 ms: 1.3 s
        for _ in range(8):                  # their back halves, a ms at a time
            t += 1000
            f.collect(t, flickering(t), 1000)
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
        for _ in range(8):
            t += 1000
            f.collect(t, flickering(t), 1000)
        f.close(False)
    assert f.size() == 0.0


def test_small_flicker_is_left_alone():
    f = P.FlickerFilter()
    f.amp[2 * P.FLICKER_HZ.index(120)] = 3.0   # 3 counts: under the floor
    f.refresh()
    assert f.correct(12345, 100) == 100


def test_the_data_phase_corrects_each_part_of_a_window():
    # Once a part, at its centre: the window's parts are what a slot is
    # decided from.
    t = continuous(False)
    t.levels = levels()
    t.start_data(0, P.BACKLIGHT_INDEX)
    calls = []
    t.flicker.correct = \
        lambda now_us, value, span_us=0: calls.append((now_us, span_us)) \
        or value
    for ms in range(0, 40, 2):
        t.step(ms, 32)
    # At the readings' own mean time, inside the window, in order. A
    # reading every 2 ms leaves one in a part, so a polled window merges
    # its parts to POLL_PART_READINGS each (merge_parts): fewer, longer.
    span = (t.w_close - t.w_open) // t.w_parts
    n = t.parts_now
    assert 0 < n < t.w_parts, (n, t.w_parts)
    first = [now for now, _ in calls[:n]]
    assert first == sorted(first), first
    for now in first:
        assert t.w_open <= now < t.w_close, first
    # Each merged part is corrected over the span it covers: whole parts.
    assert all(s % span == 0 and s >= span for _, s in calls[:n]), calls[:n]
    assert sum(s for _, s in calls[:n]) <= span * t.w_parts


def test_a_lit_room_with_47_percent_flicker_runs_on_8ms():
    # EXACT badges correct every part of a window from the fitted flicker:
    # every seed delivers, on the 8 ms rung or one down.
    delivered = twenty = 0
    for seed in range(1, 9):
        link = run(seed=seed, contrast=sim.SWING_MIRROR, room_light=48,
                   flicker=0.47, **sim.LOOP_SENSOR_V2)
        leader = link.leader.transfer
        delivered += leader.result == P.OK
        rung = P.SLOT_LADDER[leader.clock.index]
        assert rung <= 10, (seed, rung)
        twenty += rung <= 8
        # The flicker is 22 counts; learnt within a few.
        assert 15 < leader.flicker.size() < 26, leader.flicker.size()
    assert delivered == 8 and twenty >= 6, (delivered, twenty)


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
        detector.push(t, 280 if (t // P.PRESENT_HALF_MS) % 2 else 200)
    assert detector.matches(P.PRESENT_HALF_MS)
