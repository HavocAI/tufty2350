"""Dev Mode's frame timing probe: what the badge's own work costs it.

The first hardware run measured 54 ms frames where the docs expected 11 ms;
this found why, and now times what a transfer's reading and work cost, in
seven steps, as specified under "probe frame timing" in
.agents/share_wifi/app-spec.md:

    refresh      back-to-back presents with vsync: the panel's refresh period
    write        the same without vsync: what writing one frame costs
    reads        an empty timed loop: the interpreter's and the timing's cost
    ring         the transfer's listener through the DMA ring (M3)
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


STEPS = ("refresh", "write", "reads", "ring", "alloc", "flicker_filter", "gc")
STEP_TITLES = {
    "refresh": "Refresh rate",
    "write": "Write time",
    "reads": "Loop rate",
    "ring": "DMA ring",
    "alloc": "Allocation",
    "flicker_filter": "Flicker filter",
    "gc": "Garbage collection",
}

PRESENTS = 60               # presents timed by the refresh and write steps
GC_FRAMES = 120             # frames with the collector on, and again off
GC_COLLECTS = 3
ALLOC_CALLS = 1000
# The reads step's loop runs until it has made READ_CALLS calls or READ_MS
# has passed, whichever comes first.
READ_CALLS = 5000
READ_MS = 1000
# The flicker_filter step's room: 2 ms means back to back, as a transfer now
# pulls them from its reader, of a light at FILTER_MEAN counts flickering
# FILTER_FLICKER at 120 Hz and FILTER_HARMONIC at 240, with noise of about
# FILTER_NOISE -- the shape of the 2026-10-06 room that flickered 12.5
# counts. The Leader's window, 160 ms, is FILTER_SAMPLES; the Follower's,
# 100 ms, FILTER_FOLLOWER_SAMPLES (M7: a fit on 50 two-ms samples).
FILTER_EVERY_US = 2000
FILTER_SPAN_US = 2000
FILTER_SAMPLES = 80
FILTER_FOLLOWER_SAMPLES = 50
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
FILTER_UPDATES = 64
FILTER_RATE_BEFORE_S = 0.3
FILTER_RATE_FROM_S = 0.7
# The steadiness check that stands in for a fit in a steady room
# (FlickerFilter.steady_window()), on the windows a transfer checks: the
# Leader's, the protocol's FLICKER_FAST_FIT_MS (100 ms) of 2 ms means, and
# the Follower's, FOLLOWER_CHECK_MS (60 ms). Its cost does not depend on
# the verdict: two passes over the window either way.
STEADY_LEADER_SAMPLES = 50
STEADY_FOLLOWER_SAMPLES = 30
STEADY_CHECKS = 10


class TimeStats:
    """Microsecond durations, summarised in milliseconds.

    add() allocates nothing: it runs inside the pass being timed. The sum of
    squares is two small ints, `sq_hi` in units of 2**28 and `sq_lo` kept
    under 2**28 by carrying. As one int it passed 2**30 -- a small int's
    limit on the badge -- within a second of 700 us passes, and from then
    on every add was big-int arithmetic (2026-10-06a: `reads.transfer` at
    705 us, sd 502). As a float (4b054c5) every add boxed a float on v2,
    and the probe's empty `loop` went from 41-43 us a pass to 61-63 from
    the next run on, while `call_us` and `pair_us` never moved: the probe
    timing itself [inferred-v2]; on v2's rp2 build every float is a heap
    object [source-v2]. This split version allocates nothing and its fast
    path is only ~10 opcodes over the integer one, yet the empty loop read
    70-81 us with it, 81 in a 72 KB heap (heap_probe, 2026-10-09)
    [measured-v2] -- not the heap; that it is add() rests on the timeline
    alone [inferred-v2]. Compare `reads.loop` only between runs of the
    same add()."""

    def __init__(self):
        self.n = 0
        self.total = 0
        self.sq_hi = 0
        self.sq_lo = 0
        self.lo = 0
        self.hi = 0

    def add(self, us):
        if self.n == 0 or us < self.lo:
            self.lo = us
        if self.n == 0 or us > self.hi:
            self.hi = us
        self.n += 1
        self.total += us
        if us < 0:
            us = -us
        if us < 16384:
            self.sq_lo += us * us                   # < 2**28
        else:
            # us = a * 2**14 + b: us**2 = a*a * 2**28 + 2ab * 2**14 + b*b,
            # each piece a small int for any us under about 2**28.
            a = us >> 14
            b = us & 16383
            cross = 2 * a * b
            self.sq_hi += a * a + (cross >> 14)
            self.sq_lo += b * b + ((cross & 16383) << 14)
        if self.sq_lo >= 268435456:                 # 2**28
            self.sq_hi += self.sq_lo >> 28
            self.sq_lo &= 268435455

    @property
    def total_sq(self):
        """The sum of squares, as a float: for the summary, never in add()."""
        return self.sq_hi * 268435456.0 + self.sq_lo

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
    frame        ((part, callable), ...): the probe's own status frame, each
                 part in order -- text, no signal -- which the gc step draws
                 with the collector on and off.
    cancelled    True to stop: B pressed. Checked after every frame.
    collect, enable, disable    gc.collect, gc.enable, gc.disable
    mem_alloc, mem_free         gc.mem_alloc, gc.mem_free, or None
    temp         the chip's temperature in degrees C, or None where there is
                 no sensor: read before every step and once at the end --
                 never while the ring step's DMA owns the ADC.
    ring         the ring step's measurement: called with each of RING_MODES,
                 each giving a dict -- ring_listen()'s, or {"error": why
                 there is no ring}.
    flicker_filter  the protocol's FlickerFilter class, or None to skip the
                 flicker_filter step.
    heap         a heap reading, heap_reader()'s, or None to skip the heap
                 readings at the start and in gc. mem_info() prints to the
                 USB serial too, so never while the dev link owns it.
    """

    def __init__(self, present, set_vsync, poll, clear, frame, cancelled,
                 collect, enable, disable, mem_alloc=None, mem_free=None,
                 ticks_us=None, temp=None, flicker_filter=None, ring=None,
                 heap=None):
        self.present = present
        self.set_vsync = set_vsync
        self.poll = poll
        self.clear = clear
        self.frame = frame
        self.cancelled = cancelled
        self.collect = collect
        self.enable = enable
        self.disable = disable
        self.mem_alloc = mem_alloc
        self.mem_free = mem_free
        self.ticks_us = ticks_us or time.ticks_us
        self.temp = temp
        self.flicker_filter = flicker_filter
        self.ring = ring
        self.heap = heap


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
        if self.index == 0 and "start" not in self.out:
            # The heap as the probe found it: against the gc step's reading,
            # what the probe's own steps leave live.
            self.out["start"] = settle(self.hooks.collect,
                                       self.hooks.ticks_us, self.hooks.heap)
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

    # -- frames: the gc step's

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

    # -- reads

    def reads(self):
        """An empty loop, timed pass by pass with nothing presented: the
        interpreter's own pace, and what timing a pass costs -- to take off
        any loop timed the same way."""
        return {"loop": loop_rate(_idle, self.hooks.ticks_us)}

    # -- the DMA ring (Dev Mode M3)

    def ring(self):
        """The ring under the real EXACT listener, the screen held and then
        presenting every pass: see ring_listen()."""
        hooks = self.hooks
        if hooks.ring is None:
            return {"error": "no ring hook"}
        out = {}
        # quiet: the screen held; presenting: the status frame every pass;
        # fitted: held, with a fitted flicker filter, the slot's real cost
        # in a flickering room -- its peer dark, every slot LOW and tracked,
        # an upper bound; fitted_lit: the same with the room read as the
        # middle level, no slot tracked, the lower bound.
        for name in RING_MODES:
            try:
                out[name] = hooks.ring(name)
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
        # The heap as the step begins: whether allocation is what the
        # times below are paying for.
        free = self.hooks.mem_free() if self.hooks.mem_free else None
        out = filter_costs(self.hooks.flicker_filter, self.hooks.ticks_us)
        if free is not None:
            out["mem_free_start"] = free
        return out

    # -- gc

    def gc(self):
        hooks = self.hooks
        ticks_us = hooks.ticks_us
        out = {}
        collects = TimeStats()
        # Untimed first: the flicker_filter step's garbage, which the first
        # timed collection swept until 2026-10-10.
        hooks.collect()
        for _ in range(GC_COLLECTS):
            t = ticks_us()
            hooks.collect()
            collects.add(time.ticks_diff(ticks_us(), t))
        out["collect"] = collects.part()
        if hooks.mem_alloc and hooks.mem_free:
            out["alloc_kb"] = hooks.mem_alloc() // 1024
            out["free_kb"] = hooks.mem_free() // 1024
        heap = heap_reading(hooks.heap)
        if heap is not None:
            out["heap"] = heap
            hooks.collect()             # the reading's own garbage
        out["screen"] = "status"
        parts = hooks.frame
        _, on, finished = self.frames(_idle, parts, GC_FRAMES)
        out["frames_on"] = on.result()
        if not finished:
            return out
        hooks.disable()
        try:
            _, off, _ = self.frames(_idle, parts, GC_FRAMES)
        finally:
            hooks.enable()
        out["frames_off"] = off.result()
        hooks.collect()
        return out


