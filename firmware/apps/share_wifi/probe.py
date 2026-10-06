"""Dev Mode's frame timing probe: where a frame's time goes on this badge.

The first hardware run measured 54 ms frames where the docs expected 11 ms.
This finds out why, in six steps, as specified under "probe frame timing" in
.agents/docs/share_wifi_spec.md:

    refresh      back-to-back presents with vsync: the panel's refresh period
    write        the same without vsync: what writing one frame costs
    breakdown    the signalling and status screens' frames, each part timed
    reads        loops that never present: how fast a static screen can listen
    alloc        what allocating a 1-, 2- and 3-block tuple costs
    gc           what one collection costs, and frames with it on and off

Pure Python, like measure.py: no `screen`, no `badge`, no `display`. Every
part that touches the badge is a callable handed in through `Hooks`, so the
sequencing and the arithmetic can be tested under CPython.

The app calls `step()` once a frame. Each call runs one step to completion and
blocks while it does -- up to about 12 s for the gc step -- which is the point:
the probe has to own the loop to time it.
"""

import time

TOOL_PROBE = "frame_probe"      # its key in the measurements file

STEPS = ("refresh", "write", "breakdown", "reads", "alloc", "gc")
STEP_TITLES = {
    "refresh": "Refresh rate",
    "write": "Write time",
    "breakdown": "Frame breakdown",
    "reads": "Read rate",
    "alloc": "Allocation",
    "gc": "Garbage collection",
}

PRESENTS = 60               # presents timed by the refresh and write steps
BREAKDOWN_FRAMES = 60       # frames of each screen in the breakdown
# Frames of each screen whose allocation is counted, separately and untimed:
# gc.mem_alloc() walks the whole heap's block table, which took ~110 ms a call
# on the first hardware run and swamped the frame it was measuring.
ALLOC_FRAMES = 8
GC_FRAMES = 120             # frames with the collector on, and again off
GC_COLLECTS = 3
ALLOC_CALLS = 1000
# Each loop of the reads step runs until it has made READ_CALLS calls or
# READ_MS has passed, whichever comes first.
READ_CALLS = 5000
READ_MS = 1000


class TimeStats:
    """Microsecond durations, summarised in milliseconds without allocating."""

    def __init__(self):
        self.n = 0
        self.total = 0
        self.total_sq = 0
        self.lo = 0
        self.hi = 0

    def add(self, us):
        if self.n == 0 or us < self.lo:
            self.lo = us
        if self.n == 0 or us > self.hi:
            self.hi = us
        self.n += 1
        self.total += us
        self.total_sq += us * us

    def mean_us(self):
        return self.total / self.n if self.n else 0

    def result(self):
        """{frames, mean_ms, min_ms, max_ms, sd_ms}, or an error if empty."""
        if not self.n:
            return {"error": "no frames"}
        n = self.n
        var = (n * self.total_sq - self.total * self.total) / (n * n)
        return {"frames": n, "mean_ms": ms(self.total / n), "min_ms": ms(self.lo),
                "max_ms": ms(self.hi), "sd_ms": ms(max(0.0, var) ** 0.5)}

    def part(self):
        """{mean_ms, max_ms}: one part of a frame."""
        return {"mean_ms": ms(self.mean_us()), "max_ms": ms(self.hi)}

    def rate(self):
        """{calls, per_s, mean_us, max_us, sd_us}: a loop's passes, in µs."""
        if not self.n or not self.total:
            return {"error": "no calls"}
        n = self.n
        var = (n * self.total_sq - self.total * self.total) / (n * n)
        return {"calls": n, "per_s": round(n * 1000000.0 / self.total),
                "mean_us": round(self.total / n, 1), "max_us": self.hi,
                "sd_us": round(max(0.0, var) ** 0.5, 1)}


def ms(us):
    return round(us / 1000.0, 2)


class Hooks:
    """What the probe needs from the badge, each a callable.

    present      display.update()
    set_vsync    display.set_vsync, or None where the firmware has none
    poll         badge.poll()
    clear        badge.clear(): what run() does before every update()
    screens      ((name, start, ((part, callable), ...)), ...): the frames
                 drawn in the breakdown, each part in order. `start` is called
                 before a screen's frames: it sets the display mode and begins
                 a run. The gc step draws the last screen.
    reads        ((name, callable), ...): one pass of each loop the reads step
                 times, never presenting. The first should do nothing, so the
                 loop's own cost can be taken off the others.
    temp         the chip's temperature in degrees C, or None where there is
                 no sensor: read before every step and once at the end.
    cancelled    True to stop: B pressed. Checked after every frame.
    collect, enable, disable    gc.collect, gc.enable, gc.disable
    mem_alloc, mem_free         gc.mem_alloc, gc.mem_free, or None
    """

    def __init__(self, present, set_vsync, poll, clear, screens, cancelled, collect, enable, disable, mem_alloc=None,
                 mem_free=None, ticks_us=None, reads=(), temp=None):
        self.present = present
        self.set_vsync = set_vsync
        self.poll = poll
        self.clear = clear
        self.screens = screens
        self.cancelled = cancelled
        self.collect = collect
        self.enable = enable
        self.disable = disable
        self.mem_alloc = mem_alloc
        self.mem_free = mem_free
        self.ticks_us = ticks_us or time.ticks_us
        self.reads = reads
        self.temp = temp


