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
import held
from held import HeldTransfer

P = load_module("share_wifi", "probe")
PROTOCOL = load_module("share_wifi", "protocol")
READER = load_module("share_wifi", "reader")
PATH = os.path.join(os.path.dirname(P.__file__), "probe.py")

REFRESH_US = 16667      # 60 Hz
INFO = {"version": "test", "set_framerate": False, "rp2": True,
        "uctypes": True, "rp2_dma": True, "ring": None,
        "heap_addr": "0x11012000", "ring_addr": "0x15018000"}
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

    def __init__(self, cancel_after=None, part_us=(1000, 2000)):
        self.clock = Clock()
        self.temp_reads = 0
        self.presents = 0
        self.vsync = True
        self.vsync_calls = []
        self.gc_on = True
        self.gc_log = []
        self.allocated = 0
        self.frames = 0
        self.cancel_after = cancel_after
        self.part_us = part_us
        self.mem_alloc_us = 110000
        # A collection costs more for the garbage since the last one, and
        # for anything left live, for good.
        self.garbage = 0
        self.live = 0

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

    def cancelled(self):
        self.frames += 1
        return self.cancel_after is not None and self.frames >= self.cancel_after

    def collect(self):
        self.clock.advance(40000 + self.garbage + self.live)
        self.garbage = 0
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

    def heap(self):
        """The heap hook: mem_info()'s text, as v2 prints it, laid out. The
        largest free run grows shorter as live objects climb."""
        self.gc_log.append("mem_info")
        self.clock.advance(110000)
        top = 600 + self.live // 100               # KiB
        free_run = (8192 - top) * 1024 // 16
        return P.heap_layout(
            "GC: total: 8388608, used: 460800, free: 7927808\n"
            " No. of 1-blocks: 5210, 2-blocks: 930, max blk sz: 566, "
            "max free sz: %d\n" % free_run, 16)

    def ticking(self):
        """The clock as a reader reads it: 5 us a call, so a wait ends."""
        self.clock.advance(5)
        return self.clock.t

    def ring(self, mode):
        """The ring step's hook: ring_listen() on a listener whose ring
        fills at 50 kHz of this clock, every reading 7 raw (112 counts)."""
        badge = self

        class ClockRing(held.ListRing):
            def __init__(self):
                held.ListRing.__init__(self, 16384, 20)
                for i in range(self.samples):
                    self.buf[i] = 7
                self.t0 = badge.clock.t

            def position(self):
                return (badge.clock.t - self.t0) // 20

        reader = READER.RingReader(ClockRing(), self.ticking)
        reader.start()
        transfer = HeldTransfer(False, reader=reader)
        transfer.levels = PROTOCOL.Levels()
        transfer.levels.fit(32, 64, 112)
        transfer.start_data(0, PROTOCOL.EXACT_LIT_INDEX)
        if mode in ("fitted", "fitted_lit"):
            transfer.flicker = self.fitted()
        out = P.ring_listen(transfer, self.ticking,
                            present=self.present if mode == "presenting"
                            else None,
                            poll=self.poll, collect=self.collect,
                            mem_free=self.mem_free,
                            cancelled=lambda: False, ms=60)
        out["room"] = 32
        if mode == "quiet":
            # The app's probe_tx_path(), in shape.
            stats = {"calls": 200, "per_s": 5000, "mean_us": 200.0,
                     "max_us": 260, "sd_us": 10.0}
            out["tx_path"] = {"field_and_show": stats,
                              "backlight_call": stats, "changes": 200}
        if mode in ("fitted", "fitted_lit"):
            out["fit_verdict"] = "flicker"
        return out

    def fitted(self):
        """A filter fitted on the probe's room, as the app's
        fitted_filter() makes one."""
        ts, vs, spans = P.filter_room(0, P.FILTER_SAMPLES)
        flicker = PROTOCOL.FlickerFilter()
        assert flicker.fit(ts, vs, spans) == "flicker"
        return flicker

    def hooks(self, set_vsync=True):
        a, b = self.part_us
        # The app's status frame, its one part: text.
        return P.Hooks(
            present=self.present,
            set_vsync=self.set_vsync if set_vsync else None,
            poll=self.poll, clear=self.clear,
            frame=(("status", self.part(a, 0)),),
            cancelled=self.cancelled,
            collect=self.collect, enable=self.enable, disable=self.disable,
            mem_alloc=self.mem_alloc, mem_free=self.mem_free,
            ticks_us=self.clock, temp=self.temp,
            flicker_filter=self.flicker_filter(), ring=self.ring,
            heap=self.heap)

    def flicker_filter(self):
        """The protocol's filter, each call costing time on the clock."""
        clock = self.clock
        base = PROTOCOL.FlickerFilter

        class Ticking(base):
            updates = []

            def fit(self, ts, vs, spans):
                clock.advance(3000)
                return base.fit(self, ts, vs, spans)

            def correct(self, now_us, value, span_us=0):
                clock.advance(40)
                return base.correct(self, now_us, value, span_us)

            def track(self, now_us, value, span_us):
                clock.advance(30)
                base.track(self, now_us, value, span_us)

            def steady_window(self, ts, vs):
                clock.advance(20 * len(ts))
                return base.steady_window(self, ts, vs)

            def update(self):
                clock.advance(60)
                base.update(self)
                # Whether this update reached the rate's line.
                w = self.lw
                spread = self.ltt / w - (self.lt / w) ** 2 if w else 0.0
                Ticking.updates.append(
                    spread >= PROTOCOL.FLICKER_RATE_MIN_S ** 2 / 4)
        return Ticking

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
    assert sorted(out) == sorted(list(P.STEPS) + ["firmware", "start",
                                                  "temperature_c"])
    assert out["firmware"] == INFO


