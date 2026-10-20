"""The dev link's measurement subtools (tests/share_wifi/devtests/), each
run as the link runs it -- run(api, args) -- on DMA rings in virtual time
(ring_optics), the badge's globals stubbed as the harness stubs them.

What this covers is each tool's shell: the ring held and released, the
white screen, the tests paced and run to the end, what is saved and under
which name. The numbers are lib/measure.py's, lib/probe.py's and
lib/flicker.py's, tested in test_measure.py, test_probe.py and
test_flicker.py; what the hardware gives, none of this says.
"""

import builtins
import gc
import math
import os
import sys
import time
import types

import ring_optics as RO
from harness import load_module, make_globals

protocol = load_module("share_wifi", "protocol")
measure = load_module("share_wifi", "measure")
flicker = load_module("share_wifi", "flicker")
probe = load_module("share_wifi", "probe")

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "devtests")


class OpticsTime:
    """time.ticks_ms and ticks_us on the optics' virtual time."""

    def __init__(self, optics):
        self.optics = optics

    def __enter__(self):
        self.saved = (time.ticks_ms, time.ticks_us)
        time.ticks_ms = self.optics.ticks_ms
        time.ticks_us = self.optics.ticks_us
        return self.optics

    def __exit__(self, *exc):
        time.ticks_ms, time.ticks_us = self.saved
        return False


class Injected:
    """The badge's globals in `builtins`, as the firmware puts them, for the
    length of a `with`: the tools and their libraries read them there."""

    def __init__(self):
        self.globals = make_globals()
        self.names = ("badge", "screen", "display", "color", "BUTTON_B")

    def __enter__(self):
        self.saved = {n: getattr(builtins, n, None) for n in self.names}
        for name in self.names:
            setattr(builtins, name, self.globals[name])
        return self.globals

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            if value is None:
                delattr(builtins, name)
            else:
                setattr(builtins, name, value)
        return False


class Api:
    """A subtool's api as devserver.Api gives it, on `sensor`'s ring. The
    interlock is asserted, not enforced: no save, send or temperature read
    while the ring runs."""

    def __init__(self, sensor, display, has_ring=True):
        self.sensor = sensor
        self.display = display
        self.reader = sensor.reader() if has_ring else None
        self.ring = None
        self.saved = []
        self.sent = []
        self.cancel_after = None
        self.checks = 0
        self.restores = 0
        self.hooks = types.SimpleNamespace(temp=self.temp,
                                           ring=lambda: self.reader,
                                           restore=self.restore)

    def temp(self):
        # hooks.temp: the tools read it with the ring held but stopped.
        assert not self.sensor.ring.running, \
            "the temperature read while the ring has the ADC"
        return 30.0

    def restore(self):
        self.restores += 1

    def backlight(self, level):
        self.display.backlight(level)
        self.sensor.show(level)

    def present(self):
        pass

    def ring_start(self):
        if self.reader is None:
            return None
        self.reader.start()
        self.ring = self.reader
        return self.reader

    def ring_stop(self):
        if self.ring is not None:
            self.ring.stop()
            self.ring = None

    def ring_note(self):
        return None if self.reader is not None else "no ring here"

    def cancelled(self):
        self.checks += 1
        return self.cancel_after is not None and \
            self.checks > self.cancel_after

    def tethered(self):
        return True

    def save(self, entry, name=None):
        assert self.ring is None, "saved with the ring running"
        self.saved.append((name, entry))
        return True

    def send(self, data, ext=None, name=None):
        assert self.ring is None, "sent with the ring running"
        self.sent.append(data)


def tool(name):
    """A subtool's namespace, loaded as devserver.Server.load() loads it."""
    path = os.path.join(TOOLS, name + ".py")
    namespace = {"__name__": name}
    with open(path) as handle:
        exec(compile(handle.read(), path, "exec"), namespace)
    return namespace


def drawn_white(injected):
    return injected["badge"].default_clear is None


# --- mirror ------------------------------------------------------------------------

