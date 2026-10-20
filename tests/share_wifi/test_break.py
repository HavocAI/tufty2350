"""The LIT class, the staircase, the Leader's lead-in, and mains flicker.

See sections 6 and 14.1 of the protocol spec. The unit tests drive
Transfers directly, one reading at a time through step(); the end-to-end ones
run the simulator.
"""

import math
import simulator as sim
from harness import load_module
from held import HeldTransfer

P = load_module("share_wifi", "protocol")


def flicker_size(f):
    """The largest flicker amplitude a filter holds, in counts."""
    if f.harmonics is not None:
        return max([math.sqrt(a * a + b * b) for a, b in f.harmonics] + [0.0])
    return max(math.sqrt(f.amp[2 * i] ** 2 + f.amp[2 * i + 1] ** 2)
               for i in range(len(f.amp) // 2))

# The rung the unit tests run on: 32 ms, four reads a ms of step() each way.
RUNG = P.SLOT_LADDER.index(32)


def continuous(leader, scheme=P.SCHEME_BACKLIGHT, payload=(b"net", b"key1")):
    t = HeldTransfer(leader, payload if leader else None)
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


def test_lit_sends_one_repetition_and_dark_two():
    # The Follower's staircase -- the only one -- carries the pair's light.
    f = continuous(False)
    f.pair_lit = True
    assert f.cal_reps() == P.CAL_REPS_FAST_LIT == 1
    f.pair_lit = False
    assert f.cal_reps() == P.CAL_REPS_FAST == 2


def test_the_pair_starts_on_its_light():
    t = continuous(True)
    for lit, rung in ((True, 8), (False, 16)):
        t.pair_lit = lit
        assert P.SLOT_LADDER[t.start_index()] == rung, lit


def test_a_lit_staircase_reads_back_as_lit_and_a_dark_one_does_not():
    for reps, lit in ((P.CAL_REPS_FAST_LIT, True), (P.CAL_REPS_FAST, False)):
        values = []
        for t in range(0, P.staircase_ms(reps, P.CAL_OPEN_FAST_MS,
                                         P.PLATEAU_FAST_MS,
                                         P.CAL_CLOSE_FAST_MS),
                       P.FINE_MS):
            symbol = P.staircase_field(t, P.STAIRCASE, reps,
                                       P.CAL_OPEN_FAST_MS, P.PLATEAU_FAST_MS)
            values.append({P.LOW: 32, P.MID: 64, P.HIGH: 112}[symbol])
        highs = P.staircase_highs(values, levels())
        assert (highs <= P.LIT_STEPS_MAX_FAST) == lit, reps


def test_the_staircase_steps_in_20ms():
    assert P.PLATEAU_FAST_MS == 20
    # The Follower's: its opening, 1 or 2 x 3 x 20, and the shared gap.
    assert P.staircase_ms(P.CAL_REPS_FAST_LIT, P.CAL_OPEN_FAST_MS,
                          P.PLATEAU_FAST_MS, P.CAL_GAP_FAST_MS) == 210
    assert P.staircase_ms(P.CAL_REPS_FAST, P.CAL_OPEN_FAST_MS,
                          P.PLATEAU_FAST_MS, P.CAL_GAP_FAST_MS) == 270


def test_the_follower_judges_its_light_from_the_announce():
    detector = P.BeaconDetector()
    for i in range(80):
        detector.push(i * 8, 80 if (i * 8 // 400) % 2 == 0 else 160)
    assert detector.low() == 80 and detector.high() == 160


# --- the Leader's lead-in ---------------------------------------------------------

def sending_leader():
    """A continuous Leader part way through sending its first block."""
    t = continuous(True)
    t.levels = levels()
    t.now = 0
    t.start_data(0, RUNG)
    return t


def feed(t, start, end, value, step=1):
    """Step `t` with `value` every `step` ms from `start` to `end`."""
    field = None
    for now in range(start, end, step):
        field = t.step(now, value)
    return field


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
    assert abs(flicker_size(f) - 22) < 2, flicker_size(f)
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
    assert flicker_size(f) == 0.0


def test_small_flicker_is_left_alone():
    f = P.FlickerFilter()
    f.amp[2 * P.FLICKER_HZ.index(120)] = 3.0   # 3 counts: under the floor
    f.refresh()
    assert f.correct(12345, 100) == 100


def test_the_data_phase_corrects_each_part_of_a_window():
    # Once a part, at its centre: the window's parts are what a slot is
    # decided from, each the same span.
    t = continuous(False)
    t.levels = levels()
    t.start_data(0, RUNG)
    calls = []
    t.flicker.amounts = \
        lambda times, n, span_us, out: calls.append((list(times[:n]), span_us))
    for ms in range(0, 40, 2):
        t.step(ms, 32)
    span = (t.w_close - t.w_open) // t.w_parts
    assert calls, "no window decided"
    times, s = calls[0]
    assert s == span and len(times) == t.w_parts == t.parts_now
    assert times == sorted(times), times
    for now in times:
        assert t.w_open <= now < t.w_close, times


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
        assert 15 < flicker_size(leader.flicker) < 26, flicker_size(leader.flicker)
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
    for window in (None, 1000):
        assert not detector.matches(P.ANNOUNCE_FAST_HALF_MS, window), window


def test_a_real_beacon_still_matches():
    detector = P.BeaconDetector()
    for i in range(125):
        t = i * P.FINE_MS
        detector.push(t, 280 if (t // P.ANNOUNCE_FAST_HALF_MS) % 2 else 200)
    assert detector.matches(P.ANNOUNCE_FAST_HALF_MS)


def test_after_a_stall_the_lead_in_watch_reads_only_its_last_slots():
    # A collection of ten slots before the Leader's next pass: catching up
    # on every one, each window read and corrected, ran past the next
    # symbol's boundary. Only the last WATCH_SLOTS are read.
    t = sending_leader()
    duration = t.clock.duration // 1000
    feed(t, 1, 3 * duration, 32)
    assert t.tx and t.sub == t.STEP_BLOCK
    stall_end = 13 * duration
    for now in range(3 * duration, stall_end):
        t.reader.add(now * 1000, 32)
    read = []
    window = t.window_value

    def counted(k):
        read.append(k)
        return window(k)
    t.window_value = counted
    t.step(stall_end, 32)
    assert 0 < len(read) <= P.WATCH_SLOTS, read
