"""Dev Mode's measurement engine: the tests, the runs, the results file.

The calculations are checked on synthetic data with known answers. The tests
and runs are checked end to end on rings in virtual time (ring_optics): a
badge on a mirror, and two badges screen to screen, each test sending on the
backlight and reading through the production RingReader. What none of this
says anything about is the real sensor: the coupling, its lag and its noise
are whatever a test says.
"""

import json
import math
import os
import random
import re
import statistics

import ring_optics as RO
from harness import REPO, load_module

M = load_module("share_wifi", "measure")
P = load_module("share_wifi", "protocol")
R = load_module("share_wifi", "reader")

SPEC = os.path.join(REPO, ".agents", "share_wifi", "app-spec.md")
DUTY = M.backlight_duty()


def started(sensor):
    reader = sensor.reader()
    reader.start()
    return reader


def run_one(fn, gain=400.0, room=40.0, noise=0.0, tau_us=500.0, seed=1):
    """`fn` -- one of the tests -- on a mirror, from 100 ms after the ring
    starts. (its result, the sensor, the reader, its origin)."""
    optics, sensor = RO.mirror(room=room, gain=gain, noise=noise,
                               tau_us=tau_us, seed=seed)
    reader = started(sensor)
    t0 = reader.now_us() + 100000
    out = fn(reader, sensor.show, t0, M.Buffers())
    return out, sensor, reader, t0


# --- de Bruijn ----------------------------------------------------------------

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


def test_count_symbol_errors():
    assert M.count_symbol_errors(list(M.DE_BRUIJN), M.DE_BRUIJN) == 0
    assert M.count_symbol_errors([None] * 29, M.DE_BRUIJN) == 29
    assert M.count_symbol_errors(list(M.DE_BRUIJN[:20]), M.DE_BRUIJN) == 9


# --- statistics -----------------------------------------------------------------

def test_median_is_the_upper_median_and_leaves_the_input_alone():
    values = [5, 1, 4, 2]
    assert M.median(values) == 4
    assert values == [5, 1, 4, 2]
    assert M.median([3, 1, 2]) == 2
    assert M.median([9, 1, 2, 100], count=3) == 2
    assert M.median([]) is None


def test_sd():
    values = [32, 48, 32, 64, 48, 32]
    assert abs(M.sd(values) - statistics.pstdev(values)) < 1e-9
    assert M.sd([7, 7, 7]) == 0
    assert M.sd([1, 3, 1000], count=2) == 1.0
    assert M.sd([]) is None


def test_noise_falls_as_the_square_root_of_the_depth_when_it_spans_steps():
    # Readings dithered across 16-count steps: averaging resolves finer.
    rng = random.Random(1)
    values = [int(round((400 + rng.gauss(0, 12)) / 16.0)) * 16
              for _ in range(M.NOISE_READS)]
    out = M.noise_of(values)
    assert abs(out["sd"] - 12) < 1.5, out
    assert out["p2p"] % 16 == 0 and out["p2p"] >= 48
    means = out["means"]
    assert sorted(means, key=int) == ["4", "16", "64", "256"]
    for depth in (4, 16, 64):
        expected = out["sd"] / math.sqrt(depth)
        assert abs(means[str(depth)] - expected) < expected * 0.4, \
            (depth, means)


def test_noise_narrower_than_a_step_stalls_at_it():
    # Every reading one code: no spread at any depth -- the mean is the
    # step, not the level.
    out = M.noise_of([400] * M.NOISE_READS)
    assert out == {"sd": 0.0, "p2p": 0,
                   "means": {"4": 0.0, "16": 0.0, "64": 0.0, "256": 0.0}}


def test_noise_leaves_out_depths_with_too_few_means():
    out = M.noise_of(list(range(64)))
    assert sorted(out["means"], key=int) == ["4"]
    assert M.noise_of([]) == {"error": "no readings"}


def test_flicker_verdict():
    steady = [96] * 200 + [112] * 40 + [128] * 16
    assert M.flicker_verdict(steady, 16) == "averages"
    pwm = [16] * 190 + [112] * 66
    assert M.flicker_verdict(pwm, 16) == "follows_pwm"
    # Exactly 90% within two steps of the median is enough.
    assert M.flicker_verdict([100] * 90 + [500] * 10, 16) == "averages"
    assert M.flicker_verdict([100] * 89 + [500] * 11, 16) == "follows_pwm"
    result = M.flicker_result(pwm)
    assert set(result) == {"min", "max", "sd", "verdict"}
    assert result["min"] == 16 and result["max"] == 112


# --- reading the ring ------------------------------------------------------------

