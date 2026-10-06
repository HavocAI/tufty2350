"""Dev Mode's frame timing probe (probe.py), against a simulated clock.

Every hook advances a fake microsecond clock by a known amount, so each
figure the probe reports can be checked exactly: a 60 Hz panel with a 7 ms
write must come back as 16.67 ms and 60 Hz with vsync and 7 ms without.
Nothing here says what the badge will measure -- only that the probe adds up
what it times, restores what it changes, and survives what it cannot do.
"""

import ast
import os

from harness import load_module

P = load_module("share_wifi", "probe")
PATH = os.path.join(os.path.dirname(P.__file__), "probe.py")

REFRESH_US = 16667      # 60 Hz
INFO = {"version": "test", "set_framerate": False}
WRITE_US = 7000


class Clock:
    def __init__(self):
        self.t = 0

    def __call__(self):
        return self.t

    def advance(self, us):
        self.t += us


class Badge:
    """The hooks, each costing a fixed time on the clock."""

    def __init__(self, cancel_after=None, part_us=(1000, 2000),
                 read_us=(2, 12, 40, 150, 180)):
        self.clock = Clock()
        self.temp_reads = 0
        self.presents = 0
        self.read_us = read_us
        self.vsync = True
        self.vsync_calls = []
        self.gc_on = True
        self.gc_log = []
        self.allocated = 0
        self.frames = 0
        self.starts = 0
        self.cancel_after = cancel_after
        self.part_us = part_us
        self.mem_alloc_us = 110000

    def present(self):
        self.presents += 1
        if self.vsync:
            # Wait for the next refresh, then write.
            t = self.clock.t
            self.clock.t = (t // REFRESH_US + 1) * REFRESH_US
        self.clock.advance(WRITE_US)

    def set_vsync(self, on):
        self.vsync = on
        self.vsync_calls.append(on)

    def poll(self):
        self.clock.advance(100)

    def clear(self):
        self.clock.advance(500)

    def part(self, us, grows):
        def run():
            self.clock.advance(us)
            self.allocated += grows
        return run

    def start(self):
        self.starts += 1

    def cancelled(self):
        self.frames += 1
        return self.cancel_after is not None and self.frames >= self.cancel_after

    def collect(self):
        self.clock.advance(40000)
        self.gc_log.append("collect")

    def enable(self):
        self.gc_on = True
        self.gc_log.append("enable")

    def disable(self):
        self.gc_on = False
        self.gc_log.append("disable")

    def mem_alloc(self):
        # As on the badge's v2 firmware: walking the heap is slow.
        self.clock.advance(self.mem_alloc_us)
        return self.allocated

    def temp(self):
        self.temp_reads += 1
        return 30.0 + self.temp_reads

    def mem_free(self):
        return 8 * 1024 * 1024 - self.allocated

    def hooks(self, set_vsync=True):
        a, b = self.part_us
        # The app's parts, by name; lights and measure cost nothing here.
        free = self.part(0, 0)
        screens = (("field", self.start, (("field", self.part(a, 64)),
                                          ("measure", free))),
                   ("status", self.start, (("bands", self.part(a, 96)),
                                           ("column", self.part(b, 320)),
                                           ("lights", free),
                                           ("measure", free))))
        return P.Hooks(
            present=self.present,
            set_vsync=self.set_vsync if set_vsync else None,
            poll=self.poll, clear=self.clear, screens=screens,
            cancelled=self.cancelled,
            collect=self.collect, enable=self.enable, disable=self.disable,
            mem_alloc=self.mem_alloc, mem_free=self.mem_free,
            ticks_us=self.clock, temp=self.temp,
            reads=tuple((name, self.part(us, 0)) for name, us in
                        zip(("loop", "read", "sample", "listen",
                             "transfer"),
                            self.read_us)))


def run_all(badge, **kw):
    probe = P.FrameProbe(badge.hooks(**kw), INFO)
    names = []
    while not probe.done:
        names.append(probe.step_name)
        probe.step()
    return probe, names


def test_the_steps_run_in_order_one_per_call():
    probe, names = run_all(Badge())
    assert names == list(P.STEPS)
    assert sorted(P.STEP_TITLES) == sorted(P.STEPS)
    out = probe.results()
    assert sorted(out) == sorted(list(P.STEPS) + ["firmware", "temperature_c"])
    assert out["firmware"] == INFO


def test_refresh_reads_the_panel_period_and_rate():
    probe, _ = run_all(Badge())
    refresh = probe.results()["refresh"]
    assert refresh["frames"] == P.PRESENTS
    assert abs(refresh["mean_ms"] - 16.67) <= 0.01, refresh
    assert refresh["hz"] == 60.0
    assert refresh["sd_ms"] <= 0.01


def test_write_is_timed_without_vsync_and_vsync_is_restored():
    badge = Badge()
    probe, _ = run_all(badge)
    write = probe.results()["write"]
    assert write["mean_ms"] == 7.0 and write["max_ms"] == 7.0, write
    assert badge.vsync_calls == [False, True]
    assert badge.vsync


def test_a_firmware_without_set_vsync_skips_the_write_step():
    probe, _ = run_all(Badge(), set_vsync=False)
    assert probe.results()["write"] == {"error": "no set_vsync"}
    assert "mean_ms" in probe.results()["breakdown"]["field"]["frame"]


def test_the_breakdown_times_each_part_and_the_frame():
    probe, _ = run_all(Badge(part_us=(1000, 2000)))
    breakdown = probe.results()["breakdown"]
    assert sorted(breakdown) == ["field", "status"]
    status = breakdown["status"]
    assert status["bands"]["mean_ms"] == 1.0
    assert status["column"]["mean_ms"] == 2.0
    assert status["clear"]["mean_ms"] == 0.5
    assert status["poll"]["mean_ms"] == 0.1
    assert status["alloc_bytes"] == 96 + 320
    assert breakdown["field"]["alloc_bytes"] == 64
    # Clear, parts, and the write fit inside one refresh, so a frame is one
    # refresh: present() waits out the rest.
    assert abs(status["frame"]["mean_ms"] - 16.67) <= 0.01, status["frame"]
    assert status["frame"]["frames"] == P.BREAKDOWN_FRAMES
    present = status["present"]["mean_ms"]
    assert 7.0 <= present <= 16.67


def test_a_collection_inside_a_frame_is_not_read_as_allocation():
    badge = Badge()
    hooks = badge.hooks()
    shrink = [0]

    def collected():
        # Every third frame a collection frees more than the frame made.
        shrink[0] += 1
        badge.allocated += 100 if shrink[0] % 3 else -5000
    hooks.screens = (("field", badge.start, (("field", collected),)),)
    probe = P.FrameProbe(hooks)
    while probe.step_name != "breakdown":
        probe.step()
    probe.step()
    assert probe.results()["breakdown"]["field"]["alloc_bytes"] == 100


def test_counting_allocation_does_not_slow_the_timed_frames():
    # gc.mem_alloc() took ~110 ms a call on the badge and was once called
    # twice inside every timed frame, which put 267 ms frames in the first
    # probe's results. Allocation is now counted in frames of its own.
    probe, _ = run_all(Badge(part_us=(1000, 2000)))
    out = probe.results()
    # Every frame is one refresh, bar the first, which starts part way
    # through one: none comes near a 110 ms mem_alloc().
    frames = [out["breakdown"][name]["frame"] for name in ("field", "status")]
    frames += [out["gc"][key] for key in ("frames_on", "frames_off")]
    for frame in frames:
        # One odd first frame in 60 moves the mean by up to a sixtieth of a
        # refresh or so; a mem_alloc() in every frame would move it by 110.
        assert abs(frame["mean_ms"] - 16.67) <= 0.3, frame
        assert frame["max_ms"] < 2 * 16.67, frame


def test_a_frame_that_overruns_a_refresh_waits_for_the_next():
    # 12 ms of drawing + a 7 ms write is more than 16.7 ms: two refreshes.
    probe, _ = run_all(Badge(part_us=(4000, 8000)))
    frame = probe.results()["breakdown"]["status"]["frame"]
    assert abs(frame["mean_ms"] - 33.33) <= 0.01, frame


def test_each_screen_starts_fresh():
    badge = Badge()
    run_all(badge)
    # Two breakdown screens, then the gc step's two runs of the last one.
    assert badge.starts == 4


def run_until(probe, name):
    while probe.step_name != name:
        probe.step()


def test_reads_loops_each_call_without_presenting():
    badge = Badge()
    probe = P.FrameProbe(badge.hooks())
    run_until(probe, "reads")
    presents = badge.presents
    probe.step()
    assert badge.presents == presents, "a reads loop presented"
    reads = probe.results()["reads"]
    assert list(reads) == ["loop", "read", "sample", "listen", "transfer"]
    for name, us in zip(reads, badge.read_us):
        entry = reads[name]
        # Each is fast enough to make every call inside READ_MS.
        assert entry["calls"] == P.READ_CALLS, (name, entry)
        assert entry["mean_us"] == us and entry["max_us"] == us, (name, entry)
        assert entry["per_s"] == round(1000000.0 / us), (name, entry)
        assert entry["sd_us"] == 0.0


def test_a_slow_loop_stops_at_read_ms():
    badge = Badge(read_us=(2, 1000, 2500, 3000, 3000))
    probe = P.FrameProbe(badge.hooks())
    run_until(probe, "reads")
    probe.step()
    reads = probe.results()["reads"]
    assert reads["read"]["calls"] == P.READ_MS
    # A pass is counted once it ends, so the last one may run past.
    assert reads["sample"]["calls"] == P.READ_MS * 1000 // 2500
    assert reads["listen"]["calls"] == -(-P.READ_MS * 1000 // 3000)


def test_a_stall_in_a_reads_loop_shows_as_its_max():
    badge = Badge()
    hooks = badge.hooks()
    passes = [0]

    def listen():
        # A collection every 100th pass, as the slot receiver's lists grow.
        passes[0] += 1
        badge.clock.advance(40000 if passes[0] % 100 == 0 else 150)
    hooks.reads = (("listen", listen),)
    probe = P.FrameProbe(hooks)
    run_until(probe, "reads")
    probe.step()
    listen = probe.results()["reads"]["listen"]
    assert listen["max_us"] == 40000
    assert listen["mean_us"] > 150 and listen["sd_us"] > 0


def test_cancelling_between_reads_loops_stops_there():
    badge = Badge()
    hooks = badge.hooks()
    probe = P.FrameProbe(hooks)
    run_until(probe, "reads")
    hooks.cancelled = lambda: True
    probe.step()
    assert probe.cancelled and probe.done
    assert list(probe.results()["reads"]) == ["loop"]


def test_no_reads_hooks_is_an_error_not_a_crash():
    badge = Badge()
    hooks = badge.hooks()
    hooks.reads = ()
    probe = P.FrameProbe(hooks)
    run_until(probe, "reads")
    probe.step()
    assert probe.results()["reads"] == {"error": "no reads"}
    assert probe.step_name == "alloc"


def test_the_temperature_is_read_before_every_step_and_at_the_end():
    probe, _ = run_all(Badge())
    temps = probe.results()["temperature_c"]
    assert list(temps) == list(P.STEPS) + ["end"], list(temps)
    values = list(temps.values())
    assert values == sorted(values), values


def test_no_temperature_sensor_is_recorded_in_the_probe():
    badge = Badge()
    hooks = badge.hooks()
    hooks.temp = None
    probe = P.FrameProbe(hooks)
    while not probe.done:
        probe.step()
    assert probe.results()["temperature_c"] == {"error": "no temperature sensor"}


def test_gc_times_collection_and_compares_frames_with_it_off():
    badge = Badge()
    probe, _ = run_all(badge)
    gc = probe.results()["gc"]
    assert gc["collect"]["mean_ms"] == 40.0
    assert gc["screen"] == "status"
    assert gc["frames_on"]["frames"] == P.GC_FRAMES
    assert gc["frames_off"]["frames"] == P.GC_FRAMES
    assert "free_kb" in gc and "alloc_kb" in gc
    assert badge.gc_log.count("disable") == 1
    assert badge.gc_log[-2:] == ["enable", "collect"]
    assert badge.gc_on


def test_the_collector_is_reenabled_when_a_frame_raises():
    badge = Badge()
    probe = P.FrameProbe(badge.hooks())
    while probe.step_name != "gc":
        probe.step()
    calls = [0]

    def explode():
        calls[0] += 1
        if not badge.gc_on:
            raise RuntimeError("boom")
    probe.hooks.screens = (("status", badge.start, (("column", explode),)),)
    probe.step()
    assert badge.gc_on, "the collector was left off"
    assert probe.results()["gc"] == {"error": "RuntimeError: boom"}
    assert probe.done


def test_a_step_that_raises_is_recorded_and_the_probe_goes_on():
    badge = Badge()

    def broken():
        raise AttributeError("no update")
    hooks = badge.hooks()
    hooks.present = broken
    probe = P.FrameProbe(hooks)
    probe.step()
    assert probe.results()["refresh"] == {
        "error": "AttributeError: no update"}
    assert probe.step_name == "write"
    probe.step()
    assert probe.results()["write"]["error"].startswith("AttributeError")
    assert badge.vsync_calls == [False, True], "write still restores vsync"


def test_cancelling_stops_mid_step_and_ends_the_probe():
    badge = Badge(cancel_after=5)
    probe = P.FrameProbe(badge.hooks())
    probe.step()
    probe.step()
    assert not probe.done
    probe.step()                        # the breakdown: cancelled after 5
    assert probe.cancelled and probe.done
    assert probe.results()["breakdown"]["field"]["frame"]["frames"] == 5
    assert "status" not in probe.results()["breakdown"]
    probe.step()                        # does nothing more
    assert "alloc" not in probe.results()


def test_alloc_costs_report_every_maker_net_of_the_call():
    clock = Clock()

    def ticks():
        clock.advance(3)
        return clock.t
    out = P.alloc_costs(ticks)
    assert out["calls"] == P.ALLOC_CALLS
    for key in ("call_us", "pair_us", "quad_us", "octet_us"):
        assert out[key] >= 0, (key, out)


def test_the_tuple_makers_really_allocate():
    # A literal tuple of constants is built at compile time and allocates
    # nothing; the makers must build theirs from their arguments.
    a, b = object(), object()
    for _key, maker in P.ALLOCATIONS[1:]:
        made = maker(a, b, a, b)
        assert made is not maker(a, b, a, b)
        assert len(made) in (2, 4, 8)


def test_stats_summarise_in_milliseconds():
    stats = P.TimeStats()
    assert stats.result() == {"error": "no frames"}
    for us in (16000, 17000, 18000):
        stats.add(us)
    assert stats.result() == {"frames": 3, "mean_ms": 17.0, "min_ms": 16.0,
                              "max_ms": 18.0, "sd_ms": 0.82}
    assert stats.part() == {"mean_ms": 17.0, "max_ms": 18.0}
    assert stats.rate() == {"calls": 3, "per_s": 59, "mean_us": 17000.0,
                            "max_us": 18000, "sd_us": 816.5}
    assert P.TimeStats().rate() == {"error": "no calls"}


def keys_of(obj):
    out = set()
    if isinstance(obj, dict):
        for key in obj:
            out.add(key)
            out |= keys_of(obj[key])
    return out


def test_the_spec_example_names_only_keys_the_probe_writes_and_all_of_them():
    import re
    from harness import REPO
    spec = os.path.join(REPO, ".agents", "docs", "share_wifi_spec.md")
    text = open(spec).read()
    block = text.split("`<probe>` has one key", 1)[1].split("### display", 1)[0]
    spec_keys = set(re.findall(r'"([A-Za-z0-9_.]+)":', block))
    probe, _ = run_all(Badge())
    ours = keys_of(probe.results())
    assert spec_keys - ours == set(), spec_keys - ours
    # The example is the only description of the format, so nothing the probe
    # writes may be missing from it.
    assert ours - spec_keys == set(), ours - spec_keys


def test_display_measurements_orders_every_probe_key():
    # Keys missing from KEY_ORDER would be shown alphabetically, putting
    # "clear" after "bands" and "write" after "refresh"'s neighbours.
    measure = load_module("share_wifi", "measure")
    probe, _ = run_all(Badge())
    missing = sorted(keys_of(probe.results()) - set(measure.KEY_ORDER))
    assert not missing, missing


# --- MicroPython hygiene ----------------------------------------------------

def test_probe_references_no_badge_globals():
    import test_module_boundary as boundary
    _, read = boundary.loaded_names(PATH)
    leaked = sorted(read & (boundary.BADGE_GLOBALS | {"display", "gc"}))
    assert not leaked, "probe.py reaches for the badge: %s" % leaked


def test_probe_avoids_what_micropython_lacks():
    tree = ast.parse(open(PATH).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            assert not any(k.arg is None for k in node.keywords), \
                "** unpacking at line %d" % node.lineno
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "%r" not in node.value, "%%r at line %d" % node.lineno
