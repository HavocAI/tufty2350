"""Dev Mode's room-flicker tool (flicker.py), against a synthetic light.

The sensor is a function of a fake microsecond clock that moves on with every
read, as the badge's does, so a capture's bins fill as they would there.
Nothing here says what a room's lights do -- only that a flicker of a known
frequency and size comes back as that frequency and size.
"""

import ast
import math
import os

from harness import load_module

F = load_module("share_wifi", "flicker")
PATH = os.path.join(os.path.dirname(F.__file__), "flicker.py")


def captured(light, read_us=27):
    """One capture of light(t_us), reading every `read_us`."""
    t = [0]

    def ticks():
        return t[0]

    def read():
        t[0] += read_us
        return light(t[0])
    capture = F.Capture()
    capture.take(read, ticks)
    return capture


def sine(hz, size, mean=400, quantum=1):
    def light(t_us):
        value = mean + size * math.sin(2 * math.pi * hz * t_us / 1e6)
        return int(value) // quantum * quantum
    return light


def test_a_capture_fills_every_bin_for_a_quarter_second():
    capture = captured(lambda t: 100)
    assert capture.reads > 9000
    assert all(capture.counts), "a bin with no reading"
    assert 250000 <= capture.elapsed_us < 250000 + 100


def test_mains_flicker_comes_back_at_its_frequency_and_size():
    for hz in (100, 120):
        result = F.analyse(captured(sine(hz, 20)))
        assert result["peak_hz"] == hz, (hz, result)
        assert abs(result["peak_counts"] - 20) < 1, (hz, result)
        assert result["hz"][str(hz)] > 15, result["hz"]
        assert abs(result["mean"] - 400) < 1


def test_flicker_smaller_than_an_adc_step_is_still_seen():
    # 6 counts of 100 Hz in 16-count steps, dithered by noise: the bins
    # average enough reads to bring it back.
    import random
    rng = random.Random(1)
    base = sine(100, 6)

    def light(t_us):
        return int(base(t_us) + rng.gauss(0, 6)) // 16 * 16
    result = F.analyse(captured(light))
    assert result["peak_hz"] == 100, result
    assert 3 < result["peak_counts"] < 9, result


def test_a_steady_light_has_no_peak_to_speak_of():
    result = F.analyse(captured(lambda t: 400))
    assert result["peak_counts"] < 0.01 and result["p2p"] == 0
    assert result["read_us"] == 27.0


def test_a_fast_flicker_is_found_up_to_a_kilohertz():
    result = F.analyse(captured(sine(700, 15)))
    assert result["peak_hz"] == 700, result


def test_an_empty_bin_takes_the_one_before():
    capture = F.Capture()
    capture.reads = 2
    capture.elapsed_us = 250000
    capture.sums[3] = 100
    capture.counts[3] = 1
    capture.sums[10] = 200
    capture.counts[10] = 1
    series = capture.series()
    assert series[0] == series[3] == 100      # leading empties take the first
    assert series[9] == 100 and series[10] == 200 and series[-1] == 200


def test_no_reading_is_an_error_not_a_crash():
    capture = F.Capture()
    assert F.analyse(capture) == {"error": "no readings"}
    assert F.summary({"error": "no readings"}) == "no readings"


def test_the_summary_fits_a_line():
    line = F.summary(F.analyse(captured(sine(100, 20))))
    assert line.startswith("100 Hz, ") and "on 400" in line, line


def test_every_harmonic_the_fit_uses_is_reported():
    result = F.analyse(captured(sine(120, 20)))
    assert sorted(result["hz"], key=int) == [
        "50", "60", "100", "120", "150", "180", "200", "240"]
    assert result["hz"]["120"] > 15 and result["hz"]["240"] < 1


# --- the transfer's fit, on the capture --------------------------------------

def room(hz, size, harmonic=0, mean=700, noise=5, seed=1):
    """A room's light: flicker at `hz` and twice it, sensor noise, and the
    sensor's 16-count steps."""
    import random
    rng = random.Random(seed)

    def light(t_us):
        x = 2 * math.pi * hz * t_us / 1e6
        value = mean + size * math.sin(x) + harmonic * math.sin(2 * x + 0.5)
        return int(value + rng.gauss(0, noise)) // 16 * 16
    return light