def test_every_step_is_a_method_of_its_name():
    # step() dispatches by name: a step without a method would only ever
    # record an AttributeError.
    for name in P.STEPS:
        assert callable(getattr(P.FrameProbe, name, None)), name


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
    assert "mean_ms" in probe.results()["refresh"]


def test_the_heap_walk_stays_out_of_the_timed_frames():
    # gc.mem_alloc() took ~110 ms a call on the badge and was once called
    # twice inside every timed frame, which put 267 ms frames in the first
    # probe's results. The gc step reads it once, outside its frames.
    probe, _ = run_all(Badge(part_us=(1000, 2000)))
    out = probe.results()
    # Every frame is one refresh, bar the first, which starts part way
    # through one: none comes near a 110 ms mem_alloc().
    frames = [out["gc"][key] for key in ("frames_on", "frames_off")]
    for frame in frames:
        # One odd first frame in 60 moves the mean by up to a sixtieth of a
        # refresh or so; a mem_alloc() in every frame would move it by 110.
        assert abs(frame["mean_ms"] - 16.67) <= 0.3, frame
        assert frame["max_ms"] < 2 * 16.67, frame


def test_a_frame_that_overruns_a_refresh_waits_for_the_next():
    # 12 ms of drawing + a 7 ms write is more than 16.7 ms: two refreshes.
    probe, _ = run_all(Badge(part_us=(12000, 0)))
    frame = probe.results()["gc"]["frames_on"]
    # The first frame starts part way through a refresh; every other is two.
    assert frame["max_ms"] == 33.33 and abs(frame["mean_ms"] - 33.33) < 0.2, \
        frame


def run_until(probe, name):
    while probe.step_name != name:
        probe.step()


def test_reads_times_an_empty_loop_without_presenting():
    badge = Badge()
    probe = P.FrameProbe(badge.hooks())
    run_until(probe, "reads")
    presents = badge.presents
    # A clock read costs 5 us: the empty loop's whole pass.
    probe.hooks.ticks_us = badge.ticking
    probe.step()
    assert badge.presents == presents, "the loop presented"
    reads = probe.results()["reads"]
    assert list(reads) == ["loop"]
    assert reads["loop"]["calls"] == P.READ_CALLS, reads
    assert reads["loop"]["mean_us"] == 5.0, reads


