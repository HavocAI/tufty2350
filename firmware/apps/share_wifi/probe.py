"""Dev Mode's frame timing probe: where a frame's time goes on this badge.

The first hardware run measured 54 ms frames where the docs expected 11 ms.
This finds out why, in eight steps, as specified under "probe frame timing"
in .agents/docs/share_wifi_spec.md:

    refresh      back-to-back presents with vsync: the panel's refresh period
    write        the same without vsync: what writing one frame costs
    breakdown    the signalling and status screens' frames, each part timed
    reads        loops that never present: how fast a static screen can listen
    fast_reads   the sensor read straight from the ADC, by viper and by DMA:
                 how fast it can be read at all, and how its noise averages
    alloc        what allocating a 1-, 2- and 3-block tuple costs
    flicker_filter  the transfer's flicker filter: a fit, a correction, a
                 phase-lock point and update, on a synthetic room
    gc           what one collection costs, and frames with it on and off

Pure Python, like measure.py: no `screen`, no `badge`, no `display`. Every
part that touches the badge is a callable handed in through `Hooks`, so the
sequencing and the arithmetic can be tested under CPython.

The app calls `step()` once a frame. Each call runs one step to completion and
blocks while it does -- up to about 12 s for the gc step -- which is the point:
the probe has to own the loop to time it.
"""

import math
import time

TOOL_PROBE = "frame_probe"      # its key in the measurements file

STEPS = ("refresh", "write", "breakdown", "reads", "fast_reads", "alloc",
         "flicker_filter", "gc")
