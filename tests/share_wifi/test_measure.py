"""Dev Mode's measurement engine: schedule, calculations, runs, results file.

The calculations are checked on synthetic data with known answers. The runs
are checked end to end against a simulated panel and sensor, through the same
frame()/presented() calls the app makes -- the seam where a per-frame engine
can be right in every unit and still wrong as a whole. What none of this says
anything about is the real panel: the simulator's tau, gamma and noise are
whatever it was told.
"""

import json
import math
import os
import random
import re
import statistics

from harness import REPO, load_module

M = load_module("share_wifi", "measure")
P = load_module("share_wifi", "protocol")

SPEC = os.path.join(REPO, ".agents", "docs", "share_wifi_spec.md")


# --- a simulated panel and sensor -------------------------------------------

class Clock:
    """Microseconds. Every light_level() costs READ_US, as a real read would."""

    READ_US = 30

    def __init__(self):
        self.t = 0
        self.on_advance = None
        self.busy = False

    def ms(self):
        return self.t // 1000

    def us(self):
        return self.t

    def advance(self, us):
        self.t += us
        if self.on_advance is not None and not self.busy:
            self.busy = True
            try:
                self.on_advance()
            finally:
                self.busy = False


class Panel:
    """A single-pole panel: each change starts PRESENT_LATENCY_US after present.

    Luminance is backlight * (bleed + (1 - bleed) * pixels ** gamma), so black
    at full backlight is not quite black and switching the backlight off
    deepens it, and the ramp is an exact power law above black.
    """

    PRESENT_LATENCY_US = 2000

    def __init__(self, clock, gamma=2.2, tau_rise=4.0, tau_fall=3.0,
                 tau_mid=6.0, bleed=0.05, tau_backlight=0.3):
        self.clock = clock
        # A backlight-only change: no present and no liquid crystal, so it
        # starts at the call and what is left is the sensor's own response.
        self.tau_backlight = tau_backlight
        self.gamma = gamma
        self.taus = (tau_rise, tau_fall, tau_mid)
        self.bleed = bleed
        self.start = self.target = self.luminance("black", 1.0)
        self.t0 = 0
        self.tau = tau_rise
        self.field = ("black", 1.0)
        self.backlight = 1.0

    def luminance(self, pixels, backlight):
        if pixels == "black":
            p = 0.0
        elif pixels == "white":
            p = 1.0
        else:
            p = (sum(pixels) / 3 / 255) ** self.gamma
        return backlight * (self.bleed + (1 - self.bleed) * p)

    def level(self, t):
        if t < self.t0:
            return self.start
        return self.target + (self.start - self.target) * math.exp(
            -(t - self.t0) / 1000 / self.tau)

    def set_backlight(self, backlight, at_us):
        """display.backlight() alone: live at once, the pixels unchanged."""
        if backlight == self.backlight:
            return
        pixels = self.field[0]
        self.start = self.level(at_us)
        self.target = self.luminance(pixels, backlight)
        self.t0 = at_us
        self.tau = self.tau_backlight
        self.field = (pixels, backlight)
        self.backlight = backlight

    def show(self, pixels, backlight, present_us):
        if (pixels, backlight) == self.field:
            return
        if pixels == self.field[0]:
            # Only the backlight moved: the app set it in update(), live at
            # once, and the present carries no new pixels.
            self.set_backlight(backlight, present_us)
            return
        at = present_us + self.PRESENT_LATENCY_US
        self.start = self.level(at)
        self.target = self.luminance(pixels, backlight)
        self.t0 = at
        is_mid = isinstance(pixels, tuple) and 0 < sum(pixels) < 765
        if self.target < self.start:
            self.tau = self.taus[1]
        elif is_mid:
            self.tau = self.taus[2]
        else:
            self.tau = self.taus[0]
        self.field = (pixels, backlight)
        self.backlight = backlight