def test_a_loop_stops_at_read_ms_and_a_stall_shows_as_its_max():
    clock = Clock()
    passes = [0]

    def listen():
        passes[0] += 1
        clock.advance(40000 if passes[0] % 100 == 0 else 3000)
    out = P.loop_rate(listen, clock)
    # A pass is counted once it ends, so the last one may run past.
    assert out["calls"] < P.READ_CALLS
    assert out["max_us"] == 40000
    assert out["mean_us"] > 3000 and out["sd_us"] > 0


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
    gc_end = badge.gc_log.index("disable") + 3
    assert badge.gc_log[gc_end - 2:gc_end] == ["enable", "collect"]
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
    probe.hooks.frame = (("status", explode),)
    probe.step()
    assert badge.gc_on, "the collector was left off"
    assert probe.results()["gc"] == {"error": "RuntimeError: boom"}
    assert probe.done, "the probe goes on to its end"


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
    badge = Badge()
    probe = P.FrameProbe(badge.hooks())
    run_until(probe, "gc")
    badge.cancel_after = badge.frames + 5
    probe.step()                        # the gc step: cancelled after 5
    assert probe.cancelled and probe.done
    assert probe.results()["gc"]["frames_on"]["frames"] == 5
    assert "frames_off" not in probe.results()["gc"]
    assert badge.gc_on


def test_cancelling_the_ring_step_ends_the_probe():
    badge = Badge()
    probe = P.FrameProbe(badge.hooks())
    run_until(probe, "ring")
    probe.hooks.cancelled = lambda: True
    probe.step()
    assert probe.cancelled and probe.done
    assert list(probe.results()["ring"]) == [P.RING_MODES[0]]
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
    spec = os.path.join(REPO, ".agents", "share_wifi", "app-spec.md")
    text = open(spec).read()
    block = text.split("`<probe>` has one key", 1)[1].split("### Display", 1)[0]
    spec_keys = set(re.findall(r'"([A-Za-z0-9_.]+)":', block))
    probe, _ = run_all(Badge())
    # The fake's clock moves only when a hook costs time, so the empty loop
    # times nothing and records an error; the badge's always moves.
    ours = keys_of(probe.results()) - {"error"}
    assert spec_keys - ours == set(), spec_keys - ours
    # The example is the only description of the format, so nothing the probe
    # writes may be missing from it.
    assert ours - spec_keys == set(), ours - spec_keys


def test_flicker_filter_times_each_part_on_a_room_it_fits():
    badge = Badge()
    hooks = badge.hooks()
    out = P.filter_costs(hooks.flicker_filter, badge.clock)
    assert out["verdict"] == "flicker", out
    assert out["samples"] == 80 and out["follower_samples"] == 50
    assert out["fit_first_us"] == 3000 and out["correct_first_us"] == 40
    assert out["fit"]["calls"] == out["fit_follower"]["calls"] == 3
    assert out["fit"]["mean_us"] == 3000.0
    assert out["correct"] == {"calls": 500, "per_s": 25000, "mean_us": 40.0,
                              "max_us": 40, "sd_us": 0.0}
    assert out["track"]["calls"] == 8 * P.FILTER_UPDATES
    assert out["track"]["mean_us"] == 30.0
    assert out["update"]["mean_us"] == out["update_rate"]["mean_us"] == 60.0
    assert out["update"]["calls"] + out["update_rate"]["calls"] < \
        P.FILTER_UPDATES, "the updates between the two are not timed"
    # The steadiness check on each side's window.
    assert out["steady_check"]["calls"] == P.STEADY_CHECKS
    assert out["steady_check"]["mean_us"] == 20.0 * 50
    assert out["steady_check_follower"]["mean_us"] == 20.0 * 30


def test_the_steady_windows_are_the_protocols():
    every_ms = P.FILTER_EVERY_US // 1000
    assert P.STEADY_LEADER_SAMPLES == PROTOCOL.FLICKER_FAST_FIT_MS // every_ms
    assert P.STEADY_FOLLOWER_SAMPLES == PROTOCOL.FOLLOWER_CHECK_MS // every_ms