STEP_TITLES = {
    "refresh": "Refresh rate",
    "write": "Write time",
    "breakdown": "Frame breakdown",
    "reads": "Read rate",
    "fast_reads": "Fast reads",
    "alloc": "Allocation",
    "flicker_filter": "Flicker filter",
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
# Each capture of the fast_reads step: this many raw 12-bit readings, and the
# group sizes whose means' spread says how far averaging beats the noise --
# by sqrt(n) if the noise is independent from reading to reading. A raw
# reading is a sixteenth of a light_level() count.
FAST_READS = 4096
AVERAGES = (1, 4, 16, 64, 256)
COUNTS_PER_RAW = 16
# The flicker_filter step's room: 2 ms means one every 3 ms, as a transfer
# keeps them, of a light at FILTER_MEAN counts flickering FILTER_FLICKER at
# 120 Hz and FILTER_HARMONIC at 240, with noise of about FILTER_NOISE --
# the shape of the 2026-10-06 room that flickered 12.5 counts. Leader's
# window 200 ms, the Follower's 100: FILTER_SAMPLES and half of it.
FILTER_EVERY_US = 3000
FILTER_SPAN_US = 2000
FILTER_SAMPLES = 66
FILTER_MEAN = 700
FILTER_FLICKER = 12
FILTER_HARMONIC = 4
FILTER_NOISE = 4
FILTER_FITS = 3             # fits timed after the first, each on a fresh filter
FILTER_CORRECTS = 500
# Phase-lock updates, each after this many points (the protocol's
# FLICKER_TRACK_POINTS): updates before FILTER_RATE_FROM_S from the fit are
# timed as `update`, those after as `update_rate` -- by then the rate's line
# spans the protocol's FLICKER_RATE_MIN_S and every update fits it.
FILTER_TRACK_POINTS = 8
FILTER_UPDATES = 48
FILTER_RATE_BEFORE_S = 0.3
FILTER_RATE_FROM_S = 0.7


class TimeStats:
    """Microsecond durations, summarised in milliseconds.

    The sum of squares is a float: as an int it passes 2**30 -- a small
    int's limit on the badge -- within a second of 700 us passes, and from
    then on every add is big-int arithmetic and allocation, inside the very
    pass being timed (2026-10-06a: `reads.transfer` at 705 us, sd 502). A
    float costs one small allocation an add, always."""

    def __init__(self):
        self.n = 0
        self.total = 0
        self.total_sq = 0.0
        self.lo = 0
        self.hi = 0

    def add(self, us):
        if self.n == 0 or us < self.lo:
            self.lo = us
        if self.n == 0 or us > self.hi:
            self.hi = us
        self.n += 1
        self.total += us
        self.total_sq += float(us) * us

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


def _error(error):
    return {"error": "%s: %s" % (type(error).__name__, error)}


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
    reads_detail what the reads loops found out besides their rates -- a dict,
                 stored as reads.detail -- or None.
    fast         ((name, capture), ...): the fast_reads step's captures, each
                 a callable giving (FAST_READS raw readings, the us they took)
                 or raising where the firmware cannot.
    temp         the chip's temperature in degrees C, or None where there is
                 no sensor: read before every step and once at the end.
    flicker_filter  the protocol's FlickerFilter class, or None to skip the
                 flicker_filter step.
    cancelled    True to stop: B pressed. Checked after every frame.
    collect, enable, disable    gc.collect, gc.enable, gc.disable
    mem_alloc, mem_free         gc.mem_alloc, gc.mem_free, or None
    """

    def __init__(self, present, set_vsync, poll, clear, screens, cancelled, collect, enable, disable, mem_alloc=None,
                 mem_free=None, ticks_us=None, reads=(), temp=None, fast=(),
                 reads_detail=None, flicker_filter=None):
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
        self.fast = fast
        self.reads_detail = reads_detail
        self.flicker_filter = flicker_filter


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
        # Each step is the method of its name.
        try:
            result = getattr(self, name)()
        except Exception as error:     # noqa: BLE001
            result = _error(error)
        self.out[name] = result
        self.index += 1

    def results(self):
        """Every step's result, and the chip's temperature before each step
        and now: {"temperature_c": {<step>: C, ..., "end": C}}."""
        if self.hooks.temp is None:
            self.out["temperature_c"] = {"error": "no temperature sensor"}
        else:
            temps = dict(self.temps)
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

    def frames(self, begin, parts, count):
        """`count` frames the way run() draws them, each part timed, after
        calling `begin`: (stats, frame_stats, finished).

        `stats` holds one TimeStats per part, then poll, clear and present;
        `frame_stats` the time from one frame's start to the next's.
        """
        hooks = self.hooks
        ticks_us = hooks.ticks_us
        n = len(parts)
        stats = [TimeStats() for _ in range(n + 3)]
        frame_stats = TimeStats()
        begin()
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
                return stats, frame_stats, False
        return stats, frame_stats, True

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

    def breakdown(self):
        out = {}
        for name, start, parts in self.hooks.screens:
            stats, frame_stats, finished = self.frames(
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

    def reads(self):
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
        if hooks.reads_detail is not None and not self.cancelled:
            try:
                out["detail"] = hooks.reads_detail()
            except Exception as error:     # noqa: BLE001
                out["detail"] = _error(error)
        return out

    # -- fast reads

    def fast_reads(self):
        """Each of hooks.fast, its readings summarised. One that raises --
        no viper, no rp2.DMA, registers not where expected -- is recorded
        and the next tried."""
        hooks = self.hooks
        if not hooks.fast:
            return {"error": "no fast reads"}
        out = {}
        for name, capture in hooks.fast:
            try:
                values, us = capture()
                out[name] = fast_stats(values, us)
            except Exception as error:     # noqa: BLE001
                out[name] = _error(error)
            if hooks.cancelled():
                self.cancelled = True
                break
        return out

    # -- alloc and the flicker filter: module functions, below

    def alloc(self):
        return alloc_costs(self.hooks.ticks_us)

    def flicker_filter(self):
        return filter_costs(self.hooks.flicker_filter, self.hooks.ticks_us)

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
        _, on, finished = self.frames(start, parts, GC_FRAMES)
        out["frames_on"] = on.result()
        if not finished:
            return out
        hooks.disable()
        try:
            _, off, _ = self.frames(start, parts, GC_FRAMES)
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


# -- fast reads --------------------------------------------------------------

def fast_stats(values, us):
    """{reads, us, per_s, mean, noise}: raw readings, summarised in
    light_level() counts. `noise` is, for each of AVERAGES, the standard
    deviation of the means of that many consecutive readings: noise that
    averages away falls by sqrt(n), and noise that does not -- the light
    itself, the sensor -- stays."""
    n = len(values)
    if not n or us <= 0:
        return {"error": "no readings"}
    total = 0
    for v in values:
        total += v
    mean = total / n
    noise = {}
    for k in AVERAGES:
        groups = n // k
        if groups < 4:
            break
        sums = 0.0
        sums_sq = 0.0
        for g in range(groups):
            part = 0
            for i in range(g * k, g * k + k):
                part += values[i]
            m = part / k
            sums += m
            sums_sq += m * m
        var = sums_sq / groups - (sums / groups) ** 2
        noise[str(k)] = round(max(0.0, var) ** 0.5 * COUNTS_PER_RAW, 2)
    return {"reads": n, "us": us, "per_s": round(n * 1000000.0 / us),
            "mean": round(mean * COUNTS_PER_RAW, 1), "noise": noise}


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


# -- the flicker filter --------------------------------------------------------

def filter_room(start_us, count):
    """`count` samples of the step's synthetic room from `start_us`: (times,
    values, spans). Noise from a fixed generator, so every run sees the same
    room; made before anything is timed."""
    ts = [0] * count
    vs = [0] * count
    spans = [FILTER_SPAN_US] * count
    # A 15-bit generator: seed x multiplier stays under 2**30, a small int on
    # the badge, so making the room allocates no big ints.
    seed = 12345
    for k in range(count):
        t = time.ticks_add(start_us, k * FILTER_EVERY_US)
        x = 2 * math.pi * 120 * (t % 1000000) / 1000000.0
        seed = (seed * 25173 + 13849) & 0x7FFF
        noise = (seed >> 4) % (4 * FILTER_NOISE + 1) - 2 * FILTER_NOISE
        ts[k] = t
        vs[k] = int(FILTER_MEAN + FILTER_FLICKER * math.sin(x)
                    + FILTER_HARMONIC * math.sin(2 * x + 0.5) + noise)
    return ts, vs, spans


def filter_costs(flicker_filter, ticks_us):
    """What FlickerFilter's work costs on this badge, in us: the first fit
    -- which also fills the protocol's gain tables, unless a fit already ran
    since launch -- then FILTER_FITS more of the Leader's window and of the
    Follower's, the first correction (which builds the table for its span),
    corrections, phase-lock points, and updates before and after the rate's
    line is in use."""
    if flicker_filter is None:
        return {"error": "no flicker filter"}
    tracked = FILTER_TRACK_POINTS * FILTER_UPDATES
    ts, vs, spans = filter_room(ticks_us(), FILTER_SAMPLES + tracked)
    fit_t = ts[:FILTER_SAMPLES]
    fit_v = vs[:FILTER_SAMPLES]
    fit_s = spans[:FILTER_SAMPLES]
    half = FILTER_SAMPLES // 2
    out = {"samples": FILTER_SAMPLES,
           "follower_samples": FILTER_SAMPLES - half}
    flicker = flicker_filter()
    t = ticks_us()
    verdict = flicker.fit(fit_t, fit_v, fit_s)
    out["fit_first_us"] = time.ticks_diff(ticks_us(), t)
    out["verdict"] = verdict
    for name, a, b, c in (("fit", fit_t, fit_v, fit_s),
                          ("fit_follower", fit_t[half:], fit_v[half:],
                           fit_s[half:])):
        stats = TimeStats()
        for _ in range(FILTER_FITS):
            fresh = flicker_filter()
            t = ticks_us()
            fresh.fit(a, b, c)
            stats.add(time.ticks_diff(ticks_us(), t))
        out[name] = stats.rate()
    if verdict != "flicker":
        return out
    # The rest on the first filter, as a transfer goes on from its fit.
    first = FILTER_SAMPLES
    t = ticks_us()
    flicker.correct(ts[first], vs[first], spans[first])
    out["correct_first_us"] = time.ticks_diff(ticks_us(), t)
    stats = TimeStats()
    for i in range(FILTER_CORRECTS):
        k = first + i % tracked
        t = ticks_us()
        flicker.correct(ts[k], vs[k], spans[k])
        stats.add(time.ticks_diff(ticks_us(), t))
    out["correct"] = stats.rate()
    track = TimeStats()
    update = TimeStats()
    update_rate = TimeStats()
    fitted_at = ts[first - 1]
    k = first
    for _ in range(FILTER_UPDATES):
        for _ in range(FILTER_TRACK_POINTS):
            t = ticks_us()
            flicker.track(ts[k], vs[k], spans[k])
            track.add(time.ticks_diff(ticks_us(), t))
            k += 1
        since = time.ticks_diff(ts[k - 1], fitted_at) / 1000000.0
        t = ticks_us()
        flicker.update()
        us = time.ticks_diff(ticks_us(), t)
        if since < FILTER_RATE_BEFORE_S:
            update.add(us)
        elif since >= FILTER_RATE_FROM_S:
            update_rate.add(us)
    out["track"] = track.rate()
    out["update"] = update.rate()
    out["update_rate"] = update_rate.rate()
    return out