def test_copy_readings_gives_each_reading_in_counts():
    optics, sensor = RO.mirror(room=96.0, gain=0.0)
    reader = started(sensor)
    reader.pump(50000)
    out = [0] * 100
    assert M.copy_readings(reader, 40000, 100, out, [0] * 32) == 100
    assert out == [96] * 100
    # Older than the ring holds, or not yet read: the copy stops there.
    reader.pump(1000000)
    assert M.copy_readings(reader, 40000, 100, out, [0] * 32) == 0
    assert M.copy_readings(reader, reader.now_us() + 5000, 100, out,
                           [0] * 32) < 100


# --- the tests, on a mirror -------------------------------------------------------

def test_ambient_is_the_room_with_the_backlight_off():
    out, sensor, _, _ = run_one(M.ambient_test, room=80.0)
    assert out == {"ambient": 80}
    assert sensor.shown[-1][1] == M.BACKLIGHT_OFF


def test_levels_are_the_backlight_schemes_three_and_their_noise():
    out, sensor, _, _ = run_one(M.levels_test, room=40.0, gain=400.0,
                                noise=6.0)
    levels = out["levels"]
    for name, symbol in (("low", P.LOW), ("mid", P.MID), ("high", P.HIGH)):
        expected = 40 + 400 * DUTY[symbol]
        assert abs(levels[name] - expected) <= 4, (name, levels)
    assert levels["span"] == levels["high"] - levels["low"]
    assert abs(levels["mid_share"] - (DUTY[P.MID] - DUTY[P.LOW])
               / (1.0 - DUTY[P.LOW])) < 0.02, levels
    for name in ("low", "mid", "high"):
        noise = out["noise"][name]
        assert 4 <= noise["sd"] <= 9, (name, noise)
        assert noise["means"]["256"] < noise["sd"] / 4, (name, noise)
    # Low, middle, high, and back to low.
    assert [v for _, v in sensor.shown] == [DUTY[P.LOW], DUTY[P.MID],
                                            DUTY[P.HIGH], DUTY[P.LOW]]


def test_levels_with_high_not_above_low_say_so():
    out, _, _, _ = run_one(M.levels_test, gain=0.0)
    assert out["levels"]["error"] == "high not above low", out


def test_the_backlight_curve_is_eleven_tenths_and_proportional_here():
    out, sensor, _, _ = run_one(M.curve_test, room=40.0, gain=400.0)
    curve = out["backlight_curve"]
    assert sorted(curve, key=float) == ["%.1f" % (k / 10) for k in range(11)]
    for key, value in curve.items():
        assert abs(value - (40 + 400 * float(key))) <= 9, (key, value)


class PwmSensor(RO.Sensor):
    """A sensor that follows the backlight's PWM rather than averaging it:
    each reading catches the LED on or off, in proportion to the duty."""

    def target(self):
        return self.room

    def fill(self, t):
        ring = self.ring
        while ring.written * RO.PERIOD_US < t:
            on = ring.written * RO.PERIOD_US % 262 < 262 * self.backlight
            ring.push(int(round((self.room + (400 if on else 0)) / 16.0)))


def test_flicker_says_whether_the_sensor_averages_the_pwm():
    out, _, _, _ = run_one(M.flicker_test, noise=4.0)
    flicker = out["flicker"]
    assert sorted(flicker) == sorted(k for k, _ in M.FLICKER_BACKLIGHTS)
    for key in flicker:
        assert flicker[key]["verdict"] == "averages", (key, flicker[key])
    optics = RO.Optics()
    sensor = PwmSensor(optics, 40.0, 500.0, 0.0, 1)
    optics.sensors.append(sensor)
    reader = started(sensor)
    flicker = M.flicker_test(reader, sensor.show, reader.now_us() + 1000,
                             M.Buffers())["flicker"]
    for key in ("0.25", "0.5", "0.74"):
        assert flicker[key]["verdict"] == "follows_pwm", (key, flicker[key])
    assert flicker["1.0"]["verdict"] == "averages"


def test_backlight_step_ring_times_each_transition_from_the_call():
    out, _, _, _ = run_one(M.backlight_step_ring)
    step = out["backlight_step_ring"]
    for got, want in zip(step["levels"], (240, 368, 440)):
        assert abs(got - want) <= 8, step["levels"]
    for kind, _, _ in M.RING_STEP_KINDS:
        result = step[kind]
        assert result["count"] == M.RING_STEP_TRANSITIONS, (kind, result)
        # A first-order step: half way at tau ln 2, 0.35 ms, to a 100 us bin.
        assert 0.2 <= result["t50_ms"] <= 0.5, (kind, result)
        assert result["t10_ms"] <= result["t50_ms"] <= result["t90_ms"]
    assert "tlow_ms" in step["rise"] and "tlow_ms" in step["mid_rise"]
    assert step["tx_late_us"]["max"] < 100


