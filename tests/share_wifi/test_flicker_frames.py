"""The flicker filter's per-sample calls allocate nothing.

MicroPython runs a Python function's frame -- its locals and its stack --
on the C stack only while it is at most 11 words; a larger one is allocated
on the heap and freed again, every call (VM_MAX_STATE_ON_STACK in
py/objfun.c; MICROPY_ENABLE_PYSTACK off, and set by neither ports/rp2 nor
board/mpconfigboard.h). Read in the pimoroni/micropython fork at f173bb7b6,
older than both firmware pins (bw-1.27.0 for v2, bw-1.28.0-3 for v3), not
at either pin; that the pins keep it, core VM code, is [inferred-v2]
[inferred-v3], and on the badge it is [unverified] until M7 shows the
cost. On v2's 16-byte GC blocks that is a multi-block
allocation, which searches the allocation table from its first free block
at a cost set by the heap's layout. correct() went through amount() and
table(), two such frames a call, and cost 0.43-0.73 ms on the badge against
0.16-0.20 for amount() timed after it [measured-v2, M7 2026-10-07a to -09d].

So the fitted path -- correct(), amount(), track(), collect() -- keeps
every frame on the stack, and keeps the table for the span in use rather
than looking it up each call. The first test counts the bytes each call
allocates under MicroPython's unix port, whose frame rule is taken to be
the badge's (inferred, as above);
the rest pin that the cached table gives what the uncached lookup gave.
"""

import math
import os
import shutil
import subprocess
import tempfile

from harness import APPS, Skipped, load_module

P = load_module("share_wifi", "protocol")

UNIX_SCRIPT = """
import sys, math, micropython
sys.path.insert(0, %(app)r)
import protocol as p

def samples(t0, n):
    ts = []; vs = []; sp = []
    for k in range(n):
        t = t0 + k * 2000
        x = 2 * math.pi * 120 * (t %% 1000000) / 1e6
        ts.append(t)
        vs.append(int(500 + 40 * math.sin(x) + 10 * math.sin(2 * x + 0.5)))
        sp.append(2000 + (k %% 3) * 300)
    return ts, vs, sp

ts, vs, sp = samples(1000000, 80)
f = p.FlickerFilter()
print("fit", f.fit(ts, vs, sp))
for k in range(len(ts)):        # every span's table built once
    f.correct(ts[k], vs[k], sp[k])

def cost(name, fn):
    fn()
    before = micropython.mem_total()
    for k in range(20):
        fn(k)
    print(name, micropython.mem_total() - before)

cost("correct", lambda k=0: f.correct(ts[k], vs[k], sp[k]))
cost("amount", lambda k=0: f.amount(ts[k], sp[k]))
cost("track", lambda k=0: f.track(ts[k], vs[k], sp[k]))
cost("collect", lambda k=0: f.collect(ts[k], vs[k], sp[k]))
s = p.FlickerFilter()
s.steady = True
cost("steady", lambda k=0: s.correct(ts[k], vs[k], sp[k]))
"""


def test_fitted_calls_allocate_nothing_under_micropython():
    tool = os.environ.get("MICROPYTHON") or shutil.which("micropython")
    if not tool or not os.path.exists(tool):
        raise Skipped("no MicroPython unix port: set MICROPYTHON")
    with tempfile.TemporaryDirectory() as out:
        script = os.path.join(out, "frames.py")
        with open(script, "w") as handle:
            handle.write(UNIX_SCRIPT % {"app": os.path.join(APPS, "share_wifi")})
        result = subprocess.run([tool, "-X", "heapsize=4M", script],
                                capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.split("\n")
    assert lines[0] == "fit flicker", lines
    for line in lines[1:]:
        if line:
            name, allocated = line.split()
            assert allocated == "0", line


def room(t_us):
    cycle = 2 * math.pi * 60 * t_us / 1e6
    return 88 + 22 * math.sin(2 * cycle + 0.7) + 9 * math.sin(4 * cycle + 2.1)


def window(scale=1.0):
    ts = list(range(1000, 161000, 2000))
    vs = [int(round(88 + scale * (room(t) - 88))) for t in ts]
    return ts, vs, [2000] * len(ts)


def fitted(scale=1.0):
    f = P.FlickerFilter()
    assert f.fit(*window(scale)) == "flicker"
    return f


def uncached(f, t, span):
    flicker, _ = f.table(P._span_step(span))
    return (flicker[f.step_at(t)] + 2) // 4


def test_cached_table_follows_the_span():
    f = fitted()
    spans = (2000, 2000, 1500, 3800, 2000, 250, 0, 4000)
    for i, span in enumerate(spans * 4):
        t = 170000 + i * 1300
        assert f.amount(t, span) == uncached(f, t, span), (t, span)
        assert f.correct(t, 90, span) == max(0, 90 - uncached(f, t, span))


def test_a_refit_drops_the_cached_table():
    f = fitted()
    before = f.amount(170000, 2000)
    g = fitted(scale=2.0)
    assert f.fit(*window(2.0)) == "flicker"
    assert f.amount(170000, 2000) == g.amount(170000, 2000)
    assert f.amount(170000, 2000) != before


def test_track_uses_the_table_of_its_span():
    f = fitted()
    g = fitted()
    for i in range(16):
        t = 170000 + i * 2100
        span = 2000 if i % 2 else 3000
        f.track(t, 100, span)
        # g by hand, as track() did before it kept the table.
        flicker, slope = g.table(P._span_step(span))
        j = g.step_at(t)
        r = 4 * 100 - flicker[j]
        g.tn += 1
        g.tr += r
        g.td += slope[j]
        g.trd += r * slope[j]
        g.tdd += slope[j] * slope[j]
    assert (f.tn, f.tr, f.td, f.trd, f.tdd) == (g.tn, g.tr, g.td, g.trd, g.tdd)


def test_step_at_is_the_mains_step_and_the_shift():
    f = fitted()
    f.off_m = 2345678
    f.rate_m = -7000
    for t in (0, 999999, 1000000, 161000, 170000, 30161000, 40000000,
              (1 << 30) - 1):
        base = (t % 1000000) * f.mains // P._US_PER_STEP_HZ
        assert f.step_at(t) == (base + f.shift_at(t)) % P.FLICKER_STEPS, t
