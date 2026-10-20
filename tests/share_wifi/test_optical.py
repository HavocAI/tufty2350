"""The optical layer: beacon detection, calibration, the slot ladder.

Calibration is driven with a synthetic sensor rather than real hardware, so
these tests prove the maths is sound, not that the constants are right.
Whether the separability check (MIN_GAP_EXACT, GAP_SIGMAS) refuses the right
links on a badge still has to be measured there.
"""

import itertools
import math
import random

from harness import load_module
from held import HeldTransfer

protocol = load_module("share_wifi", "protocol")
StaircaseReader = protocol.StaircaseReader
BeaconDetector = protocol.BeaconDetector
SlotClock = protocol.SlotClock
Levels = protocol.Levels
SLOT_LADDER = protocol.SLOT_LADDER
MIN_SWING = protocol.MIN_SWING

# The staircase these read: a dark Follower's, the only one there is.
REPS = protocol.CAL_REPS_FAST
OPEN_MS = protocol.CAL_OPEN_FAST_MS
PLATEAU_MS = protocol.PLATEAU_FAST_MS
STAIRCASE_MS = protocol.staircase_ms(REPS, OPEN_MS, PLATEAU_MS,
                                     protocol.CAL_GAP_FAST_MS)


def staircase_field(t):
    return protocol.staircase_field(t, protocol.STAIRCASE, REPS, OPEN_MS,
                                    PLATEAU_MS)


def emit(level, ambient, contrast, noise, rng, middle=0.45):
    """One sensor reading of a peer displaying `level`."""
    base = {" ": 0.0, "_": middle, "-": 1.0}[level]
    return int(ambient + base * contrast + rng.uniform(-noise, noise))


# A staircase is read in calibration samples of FINE_MS:
# `frame_ms` below is that, unless a test says otherwise.
SAMPLE_MS = protocol.FINE_MS


LEAD_MS = 300       # the Follower dark before its staircase


def run_staircase(ambient=200, contrast=9000, noise=120, frame_ms=SAMPLE_MS,
                  start_offset=-LEAD_MS, seed=1, middle=0.45, settle_ms=0):
    """A staircase read as the Leader reads it -- over LEADER_CAL_LIMIT
    samples, its gap found on a moving mean -- through a sensor that takes
    `settle_ms` to settle, from `start_offset` ms (the Leader reads from
    long before; a start inside the opening is the stress).

    `middle` is the middle level's share of the swing. A settling sensor
    leaves samples part way through each transition, which is what a middle
    level near an end can be mistaken for.
    """
    rng = random.Random(seed)
    reader = StaircaseReader(protocol.LEADER_CAL_LIMIT,
                             protocol.LEADER_CAL_SMOOTH)
    tau = settle_ms / 2.3
    shown = 0.0
    t = -LEAD_MS
    while t < STAIRCASE_MS + 2000:
        field = staircase_field(t) if 0 <= t < STAIRCASE_MS else " "
        target = {" ": 0.0, "_": middle, "-": 1.0}[field]
        shown = target if not tau else \
            shown + (target - shown) * (1 - math.exp(-frame_ms / tau))
        value = int(ambient + shown * contrast + rng.uniform(-noise, noise))
        if t >= start_offset and reader.feed(t, value):
            return reader
        t += frame_ms
    return None


# Late starts inside the opening gap, the stress: the Leader reads from
# long before the staircase.
CONDITIONS = {
    "ideal": {},
    "started 20ms late": {"start_offset": 20},
    "started 36ms late": {"start_offset": 36},
    "bright ambient": {"ambient": 30000},
    "noisy": {"noise": 900},
    "1ms samples": {"frame_ms": 1},
}


def test_calibration_recovers_levels():
    # Listening late is the case that matters: the measuring badge cannot know
    # when the staircase started, which is why levels come from the sample
    # distribution rather than from plateau timing.
    for name, kw in CONDITIONS.items():
        reader = run_staircase(**kw)
        assert reader is not None, "%s: never detected the closing gap" % name
        levels = reader.levels()
        assert levels is not None, "%s: rejected a good staircase" % name

        contrast = kw.get("contrast", 9000)
        ambient = kw.get("ambient", 200)
        for label, got, want in (("black", levels.black, ambient),
                                 ("grey", levels.grey, ambient + 0.45 * contrast),
                                 ("white", levels.white, ambient + contrast)):
            assert abs(got - want) < contrast * 0.2, \
                "%s: %s %d, expected near %d" % (name, label, got, want)


