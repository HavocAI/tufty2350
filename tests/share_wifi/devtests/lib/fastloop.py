"""Fast blocks on a mirror, sent and received by the code a transfer runs
(protocol spec 13.1, 14.2; .agents/tasks/optical_data_transfer/fast-blocks-data.md). Badge-only:
the DMA pacer and the ring.

A dev link library: the subtool devtests/fast_loopback.py calls run()
with the host's arguments and a subtool's api (backlight, present, temp,
tethered, ring_start/stop/note, cancelled, send, save).

Sender: as the app's fast_pacer(), fill_burst() and play_burst() -- a
fasttx.Pacer on mem32 and an rp2.DMA(), transmit() from the burst's slot
boundary -- after a Leader's slot lead-in. Receiver: a Follower Transfer
on the ring, its fast_receive() run in transmit()'s wait hook; its reply
caught, not sent. A rung's captures share one decoder: the first trains,
later ones retune.

Per capture: decoded and matched; the trigger's lateness; the first word's
landing and the rise through T_low after it (the edge lag); train() or
retune(), feed(), bins() and the rise, timed; the ring check and a
collection; DIV and CC before and after. Last, transmit() with the loop
blocked. `defer`: no decoding until the bins cover the longest burst.

White screen, drawn once; no USB or ADC while the ring runs. `fit` last:
a FlickerFilter fitted first, as the gap fits it, and every Follower
decodes with its flicker terms on. Args as the subtool's: `0 8 0 defer
fit` is F0, eight blocks, each the rung's most, deferred, fitted.
"""

import gc
import time
from array import array


PWM_SLICE = 0x400A8000 + 5 * 0x14       # fasttx's
PWM_DIV = PWM_SLICE + 0x04
PWM_CTR = PWM_SLICE + 0x08
PWM_CC = PWM_SLICE + 0x0C
PWM_TOP = PWM_SLICE + 0x10
CHUNK = 3                               # the block's chunk index: any
LEVEL_MS = 150                          # each level held to measure it
FIT_MS = 100                            # FLICKER_FAST_FIT_MS
SEED = 0x2545F491