class FrameProbe:
    def __init__(self, hooks, info=None):
        self.hooks = hooks
        self.index = 0
        self.cancelled = False
        self.out = {}
        self.temps = {}
        if info:
            self.out["firmware"] = info

    @property
    def done(self):
        return self.cancelled or self.index >= len(STEPS)

    @property
    def step_name(self):
        return STEPS[self.index] if self.index < len(STEPS) else None

    def step(self):
        """Run the next step. Blocks until it is finished or cancelled."""
        if self.done:
            return
        name = STEPS[self.index]
        if self.hooks.temp is not None:
            self.temps[name] = self.hooks.temp()
        # A firmware without something a step uses fails that step, not the
        # probe: under run() an exception is the error screen and a reset.
        try:
            if name == "refresh":
                result = self.refresh()
            elif name == "write":
                result = self.write()
            elif name == "breakdown":
                result = self.breakdown()
            elif name == "reads":
                result = self.read_rates()
            elif name == "alloc":
                result = alloc_costs(self.hooks.ticks_us)
            else:
                result = self.gc()
        except Exception as error:     # noqa: BLE001
            result = {"error": "%s: %s" % (type(error).__name__, error)}
        self.out[name] = result
        self.index += 1

    def results(self):
        """Every step's result, and the chip's temperature before each step
        and now: {"temperature_c": {<step>: C, ..., "end": C}}."""
        if self.hooks.temp is None:
            self.out["temperature_c"] = {"error": "no temperature sensor"}
        else:
            temps = {}
            for key in self.temps:
                temps[key] = self.temps[key]
            temps["end"] = self.hooks.temp()
            self.out["temperature_c"] = temps
        return self.out

    # -- refresh and write

    def presents(self):
        """PRESENTS intervals between back-to-back presents, after one to align."""
        hooks = self.hooks
        ticks_us = hooks.ticks_us
        stats = TimeStats()
        hooks.present()
        last = ticks_us()
        for _ in range(PRESENTS):
            hooks.present()
            now = ticks_us()
            stats.add(time.ticks_diff(now, last))
            last = now
        return stats

    def refresh(self):
        stats = self.presents()
        out = stats.result()
        mean = stats.mean_us()
        if mean:
            out["hz"] = round(1000000.0 / mean, 1)
        return out

    def write(self):
        set_vsync = self.hooks.set_vsync
        if set_vsync is None:
            return {"error": "no set_vsync"}
        set_vsync(False)
        try:
            return self.presents().result()
        finally:
            set_vsync(True)

    # -- breakdown

    def frames(self, parts, count, stats, frame_stats):
        """`count` frames the way run() draws them, each part timed.

        `stats` holds one TimeStats per part, then poll, clear and present;
        `frame_stats` the time from one frame's start to the next's.
        """
        hooks = self.hooks
        ticks_us = hooks.ticks_us
        n = len(parts)
        start = ticks_us()
        for _ in range(count):
            t = ticks_us()
            hooks.clear()
            now = ticks_us()
            stats[n + 1].add(time.ticks_diff(now, t))
            for i in range(n):
                t = now
                parts[i][1]()
                now = ticks_us()
                stats[i].add(time.ticks_diff(now, t))
            t = ticks_us()
            hooks.present()
            now = ticks_us()
            stats[n + 2].add(time.ticks_diff(now, t))
            hooks.poll()
            t = ticks_us()
            stats[n].add(time.ticks_diff(t, now))
            frame_stats.add(time.ticks_diff(t, start))
            start = t
            if hooks.cancelled():
                self.cancelled = True
                return False
        return True

    def allocation(self, parts):
        """Mean heap growth per frame over ALLOC_FRAMES frames, untimed.

        None where the firmware has no gc.mem_alloc, or if every frame had a
        collection in it.
        """
        hooks = self.hooks
        mem_alloc = hooks.mem_alloc
        if not mem_alloc:
            return None
        grown = TimeStats()
        for _ in range(ALLOC_FRAMES):
            hooks.clear()
            before = mem_alloc()
            for _name, part in parts:
                part()
            grew = mem_alloc() - before
            if grew >= 0:              # a collection in between: no reading
                grown.add(grew)
            hooks.present()
            hooks.poll()
            if hooks.cancelled():
                self.cancelled = True
                break
        return round(grown.mean_us()) if grown.n else None

    def screen_frames(self, start, parts, count):
        stats = [TimeStats() for _ in range(len(parts) + 3)]
        frame_stats = TimeStats()
        start()
        finished = self.frames(parts, count, stats, frame_stats)
        return stats, frame_stats, finished

    def breakdown(self):
        out = {}
        for name, start, parts in self.hooks.screens:
            stats, frame_stats, finished = self.screen_frames(
                start, parts, BREAKDOWN_FRAMES)
            entry = {"frame": frame_stats.result()}
            for i in range(len(parts)):
                entry[parts[i][0]] = stats[i].part()
            n = len(parts)
            entry["poll"] = stats[n].part()
            entry["clear"] = stats[n + 1].part()
            entry["present"] = stats[n + 2].part()
            out[name] = entry
            if not finished:
                break
            grown = self.allocation(parts)
            if grown is not None:
                entry["alloc_bytes"] = grown
            if self.cancelled:
                break
        return out

    # -- reads

    def read_rates(self):
        """Each of hooks.reads looped with nothing presented, as listening
        on a static screen would be: run() presents every frame, and the
        present blocks reading for most of one."""
        hooks = self.hooks
        if not hooks.reads:
            return {"error": "no reads"}
        out = {}
        for name, call in hooks.reads:
            out[name] = loop_rate(call, hooks.ticks_us)
            if hooks.cancelled():
                self.cancelled = True
                break
        return out

    # -- gc

    def gc(self):
        hooks = self.hooks
        ticks_us = hooks.ticks_us
        out = {}
        collects = TimeStats()
        for _ in range(GC_COLLECTS):
            t = ticks_us()
            hooks.collect()
            collects.add(time.ticks_diff(ticks_us(), t))
        out["collect"] = collects.part()
        if hooks.mem_alloc and hooks.mem_free:
            out["alloc_kb"] = hooks.mem_alloc() // 1024
            out["free_kb"] = hooks.mem_free() // 1024
        out["screen"], start, parts = hooks.screens[-1]
        _, on, finished = self.screen_frames(start, parts, GC_FRAMES)
        out["frames_on"] = on.result()
        if not finished:
            return out
        hooks.disable()
        try:
            _, off, _ = self.screen_frames(start, parts, GC_FRAMES)
        finally:
            hooks.enable()
        out["frames_off"] = off.result()
        hooks.collect()
        return out


