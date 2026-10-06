"""Dev Mode measurement engine: the test schedule, the readings, the results.

Measures the panel and the light sensor on real hardware, as specified under
"Dev Mode" in .agents/docs/share_wifi_spec.md. Pure Python -- no `screen`, no
`badge` -- so the schedule and every calculation can be tested under CPython.
What only the badge has is injected: the light sensor and the microsecond
clock, the way Transfer takes `frame_ms`.

One run object measures one condition (dark or lit). The app drives it once a
frame:

    run = MirrorRun(light=badge.light_level, ticks_us=time.ticks_us)
    # or SecondBadgeRun(first, light=badge.light_level, ticks_us=time.ticks_us)
    run.begin(time.ticks_ms())
    while not run.done:
        run.frame(time.ticks_ms(), badge.ticks_delta)   # takes this frame's reads
        draw run.pixels in the edge bands, display.backlight(run.backlight)
        present_us = time.ticks_us()
        present the frame
        run.presented(present_us)                       # the settle capture, if due
    condition = run.results()

and when a whole run (both conditions) is over:

    file_obj = merge_results(State-loaded object, TOOL_MIRROR,
                             tool_result(dark, lit))

`frame()` returns the kind of read it took (READ_NONE, READ_SAMPLE or
READ_BURST) so the app can show the live level. `presented()` may block for up
to CAPTURE_MS + CAPTURE_LEAD_MS reading continuously: that is the settle test,
and it is why it is called after the present rather than inside `frame()`.
"""

import math
import time

# Absolute first, as in __init__.py: the launcher puts the app folder on
# sys.path, so the protocol is a plain top-level module there.
try:
    from protocol import (
        BACKLIGHT_EDGE_LAG_MS, BACKLIGHT_GUARD_PERCENT, BACKLIGHT_TX_MS,
        FINE_MS, HIGH, LCD_SETTLE_MS, LEVEL_SCHEMES, LOW, MID, MIN_GAP,
        MID_FIELD_RGB, SCHEME_BACKLIGHT, SCHEME_FIELDS, SENSOR_STEP,
        SLOT_LADDER, Levels, SlotClock, SlotReceiver,
    )
except ImportError:
    from .protocol import (
        BACKLIGHT_EDGE_LAG_MS, BACKLIGHT_GUARD_PERCENT, BACKLIGHT_TX_MS,
        FINE_MS, HIGH, LCD_SETTLE_MS, LEVEL_SCHEMES, LOW, MID, MIN_GAP,
        MID_FIELD_RGB, SCHEME_BACKLIGHT, SCHEME_FIELDS, SENSOR_STEP,
        SLOT_LADDER, Levels, SlotClock, SlotReceiver,
    )

# The two tools, named as their keys in the measurements file.
TOOL_MIRROR = "mirror"
TOOL_SECOND_BADGE = "second_badge"

# --- the schedule -----------------------------------------------------------
# A step is one field held for a fixed time, and the reading taken while it is
# up. The schedule is a plain list of them, so a transmitter can show it on a
# fixed timeline and a receiver can know, from the same list, when to read.

# A step's `read`, and -- for the first three -- what frame() reports it took.
READ_NONE = 0
READ_SAMPLES = 1    # one light_level() a frame, summarised by their median
READ_SAMPLE = READ_SAMPLES
READ_BURST = 2      # N light_level()s back to back within one frame
READ_CAPTURE = 3    # read continuously, timestamped, after the transition
READ_SYMBOLS = 4    # one a frame, decided into symbol-error slots
READ_AVERAGE = 5    # a burst a frame, its means at each averaging depth
READ_EYE = 6        # a burst a frame, binned by phase into an eye diagram
READ_LOOPBACK = 7   # send a block on the backlight and read it back, blocking

# Reads this close to either end of a held step are left out. The start is the
# panel settling and, in 2nd-badge mode, the receiver's timeline being a frame
# or two off the transmitter's; the end is the same error the other way round.
GUARD_MS = 100

# Per-frame reads kept per step. A 1 s step at the fastest plausible frame time
# fits comfortably; anything past it is dropped rather than allocated.
MAX_STEP_SAMPLES = 256

AMBIENT_MS = 1500           # long enough for 64 reads at frames up to ~21ms
AMBIENT_READS = 64
LEVEL_MS = 1000
RAMP_MS = 1000
RAMP_PERCENTS = (0, 25, 50, 67, 75, 100)
CURVE_MS = 500
CURVE_TENTHS = 11           # 0.0, 0.1 ... 1.0
BLEED_MS = 1000
FLICKER_MS = 500            # the burst is taken half way through
FLICKER_READS = 256
# Keys are spelled out rather than formatted from the floats: a single-precision
# build need not print 0.74 the way the spec does.
FLICKER_BACKLIGHTS = (("0.25", 0.25), ("0.5", 0.5), ("0.74", 0.74),
                      ("0.9", 0.9), ("1.0", 1.0))
SETTLE_GAP_MS = 300
SETTLE_TRANSITIONS = 10
CAPTURE_MS = 60
# A receiver starts its capture this early, because its timeline is the peer's
# observed edges and so sits on the transition rather than before it. Missing
# the start would not bias the slope fit, but it would leave fewer points.
CAPTURE_LEAD_MS = 20
# Reads one capture may hold. light_level() takes tens of microseconds, so 80ms
# is a couple of thousand; a faster sensor just covers less of the window.
CAPTURE_MAX = 3000
# Samples at each end of a capture taken as its starting and final levels.
CAPTURE_EDGE_SAMPLES = 16
SETTLE_90_FACTOR = 2.3      # ln(10): 90% of a single-pole step
# Backlight step: white pixels throughout and only the backlight moving, so the
# liquid crystal never changes and what is timed is the backlight and the
# sensor. display.backlight() sets the PWM duty at once, with no present to
# wait for, so the capture runs around that call rather than after a present,
# reading for CHANGE_LEAD_US before it so the starting level is known.
BACKLIGHT_STEP_TRANSITIONS = 10
CHANGE_LEAD_US = 2000
# One capture of a four- or five-step swing is mostly quantisation, so each
# direction's captures are averaged: every read normalised to 0..1 between its
# capture's starting and final levels and binned by time, then the 10%, 50%
# and 90% crossings read off the mean. Bins run from CURVE_LEAD_US before the
# change (the half way crossing, on a receiver) to CURVE_BINS bins after.
CURVE_BIN_US = 50
CURVE_LEAD_US = 2000
CURVE_BINS = 440            # -2 ms to +20 ms
# A capture whose reads either side of the half way crossing are further
# apart than this was interrupted there -- a collection, an interrupt -- and
# interpolating across the gap would make the edge look sharper than it is.
# Reads come every ~50 µs on the badge. Such a capture is left out.
CAPTURE_GAP_US = 300
# A receiver's timeline runs late -- it hears the peer's go a frame or two
# after it went out, and starts a capture on the frame after its time -- and
# CAPTURE_LEAD_MS covers that only for a settle, whose fit needs no more than
# the tail of a slow decay. A backlight step is over in a millisecond, so a
# receiver starts that capture this early: two 60 Hz frames late plus one of
# start granularity is 50 ms.
BACKLIGHT_LEAD_MS = 50

# Flicker verdict: the share of a burst within FLICKER_SPREAD_STEPS adc_steps of
# its median for the sensor to be averaging the PWM.
FLICKER_SPREAD_STEPS = 2
FLICKER_AVERAGES_PERCENT = 90

# Symbol errors: the three fastest rungs, slowest last as the spec lists them.
SYMBOL_RUNGS = (50, 65, 80)
# Black slots before the sync symbol. The same 4 as protocol.RESYNC_LEAD_IN, and
# for the same reason: enough silence to find an edge out of.
SYNC_LEAD_SLOTS = 4
# How far past its due slot the sync symbol may arrive and still be locked on.
SYNC_LATE_SLOTS = 2

LEVEL_NAMES = ((LOW, "black"), (MID, "mid"), (HIGH, "white"))
BLACK_FULL = ("black", 1.0)
WHITE_FULL = ("white", 1.0)
BLACK_OFF = ("black", 0.0)
WHITE_OFF = ("white", 0.0)

# What a badge shows when it is not showing a test field: black, backlight on,
# so the centre column's text can still be read.
IDLE_FIELD = BLACK_FULL


def de_bruijn(k, n):
    """The lexicographically least de Bruijn sequence B(k, n), as digits.

    The standard construction: concatenate, in order, every Lyndon word whose
    length divides n. Cyclic, so every length-n word appears exactly once when
    the sequence is read round the end.
    """
    a = [0] * (k * n)
    sequence = []

    def db(t, p):
        if t > n:
            if n % p == 0:
                sequence.extend(a[1:p + 1])
        else:
            a[t] = a[t - p]
            db(t + 1, p)
            for j in range(a[t - p] + 1, k):
                a[t] = j
                db(t + 1, t)

    db(1, 1)
    return sequence


# B(3, 3) over black, middle and high, opened out: the 27-symbol cycle with its
# first two symbols repeated on the end, so every one of the 27 triples appears
# in a straight line. 29 symbols.
_ALPHABET = (LOW, MID, HIGH)
_CYCLE = "".join([_ALPHABET[d] for d in de_bruijn(3, 3)])
DE_BRUIJN = _CYCLE + _CYCLE[:2]

# What a symbol-error step transmits: lead-in, sync, sequence.
SYMBOL_BLOCK = LOW * SYNC_LEAD_SLOTS + HIGH + DE_BRUIJN

# --- loopback: the continuous receiver, against the mirror -------------------
# Mirror only. The badge sends SYMBOL_BLOCK on the backlight -- white pixels
# throughout, so no present -- and reads its own reflection in the same tight
# loop, decoding it as a transfer now does: readings pooled into FINE_MS
# samples, locked on the sync symbol's edge less BACKLIGHT_EDGE_LAG_MS, each
# slot decided by the protocol's SlotReceiver with the backlight's guard. It
# is the whole continuous path on hardware with no second badge, at rungs down
# to the backlight scheme's start and past it. And because the badge made the
# change itself, it can time the edge delay the protocol has to assume.
LOOPBACK_RUNGS = (50, 32, 25, 20, 16)
# And with low off: the backlight scheme's low was 0.0 until the dim low
# measured better on 2026-10-06. Kept for comparison -- how much faster the
# dim low's dark rises are, run to run. Its levels are measured first, since
# this run's are for the dim low.
DIM_RUNGS = (25, 16)
DIM_LOW = 0.0
DIM_MID = 0.80
DIM_HOLD_MS = 150
# Step time beyond the block's, so the next step does not begin while the
# loop that blocks the frame is still running.
LOOPBACK_MARGIN_MS = 200


class _UsClock:
    """A slot grid in microseconds from the loop's start: what SlotReceiver
    asks of a SlotClock, finer than its whole milliseconds."""

    def __init__(self, duration):
        self.epoch = 0
        self.duration = duration

    def number(self, t):
        return (t - self.epoch) // self.duration

    def offset(self, t):
        return (t - self.epoch) % self.duration


def hold_levels(light, ticks_us, set_backlight, low, mid):
    """The three backlight levels, measured: each held DIM_HOLD_MS and the
    median of its second half's readings taken. Returns (low, mid, high)."""
    out = []
    buf = [0] * 512
    for level in (low, mid, 1.0):
        set_backlight(level)
        start = ticks_us()
        count = 0
        while True:
            t = time.ticks_diff(ticks_us(), start)
            if t >= DIM_HOLD_MS * 1000:
                break
            value = light()
            if t >= DIM_HOLD_MS * 500 and count < len(buf):
                buf[count] = value
                count += 1
        out.append(median(buf, count) if count else 0)
    set_backlight(low)
    return out[0], out[1], out[2]


def dim_loopback_block(rung_ms, light, ticks_us, set_backlight):
    """The loopback block with low off: its levels measured first, then
    reported with the block's result."""
    low, mid, high = hold_levels(light, ticks_us, set_backlight, DIM_LOW,
                                 DIM_MID)
    measured = [low, mid, high]
    if mid - low < MIN_GAP or high - mid < MIN_GAP:
        return {"error": "levels too close", "levels": measured}
    levels = Levels()
    levels.fit(low, mid, high)
    out = loopback_block(rung_ms, levels, light, ticks_us, set_backlight,
                         DIM_LOW, DIM_MID)
    out["levels"] = measured
    return out


