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
    result = F.analyse(captured(sine(100, 20)))
    ours = set(result) | set(result["hz"]) | {"1", "2", "temp_c"}
    assert spec_keys == ours, (sorted(spec_keys - ours), sorted(ours - spec_keys))