# -- the heap ----------------------------------------------------------------
#
# Whether something slows every later collection. A collection sweeps the
# block table up to the collector's high-water mark, which every allocation
# raises to its end and each sweep lowers to the highest block still live
# (py/gc.c, read in the pimoroni fork at f173bb7b6 [source], kept by v2
# [inferred-v2]): so its cost can follow where live blocks sit, which bytes
# in use cannot show. See section 2 of .agents/optical_data_transfer/data.md.

# micropython.mem_info()'s GC lines, by the keys it prints, as saved.
MEM_INFO_KEYS = (("total", "total"), ("used", "used"), ("free", "free"),
                 ("1-blocks", "blocks_1"), ("2-blocks", "blocks_2"),
                 ("max blk sz", "max_block"), ("max free sz", "max_free"))


def parse_mem_info(text):
    """The GC figures in micropython.mem_info()'s output, by MEM_INFO_KEYS,
    or {} if there are none. Its lines look like

        GC: total: 8126464, used: 470000, free: 7656464
         No. of 1-blocks: 5000, 2-blocks: 900, max blk sz: 4096, max free sz: 4

    with more fields on some builds; anything not a number is skipped.
    `total`, `used` and `free` are bytes; the rest are blocks."""
    out = {}
    if not text:
        return out
    for part in text.replace("\n", ",").split(","):
        if ":" not in part:
            continue
        name, _, value = part.rpartition(":")
        name = name.strip()
        if name.startswith("GC:"):
            name = name[3:].strip()
        if name.startswith("No. of"):
            name = name[6:].strip()
        value = value.strip()
        for key, saved in MEM_INFO_KEYS:
            if name == key and value.isdigit():
                out[saved] = int(value)
    return out


