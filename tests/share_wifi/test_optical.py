"""The optical layer: beacon detection, calibration, the slot ladder.

Calibration is driven with a synthetic sensor rather than real hardware, so
these tests prove the maths is sound, not that the constants are right.
Whether the separability check (MIN_GAP, GAP_SIGMAS) refuses the right links
on a badge, and LCD_SETTLE_MS, still have to be measured there.
"""

import itertools
import math
import random

from harness import load_module

protocol = load_module("share_wifi", "protocol")
StaircaseReader = protocol.StaircaseReader
BeaconDetector = protocol.BeaconDetector
SlotClock = protocol.SlotClock
Levels = protocol.Levels
staircase_field = protocol.staircase_field
SLOT_LADDER = protocol.SLOT_LADDER
STAIRCASE_MS = protocol.STAIRCASE_MS
MIN_SWING = protocol.MIN_SWING


def emit(level, ambient, contrast, noise, rng, middle=0.45):
    """One sensor reading of a peer displaying `level`."""
    base = {" ": 0.0, "_": middle, "-": 1.0}[level]
    return int(ambient + base * contrast + rng.uniform(-noise, noise))


def run_staircase(ambient=200, contrast=9000, noise=120, frame_ms=16,
                  start_offset=0, seed=1, middle=0.45, settle_ms=0):
    """A staircase read through a panel that takes `settle_ms` to settle.

    `middle` is the middle level's share of the swing. A settling panel
    leaves samples part way through each transition, which is what a middle
    level near an end can be mistaken for.
    """
    rng = random.Random(seed)
    reader = StaircaseReader()
    tau = settle_ms / 2.3
    shown = 0.0
    t = 0
    while t < STAIRCASE_MS + 2000:
        field = staircase_field(t) if t < STAIRCASE_MS else " "
        target = {" ": 0.0, "_": middle, "-": 1.0}[field]
        shown = target if not tau else \
            shown + (target - shown) * (1 - math.exp(-frame_ms / tau))
        value = int(ambient + shown * contrast + rng.uniform(-noise, noise))
        if t >= start_offset and reader.feed(t, value):
            return reader
        t += frame_ms
    return None