def payload(n, seed):
    """`n` bytes: xorshift32, with a run of zeros in the middle -- a file's
    worst case for stuffing, which random bytes never test."""
    x = (SEED ^ seed) & 0xFFFFFFFF or 1
    out = bytearray(n)
    for i in range(n):
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= x >> 17
        x ^= (x << 5) & 0xFFFFFFFF
        out[i] = x & 0xFF
    zeros = n // 4
    for i in range(n // 2 - zeros // 2, n // 2 - zeros // 2 + zeros):
        out[i] = 0
    return bytes(out)


def white(api):
    screen.pen = color.rgb(255, 255, 255)       # noqa: F821 - badge global
    screen.clear()                              # noqa: F821
    api.present()
    api.present()


class Timer:
    """Wraps a callable to count its calls and time them, in us. Positional
    arguments only: `**` in a call misplaces arguments on the badge's v2
    (AGENTS.md), so a call with keywords gets its own wrapper (Begin)."""

    def __init__(self, fn):
        self.fn = fn
        self.n = 0
        self.total = 0
        self.most = 0

    def __call__(self, *args):
        t0 = time.ticks_us()
        out = self.fn(*args)
        self.add(time.ticks_diff(time.ticks_us(), t0))
        return out

    def add(self, dt):
        self.n += 1
        self.total += dt
        if dt > self.most:
            self.most = dt

    def report(self):
        return {"n": self.n, "ms": round(self.total / 1000.0, 2),
                "max_ms": round(self.most / 1000.0, 2)}


class Begin(Timer):
    """Decoder.begin(), which the Follower calls with keywords: named here,
    one by one, rather than forwarded with `**`."""

    def __call__(self, start, width, flicker=0, max_len=128, shifts=None):
        t0 = time.ticks_us()
        out = self.fn(start, width, flicker=flicker, max_len=max_len,
                      shifts=shifts)
        self.add(time.ticks_diff(time.ticks_us(), t0))
        return out


def regs(mem):
    return mem[PWM_DIV] & 0xFFF, mem[PWM_CC]


def measure_levels(api, P, reader):
    """Each data level held LEVEL_MS on the white screen, read through the
    ring over its back half: the Follower's levels, as a Begin trains them."""
    scheme = P.LEVEL_SCHEMES[P.SCHEME_BACKLIGHT]
    out = {}
    for symbol, name in ((P.LOW, "black"), (P.MID, "grey"), (P.HIGH, "white")):
        api.backlight(scheme[symbol])
        start = reader.now_us()
        reader.pump(start + LEVEL_MS * 1000)
        end = reader.newest_us()
        out[name] = reader.mean(end - LEVEL_MS * 500, end)
    api.backlight(scheme[P.LOW])
    return out


class Sender:
    """The app's fast_pacer() and fill_burst(), as a subtool can hold them."""

    def __init__(self, api, P, FB):
        import machine
        import rp2
        import fasttx
        t0 = time.ticks_us()
        self.FB = FB
        self.mem = machine.mem32
        self.freq = machine.freq()
        self.div16 = FB.common_div(self.freq)[0]
        if self.div16 is None:
            raise RuntimeError("no divider reaches the common period at %d Hz"
                               % self.freq)
        most = 0
        for periods in FB.RUNGS:
            symbols = FB.PREAMBLE_LEN + FB.TAIL + FB.stuffed_len(
                FB.HEADER + FB.block_bytes(periods) + FB.CRC)
            most = max(most, periods * symbols + 1)
        self.words = array("I", bytes(4 * most))
        self.pacer = fasttx.Pacer(self.mem, rp2.DMA())
        with self.pacer.opened(self.div16):
            self.low_cc = self.pacer.cc_for(api.backlight, P.LOW_BACKLIGHT)
            self.high_cc = self.pacer.cc_for(api.backlight, 1.0)
            self.low = self.pacer.word(self.low_cc)
            self.high = self.pacer.word(self.high_cc)
        self.setup_ms = time.ticks_diff(time.ticks_us(), t0) / 1000.0
        self.n = 0

    def fill(self, symbols, periods):
        t0 = time.ticks_us()
        self.n = self.FB.fill_words(self.words, symbols, periods, self.low,
                                    self.high)
        return time.ticks_diff(time.ticks_us(), t0) / 1000.0

    def release(self):
        self.pacer.release()


def fit_flicker(P, FB, reader):
    """A FlickerFilter fitted on FIT_MS of the ring, the backlight low,
    as the gap fits one; a steady room left unfitted."""
    t = P.Transfer(False, reader=reader, fast=FB)
    start = reader.now_us()
    reader.pump(start + FIT_MS * 1000 + 2 * P.FINE_US)
    ts, vs, spans = t.ring_samples(start, start + FIT_MS * 1000)
    f = P.FlickerFilter()
    t0 = time.ticks_us()
    verdict = "steady" if f.steady_window(ts, vs) else f.fit(ts, vs, spans)
    t.flicker = f
    return f, {"verdict": verdict, "n": len(vs), "mhz": t.fast_flicker_mhz(),
               "ms": time.ticks_diff(time.ticks_us(), t0) / 1000.0}


def follower(P, FB, reader, levels, lit, defer, dec, flicker=None):
    """A Follower Transfer on the ring, in the data phase, its levels as a
    Begin would have trained them, and `dec` its decoder if one is kept;
    and its timers."""
    t = P.Transfer(False, reader=reader, fast=FB)
    if dec is not None:
        t.decoder = dec
    t.levels = P.Levels()
    t.levels.fit(levels["black"], levels["grey"], levels["white"])
    t.lit = lit
    if flicker is not None:
        t.flicker = flicker
    t.start_data(reader.now_us() // 1000 + 2, P.EXACT_LIT_INDEX)
    d = t.decoder
    D = FB.Decoder

    def begin(start, width, flicker=0, max_len=128, shifts=None):
        return D.begin(d, start, width, flicker=flicker, max_len=max_len,
                       shifts=shifts)
    # The class's methods, not d's: a kept decoder's are last capture's
    # timers, and wrapping those nests a level a capture.
    timers = {"begin": Begin(begin), "feed": Timer(lambda b, n: D.feed(d, b, n)),
              "bins": Timer(reader.bins), "rise": Timer(t.burst_rise),
              "train": Timer(lambda b: FB.Decoder.train(d, b)),
              "retune": Timer(lambda b: FB.Decoder.retune(d, b))}
    d.train = timers["train"]
    d.retune = timers["retune"]
    t.decoder.begin = timers["begin"]
    t.decoder.feed = timers["feed"]
    if defer:
        feed = timers["feed"]

        def deferred(bins, avail):
            # NEED until the bins reach past the longest burst could end.
            most = P.burst_max_us(FB, t.fast_periods) // P.FAST_BIN_US
            if avail < P.FAST_BINS and t.fast_anchor is not None and \
                    t.fast_have < most + (t.fast_anchor - t.fast_b0) \
                    // P.FAST_BIN_US + 20:
                return FB.NEED
            return feed(bins, avail)
        t.decoder.feed = deferred
    t.burst_rise = timers["rise"]
    return t, timers


def capture(api, P, FB, reader, sender, levels, lit, rung, size, seed,
            defer, kept, flicker=None):
    """One fast block: the lead-in, the burst, the Follower decoding it."""
    periods = FB.RUNGS[rung]
    data = payload(size, seed)
    symbols = FB.encode(CHUNK, data)
    t, timers = follower(P, FB, reader, levels, lit, defer, kept.get(rung),
                         flicker)
    kept[rung] = t.decoder
    lost = reader.lost
    real_bins = reader.bins
    reader.bins = timers["bins"]
    reply = {}

    def fast_reply(ok, burst_end):
        if "ok" not in reply:
            reply["ok"] = ok
            reply["end"] = burst_end
            reply["at"] = reader.now_us()
    t.fast_reply = fast_reply

    scheme = P.LEVEL_SCHEMES[P.SCHEME_BACKLIGHT]
    lead = [P.LOW] * P.RESYNC_LEAD_IN + list(P.FAST_BEGIN) \
        + P.bits_symbols(rung, P.FAST_RUNG_BITS) + [P.LOW] * P.FAST_GAP_SLOTS
    clock = t.clock
    first = clock.number(reader.now_us()) + 2
    t.deciding = first + len(lead) - P.FAST_GAP_SLOTS - 1     # the last bit
    t.start_fast_rx(periods)
    due = t.fast_expect
    fill_ms = sender.fill(symbols, periods)
    regs_before = regs(sender.mem)
    try:
        for j in range(len(lead)):
            reader.pump(clock.start_of(first + j))
            api.backlight(scheme[lead[j]])

        def feed():
            t.now_us = reader.now_us()
            if "ok" not in reply:
                t.fast_receive(reader.newest_us())

        reader.pump(due)
        top = sender.mem[PWM_TOP] & 0xFFFF
        ctr = sender.mem[PWM_CTR] & 0xFFFF
        started = reader.now_us()
        sender.pacer.transmit(sender.div16, sender.words, sender.n, feed)
        sent = reader.now_us()
        limit = started + FB.MAX_AIR_NS // 1000 + 2000000
        while "ok" not in reply and reader.now_us() < limit:
            feed()
    finally:
        reader.bins = real_bins
        api.backlight(scheme[P.LOW])
    regs_after = regs(sender.mem)
    on_air = len(symbols) * periods * FB.COMMON_NS // 1000
    out = {"rung": rung, "periods": periods, "bytes": size, "seed": seed,
           "defer": defer, "mode": t.decoder.mode,
           "flicker_mhz": t.fast_flicker_mhz(),
           "symbols": len(symbols), "fill_ms": round(fill_ms, 2),
           "late_us": started - due, "transmit_us": sent - started,
           "on_air_us": on_air,
           "regs_restored": regs_after == regs_before,
           "replied": "ok" in reply, "ok": bool(reply.get("ok")),
           "timers": {k: v.report() for k, v in timers.items()}}
    if t.fast_anchor is not None:
        lag = P.EDGE_LAG_FAST_LIT_US if lit else P.EDGE_LAG_FAST_DARK_US
        out["rise_us"] = t.fast_anchor + lag - due      # the crossing
        out["lag_assumed_us"] = lag
        period = FB.COMMON_NS / 1000.0
        landing = started - due + (top + 1 - ctr) * period / (top + 1) \
            + period
        out["landing_us"] = int(landing)
        out["lag_us"] = int(out["rise_us"] - landing)
    if "ok" in reply:
        out["reply_after_end_us"] = reply["at"] - reply["end"]
        out["end_us"] = reply["end"] - due
    kept = t.fast_chunks.get(CHUNK)
    out["matched"] = kept == data
    t0 = time.ticks_us()
    out["check"] = reader.check()
    out["check_ms"] = time.ticks_diff(time.ticks_us(), t0) / 1000.0
    out["lost"] = reader.lost - lost
    t = None
    t0 = time.ticks_us()
    gc.collect()
    out["gc_ms"] = time.ticks_diff(time.ticks_us(), t0) / 1000.0
    return out


def blocking(FB, reader, sender):
    """The app's play_burst(): transmit() with no wait hook, the longest
    block on the slowest rung, the loop blocked throughout. Then the ring."""
    periods = FB.RUNGS[-1]
    symbols = FB.encode(CHUNK, payload(FB.block_bytes(periods), 99))
    sender.fill(symbols, periods)
    lost = reader.lost
    started = reader.now_us()
    t0 = time.ticks_us()
    sender.pacer.transmit(sender.div16, sender.words, sender.n)
    blocked = time.ticks_diff(time.ticks_us(), t0)
    after = reader.now_us()
    ok = reader.check()             # reads, and clears, the ring's health
    return {"periods": periods, "symbols": len(symbols),
            "on_air_us": len(symbols) * periods * FB.COMMON_NS // 1000,
            "blocked_us": blocked, "clock_us": after - started,
            "check": ok, "lost": reader.lost - lost,
            "drift": reader.drift}


def run(api, args):
    try:
        import protocol as P
        import fastblock as FB
    except ImportError as error:
        return {"error": "not importable: %s" % error}
    rungs = [int(v) for v in args[0].split(",")] if args else \
        list(range(len(FB.RUNGS)))
    repeats = int(args[1]) if len(args) > 1 else 3
    size = int(args[2]) if len(args) > 2 else 0
    defer = len(args) > 3 and args[3] == "defer"
    fit = len(args) > 4 and args[4] == "fit"
    for r in rungs:
        if not 0 <= r < len(FB.RUNGS):
            return {"error": "rungs: 0 to %d" % (len(FB.RUNGS) - 1)}
    repeats = max(1, min(repeats, 20))
    out = {"tethered": api.tethered(), "temp": api.temp(),
           "captures": []}
    white(api)
    api.backlight(P.LEVEL_SCHEMES[P.SCHEME_BACKLIGHT][P.LOW])
    sender = None
    reader = None
    try:
        sender = Sender(api, P, FB)
        out.update({"freq": sender.freq, "div16": sender.div16,
                    "setup_ms": round(sender.setup_ms, 2),
                    "low_cc": sender.low_cc, "high_cc": sender.high_cc,
                    "words_bytes": 4 * len(sender.words)})
        gc.collect()
        reader = api.ring_start()
        if reader is None:
            return {"error": "no ring: %s" % api.ring_note()}
        levels = measure_levels(api, P, reader)
        lit = levels["black"] >= P.LIT_BLACK_COUNTS
        out["levels"] = levels
        out["lit"] = lit
        flicker = None
        if fit:
            flicker, out["fit"] = fit_flicker(P, FB, reader)
        kept = {}       # rung -> its decoder: a transfer's blocks share one
        for rung in rungs:
            most = FB.block_bytes(FB.RUNGS[rung])
            n = min(size, most) if size else most
            for i in range(repeats):
                if api.cancelled():
                    break
                out["captures"].append(capture(api, P, FB, reader, sender,
                                               levels, lit, rung, n, i + 1,
                                               defer, kept, flicker))
        if not api.cancelled():
            out["blocking"] = blocking(FB, reader, sender)
    finally:
        if reader is not None:
            api.ring_stop()
        if sender is not None:
            sender.release()
        api.backlight(1.0)
    out["summary"] = summarise(out)
    api.send(out)
    api.save(out)
    return out["summary"]


def summarise(out):
    """Per rung: decoded and matched of captured; and every rule that
    failed. Pure: the host runs it too."""
    rungs = {}
    failed = []
    for cap in out.get("captures", []):
        key = str(cap["rung"])
        good, total = rungs.get(key, (0, 0))
        hit = cap["ok"] and cap["matched"]
        rungs[key] = (good + (1 if hit else 0), total + 1)
        if not hit:
            failed.append("rung %s seed %s: not decoded" % (key, cap["seed"]))
        if not cap["regs_restored"]:
            failed.append("rung %s seed %s: DIV or CC not put back"
                          % (key, cap["seed"]))
        if not cap["check"] or cap["lost"]:
            failed.append("rung %s seed %s: ring check failed or lost readings"
                          % (key, cap["seed"]))
    block = out.get("blocking")
    if block is not None and (not block["check"] or block["lost"]):
        failed.append("blocking burst: ring check %s, lost readings %s"
                      % (block["check"], block["lost"]))
    return {"rungs": rungs, "failed": failed}