def test_mirror_runs_one_condition_and_saves_it_under_its_name():
    optics, sensor = RO.mirror(room=40.0, gain=400.0, noise=4.0)
    with Injected() as injected, OpticsTime(optics):
        api = Api(sensor, injected["display"])
        result = tool("mirror")["run"](api, ["dark"])
        assert drawn_white(injected)
    assert api.saved and api.saved[0][0] is None
    saved = api.saved[0][1]
    assert list(saved) == ["dark"] and saved["dark"] is result
    assert sorted(result) == sorted(list(measure.RESULT_KEYS)
                                    + ["temperature_c", "tethered"])
    for key in measure.RESULT_KEYS:
        assert not isinstance(result[key], dict) or "error" not in result[key], \
            (key, result[key])
    assert abs(result["ambient"] - 40) <= 8, result["ambient"]
    assert result["levels"]["low"] < result["levels"]["mid"] < \
        result["levels"]["high"], result["levels"]
    assert result["loopback_ring"]["16"]["errors"] == 0
    assert not sensor.ring.running and api.ring is None


def test_mirror_refuses_a_condition_it_does_not_know():
    optics, sensor = RO.mirror()
    with Injected() as injected:
        api = Api(sensor, injected["display"])
        assert "error" in tool("mirror")["run"](api, ["dusk"])
    assert api.saved == [] and not sensor.ring.running


def test_mirror_without_a_ring_saves_why():
    optics, sensor = RO.mirror()
    with Injected() as injected, OpticsTime(optics):
        api = Api(sensor, injected["display"], has_ring=False)
        result = tool("mirror")["run"](api, ["lit"])
    assert result["ambient"] == {"error": "no ring here"}, result
    assert api.saved[0][1]["lit"] is result


def test_a_cancelled_mirror_saves_nothing_and_stops_the_ring():
    optics, sensor = RO.mirror()
    with Injected() as injected, OpticsTime(optics):
        api = Api(sensor, injected["display"])
        api.cancel_after = 2
        assert tool("mirror")["run"](api, ["dark"]) is None
        assert not sensor.ring.running
        api.ring_stop()             # what the link's cleanup does
    assert api.saved == []


# --- second badge ------------------------------------------------------------------

def test_second_badge_needs_a_role_and_a_condition():
    optics, sensor = RO.mirror()
    with Injected() as injected:
        api = Api(sensor, injected["display"])
        run = tool("second_badge")["run"]
        for args in ([], ["first"], ["third", "dark"], ["first", "dusk"]):
            assert "error" in run(api, args), args
    assert api.saved == []


def test_two_badges_measure_each_other_each_on_its_own_link():
    optics, sensor_a, sensor_b = RO.pair(gain=200.0, own=6.0, noise=4.0)
    out = {}
    with Injected() as injected, OpticsTime(optics):
        apis = {"first": Api(sensor_a, injected["display"]),
                "second": Api(sensor_b, injected["display"])}

        def loop(role, delay_ms):
            def body():
                optics.sleep(delay_ms * 1000)
                out[role] = tool("second_badge")["run"](apis[role],
                                                        [role, "dark"])
            return body
        optics.run(loop("first", 0), loop("second", 2500))
    for role in ("first", "second"):
        name, saved = apis[role].saved[0]
        assert saved["role"] == role and saved["dark"] is out[role]
        result = out[role]
        for key in measure.RESULT_KEYS:
            assert not isinstance(result[key], dict) \
                or "error" not in result[key], (role, key, result[key])
        assert abs(result["levels"]["high"] - 240) <= 8, result["levels"]
    assert not sensor_a.ring.running and not sensor_b.ring.running


# --- room flicker ------------------------------------------------------------------

def flicker_optics(hz=100, size=20, room=400.0):
    optics = RO.Optics()

    def light(t_us):
        return room + size * math.sin(2 * math.pi * hz * t_us / 1e6)
    return optics, optics.sensor(room=light if size else room)