def test_loopback_ring_decodes_every_rung_through_the_data_phase():
    out, sensor, _, _ = run_one(M.loopback_ring)
    loop = out["loopback_ring"]
    for got, want in zip(loop["levels"], (240, 368, 440)):
        assert abs(got - want) <= 8, loop["levels"]
    assert loop["lit"] is True, loop
    for rung in M.RING_LOOP_RUNGS:
        result = loop[str(rung)]
        assert result["errors"] == 0, (rung, result)
        assert result["symbols"] == 3 * len(M.DE_BRUIJN)
        assert result["locks"] >= 1, (rung, result)
        assert 0 < result["lag_us"]["max"] < 2000, (rung, result)
    assert sensor.shown[-1][1] == DUTY[P.LOW]


def dither(levels):
    """A mirror whose readings dither across whole raw steps, so a level's
    mean can sit between them -- as the ring's noise makes it."""
    optics, sensor = RO.mirror(gain=0.0)
    lows = dict(levels)

    class Dithered(RO.Sensor):
        def fill(self, t):
            ring = self.ring
            while ring.written * RO.PERIOD_US < t:
                raw = lows.get(round(self.backlight, 2), 0.0)
                ring.push(int(raw + (ring.written % 4) * 0.25))
    sensor.__class__ = Dithered
    return optics, sensor


def test_loopback_ring_gates_on_a_transfers_gap():
    # Mirror B's gaps, 27-30 counts (2026-10-09): over MIN_GAP_EXACT and the
    # quantisation floor -- what a transfer accepts, so M5 runs there.
    optics, sensor = dither({0.5: 2.0, 0.82: 3.75, 1.0: 5.5})
    reader = started(sensor)
    out = M.loopback_ring(reader, sensor.show, reader.now_us() + 1000)
    loop = out["loopback_ring"]
    assert "error" not in loop, loop
    low, mid, high = loop["levels"]
    assert M.MIN_GAP_EXACT <= mid - low, loop["levels"]
    for rung in M.RING_LOOP_RUNGS:
        assert loop[str(rung)]["errors"] == 0, (rung, loop[str(rung)])
    # One ADC step apart: under the floor's 18.5, refused -- and still sent.
    optics, sensor = dither({0.5: 2.0, 0.82: 3.0, 1.0: 4.0})
    reader = started(sensor)
    out = M.loopback_ring(reader, sensor.show, reader.now_us() + 1000)
    assert out["loopback_ring"]["error"] == "levels too close", out
    assert len(sensor.shown) > 3 + len(M.RING_LOOP_RUNGS) * len(M.RING_BLOCK) \
        // 2, "a sender that cannot read its blocks still sends them"


def test_every_test_keeps_to_its_time():
    # Two badges run a test in lockstep from a shared origin: each must be
    # done by its ms, analysis included, or the next starts late.
    for index, (name, ms, fn) in enumerate(M.TESTS):
        optics, sensor = RO.mirror(noise=4.0)
        reader = started(sensor)
        t0 = reader.now_us() + 1000
        M.run_test(index, reader, sensor.show, t0, M.Buffers())
        # Its last window read once its readings are all in: the gap after
        # it covers that.
        assert reader.now_us() <= t0 + ms * 1000 + M.READ_MARGIN_US, \
            (name, reader.now_us() - t0)
    assert M.TEST_OFFSETS[0] == 0
    for k in range(1, len(M.TESTS)):
        assert M.TEST_OFFSETS[k] == M.TEST_OFFSETS[k - 1] \
            + M.TESTS[k - 1][1] + M.TEST_GAP_MS
    assert M.SCHEDULE_MS == M.TEST_OFFSETS[-1] + M.TESTS[-1][1]


def test_the_schedule_takes_about_eighteen_seconds():
    # The spec's table: ambient 0.3, levels 1.1, curve 1.1, flicker 0.5,
    # M4 9.07, M5 5.735 s, and 0.1 s between each.
    assert [ms for _, ms, _ in M.TESTS] == [300, 1100, 1100, 500, 9070, 5735]
    assert M.SCHEDULE_MS == 18305


def test_every_result_key_comes_from_a_test():
    keys = []
    for name, _ms, _fn in M.TESTS:
        keys.extend(M.TEST_KEYS.get(name, (name,)))
    assert tuple(keys) == M.RESULT_KEYS


def test_a_test_that_raises_or_a_failed_ring_is_an_error_under_its_keys():
    optics, sensor = RO.mirror()
    reader = started(sensor)
    saved = M.TESTS
    try:
        def broken(reader, show, t0, buffers):
            raise ValueError("bad")
        M.TESTS = (("levels", 10, broken),)
        out = M.run_test(0, reader, sensor.show, 0, M.Buffers())
        assert out == {"levels": {"error": "ValueError: bad"},
                       "noise": {"error": "ValueError: bad"}}
    finally:
        M.TESTS = saved
    sensor.ring.fault = R.HEALTH_DMA
    assert M.run_test(0, reader, sensor.show, 0, M.Buffers()) == \
        {"ambient": {"error": "ring failed"}}


# --- the mirror run ------------------------------------------------------------