def test_calibration_rejects_low_contrast():
    reader = run_staircase(contrast=MIN_SWING // 2)
    assert reader is None or reader.levels() is None


def test_calibration_rejects_two_level_signal():
    # A peer stuck between black and white, with no middle, must not be read as
    # three usable levels.
    rng = random.Random(3)
    reader = StaircaseReader()
    for i in range(400):
        t = i * 16
        level = "-" if (t // 200) % 2 else " "
        reader.feed(t, emit(level, 200, 9000, 100, rng))
    assert reader.levels() is None


# The quarter rule: each level at least a quarter of the swing from the next.
# It used to be checked against a middle level taken only from the middle half
# of the swing, which passes it by construction -- so a middle level near an
# end, at 7%, was accepted whenever transitions left a few samples there.
# These are the cases that used to get through.
QUARTER_RULE_CONDITIONS = list(itertools.product(
    (0, 120, 400),          # noise
    (0, 4, 8),              # settle_ms: the sensor's, up to the dark's
    (1, 2),                 # frame_ms: the calibration sample
    (-LEAD_MS, 30)))        # start_offset


def test_a_middle_level_near_either_end_is_rejected():
    for middle in (0.07, 0.93):
        for noise, settle, frame, start in QUARTER_RULE_CONDITIONS:
            reader = run_staircase(noise=noise, settle_ms=settle,
                                   frame_ms=frame, start_offset=start,
                                   middle=middle)
            levels = reader and reader.levels()
            assert levels is None, \
                "middle at %d%% accepted (noise %d, settle %dms, %dms frames, " \
                "%dms late): grey read at %d%%" % (
                    middle * 100, noise, settle, frame, start,
                    100 * (levels.grey - levels.black) // (levels.white - levels.black))


def test_a_middle_level_well_inside_the_swing_is_accepted():
    # 30-70% covers the backlight's middle, about 50%, with room either
    # side.
    conditions = [dict(kw) for kw in CONDITIONS.values()]
    # A sensor settling in up to 4 ms, the dark's worst fall [measured-all,
    # M4]: a 20 ms step holds too few settled samples of a 30% middle past
    # that.
    conditions += [{"settle_ms": settle} for settle in (2, 4)]
    for middle in (0.30, 0.45, 0.50, 0.70):
        for kw in conditions:
            reader = run_staircase(middle=middle, **kw)
            assert reader is not None, "%r: never detected the closing gap" % kw
            levels = reader.levels()
            assert levels is not None, \
                "middle at %d%% rejected: %r" % (middle * 100, kw)
            contrast = 9000
            want = kw.get("ambient", 200) + middle * contrast
            assert abs(levels.grey - want) < contrast * 0.1, \
                "middle at %d%%, %r: grey %d, expected near %d" % (
                    middle * 100, kw, levels.grey, want)


def test_thresholds_classify_the_three_levels():
    levels = Levels()
    levels.fit(200, 4250, 9200)
    assert levels.classify(200) == " "
    assert levels.classify(4250) == "_"
    assert levels.classify(9200) == "-"


def test_hysteresis_holds_the_previous_level():
    levels = Levels()
    levels.fit(0, 5000, 10000)
    levels.classify(10000)                       # settle on high
    assert levels.classify(levels.t_high) == "-", "should hold, not flip"
    levels.classify(0)                           # settle on low
    assert levels.classify(levels.t_low) == " ", "should hold the other way"
    levels.classify(5000)                        # settle on middle
    assert levels.classify(levels.t_low) == "_"
    assert levels.classify(levels.t_high) == "_"


def test_hysteresis_holds_a_level_only_against_its_own_threshold():
    """A value at T_high is not low, whatever came before: held low, a
    slow sensor's first high lost a fast block's ACK [simulation, k3.0]."""
    levels = Levels()
    levels.fit(0, 5000, 10000)
    levels.classify(0)                           # settle on low
    assert levels.classify(levels.t_high) == "-"
    levels.classify(10000)                       # settle on high
    assert levels.classify(levels.t_low) == "_"


ANNOUNCE = protocol.ANNOUNCE_FAST_HALF_MS
OTHER = 40          # the Announce's half before 2026-10-11, now no beacon's


def test_beacons_are_told_apart_by_period():
    # Not by brightness: before calibration there are no thresholds to compare
    # a brightness against.
    for half_ms, announce, other in ((ANNOUNCE, True, False),
                                     (OTHER, False, True)):
        rng = random.Random(2)
        detector = BeaconDetector()
        for i in range(120):
            t = i * protocol.FINE_MS
            level = "-" if (t // half_ms) % 2 else " "
            detector.push(t, emit(level, 200, 9000, 120, rng))
        window = protocol.BEACON_FAST_HALVES * half_ms
        assert abs(detector.half_period(window, half_ms) - half_ms) \
            < half_ms * 0.2
        assert detector.matches(ANNOUNCE) is announce
        assert detector.matches(OTHER) is other


def test_steady_light_is_not_a_beacon():
    detector = BeaconDetector()
    for i in range(200):
        detector.push(i * protocol.FINE_MS, 5000)
    assert detector.half_period(1000, ANNOUNCE) is None
    assert not detector.matches(ANNOUNCE) and not detector.matches(OTHER)


def test_dim_flicker_is_not_a_beacon():
    # A swing smaller than MIN_SWING is noise or someone walking past.
    rng = random.Random(4)
    detector = BeaconDetector()
    for i in range(200):
        t = i * protocol.FINE_MS
        level = "-" if (t // ANNOUNCE) % 2 else " "
        detector.push(t, emit(level, 200, MIN_SWING // 3, 20, rng))
    assert detector.half_period(1000, ANNOUNCE) is None


def test_slot_ladder_slows_and_floors():
    clock = SlotClock()
    clock.start(0, len(SLOT_LADDER) - 1)
    assert clock.duration == SLOT_LADDER[-1] * 1000
    clock.slow_down()
    assert clock.duration == SLOT_LADDER[-2] * 1000, \
        "index 0 is the slowest rung"
    for _ in range(20):
        clock.slow_down()
    assert clock.index == 0
    assert clock.duration == SLOT_LADDER[0] * 1000


def test_slot_numbering_advances_with_time():
    clock = SlotClock()
    clock.start(1000, 4)
    duration = SLOT_LADDER[4] * 1000
    assert clock.number(1000) == 0
    assert clock.number(1000 + duration - 1) == 0
    assert clock.number(1000 + duration) == 1


def test_a_pair_starts_on_its_lights_rung():
    # No rung above the start is gambled on: its demotion is announced by a
    # pattern the peer must read at the rate being demoted FROM, so a failed
    # gamble could split the two badges onto different rungs.
    assert not hasattr(protocol, "OPTIMISTIC_RUNGS")
    transfer = HeldTransfer(True, (b"n", b"password1"))
    # With no flags read yet: a dark pair.
    assert transfer.start_index() == protocol.EXACT_INDEX
    assert protocol.SLOT_LADDER[transfer.start_index()] == 16


def test_every_rung_an_exact_pair_starts_on_has_a_window():
    for settle, guard in ((protocol.SETTLE_LIT_US, protocol.GUARD_LIT_US),
                          (protocol.SETTLE_DARK_US, protocol.GUARD_DARK_US)):
        index = protocol.exact_index(settle, guard)
        d = protocol.SLOT_LADDER[index] * 1000
        opens, closes = protocol.exact_window(d, settle, guard)
        assert closes - opens >= protocol.WINDOW_MIN_US
        assert opens >= settle and d - closes >= guard
        if index + 1 < len(protocol.SLOT_LADDER):
            faster = protocol.SLOT_LADDER[index + 1] * 1000
            o, c = protocol.exact_window(faster, settle, guard)
            assert c - o < protocol.WINDOW_MIN_US


def test_the_exact_starts_follow_the_measured_settles():
    # Lit: 2.5 ms settle and a 1 ms guard allow 6 ms, which is not on the
    # ladder until the hardware says so; the dark's 10 and 2 allow 16.
    assert protocol.SLOT_LADDER[-1] == 8
    assert protocol.SLOT_LADDER[protocol.EXACT_LIT_INDEX] == 8
    assert protocol.SLOT_LADDER[protocol.EXACT_INDEX] == 16


def test_start_index_cannot_run_off_the_ladder():
    transfer = HeldTransfer(True, (b"n", b"password1"))
    transfer.pair_lit = True
    assert transfer.start_index() <= len(protocol.SLOT_LADDER) - 1


def test_demoting_from_the_starting_rung_steps_one_rung_slower():
    # Demotion is the only rate change there is, and it must move exactly
    # one rung: a badge that skipped one would land somewhere its peer is not.
    transfer = HeldTransfer(True, (b"n", b"password1"))
    transfer.clock.set_index(transfer.start_index())
    assert protocol.SLOT_LADDER[transfer.clock.index] == 16
    transfer.clock.slow_down()
    assert transfer.clock.index == transfer.start_index() - 1
    assert protocol.SLOT_LADDER[transfer.clock.index] == 20


def test_slow_down_stops_at_the_slowest_rung():
    clock = protocol.SlotClock()
    clock.start(0, 0)
    for _ in range(5):
        clock.slow_down()
    assert clock.index == 0
    # A transfer's clock counts microseconds.
    assert clock.duration == protocol.SLOT_LADDER[0] * 1000


# --- separability: what the badge's sensor actually gives --------------------
# A swing of five ADC steps, Gaussian noise of a few counts, every reading
# floored to 16: what three mirror runs measured. The old fixed floor, six
# times a peak-to-peak noise of 32 (192 counts), refused all of it.

def badge_staircase(swing=80, sd=4.0, black=40, seed=1, frame_ms=SAMPLE_MS,
                    middle=0.45):
    """A staircase read through the badge's sensor model, or None."""
    rng = random.Random(seed)
    reader = StaircaseReader()
    t = 0
    while t < STAIRCASE_MS + 2000:
        field = staircase_field(t) if t < STAIRCASE_MS else " "
        share = {" ": 0.0, "_": middle, "-": 1.0}[field]
        value = int(black + share * swing + rng.gauss(0, sd)) // 16 * 16
        if reader.feed(t, max(0, value)):
            return reader
        t += frame_ms
    return None


def test_a_five_step_swing_with_the_measured_noise_calibrates():
    for seed in range(1, 6):
        reader = badge_staircase(swing=80, sd=4.0, seed=seed)
        assert reader is not None, seed
        levels = reader.levels()
        assert levels is not None, "refused a link the badge measures (%d)" % seed
        assert levels.white - levels.black < 192, "not the old floor's regime"
        for noise in (levels.noise_black, levels.noise_grey,
                      levels.noise_white):
            assert 0.0 <= noise < 16, noise


def test_a_swing_drowned_in_noise_is_refused():
    # The same swing with three times the noise: the gaps are under four
    # standard deviations, so thresholds two from each level would not hold.
    for seed in range(1, 6):
        reader = badge_staircase(swing=80, sd=14.0, seed=seed)
        assert reader is None or reader.levels() is None, seed


def test_the_gap_check_is_min_gap_and_gap_sigmas_of_the_noisier_side():
    gap_separable = protocol.gap_separable
    k, floor = protocol.GAP_SIGMAS, protocol.QUANTISATION_SD
    assert k == 4 and protocol.MIN_GAP_EXACT == 16
    assert gap_separable(40, 2.0, 9.0) == (40 >= k * 9.0)
    assert not gap_separable(40, 2.0, 11.0)             # the noisier side
    assert not gap_separable(30, 0.0, 0.0, 32)          # under a min_gap
    # Readings that all land on one code still carry quantisation error.
    assert gap_separable(32, 0.0, 0.0) == (32 >= k * floor)
    assert abs(floor - 16 / math.sqrt(12)) < 1e-9


def test_held_spreads_leave_transitions_out():
    held_spreads = protocol.held_spreads
    # Three runs, black, middle, white (thresholds 56 and 104). Each opens
    # with two samples still arriving -- a transition passing into the class,
    # far from where the level settles -- then holds.
    black = [48, 40] + [32, 32, 48, 32, 32, 16, 32, 32, 32]
    grey = [60, 100] + [80, 80, 96, 80, 80, 64, 80, 80, 80]
    white = [108, 124] + [144, 144, 160, 144, 144, 128, 144, 144, 144]
    got = held_spreads(black + grey + white, 56, 104)
    assert got is not None

    def sd(values):
        mean = sum(values) / len(values)
        return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
    assert protocol.STEP_SAMPLES == 3
    for spread, level in zip(got, (black, grey, white)):
        assert abs(spread - sd(level[2:])) < 1e-9, (spread, level)
        # Counting the arriving samples would have widened every level.
        assert sd(level) > spread


def test_a_level_with_too_few_settled_samples_cannot_be_judged():
    values = [32] * 12 + [80] * 3 + [144] * 12
    assert protocol.held_spreads(values, 56, 104) is None


def test_one_step_of_gap_and_the_quantisation_floor_binds():
    # MIN_GAP_EXACT: staircase samples are means of a hundred ring readings,
    # so HW1's gaps of 27-30 counts (an obstructed sensor) separate; the
    # quantisation floor (4 x 4.62 = 18.5) is then what binds.
    gap_separable = protocol.gap_separable
    assert protocol.MIN_GAP_EXACT == protocol.SENSOR_STEP == 16
    assert gap_separable(28, 1.0, 1.5)
    assert not gap_separable(28, 1.0, 1.5, 2 * protocol.SENSOR_STEP)
    assert not gap_separable(18, 0.0, 0.0)              # under 4 x 4.62
    assert gap_separable(19, 0.0, 0.0)


def staircase_values(gap, sd, rng, reps=3, step=10):
    """A staircase as a ring reader's coarse samples: black, black + gap,
    black + 2 gap, each a mean -- no 16-count steps left in it."""
    values = []
    for _ in range(reps):
        for level in (0, 1, 2):
            values += [int(round(80 + level * gap + rng.gauss(0, sd)))
                       for _ in range(step)]
    return values


def test_a_staircase_of_hw1s_gaps_separates():
    rng = random.Random(5)
    reader = StaircaseReader()
    t = 0
    for v in [80] * 20 + staircase_values(28, 1.0, rng) + [80] * 70:
        reader.feed(t, v)
        t += 2
    assert reader.levels() is not None