def loopback_block(rung_ms, levels, light, ticks_us, set_backlight,
                   low=None, mid=None):
    """Send SYMBOL_BLOCK at `rung_ms` on the backlight and decode it back.

    Blocks for the block's length. `levels` are this run's backlight levels;
    `set_backlight` is display.backlight(); `low` and `mid` the backlight for
    those symbols, the scheme's unless given. Returns {"symbols", "errors",
    "lag_ms", "reads_per_s"} or {"error": ...}.
    """
    if levels is None:
        return {"error": "no levels"}
    rung = rung_ms * 1000
    block = SYMBOL_BLOCK
    count = len(block)
    sync = SYNC_LEAD_SLOTS
    scheme = LEVEL_SCHEMES[SCHEME_BACKLIGHT]
    duty = {LOW: scheme[LOW][1] if low is None else low, HIGH: 1.0,
            MID: scheme[MID][1] if mid is None else mid}
    clock = _UsClock(rung)
    receiver = SlotReceiver(clock, levels, BACKLIGHT_TX_MS * 1000,
                            BACKLIGHT_GUARD_PERCENT)
    t_low = levels.t_low
    fine = FINE_MS * 1000
    end = (count + 1) * rung
    give_up = (sync + 1 + SYNC_LATE_SLOTS) * rung
    decided = []
    sent = None
    sync_set = None
    reads = 0
    base = pool_sum = pool_n = pool_t = 0
    have_dark = False
    dark_t = dark_v = 0
    lit_pending = False
    lit_t = lit_v = 0
    locked = False
    crossing = 0
    start = ticks_us()
    while True:
        t = time.ticks_diff(ticks_us(), start)
        if t >= end:
            break
        slot = t // rung
        symbol = block[slot] if slot < count else LOW
        if symbol != sent:
            set_backlight(duty[symbol])
            sent = symbol
            if slot == sync:
                sync_set = t
        value = light()
        reads += 1
        pool_sum += value
        pool_n += 1
        pool_t += t - base
        if t - base < fine:
            continue
        ft = base + pool_t // pool_n
        fv = pool_sum // pool_n
        base = t
        pool_sum = pool_n = pool_t = 0
        if locked:
            got = receiver.feed(ft, fv)
            if got is not None:
                decided.append(got)
            continue
        if t > give_up:
            return {"error": "no sync symbol"}
        if fv < t_low:
            have_dark = True
            dark_t, dark_v = ft, fv
            lit_pending = False
            continue
        if not have_dark:
            continue
        if not lit_pending:
            lit_pending = True
            lit_t, lit_v = ft, fv
            continue
        # A second lit sample: the edge is real. Timed at the first's
        # crossing of T_low, as the protocol's phase lock times it.
        span = lit_v - dark_v
        crossing = lit_t
        if span > 0:
            crossing = dark_t + (t_low - dark_v) * (lit_t - dark_t) // span
        clock.epoch = crossing - BACKLIGHT_EDGE_LAG_MS * 1000 - sync * rung
        receiver.current = clock.number(ft)
        receiver.clear()
        receiver.feed(lit_t, lit_v)
        receiver.feed(ft, fv)
        locked = True
    if not locked:
        return {"error": "no sync symbol"}
    decided.append(receiver.decide())
    # decided[0] is the sync slot; the sequence follows it.
    out = {"symbols": len(DE_BRUIJN),
           "errors": count_symbol_errors(decided[1:], DE_BRUIJN)}
    if sync_set is not None:
        out["lag_ms"] = round((crossing - sync_set) / 1000.0, 2)
    out["reads_per_s"] = round(reads * 1000000.0 / end)
    return out

# --- averaging: does reading more often buy resolution? ---------------------
# Each fields level held AVERAGE_MS. Every frame inside the guard window the
# receiver takes AVERAGE_READS readings back to back and keeps the spread of
# the means of every group of 1, 4, 16 and 64 of them. If the sensor's noise
# is wide enough to spread readings over neighbouring ADC steps, the spread
# falls as the square root of the depth and averaging resolves levels finer
# than a step; if it is narrower, it stalls at one step.
AVERAGE_MS = 2000
AVERAGE_READS = 64
AVERAGE_DEPTHS = (1, 4, 16, 64)

# --- the eye diagram: where in a slot a symbol can be read -------------------
# Pseudo-random symbols over two levels and over three, at slots of 50 to
# 100 ms, in the fields scheme. The receiver locks onto the sync symbol's
# visible edge -- with no phase-lock correction, so phase 0 is when a symbol
# is first seen -- and sorts every frame's averaged reading by which symbol
# was showing and how far into its slot. The eye's opening in each of
# EYE_BINS fifths of a slot is the gap between adjacent levels' readings
# there; each slot is also decided from its back half, as the protocol does,
# from single readings and from EYE_READS-reading means.
EYE_RUNGS = (50, 65, 80, 100)
EYE_BINS = 5
EYE_READS = 16
EYE_SLOT_READS = 16         # back-half frames one slot can hold
_BINARY = (LOW, HIGH)
_CYCLE_2 = "".join([_BINARY[d] for d in de_bruijn(2, 4)])
# B(2, 4) opened out: every run of four black-or-white symbols, 19 symbols.
# Not B(2, 5): its five whites in a row are 500 ms at the 100 ms rung, inside
# the go's 450-750 ms, and a badge still answering would take them for one.
# Four is enough -- a panel's memory of a symbol is gone within two slots.
DE_BRUIJN_2 = _CYCLE_2 + _CYCLE_2[:3]
EYE_SEQUENCES = (("2-level", DE_BRUIJN_2), ("3-level", DE_BRUIJN))
LEVEL_INDEX = {LOW: 0, MID: 1, HIGH: 2}


# The chip's own temperature, read at the start of every test and at the end
# of each condition: a proxy for the panel's, whose liquid crystal slows as it
# cools and whose speed has varied between runs. RP2350 datasheet: the sensor
# reads 0.706 V at 27 C and falls 1.721 mV a degree [datasheet]; the ADC's
# reference is taken as 3.3 V. Absolute accuracy is a few degrees; a change
# within a run is what it is for. It is the die, which heats with CPU load,
# not the panel.
TEMP_V27 = 0.706
TEMP_SLOPE = 0.001721
TEMP_VREF = 3.3


def core_temp_c(raw_u16):
    """The RP2350's sensor reading (a read_u16() value, or a mean of several)
    as degrees C, to one decimal place."""
    volts = raw_u16 * TEMP_VREF / 65535
    return round(27 - (volts - TEMP_V27) / TEMP_SLOPE, 1)


class Step:
    """One field held for `ms`, and what to read while it is up.

    `pixels` is "black", "white" or an (r, g, b) tuple, as in LEVEL_SCHEMES.
    A symbol step has no fixed field: `field_at()` walks `symbols`, one per
    `slot_ms`, in `scheme`.
    """

    def __init__(self, test, key, field, ms, read=READ_NONE, count=0,
                 scheme=None, symbols=None, slot_ms=0):
        self.test = test
        self.key = key
        self.field = field
        self.ms = ms
        self.read = read
        self.count = count
        self.scheme = scheme
        self.symbols = symbols
        self.slot_ms = slot_ms

    def field_at(self, offset):
        if self.symbols is None:
            return self.field
        slot = offset // self.slot_ms
        if slot >= len(self.symbols):
            slot = len(self.symbols) - 1
        return LEVEL_SCHEMES[self.scheme][self.symbols[slot]]


def ramp_value(percent):
    return (255 * percent + 50) // 100