def test_updates_are_split_where_the_rate_line_starts():
    badge = Badge()
    hooks = badge.hooks()
    hooks.flicker_filter.updates.clear()
    out = P.filter_costs(hooks.flicker_filter, badge.clock)
    reached = hooks.flicker_filter.updates
    # Every update timed as `update` came before the rate's line was used,
    # every one timed as `update_rate` after.
    early = out["update"]["calls"]
    late = out["update_rate"]["calls"]
    assert early >= 8 and late >= 8, out
    assert not any(reached[:early]), reached
    assert all(reached[-late:]), reached


def test_flicker_filter_is_skipped_without_the_hook():
    assert P.filter_costs(None, Clock()) == {"error": "no flicker filter"}


def test_the_synthetic_room_is_the_same_every_run():
    a = P.filter_room(1000, 20)
    b = P.filter_room(1000, 20)
    assert a == b
    assert {a[0][k + 1] - a[0][k] for k in range(19)} == {2000}
    assert set(a[2]) == {2000}


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


def test_the_ring_step_runs_the_exact_listener_quiet_and_presenting():
    probe, _ = run_all(Badge())
    ring = probe.results()["ring"]
    # And a third run with a fitted flicker filter: M3's slot cost as a
    # flickering room would make it.
    assert tuple(sorted(ring)) == tuple(sorted(P.RING_MODES))
    for name in P.RING_MODES:
        out = ring[name]
        assert "error" not in out, out
        assert out["rung_ms"] == 8
        assert out["slots"] > 0 and out["slot_work"]["calls"] > 0, out
        assert 45000 <= out["rate_hz"] <= 55000, out
        assert out["fifo_over"] == 0 and out["faults"] == 0
        assert abs(out["gc_ms"] - 40.0) < 0.1 and out["gc_overran"] is False
        assert "garbage_per_slot" in out and "kernel_us_per_1000" in out


def dark_listener(badge, raw):
    """A Follower listening on the lit 8 ms rung through a ring that reads
    `raw` on every reading, with the dark mirror run's levels -- what the
    app's probe_listener() gave it until 2026-10-09."""
    class ClockRing(held.ListRing):
        def __init__(self):
            held.ListRing.__init__(self, 16384, 20)
            for i in range(self.samples):
                self.buf[i] = raw
            self.t0 = badge.clock.t

        def position(self):
            return (badge.clock.t - self.t0) // 20

    reader = READER.RingReader(ClockRing(), badge.ticking)
    reader.start()
    transfer = HeldTransfer(False, reader=reader)
    transfer.levels = PROTOCOL.Levels()
    transfer.levels.fit(32, 64, 112)
    transfer.start_data(0, PROTOCOL.EXACT_LIT_INDEX)
    return transfer


def test_a_dark_room_and_a_slow_start_do_not_end_the_ring_listener():
    # The fresh launch of 2026-10-09: the room read 33 counts, below T_low,
    # so every slot was black; and mem_free() walked the heap for over
    # 136 ms after the listener's clock started, and again before the
    # mid-run collection. Its first pass decided
    # those 17 slots in one work() call, LOST_PEER_SLOTS ran out inside it,
    # and the listener failed: slot_work had one call, and the quiet step's
    # tx_path raised on a listener with no slot to send on.
    badge = Badge()
    transfer = dark_listener(badge, 2)          # raw 2: 32 counts

    def slow_mem_free():
        badge.clock.advance(150000)
        return badge.mem_free()
    out = P.ring_listen(transfer, badge.ticking, poll=badge.poll,
                        collect=badge.collect, mem_free=slow_mem_free,
                        cancelled=lambda: False, ms=400)
    assert transfer.result is None, transfer.result
    assert "ended" not in out, out
    # 400 ms of 8 ms slots, less the 150 ms the walk took again before the
    # mid-run collection: those slots are skipped too.
    assert 28 <= out["slots"] <= 34, out
    assert out["slot_work"]["calls"] > 10, out