def block_bytes(set_framerate):
    """The GC block in bytes, by firmware: 16 on v2 [source-v2], 32 on v3
    since 2026-07-31 [source-v3]. v3 is told by display.set_framerate,
    absent on the badge's v2 [measured-v2], present in every v3 release
    (added 2026-06-30) [source-v3]."""
    return 32 if set_framerate else 16


def heap_layout(text, block_bytes):
    """parse_mem_info(), `block_bytes`, and `top_kb`: the heap's size less
    its largest free run (`max_free` blocks of `block_bytes`), in KiB. The
    run above the highest live block is at most the largest, so live
    blocks reach at least this far up -- exactly this far when the largest
    run is the top one, as in a heap mostly free. Read just after a
    collection, it is where that sweep left the high-water mark, or below
    it. {} if nothing parsed."""
    out = parse_mem_info(text)
    if not out:
        return out
    out["block_bytes"] = block_bytes
    if "total" in out and "max_free" in out:
        top = out["total"] - out["max_free"] * block_bytes
        out["top_kb"] = max(0, top) // 1024
    return out


# The heap's end: rp2's main.c gives the GC the PSRAM alone,
# gc_init(PSRAM_BASE, PSRAM_BASE + MICROPY_HW_PSRAM_MAX_HEAP_SIZE), 8 MB, with
# MICROPY_GC_SPLIT_HEAP 0 in the board's config [source-v2] [source-v3];
# py/gc.c puts the pool at the area's end, its tables below it. So the pool
# is [PSRAM_HEAP_END - total, PSRAM_HEAP_END).
PSRAM_HEAP_END = 0x11000000 + 8 * 1024 * 1024