class FakeTemp:
    """The chip warming a tenth of a degree a read, from 25.0."""

    def __init__(self):
        self.reads = 0

    def __call__(self):
        self.reads += 1
        return round(25.0 + 0.1 * (self.reads - 1), 1)


def mirror_run(noise=4.0, temp=None, **kw):
    optics, sensor = RO.mirror(noise=noise, **kw)
    run = M.MirrorRun(sensor.reader(), None, sensor.show,
                      temp if temp is not None else FakeTemp())
    run.begin()
    frames = 0
    while not run.done:
        run.frame()
        optics.sleep(20000)
        frames += 1
        assert frames < 100
    return run, sensor


def test_a_mirror_run_is_one_test_a_frame_and_every_result():
    run, sensor = mirror_run()
    result = run.results()
    assert sorted(result) == sorted(list(M.RESULT_KEYS) + ["temperature_c"])
    for key in M.RESULT_KEYS:
        assert not isinstance(result[key], dict) or "error" not in result[key], \
            (key, result[key])
    json.dumps(result)
    # Read before the ring started and after it stopped: never while it ran.
    assert result["temperature_c"] == {"start": 25.0, "end": 25.1}
    assert not sensor.ring.running
    assert sensor.shown[-1][1] == M.BACKLIGHT_OFF


def test_a_mirror_run_names_the_test_in_progress():
    optics, sensor = RO.mirror()
    run = M.MirrorRun(sensor.reader(), None, sensor.show)
    run.begin()
    names = []
    while not run.done:
        names.append(run.test_name)
        run.frame()
    assert names == [name for name, _, _ in M.TESTS]
    assert run.test_name is None


def test_a_mirror_run_without_a_ring_says_why_and_ends_at_once():
    run = M.MirrorRun(None, "no rp2.DMA", lambda level: None, FakeTemp())
    run.begin()
    assert run.done
    result = run.results()
    for key in M.RESULT_KEYS:
        assert result[key] == {"error": "no rp2.DMA"}
    assert result["temperature_c"] == {"start": 25.0, "end": 25.1}


def test_no_temperature_sensor_is_recorded_not_raised():
    optics, sensor = RO.mirror()
    run = M.MirrorRun(sensor.reader(), None, sensor.show)
    run.begin()
    run.stop()
    assert run.results()["temperature_c"] == {"error": "no temperature sensor"}


# --- the results' shape, against the spec --------------------------------------

def keys_of(value):
    out = set()
    if isinstance(value, dict):
        for k, v in value.items():
            out.add(k)
            out |= keys_of(v)
    return out


def test_the_spec_example_names_only_keys_the_tools_write():
    text = open(SPEC).read()
    block = text.split("### Measurements file", 1)[1].split("### Display", 1)[0]
    # The probe's own example follows; test_probe.py checks that part.
    block = block.split("`<probe>` has one key", 1)[0]
    spec_keys = set(re.findall(r'"([A-Za-z0-9_.]+)":', block))
    mirror, _ = mirror_run()
    ours = keys_of(mirror.results()) | keys_of(pair_results()[0]) | {
        "mirror", "second_badge", "frame_probe", "role", "dark", "lit",
        "error", "self"}
    assert spec_keys - ours == set(), spec_keys - ours
    # And the example names every key the runs write.
    missing = sorted(k for k in ours - spec_keys if not k[0].isdigit())
    assert not missing, missing


# --- MicroPython hygiene ----------------------------------------------------

def test_measure_references_no_badge_globals():
    import test_module_boundary as boundary
    path = M.__file__
    _, read = boundary.loaded_names(path)
    leaked = sorted(read & (boundary.BADGE_GLOBALS | {"display", "screen"}))
    assert not leaked, "measure.py reaches for the badge: %s" % leaked


def test_measure_avoids_what_micropython_lacks():
    import ast
    path = M.__file__
    source = open(path).read()
    assert "%r" not in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            assert not any(k.arg is None for k in node.keywords), \
                "** unpacking on line %d" % node.lineno
        assert not isinstance(node, (ast.JoinedStr, ast.NamedExpr,
                                     ast.Global)), \
            "line %d" % getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            assert all(a.name in ("math", "time") for a in node.names)


def test_measure_never_reads_light_level_or_draws():
    # Every reading is the ring's, every signal the backlight's: no name in
    # it that reads the sensor directly, draws, presents or switches mode.
    import ast
    path = M.__file__
    names = set()
    for node in ast.walk(ast.parse(open(path).read())):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    for word in ("light_level", "fill", "clear", "rectangle", "text", "pen",
                 "LORES", "HIRES", "screen", "color", "display"):
        assert word not in names, word


# --- the handshake, on a scripted peer -----------------------------------------