def test_the_fit_is_fed_2_ms_means_every_3_ms_as_a_transfer_feeds_it():
    ts, vs, spans = F.samples(captured(lambda t: 400))
    assert len(ts) == 83
    assert set(spans) == {2000}
    assert {ts[k + 1] - ts[k] for k in range(len(ts) - 1)} == {3000}
    assert ts[0] == 1000 and set(vs) == {400}


def test_a_flickering_room_is_fitted_and_corrected_on_samples_it_never_saw():
    for hz, mains in ((120, 60), (100, 50)):
        found = F.analyse(captured(room(hz, 20, harmonic=5)))["filter"]
        assert found["verdict"] == "flicker", found
        assert found["mains"] == mains and found["window_ms"] == F.FLICKER_FIT_MS == 160, found
        assert found["reason"] is None
        assert abs(found["harmonics"][str(hz)] - 20) < 2, found
        assert hz in found["kept"], found
        # The last ~90 ms of the capture, outside the fit's 160.
        assert 28 <= found["held_out"] <= 31, found
        assert found["sd_after"] < found["sd_before"] / 3, found


def test_a_steady_room_is_steady_and_nothing_is_taken_off():
    found = F.analyse(captured(room(120, 0, noise=3)))["filter"]
    assert found["verdict"] == "steady", found
    assert found["sd_after"] == found["sd_before"]
    assert "kept" not in found


def test_a_window_too_noisy_to_fit_is_refused_after_the_half_window_retry():
    found = F.analyse(captured(room(120, 0, noise=250)))["filter"]
    assert found["verdict"] is None and found["reason"] == "noise", found
    assert found["window_ms"] == 80, "the transfer's retry, on half the window"
    assert found["noise"] > 12
    assert found["sd_after"] == found["sd_before"]


def fit_reason(ts, vs, spans, patch=None):
    P = load_module("share_wifi", "protocol")
    flicker = P.FlickerFilter()
    if patch:
        flicker.fit_mains = patch
    verdict = flicker.fit(ts, vs, spans)
    return verdict, flicker.fit_info["reason"]


def even(n, every=3000, span=2000):
    ts = [1000 + every * k for k in range(n)]
    return ts, [400] * n, [span] * n


def test_each_refusal_has_its_own_reason():
    assert fit_reason(*even(11)) == (None, "few")
    # 12 samples, but four harmonics' sine and cosine leave 12 - 2 - 8.
    assert fit_reason(*even(12, every=1000, span=500)) == (None, "dof")
    assert fit_reason(*even(60), patch=lambda *a: None) == (None, "unfit")
    noisy = F.analyse(captured(room(120, 0, noise=250)))["filter"]
    assert noisy["reason"] == "noise"
    assert fit_reason(*even(60)) == ("steady", None)


def test_a_capture_with_no_readings_has_no_fit():
    assert F.fit_capture(F.Capture()) == {"verdict": None, "reason": "few"}


# --- mains drift --------------------------------------------------------------

def drifted(mains_offset_hz, windows=24, hz=120, size=20, gaps=None,
            noise=0):
    """Drift windows of a light flickering at `hz` + 2 x `mains_offset_hz`,
    read every 27 us of a clock that moves on once a read, with `gaps` us
    between windows -- a frame's present and poll -- cycled."""
    import random
    rng = random.Random(3)
    t = [5000000]
    gaps = gaps or (20000,)

    def ticks():
        return t[0]

    def read():
        t[0] += 27
        x = 2 * math.pi * (hz + 2 * mains_offset_hz) * t[0] / 1e6
        return int(700 + size * math.sin(x) + rng.gauss(0, noise)) // 16 * 16
    drift = F.Drift(hz, windows)
    capture = F.Capture()
    for w in range(windows):
        capture.take(read, ticks)
        drift.add(capture)
        t[0] += gaps[w % len(gaps)]
    return drift.result()


def test_drift_measures_the_mains_offset_in_mhz():
    for offset in (0.013, -0.02, 0.0):
        result = drifted(offset, noise=4)
        assert abs(result["offset_mhz_vs_clock"] - 1000 * offset) < 0.5, \
            (offset, result)
        assert result["hz"] == 120 and result["windows"] == 24
        assert result["wander_deg"] < 3, result