def heap_extent(layout, addressof, heap_end=PSRAM_HEAP_END, alloc=bytearray,
                ident=id):
    """Where the largest free run sits, and so how far up live blocks reach:
    the extent a collection sweeps (py/gc.c sweeps to gc_last_used_block,
    which a sweep leaves at the highest live block [source-v2]). `top_kb`
    is only a floor for it.

    Allocates the run less one block -- first fit lands it there, since no
    other run is as long; the one block spare is for the bytearray's own
    header, allocated first (objarray.c's array_new), which takes the run's
    first block only if that is the lowest free one -- reads both
    addresses (MicroPython's id() is the object's) and drops it. Zeroes
    ~8 MB of PSRAM, and leaves gc_last_used_block at the heap's top until
    the next collection: so never in a timed path, and collect after. In
    KiB from the pool's start:

        run_start_kb, run_end_kb  the run, to a block
        extent_kb    the run's start when it reaches the pool's end (the
                     highest live block is just below it); else the
                     run's end, a floor: live blocks sit above the run,
                     and where the highest is cannot be read (on
                     2026-10-13 they reached ~250 KB past it)
        extent_exact whether extent_kb is the former

    {} without the figures; {"extent_error": why} if it fails."""
    size = layout.get("block_bytes")
    total = layout.get("total")
    run_blocks = layout.get("max_free")
    if not size or not total or not run_blocks or run_blocks < 2:
        return {}
    start = heap_end - total
    try:
        buf = alloc((run_blocks - 1) * size)
        address = addressof(buf)
        header = ident(buf)
        buf = None
    except Exception as error:     # noqa: BLE001 - MemoryError above all
        return {"extent_error": "%s: %s" % (type(error).__name__, error)}
    if not start <= address < heap_end:
        return {"extent_error": "run at 0x%08x, outside the pool" % address}
    blocks = total // size
    first = (address - start) // size
    # The header just below the buffer was in the run: a free block there
    # would have joined it.
    if header == address - size:
        first -= 1
    reaches_top = first + run_blocks >= blocks
    out = {"run_start_kb": first * size // 1024,
           "run_end_kb": min(blocks, first + run_blocks) * size // 1024,
           "extent_exact": reaches_top}
    out["extent_kb"] = out["run_start_kb"] if reaches_top \
        else out["run_end_kb"]
    return out


def heap_reader(dupterm, mem_info, base, block_bytes, addressof=None,
                heap_end=PSRAM_HEAP_END):
    """The heap hook: a callable giving heap_layout() of what mem_info()
    prints, caught by capture_print(), and heap_extent()'s figures when
    `addressof` is given; None where the firmware lacks dupterm or
    mem_info. The app hands in os.dupterm, micropython.mem_info, io.IOBase,
    block_bytes()'s answer and uctypes.addressof."""
    if dupterm is None or mem_info is None or base is None:
        return None

    def read():
        out = heap_layout(capture_print(mem_info, dupterm, base),
                          block_bytes)
        if out and addressof is not None:
            out.update(heap_extent(out, addressof, heap_end))
        return out
    return read


def heap_reading(heap):
    """What the `heap` hook reads; None without the hook. A walk of the
    whole table (~110 ms on v2 [measured-v2]): never timed."""
    if heap is None:
        return None
    try:
        return heap() or {"error": "nothing parsed"}
    except Exception as error:     # noqa: BLE001 - recorded, not fatal
        return _error(error)


def capture_print(call, dupterm, base):
    """What `call` prints, as text. MicroPython's mem_info() prints and
    returns nothing, so its output is caught through a dupterm stream -- a
    `base` (the firmware's io.IOBase) with readinto, write and ioctl, as
    dupterm wants; usbport.CancelTerm is the same -- and still goes to the
    USB serial too. The slot's old stream is put back even if `call`
    raises. Allocates."""

    class Capture(base):
        def __init__(self):
            self.parts = []

        def write(self, buf):
            self.parts.append(bytes(buf))
            return len(buf)

        def readinto(self, _buf):
            return None

        def ioctl(self, _request, _arg):
            return 0

    capture = Capture()
    previous = dupterm(capture, 0)
    try:
        call()
    finally:
        dupterm(previous, 0)
    return b"".join(capture.parts).decode("utf-8", "ignore")