def test_handshake_constants_are_the_specs():
    assert (M.PULSE_HIGH_MS, M.PULSE_PERIOD_MS, M.REPLY_DELAY_MS) == \
        (60, 360, 180)
    assert (M.REPLY_EARLIEST_MS, M.REPLY_LATEST_MS) == (120, 270)
    assert M.REPLIES_NEEDED == 3
    assert (M.GO_MS, M.GO_GAP_MS, M.TURN_END_MS) == (600, 1000, 1000)
    assert M.HANDSHAKE_TIMEOUT_MS == 60000 and M.NOISE_MS == 1000
    assert (M.PULSE_MIN_MS, M.PULSE_MAX_MS) == (20, 150)
    assert M.PULSE_MIN_SWING == 48 and M.SAMPLE_US == 4000
    # The window ends before the caller's next pulse and starts after its echo.
    assert M.ECHO_MS < M.REPLY_EARLIEST_MS < M.REPLY_DELAY_MS
    assert M.REPLY_LATEST_MS + M.PULSE_HIGH_MS // 2 < M.PULSE_PERIOD_MS
    assert M.GO_MIN_MS > 3 * M.PULSE_HIGH_MS
    # A level held longer than a go is a step of the schedule, never a go:
    # the go is the longest high the detector takes.
    assert M.GO_MAX_MS < M.LEVEL_MS * 3


def samples(peer, total_ms, gain=80, room=32, tau_ms=0.5, start_ms=0):
    """(t_ms, value): SAMPLE_US means of a sensor reading a peer whose
    backlight is `peer(t_ms)`, through a first-order lag."""
    out = []
    level = 0.0
    step = 0.1
    k = 1 - math.exp(-step / tau_ms)
    per = int(M.SAMPLE_US / 1000 / step)
    t = start_ms
    while t < total_ms:
        total = 0.0
        for i in range(per):
            level += (peer(t + i * step) - level) * k
            total += level
        out.append((int(t + M.SAMPLE_US / 2000), int(room + gain * total
                                                      / per)))
        t += M.SAMPLE_US / 1000
    return out


def call_train(start_ms=1000, count=3, go=False):
    """A caller's backlight: `count` call pulses from start_ms, then a go."""
    go_at = start_ms + count * M.PULSE_PERIOD_MS

    def peer(t):
        e = t - start_ms
        if 0 <= e < count * M.PULSE_PERIOD_MS:
            return 1.0 if e % M.PULSE_PERIOD_MS < M.PULSE_HIGH_MS else 0.0
        if go and go_at <= t < go_at + M.GO_MS:
            return 1.0
        return 0.0
    return peer, go_at


def pulse_events(reads, threshold=48):
    detector = M.PulseDetector()
    detector.threshold = threshold
    events = []
    for t, v in reads:
        event = detector.push(t, v)
        if event != M.PULSE_NONE:
            events.append((event, detector.rise, detector.fall))
    return events


def test_call_pulses_are_found_on_an_80_count_swing():
    peer, _ = call_train(count=4)
    events = pulse_events(samples(peer, 3000))
    assert [e[0] for e in events] == [M.PULSE_SEEN] * 4
    for k, (_, rise, fall) in enumerate(events):
        start = 1000 + k * M.PULSE_PERIOD_MS
        # Interpolated between the samples either side: within one.
        assert abs(rise - start) <= 4, (k, rise)
        assert 50 <= fall - rise <= 70


def test_a_go_is_found_at_its_fall():
    peer, go_at = call_train(count=3, go=True)
    events = pulse_events(samples(peer, 4000))
    assert [e[0] for e in events] == [M.PULSE_SEEN] * 3 + [M.PULSE_GO]
    _, rise, fall = events[-1]
    assert abs(rise - go_at) <= 4
    assert abs(fall - (go_at + M.GO_MS)) <= 4


def test_a_held_level_or_a_small_swing_is_neither():
    for width in (300, 1000, 3000):            # the schedule's holds
        events = pulse_events(samples(
            lambda t: 1.0 if 1000 <= t < 1000 + width else 0.0, 6000))
        assert events == [], width
    peer, _ = call_train(count=4, go=True)
    assert pulse_events(samples(peer, 4000, gain=32)) == []
    assert pulse_events(samples(peer, 4000), threshold=96) == []