# -- reads -------------------------------------------------------------------

def loop_rate(call, ticks_us):
    """`call` back to back for READ_CALLS calls or READ_MS, timing each pass.

    A pass includes the clock read and the bookkeeping, as any loop with a
    deadline pays; the do-nothing loop measures that, to take off the rest.
    """
    stats = TimeStats()
    window = READ_MS * 1000
    start = ticks_us()
    last = start
    for _ in range(READ_CALLS):
        call()
        now = ticks_us()
        stats.add(time.ticks_diff(now, last))
        last = now
        if time.ticks_diff(now, start) >= window:
            break
    return stats.rate()


# -- allocation --------------------------------------------------------------
# A tuple is a 4-byte type, a 4-byte length and 4 bytes an item, so with the
# default 16-byte GC blocks a pair is one block, four items two and eight
# three. Firmware with 32-byte blocks fits the four in one. Built from
# arguments, not literals: a literal tuple of constants is made once, at
# compile time, and allocates nothing. All four take four arguments, so the
# call costs the same and subtracts out.

def _none(_a, _b, _c, _d):
    return None


def _pair(a, b, _c, _d):
    return (a, b)


def _quad(a, b, c, d):
    return (a, b, c, d)


def _octet(a, b, c, d):
    return (a, b, c, d, a, b, c, d)


ALLOCATIONS = (("call_us", _none), ("pair_us", _pair), ("quad_us", _quad),
               ("octet_us", _octet))


def alloc_costs(ticks_us):
    """Microseconds per call of each maker, less the bare call for the tuples."""
    out = {"calls": ALLOC_CALLS}
    call = 0.0
    for key, maker in ALLOCATIONS:
        t = ticks_us()
        for i in range(ALLOC_CALLS):
            maker(i, i, i, i)
        each = time.ticks_diff(ticks_us(), t) / ALLOC_CALLS
        if key == "call_us":
            call = each
            out[key] = round(each, 2)
        else:
            out[key] = round(max(0.0, each - call), 2)
    return out