CONDITIONS = {
    "ideal": {},
    "started 700ms late": {"start_offset": 700},
    "started 1500ms late": {"start_offset": 1500},
    "bright ambient": {"ambient": 30000},
    "noisy": {"noise": 900},
    "slow 33ms frames": {"frame_ms": 33},
    "fast 8ms frames": {"frame_ms": 8},
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
# end, such as color.grey at 7%, was accepted whenever the panel's transitions
# left a few samples there. These are the cases that used to get through.
QUARTER_RULE_CONDITIONS = list(itertools.product(
    (0, 120, 400),          # noise
    (0, 16, 24),            # settle_ms
    (8, 11, 16),            # frame_ms
    (0, 700)))              # start_offset


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
    # 30-70% covers both schemes with room either side: the fields scheme's
    # middle measured at 40% and is modelled at 45%, the backlight's sits at
    # about 50%.
    conditions = [dict(kw) for kw in CONDITIONS.values()]
    conditions += [{"settle_ms": settle, "frame_ms": frame}
                   for settle in (16, 24) for frame in (8, 11, 16)]
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
    on_threshold = levels.t_high
    assert levels.classify(on_threshold) == "-", "should hold, not flip"
    levels.classify(0)                           # settle on low
    assert levels.classify(on_threshold) == " ", "should hold the other way"


def test_beacons_are_told_apart_by_period():
    # Not by brightness: before calibration there are no thresholds to compare
    # a brightness against.
    for half_ms, announce, present in ((250, True, False), (400, False, True)):
        rng = random.Random(2)
        detector = BeaconDetector()
        for i in range(200):
            t = i * 16
            level = "-" if (t // half_ms) % 2 else "_"
            detector.push(t, emit(level, 200, 9000, 120, rng))
        assert abs(detector.half_period() - half_ms) < half_ms * 0.2
        assert detector.matches(250) is announce
        assert detector.matches(400) is present


def test_steady_light_is_not_a_beacon():
    detector = BeaconDetector()
    for i in range(200):
        detector.push(i * 16, 5000)
    assert detector.half_period() is None
    assert not detector.matches(250)


def test_dim_flicker_is_not_a_beacon():
    # A swing smaller than MIN_SWING is noise or someone walking past.
    rng = random.Random(4)
    detector = BeaconDetector()
    for i in range(200):
        t = i * 16
        level = "-" if (t // 250) % 2 else "_"
        detector.push(t, emit(level, 200, MIN_SWING // 3, 20, rng))
    assert detector.half_period() is None


def test_slot_ladder_slows_and_floors():
    clock = SlotClock()
    clock.start(0, len(SLOT_LADDER) - 1)
    assert clock.duration == SLOT_LADDER[-1]
    clock.slow_down()
    assert clock.duration == SLOT_LADDER[-2], "index 0 is the slowest rung"
    for _ in range(20):
        clock.slow_down()
    assert clock.index == 0
    assert clock.duration == SLOT_LADDER[0]


def test_slot_numbering_advances_with_time():
    clock = SlotClock()
    clock.start(1000, 4)
    duration = SLOT_LADDER[4]
    assert clock.number(1000) == 0
    assert clock.number(1000 + duration - 1) == 0
    assert clock.number(1000 + duration) == 1
    assert clock.offset(1000 + duration // 2) == duration // 2


def test_conservative_index_never_goes_below_the_settling_floor():
    # The floor formula used to be 2x the settle time, which permitted a 32ms
    # slot. Simulating the whole chain -- staircase through a panel lag, into
    # the real reader, into a real transfer -- nothing below ~37ms decodes at a
    # 16ms settle, and nothing below 45ms is reliable.
    floor = protocol.LCD_SETTLE_MS * protocol.SETTLE_SLOTS
    for frame in (11, 16, 20, 33):
        transfer = protocol.Transfer(True, (b"n", b"password1"),
                                     frame_ms=lambda f=frame: f)
        chosen = protocol.SLOT_LADDER[transfer.conservative_index()]
        assert chosen >= floor, \
            "at %dms frames it picks %dms, below the %dms floor" % (frame, chosen, floor)
        assert chosen >= frame * 2.5, \
            "at %dms frames it picks %dms, too few frames per slot" % (frame, chosen)


def test_conservative_rung_at_90fps_is_80ms():
    # Three of the panel's measured 24 ms settles: 72 ms, so the 80 ms rung,
    # whatever the frame rate. It was 50 against a 16 ms datasheet figure.
    transfer = protocol.Transfer(True, (b"n", b"password1"), frame_ms=lambda: 11)
    assert protocol.SLOT_LADDER[transfer.conservative_index()] == 80


def test_transfers_start_on_proven_ground():
    # There is no gamble rung on pixels. A transfer starts at the fastest rate
    # the measured panel allows, which is 80ms.
    transfer = protocol.Transfer(True, (b"n", b"password1"), frame_ms=lambda: 11)
    assert transfer.start_index() == transfer.conservative_index()
    assert protocol.SLOT_LADDER[transfer.start_index()] == 80


def test_there_is_no_optimistic_rung():
    # A rung above proven ground was tried and removed: its demotion is
    # announced by a pattern the peer must read at the rate being demoted FROM,
    # so a failed gamble could split the two badges onto different rungs rather
    # than stepping them down together. The ladder now ends at the conservative
    # floor, so there is nothing above it to gamble on.
    assert protocol.OPTIMISTIC_RUNGS == 0
    transfer = protocol.Transfer(True, (b"n", b"password1"), frame_ms=lambda: 11)
    assert transfer.start_index() == transfer.conservative_index() == \
        protocol.FAST_INDEX
    # The ladder runs on past pixels' floor only for a continuous badge on
    # the backlight, which starts there and counts it a gamble: its proven
    # floor is BACKLIGHT_PROVEN_MS, so a bad CRC steps it down.
    fast = protocol.Transfer(True, (b"n", b"password1"), frame_ms=lambda: 16,
                             continuous=True,
                             schemes=(protocol.SCHEME_FIELDS,
                                      protocol.SCHEME_BACKLIGHT))
    fast.scheme = protocol.SCHEME_BACKLIGHT
    assert fast.start_index() == protocol.BACKLIGHT_INDEX
    assert protocol.SLOT_LADDER[fast.start_index()] == 32
    assert protocol.SLOT_LADDER[fast.conservative_index()] == \
        protocol.BACKLIGHT_PROVEN_MS


def test_start_index_cannot_run_off_the_ladder():
    # A badge already at the fastest rung must not index past it.
    transfer = protocol.Transfer(True, (b"n", b"password1"), frame_ms=lambda: 1)
    assert transfer.start_index() <= len(protocol.SLOT_LADDER) - 1


def test_demoting_from_the_starting_rung_steps_one_rung_slower():
    # Demotion is the only rate change there is now, and it must move exactly
    # one rung: a badge that skipped one would land somewhere its peer is not.
    transfer = protocol.Transfer(True, (b"n", b"password1"), frame_ms=lambda: 11)
    transfer.clock.set_index(transfer.start_index())
    assert protocol.SLOT_LADDER[transfer.clock.index] == 80
    transfer.clock.slow_down()
    assert transfer.clock.index == transfer.start_index() - 1
    assert protocol.SLOT_LADDER[transfer.clock.index] == 100


def test_slow_down_stops_at_the_slowest_rung():
    clock = protocol.SlotClock()
    clock.start(0, 0)
    for _ in range(5):
        clock.slow_down()
    assert clock.index == 0
    assert clock.duration == protocol.SLOT_LADDER[0]


# --- separability: what the badge's sensor actually gives --------------------
# A swing of five ADC steps, Gaussian noise of a few counts, every reading
# floored to 16: what three mirror runs measured. The old fixed floor, six
# times a peak-to-peak noise of 32 (192 counts), refused all of it.

def badge_staircase(swing=80, sd=4.0, black=40, seed=1, frame_ms=16,
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
    assert k == 4 and protocol.MIN_GAP == 32
    assert gap_separable(40, 2.0, 9.0) == (40 >= k * 9.0)
    assert not gap_separable(40, 2.0, 11.0)             # the noisier side
    assert not gap_separable(24, 0.0, 0.0)              # under MIN_GAP
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


def test_the_quantisation_floor_binds_once_gap_sigmas_outgrows_min_gap():
    # At GAP_SIGMAS 4 the floor (4 x 4.62 = 18.5) sits under MIN_GAP (32),
    # so it does not bind; raise GAP_SIGMAS and it must, or a level whose
    # readings all land on one ADC code would count as noiseless.
    saved = protocol.GAP_SIGMAS
    protocol.GAP_SIGMAS = 8                 # 8 x 4.62 = 37 > 32
    try:
        assert not protocol.gap_separable(32, 0.0, 0.0)
        assert protocol.gap_separable(40, 0.0, 0.0)
    finally:
        protocol.GAP_SIGMAS = saved