class Scripted:
    """One run against a scripted peer on a ring, a frame every `frame_ms`.

    `peer(script, t_us)` is the peer's backlight at optics time t; it may
    read what the run has shown (`sensor.shown`) to answer it. `echo` is
    the run's own backlight in its own sensor, in counts at full."""

    def __init__(self, first, peer, frame_ms=17, gain=80.0, room=32.0,
                 echo=0.0):
        self.optics = RO.Optics()
        self.peer = peer
        self.sensor = self.optics.sensor(
            room=lambda t: room + gain * peer(self, t))
        if echo:
            self.sensor.couple(self.sensor, echo)
        self.run = M.SecondBadgeRun(first, self.sensor.reader(), None,
                                    self.sensor.show)
        self.frame_ms = frame_ms
        self.zero = None
        self.phases = []
        self.go = None              # optics us when a calling run's go began

    def now_ms(self):
        """The run's own clock: ms since its ring started."""
        return (self.optics.t - self.zero) // 1000

    def until(self, done, limit_ms=200000):
        run = self.run
        if self.zero is None:
            self.zero = self.optics.t
            run.begin()
        while not done(self) and self.now_ms() < limit_ms:
            run.frame()
            if self.go is None and run.phase == M.PHASE_CALLING and \
                    run.go_start is not None:
                self.go = self.zero + run.go_start * 1000
            if not self.phases or self.phases[-1] != run.phase:
                self.phases.append(run.phase)
            self.optics.sleep(self.frame_ms * 1000)
        return self

    def rises(self, before=None):
        """When the run's backlight went to full, optics us."""
        out = []
        prev = 0.0
        for t, value in self.sensor.shown:
            if value == 1.0 and prev != 1.0 and (before is None or t < before):
                out.append(t)
            prev = value
        return out


def caller(delay=190, skip=(), echo=0.0, answers=True, frame_ms=17):
    """A first badge calling a scripted peer, which answers `delay` ms after
    each call pulse it saw go up, but those in `skip`; or never."""
    def peer(script, t):
        if not answers:
            return 0.0
        for k, start in enumerate(script.rises(before=script.go)):
            if k not in skip and start + delay * 1000 <= t < \
                    start + (delay + M.PULSE_HIGH_MS) * 1000:
                return 1.0
        return 0.0
    return Scripted(True, peer, frame_ms=frame_ms, echo=echo)


def go_cycle(script):
    run = script.run
    script.until(lambda s: s.go is not None or s.run.no_handshake,
                 limit_ms=M.NOISE_MS + M.HANDSHAKE_TIMEOUT_MS + 100)
    if script.go is None:
        return None
    return (run.go_start - run.handshake_at) // M.PULSE_PERIOD_MS


def test_the_go_follows_three_answers_in_a_row():
    assert go_cycle(caller()) == 3
    for frame_ms in (8, 25):
        assert go_cycle(caller(frame_ms=frame_ms)) == 3, frame_ms


def test_a_missed_answer_starts_the_count_again():
    assert go_cycle(caller(skip=(1,))) == 5
    assert go_cycle(caller(skip=(0, 2, 4))) == 8


def test_only_an_answer_inside_the_window_counts():
    for delay in (130, 180, 250):
        assert go_cycle(caller(delay=delay)) == 3, delay
    for delay in (60, 100, 290):
        script = caller(delay=delay)
        assert go_cycle(script) is None, delay
        assert script.run.no_handshake


def test_the_callers_own_echo_is_not_an_answer():
    # Its own pulses in its own sensor, far over the threshold, and no one
    # answering: seen as pulses every cycle, never counted.
    script = caller(echo=400.0, answers=False)
    seen = []
    run = script.run
    original = run._answered
    run._answered = lambda rise: (seen.append(rise), original(rise))
    assert go_cycle(script) is None and run.no_handshake
    assert len(seen) > 100 and run.replies == 0
    assert go_cycle(caller(echo=400.0)) == 3


def test_a_badge_that_gives_up_stops_its_ring():
    script = caller(answers=False)
    go_cycle(script)
    assert script.run.no_handshake and not script.sensor.ring.running
    script.until(lambda s: s.run.done)
    result = script.run.results()
    for key in M.RESULT_KEYS:
        assert result[key] == {"error": "no handshake"}, key
    assert result["self"] == {"error": "no handshake"}


def calling_peer(start_ms, go_after=None):
    """A peer calling from start_ms after the run began, with a go in place
    of pulse `go_after`."""
    def peer(script, t):
        if script.zero is None:
            return 0.0
        e = (t - script.zero) / 1000.0 - start_ms
        if e < 0:
            return 0.0
        k = e // M.PULSE_PERIOD_MS
        if go_after is not None and k >= go_after:
            g = e - go_after * M.PULSE_PERIOD_MS
            return 1.0 if g < M.GO_MS else 0.0
        return 1.0 if e % M.PULSE_PERIOD_MS < M.PULSE_HIGH_MS else 0.0
    return peer


def test_the_answer_goes_up_half_a_period_after_the_call():
    for frame_ms in (8, 17):
        start = 1500
        script = Scripted(False, calling_peer(start), frame_ms=frame_ms)
        script.until(lambda s: s.now_ms() > start + 8 * M.PULSE_PERIOD_MS)
        starts = script.rises()
        assert len(starts) >= 7, starts
        for k, at in enumerate(starts):
            call = script.zero + (start + k * M.PULSE_PERIOD_MS) * 1000
            # The rise as the ring timed it -- to a 4 ms sample -- and the
            # frame the answer lands in.
            assert -4 <= (at - call) / 1000 - M.REPLY_DELAY_MS <= frame_ms + 2, \
                (frame_ms, k, at - call)