def _collect_ms(collect, ticks_us):
    t = ticks_us()
    collect()
    return ms(time.ticks_diff(ticks_us(), t))


def settle(collect, ticks_us, heap):
    """The heap swept clean, timed and read: an untimed collection first,
    so `gc_ms` -- the next, timed -- sweeps no one else's garbage; `heap`,
    heap_reading() just after it, then an untimed collection for that
    reading's own garbage."""
    collect()
    out = {"gc_ms": _collect_ms(collect, ticks_us)}
    reading = heap_reading(heap)
    if reading is not None:
        out["heap"] = reading
        collect()
    return out


# -- reads -------------------------------------------------------------------

def _idle():
    pass


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


# -- the DMA ring ------------------------------------------------------------
# Dev Mode M3: an EXACT listener -- a Follower in the data phase on the 8 ms
# rung, reading through the ring -- run as the transfer loop runs it, for
# RING_LISTEN_MS. What it settles: the ring's real rate and whether readings
# are lost (fifo_over, faults), how far behind the protocol falls (lag_max_us)
# and what a decided slot costs on this badge (slot_work), what the uncached
# kernels cost, whether a collection mid-listen overruns the ring, and how
# much garbage a slot leaves.

RING_LISTEN_MS = 2000
RING_MODES = ("quiet", "presenting", "fitted", "fitted_lit")
# A pass is long when its own time -- the gap since the last pass, less
# the pump's wait for what is due, which is up to a slot by design -- is
# over this.
RING_LONG_PASS_US = 5000
RING_KERNEL_READS = 1000