class Sensor:
    """Reads a panel through ambient light, noise and a 16-count quantiser.

    `pwm` makes it follow the backlight's PWM instead of averaging it: each
    read catches the backlight either on or off, in proportion to its duty.
    `own` and `echo` mix in that share of the badge's OWN panel, as its light
    reflected off the other badge's glass would be.
    """

    def __init__(self, clock, panel, ambient=32, span=4000, noise=8,
                 step=16, pwm=False, seed=1, own=None, echo=0.0):
        self.clock = clock
        self.panel = panel
        self.own = own
        self.echo = echo
        self.ambient = ambient
        self.span = span
        self.noise = noise
        self.step = step
        self.pwm = pwm
        self.rng = random.Random(seed)

    def __call__(self):
        self.clock.advance(Clock.READ_US)
        level = self.panel.level(self.clock.t)
        backlight = self.panel.backlight
        if self.pwm and 0 < backlight < 1:
            level = level / backlight if self.rng.random() < backlight else 0.0
        if self.own is not None and self.echo:
            level += self.echo * self.own.level(self.clock.t)
        value = self.ambient + level * self.span
        value += self.rng.uniform(-self.noise, self.noise)
        return max(0, int(value) // self.step * self.step)


class Badge:
    """A run driven the way the app drives it, one frame at a time."""

    PRESENT_US = 1000

    def __init__(self, clock, run, panel, frame_us=11000):
        self.clock = clock
        self.run = run
        self.panel = panel
        self.frame_us = frame_us
        self.next_frame = 0
        self.last_start = None
        self.frames = 0

    def frame(self, inline=False):
        """One frame. `inline` is a frame run from inside the other badge's
        reads: its present must not stop the other badge's clock, which on
        hardware it would not."""
        clock = self.clock
        start = clock.t
        delta = 11 if self.last_start is None else (start - self.last_start) // 1000
        self.last_start = start
        self.run.frame(clock.ms(), delta)
        if self.run.change_due:
            # As the app does: a backlight step is set inside its capture,
            # and the frame presented after it shows nothing new.
            backlight = self.run.backlight
            self.run.changed(
                lambda: self.panel.set_backlight(backlight, clock.t))
        elif getattr(self.run, "loopback_due", False):
            self.run.loopback(
                lambda level: self.panel.set_backlight(level, clock.t))
        elif getattr(self.run, "ring_due", False):
            # No DMA ring under CPython: the tests say why, as the app does.
            self.run.ring_test(None, "no ring here",
                               lambda level: self.panel.set_backlight(
                                   level, clock.t))
        present_us = clock.t
        self.panel.show(self.run.pixels, self.run.backlight, present_us)
        # Not clock.advance(): the other badge's frame must not start inside
        # this one's present. If it did, and took a settle capture there,
        # this badge would be frozen mid-frame for the whole capture and the
        # capture would read a panel that never changes.
        if not inline:
            clock.t += self.PRESENT_US
        # As the app does: the stamp from before the present, and the time
        # the present call returned.
        self.run.presented(present_us, clock.t)
        self.next_frame = max(start + self.frame_us, clock.t)
        self.frames += 1


class FakeTemp:
    """The chip warming a tenth of a degree a read, from 25.0."""

    def __init__(self):
        self.reads = 0

    def __call__(self):
        self.reads += 1
        return round(25.0 + 0.1 * (self.reads - 1), 1)


def mirror_run(span=4000, noise=8, pwm=False, frame_us=11000, seed=1):
    clock = Clock()
    clock.t = 5000000
    panel = Panel(clock)
    sensor = Sensor(clock, panel, span=span, noise=noise, pwm=pwm, seed=seed)
    run = M.MirrorRun(sensor, clock.us, FakeTemp())
    badge = Badge(clock, run, panel, frame_us)
    run.begin(clock.ms())
    while not run.done:
        if clock.t < badge.next_frame:
            clock.t = badge.next_frame
        badge.frame()
        assert badge.frames < 20000, "mirror run never finished"
    return run, badge


def two_badges(span=4000, noise=8, second_offset_us=-200000, seed=1,
               ambient=32, first_present=True, second_present=True, echo=0.0,
               limit=60000):
    """Two runs held screen to screen; each one's sensor reads the other's panel.

    `second_offset_us` is how long after the first badge's A the second's
    comes; negative, the second started first. `echo` mixes that share of a
    badge's own panel into its own sensor. A badge that is not `present`
    shows the other a dark screen whatever it draws.

    A frame that blocks reading (a capture, a burst) must not freeze the other
    badge, so a read that carries the clock past the other badge's next frame
    runs that frame there and then.
    """
    clock = Clock()
    clock.t = 10000000
    panel_a = Panel(clock)
    panel_b = Panel(clock, tau_rise=5.0, tau_fall=3.5, tau_mid=7.0)
    sensor_a = Sensor(clock, panel_b, ambient=ambient, span=span, noise=noise,
                      seed=seed, own=panel_a, echo=echo)
    sensor_b = Sensor(clock, panel_a, ambient=ambient, span=span, noise=noise,
                      seed=seed + 1, own=panel_b, echo=echo)
    if not first_present:
        sensor_b.span = 0
    if not second_present:
        sensor_a.span = 0
    run_a = M.SecondBadgeRun(True, sensor_a, clock.us, FakeTemp())
    run_b = M.SecondBadgeRun(False, sensor_b, clock.us, FakeTemp())
    # The same frame time, as two badges running the same firmware have: the
    # symbol blocks lock with the protocol's edge-lag correction, which is
    # half the RECEIVER's frame, standing in for the sender's. Give the badges
    # different frame times and that correction is wrong by half the
    # difference, and at some frame phases a 50 ms slot then decodes one
    # symbol wrong -- the protocol's same-hardware assumption, not a fault here.
    a = Badge(clock, run_a, panel_a, 11000)
    b = Badge(clock, run_b, panel_b, 11000)
    a.next_frame = clock.t
    b.next_frame = clock.t + second_offset_us
    started = {}

    def step(badge, inline=False):
        if badge not in started:
            badge.run.begin(clock.ms())
            started[badge] = True
        if not badge.run.done:
            badge.frame(inline)
        else:
            badge.next_frame = clock.t + badge.frame_us

    current = [None]

    def catch_up():
        for badge in (a, b):
            if badge is not current[0] and clock.t >= badge.next_frame:
                step(badge, inline=True)

    clock.on_advance = catch_up
    while not (run_a.done and run_b.done):
        badge = a if a.next_frame <= b.next_frame else b
        if clock.t < badge.next_frame:
            clock.t = badge.next_frame
        current[0] = badge
        step(badge)
        assert a.frames + b.frames < limit, "2nd-badge run never finished"
    return run_a, run_b


# --- the schedule -----------------------------------------------------------

def steps_of(steps, test):
    return [s for s in steps if s.test == test]


def test_schedule_runs_the_tests_in_the_order_of_the_table():
    seen = []
    for step in M.SECOND_BADGE_SCHEDULE:
        if not seen or seen[-1] != step.test:
            seen.append(step.test)
    assert seen == ["ambient", "levels", "gamma_ramp", "backlight_curve",
                    "bleed", "flicker", "settle", "backlight_step",
                    "symbol_errors", "averaging", "eye"]
    mirror = [s.test for s in M.MIRROR_SCHEDULE]
    assert "symbol_errors" not in mirror
    # The two share everything up to the backlight step; the mirror then
    # loops back on itself, which needs no second badge.
    mirror_only = ("loopback", "backlight_step_ring", "loopback_ring")
    shared = [s for s in M.MIRROR_SCHEDULE if s.test not in mirror_only]
    assert [s.key for s in shared] == \
        [s.key for s in M.SECOND_BADGE_SCHEDULE[:len(shared)]]
    assert mirror[len(shared):] == ["loopback"] * (
        len(M.LOOPBACK_RUNGS) + len(M.DIM_RUNGS)) + [
        "backlight_step_ring", "loopback_ring"]


def test_ambient_is_black_with_the_backlight_off_and_64_reads():
    (step,) = steps_of(M.MIRROR_SCHEDULE, "ambient")
    assert step.field == ("black", 0.0)
    assert step.read == M.READ_SAMPLES and step.count == 64
    # Room for 64 reads inside the guards at frames up to 20ms.
    assert (step.ms - 2 * M.GUARD_MS) // 20 >= 64


def test_levels_are_each_schemes_three_fields_for_a_second():
    steps = steps_of(M.MIRROR_SCHEDULE, "levels")
    assert [s.key for s in steps] == [
        ("fields", "black"), ("fields", "mid"), ("fields", "white"),
        ("backlight", "black"), ("backlight", "mid"), ("backlight", "white")]
    assert [s.field for s in steps] == [
        ("black", 1.0), (P.MID_FIELD_RGB, 1.0), ("white", 1.0),
        ("white", P.LOW_BACKLIGHT), ("white", P.MID_BACKLIGHT), ("white", 1.0)]
    assert all(s.ms == 1000 and s.read == M.READ_SAMPLES for s in steps)


def test_gamma_ramp_is_six_greys_for_a_second_each():
    steps = steps_of(M.MIRROR_SCHEDULE, "gamma_ramp")
    assert [s.key for s in steps] == ["0", "25", "50", "67", "75", "100"]
    assert [s.field[0] for s in steps] == [
        (0, 0, 0), (64, 64, 64), (128, 128, 128), (171, 171, 171),
        (191, 191, 191), (255, 255, 255)]
    assert all(s.field[1] == 1.0 and s.ms == 1000 for s in steps)


def test_backlight_curve_is_eleven_half_second_steps():
    steps = steps_of(M.MIRROR_SCHEDULE, "backlight_curve")
    assert [s.key for s in steps] == ["0.0", "0.1", "0.2", "0.3", "0.4", "0.5",
                                      "0.6", "0.7", "0.8", "0.9", "1.0"]
    for i, step in enumerate(steps):
        assert step.field[0] == "white"
        assert abs(step.field[1] - i / 10) < 1e-9
        assert step.ms == 500


def test_bleed_is_black_on_then_off():
    steps = steps_of(M.MIRROR_SCHEDULE, "bleed")
    assert [(s.key, s.field, s.ms) for s in steps] == [
        ("black_on", ("black", 1.0), 1000), ("black_off", ("black", 0.0), 1000)]


def test_flicker_is_a_256_read_burst_at_each_backlight():
    steps = steps_of(M.MIRROR_SCHEDULE, "flicker")
    assert [s.key for s in steps] == ["0.25", "0.5", "0.74", "0.9", "1.0"]
    assert [s.field for s in steps] == [("white", 0.25), ("white", 0.5),
                                        ("white", 0.74), ("white", 0.9),
                                        ("white", 1.0)]
    assert all(s.read == M.READ_BURST and s.count == 256 for s in steps)


def test_settle_is_ten_of_each_transition_300ms_apart_at_full_backlight():
    steps = steps_of(M.MIRROR_SCHEDULE, "settle")
    assert all(s.ms == 300 and s.field[1] == 1.0 for s in steps)
    captured = [s for s in steps if s.read == M.READ_CAPTURE]
    keys = [s.key for s in captured]
    assert keys.count("rise") == keys.count("fall") == keys.count("mid_rise") == 10
    # Every captured transition really is the transition it is named for.
    for prev, step in zip(steps, steps[1:]):
        if step.read != M.READ_CAPTURE:
            continue
        before, after = prev.field[0], step.field[0]
        expected = {"rise": ("black", "white"), "fall": ("white", "black"),
                    "mid_rise": ("black", P.MID_FIELD_RGB)}[step.key]
        assert (before, after) == expected
    assert steps[0].field == ("black", 1.0) and steps[0].read == M.READ_NONE
    assert M.CAPTURE_MS == 60


def test_backlight_step_moves_only_the_backlight_on_white_pixels():
    steps = steps_of(M.MIRROR_SCHEDULE, "backlight_step")
    assert all(s.ms == 300 and s.field[0] == "white" for s in steps)
    captured = [s for s in steps if s.read == M.READ_CAPTURE]
    keys = [s.key for s in captured]
    assert keys.count("rise") == keys.count("fall") == keys.count("mid_rise") == 10
    mid = P.LEVEL_SCHEMES[P.SCHEME_BACKLIGHT][P.MID][1]
    for prev, step in zip(steps, steps[1:]):
        if step.read != M.READ_CAPTURE:
            continue
        expected = {"rise": (0.0, 1.0), "fall": (1.0, 0.0),
                    "mid_rise": (0.0, mid)}[step.key]
        assert (prev.field[1], step.field[1]) == expected
    # The hold before them is the only pixel change: from settle's last
    # middle to white, backlight off.
    assert steps[0].field == ("white", 0.0) and steps[0].read == M.READ_NONE


def backlight_step_on(tau, span=4000, noise=8, seed=1):
    clock = Clock()
    clock.t = 5000000
    panel = Panel(clock, tau_backlight=tau)
    sensor = Sensor(clock, panel, span=span, noise=noise, seed=seed)
    run = M.MirrorRun(sensor, clock.us)
    badge = Badge(clock, run, panel)
    run.begin(clock.ms())
    while not run.done:
        clock.t = max(clock.t, badge.next_frame)
        badge.frame()
    return run.results()["backlight_step"]


def test_backlight_step_averages_through_the_measured_quantiser():
    # One capture of an 80-count swing in 16-count steps is four or five
    # levels: a lone capture's timing is mostly quantisation. Ten averaged
    # land within a quarter of the truth, from 0.3 to 5 ms -- where the
    # per-capture width this replaced read 0.24 ms for a true 0.66.
    for tau in (0.3, 1.0, 5.0):
        for seed in (1, 2, 3):
            step = backlight_step_on(tau, span=80, noise=12, seed=seed)
            for key in ("rise", "fall"):
                got = step[key]["t10_90_ms"]
                assert abs(got - tau * math.log(9)) <= 0.25 * tau * math.log(9) + 0.05, \
                    (tau, seed, key, step[key])


def test_a_backlight_faster_than_the_reads_comes_out_as_a_bin():
    step = backlight_step_on(0.01)
    for key in ("rise", "fall", "mid_rise"):
        assert step[key]["t10_90_ms"] <= 2 * M.CURVE_BIN_US / 1000.0, step[key]
        assert abs(step[key]["t50_ms"]) <= M.CURVE_BIN_US / 1000.0, step[key]


def test_half_time_ignores_one_noisy_read_before_the_edge():
    times = list(range(0, 4000, 100))
    values = [32] * 40
    values[18] = 96                          # one read past half way, then back
    for i in range(25, 40):
        values[i] = 112
    initial, final, floor = M.step_levels(values, 40, 16)
    t, reason = M.half_time(times, values, 40, initial, final, floor)
    assert 2400 <= t <= 2500 and reason is None, (t, reason)
    assert not M.step_moved(*M.step_levels([32] * 40, 40, 16))
    # Flickering between two levels: never two reads running away from the
    # start, so never an edge.
    values = [32] * 40
    for i in range(10, 40, 2):
        values[i] = 64
    initial, final, floor = 32, 64, 16
    assert M.half_time(times, values, 40, initial, final, floor) == \
        (None, M.SKIP_NO_EDGE)


def test_a_capture_interrupted_across_its_edge_is_left_out():
    # Reads every 50 µs, then nothing for 1 ms across the edge: interpolating
    # over the gap would put a 0.66 ms rise into one step.
    times = [t for t in range(0, 6000, 50) if not 2000 < t < 3000]
    values = [32 if t <= 2000 else 112 for t in times]
    initial, final, floor = M.step_levels(values, len(values), 16)
    assert M.half_time(times, values, len(values), initial, final, floor) == \
        (None, M.SKIP_GAP)
    times = list(range(0, 6000, 50))
    values = [32 if t <= 2500 else 112 for t in times]
    t, reason = M.half_time(times, values, len(values), initial, final, floor)
    assert t is not None and reason is None


def test_a_receiver_lines_its_captures_up_on_their_own_edges():
    # Its time origin is its estimate of the peer's, so the curve is aligned
    # on each capture's half way crossing: the same rise either way.
    for seed in (1, 2):
        run_a, run_b = two_badges(seed=seed)
        for run in (run_a, run_b):
            step = run.results()["backlight_step"]
            for key in ("rise", "fall", "mid_rise"):
                assert step[key]["transitions"] == 10, (seed, key, step[key])
                assert abs(step[key]["t10_90_ms"] - 0.3 * math.log(9)) < 0.1, \
                    (seed, key, step[key])
                assert "t50_ms" not in step[key]


def test_reading_resumes_straight_after_a_backlight_change():
    # Nothing between the change and the next read: on the badge the lead-in's
    # re-timing loop, run there, blinded the capture for a few hundred µs right
    # where a fast step crosses half way. The simulated clock does not charge
    # for Python, so check the order of the writes instead: the first after
    # the change must be a fresh read's, not a correction to an old one.
    clock = Clock()
    clock.t = 5000000
    panel = Panel(clock)
    recorder = M.Recorder(M.MIRROR_SCHEDULE, M.TOOL_MIRROR,
                          Sensor(clock, panel), clock.us)
    log = []

    class Logged(list):
        def __setitem__(self, i, value):
            log.append(i)
            list.__setitem__(self, i, value)
    recorder.cap_t = Logged(recorder.cap_t)
    recorder.pending = next(
        i for i, step in enumerate(M.MIRROR_SCHEDULE)
        if step.test == "backlight_step" and step.read == M.READ_CAPTURE)
    recorder.changed(lambda: log.append("change"))
    at = log.index("change")
    assert at > 0, "no lead-in"
    assert log[at + 1] == at, "the first write after the change was %r" % (
        log[at + 1],)
    # The lead-in is re-timed once the capture is over.
    after = log[at + 1:]
    read_at = [n for n, i in enumerate(after) if i >= at]
    fixed_at = [n for n, i in enumerate(after) if i < at]
    assert sorted(after[n] for n in fixed_at) == list(range(at))
    assert min(fixed_at) > max(read_at)


def test_a_direction_that_finds_nothing_says_why():
    # The lit fall on 2026-10-05c found no step and said nothing more. A
    # backlight that never moves: every capture flat, and its swing says so.
    clock = Clock()
    clock.t = 5000000
    panel = Panel(clock)
    run = M.MirrorRun(Sensor(clock, panel), clock.us)
    badge = Badge(clock, run, panel)
    panel.set_backlight = lambda backlight, at_us: None
    run.begin(clock.ms())
    while not run.done:
        clock.t = max(clock.t, badge.next_frame)
        badge.frame()
    step = run.results()["backlight_step"]
    for key in ("rise", "fall", "mid_rise"):
        entry = step[key]
        assert entry["error"] == "no %s step found" % key.replace("_", " ")
        assert entry["skipped"] == {"flat": 10, "no_edge": 0, "gap": 0}, entry
        assert abs(entry["swing"]) <= 16, entry
    # Nothing found anywhere is still three answers, not one error.
    assert "error" not in step and step["bin_us"] == M.CURVE_BIN_US


def test_a_kept_direction_reports_its_swing_and_no_skips():
    step = backlight_step_on(0.3, span=80, noise=6)
    assert step["rise"]["skipped"] == {"flat": 0, "no_edge": 0, "gap": 0}
    assert step["rise"]["swing"] > 0 > step["fall"]["swing"]


def test_the_loopback_decodes_the_block_on_every_rung():
    # The sensor follows the backlight in 0.3 ms here, so every rung down to
    # 16 ms carries the block, and the edge it locks on is seen ~0.2 ms
    # after the change -- the climb to T_low.
    run, _ = mirror_run(span=4000, noise=8)
    loops = run.results()["loopback"]
    for rung in M.LOOPBACK_RUNGS:
        entry = loops[str(rung)]
        assert entry["errors"] == 0, (rung, entry)
        # Good to about a millisecond: readings are pooled into 2 ms.
        assert -1.0 < entry["lag_ms"] < 1.0, (rung, entry)
        assert entry["reads_per_s"] > 20000, (rung, entry)


def test_the_loopback_through_the_measured_quantiser_still_locks():
    # An 80-count swing in 16-count steps, with noise: it still finds the
    # sync edge on every rung and times it within a millisecond. Its error
    # count is this panel model's, not the badge's: Panel takes light as
    # proportional to the backlight value, which puts 0.80 one ADC step
    # below white, where v2's curve puts it at 54% duty.
    run, _ = mirror_run(span=80, noise=12)
    loops = run.results()["loopback"]
    for rung in M.LOOPBACK_RUNGS:
        entry = loops[str(rung)]
        assert "error" not in entry, (rung, entry)
        assert 0.0 <= entry["lag_ms"] < 1.0, (rung, entry)


def test_the_off_loopback_measures_its_levels_and_decodes():
    # The comparison with low off: the measure tests' panel is linear in the
    # backlight, so low 0.0, middle 0.80, high 1.0 of a 4000 swing, read back
    # as such, and decoded.
    run, _ = mirror_run(span=4000, noise=8)
    loops = run.results()["loopback"]
    for rung in M.DIM_RUNGS:
        entry = loops["off_%d" % rung]
        assert entry["errors"] == 0, (rung, entry)
        low, mid, high = entry["levels"]
        assert low < mid < high, entry
        assert abs((mid - low) / (high - low) - 0.8) < 0.05, entry


def test_a_dim_loopback_whose_levels_are_too_close_says_so():
    t = [0]

    def ticks():
        t[0] += 30
        return t[0]
    shown = [0.0]
    out = M.dim_loopback_block(25, lambda: 32 + int(shown[0] * 40), ticks,
                               lambda b: shown.__setitem__(0, b))
    assert out["error"] == "levels too close", out
    assert len(out["levels"]) == 3


def test_a_loopback_with_no_backlight_levels_says_so():
    out = M.loopback_block(50, None, lambda: 0, lambda: 0, lambda b: None)
    assert out == {"error": "no levels"}


def test_a_loopback_that_never_sees_its_sync_says_so():
    levels = P.Levels()
    levels.fit(32, 64, 112)
    t = [0]

    def ticks():
        t[0] += 30
        return t[0]
    out = M.loopback_block(25, levels, lambda: 32, ticks, lambda b: None)
    assert out == {"error": "no sync symbol"}


def test_curve_crossing_needs_two_bins_and_interpolates():
    n = 10
    sums = [0.0] * n
    counts = [0] * n
    for b, v in enumerate((0.0, 0.0, 0.95, 0.0, 0.2, 0.6, 1.0, 1.0, 1.0, 1.0)):
        sums[b] = v * 2
        counts[b] = 2
    # Bin 2's lone spike is not a crossing; the rise through bins 4-5 is.
    bin_mid = lambda b: b * M.CURVE_BIN_US - M.CURVE_LEAD_US + M.CURVE_BIN_US / 2.0
    t = M.curve_crossing(sums, counts, 0.5)
    expected = bin_mid(4) + (0.5 - 0.2) / (0.6 - 0.2) * M.CURVE_BIN_US
    assert abs(t - expected) < 1e-6, (t, expected)
    assert M.curve_crossing(sums, counts, 1.5) is None


def test_symbol_blocks_are_three_rungs_in_each_scheme():
    steps = steps_of(M.SECOND_BADGE_SCHEDULE, "symbol_errors")
    assert [s.key for s in steps] == [("fields", 50), ("fields", 65),
                                      ("fields", 80), ("backlight", 50),
                                      ("backlight", 65), ("backlight", 80)]
    for step in steps:
        assert step.symbols == "    -" + M.DE_BRUIJN
        assert step.ms == 34 * step.slot_ms
        scheme = P.LEVEL_SCHEMES[step.scheme]
        for slot, symbol in enumerate(step.symbols):
            assert step.field_at(slot * step.slot_ms) == scheme[symbol]
            assert step.field_at(slot * step.slot_ms + step.slot_ms - 1) == scheme[symbol]


def test_schedule_durations():
    shared = (1500 + 6 * 1000 + 6 * 1000 + 11 * 500 + 2 * 1000 + 5 * 500
              + 40 * 300 + 40 * 300)
    assert shared == 47500
    loopback = sum(35 * rung + 200 for rung in (50, 32, 25, 20, 16))
    dim = sum(3 * 150 + 35 * rung + 200 for rung in (25, 16))
    assert shared + loopback + dim == 56240
    assert M.MIRROR_SCHEDULE_MS == 56240 + M.RING_STEP_MS + M.RING_LOOP_MS
    eye_slots = (4 + 1 + 19) + (4 + 1 + 29)
    assert M.SECOND_BADGE_SCHEDULE_MS == (
        47500 + 2 * 34 * (50 + 65 + 80) + 3 * 2000
        + eye_slots * (50 + 65 + 80 + 100)) == 83870
    assert M.schedule_ms(M.MIRROR_SCHEDULE) == M.MIRROR_SCHEDULE_MS


def test_test_lists_have_the_tests_of_the_table_in_order():
    assert len(M.MIRROR_TESTS) == 15 and len(M.SECOND_BADGE_TESTS) == 14
    assert M.SECOND_BADGE_TESTS[-2:] == ("averaging", "eye")
    assert "edge_latency" in M.MIRROR_TESTS and "symbol_errors" not in M.MIRROR_TESTS
    assert "symbol_errors" in M.SECOND_BADGE_TESTS and "edge_latency" not in M.SECOND_BADGE_TESTS


# --- averaging and the eye ---------------------------------------------------

def averaged(sd, level=40.0, frames=40, seed=1):
    """The averaging result for one level read through a 16-count quantiser
    with Gaussian noise of standard deviation `sd`."""
    rng = random.Random(seed)

    def light():
        return int(level + rng.gauss(0, sd)) // 16 * 16
    recorder = M.Recorder(M.SECOND_BADGE_SCHEDULE, M.TOOL_SECOND_BADGE, light)
    index = [i for i, s in enumerate(M.SECOND_BADGE_SCHEDULE)
             if s.test == "averaging" and s.key == "black"][0]
    recorder._begin(index)
    for _ in range(frames):
        recorder._average_burst(M.AVERAGE_READS)
    recorder._end(index)
    return recorder.averages["black"]


def test_averaging_buys_resolution_when_noise_spans_the_steps():
    # sd of 12 counts, three-quarters of a step: readings spread over several
    # steps, so the means of 64 are 8x steadier than one reading.
    result = averaged(12.0)
    sd = result["sd"]
    assert result["bursts"] == 40
    assert 6 < sd["1"] / sd["64"] < 11, sd
    assert sd["1"] > sd["4"] > sd["16"] > sd["64"]
    assert abs(result["mean"] - 32) < 4      # 40 floored, on average


def test_averaging_stalls_when_noise_is_narrower_than_a_step():
    # sd of 1 count, centred in a step: every reading is the same step, so
    # averaging has nothing to resolve -- the spread is zero at every depth
    # and the mean is the step, not the level.
    result = averaged(1.0)
    assert result["sd"] == {"1": 0.0, "4": 0.0, "16": 0.0, "64": 0.0}
    assert result["mean"] == 32.0


def test_averaging_separation_is_the_narrowest_gap_in_spreads():
    recorder = M.Recorder(M.SECOND_BADGE_SCHEDULE, M.TOOL_SECOND_BADGE,
                          lambda: 0)
    level = {"mean": 0.0, "sd": {"1": 8.0, "4": 4.0, "16": 2.0, "64": 1.0},
             "bursts": 10}
    recorder.averages = {"black": dict(level, mean=40.0),
                         "mid": dict(level, mean=72.0),
                         "white": dict(level, mean=120.0)}
    out = recorder._averaging_result()
    assert out["span"] == 80.0 and out["mid_share"] == 0.4
    # The narrower gap is black to mid, 32.
    assert out["separation"] == {"1": 4.0, "4": 8.0, "16": 16.0, "64": 32.0}


def eye_of(sequence, slot_ms, value_of, frame_ms=11, start=0):
    """Feeds an EyeBlock a peer showing `sequence` after lead-in and sync."""
    levels = P.Levels()
    levels.fit(100, 500, 900)
    block = M.EyeBlock()
    block.start("k", start, slot_ms, levels, sequence)
    shown = P.LOW * M.SYNC_LEAD_SLOTS + P.HIGH + sequence
    t = start
    while block.active and t < start + (len(shown) + 3) * slot_ms:
        slot = t // slot_ms - start // slot_ms
        symbol = shown[slot] if 0 <= slot < len(shown) else P.LOW
        value = value_of(symbol, t % slot_ms)
        block.feed(t, value, value)
        t += frame_ms
    block.finish()
    return block.result()


LEVEL_VALUE = {P.LOW: 100, P.MID: 500, P.HIGH: 900}


def test_a_clean_eye_is_open_everywhere_and_decodes():
    for name, sequence in M.EYE_SEQUENCES:
        got = eye_of(sequence, 50, lambda s, phase: LEVEL_VALUE[s])
        assert got["errors_1"] == got["errors_avg"] == 0, (name, got)
        assert all(o is not None and o > 0 for o in got["opening"]), got


def test_an_eye_that_settles_late_is_closed_at_the_front_of_the_slot():
    # Each symbol shows its predecessor's level for the first 40% of its
    # slot: the eye is shut there and open behind it, and the back half --
    # where slots are decided -- still decodes.
    previous = {}

    def late(symbol, phase):
        if phase < 20:
            return previous.get("v", 100)
        previous["v"] = LEVEL_VALUE[symbol]
        return previous["v"]
    got = eye_of(M.DE_BRUIJN, 50, late)
    assert got["opening"][0] < 0, got
    assert all(o > 0 for o in got["opening"][1:]), got
    assert got["best_bin"] >= 1 and got["errors_avg"] == 0, got


def test_two_levels_are_split_half_way_between_black_and_white():
    # Not at a three-level threshold: a white that reads 650 against black's
    # 100 and white's 900 is past half way (500) but short of the upper
    # three-level threshold (700), and is still white.
    dim = dict(LEVEL_VALUE, **{P.HIGH: 650})
    got = eye_of(M.DE_BRUIJN_2, 50, lambda s, phase: dim[s])
    assert got["errors_1"] == got["errors_avg"] == 0, got


def test_slots_are_decided_from_their_back_half():
    # Rises are instant and falls linger for 60% of their slot. The sync is a
    # rise, so the phase reference is on time, and every fall shows the level
    # above it for most of its slot: decided from the whole slot those would
    # be wrong, from the back half -- the protocol's window -- they are right.
    state = {"v": 100, "slot": None}

    def slow_falls(symbol, phase):
        target = LEVEL_VALUE[symbol]
        if phase < 30 and target < state["v"]:
            return state["v"]
        if phase >= 30 or target >= state["v"]:
            state["next"] = target
        return state.get("next", target)

    def value_of(symbol, phase):
        value = slow_falls(symbol, phase)
        if phase >= 45:
            state["v"] = LEVEL_VALUE[symbol]   # settled by the slot's end
        return value
    got = eye_of(M.DE_BRUIJN, 50, value_of, frame_ms=5)
    assert got["errors_avg"] == 0, got
    assert got["opening"][0] < 0 < got["opening"][-1], got


def test_an_eye_with_no_sync_says_so():
    got = eye_of(M.DE_BRUIJN, 50, lambda s, phase: 100)
    assert got == {"error": "no sync symbol"}


# --- de Bruijn --------------------------------------------------------------

def test_the_two_level_sequence_holds_every_run_of_four():
    seq = M.DE_BRUIJN_2
    assert len(seq) == 19 and set(seq) == {P.LOW, P.HIGH}
    runs = set(seq[i:i + 4] for i in range(len(seq) - 3))
    assert len(runs) == 16


def test_no_eye_block_holds_white_long_enough_to_look_like_a_go():
    # A badge still answering reads 450-750 ms of white as the go.
    for step in M.SECOND_BADGE_SCHEDULE:
        if step.test != "eye":
            continue
        longest = run = 0
        for symbol in step.symbols:
            run = run + 1 if symbol == P.HIGH else 0
            longest = max(longest, run)
        assert longest * step.slot_ms < M.GO_MIN_MS, (step.key, longest)


def test_de_bruijn_holds_every_triple_in_29_symbols():
    seq = M.DE_BRUIJN
    assert len(seq) == 29
    assert set(seq) == {P.LOW, P.MID, P.HIGH}
    triples = [seq[i:i + 3] for i in range(len(seq) - 2)]
    assert len(triples) == 27
    alphabet = (P.LOW, P.MID, P.HIGH)
    every = {a + b + c for a in alphabet for b in alphabet for c in alphabet}
    assert set(triples) == every, "missing %s" % sorted(every - set(triples))


def test_de_bruijn_generator_is_a_de_bruijn_sequence_generally():
    for k, n in ((2, 3), (2, 4), (3, 2), (4, 2)):
        cycle = M.de_bruijn(k, n)
        assert len(cycle) == k ** n
        linear = cycle + cycle[:n - 1]
        words = {tuple(linear[i:i + n]) for i in range(k ** n)}
        assert len(words) == k ** n


# --- calculations -----------------------------------------------------------

def test_adc_step_is_the_gcd_of_every_reading():
    assert M.adc_step([32, 48, 112, 288]) == 16
    assert M.adc_step([32, 0, 64]) == 32
    assert M.adc_step([0, 0]) == 0
    assert M.adc_step([]) == 0
    assert M.adc_step([300, 301]) == 1
    assert M.adc_step([16, 32, 999], count=2) == 16


def test_median_is_the_upper_median_and_leaves_the_input_alone():
    values = [5, 1, 4, 2]
    assert M.median(values) == 4
    assert values == [5, 1, 4, 2]
    assert M.median([3, 1, 2]) == 2
    assert M.median([9, 1, 2, 100], count=3) == 2
    assert M.median([]) is None


def test_peak_to_peak_and_sd():
    values = [32, 48, 32, 64, 48, 32]
    assert M.peak_to_peak(values) == 32
    assert abs(M.sd(values) - statistics.pstdev(values)) < 1e-9
    assert M.sd([7, 7, 7]) == 0
    assert M.sd([1, 3, 1000], count=2) == 1.0
    assert M.peak_to_peak([]) is None and M.sd([]) is None


def test_gamma_fit_recovers_a_known_gamma():
    for gamma in (1.0, 1.58, 2.2, 2.32, 2.8):
        fractions = [M.ramp_value(p) / 255 for p in M.RAMP_PERCENTS]
        readings = [32 + 4000 * f ** gamma for f in fractions]
        assert abs(M.fit_gamma(fractions, readings) - gamma) < 1e-9


def test_gamma_fit_drops_steps_no_brighter_than_black():
    # The measured dark ramp: 25% read the same as black.
    fractions = [M.ramp_value(p) / 255 for p in M.RAMP_PERCENTS]
    gamma = M.fit_gamma(fractions, [32, 32, 48, 64, 80, 112])
    assert 2.0 < gamma < 2.6
    assert M.fit_gamma(fractions, [32, 32, 32, 32, 32, 32]) is None
    assert M.fit_gamma([0.0, 1.0], [32, 112]) is None


def exponential(tau_ms, start, final, n=2000, read_us=30, delay_us=0, step=1):
    times = [i * read_us for i in range(n)]
    values = []
    for t in times:
        if t < delay_us:
            v = start
        else:
            v = final + (start - final) * math.exp(-(t - delay_us) / 1000 / tau_ms)
        values.append(int(round(v)) // step * step)
    return times, values


def test_settle_tau_recovers_a_known_time_constant():
    for tau in (2.0, 4.1, 8.0):
        for start, final in ((100, 4100), (4100, 100)):      # rise and fall
            times, values = exponential(tau, start, final)
            got = M.settle_tau(times, values, len(values), 1)
            assert abs(got - tau) / tau < 0.03, (tau, start, final, got)


def test_settle_tau_ignores_the_plateau_before_the_edge():
    times, values = exponential(4.0, 100, 4100, delay_us=5000)
    got = M.settle_tau(times, values, len(values), 1)
    assert abs(got - 4.0) < 0.15


def test_settle_tau_does_not_need_the_start_of_the_transition():
    # A receiver whose capture began 3ms late sees only the tail.
    times, values = exponential(4.0, 100, 4100, n=2000)
    late = 100
    got = M.settle_tau([t - times[late] for t in times[late:]], values[late:],
                       len(values) - late, 1)
    assert abs(got - 4.0) < 0.15


def test_settle_tau_survives_a_16_count_quantiser():
    times, values = exponential(4.0, 96, 4096, step=16)
    got = M.settle_tau(times, values, len(values), 16)
    assert abs(got - 4.0) < 0.3


def test_settle_tau_on_the_measured_80_count_swing_is_the_right_order():
    # Five ADC steps of swing is all the badge has: the fit is coarse, but it
    # must still land within a factor of two rather than failing outright.
    times, values = exponential(4.0, 32, 112, step=16)
    got = M.settle_tau(times, values, len(values), 16)
    assert got is not None and 2.0 < got < 8.0


def noisy_steps(tau_ms, start, final, seed, sd=4.0, offset=0.0):
    """A capture as the sensor gives it: noise, then floored to 16-count steps.

    The noise matters. Without it a four-step swing quantises into a
    staircase the fit reads 1.9x slow, or not at all -- but the badge's held
    levels measured a standard deviation of 3-8 counts, which spreads each
    reading across the steps either side.
    """
    rng = random.Random(seed)
    times = [i * 25 for i in range(2400)]
    values = []
    for t in times:
        v = start if t < 400 else (
            final + (start - final) * math.exp(-(t - 400) / 1000 / tau_ms))
        values.append(int(v + offset + rng.gauss(0, sd)) // 16 * 16)
    return times, values


def test_settle_tau_on_the_second_runs_64_count_swing_is_within_a_sixth():
    # Four ADC steps, as the 2026-10-04 run measured, with the sensor's noise.
    # The median over captures -- what the result reports -- is what counts.
    for tau in (5.0, 8.7, 10.5):
        for start, final in ((40, 104), (104, 40)):
            got = []
            for seed in range(16):
                times, values = noisy_steps(tau, start, final, seed,
                                            offset=(seed % 8) * 2.0)
                fitted = M.settle_tau(times, values, len(values), 16)
                if fitted is not None:
                    got.append(fitted)
            assert len(got) >= 12, (tau, start, len(got))
            middle = sorted(got)[len(got) // 2]
            assert abs(middle - tau) / tau < 1 / 6.0, (tau, start, middle)


def test_settle_tau_refuses_a_decay_of_one_residual_level():
    # The 2026-10-04 dark run reported a fall tau of 41,890,100,000 ms. With
    # four steps of swing the whole fitted run can sit one level from its
    # final value: a zero slope, which rounding turned into a tiny negative
    # one and so an enormous tau.
    t = [i * 25 for i in range(1216)]
    values = [96] * 16 + [64] * 400 + [32] * 800
    # CPython's doubles round the identical logs back to an exact zero; the
    # badge's single-precision floats did not. Emulate that with a log that
    # drifts by parts in a billion, and lift the window limit so this guard
    # is tested alone.
    real_math = M.math
    calls = [0]

    class Drifting:
        @staticmethod
        def log(x):
            calls[0] += 1
            return real_math.log(x) - calls[0] * 1e-9

        sqrt = staticmethod(real_math.sqrt)
    saved = M.CAPTURE_MS
    M.math, M.CAPTURE_MS = Drifting, 10 ** 15
    try:
        assert M.settle_tau(t, values, len(values), 16) is None
    finally:
        M.math, M.CAPTURE_MS = real_math, saved


def test_settle_tau_refuses_a_tau_longer_than_its_capture():
    # A reading that lingers a step off its final level fits a slope, but not
    # one the capture window could have measured.
    for hold in (400, 1200):
        values = [96] * 16 + [80] + [64] * hold + [32] * 800
        t = [i * 25 for i in range(len(values))]
        assert M.settle_tau(t, values, len(values), 16) is None, hold


def test_settle_tau_gives_up_on_a_flat_capture():
    assert M.settle_tau(list(range(100)), [64] * 100, 100, 16) is None
    assert M.settle_tau([0, 1, 2], [0, 100, 100], 3, 1) is None


def test_settle_90_is_2_3_times_the_slowest_median():
    recorder = M.Recorder(M.MIRROR_SCHEDULE, M.TOOL_MIRROR, lambda: 0, lambda: 0)
    recorder.taus = {"rise": [4.0, 4.2, 3.9], "fall": [3.0, 3.1, 2.9],
                     "mid_rise": [6.0, 6.1, 5.0]}
    assert recorder._settle_result() == {
        "rise_tau_ms": 4.0, "fall_tau_ms": 3.0, "mid_rise_tau_ms": 6.0,
        "settle_90_ms": 13.8}
    # One direction failing keeps the others: the first hardware run lost a
    # rise and a fall that fitted because the middle rise did not.
    recorder.taus["mid_rise"] = []
    assert recorder._settle_result() == {
        "rise_tau_ms": 4.0, "fall_tau_ms": 3.0,
        "mid_rise_tau_ms": {"error": "no mid rise transition fitted"},
        "settle_90_ms": 9.2}
    recorder.taus["fall"] = []
    assert recorder._settle_result()["fall_tau_ms"] == {
        "error": "no fall transition fitted"}
    assert recorder._settle_result()["settle_90_ms"] == 9.2
    recorder.taus["rise"] = []
    assert recorder._settle_result() == {"error": "no transition fitted"}


def captured_rise(first_values, returned_us=None):
    """A Recorder whose one settle rise capture reads `first_values`."""
    reads = iter(first_values + [first_values[-1]] * 4000)
    clock = [0]

    def ticks_us():
        clock[0] += 50
        return clock[0]
    recorder = M.Recorder(M.MIRROR_SCHEDULE, M.TOOL_MIRROR, lambda: next(reads),
                          ticks_us)
    levels = M.Levels()
    levels.fit(32, 64, 112)
    recorder.levels = lambda scheme: levels
    rise = [i for i, step in enumerate(recorder.steps) if step.key == "rise"][0]
    recorder.pending = rise
    recorder.presented(0, returned_us)
    return recorder


def test_a_rise_already_past_t_low_counts_as_an_upper_bound():
    # Read after the present call returned, a fast panel has often already
    # crossed: that read's time bounds the latency rather than measuring it.
    recorder = captured_rise([112, 112])
    result = recorder._latency_result()
    assert result["rises"] == 1 and result["upper_bounds"] == 1
    recorder = captured_rise([32, 32, 112, 112])
    result = recorder._latency_result()
    assert result["rises"] == 1 and result["upper_bounds"] == 0


def test_the_blind_time_of_the_present_call_is_reported():
    recorder = captured_rise([32, 112], returned_us=22000)
    assert recorder._latency_result()["blind_ms"] == 22.0
    assert "blind_ms" not in captured_rise([32, 112])._latency_result()


def test_a_run_says_when_a_capture_is_due():
    # The app presents early, inside update(), only on a frame with a capture
    # due -- so the flag must be set between frame() and presented() exactly
    # once per captured settle step, and clear afterwards.
    clock = Clock()
    panel = Panel(clock)
    run = M.MirrorRun(Sensor(clock, panel), clock.us)
    run.begin(clock.ms())
    due = changes = 0
    while not run.done:
        run.frame(clock.ms(), 11)
        if run.change_due:
            # A backlight step: changed(), never a present.
            changes += 1
            backlight = run.backlight
            run.changed(lambda: panel.set_backlight(backlight, clock.t))
            assert not run.capture_due and not run.change_due
        panel.show(run.pixels, run.backlight, clock.t)
        if run.capture_due:
            due += 1
            present = clock.t
            clock.t += 1000
            run.presented(present, clock.t)
            assert not run.capture_due
        clock.t += 11000
    assert due == changes == 30
    assert due + changes == len(run.recorder.capture_steps)
    assert run.results()["edge_latency"]["blind_ms"] == 1.0


def test_edge_latency_interpolates_the_crossing():
    times = [1000, 2000, 3000, 4000]
    values = [32, 32, 132, 232]
    assert M.edge_latency_ms(times, values, 4, 82) == 2.5
    assert M.edge_latency_ms(times, values, 4, 32) == 1.0
    assert M.edge_latency_ms(times, values, 4, 500) is None


def test_flicker_verdict():
    steady = [96] * 200 + [112] * 40 + [128] * 16
    assert M.flicker_verdict(steady, 16) == "averages"
    pwm = [16] * 190 + [112] * 66
    assert M.flicker_verdict(pwm, 16) == "follows_pwm"
    # Exactly 90% within two steps of the median is enough.
    edge = [100] * 90 + [500] * 10
    assert M.flicker_verdict(edge, 16) == "averages"
    assert M.flicker_verdict([100] * 89 + [500] * 11, 16) == "follows_pwm"
    result = M.flicker_result(pwm, 16)
    assert set(result) == {"min", "max", "sd", "verdict"}
    assert result["min"] == 16 and result["max"] == 112


def test_frame_timing_stats():
    deltas = [11, 12, 10, 11, 13, 9, 11]
    stats = M.FrameStats()
    for delta in deltas:
        stats.add(delta)
    assert stats.result() == {
        "frames": 7, "mean_ms": round(statistics.mean(deltas), 1),
        "min_ms": 9, "max_ms": 13,
        "sd_ms": round(statistics.pstdev(deltas), 1)}
    assert M.FrameStats().result() == {"error": "no frames"}


# --- symbol errors ----------------------------------------------------------

def measured_levels():
    levels = P.Levels()
    levels.fit(32, 1632, 4032)
    return levels


def symbol_stream(slot_ms, symbols, start_ms=0, frame_ms=11, tau=4.0,
                  corrupt=(), lag_ms=3):
    """Reads of a peer sending `symbols` from start_ms, one per slot."""
    shares = {P.LOW: 0.0, P.MID: 0.4, P.HIGH: 1.0}
    out = []
    level = 0.0
    t = 0
    end = start_ms + slot_ms * (len(symbols) + 4)
    while t < end:
        slot = (t - start_ms - lag_ms) // slot_ms
        symbol = symbols[slot] if 0 <= slot < len(symbols) else P.LOW
        if slot in corrupt:
            symbol = {P.LOW: P.HIGH, P.MID: P.LOW, P.HIGH: P.MID}[symbol]
        level += (shares[symbol] - level) * (1 - math.exp(-frame_ms / tau))
        out.append((t, 32 + int(level * 4000) // 16 * 16))
        t += frame_ms
    return out


def receive_block(slot_ms, samples, start_ms=0):
    block = M.SymbolBlock()
    block.start(("fields", slot_ms), start_ms, slot_ms, measured_levels(), 11)
    for t, v in samples:
        if block.feed(t, v):
            break
    if block.active:
        block.finish()
    return block.result()


def test_symbol_block_decodes_a_clean_stream_with_no_errors():
    for rung in M.SYMBOL_RUNGS:
        samples = symbol_stream(rung, M.SYMBOL_BLOCK, start_ms=200)
        assert receive_block(rung, samples, start_ms=200) == \
            {"symbols": 29, "errors": 0}, rung


def test_symbol_block_counts_injected_errors():
    for wrong in ((10,), (6, 12, 20), (5, 33)):
        samples = symbol_stream(80, M.SYMBOL_BLOCK, start_ms=200, corrupt=wrong)
        result = receive_block(80, samples, start_ms=200)
        assert result == {"symbols": 29, "errors": len(wrong)}, (wrong, result)


def test_symbol_block_with_no_sync_symbol_says_so():
    samples = symbol_stream(50, P.LOW * 34, start_ms=200)
    assert receive_block(50, samples, start_ms=200) == {"error": "no sync symbol"}


def test_count_symbol_errors():
    assert M.count_symbol_errors(list(M.DE_BRUIJN), M.DE_BRUIJN) == 0
    assert M.count_symbol_errors([None] * 29, M.DE_BRUIJN) == 29
    assert M.count_symbol_errors(list(M.DE_BRUIJN[:20]), M.DE_BRUIJN) == 9


# --- the results' shape -----------------------------------------------------

LEVEL_KEYS = {"black", "mid", "white", "span", "mid_share"}
NOISE_KEYS = {"black", "mid", "white"}
SHAPE = {
    "ambient": int,
    "adc_step": int,
    "levels": {"fields": LEVEL_KEYS, "backlight": LEVEL_KEYS},
    "noise": {"fields": {k: {"p2p", "sd"} for k in NOISE_KEYS},
              "backlight": {k: {"p2p", "sd"} for k in NOISE_KEYS}},
    "gamma_ramp": {"0", "25", "50", "67", "75", "100", "gamma"},
    "backlight_curve": {"0.0", "0.1", "0.2", "0.3", "0.4", "0.5", "0.6",
                        "0.7", "0.8", "0.9", "1.0"},
    "bleed": {"black_on", "black_off"},
    "flicker": {k: {"min", "max", "sd", "verdict"}
                for k in ("0.25", "0.5", "0.74", "0.9", "1.0")},
    "settle": {"rise_tau_ms", "fall_tau_ms", "mid_rise_tau_ms", "settle_90_ms"},
    "frame_timing": {"frames", "mean_ms", "min_ms", "max_ms", "sd_ms"},
}
SKIPPED = {"flat", "no_edge", "gap"}
STEP_KEYS = {"transitions": int, "t10_90_ms": float, "swing": int,
             "skipped": SKIPPED}
SHAPE["backlight_step"] = {"rise": STEP_KEYS, "fall": STEP_KEYS,
                           "mid_rise": STEP_KEYS, "bin_us": int,
                           "read_us": float}
# Only the badge that made the change knows when it was made.
MIRROR_STEP_KEYS = dict(STEP_KEYS, t10_ms=float, t50_ms=float, t90_ms=float)
LOOPED = {"symbols", "errors", "lag_ms", "reads_per_s"}
MIRROR_SHAPE = dict(SHAPE, edge_latency={"mean_ms", "min_ms", "max_ms",
                                         "rises", "upper_bounds", "blind_ms"},
                    loopback=dict(
                        [(str(r), LOOPED) for r in (50, 32, 25, 20, 16)]
                        + [("off_%d" % r, LOOPED | {"levels"})
                           for r in (25, 16)]),
                    backlight_step={"rise": MIRROR_STEP_KEYS,
                                    "fall": MIRROR_STEP_KEYS,
                                    "mid_rise": MIRROR_STEP_KEYS,
                                    "bin_us": int, "read_us": float})
# Without a ring the two ring tests say why; with one, see the ring tests.
NO_RING = object()
MIRROR_SHAPE["backlight_step_ring"] = NO_RING
MIRROR_SHAPE["loopback_ring"] = NO_RING
# temperature_c: {"start": C, <test>: C, ..., "end": C}.
TEMPS = object()
SHAPE["temperature_c"] = TEMPS
MIRROR_SHAPE["temperature_c"] = TEMPS
DEPTHS = {"1", "4", "16", "64"}
AVERAGED_LEVEL = {"mean": float, "sd": DEPTHS, "bursts": int}
SECOND_SHAPE = dict(SHAPE, symbol_errors={
    scheme: {rung: {"symbols", "errors"} for rung in ("50", "65", "80")}
    for scheme in ("fields", "backlight")},
    averaging={"black": AVERAGED_LEVEL, "mid": AVERAGED_LEVEL,
               "white": AVERAGED_LEVEL, "span": float, "mid_share": float,
               "separation": DEPTHS},
    eye={name: {rung: {"symbols", "errors_1", "errors_avg", "opening",
                       "best_bin"} for rung in ("50", "65", "80", "100")}
         for name in ("2-level", "3-level")})


def assert_shape(value, shape, path="results"):
    if shape is NO_RING:
        assert value == {"error": "no ring here"}, (path, value)
        return
    if shape is TEMPS:
        # Its keys are the tests the run passed through, so they are checked
        # by the temperature tests; here, only that they are degrees.
        assert isinstance(value, dict) and "error" not in value, (path, value)
        assert "start" in value and "end" in value, (path, value)
        for key in value:
            assert isinstance(value[key], float), (path, key, value[key])
        return
    if shape is int:
        assert isinstance(value, int) and not isinstance(value, bool), (path, value)
        return
    if shape is float:
        assert isinstance(value, (int, float)) and not isinstance(value, bool), \
            (path, value)
        return
    assert isinstance(value, dict), (path, value)
    assert "error" not in value, (path, value)
    expected = set(shape)
    assert set(value) == expected, (path, sorted(value), sorted(expected))
    if isinstance(shape, dict):
        for key in shape:
            assert_shape(value[key], shape[key], path + "." + key)
    else:
        for key in value:
            # A list is an eye's openings, one a phase bin; None is an eye
            # bin with nothing in it, or a depth with no spread to divide by.
            assert value[key] is None or isinstance(
                value[key], (int, float, str, list)), (path, key, value[key])


def test_spec_keys_are_all_produced():
    # Every key the spec's example names is one the engine writes.
    text = open(SPEC).read()
    block = text.split("### Measurements file", 1)[1].split("### display", 1)[0]
    # The probe's own example follows; test_probe.py checks that part.
    block = block.split("`<probe>` has one key", 1)[0]
    spec_keys = set(re.findall(r'"([A-Za-z0-9_.]+)":', block))

    def keys(shape):
        out = set()
        if isinstance(shape, dict):
            for k, v in shape.items():
                out.add(k)
                out |= keys(v)
        elif isinstance(shape, set):
            out |= shape
        elif shape is TEMPS:
            out |= {"start", "end"}
        return out
    ours = keys(MIRROR_SHAPE) | keys(SECOND_SHAPE) | {
        "mirror", "second_badge", "frame_probe", "role", "dark", "lit",
        "error"}

    # The ring tests are NO_RING in the harness; their keys come from a run
    # over a simulated ring.
    def produced(value):
        out = set()
        if isinstance(value, dict):
            for k, v in value.items():
                out.add(k)
                out |= produced(v)
        return out
    for test in (M.backlight_step_ring, M.loopback_ring):
        mirror = RingMirror(tau_us=500.0)
        ours |= produced(test(mirror.reader(), mirror.set_backlight))
    # The example elides with "..." so it names fewer than we write; it must
    # never name one we do not.
    assert spec_keys - ours == set(), spec_keys - ours


def test_mirror_run_produces_the_spec_shape_and_the_right_numbers():
    run, badge = mirror_run()
    result = run.results()
    assert_shape(result, MIRROR_SHAPE)
    json.dumps(result)          # nothing in it JSON cannot hold

    # The run took the schedule's time, near enough.
    # Less the loopback, whose blocks take their time inside single frames.
    looped = sum((len(M.SYMBOL_BLOCK) + 1) * rung for rung in M.LOOPBACK_RUNGS)
    looped += sum(3 * M.DIM_HOLD_MS + (len(M.SYMBOL_BLOCK) + 1) * rung
                  for rung in M.DIM_RUNGS)
    assert abs(badge.frames * 11 + looped - M.MIRROR_SCHEDULE_MS) < 4000

    assert result["adc_step"] == 16
    # Ambient 32 +/- 8 of noise through a quantiser that floors: 16 or 32.
    # The backlight is off and black has no light of its own.
    assert result["ambient"] in (16, 32)
    fields = result["levels"]["fields"]
    assert fields["span"] == fields["white"] - fields["black"]
    # The middle field through gamma 2.2: (mean channel / 255)^2.2.
    expected = (sum(P.MID_FIELD_RGB) / 3 / 255) ** 2.2
    assert abs(fields["mid_share"] - expected) < 0.01
    backlight = result["levels"]["backlight"]
    # This panel's light is the backlight value itself: low dim at 0.5 of
    # the 4000 swing, the middle 0.82, high 1.0.
    low, mid = P.LOW_BACKLIGHT, P.MID_BACKLIGHT
    assert abs(backlight["black"] - (32 + low * 4000)) < 100, backlight
    assert abs(backlight["mid_share"] - (mid - low) / (1 - low)) < 0.02, backlight
    assert abs(result["gamma_ramp"]["gamma"] - 2.2) < 0.05
    assert result["bleed"]["black_on"] > result["bleed"]["black_off"]
    curve = [result["backlight_curve"][k] for k in sorted(result["backlight_curve"])]
    assert curve == sorted(curve)
    assert all(v["verdict"] == "averages" for v in result["flicker"].values())

    settle = result["settle"]
    assert abs(settle["rise_tau_ms"] - 4.0) < 0.5
    assert abs(settle["fall_tau_ms"] - 3.0) < 0.5
    assert abs(settle["mid_rise_tau_ms"] - 6.0) < 0.7
    assert settle["settle_90_ms"] == round(2.3 * max(
        settle["rise_tau_ms"], settle["fall_tau_ms"], settle["mid_rise_tau_ms"]), 1) \
        or abs(settle["settle_90_ms"] - 2.3 * settle["mid_rise_tau_ms"]) < 0.2

    # Present latency 2ms, then 0.16 of a 4ms rise to reach T_low.
    latency = result["edge_latency"]
    assert 2.0 < latency["min_ms"] <= latency["mean_ms"] <= latency["max_ms"] < 4.0

    # The backlight alone: no present, no panel, a 0.3 ms single pole from
    # the call. 10-90% is ln(9) taus, half way ln(2), within a bin or two.
    step = result["backlight_step"]
    for key in ("rise", "fall", "mid_rise"):
        entry = step[key]
        assert entry["transitions"] == 10, (key, entry)
        assert abs(entry["t10_90_ms"] - 0.3 * math.log(9)) < 0.06, (key, entry)
        assert abs(entry["t50_ms"] - 0.3 * math.log(2)) < 0.05, (key, entry)
        assert entry["t10_ms"] < entry["t50_ms"] < entry["t90_ms"], (key, entry)
    assert step["bin_us"] == M.CURVE_BIN_US
    assert step["read_us"] == Clock.READ_US

    timing = result["frame_timing"]
    # Frames that followed a capture or burst are left out, so none is 60ms.
    assert timing["max_ms"] <= 13 and timing["min_ms"] >= 11


def test_mirror_run_on_the_measured_panel_still_has_the_shape():
    # The real badge: an 80-count swing and 16 counts of noise either way.
    run, _ = mirror_run(span=80, noise=12)
    result = run.results()
    for key in MIRROR_SHAPE:
        assert key in result
    assert result["adc_step"] == 16
    assert_shape(result["levels"], MIRROR_SHAPE["levels"])
    assert result["levels"]["fields"]["span"] in (64, 80, 96)
    json.dumps(result)


def test_flicker_follows_a_sensor_that_follows_the_pwm():
    run, _ = mirror_run(pwm=True)
    flicker = run.results()["flicker"]
    assert flicker["1.0"]["verdict"] == "averages"
    for key in ("0.25", "0.5", "0.74", "0.9"):
        assert flicker[key]["verdict"] == "follows_pwm", (key, flicker[key])


def test_mirror_run_names_the_test_in_progress():
    clock = Clock()
    panel = Panel(clock)
    run = M.MirrorRun(Sensor(clock, panel), clock.us)
    badge = Badge(clock, run, panel)
    run.begin(clock.ms())
    seen = []
    while not run.done:
        clock.t = max(clock.t, badge.next_frame)
        badge.frame()
        label = (run.test_number, run.test_name)
        if run.test_name and (not seen or seen[-1] != label):
            seen.append(label)
    assert seen == [(1, "ambient"), (3, "levels"), (5, "gamma_ramp"),
                    (6, "backlight_curve"), (7, "bleed"), (8, "flicker"),
                    (9, "settle"), (10, "backlight_step"), (11, "loopback"),
                    (12, "backlight_step_ring"), (13, "loopback_ring")]
    assert run.test_count == 15


# --- temperature ------------------------------------------------------------

def test_core_temp_c_is_the_datasheet_line():
    at_27 = 0.706 / 3.3 * 65535
    assert M.core_temp_c(at_27) == 27.0
    # 1.721 mV a degree, falling as it warms.
    assert M.core_temp_c(at_27 - 0.01721 / 3.3 * 65535) == 37.0
    assert M.core_temp_c(at_27 + 0.01721 / 3.3 * 65535) == 17.0


def test_a_mirror_run_reads_the_temperature_at_every_test():
    run, _ = mirror_run()
    temps = run.results()["temperature_c"]
    tested = []
    for step in M.MIRROR_SCHEDULE:
        if step.test not in tested:
            tested.append(step.test)
    assert list(temps) == ["start"] + tested + ["end"], list(temps)
    values = list(temps.values())
    assert values == sorted(values) and values[0] == 25.0, values


def test_a_2nd_badge_run_keeps_the_turn_it_sent_apart():
    run_a, run_b = two_badges()
    for run in (run_a, run_b):
        temps = run.results()["temperature_c"]
        assert "sent_ambient" in temps and "ambient" in temps, list(temps)
        assert "sent_eye" in temps and "eye" in temps, list(temps)
        assert list(temps)[0] == "start" and list(temps)[-1] == "end"


def test_no_temperature_sensor_is_recorded_not_raised():
    clock = Clock()
    panel = Panel(clock)
    run = M.MirrorRun(Sensor(clock, panel), clock.us)
    run.begin(clock.ms())
    assert run.results()["temperature_c"] == {"error": "no temperature sensor"}


def test_a_step_with_no_readings_is_an_error_not_a_crash():
    recorder = M.Recorder(M.MIRROR_SCHEDULE, M.TOOL_MIRROR, lambda: 0, lambda: 0)
    result = recorder.results(M.FrameStats().result())
    # The recorder's own results; temperature_c is the run's.
    assert set(result) == set(M.MIRROR_TESTS)
    assert result["ambient"] == {"error": "no readings"}
    assert result["adc_step"] == {"error": "no nonzero reading"}
    assert result["levels"]["fields"] == {"error": "no readings"}
    assert result["frame_timing"] == {"error": "no frames"}
    assert result["edge_latency"] == {"error": "no levels"}
    for test in result:
        assert "error" in json.dumps(result[test])


# --- the measurements file --------------------------------------------------

def test_merge_replaces_only_its_own_tool():
    existing = {"mirror": {"dark": {"ambient": 32}},
                "second_badge": {"role": "first", "dark": {"ambient": 16}}}
    merged = M.merge_results(existing, M.TOOL_MIRROR,
                             M.tool_result({"ambient": 48}, {"ambient": 288}))
    assert merged["second_badge"] == existing["second_badge"]
    assert merged["mirror"] == {"dark": {"ambient": 48}, "lit": {"ambient": 288}}
    assert existing["mirror"] == {"dark": {"ambient": 32}}      # not mutated

    merged = M.merge_results(merged, M.TOOL_SECOND_BADGE,
                             M.tool_result({"ambient": 1}, None, role="second"))
    assert merged["mirror"] == {"dark": {"ambient": 48}, "lit": {"ambient": 288}}
    assert merged["second_badge"] == {"role": "second", "dark": {"ambient": 1}}

    assert M.merge_results(None, M.TOOL_MIRROR, {"dark": {}}) == {"mirror": {"dark": {}}}
    assert M.merge_results(False, M.TOOL_MIRROR, {}) == {"mirror": {}}


def test_format_value_writes_json():
    for value in (32, 0.4, 2.32, -1, "follows_pwm", 'say "hi"', None, True, 1.0):
        assert M.format_value(value) == json.dumps(value)


def sample_file():
    return {
        "second_badge": {"role": "first",
                         "dark": {"ambient": 16, "settle": {"error": "no rise transition fitted"}}},
        "mirror": {"dark": {
            "adc_step": 16, "ambient": 32,
            "levels": {"fields": {"mid_share": 0.4, "white": 112, "span": 80,
                                  "black": 32, "mid": 64}},
            "gamma_ramp": {"100": 112, "gamma": 2.32, "25": 32, "0": 32},
            "backlight_curve": {"1.0": 112, "0.0": 16, "0.5": 64},
            "flicker": {"1.0": {"verdict": "averages", "min": 96}},
        }},
    }


def test_sections_are_titled_by_json_path_in_the_files_order():
    got = M.sections(sample_file())
    titles = [title for title, _ in got]
    assert titles == ["mirror.dark", "mirror.dark.levels.fields",
                      "mirror.dark.gamma_ramp", "mirror.dark.backlight_curve",
                      "mirror.dark.flicker.1.0", "second_badge",
                      "second_badge.dark", "second_badge.dark.settle"]
    by_title = dict(got)
    assert by_title["mirror.dark"] == ["ambient: 32", "adc_step: 16"]
    assert by_title["mirror.dark.levels.fields"] == [
        "black: 32", "mid: 64", "white: 112", "span: 80", "mid_share: 0.4"]
    assert by_title["mirror.dark.gamma_ramp"] == [
        "0: 32", "25: 32", "100: 112", "gamma: 2.32"]
    assert by_title["mirror.dark.backlight_curve"] == [
        "0.0: 16", "0.5: 64", "1.0: 112"]
    assert by_title["mirror.dark.flicker.1.0"] == ["min: 96", 'verdict: "averages"']
    assert by_title["second_badge"] == ['role: "first"']
    assert by_title["second_badge.dark.settle"] == [
        'error: "no rise transition fitted"']


def test_pages_split_long_sections_with_cont_titles():
    got = M.pages(sample_file(), 3)
    titles = [title for title, _ in got]
    assert titles[:4] == ["mirror.dark", "mirror.dark.levels.fields",
                          "mirror.dark.levels.fields (cont.)",
                          "mirror.dark.gamma_ramp"]
    assert got[1][1] == ["black: 32", "mid: 64", "white: 112"]
    assert got[2][1] == ["span: 80", "mid_share: 0.4"]
    assert all(1 <= len(lines) <= 3 for _, lines in got)
    # Every line of every section shows up exactly once.
    flat = [line for _, lines in got for line in lines]
    assert flat == [line for _, lines in M.sections(sample_file()) for line in lines]
    assert M.pages({}, 5) == []
    assert len(M.pages(sample_file(), 0)) == len(flat)


def test_wrapped_lines_keep_their_rows_together_on_a_page():
    # A line of n words wraps to n rows here.
    def wrap(line):
        return [(word, i > 0) for i, word in enumerate(line.split(" "))]
    file_obj = {"s": {"a": "x", "b": "one two three", "c": "y"}}
    got = M.pages(file_obj, 4, wrap)
    assert got == [
        ("s", [("a:", False), ('"x"', True)]),
        ("s (cont.)", [("b:", False), ('"one', True), ("two", True),
                       ('three"', True)]),
        ("s (cont.)", [("c:", False), ('"y"', True)])]


def test_a_line_longer_than_a_page_is_split_across_pages():
    def wrap(line):
        return line.split(" ")
    file_obj = {"s": {"a": "1 2 3 4 5 6 7", "b": "z"}, "t": {"c": 1}}
    got = M.pages(file_obj, 3, wrap)
    assert [title for title, _ in got] == ["s", "s (cont.)", "s (cont.)",
                                           "s (cont.)", "t"]
    assert [row for _, rows in got[:4] for row in rows] == [
        "a:", '"1', "2", "3", "4", "5", "6", '7"', "b:", '"z"']
    # The long line fills whole pages; b's two rows do not fit beside its
    # last one, so they start a page of their own rather than split.
    assert got[2][1] == ["6", '7"']
    assert got[3][1] == ["b:", '"z"']
    assert all(len(rows) <= 3 for _, rows in got)


def test_a_whole_run_pages_and_round_trips_through_json():
    run, _ = mirror_run()
    dark = run.results()
    file_obj = json.loads(json.dumps(
        M.merge_results({}, M.TOOL_MIRROR, M.tool_result(dark, dark))))
    got = M.pages(file_obj, 8)
    titles = [t for t, _ in got]
    assert "mirror.dark.levels.fields" in titles
    assert "mirror.lit.noise.backlight.white" in titles
    assert "mirror.dark.flicker.0.74" in titles
    # Typing the pages back in rebuilds the file.
    rebuilt = {}
    for title, lines in got:
        node = rebuilt
        path = title.replace(" (cont.)", "")
        for part in split_path(path, file_obj):
            node = node.setdefault(part, {})
        for line in lines:
            key, value = line.split(": ", 1)
            node[key] = json.loads(value)
    assert rebuilt == file_obj


def split_path(path, file_obj):
    # Keys such as "0.74" contain the separator, so walk the real object.
    parts = []
    node = file_obj
    rest = path
    while rest:
        for key in sorted(node, key=len, reverse=True):
            if rest == key or rest.startswith(key + "."):
                parts.append(key)
                node = node[key]
                rest = rest[len(key) + 1:]
                break
        else:
            raise AssertionError("no key for " + rest)
    return parts


# --- MicroPython hygiene ----------------------------------------------------

def test_measure_references_no_badge_globals():
    import test_module_boundary as boundary
    path = os.path.join(os.path.dirname(P.__file__), "measure.py")
    _, read = boundary.loaded_names(path)
    leaked = sorted(read & (boundary.BADGE_GLOBALS | {"display"}))
    assert not leaked, "measure.py reaches for the badge: %s" % leaked


def test_measure_avoids_what_micropython_lacks():
    import ast
    path = os.path.join(os.path.dirname(P.__file__), "measure.py")
    source = open(path).read()
    assert "%r" not in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            assert not any(k.arg is None for k in node.keywords), \
                "** unpacking on line %d" % node.lineno
        assert not isinstance(node, (ast.JoinedStr, ast.NamedExpr, ast.Global)), \
            "line %d" % getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            assert all(a.name in ("math", "time") for a in node.names)


# --- the handshake ----------------------------------------------------------

def test_handshake_constants_are_the_specs():
    assert (M.PULSE_HIGH_MS, M.PULSE_PERIOD_MS, M.REPLY_DELAY_MS) == (60, 360, 180)
    assert (M.REPLY_EARLIEST_MS, M.REPLY_LATEST_MS) == (120, 270)
    assert M.REPLIES_NEEDED == 3
    assert (M.GO_MS, M.GO_GAP_MS, M.TURN_END_MS) == (600, 1000, 1000)
    assert M.HANDSHAKE_TIMEOUT_MS == 60000 and M.NOISE_MS == 1000
    assert (M.PULSE_MIN_MS, M.PULSE_MAX_MS) == (20, 150)
    assert M.PULSE_MIN_SWING == 48
    # The window ends before the caller's next pulse and starts after its echo.
    assert M.ECHO_MS < M.REPLY_EARLIEST_MS < M.REPLY_DELAY_MS
    assert M.REPLY_LATEST_MS + M.PULSE_HIGH_MS // 2 < M.PULSE_PERIOD_MS
    # A go is longer than 3 pulse widths, and longer than a settle step's white.
    assert M.GO_MIN_MS > 3 * M.PULSE_HIGH_MS and M.GO_MIN_MS > M.SETTLE_GAP_MS


def panel_reads(white, total_ms, frame_ms=11, span=80, black=32, tau=4.0,
                step=16, start_ms=0):
    """(t, read) once a frame of a peer whose share of white is `white(t)`.

    A single-pole panel, quantised like the badge's sensor.
    """
    out = []
    level = 0.0
    t = start_ms
    k = 1 - math.exp(-frame_ms / tau)
    while t < total_ms:
        level += (white(t) - level) * k
        out.append((t, (black + int(level * span)) // step * step))
        t += frame_ms
    return out


def call_train(start_ms=1000, count=3, go=False):
    """A caller's field: `count` call pulses from start_ms, then a go if asked."""
    go_at = start_ms + count * M.PULSE_PERIOD_MS

    def white(t):
        e = t - start_ms
        if 0 <= e < count * M.PULSE_PERIOD_MS:
            return 1.0 if e % M.PULSE_PERIOD_MS < M.PULSE_HIGH_MS else 0.0
        if go and go_at <= t < go_at + M.GO_MS:
            return 1.0
        return 0.0
    return white, go_at


def pulse_events(samples, threshold=48):
    detector = M.PulseDetector()
    detector.threshold = threshold
    events = []
    for t, v in samples:
        event = detector.push(t, v)
        if event != M.PULSE_NONE:
            events.append((event, detector.rise, detector.fall))
    return events


def test_call_pulses_are_found_on_an_80_count_swing():
    white, _ = call_train(count=4)
    events = pulse_events(panel_reads(white, 3000))
    assert [e[0] for e in events] == [M.PULSE_SEEN] * 4
    for k, (_, rise, fall) in enumerate(events):
        start = 1000 + k * M.PULSE_PERIOD_MS
        # Interpolated between the reads either side: within a frame.
        assert abs(rise - start) <= 11, (k, rise)
        assert 40 <= fall - rise <= 80


def test_a_go_is_found_at_its_fall():
    white, go_at = call_train(count=3, go=True)
    events = pulse_events(panel_reads(white, 4000))
    assert [e[0] for e in events] == [M.PULSE_SEEN] * 3 + [M.PULSE_GO]
    _, rise, fall = events[-1]
    assert abs(rise - go_at) <= 11
    assert abs(fall - (go_at + M.GO_MS)) <= 11


def test_a_held_field_or_a_small_swing_is_neither():
    for width in (300, 1000, 3000):          # settle's whites, a level step
        events = pulse_events(panel_reads(
            lambda t: 1.0 if 1000 <= t < 1000 + width else 0.0, 6000))
        assert events == [], width
    white, _ = call_train(count=4, go=True)
    # 32 counts of swing is under the 48 floor.
    assert pulse_events(panel_reads(white, 4000, span=32)) == []
    # And a noisy room's threshold of 96 hides an 80-count panel.
    assert pulse_events(panel_reads(white, 4000), threshold=96) == []
    still = [(t, 32 + (16 if (t // 11) % 3 == 0 else 0)) for t in range(0, 5000, 11)]
    assert pulse_events(still) == []


def schedule_reads(span=4000, steps=None):
    """A peer showing the 2nd-badge schedule, through the simulated panel."""
    schedule = M.Cursor(steps or M.SECOND_BADGE_SCHEDULE)
    panel = Panel(Clock())

    def white(t):
        pixels, backlight = schedule.field(t)
        return panel.luminance(pixels, backlight)
    total = M.schedule_ms(steps or M.SECOND_BADGE_SCHEDULE)
    return panel_reads(white, total, span=span)


def test_the_settle_steps_cannot_pass_for_a_call_or_a_go():
    # The settle test alternates white and black every 300 ms. The start mark
    # this handshake replaced had that rhythm at first, and a badge that missed
    # the real mark locked onto the settle section and measured the rest of the
    # run out of step, reporting success.
    settle = [s for s in M.SECOND_BADGE_SCHEDULE if s.test == "settle"]
    for span in (80, 4000):
        assert pulse_events(schedule_reads(span, settle)) == [], span
    # ...which a go of any length over 180 ms, as the spec has it bare, is not.
    saved = M.GO_MIN_MS, M.GO_MAX_MS
    try:
        M.GO_MIN_MS, M.GO_MAX_MS = 3 * M.PULSE_HIGH_MS + 1, 10 ** 9
        assert M.PULSE_GO in [e[0] for e in pulse_events(
            schedule_reads(4000, settle))]
    finally:
        M.GO_MIN_MS, M.GO_MAX_MS = saved


def test_an_answerer_does_not_take_the_schedule_for_a_go():
    # A badge that missed the real go is still answering while the other
    # sends. The symbol blocks hold a ten-slot run that is never black (500
    # and 650 ms at the two fastest rungs) and single high slots that are
    # pulses, so the detector alone can be fooled; a go must also come a
    # period after the call pulse it follows, and none of these does.
    # Whether the detector alone is fooled depends on the schedule: with the
    # backlight scheme's low dim rather than off it no longer is. The run
    # below must hold either way.
    reads = schedule_reads(4000)
    # Answering three real calls first, as a badge that missed the go was.
    white, _ = call_train(start_ms=0, count=3)
    lead = panel_reads(white, 1500, span=4000)
    current = [32]
    run = M.SecondBadgeRun(False, lambda: current[0], lambda: 0)
    run.begin(-M.NOISE_MS)
    # The schedule (72 s) outlasts the handshake timeout (60 s): until then
    # the badge answers, and after it gives up -- but it is never fooled into
    # measuring a schedule it never saw the go for.
    for t, v in lead + [(1500 + t, v) for t, v in reads]:
        current[0] = v
        run.frame(t, 11)
        assert run.phase != M.PHASE_MEASURING, t
        if t + M.NOISE_MS < M.HANDSHAKE_TIMEOUT_MS:
            assert run.phase == M.PHASE_ANSWERING, t
    assert run.calls >= 1


class Scripted:
    """One run against a scripted peer, a frame every `frame_ms`.

    `peer(t)` is the share of white the peer shows at t; the run's own field
    reaches its sensor too when `echo` is set. Every frame's field is kept in
    `shown`, so a scripted peer can answer what the run drew.
    """

    def __init__(self, first, peer, frame_ms=11, span=80, black=32, echo=0.0,
                 tau=4.0):
        self.run = M.SecondBadgeRun(first, self.light, lambda: 0)
        self.peer = peer
        self.frame_ms = frame_ms
        self.span = span
        self.black = black
        self.echo = echo
        self.k = 1 - math.exp(-frame_ms / tau)
        self.level = 0.0
        self.own = 0.0
        self.t = 0
        self.shown = []
        self.phases = []
        self.go = None          # when a calling run's go went up

    def light(self):
        self.level += (self.peer(self.t) - self.level) * self.k
        target = 1.0 if self.run.pixels == "white" else 0.0
        self.own += (target - self.own) * self.k
        value = self.black + int((self.level + self.echo * self.own) * self.span)
        return value // 16 * 16

    def until(self, done, limit_ms=200000):
        run = self.run
        if not self.shown:
            run.begin(self.t)
        while not done(self) and self.t < limit_ms:
            run.frame(self.t, self.frame_ms)
            if self.go is None and run.phase == M.PHASE_CALLING and \
                    run.go_start is not None:
                self.go = run.go_start
            self.shown.append((self.t, run.pixels, run.backlight))
            if not self.phases or self.phases[-1] != run.phase:
                self.phases.append(run.phase)
            self.t += self.frame_ms
        return self


def call_starts(script):
    """When the scripted caller drew each call pulse (not the go)."""
    starts = []
    prev = None
    go = script.go
    for t, pixels, _ in script.shown:
        if pixels == "white" and prev != "white" and (go is None or t < go):
            starts.append(t)
        prev = pixels
    return starts


def caller(delay=190, skip=(), echo=0.0, answers=True, frame_ms=11, span=80):
    """A first badge calling a scripted peer.

    The peer answers `delay` ms after each frame that drew a call pulse,
    except the pulses whose index is in `skip`; or never, without `answers`.
    """
    script = None

    def peer(t):
        if not answers:
            return 0.0
        for k, start in enumerate(call_starts(script)):
            if k not in skip and start + delay <= t < start + delay + M.PULSE_HIGH_MS:
                return 1.0
        return 0.0
    script = Scripted(True, peer, frame_ms=frame_ms, echo=echo, span=span)
    return script


def go_cycle(script):
    run = script.run
    script.until(lambda s: s.go is not None or s.run.no_handshake,
                 limit_ms=M.NOISE_MS + M.HANDSHAKE_TIMEOUT_MS + 100)
    if script.go is None:
        return None
    return (script.go - run.handshake_at) // M.PULSE_PERIOD_MS


def test_the_go_follows_three_answers_in_a_row():
    # Answered from the first pulse: the go is where the fourth would be.
    assert go_cycle(caller()) == 3
    for frame_ms in (8, 16, 22):
        assert go_cycle(caller(frame_ms=frame_ms)) == 3, frame_ms


def test_a_missed_answer_starts_the_count_again():
    # Pulse 1 unanswered: 0 counts, then 2, 3, 4 make three in a row.
    assert go_cycle(caller(skip=(1,))) == 5
    assert go_cycle(caller(skip=(0, 2, 4))) == 8


def test_only_an_answer_inside_the_window_counts():
    for delay in (130, 180, 250):
        assert go_cycle(caller(delay=delay)) == 3, delay
    for delay in (60, 100, 290, 320):
        script = caller(delay=delay)
        assert go_cycle(script) is None, delay
        assert script.run.no_handshake


def test_the_callers_own_echo_is_not_an_answer():
    # 30% of its own panel in its own sensor, on a bright panel so the echo
    # is far over the threshold, and no one answering.
    script = caller(echo=0.3, answers=False, span=4000)
    seen = []
    run = script.run
    original = run._answered
    run._answered = lambda rise: (seen.append(rise), original(rise))
    assert go_cycle(script) is None and run.no_handshake
    # The echo is seen as a pulse, every cycle, but never counted.
    assert len(seen) > 100 and run.replies == 0
    # ...and with an answer as well, the echo does not get in the way.
    assert go_cycle(caller(echo=0.3, span=4000)) == 3


def test_the_caller_shows_the_go_then_black_then_the_schedule():
    script = caller()
    run = script.run
    script.until(lambda s: s.run.phase == M.PHASE_ANSWERING
                 and s.run.turn == 2)
    go = script.go
    shown = dict((t, (p, b)) for t, p, b in script.shown)
    times = sorted(shown)
    white = [t for t in times if go <= t < go + M.GO_MS]
    assert all(shown[t] == M.WHITE_FULL for t in white)
    zero = min(t for t in times if t >= go + M.GO_MS)      # the go's fall
    assert run.epoch == zero + M.GO_GAP_MS
    assert all(shown[t] == M.BLACK_FULL for t in times
               if zero <= t < zero + M.GO_GAP_MS)
    schedule = M.Cursor(M.SECOND_BADGE_SCHEDULE)
    begin = zero + M.GO_GAP_MS
    for t in times:
        if begin <= t < begin + M.SECOND_BADGE_SCHEDULE_MS:
            assert shown[t] == schedule.field(t - begin), t
    # The first badge listens from the end of its own schedule.
    assert begin + M.SECOND_BADGE_SCHEDULE_MS <= script.t - 11 \
        < begin + M.SECOND_BADGE_SCHEDULE_MS + 11
    assert script.phases == [M.PHASE_NOISE, M.PHASE_CALLING, M.PHASE_SENDING,
                             M.PHASE_ANSWERING]


def calling_peer(start_ms, go_after=None):
    """A peer calling from start_ms, with a go in place of pulse `go_after`."""
    def white(t):
        e = t - start_ms
        if e < 0:
            return 0.0
        k = e // M.PULSE_PERIOD_MS
        if go_after is not None and k >= go_after:
            g = e - go_after * M.PULSE_PERIOD_MS
            return 1.0 if g < M.GO_MS else 0.0
        return 1.0 if e % M.PULSE_PERIOD_MS < M.PULSE_HIGH_MS else 0.0
    return white


def answer_starts(script):
    starts = []
    prev = None
    for t, pixels, _ in script.shown:
        if pixels == "white" and prev != "white":
            starts.append(t)
        prev = pixels
    return starts


def test_the_answer_goes_up_half_a_period_after_the_call():
    for frame_ms in (8, 11, 16):
        start = 1500
        script = Scripted(False, calling_peer(start), frame_ms=frame_ms)
        script.until(lambda s: s.t > start + 8 * M.PULSE_PERIOD_MS)
        starts = answer_starts(script)
        assert len(starts) >= 7, starts
        for k, at in enumerate(starts):
            call = start + k * M.PULSE_PERIOD_MS
            # The rise corrected by the edge lag, and the frame it lands in.
            assert abs(at - call - M.REPLY_DELAY_MS) <= frame_ms, \
                (frame_ms, k, at)
        # Timed from the rise less Dev Mode's edge_lag(): half a frame and
        # a tenth of LCD_SETTLE_MS.
        run = script.run
        lag = frame_ms // 2 + P.LCD_SETTLE_MS // 10
        assert run.answer_at - run.last_call == M.REPLY_DELAY_MS - lag


def test_the_answerer_ignores_its_own_echo():
    start = 1500
    # On a bright panel, so the echo is far over the threshold.
    script = Scripted(False, calling_peer(start), echo=0.3, span=4000)
    script.until(lambda s: s.t > start + 8 * M.PULSE_PERIOD_MS)
    starts = answer_starts(script)
    # One answer per call, each half a period on: none to an echo.
    assert len(starts) == 8
    for k, at in enumerate(starts):
        assert abs(at - (start + k * M.PULSE_PERIOD_MS) - M.REPLY_DELAY_MS) <= 22


def test_the_answerer_measures_from_the_go_and_stops_answering():
    start = 1500
    script = Scripted(False, calling_peer(start, go_after=4))
    go_at = start + 4 * M.PULSE_PERIOD_MS
    script.until(lambda s: s.run.phase != M.PHASE_ANSWERING
                 and s.run.phase != M.PHASE_NOISE)
    run = script.run
    assert run.phase == M.PHASE_MEASURING
    fall = go_at + M.GO_MS
    assert abs(run.recorder.epoch - M.GO_GAP_MS - fall) <= 11
    assert len(answer_starts(script)) == 4


# --- the noise second -------------------------------------------------------

def test_the_threshold_is_half_again_the_noise_and_never_below_48():
    assert M.pulse_threshold(None) == 48
    assert M.pulse_threshold(0) == 48
    assert M.pulse_threshold(32) == 48
    assert M.pulse_threshold(64) == 96
    assert M.pulse_threshold(160) == 240


def test_the_noise_is_twice_the_spread_to_the_median():
    assert M.noise_spread([32] * 7, 7) is None          # too few reads
    assert M.noise_spread([32] * 8, 8) == 0
    # Evenly spread noise: twice lowest-to-median is about its peak-to-peak.
    assert M.noise_spread([32, 48, 64, 80] * 20, 80) == 32
    assert M.noise_spread([32, 48, 64] * 20, 60) == 32
    # The other badge's calls -- a sixth of the time, or even two fifths with
    # their settling -- cannot reach the median, so they are not noise.
    for lit in (15, 25, 40):
        reads = [32] * (100 - lit) + [4032] * lit
        random.Random(lit).shuffle(reads)
        assert M.noise_spread(reads, len(reads)) == 0, lit
    # More than half the reads lit is not a still screen at all.
    assert M.noise_spread([32] * 40 + [4032] * 60, 100) == 8000
    # Only the first `count` reads count.
    assert M.noise_spread([32] * 10 + [4032] * 10, 10) == 0


def noise_run(first, reads):
    """A run fed `reads(t)` through its noise second; returns it."""
    clock = [0]
    run = M.SecondBadgeRun(first, lambda: reads(clock[0]), lambda: 0)
    run.begin(0)
    t = 0
    while run.phase == M.PHASE_NOISE:
        clock[0] = t
        run.frame(t, 11)
        t += 11
    return run, t


def test_the_threshold_comes_from_the_runs_first_second():
    # Not from the start delay: the hand that pressed A may still be moving
    # the badge then. The run opens on a second of black and reads it.
    for first, phase in ((False, M.PHASE_ANSWERING), (True, M.PHASE_CALLING)):
        # Three levels evenly spread: 64 counts peak to peak.
        run, ended = noise_run(first, lambda t: 32 + 32 * ((t // 11) % 3))
        assert M.NOISE_MS <= ended < M.NOISE_MS + 30
        assert run.phase == phase
        assert run.pulse_noise == 64
        assert run.pulse_threshold == 96 == run.detector.threshold


def test_a_still_screen_leaves_the_floor():
    run, _ = noise_run(False, lambda t: 32)
    assert run.pulse_noise == 0 and run.pulse_threshold == 48


def test_the_peers_calls_during_the_noise_second_leave_the_floor():
    # A second badge started while the first is already calling sees two or
    # three of its pulses in the noise second, through the panel's settling.
    # On the measured 80-count panel the bar stays at the floor; on a bright
    # one the settling tails can lift it a little, never out of reach. The
    # peak-to-peak spread would have put it above the pulses themselves.
    for span, most in ((80, 48), (4000, 4000 // 4)):
        for phase in range(0, 360, 30):
            white, _ = call_train(start_ms=-phase, count=10)
            reads = dict(panel_reads(white, 1200, span=span))
            run, _ = noise_run(False, lambda t: reads[t])
            assert run.pulse_threshold <= most, (span, phase, run.pulse_noise)
            p2p = M.peak_to_peak(run.noise, run.noise_count)
            assert M.pulse_threshold(p2p) > span, (span, phase)


# --- 2nd-badge runs ---------------------------------------------------------

def assert_clean(run):
    result = run.results()
    assert not run.no_handshake, run.role
    assert_shape(result, SECOND_SHAPE)
    json.dumps(result)
    for scheme in ("fields", "backlight"):
        for rung in ("50", "65", "80"):
            assert result["symbol_errors"][scheme][rung] == \
                {"symbols": 29, "errors": 0}, (run.role, scheme, rung)
    for name, symbols in (("2-level", 19), ("3-level", 29)):
        for rung in ("50", "65", "80", "100"):
            eye = result["eye"][name][rung]
            assert eye["symbols"] == symbols, (run.role, name, rung)
            assert eye["errors_avg"] == 0 and eye["errors_1"] == 0, \
                (run.role, name, rung, eye)
            # Open somewhere -- not necessarily in the last fifth: phase 0 is
            # when the sync was first seen, a little after it went up, so a
            # slot's last few ms can already show the next symbol coming.
            best = eye["best_bin"]
            assert best is not None and eye["opening"][best] > 0, \
                (run.role, name, rung, eye)
    averaging = result["averaging"]
    assert averaging["black"]["mean"] < averaging["mid"]["mean"] \
        < averaging["white"]["mean"]
    assert averaging["separation"]["64"] >= averaging["separation"]["1"]
    return result


def test_two_badges_measure_each_other_and_decode_every_symbol():
    for ambient in (32, 1500):                      # a dark room, a lit one
        run_a, run_b = two_badges(ambient=ambient)
        assert (run_a.role, run_b.role) == ("first", "second")
        for run in (run_a, run_b):
            result = assert_clean(run)
            assert abs(result["gamma_ramp"]["gamma"] - 2.2) < 0.1
        # Each measured the other's panel: B's is the slower one.
        assert run_a.results()["settle"]["rise_tau_ms"] > \
            run_b.results()["settle"]["rise_tau_ms"]


def test_badges_started_far_apart_still_find_each_other():
    # The second started half a second, 5 s and 30 s after the first, and
    # the first 5 s after the second.
    for offset_s in (0.5, 5, 30, -5):
        run_a, run_b = two_badges(second_offset_us=int(offset_s * 1000000),
                                  limit=100000)
        for run in (run_a, run_b):
            assert_clean(run)


def test_an_echo_of_a_badges_own_screen_does_not_stop_the_run():
    run_a, run_b = two_badges(echo=0.3)
    for run in (run_a, run_b):
        assert_clean(run)


def phases_of(runs):
    seen = {}
    original = M.SecondBadgeRun.frame

    def framing(self, now, frame_ms):
        kind = original(self, now, frame_ms)
        trail = seen.setdefault(self.role, [])
        if not trail or trail[-1] != self.phase:
            trail.append(self.phase)
        return kind
    M.SecondBadgeRun.frame = framing
    try:
        runs()
    finally:
        M.SecondBadgeRun.frame = original
    return seen


def test_each_badge_goes_through_both_turns():
    seen = phases_of(two_badges)
    assert seen["first"] == [M.PHASE_NOISE, M.PHASE_CALLING, M.PHASE_SENDING,
                             M.PHASE_ANSWERING, M.PHASE_MEASURING,
                             M.PHASE_DONE]
    assert seen["second"] == [M.PHASE_NOISE, M.PHASE_ANSWERING,
                              M.PHASE_MEASURING, M.PHASE_CALLING,
                              M.PHASE_SENDING, M.PHASE_DONE]


def test_both_badges_finish_together():
    # The first badge finishes at the end of the second turn's closing black
    # as measured from the second go, the second as it drew it.
    finished = {}
    original = M.SecondBadgeRun._finish

    def recording(self):
        finished[self.role] = self.last_now
        original(self)
    original_frame = M.SecondBadgeRun.frame

    def framing(self, now, frame_ms):
        self.last_now = now
        return original_frame(self, now, frame_ms)
    M.SecondBadgeRun._finish = recording
    M.SecondBadgeRun.frame = framing
    try:
        two_badges()
    finally:
        M.SecondBadgeRun._finish = original
        M.SecondBadgeRun.frame = original_frame
    assert abs(finished["first"] - finished["second"]) <= 50, finished


def alone(first):
    """A run whose peer never shows anything."""
    script = Scripted(first, lambda t: 0.0)
    script.until(lambda s: s.run.no_handshake)
    gave_up = script.t - 11
    script.until(lambda s: s.run.done)
    return script, gave_up


def test_a_badge_with_no_answer_records_no_handshake_after_60s():
    for first in (True, False):
        script, gave_up = alone(first)
        run = script.run
        start = M.NOISE_MS
        assert start + 60000 <= gave_up < start + 60000 + 11, (first, gave_up)
        # It holds the screen long enough for the message to be read.
        assert M.NO_ANSWER_HOLD_MS <= script.t - gave_up \
            < M.NO_ANSWER_HOLD_MS + 30
        assert run.phase == M.PHASE_DONE
        result = run.results()
        assert set(result) == set(M.SECOND_BADGE_TESTS) | {"temperature_c"}
        for test in M.SECOND_BADGE_TESTS:
            if test == "frame_timing":
                assert result[test]["frames"] > 5000
            else:
                assert result[test] == {"error": "no handshake"}, test


def test_a_peer_that_never_sees_the_calls_ends_both_badges():
    # The first badge's screen never reaches the second: the second answers
    # nothing, so the first hears nothing back, and both give up.
    run_a, run_b = two_badges(first_present=False, limit=100000)
    for run in (run_a, run_b):
        assert run.no_handshake
        assert run.results()["levels"] == {"error": "no handshake"}
        assert "error" not in run.results()["frame_timing"]


def test_a_handshake_that_fails_after_measuring_keeps_the_measurements():
    # The first badge stops answering in the second turn: the second badge
    # has measured already and keeps it; the first has measured nothing.
    original = M.SecondBadgeRun._heard

    def deaf_in_turn_two(self, event):
        if self.first and self.turn == 2:
            return
        original(self, event)
    M.SecondBadgeRun._heard = deaf_in_turn_two
    try:
        run_a, run_b = two_badges(limit=100000)
    finally:
        M.SecondBadgeRun._heard = original
    assert run_a.no_handshake and run_b.no_handshake
    assert_shape(run_b.results(), SECOND_SHAPE)
    result = run_a.results()
    assert result["levels"] == {"error": "no handshake"}
    assert "error" not in result["frame_timing"]


def test_adc_step_counts_the_handshakes_reads_too():
    run_a, run_b = two_badges()
    for run in (run_a, run_b):
        assert run.results()["adc_step"] == 16


# --- the DMA ring on the mirror (M4, M5) ---------------------------------------
# Through the production RingReader over a ring that fills as a fake clock
# runs, its readings the badge's own backlight seen through a first-order
# sensor: what the badge would read off a mirror, minus the noise.

READER = load_module("share_wifi", "reader")


class RingMirror:
    """A clock that moves 5 us a call, a backlight, and a ring of 20 us
    readings following it with time constant `tau_us`."""

    # Raw 8, 16, 28: 128, 256 and 448 counts -- a swing wide enough that
    # whole raw steps do not hide a crossing's time.
    LEVELS = {0.5: 8.0, 0.82: 16.0, 1.0: 28.0}

    def __init__(self, tau_us=500.0):
        self.t = 0
        self.tau = tau_us
        self.target = 8.0
        self.level = 8.0
        mirror = self

        self.ring = READER.ListRing(16384, 20)

    def ticks_us(self):
        # The DMA writes as time passes, whoever is looking.
        self.t += 5
        ring = self.ring
        while ring.written < self.t // 20:
            self.level += (self.target - self.level) \
                * (1 - math.exp(-20.0 / self.tau))
            ring.push(int(round(self.level)))
        return self.t

    def set_backlight(self, value):
        self.target = self.LEVELS.get(round(value, 2), 28.0 * value)

    def reader(self):
        return READER.RingReader(self.ring, self.ticks_us)


def test_backlight_step_ring_times_each_transition_from_the_call():
    mirror = RingMirror(tau_us=500.0)
    out = M.backlight_step_ring(mirror.reader(), mirror.set_backlight)
    assert out["levels"] == [128, 256, 448], out["levels"]
    for kind, _, _ in M.RING_STEP_KINDS:
        result = out[kind]
        assert result["count"] == M.RING_STEP_TRANSITIONS, (kind, result)
        # A first-order step: half way at tau ln 2, 0.35 ms, to a 100 us bin.
        assert 0.2 <= result["t50_ms"] <= 0.5, (kind, result)
        assert result["t10_ms"] <= result["t50_ms"] <= result["t90_ms"]
    assert "tlow_ms" in out["rise"] and "tlow_ms" in out["mid_rise"]
    assert out["tx_late_us"]["max"] < 100


def test_loopback_ring_decodes_every_rung_through_the_data_phase():
    mirror = RingMirror(tau_us=500.0)
    out = M.loopback_ring(mirror.reader(), mirror.set_backlight)
    assert out["levels"] == [128, 256, 448] and out["lit"] is True
    for rung in M.RING_LOOP_RUNGS:
        result = out[str(rung)]
        assert result["errors"] == 0, (rung, result)
        assert result["symbols"] == 3 * len(M.DE_BRUIJN)
        assert result["locks"] >= 1, (rung, result)
        assert result["lag_us"]["max"] < 2000, (rung, result)


def test_the_ring_tests_stop_the_ring_and_leave_the_backlight_low():
    mirror = RingMirror()
    reader = mirror.reader()
    M.backlight_step_ring(reader, mirror.set_backlight)
    assert not mirror.ring.running
    assert mirror.target == 8.0