def test_the_answerer_ignores_its_own_echo():
    start = 1500
    script = Scripted(False, calling_peer(start), echo=400.0)
    script.until(lambda s: s.now_ms() > start + 8 * M.PULSE_PERIOD_MS)
    starts = script.rises()
    assert len(starts) == 8, starts
    for k, at in enumerate(starts):
        call = script.zero + (start + k * M.PULSE_PERIOD_MS) * 1000
        assert abs((at - call) / 1000 - M.REPLY_DELAY_MS) <= 20


def test_the_answerer_measures_from_the_go_and_stops_answering():
    start = 1500
    script = Scripted(False, calling_peer(start, go_after=4))
    go_at = start + 4 * M.PULSE_PERIOD_MS
    script.until(lambda s: s.run.phase not in (M.PHASE_ANSWERING,
                                               M.PHASE_NOISE))
    run = script.run
    assert run.phase == M.PHASE_MEASURING
    fall_us = (go_at + M.GO_MS) * 1000
    # Found again in the ring: to a bin and the sensor's fall.
    assert 0 <= run.epoch - M.GO_GAP_MS * 1000 - fall_us <= 600, \
        run.epoch - M.GO_GAP_MS * 1000 - fall_us
    assert len(script.rises()) == 4


def test_the_threshold_is_half_again_the_noise_and_never_below_48():
    assert M.pulse_threshold(None) == 48
    assert M.pulse_threshold(0) == 48
    assert M.pulse_threshold(32) == 48
    assert M.pulse_threshold(64) == 96


def test_the_noise_is_twice_the_spread_to_the_median():
    assert M.noise_spread([32] * 7, 7) is None
    assert M.noise_spread([32] * 8, 8) == 0
    assert M.noise_spread([32, 48, 64, 80] * 20, 80) == 32
    for lit in (15, 25, 40):
        reads = [32] * (100 - lit) + [4032] * lit
        random.Random(lit).shuffle(reads)
        assert M.noise_spread(reads, len(reads)) == 0, lit
    assert M.noise_spread([32] * 10 + [4032] * 10, 10) == 0