def ring_listen(transfer, ticks_us, present=None, poll=None, collect=None,
                mem_free=None, cancelled=None, ms=RING_LISTEN_MS):
    """Runs `transfer` -- listening, through its RingReader, already started
    -- for `ms`, calling `present` every pass if given. Its budgets are reset
    every pass: nobody is sending."""
    reader = transfer.reader
    ring = reader.ring
    work = TimeStats()
    per_slot = TimeStats()
    passes = TimeStats()
    slots = 0
    long_passes = 0
    # Signed: the newest reading's computed time can sit after `now` --
    # the anchor's bias -- and a lag clamped at 0 hid that (2026-10-07a).
    lag_min = lag_max = None
    gc_ms = None
    lost_at_gc = None
    # Garbage is read before the mid-run collection, over the slots
    # decided by then: read after it, the collection's frees made it
    # negative (2026-10-07a).
    free_start = mem_free() if mem_free is not None else None
    free_mid = None
    slots_mid = 0
    start_us = ticks_us()
    start_position = ring.position()
    start = reader.now_us()
    catch_up(transfer, start)
    end = start + ms * 1000
    gc_at = start + ms * 500
    last = ticks_us()
    while True:
        now = reader.now_us()
        if now >= end:
            break
        if present is not None:
            present()
        if collect is not None and gc_ms is None and now >= gc_at:
            if mem_free is not None:
                free_mid = mem_free()
                slots_mid = slots
                # Its walk is the probe's, not a transfer's: those slots
                # are skipped. The collection's are decided after it, as a
                # transfer's would be.
                catch_up(transfer, reader.now_us())
            lost_at_gc = reader.lost
            a = ticks_us()
            collect()
            gc_ms = time.ticks_diff(ticks_us(), a) / 1000.0
            # mem_free() walks the heap (~110 ms on v2) and the collection
            # is timed on its own: neither belongs to this pass's time.
            last = ticks_us()
        w = ticks_us()
        reader.pump(transfer.due_us())
        # The pump's wait is by design -- up to a slot -- not a stall.
        waited = time.ticks_diff(ticks_us(), w)
        t = reader.now_us()
        lag = t - reader.newest_us()
        if lag_max is None or lag > lag_max:
            lag_max = lag
        if lag_min is None or lag < lag_min:
            lag_min = lag
        a = ticks_us()
        transfer.field_at(t)
        transfer.work(t)
        w = time.ticks_diff(ticks_us(), a)
        work.add(w)
        if transfer.work_slots:
            slots += transfer.work_slots
            per_slot.add(w // transfer.work_slots)
        transfer.idle_slots = 0
        transfer.stall_slots = 0
        if poll is not None:
            poll()
        b = ticks_us()
        gap = time.ticks_diff(b, last)
        passes.add(gap)
        if gap - waited > RING_LONG_PASS_US:
            long_passes += 1
        last = b
        if cancelled is not None and cancelled():
            break
    us = time.ticks_diff(ticks_us(), start_us)
    readings = ring.position() - start_position
    reader.check()
    out = {"rate_hz": round(readings * 1000000.0 / us) if us > 0 else 0,
           "anchor_spread_us": reader.anchor_width,
           "fifo_over": reader.lost, "faults": reader.faults,
           "drift": reader.drift, "lag_min_us": lag_min,
           "lag_max_us": lag_max,
           "work": work.rate(), "slot_work": per_slot.rate(), "slots": slots,
           "passes": passes.rate(), "long_passes": long_passes,
           "rung_ms": transfer.clock.duration // 1000}
    position = ring.position()
    a = ticks_us()
    ring.sum(position - 2 * RING_KERNEL_READS, RING_KERNEL_READS)
    out["kernel_us_per_1000"] = time.ticks_diff(ticks_us(), a)
    if gc_ms is not None:
        out["gc_ms"] = gc_ms
        out["gc_overran"] = reader.lost > lost_at_gc
    if free_start is not None:
        out["mem_free_start"] = free_start
        if free_mid is not None and slots_mid:
            out["garbage_per_slot"] = round(
                (free_start - free_mid) / float(slots_mid), 1)
    if transfer.result is not None:
        # It stopped listening, and every figure above is of a shorter run.
        out["ended"] = transfer.result
    return out


def catch_up(transfer, now_us):
    """Starts a probe listener's decisions at `now_us`. The slots that
    closed while it was set up -- a fit, mem_free()'s walk of the heap --
    are not what the probe times, and on 2026-10-09 its first pass decided
    17 of them in one work() call: past LOST_PEER_SLOTS in a dark room, so
    the listener failed there and decided nothing more."""
    n = transfer.clock.number(now_us)
    if transfer.next < n:
        transfer.next = n


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
    half = FILTER_SAMPLES - FILTER_FOLLOWER_SAMPLES
    out = {"samples": FILTER_SAMPLES,
           "follower_samples": FILTER_FOLLOWER_SAMPLES}
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
    for name, n in (("steady_check", STEADY_LEADER_SAMPLES),
                    ("steady_check_follower", STEADY_FOLLOWER_SAMPLES)):
        stats = TimeStats()
        a = fit_t[:n]
        b = fit_v[:n]
        for _ in range(STEADY_CHECKS):
            t = ticks_us()
            flicker.steady_window(a, b)
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
    # Where correct()'s time goes (M7): each part of its call chain alone,
    # on the same samples. correct() is amount() plus a subtraction and a
    # clamp; amount() is table() and step_at() plus an index.
    k_span = next(iter(flicker.tables))
    parts = (("step_at", lambda k: flicker.step_at(ts[k])),
             ("table", lambda _k: flicker.table(k_span)),
             ("amount", lambda k: flicker.amount(ts[k], spans[k])))
    for name, call in parts:
        stats = TimeStats()
        for i in range(FILTER_CORRECTS):
            k = first + i % tracked
            t = ticks_us()
            call(k)
            stats.add(time.ticks_diff(ticks_us(), t))
        out["correct_" + name] = stats.rate()
    # And amount() on the learner's path, a filter that learnt its sines
    # from dark slots rather than fitting: what try_lock pays per step
    # when no fit was made.
    learner = flicker_filter()
    for k in range(first, first + tracked):
        learner.collect(ts[k], vs[k], spans[k])
        if (k - first) % FILTER_TRACK_POINTS == FILTER_TRACK_POINTS - 1:
            learner.close(True)
    stats = TimeStats()
    for i in range(FILTER_CORRECTS):
        k = first + i % tracked
        t = ticks_us()
        learner.amount(ts[k], spans[k])
        stats.add(time.ticks_diff(ticks_us(), t))
    out["amount_learner"] = stats.rate()
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