def test_room_flicker_measures_with_the_backlight_off_and_numbers_each():
    optics, sensor = flicker_optics()
    saved_windows = flicker.DRIFT_WINDOWS
    flicker.DRIFT_WINDOWS = 3
    try:
        with Injected() as injected, OpticsTime(optics):
            api = Api(sensor, injected["display"])
            out = tool("room_flicker")["run"](api, ["2"])
            assert injected["display"].backlights == [0.0]
            assert injected["badge"].lights == 0.0
    finally:
        flicker.DRIFT_WINDOWS = saved_windows
    assert [name for name, _ in api.saved] == ["room_flicker"] * 2
    assert [list(entry) for _, entry in api.saved] == [["1"], ["1"]], \
        "numbered from the file, which this fake api never writes"
    result = api.saved[-1][1]["1"]
    assert result["peak_hz"] == 100, result
    assert abs(result["peak_counts"] - 20) < 2, result
    assert result["filter"]["verdict"] == "flicker", result["filter"]
    assert result["drift"]["hz"] == 100 and result["drift"]["windows"] == 3
    assert result["tethered"] is True and result["temp_c"] == 30.0
    assert len(out["measured"]) == 2 and not sensor.ring.running


def test_a_steady_room_has_no_drift():
    optics, sensor = flicker_optics(size=0)
    with Injected() as injected, OpticsTime(optics):
        api = Api(sensor, injected["display"])
        tool("room_flicker")["run"](api, [])
    result = api.saved[0][1]["1"]
    assert result["drift"] == {"error": "no mains flicker"}
    assert not sensor.ring.running


def test_room_flicker_numbers_after_what_the_file_holds():
    import toolkit
    saved = toolkit.MEASUREMENTS_PATH
    folder = __import__("tempfile").mkdtemp()
    toolkit.MEASUREMENTS_PATH = os.path.join(folder, "m.json")
    try:
        with open(toolkit.MEASUREMENTS_PATH, "w") as handle:
            handle.write('{"room_flicker": {"1": {}, "7": {}, "x": 1}}')
        assert toolkit.next_key("room_flicker") == "8"
        assert toolkit.next_key("mirror") == "1"
        os.remove(toolkit.MEASUREMENTS_PATH)
        assert toolkit.next_key("room_flicker") == "1"
    finally:
        toolkit.MEASUREMENTS_PATH = saved


# --- frame probe -------------------------------------------------------------------

def test_frame_probe_runs_every_step_and_saves():
    optics, sensor = RO.mirror(gain=0.0, tick_us=2)
    with Injected() as injected, OpticsTime(optics):
        display = injected["display"]
        real = display.backlight

        def backlight(level):
            real(level)
            sensor.show(level)
        display.backlight = backlight
        api = Api(sensor, display)
        result = tool("frame_probe")["run"](api, [])
    assert api.saved == [(None, result)]
    assert sorted(result) == sorted(list(probe.STEPS) + [
        "firmware", "start", "temperature_c", "tethered"])
    for name in probe.STEPS:
        assert "error" not in result[name], (name, result[name])
    for mode in probe.RING_MODES:
        ring = result["ring"][mode]
        assert "error" not in ring and "ended" not in ring, (mode, ring)
        assert 45000 <= ring["rate_hz"] <= 55000, ring
    assert result["ring"]["quiet"]["tx_path"]["changes"] == \
        tool("frame_probe")["TX_PATH_CALLS"]
    assert result["firmware"]["ring"] is None
    assert not sensor.ring.running and gc.isenabled()


def test_the_probe_listener_reads_the_room_through_the_ring():
    optics, sensor = RO.mirror(room=200.0, gain=0.0)
    ns = tool("frame_probe")
    with OpticsTime(optics):
        listener = ns["probe_listener"](protocol, sensor.reader())
        assert sensor.ring.running
        black = listener.levels.black
        assert abs(black - 200) <= 16, black
        assert listener.levels.grey == black + ns["PROBE_LEVEL_GAP"]
        lit = ns["probe_listener"](protocol, sensor.reader(), peer_lit=True)
        assert abs(lit.levels.grey - 200) <= 16


# --- fast loopback -----------------------------------------------------------------

def test_fast_loopback_hands_the_hosts_arguments_to_fastloop():
    seen = []
    fake = types.ModuleType("fastloop")
    fake.run = lambda api, args: seen.append(args) or {"rungs": {}}
    saved = sys.modules.get("fastloop")
    sys.modules["fastloop"] = fake
    try:
        assert tool("fast_loopback")["run"](None, ["0", "8"]) == {"rungs": {}}
    finally:
        if saved is None:
            sys.modules.pop("fastloop", None)
        else:
            sys.modules["fastloop"] = saved
    assert seen == [["0", "8"]]