def test_the_threshold_comes_from_the_runs_first_second():
    for first, phase in ((False, M.PHASE_ANSWERING), (True, M.PHASE_CALLING)):
        # Three levels evenly spread, 64 counts peak to peak, every 4 ms.
        script = Scripted(first, lambda s, t: 0.0)
        script.sensor.room_fn = lambda t: 32 + 32 * ((t // 4000) % 3)
        script.until(lambda s: s.run.phase != M.PHASE_NOISE)
        run = script.run
        assert run.phase == phase
        assert 60 <= run.pulse_noise <= 64, run.pulse_noise
        assert run.pulse_threshold == run.detector.threshold >= 90


# --- two badges ---------------------------------------------------------------

_PAIR = []


def pair_results():
    """Both badges' results from one run of pair_run(): ~5 s of CPython."""
    if not _PAIR:
        _PAIR.append(pair_run())
    runs, _ = _PAIR[0]
    return runs[0].results(), runs[1].results()


def pair_run(start_gap_ms=2500, gain=200.0, own=6.0, deaf=None,
             gone=None, noise=4.0):
    """Two badges screen to screen, the second begun `start_gap_ms` after
    the first. `deaf` is a role whose sensor sees nothing of the other;
    `gone` a role that stops after it has measured. (runs, sensors)."""
    optics, a, b = RO.pair(gain=gain, own=own, noise=noise)
    if deaf == "second":
        b.coupling = [(other, c) for other, c in b.coupling if other is b]
    runs = (M.SecondBadgeRun(True, a.reader(), None, a.show, FakeTemp()),
            M.SecondBadgeRun(False, b.reader(), None, b.show, FakeTemp()))

    def loop(run, delay_ms):
        def body():
            optics.sleep(delay_ms * 1000)
            run.begin()
            for _ in range(20000):
                if run.done or (gone == run.role and run.did_measure):
                    return
                run.frame()
                optics.sleep(17000)
            raise AssertionError("never finished")
        return body
    optics.run(loop(runs[0], 0), loop(runs[1], start_gap_ms))
    return runs, (a, b)


def assert_clean(result, role):
    for key in M.RESULT_KEYS:
        assert not isinstance(result[key], dict) or "error" not in result[key], \
            (role, key, result[key])
    assert result["loopback_ring"]["6"]["errors"] == 0, (role, result)
    assert "levels" in result["self"], (role, result["self"])


def test_two_badges_measure_each_other_and_their_own_light():
    first, second = pair_results()
    for result, role in ((first, "first"), (second, "second")):
        assert_clean(result, role)
        # The other's backlight across the gap: 40 counts of room, 200 full.
        levels = result["levels"]
        for name, symbol in (("low", P.LOW), ("mid", P.MID),
                             ("high", P.HIGH)):
            assert abs(levels[name] - (40 + 200 * DUTY[symbol])) <= 6, \
                (role, levels)
        # Its own: 6 counts at full, off the other's glass.
        self_levels = result["self"]["levels"]
        assert self_levels["high"] - result["ambient"] <= 12, self_levels
        # Timed from the go's fall as it saw it: a sensor's delay either side.
        rise = result["backlight_step_ring"]["rise"]
        assert -1.0 <= rise["t50_ms"] <= 1.0, rise
        json.dumps(result)


def test_badges_started_far_apart_still_find_each_other():
    for gap_ms in (500, 20000):
        runs, sensors = pair_run(start_gap_ms=gap_ms, noise=0.0)
        for run in runs:
            assert not run.no_handshake, (gap_ms, run.role)
            assert_clean(run.results(), run.role)


def test_an_echo_of_a_badges_own_light_does_not_stop_the_run():
    runs, _ = pair_run(own=300.0, noise=0.0)
    for run in runs:
        assert not run.no_handshake, run.role
        result = run.results()
        assert result["levels"]["high"] > 200, result["levels"]


def phases_of(run):
    """Every phase a run went through, by wrapping its frame()."""
    seen = []
    original = run.frame

    def framing():
        original()
        if not seen or seen[-1] != run.phase:
            seen.append(run.phase)
    run.frame = framing
    return seen


def test_each_badge_goes_through_both_turns_and_they_finish_together():
    optics, a, b = RO.pair(gain=200.0, noise=0.0)
    runs = (M.SecondBadgeRun(True, a.reader(), None, a.show),
            M.SecondBadgeRun(False, b.reader(), None, b.show))
    seen = [phases_of(run) for run in runs]
    finished = {}

    def loop(run, delay_ms):
        def body():
            optics.sleep(delay_ms * 1000)
            run.begin()
            while not run.done:
                run.frame()
                optics.sleep(17000)
            finished[run.role] = optics.t
        return body
    optics.run(loop(runs[0], 0), loop(runs[1], 700))
    assert seen[0] == [M.PHASE_NOISE, M.PHASE_CALLING, M.PHASE_SENDING,
                       M.PHASE_ANSWERING, M.PHASE_MEASURING, M.PHASE_DONE], \
        seen[0]
    assert seen[1] == [M.PHASE_NOISE, M.PHASE_ANSWERING, M.PHASE_MEASURING,
                       M.PHASE_CALLING, M.PHASE_SENDING, M.PHASE_DONE], \
        seen[1]
    assert abs(finished["first"] - finished["second"]) <= 40000, finished


def test_a_peer_that_never_sees_the_calls_ends_both_badges():
    runs, sensors = pair_run(deaf="second", noise=0.0)
    for run in runs:
        assert run.no_handshake
        result = run.results()
        assert result["levels"] == {"error": "no handshake"}
        assert result["self"] == {"error": "no handshake"}
    for sensor in sensors:
        assert not sensor.ring.running


def test_a_handshake_that_fails_after_sending_keeps_what_was_measured():
    # The second badge measures the first turn and goes: it keeps what it
    # measured; the first sent, then hears no call, and keeps its own light.
    runs, _ = pair_run(gone="second", noise=0.0)
    first, second = runs
    assert first.no_handshake
    result = first.results()
    assert result["levels"] == {"error": "no handshake"}
    assert "levels" in result["self"], result["self"]
    result = second.results()
    assert_clean(dict(result, self={"levels": {}}), "second")
    assert result["self"] == {"error": "not run"}


def test_an_answerer_does_not_take_the_schedule_for_a_go():
    # A badge that missed the go is still answering while the other sends:
    # the schedule's holds and steps must never pass for one. Its peer's
    # backlight: three calls, then a whole turn, recorded off a mirror.
    optics, mirror = RO.mirror(noise=0.0)
    run = M.MirrorRun(mirror.reader(), None, mirror.show)
    run.begin()
    while not run.done:
        run.frame()
    shown = mirror.shown
    sent_from = shown[0][0]

    def peer(script, t):
        if script.zero is None:
            return 0.0
        e = (t - script.zero) / 1000.0 - 1500
        if e < 0:
            return 0.0
        if e < 3 * M.PULSE_PERIOD_MS:
            return 1.0 if e % M.PULSE_PERIOD_MS < M.PULSE_HIGH_MS else 0.0
        at = sent_from + (e - 3 * M.PULSE_PERIOD_MS) * 1000
        value = 0.0
        for when, level in shown:
            if when > at:
                break
            value = level
        return value
    script = Scripted(False, peer, gain=200.0)
    script.until(lambda s: s.now_ms() > 1500 + 3 * M.PULSE_PERIOD_MS
                 + M.SCHEDULE_MS + 500 or s.run.phase == M.PHASE_MEASURING)
    assert script.run.phase == M.PHASE_ANSWERING, script.run.phase
    assert script.run.calls >= 1