def test_a_large_offset_wraps_the_phase_many_times_and_is_unwrapped():
    # 0.4 Hz of mains is 0.8 Hz of flicker: 80-odd degrees a window, round
    # the circle every five.
    for offset in (0.4, -0.4):
        result = drifted(offset)
        assert abs(result["offset_mhz_vs_clock"] - 1000 * offset) < 1, result


def test_windows_at_irregular_gaps_still_line_up():
    # A frame can be one refresh or three, and a collection more.
    result = drifted(0.03, gaps=(17000, 50000, 3000, 120000, 33000), hz=100)
    assert abs(result["offset_mhz_vs_clock"] - 30) < 0.5, result
    assert result["hz"] == 100


def test_an_offset_past_the_unwrap_limit_is_marked_ambiguous():
    # Windows 270 ms apart: the limit is 1 / (4 x 0.27) = 0.93 Hz. 0.95
    # aliases to a clean-looking -0.9 -- marked, not trusted.
    result = drifted(0.95)
    assert result["ambiguous"] is True, result
    for offset in (0.0, 0.013, 0.4, -0.4):
        assert drifted(offset)["ambiguous"] is False, offset


def test_a_window_with_no_reading_counts_as_an_attempt():
    drift = F.Drift(120, 5)
    for _ in range(3):
        drift.add(F.Capture())
    assert drift.attempts == 3 and drift.n == 0


def test_fewer_than_three_windows_is_an_error():
    assert drifted(0.01, windows=2) == {"error": "too few windows"}


def test_drift_tracks_twice_the_fitted_mains_or_else_the_spectrum():
    fitted = {"hz": {"100": 0.5, "120": 9.0},
              "filter": {"verdict": "flicker", "mains": 60}}
    assert F.drift_hz(fitted) == 120
    # A 50 Hz fit wins over the spectrum's louder 120.
    fitted = {"hz": {"100": 3.0, "120": 9.0},
              "filter": {"verdict": "flicker", "mains": 50}}
    assert F.drift_hz(fitted) == 100
    steady = {"hz": {"100": 0.5, "120": 2.5}, "filter": {"verdict": "steady"}}
    assert F.drift_hz(steady) == 120
    quiet = {"hz": {"100": 0.5, "120": 1.5}, "filter": {"verdict": None}}
    assert F.drift_hz(quiet) is None
    # A tie goes to the first of DRIFT_HZ.
    tied = {"hz": {"100": 4.0, "120": 4.0}, "filter": {"verdict": None}}
    assert F.drift_hz(tied) == 100


# --- MicroPython hygiene ----------------------------------------------------

def test_flicker_references_no_badge_globals():
    import test_module_boundary as boundary
    _, read = boundary.loaded_names(PATH)
    leaked = sorted(read & (boundary.BADGE_GLOBALS | {"display", "gc"}))
    assert not leaked, "flicker.py reaches for the badge: %s" % leaked


def test_flicker_avoids_what_micropython_lacks():
    tree = ast.parse(open(PATH).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            assert not any(k.arg is None for k in node.keywords), \
                "** unpacking at line %d" % node.lineno
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "%r" not in node.value, "%%r at line %d" % node.lineno


def test_the_spec_example_names_only_keys_the_tool_writes_and_all_of_them():
    import re
    from harness import REPO
    spec = os.path.join(REPO, ".agents", "docs", "share_wifi_spec.md")
    text = open(spec).read()
    block = text.split("### measure room flicker", 1)[1].split("### Known gaps", 1)[0]
    block = block.split('"room_flicker": {', 1)[1]
    spec_keys = set(re.findall(r'"([A-Za-z0-9_.]+)":', block))
    result = F.analyse(captured(room(120, 20, harmonic=5)))
    result["drift"] = drifted(0.01, windows=3)
    ours = keys_of(result) | {"1", "2", "temp_c", "error"}
    assert spec_keys == ours, (sorted(spec_keys - ours), sorted(ours - spec_keys))


def keys_of(obj):
    out = set()
    if isinstance(obj, dict):
        for key in obj:
            out.add(key)
            out |= keys_of(obj[key])
    return out


def test_display_measurements_orders_every_room_flicker_key():
    measure = load_module("share_wifi", "measure")
    result = F.analyse(captured(room(120, 20, harmonic=5)))
    result["drift"] = drifted(0.01, windows=3)
    missing = sorted(k for k in keys_of(result) - set(measure.KEY_ORDER)
                     if not k.isdigit())
    assert not missing, missing