def test_a_listener_that_stops_listening_says_so():
    badge = Badge()
    transfer = dark_listener(badge, 2)
    transfer.fail(PROTOCOL.ERR_LOST_PEER)
    out = P.ring_listen(transfer, badge.ticking, cancelled=lambda: False,
                        ms=40)
    assert out["ended"] == PROTOCOL.ERR_LOST_PEER
    assert out["slots"] == 0


def test_catching_up_skips_the_slots_already_over():
    badge = Badge()
    transfer = dark_listener(badge, 2)
    P.catch_up(transfer, 150000)
    assert transfer.next == 150000 // 8000
    P.catch_up(transfer, 1000)                  # never back
    assert transfer.next == 150000 // 8000


def test_timestats_keeps_the_sum_of_squares_exactly():
    # add() splits each square across two small ints; together they are
    # the exact sum, the low one always under 2**28, for any value up to
    # ~2**28 us either side of 0.
    import random
    rng = random.Random(7)
    values = ([0, 1, 16383, 16384, 16385, 32767, 32768, -1, -16384, -40000,
               59463, 1000000, 2 ** 27, -(2 ** 27)]
              + [rng.randrange(-300000, 3000000) for _ in range(5000)])
    stats = P.TimeStats()
    exact = 0
    for us in values:
        stats.add(us)
        exact += us * us
        assert 0 <= stats.sq_lo < 2 ** 28, us
        assert stats.sq_hi * 2 ** 28 + stats.sq_lo == exact, us


def test_the_heap_is_read_at_the_start_and_in_the_gc_step():
    badge = Badge()
    probe, _ = run_all(badge)
    out = probe.results()
    assert out["start"]["gc_ms"] == 40.0
    assert out["start"]["heap"]["top_kb"] == 600
    assert out["gc"]["heap"]["block_bytes"] == 16
    # The start reading comes before the first step's temperature.
    assert badge.gc_log[:4] == ["collect", "collect", "mem_info", "collect"]


def test_without_a_heap_hook_the_collections_are_still_timed():
    # A firmware without dupterm or mem_info hands in none.
    badge = Badge()
    hooks = badge.hooks()
    hooks.heap = None
    probe = P.FrameProbe(hooks)
    while not probe.done:
        probe.step()
    out = probe.results()
    assert "heap" not in out["start"] and "heap" not in out["gc"]
    assert out["start"]["gc_ms"] == 40.0


def test_a_failing_heap_reading_is_recorded_not_fatal():
    badge = Badge()

    def broken():
        raise OSError("dupterm")
    assert P.heap_reading(broken) == {"error": "OSError: dupterm"}
    assert P.heap_reading(None) is None


def test_heap_layout_reads_the_top_from_the_largest_free_run():
    text = ("GC: total: 8196480, used: 75376, free: 8121104\n"
            " No. of 1-blocks: 626, 2-blocks: 104, max blk sz: 566, "
            "max free sz: 453039\n")
    out = P.heap_layout(text, 16)
    assert out["top_kb"] == 925 and out["used"] == 75376, out
    assert P.heap_reading(lambda: P.heap_layout("nothing", 16)) == {
        "error": "nothing parsed"}
    assert P.heap_reading(None) is None


def test_capture_print_catches_what_a_call_prints_and_restores_the_slot():
    import io
    slot = ["old"]

    def dupterm(stream, index):
        assert index == 0
        previous, slot[0] = slot[0], stream
        return previous

    def printer():
        data = bytearray(b"GC: total: 64\n")
        assert slot[0].write(memoryview(data)) == len(data)
        assert slot[0].readinto(bytearray(1)) is None
        assert slot[0].ioctl(3, 0) == 0

    assert P.capture_print(printer, dupterm, io.IOBase) == "GC: total: 64\n"
    assert slot == ["old"]

    def broken():
        raise OSError("print")
    try:
        P.capture_print(broken, dupterm, io.IOBase)
    except OSError:
        pass
    else:
        raise AssertionError("the call's error is not swallowed")
    assert slot == ["old"]