def curve_key(tenths):
    return "%d.%d" % (tenths // 10, tenths % 10)


def build_schedule(tool):
    """Every step of one condition, in the order of the spec's table."""
    steps = [Step("ambient", "ambient", BLACK_OFF, AMBIENT_MS, READ_SAMPLES,
                  AMBIENT_READS)]

    # adc_step and noise have no steps of their own: they are computed from
    # every reading and from the levels' reads respectively.
    for scheme in (SCHEME_FIELDS, SCHEME_BACKLIGHT):
        for symbol, name in LEVEL_NAMES:
            steps.append(Step("levels", (scheme, name),
                              LEVEL_SCHEMES[scheme][symbol], LEVEL_MS,
                              READ_SAMPLES, MAX_STEP_SAMPLES))

    for percent in RAMP_PERCENTS:
        value = ramp_value(percent)
        steps.append(Step("gamma_ramp", str(percent),
                          ((value, value, value), 1.0), RAMP_MS,
                          READ_SAMPLES, MAX_STEP_SAMPLES))

    for tenths in range(CURVE_TENTHS):
        steps.append(Step("backlight_curve", curve_key(tenths),
                          ("white", tenths / 10), CURVE_MS,
                          READ_SAMPLES, MAX_STEP_SAMPLES))

    steps.append(Step("bleed", "black_on", BLACK_FULL, BLEED_MS,
                      READ_SAMPLES, MAX_STEP_SAMPLES))
    steps.append(Step("bleed", "black_off", BLACK_OFF, BLEED_MS,
                      READ_SAMPLES, MAX_STEP_SAMPLES))

    for key, backlight in FLICKER_BACKLIGHTS:
        steps.append(Step("flicker", key, ("white", backlight), FLICKER_MS,
                          READ_BURST, FLICKER_READS))

    # Settle: a black to start from (the backlight was just off, so this edge
    # is not a white-to-black one), then ten white/black pairs -- a rise and a
    # fall each -- then ten middles with black between. The black after a
    # middle is a transition nobody asked for, so it is not captured.
    mid = (MID_FIELD_RGB, 1.0)
    steps.append(Step("settle", None, BLACK_FULL, SETTLE_GAP_MS))
    for _ in range(SETTLE_TRANSITIONS):
        steps.append(Step("settle", "rise", WHITE_FULL, SETTLE_GAP_MS,
                          READ_CAPTURE))
        steps.append(Step("settle", "fall", BLACK_FULL, SETTLE_GAP_MS,
                          READ_CAPTURE))
    for i in range(SETTLE_TRANSITIONS):
        if i:
            steps.append(Step("settle", None, BLACK_FULL, SETTLE_GAP_MS))
        steps.append(Step("settle", "mid_rise", mid, SETTLE_GAP_MS,
                          READ_CAPTURE))

    # Backlight step: the same three transitions, made by the backlight alone
    # on white pixels. The first hold puts the white up with the backlight off,
    # which is the one pixel change in it.
    bl_mid = LEVEL_SCHEMES[SCHEME_BACKLIGHT][MID]
    steps.append(Step("backlight_step", None, WHITE_OFF, SETTLE_GAP_MS))
    for _ in range(BACKLIGHT_STEP_TRANSITIONS):
        steps.append(Step("backlight_step", "rise", WHITE_FULL, SETTLE_GAP_MS,
                          READ_CAPTURE))
        steps.append(Step("backlight_step", "fall", WHITE_OFF, SETTLE_GAP_MS,
                          READ_CAPTURE))
    for i in range(BACKLIGHT_STEP_TRANSITIONS):
        if i:
            steps.append(Step("backlight_step", None, WHITE_OFF,
                              SETTLE_GAP_MS))
        steps.append(Step("backlight_step", "mid_rise", bl_mid, SETTLE_GAP_MS,
                          READ_CAPTURE))

    if tool == TOOL_MIRROR:
        # White, the backlight off: the lead-in. Every step since the
        # backlight_step began has had white pixels, so none goes up now.
        for rung in LOOPBACK_RUNGS:
            steps.append(Step("loopback", str(rung), WHITE_OFF,
                              (len(SYMBOL_BLOCK) + 1) * rung
                              + LOOPBACK_MARGIN_MS,
                              READ_LOOPBACK, slot_ms=rung))
        for rung in DIM_RUNGS:
            steps.append(Step("loopback", "off_%d" % rung, WHITE_OFF,
                              3 * DIM_HOLD_MS + (len(SYMBOL_BLOCK) + 1) * rung
                              + LOOPBACK_MARGIN_MS,
                              READ_LOOPBACK, slot_ms=rung))

    if tool == TOOL_SECOND_BADGE:
        for scheme in (SCHEME_FIELDS, SCHEME_BACKLIGHT):
            for rung in SYMBOL_RUNGS:
                steps.append(Step("symbol_errors", (scheme, rung), None,
                                  len(SYMBOL_BLOCK) * rung, READ_SYMBOLS,
                                  scheme=scheme, symbols=SYMBOL_BLOCK,
                                  slot_ms=rung))
        for symbol, name in LEVEL_NAMES:
            steps.append(Step("averaging", name,
                              LEVEL_SCHEMES[SCHEME_FIELDS][symbol], AVERAGE_MS,
                              READ_AVERAGE, AVERAGE_READS))
        for name, sequence in EYE_SEQUENCES:
            block = LOW * SYNC_LEAD_SLOTS + HIGH + sequence
            for rung in EYE_RUNGS:
                steps.append(Step("eye", (name, rung), None, len(block) * rung,
                                  READ_EYE, scheme=SCHEME_FIELDS,
                                  symbols=block, slot_ms=rung))
    return steps


def schedule_ms(steps):
    total = 0
    for step in steps:
        total += step.ms
    return total


def step_starts(steps):
    starts = []
    at = 0
    for step in steps:
        starts.append(at)
        at += step.ms
    return starts


MIRROR_SCHEDULE = build_schedule(TOOL_MIRROR)
SECOND_BADGE_SCHEDULE = build_schedule(TOOL_SECOND_BADGE)
MIRROR_SCHEDULE_MS = schedule_ms(MIRROR_SCHEDULE)
SECOND_BADGE_SCHEDULE_MS = schedule_ms(SECOND_BADGE_SCHEDULE)

# Every result key per tool, in the order of the spec's table: the centre
# column's "Dark 4/11" counts in these.
MIRROR_TESTS = ("ambient", "adc_step", "levels", "noise", "gamma_ramp",
                "backlight_curve", "bleed", "flicker", "settle",
                "backlight_step", "loopback", "frame_timing", "edge_latency")
SECOND_BADGE_TESTS = ("ambient", "adc_step", "levels", "noise", "gamma_ramp",
                      "backlight_curve", "bleed", "flicker", "settle",
                      "backlight_step", "frame_timing", "symbol_errors",
                      "averaging", "eye")

# --- the 2nd-badge handshake ------------------------------------------------
# Every 2nd-badge turn starts with a handshake: the badge about to send CALLS
# with short pulses until the other ANSWERS half way between them, then says
# GO. Nothing is timed against the other badge until both have seen each
# other, so neither user has to press A at any particular moment. See "The
# handshake" in the spec.

# A call pulse is about five frames of white: long enough for the panel to
# settle (at most 22 ms measured) and leave several reads at full height,
# short enough that a sixth of the time is white -- a rhythm that appears
# nowhere in the schedule.
PULSE_HIGH_MS = 60
PULSE_PERIOD_MS = 360
# Half a period: a badge's own pulse can echo off the other's glass into its
# own sensor, and that echo arrives with the pulse, 0-90 ms into the cycle
# (ECHO_MS). A real answer arrives at 180 ms plus a frame or two of latency
# each side, so the accepted window cannot mistake one for the other, and it
# ends before the caller's next pulse at 360 ms.
REPLY_DELAY_MS = 180
ECHO_MS = 90
REPLY_EARLIEST_MS = 120
REPLY_LATEST_MS = 270
# Three answers in a row rather than one, so a noise spike cannot start a run.
REPLIES_NEEDED = 3
# A go rather than simply stopping, so the answerer never has to judge silence
# by a timeout; its fall is a single sharp edge both badges time. That fall is
# time zero: both hold black for GO_GAP_MS and the sender's schedule starts
# then.
GO_MS = 600
GO_GAP_MS = 1000
HANDSHAKE_TIMEOUT_MS = 60000
# The black after each turn's schedule: the sender's last field has gone and
# the measurer's last read is done before anyone calls again.
TURN_END_MS = 1000

# What counts as a pulse: a rise of at least the threshold above the baseline
# (the lowest read of the last BASELINE_MS) that falls back below half the
# threshold within PULSE_MIN_MS-PULSE_MAX_MS.
BASELINE_MS = 1000
PULSE_MIN_MS = 20
PULSE_MAX_MS = 150
# What counts as a go: a rise held for more than three pulse widths -- and,
# beyond the spec's bare "more than 180 ms", within a quarter of GO_MS either
# way. Unbounded, the settle steps' 300 ms whites and every 1 s level step are
# gos, and a badge that missed the real one would lock onto the peer's
# schedule and measure it out of step, reporting success.
GO_MIN_MS = GO_MS - GO_MS // 4
GO_MAX_MS = GO_MS + GO_MS // 4
# ...and the answerer takes a go only once it has seen REPLIES_NEEDED call
# pulses a period apart, the last of them a period before the go -- the caller
# puts its go where its next pulse would have been, and sends none without
# three answers in a row. The symbol blocks hold a ten-slot run that is never
# black, 500-800 ms long, and their single high slots are pulses: a width
# window alone lets them pass for a go.
PERIOD_MIN_MS = PULSE_PERIOD_MS - PULSE_PERIOD_MS // 4
PERIOD_MAX_MS = PULSE_PERIOD_MS + PULSE_PERIOD_MS // 4

# The threshold comes from the run's NOISE_MS of black, which comes after the
# 3 s start delay rather than during it: the hand that pressed A may still be
# moving the badge then. Not the protocol's old fixed floor (MIN_CONTRAST,
# 192): the measured panel swings 80, and a detector holding to that would
# never see a pulse. Half again
# the noise is the ratio 48 has to the 32-count noise measured on hardware, and
# never less than PULSE_MIN_SWING (three ADC steps), so a still screen cannot
# trigger on a single step.
NOISE_MS = 1000
NOISE_CAPACITY = 256        # NOISE_MS at frames down to 4 ms
NOISE_MIN_READS = 8
# The noise is twice the spread from the lowest read to the median: for noise
# spread evenly about a level that is its peak-to-peak, and the median cannot
# be reached by the other badge's call pulses if it started calling first --
# a sixth of the time, plus the panel settling either side of each. An 80th
# percentile could: with three pulses and their settling in the second it
# landed on a lit read and set a threshold of 1248 on a 4000-count swing,
# which on a weaker panel would have been above the signal.
PULSE_MIN_SWING = 3 * SENSOR_STEP

# After a handshake times out the run holds its last screen this long before
# it ends, so "No answer from the other badge" is on the screen to be read
# rather than replaced at once by the next prompt.
NO_ANSWER_HOLD_MS = 3000


def noise_spread(values, count):
    """Twice the lowest read to the (lower) median, or None under 8 reads."""
    if count < NOISE_MIN_READS:
        return None
    ordered = sorted(values[:count])
    return 2 * (ordered[(count - 1) // 2] - ordered[0])


def pulse_threshold(noise):
    """The rise a pulse must show, given the noise second's spread."""
    if noise is None:
        return PULSE_MIN_SWING
    return max(PULSE_MIN_SWING, noise * 3 // 2)


# --- calculations -----------------------------------------------------------
# Each takes plain lists so it can be tested on synthetic data. `count` limits
# a preallocated buffer to the part that was filled.

def gcd(a, b):
    a = abs(a)
    b = abs(b)
    while b:
        a, b = b, a % b
    return a


def adc_step(values, count=None):
    """The greatest common divisor of every reading: the sensor's step.

    A zero contributes nothing, so a run of all zeros gives 0 -- no step found.
    """
    if count is None:
        count = len(values)
    step = 0
    for i in range(count):
        step = gcd(step, values[i])
    return step


def median(values, count=None):
    """The upper median, so a median of readings is itself a reading."""
    if count is None:
        count = len(values)
    if count <= 0:
        return None
    ordered = sorted(values[:count])
    return ordered[count // 2]


def peak_to_peak(values, count=None):
    if count is None:
        count = len(values)
    if count <= 0:
        return None
    lo = hi = values[0]
    for i in range(1, count):
        if values[i] < lo:
            lo = values[i]
        elif values[i] > hi:
            hi = values[i]
    return hi - lo


def sd(values, count=None):
    """Population standard deviation, two-pass so float32 holds its precision."""
    if count is None:
        count = len(values)
    if count <= 0:
        return None
    mean = 0.0
    for i in range(count):
        mean += values[i]
    mean /= count
    total = 0.0
    for i in range(count):
        total += (values[i] - mean) * (values[i] - mean)
    return math.sqrt(total / count)


def fit_gamma(fractions, readings):
    """Panel gamma from a ramp: L = black + span * fraction ** gamma.

    Black and span are pinned by the 0 and 1 points, which leaves one unknown;
    each point in between then gives ln(y) = gamma * ln(f), y being its share of
    the span, and gamma is the least-squares slope of that line through the
    origin: sum(ln f * ln y) / sum(ln f ** 2). A point reading no brighter than
    black is dropped -- its log is undefined, and on a 16-count sensor it only
    says the step was below one count. One brighter than white is taken as
    white. None when no point is usable.
    """
    black = white = None
    for i in range(len(fractions)):
        if fractions[i] <= 0:
            black = readings[i]
        elif fractions[i] >= 1:
            white = readings[i]
    if black is None or white is None or white <= black:
        return None
    span = white - black
    num = den = 0.0
    for i in range(len(fractions)):
        f = fractions[i]
        if not 0 < f < 1:
            continue
        y = (readings[i] - black) / span
        if y <= 0:
            continue
        if y > 1:
            y = 1.0
        lf = math.log(f)
        num += lf * math.log(y)
        den += lf * lf
    if den == 0:
        return None
    return num / den


def settle_tau(times_us, values, count, step):
    """The time constant of one transition, in ms, or None.

    A single-pole panel approaches its final level as |final - v| = A e^(-t/tau),
    so ln|final - v| falls in a straight line of slope -1/tau whatever the
    starting point -- which is why a capture that starts late still measures
    the right tau. The final level is the median of the capture's last quarter
    and the starting one the median of its first few reads.

    Only the decay itself is fitted: the contiguous run of reads just before
    the residual first falls to the floor, back to where it last stood within
    half a floor of the whole swing. The plateau before the edge and the noise
    after it would both flatten the slope. The floor is one ADC step, or a
    sixteenth of the swing on a finer sensor, so the log of a residual of one or
    two counts never dominates.
    """
    if count < 8:
        return None
    edge = min(CAPTURE_EDGE_SAMPLES, count // 4)
    initial = median(values, edge)
    final = median(values[count - count // 4:count])
    swing = abs(final - initial)
    floor = max(step, swing // 16, 1)
    if swing < 2 * floor:
        return None
    upper = swing - floor // 2

    stop = count
    for i in range(count):
        if abs(final - values[i]) <= floor:
            stop = i
            break
    start = stop
    while start > 0:
        d = abs(final - values[start - 1])
        if d <= floor or d >= upper:
            break
        start -= 1
    n = stop - start
    if n < 3:
        return None
    # At least two residual levels. A swing of four ADC steps can leave the
    # whole run at one: then the slope is zero, and rounding the mean of
    # identical logs turns it into a tiny negative one -- a 4.2e10 ms tau on
    # the second hardware run.
    first = abs(final - values[start])
    for i in range(start + 1, stop):
        if abs(final - values[i]) != first:
            break
    else:
        return None

    mean_t = mean_y = 0.0
    for i in range(start, stop):
        mean_t += times_us[i]
        mean_y += math.log(abs(final - values[i]))
    mean_t /= n
    mean_y /= n
    sxy = sxx = 0.0
    for i in range(start, stop):
        dt = times_us[i] - mean_t
        sxy += dt * (math.log(abs(final - values[i])) - mean_y)
        sxx += dt * dt
    if sxx <= 0:
        return None
    slope = sxy / sxx              # per microsecond
    if slope >= 0:
        return None
    tau = -1.0 / slope / 1000.0
    # A time constant longer than the capture it was fitted from was not
    # measured by it: a reading held one step off its final level, not a
    # decay.
    if tau > CAPTURE_MS:
        return None
    return tau


def edge_latency_ms(times_us, values, count, t_low):
    """When a rise first reaches T_low, in ms from the capture's time origin.

    Times are relative to the call that presented the frame, so this is the
    edge latency the phase lock corrects for. Interpolated between the reads
    either side; a capture that was already past T_low gives its first read's
    time, an upper bound. None when it never got there.
    """
    for i in range(count):
        if values[i] >= t_low:
            if i == 0:
                return times_us[0] / 1000.0
            v0 = values[i - 1]
            t0 = times_us[i - 1]
            span = values[i] - v0
            t = t0 + (t_low - v0) * (times_us[i] - t0) / span
            return t / 1000.0
    return None


# Why a backlight step capture was left out, as counted in its result.
SKIP_FLAT = "flat"          # it moved less than two floors
SKIP_NO_EDGE = "no_edge"    # it never left its starting level and crossed half way
SKIP_GAP = "gap"            # its reads either side of half way were too far apart
SKIP_REASONS = (SKIP_FLAT, SKIP_NO_EDGE, SKIP_GAP)


def step_levels(values, count, step):
    """(initial, final, floor) of a captured step, or None if too short.

    Levels as settle_tau() takes them: the median of the first reads and of
    the last quarter. The floor is one ADC step or a sixteenth of the swing.
    Whether it moved enough is step_moved()'s question.
    """
    if count < 8:
        return None
    edge = min(CAPTURE_EDGE_SAMPLES, count // 4)
    initial = median(values, edge)
    final = median(values[count - count // 4:count])
    floor = max(step, abs(final - initial) // 16, 1)
    return initial, final, floor


def step_moved(initial, final, floor):
    return abs(final - initial) >= 2 * floor


def half_time(times_us, values, count, initial, final, floor):
    """When a captured step crossed half way, in µs from its time origin.

    Interpolated between the reads either side. Only after it has left the
    starting level -- two reads in a row more than a floor away -- so one
    noisy read in the lead-in is not taken for the edge.

    Returns (time, None), or (None, SKIP_NO_EDGE) if it never got there, or
    (None, SKIP_GAP) if the reads either side are more than CAPTURE_GAP_US
    apart.
    """
    edge = min(CAPTURE_EDGE_SAMPLES, count // 4)
    left = None
    for i in range(edge, count - 1):
        if (abs(values[i] - initial) > floor
                and abs(values[i + 1] - initial) > floor):
            left = i
            break
    if left is None:
        return None, SKIP_NO_EDGE
    swing = final - initial
    half = initial + swing / 2.0
    for i in range(left, count):
        if (values[i] - half) * swing >= 0:
            v0 = values[i - 1]
            t0 = times_us[i - 1]
            if times_us[i] - t0 > CAPTURE_GAP_US:
                return None, SKIP_GAP
            span = values[i] - v0
            if not span:
                return float(times_us[i]), None
            return t0 + (half - v0) * (times_us[i] - t0) / span, None
    return None, SKIP_NO_EDGE


def curve_crossing(sums, counts, fraction):
    """When an averaged step first reached `fraction`, in µs from its origin.

    The bins hold sums of normalised reads and how many there were. Two
    filled bins in a row at or past `fraction`, so one noisy bin cannot
    decide it; interpolated from the filled bin before. None if never.
    """
    prev_t = prev_v = None
    held = None
    for b in range(len(sums)):
        n = counts[b]
        if not n:
            continue
        v = sums[b] / n
        t = b * CURVE_BIN_US - CURVE_LEAD_US + CURVE_BIN_US / 2.0
        if v >= fraction:
            if held is not None:
                return held
            if prev_v is None or v == prev_v:
                held = t
            else:
                held = prev_t + (fraction - prev_v) * (t - prev_t) / (v - prev_v)
        else:
            held = None
        prev_t = t
        prev_v = v
    return None


def flicker_verdict(values, step, count=None):
    """"averages" if the sensor smooths the backlight's PWM, else "follows_pwm"."""
    if count is None:
        count = len(values)
    mid = median(values, count)
    spread = FLICKER_SPREAD_STEPS * step
    within = 0
    for i in range(count):
        if abs(values[i] - mid) <= spread:
            within += 1
    if within * 100 >= FLICKER_AVERAGES_PERCENT * count:
        return "averages"
    return "follows_pwm"


def flicker_result(values, step):
    return {"min": min(values), "max": max(values),
            "sd": round(sd(values), 1),
            "verdict": flicker_verdict(values, step)}


class FrameStats:
    """ticks_delta over a run, in integers so a frame costs no allocation."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.frames = 0
        self.total = 0
        self.total_sq = 0
        self.lo = 0
        self.hi = 0

    def add(self, delta):
        if self.frames == 0 or delta < self.lo:
            self.lo = delta
        if self.frames == 0 or delta > self.hi:
            self.hi = delta
        self.frames += 1
        self.total += delta
        self.total_sq += delta * delta

    def result(self):
        if not self.frames:
            return {"error": "no frames"}
        n = self.frames
        # Exact in integers, then one division: no cancellation to lose.
        var = (n * self.total_sq - self.total * self.total) / (n * n)
        return {"frames": n, "mean_ms": round(self.total / n, 1),
                "min_ms": self.lo, "max_ms": self.hi,
                "sd_ms": round(math.sqrt(max(0.0, var)), 1)}


def frame_timing(deltas):
    stats = FrameStats()
    for delta in deltas:
        stats.add(delta)
    return stats.result()


def edge_lag(frame_ms):
    # The protocol's own correction (Transfer.edge_lag): a symbol goes up half
    # a frame late on average, and T_low is a tenth of the way up the settle.
    return max(1, frame_ms) // 2 + LCD_SETTLE_MS // 10


def _crossing(t0, v0, t1, v1, level):
    """When the line from (t0, v0) to (t1, v1) passes `level`, clamped."""
    dt = time.ticks_diff(t1, t0)
    span = v1 - v0
    if span == 0 or dt <= 0:
        return t1
    offset = (level - v0) * dt // span
    if offset < 0:
        offset = 0
    elif offset > dt:
        offset = dt
    return time.ticks_add(t0, offset)


# --- the handshake's pulses -------------------------------------------------

PULSE_NONE = 0
PULSE_SEEN = 1      # a call or an answer: `rise` says when it began
PULSE_GO = 2        # a go: `rise` and `fall`, the fall being time zero


class PulseDetector:
    """Finds the handshake's pulses and gos in one read a frame.

    The baseline is the lowest read of the last BASELINE_MS; a rise of
    `threshold` above it starts a pulse, and a fall back below half the
    threshold above the same baseline ends it. The rise is interpolated where
    the reads cross half the threshold, the fall where they cross the middle
    of the swing the pulse reached. Nothing is allocated per read.
    """

    CAPACITY = BASELINE_MS // 4 + 1     # BASELINE_MS at frames down to 4 ms

    def __init__(self):
        self.times = [0] * self.CAPACITY
        self.values = [0] * self.CAPACITY
        self.threshold = PULSE_MIN_SWING
        self.reset()

    def reset(self):
        self.head = 0
        self.count = 0
        self.high = False
        self.have_prev = False
        self.prev_t = 0
        self.prev_v = 0
        self.base = 0
        self.peak = 0
        self.rise = 0
        self.fall = 0

    def baseline(self, now):
        cap = self.CAPACITY
        lo = None
        for n in range(self.count):
            idx = (self.head - 1 - n) % cap
            if time.ticks_diff(now, self.times[idx]) > BASELINE_MS:
                break
            if lo is None or self.values[idx] < lo:
                lo = self.values[idx]
        return lo

    def push(self, now, value):
        """One read. PULSE_NONE, PULSE_SEEN or PULSE_GO."""
        self.times[self.head] = now
        self.values[self.head] = value
        self.head = (self.head + 1) % self.CAPACITY
        if self.count < self.CAPACITY:
            self.count += 1

        event = PULSE_NONE
        half = self.threshold // 2
        if not self.high:
            base = self.baseline(now)
            if value >= base + self.threshold:
                self.high = True
                self.base = base
                self.peak = value
                if self.have_prev:
                    self.rise = _crossing(self.prev_t, self.prev_v, now,
                                          value, base + half)
                else:
                    self.rise = now
        else:
            if value > self.peak:
                self.peak = value
            if value < self.base + half:
                self.high = False
                self.fall = _crossing(self.prev_t, self.prev_v, now, value,
                                      (self.base + self.peak) // 2)
                width = time.ticks_diff(self.fall, self.rise)
                if PULSE_MIN_MS <= width <= PULSE_MAX_MS:
                    event = PULSE_SEEN
                elif GO_MIN_MS <= width <= GO_MAX_MS:
                    event = PULSE_GO
        self.prev_t = now
        self.prev_v = value
        self.have_prev = True
        return event


# --- symbol errors ----------------------------------------------------------

class SymbolBlock:
    """Receives one symbol-error block and counts its errors.

    Searches for the sync symbol's rising edge -- the first read at or above
    T_low after at least a slot and a half of black -- locks a SlotClock to it
    as the protocol's phase lock does, then decides every slot with the
    protocol's own SlotReceiver. Slot 0 is the sync; slots 1..29 are the
    sequence.
    """

    SEARCH = 0
    LOCKED = 1
    DONE = 2

    def __init__(self):
        self.decided = [None] * (len(DE_BRUIJN) + 1)
        self.clock = SlotClock()
        self.receiver = None
        self.levels = None
        self.state = self.DONE
        self.error = None
        self.key = None

    def start(self, key, block_start, slot_ms, levels, frame_ms):
        for i in range(len(self.decided)):
            self.decided[i] = None
        self.key = key
        self.levels = levels
        self.slot_ms = slot_ms
        self.clock.epoch = block_start
        self.clock.duration = slot_ms
        self.clock.index = SLOT_LADDER.index(slot_ms)
        self.frame_ms = frame_ms
        # Open half way through the lead-in: early enough for a receiver that
        # runs late, after the end of the previous block for one that runs early.
        self.open_at = time.ticks_add(block_start,
                                      slot_ms * (SYNC_LEAD_SLOTS // 2))
        self.close_at = time.ticks_add(block_start, slot_ms * (
            SYNC_LEAD_SLOTS + 1 + SYNC_LATE_SLOTS))
        self.dark_since = None
        self.have_prev = False
        self.receiver = None
        self.error = None
        self.state = self.SEARCH

    @property
    def active(self):
        return self.state != self.DONE

    def take(self, now, light):
        """This frame's read, fed in: (the reading, whether the block ended)."""
        value = light()
        return value, self.feed(now, value)

    def feed(self, now, value):
        """One read. True once the block is finished."""
        if self.state == self.SEARCH:
            self._search(now, value)
        elif self.state == self.LOCKED:
            self._decode(now, value)
        return self.state == self.DONE

    def _search(self, now, value):
        t_low = self.levels.t_low
        lit = value >= t_low
        if (lit and self.have_prev and self.dark_since is not None
                and time.ticks_diff(now, self.open_at) >= 0
                and time.ticks_diff(now, self.dark_since)
                >= self.slot_ms * 3 // 2):
            boundary = _crossing(self.prev_t, self.prev_v, now, value, t_low)
            self.clock.epoch = time.ticks_add(boundary,
                                              -edge_lag(self.frame_ms))
            self.receiver = SlotReceiver(self.clock, self.levels,
                                         self.frame_ms)
            self.levels.last = HIGH     # the sync symbol is what was just seen
            self.state = self.LOCKED
            self._decode(now, value)
            return
        if lit:
            self.dark_since = None
        elif self.dark_since is None:
            self.dark_since = now
        self.prev_t = now
        self.prev_v = value
        self.have_prev = True
        if time.ticks_diff(now, self.close_at) > 0:
            self.error = "no sync symbol"
            self.state = self.DONE

    def _decode(self, now, value):
        receiver = self.receiver
        previous = receiver.current
        decided = receiver.feed(now, value)
        if decided is not None and 0 < previous < len(self.decided):
            self.decided[previous] = decided
        if receiver.current >= len(self.decided):
            self.state = self.DONE

    def finish(self):
        """Ends the block now, deciding the slot in progress if it counts."""
        if self.state == self.SEARCH:
            self.error = "no sync symbol"
        elif self.state == self.LOCKED:
            current = self.receiver.current
            if 0 < current < len(self.decided) and self.decided[current] is None:
                self.decided[current] = self.receiver.decide()
        self.state = self.DONE

    def result(self):
        if self.error is not None:
            return {"error": self.error}
        return {"symbols": len(DE_BRUIJN),
                "errors": count_symbol_errors(self.decided[1:], DE_BRUIJN)}


class EyeBlock:
    """Receives one eye-diagram block: its eye, and its errors at two depths.

    Finds the sync symbol's rising edge as SymbolBlock does, but takes that
    edge itself as phase 0 -- no edge_lag -- so the eye shows where in a slot
    a symbol is readable after it is first seen. Nothing is allocated per
    frame: every buffer is sized for the longest sequence at start.
    """

    SEARCH = 0
    LOCKED = 1
    DONE = 2

    def __init__(self):
        longest = max(len(DE_BRUIJN), len(DE_BRUIJN_2))
        self.decided_1 = [None] * longest
        self.decided_avg = [None] * longest
        self.slot_1 = [0] * EYE_SLOT_READS
        self.slot_avg = [0.0] * EYE_SLOT_READS
        self.lo = [0.0] * (3 * EYE_BINS)
        self.hi = [0.0] * (3 * EYE_BINS)
        self.seen = [0] * (3 * EYE_BINS)
        self.state = self.DONE
        self.error = None
        self.key = None

    def start(self, key, block_start, slot_ms, levels, sequence):
        self.key = key
        self.levels = levels
        self.slot_ms = slot_ms
        self.sequence = sequence
        self.alphabet = [s for s in (LOW, MID, HIGH) if s in sequence]
        for i in range(len(self.decided_1)):
            self.decided_1[i] = None
            self.decided_avg[i] = None
        for i in range(len(self.seen)):
            self.seen[i] = 0
        self.open_at = time.ticks_add(block_start,
                                      slot_ms * (SYNC_LEAD_SLOTS // 2))
        self.close_at = time.ticks_add(block_start, slot_ms * (
            SYNC_LEAD_SLOTS + 1 + SYNC_LATE_SLOTS))
        self.dark_since = None
        self.have_prev = False
        self.edge = 0
        self.slot = 0
        self.n = 0
        self.error = None
        self.state = self.SEARCH

    @property
    def active(self):
        return self.state != self.DONE

    def take(self, now, light):
        """EYE_READS readings: (the first, whether the block ended)."""
        first = light()
        total = first
        for _ in range(EYE_READS - 1):
            total += light()
        return first, self.feed(now, first, total / EYE_READS)

    def feed(self, now, value, mean):
        if self.state == self.SEARCH:
            self._search(now, value, mean)
        elif self.state == self.LOCKED:
            self._decode(now, value, mean)
        return self.state == self.DONE

    def _search(self, now, value, mean):
        t_low = self.levels.t_low
        lit = value >= t_low
        if (lit and self.have_prev and self.dark_since is not None
                and time.ticks_diff(now, self.open_at) >= 0
                and time.ticks_diff(now, self.dark_since)
                >= self.slot_ms * 3 // 2):
            self.edge = _crossing(self.prev_t, self.prev_v, now, value, t_low)
            self.slot = 0
            self.n = 0
            self.state = self.LOCKED
            self._decode(now, value, mean)
            return
        if lit:
            self.dark_since = None
        elif self.dark_since is None:
            self.dark_since = now
        self.prev_t = now
        self.prev_v = value
        self.have_prev = True
        if time.ticks_diff(now, self.close_at) > 0:
            self.error = "no sync symbol"
            self.state = self.DONE

    def _decode(self, now, value, mean):
        elapsed = time.ticks_diff(now, self.edge)
        if elapsed < 0:
            return
        slot_ms = self.slot_ms
        k = elapsed // slot_ms
        if k != self.slot:
            self._decide()
            self.slot = k
            self.n = 0
        if k > len(self.sequence):
            self.state = self.DONE
            return
        if k == 0:
            return                  # the sync symbol
        phase = elapsed - k * slot_ms
        i = LEVEL_INDEX[self.sequence[k - 1]] * EYE_BINS + phase * EYE_BINS // slot_ms
        if not self.seen[i] or mean < self.lo[i]:
            self.lo[i] = mean
        if not self.seen[i] or mean > self.hi[i]:
            self.hi[i] = mean
        self.seen[i] += 1
        if phase * 2 >= slot_ms and self.n < EYE_SLOT_READS:
            self.slot_1[self.n] = value
            self.slot_avg[self.n] = mean
            self.n += 1

    def _decide(self):
        k = self.slot
        if 1 <= k <= len(self.sequence) and self.n:
            self.decided_1[k - 1] = self.classify(median(self.slot_1, self.n))
            self.decided_avg[k - 1] = self.classify(
                median(self.slot_avg, self.n))

    def classify(self, value):
        levels = self.levels
        if len(self.alphabet) == 2:
            return HIGH if value * 2 >= levels.black + levels.white else LOW
        if value < levels.t_low:
            return LOW
        if value < levels.t_high:
            return MID
        return HIGH

    def finish(self):
        if self.state == self.SEARCH:
            self.error = "no sync symbol"
        elif self.state == self.LOCKED:
            self._decide()
        self.state = self.DONE

    def openings(self):
        """Per phase bin, the narrowest gap between adjacent levels' averaged
        readings there -- negative where they overlap -- or None."""
        out = []
        alphabet = self.alphabet
        for b in range(EYE_BINS):
            narrowest = None
            for j in range(len(alphabet) - 1):
                lower = LEVEL_INDEX[alphabet[j]] * EYE_BINS + b
                upper = LEVEL_INDEX[alphabet[j + 1]] * EYE_BINS + b
                if not self.seen[lower] or not self.seen[upper]:
                    narrowest = None
                    break
                gap = self.lo[upper] - self.hi[lower]
                if narrowest is None or gap < narrowest:
                    narrowest = gap
            out.append(None if narrowest is None else round(narrowest, 1))
        return out

    def result(self):
        if self.error is not None:
            return {"error": self.error}
        n = len(self.sequence)
        opening = self.openings()
        best = None
        for b in range(EYE_BINS):
            if opening[b] is not None and (
                    best is None or opening[b] > opening[best]):
                best = b
        return {"symbols": n,
                "errors_1": count_symbol_errors(self.decided_1[:n],
                                                self.sequence),
                "errors_avg": count_symbol_errors(self.decided_avg[:n],
                                                  self.sequence),
                "opening": opening, "best_bin": best}


def count_symbol_errors(decided, expected):
    """Slots decided wrongly, or not decided at all, against `expected`."""
    errors = 0
    for i in range(len(expected)):
        if i >= len(decided) or decided[i] != expected[i]:
            errors += 1
    return errors


# --- the recorder -----------------------------------------------------------

class Recorder:
    """Takes a schedule's readings against a timeline and builds the results.

    The read side of a run. In mirror mode its epoch is when the badge began
    showing the schedule; in 2nd-badge mode it is GO_GAP_MS after the fall of
    the peer's go. It never decides what is displayed.
    """

    def __init__(self, steps, tool, light, ticks_us=None):
        self.steps = steps
        self.tool = tool
        self.light = light
        # Looked up here rather than as a default, so the module still imports
        # under a CPython whose time has no ticks_us.
        self.ticks_us = ticks_us or time.ticks_us
        self.starts = step_starts(steps)
        self.lead = 0 if tool == TOOL_MIRROR else CAPTURE_LEAD_MS
        self.bl_lead = 0 if tool == TOOL_MIRROR else BACKLIGHT_LEAD_MS
        self.capture_steps = [i for i in range(len(steps))
                              if steps[i].read == READ_CAPTURE]
        self.samples = [0] * MAX_STEP_SAMPLES
        self.burst = [0] * FLICKER_READS
        self.cap_t = [0] * CAPTURE_MAX
        self.cap_v = [0] * CAPTURE_MAX
        self.block = SymbolBlock()
        self.eye = EyeBlock()
        # Per averaging depth: groups seen, running mean, running sum of
        # squared deviations (Welford's, so float32 keeps its precision).
        depths = len(AVERAGE_DEPTHS)
        self.avg_n = [0] * depths
        self.avg_mean = [0.0] * depths
        self.avg_m2 = [0.0] * depths
        self.start(0)

    def start(self, epoch):
        self.epoch = epoch
        self.index = 0
        self.begun = False
        self.count = 0
        self.burst_taken = False
        self.capture_index = 0
        self.pending = None
        self.blocked = False
        self.frame_ms = 0
        self.gcd = 0
        self.summary = {}       # (test, key) -> (median, p2p, sd) or None
        self.bursts = {}        # flicker key -> sorted reads or None
        self.taus = {"rise": [], "fall": [], "mid_rise": []}
        # backlight_step: per direction, the averaged curve's bins and how
        # many captures went into it. Built once: the bins are reused.
        if not hasattr(self, "bl_sums"):
            self.bl_sums = {}
            self.bl_counts = {}
            for key in ("rise", "fall", "mid_rise"):
                self.bl_sums[key] = [0.0] * CURVE_BINS
                self.bl_counts[key] = [0] * CURVE_BINS
        for key in ("rise", "fall", "mid_rise"):
            sums = self.bl_sums[key]
            counts = self.bl_counts[key]
            for b in range(CURVE_BINS):
                sums[b] = 0.0
                counts[b] = 0
        self.bl_steps = {"rise": 0, "fall": 0, "mid_rise": 0}
        # Per direction: each capture's swing (final - initial, counts), and
        # how many were left out, by reason -- so a direction that found
        # nothing still says what the sensor did.
        self.bl_swings = {"rise": [], "fall": [], "mid_rise": []}
        self.bl_skipped = {}
        for key in ("rise", "fall", "mid_rise"):
            self.bl_skipped[key] = {SKIP_FLAT: 0, SKIP_NO_EDGE: 0, SKIP_GAP: 0}
        self.bl_read_us = []    # each capture's mean spacing between reads
        self.loops = {}         # loopback: rung -> result object
        self.loop_pending = None
        self.latencies = []
        self.bounded = 0        # rises already past T_low at the first read
        self.blinds = []        # how long each present call blocked, ms
        self.symbols = {}       # (scheme, rung) -> result object
        self.eyes = {}          # (sequence name, rung) -> result object
        self.averages = {}      # level name -> result object
        self.active = None      # the SymbolBlock or EyeBlock receiving
        self.avg_frames = 0
        self.finished = False

    def note(self, value):
        """Counts a read taken elsewhere towards adc_step."""
        self.gcd = gcd(self.gcd, value)

    def _read(self):
        value = self.light()
        self.gcd = gcd(self.gcd, value)
        return value

    def current_step(self):
        if self.index < len(self.steps):
            return self.steps[self.index]
        return None

    def frame(self, now, frame_ms):
        """Advances to `now` and takes this frame's reads. Returns the kind."""
        self.frame_ms = frame_ms
        if self.finished:
            return READ_NONE
        elapsed = time.ticks_diff(now, self.epoch)
        if elapsed < 0:
            return READ_NONE
        steps = self.steps
        n = len(steps)
        while self.index < n:
            if not self.begun:
                self._begin(self.index)
                self.begun = True
            if elapsed < self.starts[self.index] + steps[self.index].ms:
                break
            self._end(self.index)
            self.index += 1
            self.begun = False

        kind = READ_NONE
        value = None
        block = self.active
        if block is not None and block.active:
            value, done = block.take(now, self.light)
            self.gcd = gcd(self.gcd, value)
            kind = READ_SAMPLE
            if done:
                self._store(block)

        if self.index < n:
            step = steps[self.index]
            offset = elapsed - self.starts[self.index]
            if step.read == READ_SAMPLES:
                if (GUARD_MS <= offset < step.ms - GUARD_MS
                        and self.count < step.count):
                    if value is None:
                        value = self._read()
                    self.samples[self.count] = value
                    self.count += 1
                    kind = READ_SAMPLE
            elif (step.read == READ_BURST and not self.burst_taken
                  and offset >= step.ms // 2):
                light = self.light
                burst = self.burst
                for i in range(step.count):
                    burst[i] = light()
                g = self.gcd
                for i in range(step.count):
                    g = gcd(g, burst[i])
                self.gcd = g
                self.burst_taken = True
                self.blocked = True
                kind = READ_BURST
            elif (step.read == READ_AVERAGE
                  and GUARD_MS <= offset < step.ms - GUARD_MS):
                self._average_burst(step.count)
                kind = READ_BURST
        elif block is None or not block.active:
            self.finished = True

        # The capture itself waits for presented(): in mirror mode it has to
        # start after the frame showing the new field has gone up.
        caps = self.capture_steps
        while (self.capture_index < len(caps) and elapsed
               >= self.starts[caps[self.capture_index]]
               - self._lead(caps[self.capture_index])):
            self.pending = caps[self.capture_index]
            self.capture_index += 1
        return kind

    def _lead(self, index):
        if self.steps[index].test == "backlight_step":
            return self.bl_lead
        return self.lead

    def presented(self, present_us, returned_us=None):
        """Runs the settle capture due this frame, if any. Blocks while it does.

        `present_us` is ticks_us taken just before the frame was presented,
        `returned_us` just after the present call returned, if known: the
        stretch between them is blind -- Python cannot read while the panel is
        being written -- and an edge inside it can only be bounded.
        """
        if self.pending is None:
            return
        if returned_us is not None:
            self.blinds.append(time.ticks_diff(returned_us, present_us) / 1000.0)
        index = self.pending
        self.pending = None
        count = self._capture(present_us, 0, self._lead(index))
        self._fit(index, count)

    @property
    def loopback_due(self):
        """A loopback block is waiting for loopback() on this frame."""
        return self.loop_pending is not None

    def loopback(self, set_backlight):
        """Runs the loopback block due, blocking for its length."""
        if self.loop_pending is None:
            return
        step = self.steps[self.loop_pending]
        self.loop_pending = None
        if step.key.startswith("off_"):
            self.loops[step.key] = dim_loopback_block(
                step.slot_ms, self.light, self.ticks_us, set_backlight)
        else:
            self.loops[step.key] = loopback_block(
                step.slot_ms, self.levels(SCHEME_BACKLIGHT), self.light,
                self.ticks_us, set_backlight)
        self.blocked = True

    @property
    def change_due(self):
        """The capture due is one made by changed(), not after a present."""
        return (self.pending is not None
                and self.steps[self.pending].test == "backlight_step")

    def changed(self, change):
        """Calls `change` inside the capture due this frame. Blocks while it does.

        For a change the sensor sees at once -- display.backlight() -- rather
        than at the next present. Reads for CHANGE_LEAD_US, calls `change`,
        then reads on to the end of the window; times are from the call. With
        nothing due, it only calls `change`.
        """
        if self.pending is None:
            change()
            return
        index = self.pending
        self.pending = None
        ticks_us = self.ticks_us
        light = self.light
        cap_t = self.cap_t
        cap_v = self.cap_v
        count = 0
        start = ticks_us()
        while count < CAPTURE_MAX:
            t = time.ticks_diff(ticks_us(), start)
            if t >= CHANGE_LEAD_US:
                break
            cap_t[count] = t
            cap_v[count] = light()
            count += 1
        at_us = ticks_us()
        change()
        # Straight back to reading: the step starts now. The lead-in is put on
        # the call's timeline afterwards. Doing it here, as the first version
        # did, was ~80 passes of Python between the change and the next read
        # -- a few hundred µs blind exactly where a fast step crosses half
        # way -- and every capture that crossed in it was dropped as a gap:
        # 4 of 10 lit falls on 2026-10-05d, all of them on 2026-10-05c.
        lead_in = count
        count = self._capture(at_us, count, self._lead(index))
        at = time.ticks_diff(at_us, start)
        for i in range(lead_in):
            cap_t[i] -= at
        self._fit(index, count)

    def _capture(self, origin_us, count, lead):
        """Reads into cap_t/cap_v from `count` on, timed from `origin_us`, to
        the end of the window, CAPTURE_MS past `lead`. Returns the new count."""
        window = (CAPTURE_MS + lead) * 1000
        light = self.light
        ticks_us = self.ticks_us
        cap_t = self.cap_t
        cap_v = self.cap_v
        while count < CAPTURE_MAX:
            t = time.ticks_diff(ticks_us(), origin_us)
            if t > window:
                break
            cap_t[count] = t
            cap_v[count] = light()
            count += 1
        self.blocked = True
        return count

    def _fit(self, index, count):
        cap_t = self.cap_t
        cap_v = self.cap_v
        g = self.gcd
        for i in range(count):
            g = gcd(g, cap_v[i])
        self.gcd = g

        key = self.steps[index].key
        if key is None:
            return
        if self.steps[index].test == "backlight_step":
            self._fit_backlight(key, count)
            return
        tau = settle_tau(cap_t, cap_v, count, self.gcd)
        if tau is not None:
            self.taus[key].append(tau)
        if self.tool == TOOL_MIRROR and key == "rise":
            levels = self.levels(SCHEME_FIELDS)
            if levels is not None:
                latency = edge_latency_ms(cap_t, cap_v, count, levels.t_low)
                if latency is not None:
                    self.latencies.append(latency)
                    if count and cap_v[0] >= levels.t_low:
                        self.bounded += 1

    def _fit_backlight(self, key, count):
        cap_t = self.cap_t
        cap_v = self.cap_v
        if count > 1:
            self.bl_read_us.append((cap_t[count - 1] - cap_t[0]) / (count - 1))
        levels = step_levels(cap_v, count, self.gcd)
        skipped = self.bl_skipped[key]
        if levels is None:
            skipped[SKIP_FLAT] += 1
            return
        initial, final, floor = levels
        self.bl_swings[key].append(final - initial)
        if not step_moved(initial, final, floor):
            skipped[SKIP_FLAT] += 1
            return
        t50, reason = half_time(cap_t, cap_v, count, initial, final, floor)
        if t50 is None:
            skipped[reason] += 1
            return
        # The mirror made the change itself, so its captures share an origin
        # and line up as they are. A receiver's origin is its estimate of the
        # peer's timeline, a frame or two out, so each capture is lined up on
        # its own half way crossing instead.
        align = 0 if self.tool == TOOL_MIRROR else t50
        swing = final - initial
        sums = self.bl_sums[key]
        counts = self.bl_counts[key]
        for i in range(count):
            b = int(cap_t[i] - align + CURVE_LEAD_US) // CURVE_BIN_US
            if 0 <= b < CURVE_BINS:
                sums[b] += (cap_v[i] - initial) / swing
                counts[b] += 1
        self.bl_steps[key] += 1

    def _begin(self, index):
        step = self.steps[index]
        self.count = 0
        self.burst_taken = False
        if step.read == READ_LOOPBACK:
            self.loop_pending = index
        if step.read == READ_SYMBOLS or step.read == READ_EYE:
            if self.active is not None and self.active.active:
                self.active.finish()
                self._store(self.active)
            levels = self.levels(step.scheme)
            block_start = time.ticks_add(self.epoch, self.starts[index])
            if levels is None:
                found = self.symbols if step.read == READ_SYMBOLS else self.eyes
                found[step.key] = {"error": "no levels"}
            elif step.read == READ_SYMBOLS:
                self.block.start(step.key, block_start, step.slot_ms, levels,
                                 self.frame_ms)
                self.active = self.block
            else:
                self.eye.start(step.key, block_start, step.slot_ms, levels,
                               step.symbols[SYNC_LEAD_SLOTS + 1:])
                self.active = self.eye
        elif step.read == READ_AVERAGE:
            for i in range(len(AVERAGE_DEPTHS)):
                self.avg_n[i] = 0
                self.avg_mean[i] = 0.0
                self.avg_m2[i] = 0.0
            self.avg_frames = 0

    def _store(self, block):
        found = self.symbols if block is self.block else self.eyes
        found[block.key] = block.result()

    def _average_burst(self, count):
        """One frame's burst, folded into every averaging depth's statistics."""
        light = self.light
        burst = self.burst
        g = self.gcd
        for i in range(count):
            burst[i] = light()
            g = gcd(g, burst[i])
        self.gcd = g
        self.avg_frames += 1
        for d in range(len(AVERAGE_DEPTHS)):
            depth = AVERAGE_DEPTHS[d]
            start = 0
            while start + depth <= count:
                total = 0
                for i in range(start, start + depth):
                    total += burst[i]
                value = total / depth
                n = self.avg_n[d] + 1
                delta = value - self.avg_mean[d]
                self.avg_mean[d] += delta / n
                self.avg_m2[d] += delta * (value - self.avg_mean[d])
                self.avg_n[d] = n
                start += depth

    def _end(self, index):
        step = self.steps[index]
        if step.read == READ_SAMPLES:
            n = self.count
            if n:
                self.summary[(step.test, step.key)] = (
                    median(self.samples, n), peak_to_peak(self.samples, n),
                    sd(self.samples, n))
            else:
                self.summary[(step.test, step.key)] = None
            self.count = 0
        elif step.read == READ_AVERAGE:
            if self.avg_frames:
                spread = {}
                for d in range(len(AVERAGE_DEPTHS)):
                    n = self.avg_n[d]
                    spread[str(AVERAGE_DEPTHS[d])] = round(
                        math.sqrt(self.avg_m2[d] / n), 2) if n else None
                self.averages[step.key] = {
                    "mean": round(self.avg_mean[0], 1), "sd": spread,
                    "bursts": self.avg_frames}
            else:
                self.averages[step.key] = {"error": "no readings"}
        elif step.read == READ_BURST:
            if self.burst_taken:
                self.bursts[step.key] = sorted(self.burst[:step.count])
            else:
                self.bursts[step.key] = None
            self.burst_taken = False

    def held(self, test, key):
        return self.summary.get((test, key))

    def levels(self, scheme):
        """This run's measured levels for `scheme`, fitted, or None."""
        found = []
        for _, name in LEVEL_NAMES:
            held = self.held("levels", (scheme, name))
            if held is None:
                return None
            found.append(held[0])
        if found[2] <= found[0]:
            return None
        levels = Levels()
        levels.fit(found[0], found[1], found[2])
        return levels

    # --- results ---

    def results(self, frame_result):
        """The per-condition object, shaped as the spec's measurements file."""
        if self.active is not None and self.active.active:
            self.active.finish()
            self._store(self.active)
        out = {}
        out["ambient"] = self._ambient()
        step = self.gcd
        out["adc_step"] = step if step else {"error": "no nonzero reading"}
        if not step:
            step = SENSOR_STEP
        out["levels"] = self._levels_result()
        out["noise"] = self._noise_result()
        out["gamma_ramp"] = self._gamma_result()
        out["backlight_curve"] = self._curve_result()
        out["bleed"] = self._bleed_result()
        out["flicker"] = self._flicker_result(step)
        out["settle"] = self._settle_result()
        out["backlight_step"] = self._backlight_step_result()
        out["frame_timing"] = frame_result
        if self.tool == TOOL_MIRROR:
            out["loopback"] = self._loopback_result()
            out["edge_latency"] = self._latency_result()
        else:
            out["symbol_errors"] = self._symbols_result()
            out["averaging"] = self._averaging_result()
            out["eye"] = self._eye_result()
        return out

    def failed_results(self, message, frame_result):
        """Every test this badge was due to measure failed with `message`."""
        tests = MIRROR_TESTS if self.tool == TOOL_MIRROR else SECOND_BADGE_TESTS
        out = {}
        for test in tests:
            if test == "frame_timing":
                out[test] = frame_result
            else:
                out[test] = {"error": message}
        return out

    def _ambient(self):
        held = self.held("ambient", "ambient")
        if held is None:
            return {"error": "no readings"}
        return held[0]

    def _scheme_levels(self, scheme):
        found = {}
        for _, name in LEVEL_NAMES:
            held = self.held("levels", (scheme, name))
            if held is None:
                return None
            found[name] = held
        return found

    def _levels_result(self):
        out = {}
        for scheme in (SCHEME_FIELDS, SCHEME_BACKLIGHT):
            found = self._scheme_levels(scheme)
            if found is None:
                out[scheme] = {"error": "no readings"}
                continue
            black = found["black"][0]
            mid = found["mid"][0]
            white = found["white"][0]
            span = white - black
            if span <= 0:
                out[scheme] = {"error": "white not above black"}
                continue
            out[scheme] = {"black": black, "mid": mid, "white": white,
                           "span": span,
                           "mid_share": round((mid - black) / span, 3)}
        return out

    def _noise_result(self):
        out = {}
        for scheme in (SCHEME_FIELDS, SCHEME_BACKLIGHT):
            found = self._scheme_levels(scheme)
            if found is None:
                out[scheme] = {"error": "no readings"}
                continue
            entry = {}
            for _, name in LEVEL_NAMES:
                entry[name] = {"p2p": found[name][1],
                               "sd": round(found[name][2], 1)}
            out[scheme] = entry
        return out

    def _gamma_result(self):
        out = {}
        fractions = []
        readings = []
        for percent in RAMP_PERCENTS:
            held = self.held("gamma_ramp", str(percent))
            if held is None:
                return {"error": "no readings at %d%%" % percent}
            out[str(percent)] = held[0]
            fractions.append(ramp_value(percent) / 255)
            readings.append(held[0])
        gamma = fit_gamma(fractions, readings)
        if gamma is None:
            return {"error": "no gamma fit"}
        out["gamma"] = round(gamma, 2)
        return out

    def _curve_result(self):
        out = {}
        for tenths in range(CURVE_TENTHS):
            key = curve_key(tenths)
            held = self.held("backlight_curve", key)
            if held is None:
                return {"error": "no readings at %s" % key}
            out[key] = held[0]
        return out

    def _bleed_result(self):
        on = self.held("bleed", "black_on")
        off = self.held("bleed", "black_off")
        if on is None or off is None:
            return {"error": "no readings"}
        return {"black_on": on[0], "black_off": off[0]}

    def _flicker_result(self, step):
        out = {}
        for key, _ in FLICKER_BACKLIGHTS:
            values = self.bursts.get(key)
            if not values:
                return {"error": "no burst at %s" % key}
            out[key] = flicker_result(values, step)
        return out

    def _settle_result(self):
        # Each direction stands alone: a middle one or two ADC steps above
        # black leaves nothing to fit, and that must not throw away a rise and
        # a fall that fitted -- it did, in the first hardware run.
        out = {}
        slowest = None
        for key, name in (("rise", "rise_tau_ms"), ("fall", "fall_tau_ms"),
                          ("mid_rise", "mid_rise_tau_ms")):
            taus = self.taus[key]
            if not taus:
                out[name] = {"error": "no %s transition fitted"
                             % key.replace("_", " ")}
                continue
            tau = median(taus)
            slowest = tau if slowest is None else max(slowest, tau)
            out[name] = round(tau, 1)
        if slowest is None:
            return {"error": "no transition fitted"}
        # From the directions that fitted, so it can only understate.
        out["settle_90_ms"] = round(SETTLE_90_FACTOR * slowest, 1)
        return out

    def _backlight_step_result(self):
        # As settle: each direction stands alone. t50 is from the
        # display.backlight() call, so only a badge that made the call -- the
        # mirror tool -- can report it; a receiver's time origin is its own
        # estimate of the peer's.
        out = {}
        for key in ("rise", "fall", "mid_rise"):
            found = self.bl_steps[key]
            sums = self.bl_sums[key]
            counts = self.bl_counts[key]
            t10 = curve_crossing(sums, counts, 0.1)
            t90 = curve_crossing(sums, counts, 0.9)
            name = key.replace("_", " ")
            # What every capture saw, kept or not, and why any were left out.
            swings = self.bl_swings[key]
            seen = {"skipped": dict(self.bl_skipped[key]),
                    "swing": median(swings) if swings else None}
            if not found:
                seen["error"] = "no %s step found" % name
                out[key] = seen
                continue
            if t10 is None or t90 is None:
                seen["error"] = "%s curve never reached %d%%" % (
                    name, 10 if t10 is None else 90)
                out[key] = seen
                continue
            entry = {"transitions": found,
                     "t10_90_ms": round(max(0.0, t90 - t10) / 1000.0, 2),
                     "skipped": seen["skipped"], "swing": seen["swing"]}
            if self.tool == TOOL_MIRROR:
                t50 = curve_crossing(sums, counts, 0.5)
                entry["t10_ms"] = round(t10 / 1000.0, 2)
                entry["t50_ms"] = round(t50 / 1000.0, 2) if t50 is not None else None
                entry["t90_ms"] = round(t90 / 1000.0, 2)
            out[key] = entry
        # No single error even when nothing was found: each direction's says
        # why, which a single one would throw away.
        out["bin_us"] = CURVE_BIN_US
        if self.bl_read_us:
            out["read_us"] = round(median(self.bl_read_us), 1)
        return out

    def _loopback_result(self):
        out = {}
        for rung in LOOPBACK_RUNGS:
            out[str(rung)] = self.loops.get(str(rung), {"error": "not run"})
        for rung in DIM_RUNGS:
            key = "off_%d" % rung
            out[key] = self.loops.get(key, {"error": "not run"})
        return out

    def _latency_result(self):
        if self.levels(SCHEME_FIELDS) is None:
            return {"error": "no levels"}
        found = self.latencies
        if not found:
            return {"error": "no rise crossed T_low"}
        total = 0.0
        for value in found:
            total += value
        # A rise already past T_low at the first read gives that read's time:
        # an upper bound, not a latency. "upper_bounds" says how many were;
        # when it equals "rises", every figure here is only a bound, and
        # "blind_ms" -- how long the present call held the CPU -- is why.
        out = {"mean_ms": round(total / len(found), 1),
               "min_ms": round(min(found), 1),
               "max_ms": round(max(found), 1),
               "rises": len(found),
               "upper_bounds": self.bounded}
        if self.blinds:
            out["blind_ms"] = round(median(list(self.blinds)), 1)
        return out

    def _averaging_result(self):
        """Each fields level's mean and spread at every depth, then the swing
        and, per depth, how many of the wider spread the narrower of the two
        gaps between adjacent levels is."""
        out = {}
        found = []
        for _, name in LEVEL_NAMES:
            entry = self.averages.get(name, {"error": "no readings"})
            out[name] = entry
            if "error" not in entry:
                found.append(entry)
        if len(found) == 3:
            black, mid, white = found
            span = white["mean"] - black["mean"]
            out["span"] = round(span, 1)
            if span > 0:
                out["mid_share"] = round((mid["mean"] - black["mean"]) / span, 3)
            separation = {}
            for depth in AVERAGE_DEPTHS:
                key = str(depth)
                worst = None
                for lower, upper in ((black, mid), (mid, white)):
                    spread = max(lower["sd"][key] or 0, upper["sd"][key] or 0)
                    gap = upper["mean"] - lower["mean"]
                    ratio = gap / spread if spread else None
                    if ratio is None:
                        worst = None
                        break
                    if worst is None or ratio < worst:
                        worst = ratio
                separation[key] = None if worst is None else round(worst, 1)
            out["separation"] = separation
        return out

    def _eye_result(self):
        out = {}
        for name, _ in EYE_SEQUENCES:
            entry = {}
            for rung in EYE_RUNGS:
                entry[str(rung)] = self.eyes.get((name, rung),
                                                 {"error": "not received"})
            out[name] = entry
        return out

    def _symbols_result(self):
        out = {}
        for scheme in (SCHEME_FIELDS, SCHEME_BACKLIGHT):
            entry = {}
            for rung in SYMBOL_RUNGS:
                entry[str(rung)] = self.symbols.get(
                    (scheme, rung), {"error": "not received"})
            out[scheme] = entry
        return out


# --- runs -------------------------------------------------------------------

class Cursor:
    """Which step of a schedule holds a given time, found without allocating."""

    def __init__(self, steps):
        self.steps = steps
        self.starts = step_starts(steps)
        self.index = 0

    def find(self, elapsed):
        if self.index >= len(self.steps) or elapsed < self.starts[self.index]:
            self.index = 0
        while (self.index < len(self.steps)
               and elapsed >= self.starts[self.index] + self.steps[self.index].ms):
            self.index += 1
        return self.index

    def step(self, elapsed):
        index = self.find(elapsed)
        if 0 <= elapsed and index < len(self.steps):
            return self.steps[index]
        return None

    def field(self, elapsed):
        step = self.step(elapsed)
        if step is None:
            return IDLE_FIELD
        return step.field_at(elapsed - self.starts[self.index])


class _Run:
    def __init__(self, steps, tool, light, ticks_us, temp=None):
        self.steps = steps
        self.tool = tool
        self.light = light
        # Degrees C, or None where the firmware has no sensor to read.
        self.temp = temp
        self.temps = {}
        self.recorder = Recorder(steps, tool, light, ticks_us)
        self.cursor = Cursor(steps)
        self.stats = FrameStats()
        self.tests = MIRROR_TESTS if tool == TOOL_MIRROR else SECOND_BADGE_TESTS
        self.test_count = len(self.tests)
        self.test_name = None
        self.test_number = 0
        self.pixels, self.backlight = IDLE_FIELD
        self.done = False
        self.skip_frame = False

    def _count_frame(self, frame_ms):
        # A frame that followed a capture or a burst measures the reading, not
        # the frame loop; leaving it in would make max_ms the capture length.
        if self.skip_frame:
            self.skip_frame = False
        else:
            self.stats.add(frame_ms)

    def _note_blocking(self):
        if self.recorder.blocked:
            self.recorder.blocked = False
            self.skip_frame = True

    def _show(self, field):
        self.pixels = field[0]
        self.backlight = field[1]

    def _name(self, step):
        if step is None:
            self.test_name = None
            self.test_number = 0
        elif step.test != self.test_name:
            self.test_name = step.test
            self.test_number = self.tests.index(step.test) + 1
            self._note_temp(self._temp_key(step.test))

    def _temp_key(self, test):
        return test

    def _note_temp(self, key):
        """The chip's temperature now, under `key`, once: a test seen again
        keeps its first reading. Taken on a frame where a test begins, which
        is never inside a capture."""
        if self.temp is None or key in self.temps:
            return
        self.temps[key] = self.temp()

    def temperatures(self):
        """{"start": C, <test>: C, ..., "end": C}, the end read now."""
        if self.temp is None:
            return {"error": "no temperature sensor"}
        out = {}
        for key in self.temps:
            out[key] = self.temps[key]
        out["end"] = self.temp()
        return out

    @property
    def capture_due(self):
        """A settle capture is waiting for this frame to be presented."""
        return self.recorder.pending is not None

    @property
    def change_due(self):
        """The capture due is a backlight step's: make the change through
        changed(), not by presenting."""
        return self.recorder.change_due

    @property
    def loopback_due(self):
        """A loopback block is due: call loopback() this frame."""
        return self.recorder.loopback_due

    def loopback(self, set_backlight):
        """Sends and reads back the loopback block due, blocking for it;
        `set_backlight` is display.backlight()."""
        self.recorder.loopback(set_backlight)
        self._note_blocking()

    def changed(self, change):
        """Call on a frame with change_due, in place of setting the backlight:
        `change` sets it, and the capture runs around the call."""
        self.recorder.changed(change)
        self._note_blocking()

    def presented(self, present_us, returned_us=None):
        """Call right after presenting a frame: ticks_us from just before the
        present, and from just after it returned if known."""
        self.recorder.presented(present_us, returned_us)
        self._note_blocking()


class MirrorRun(_Run):
    """One condition in mirror mode: shows the schedule and measures it."""

    def __init__(self, light, ticks_us=None, temp=None):
        _Run.__init__(self, MIRROR_SCHEDULE, TOOL_MIRROR, light, ticks_us, temp)
        self.epoch = 0

    def begin(self, now):
        self.epoch = now
        self.recorder.start(now)
        self.stats.reset()
        self.skip_frame = False
        self.done = False
        self.temps = {}
        self._note_temp("start")

    def frame(self, now, frame_ms):
        self._count_frame(frame_ms)
        kind = self.recorder.frame(now, frame_ms)
        self._note_blocking()
        elapsed = time.ticks_diff(now, self.epoch)
        self._show(self.cursor.field(elapsed))
        self._name(self.cursor.step(elapsed))
        if self.recorder.finished:
            self.done = True
        return kind

    def results(self):
        out = self.recorder.results(self.stats.result())
        out["temperature_c"] = self.temperatures()
        return out


PHASE_NOISE = "noise"           # first second: measuring a still screen
PHASE_CALLING = "calling"       # calling the peer, then the go
PHASE_ANSWERING = "answering"   # answering the peer's call, until its go
PHASE_SENDING = "sending"       # showing the schedule, then its closing black
PHASE_MEASURING = "measuring"   # reading the peer's schedule
PHASE_DONE = "done"


class SecondBadgeRun(_Run):
    """One condition in 2nd-badge mode: both turns, from one badge's side.

    The noise second, then two turns. In the first the first badge calls and
    the second answers; after the go the first sends and the second measures.
    In the second turn the roles swap: the second badge calls once the first
    turn's closing black has ended, which it knows from the first go, and the
    first -- listening since its own schedule ended -- answers. Both finish
    when the second turn's closing black ends. A handshake with no go within
    HANDSHAKE_TIMEOUT_MS ends the condition for this badge.
    """

    def __init__(self, first, light, ticks_us=None, temp=None):
        _Run.__init__(self, SECOND_BADGE_SCHEDULE, TOOL_SECOND_BADGE, light,
                      ticks_us, temp)
        self.first = first
        self.role = "first" if first else "second"
        self.detector = PulseDetector()
        self.noise = [0] * NOISE_CAPACITY
        self.noise_count = 0
        # The noise second's spread and the threshold set from it: kept for
        # the app to show, and so a failed handshake can be explained.
        self.pulse_noise = None
        self.pulse_threshold = PULSE_MIN_SWING
        self.phase = PHASE_DONE
        self.no_handshake = False
        self.measured = False
        self.turn = 1
        self.begin_at = 0
        self.handshake_at = 0
        self.epoch = 0
        self.ended_at = 0
        self.frame_ms = 0
        # Calling.
        self.cycle = -1
        self.pulse_start = None
        self.pulse_cycle = -1
        self.prev_pulse_start = None
        self.prev_pulse_cycle = -1
        self.replies = 0
        self.answered_cycle = -2
        self.go_start = None
        # Answering.
        self.answer_at = None
        self.answer_start = None
        self.answer_shown = False
        self.last_call = None
        self.calls = 0          # call pulses seen a period apart, in a row

    @property
    def sending(self):
        return self.phase == PHASE_SENDING

    def begin(self, now):
        self.stats.reset()
        self.skip_frame = False
        self.done = False
        self.no_handshake = False
        self.measured = False
        self.recorder.start(now)
        self.turn = 1
        self.temps = {}
        self._note_temp("start")
        # Every run opens on its noise second; see NOISE_MS.
        self.phase = PHASE_NOISE
        self.begin_at = now
        self.noise_count = 0

    def _calls(self):
        return self.first == (self.turn == 1)

    def _handshake(self, now):
        self.detector.reset()
        self.handshake_at = now
        self.go_start = None
        if self._calls():
            self.phase = PHASE_CALLING
            self.cycle = -1
            self.pulse_start = None
            self.pulse_cycle = -1
            self.prev_pulse_start = None
            self.prev_pulse_cycle = -1
            self.replies = 0
            self.answered_cycle = -2
        else:
            self.phase = PHASE_ANSWERING
            self.answer_at = None
            self.answer_start = None
            self.answer_shown = False
            self.last_call = None
            self.calls = 0

    def _set_threshold(self):
        self.pulse_noise = noise_spread(self.noise, self.noise_count)
        self.pulse_threshold = pulse_threshold(self.pulse_noise)
        self.detector.threshold = self.pulse_threshold

    def _read(self):
        value = self.light()
        self.recorder.note(value)
        return value

    def _noise(self, now):
        """One read of the still screen in the run's first second."""
        value = self._read()
        if self.noise_count < NOISE_CAPACITY:
            self.noise[self.noise_count] = value
            self.noise_count += 1
        if time.ticks_diff(now, self.begin_at) >= NOISE_MS:
            self._set_threshold()
            self._handshake(now)

    def _next_cycle(self, now):
        """At each period's start: the next call pulse, or the go instead."""
        cycle = time.ticks_diff(now, self.handshake_at) // PULSE_PERIOD_MS
        if cycle == self.cycle:
            return
        self.cycle = cycle
        if self.replies >= REPLIES_NEEDED and self.answered_cycle == cycle - 1:
            # The go goes where the next pulse would have.
            self.go_start = now
            return
        # Timed from when this badge drew it, not from the cycle's ideal
        # start: a late frame moves the pulse.
        self.prev_pulse_start = self.pulse_start
        self.prev_pulse_cycle = self.pulse_cycle
        self.pulse_start = now
        self.pulse_cycle = cycle

    def _answered(self, rise):
        """Counts a pulse seen at `rise` if it answers one of ours in time."""
        start = self.pulse_start
        cycle = self.pulse_cycle
        if start is None:
            return
        if time.ticks_diff(rise, start) < 0:
            # Seen after our next pulse had started: it answers the one before.
            start = self.prev_pulse_start
            cycle = self.prev_pulse_cycle
            if start is None:
                return
        offset = time.ticks_diff(rise, start)
        if not REPLY_EARLIEST_MS <= offset <= REPLY_LATEST_MS:
            return
        if cycle == self.answered_cycle:
            return
        if cycle == self.answered_cycle + 1:
            self.replies += 1
        else:
            # A cycle went by without an answer: count again from this one.
            self.replies = 1
        self.answered_cycle = cycle

    def _heard(self, event):
        """The answerer's read: an answer to every call pulse, until a go."""
        detector = self.detector
        if event == PULSE_GO:
            if (self.calls >= REPLIES_NEEDED
                    and self._a_period_after_the_last_call(detector.rise)):
                self._measure(detector.fall)
        elif event == PULSE_SEEN:
            rise = detector.rise
            # Our own answer, echoed off the other badge's glass, is no call.
            if (self.answer_start is not None and -PULSE_HIGH_MS
                    <= time.ticks_diff(rise, self.answer_start) <= ECHO_MS):
                return
            if self._a_period_after_the_last_call(rise):
                self.calls += 1
            else:
                self.calls = 1
            self.last_call = rise
            # The rise is seen late by half a frame and the panel's settling;
            # take that off, as the protocol's phase lock does.
            self.answer_at = time.ticks_add(
                rise, REPLY_DELAY_MS - edge_lag(self.frame_ms))
            self.answer_shown = False

    def _a_period_after_the_last_call(self, rise):
        return (self.last_call is not None and PERIOD_MIN_MS
                <= time.ticks_diff(rise, self.last_call) <= PERIOD_MAX_MS)

    def _send(self, now):
        self.phase = PHASE_SENDING
        self.epoch = time.ticks_add(now, GO_GAP_MS)

    def _measure(self, fall):
        self.phase = PHASE_MEASURING
        # adc_step covers every read of the condition -- the noise second's
        # and the handshakes' too -- so the gcd outlives the restart.
        g = self.recorder.gcd
        self.recorder.start(time.ticks_add(fall, GO_GAP_MS))
        self.recorder.gcd = g

    def _turn_ended(self, now):
        if self.turn == 1:
            self.turn = 2
            self._handshake(now)
        else:
            self._finish()

    def _finish(self):
        self.phase = PHASE_DONE
        self.done = True

    def frame(self, now, frame_ms):
        self._count_frame(frame_ms)
        self.frame_ms = frame_ms
        kind = READ_NONE
        phase = self.phase

        if self.no_handshake:
            # Gave up: hold the screen until the message can have been read.
            if time.ticks_diff(now, self.ended_at) >= NO_ANSWER_HOLD_MS:
                self._finish()
        elif phase == PHASE_NOISE:
            self._noise(now)
            kind = READ_SAMPLE
        elif phase == PHASE_CALLING or phase == PHASE_ANSWERING:
            event = self.detector.push(now, self._read())
            kind = READ_SAMPLE
            if (self.go_start is None and time.ticks_diff(
                    now, self.handshake_at) >= HANDSHAKE_TIMEOUT_MS):
                self.no_handshake = True
                self.ended_at = now
            elif phase == PHASE_ANSWERING:
                self._heard(event)
            else:
                if event == PULSE_SEEN:
                    self._answered(self.detector.rise)
                if (self.go_start is not None
                        and time.ticks_diff(now, self.go_start) >= GO_MS):
                    # The first frame back on black is the go's fall: time
                    # zero, as this badge drew it.
                    self._send(now)
        elif phase == PHASE_MEASURING:
            kind = self.recorder.frame(now, frame_ms)
            self._note_blocking()
            if (time.ticks_diff(now, self.recorder.epoch)
                    >= SECOND_BADGE_SCHEDULE_MS + TURN_END_MS):
                self.measured = True
                self._turn_ended(now)
        elif phase == PHASE_SENDING:
            # The first badge listens from the end of its own schedule; the
            # second, sending last, finishes when its closing black ends.
            end = SECOND_BADGE_SCHEDULE_MS
            if not self.first:
                end += TURN_END_MS
            if time.ticks_diff(now, self.epoch) >= end:
                self._turn_ended(now)

        self._display(now)
        return kind

    def _display(self, now):
        phase = self.phase
        if self.no_handshake:
            self._show(IDLE_FIELD)
            self._name(None)
        elif phase == PHASE_CALLING:
            if self.go_start is None:
                self._next_cycle(now)
            elapsed = time.ticks_diff(now, self.handshake_at)
            if (self.go_start is not None
                    or elapsed % PULSE_PERIOD_MS < PULSE_HIGH_MS):
                self._show(WHITE_FULL)
            else:
                self._show(BLACK_FULL)
            self._name(None)
        elif phase == PHASE_ANSWERING:
            if (self.answer_at is not None and 0 <= time.ticks_diff(
                    now, self.answer_at) < PULSE_HIGH_MS):
                if not self.answer_shown:
                    self.answer_shown = True
                    self.answer_start = now
                self._show(WHITE_FULL)
            else:
                self._show(BLACK_FULL)
            self._name(None)
        elif phase == PHASE_SENDING:
            elapsed = time.ticks_diff(now, self.epoch)
            self._show(self.cursor.field(elapsed))
            self._name(self.cursor.step(elapsed))
        elif phase == PHASE_MEASURING:
            self._show(IDLE_FIELD)
            self._name(self.cursor.step(
                time.ticks_diff(now, self.recorder.epoch)))
        else:
            self._show(IDLE_FIELD)
            self._name(None)

    def _temp_key(self, test):
        # Both turns pass through every test: the one this badge sent is
        # keyed apart from the one it measured.
        return "sent_" + test if self.phase == PHASE_SENDING else test

    def results(self):
        if self.no_handshake and not self.measured:
            out = self.recorder.failed_results("no handshake",
                                               self.stats.result())
        else:
            out = self.recorder.results(self.stats.result())
        out["temperature_c"] = self.temperatures()
        return out


# --- the measurements file --------------------------------------------------

def tool_result(dark, lit, role=None):
    """One tool's entry: `role` for second_badge, then the conditions run."""
    out = {}
    if role is not None:
        out["role"] = role
    if dark is not None:
        out["dark"] = dark
    if lit is not None:
        out["lit"] = lit
    return out


def merge_results(file_obj, tool, result):
    """The file with `tool`'s entry replaced and every other key kept as it was."""
    out = {}
    if isinstance(file_obj, dict):
        for key in file_obj:
            out[key] = file_obj[key]
    out[tool] = result
    return out


# --- display measurements ---------------------------------------------------
# MicroPython's dict does not keep insertion order, and the file comes back
# from State as a fresh dict anyway, so "the file's own order" is rebuilt here:
# numeric keys by value (ramp percentages, backlight values, rungs), then the
# spec's keys in the spec's order, then anything else alphabetically.

KEY_ORDER = (
    "mirror", "second_badge", "frame_probe", "room_flicker", "role", "dark",
    "lit", "peak_hz", "peak_counts", "temp_c",
    "ambient", "adc_step", "levels", "noise", "gamma_ramp", "backlight_curve",
    "bleed", "flicker", "settle", "backlight_step", "frame_timing",
    "edge_latency",
    "symbol_errors", "averaging", "eye", "temperature_c", "start", "end",
    "2-level", "3-level",
    "fields", "backlight", "black", "mid", "white", "span", "mid_share",
    "p2p", "sd", "gamma", "black_on", "black_off", "min", "max", "verdict",
    "rise_tau_ms", "fall_tau_ms", "mid_rise_tau_ms", "settle_90_ms",
    "loopback", "lag_ms", "reads_per_s",
    "rise", "fall", "mid_rise", "transitions", "t10_90_ms", "t10_ms",
    "t50_ms", "t90_ms", "swing", "skipped", "flat", "no_edge", "gap",
    "bin_us", "read_us",
    "frames", "mean_ms", "min_ms", "max_ms", "sd_ms", "symbols", "errors",
    "errors_1", "errors_avg", "opening", "best_bin", "mean", "bursts",
    "separation",
    # probe frame timing (probe.py)
    "hz", "firmware", "version", "set_framerate", "refresh", "write",
    "breakdown", "test", "status", "frame", "clear", "field", "bands",
    "column", "lights", "measure", "present", "poll", "alloc_bytes",
    "reads", "loop", "read", "sample", "listen", "transfer", "calls", "per_s",
    "mean_us",
    "max_us", "sd_us", "alloc",
    "call_us", "pair_us", "quad_us", "octet_us", "gc", "screen",
    "collect", "alloc_kb", "free_kb", "frames_on", "frames_off",
    "error",
)


def ordered_keys(obj):
    numeric = []
    known = []
    other = []
    for key in obj:
        try:
            numeric.append((float(key), key))
            continue
        except ValueError:
            pass
        if key in KEY_ORDER:
            known.append((KEY_ORDER.index(key), key))
        else:
            other.append(key)
    numeric.sort()
    known.sort()
    other.sort()
    return [k for _, k in numeric] + [k for _, k in known] + other


def format_value(value):
    """`value` as JSON writes it: numbers bare, strings in double quotes."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        out = '"'
        for ch in value:
            if ch == '"' or ch == "\\":
                out += "\\" + ch
            elif ch == "\n":
                out += "\\n"
            elif ord(ch) < 32:
                out += "\\u%04x" % ord(ch)
            else:
                out += ch
        return out + '"'
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join([format_value(v) for v in value]) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join([format_value(k) + ": " + format_value(value[k])
                                for k in ordered_keys(value)]) + "}"
    return format_value(str(value))


def sections(obj, path=""):
    """(title, lines) for every object holding values that are not objects.

    An object holding both -- `mirror.dark` has ambient and adc_step beside
    levels -- gets a section for its own values, ahead of its children's.
    """
    out = []
    _collect(obj, path, out)
    return out


def _collect(obj, path, out):
    keys = ordered_keys(obj)
    lines = [key + ": " + format_value(obj[key]) for key in keys
             if not isinstance(obj[key], dict)]
    if lines:
        out.append((path or "measurements", lines))
    for key in keys:
        if isinstance(obj[key], dict):
            _collect(obj[key], path + "." + key if path else key, out)


def pages(file_obj, lines_per_page, wrap=None):
    """Sections split into screens of at most `lines_per_page` rows.

    Without `wrap` a line is a row. With it, `wrap(line)` gives the line's
    rows -- whatever the caller draws -- and a page holds rows: a line's rows
    stay on one page unless there are more of them than a page holds.
    """
    per = max(1, lines_per_page)
    out = []
    for title, lines in sections(file_obj):
        pages_before = len(out)
        page = []
        for line in lines:
            rows = wrap(line) if wrap else [line]
            if page and len(page) + len(rows) > per:
                _close(out, title, pages_before, page)
                page = []
            for row in rows:
                if len(page) == per:
                    _close(out, title, pages_before, page)
                    page = []
                page.append(row)
        if page:
            _close(out, title, pages_before, page)
    return out


def _close(out, title, pages_before, page):
    """A finished page: the section's first has its title, the rest (cont.)."""
    out.append((title if len(out) == pages_before else title + " (cont.)",
                page))