class FakeHeap:
    """A pool ending at `end`: a bytearray lands at `buffer`, its header
    at `header`, as py/gc.c's first fit would put them."""

    def __init__(self, buffer, header, fail=None):
        self.buffer = buffer
        self.header = header
        self.fail = fail
        self.sizes = []

    def alloc(self, n):
        if self.fail:
            raise self.fail
        self.sizes.append(n)
        return self

    def addressof(self, _buf):
        return self.buffer

    def ident(self, _buf):
        return self.header


def extent(heap, layout, end=0x11800000):
    return P.heap_extent(layout, heap.addressof, end, heap.alloc, heap.ident)


# 8,000 KiB of 16-byte blocks: 512,000 blocks, the pool from 0x1102f000.
POOL = {"block_bytes": 16, "total": 8192000}
START = 0x11800000 - 8192000


def test_a_run_reaching_the_top_puts_the_extent_at_its_start():
    # 2,000 KiB live below a run of the last 6,000 KiB; the header in a
    # hole lower down, so the buffer starts the run.
    run = 6000 * 1024 // 16
    layout = dict(POOL, max_free=run)
    heap = FakeHeap(START + 2000 * 1024, START + 160)
    out = extent(heap, layout)
    assert heap.sizes == [(run - 1) * 16], "the run less one block"
    assert out == {"run_start_kb": 2000, "run_end_kb": 8000,
                   "extent_kb": 2000, "extent_exact": True}, out


def test_a_header_that_took_the_runs_first_block_is_counted_in_it():
    run = 6000 * 1024 // 16
    layout = dict(POOL, max_free=run)
    first = START + 2000 * 1024
    heap = FakeHeap(first + 16, first)
    out = extent(heap, layout)
    assert out["run_start_kb"] == 2000 and out["extent_exact"], out


def test_a_live_block_above_the_run_makes_its_end_a_floor():
    # Live blocks above a run of 7,656 KiB starting 300 KiB up: the
    # extent is at least the run's end.
    run = 7656 * 1024 // 16
    layout = dict(POOL, max_free=run)
    heap = FakeHeap(START + 300 * 1024, START + 32)
    out = extent(heap, layout)
    assert out == {"run_start_kb": 300, "run_end_kb": 7956,
                   "extent_kb": 7956, "extent_exact": False}, out


def test_one_live_block_at_the_very_top_is_not_mistaken_for_none():
    # The run ends a block short of the pool: the header elsewhere, the
    # buffer at the run's start, its spare block the last free one.
    blocks = 8192000 // 16
    run = 1000
    layout = dict(POOL, max_free=run)
    first = START + (blocks - 1 - run) * 16
    out = extent(FakeHeap(first, START), layout)
    assert out["extent_exact"] is False and out["run_end_kb"] == 7999, out


def test_an_extent_that_cannot_be_read_is_an_error_not_a_crash():
    layout = dict(POOL, max_free=1000)
    out = extent(FakeHeap(0, 0, fail=MemoryError("memory allocation failed")),
                 layout)
    assert out == {"extent_error": "MemoryError: memory allocation failed"}
    out = extent(FakeHeap(0x20000000, 0), layout)
    assert out == {"extent_error": "run at 0x20000000, outside the pool"}
    assert extent(FakeHeap(START, 0), {"block_bytes": 16}) == {}
    assert extent(FakeHeap(START, 0), dict(POOL, max_free=1)) == {}


def test_the_heap_hook_adds_the_extent_only_with_addressof():
    calls = []

    def dupterm(stream, _slot):
        if stream is not None and hasattr(stream, "write"):
            stream.write(b"GC: total: 8192000, used: 1, free: 2\n"
                         b" No. of 1-blocks: 1, 2-blocks: 1, max blk sz: 1,"
                         b" max free sz: 384000\n")
        return None

    class Base:
        pass

    def addressof(buf):
        calls.append(len(buf))
        return START + 2000 * 1024
    read = P.heap_reader(dupterm, lambda: None, Base, 16)
    assert "extent_kb" not in read() and not calls
    read = P.heap_reader(dupterm, lambda: None, Base, 16, addressof)
    out = read()
    assert calls == [(384000 - 1) * 16]
    assert out["extent_kb"] == 2000 and out["extent_exact"], out
