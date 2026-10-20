"""fastadc's viper kernels against their pure twins in reader.py.

Three runs, from weakest to strongest:

- under CPython, the kernels' source run as Python (fakeviper.py): the
  logic, with wrap, short runs, ties at the threshold and too few bins;
- `mpy-cross -march=armv7emsp` over fastadc.py, where it is found (MPY_CROSS
  or on the PATH): viper's compile errors, for the badge's architecture;
- the kernels run by MicroPython's unix port, whose x64 viper emitter is
  real, over a bytearray (MICROPYTHON or on the PATH).

The last two are skipped -- and say so -- where the tool is absent. On the
badge, fastadc.Ring.verify() checks each kernel against its twin again.
"""

import os
import random
import shutil
import subprocess
import tempfile

import fakeviper
from harness import Skipped, load_module

R = load_module("share_wifi", "reader")
F = fakeviper.load_fastadc()


def ring(seed, size=256):
    rng = random.Random(seed)
    return [rng.randrange(0, 4096) for _ in range(size)]


def test_sum_matches_its_twin_across_the_wrap():
    for seed in range(20):
        buf = ring(seed)
        rng = random.Random(seed + 100)
        start = rng.randrange(0, 1000)
        n = rng.randrange(0, 300)
        assert F.ring_sum(buf, 255, start, n) == R.ref_sum(buf, 255, start, n)


def test_parts_match_their_twin():
    for seed in range(20):
        buf = ring(seed)
        rng = random.Random(seed + 200)
        start, part, n = rng.randrange(0, 1000), rng.randrange(1, 20), \
            rng.randrange(1, 12)
        got = [0] * n
        want = [0] * n
        F.ring_parts(buf, 255, start, part, n, got)
        R.ref_parts(buf, 255, start, part, n, want)
        assert got == want


def test_rise_matches_its_twin_including_ties_and_too_few_bins():
    for seed in range(40):
        rng = random.Random(seed + 300)
        buf = [rng.choice((100, 2000)) for _ in range(256)]
        bin_len = rng.randrange(1, 6)
        bins = rng.randrange(0, 30)
        step = rng.randrange(1, 4)
        thr = [rng.choice((2000 * bin_len, 1000 * bin_len, 100 * bin_len))
               for _ in range(bins // step + 2)]
        start = rng.randrange(0, 600)
        assert F.ring_rise(buf, 255, start, bin_len, bins, thr, step) == \
            R.ref_rise(buf, 255, start, bin_len, bins, thr, step)


def test_rise_needs_two_bins_in_a_row():
    buf = [0, 9, 0, 9, 9, 0, 0, 0]
    assert R.ref_rise(buf, 7, 0, 1, 8, [5], 8) == 3
    assert F.ring_rise(buf, 7, 0, 1, 8, [5], 8) == 3
    assert F.ring_rise([9] * 8, 7, 0, 1, 1, [5], 8) == -1


def test_fill_and_count():
    buf = [0] * 64
    F.ring_fill(buf, 63, 60, 8, 0xFFFF)
    assert F.ring_count(buf, 63, 0, 64, 0xFFFF) == 8
    assert buf[60:] == [0xFFFF] * 4 and buf[:4] == [0xFFFF] * 4


def _tool(env, name):
    path = os.environ.get(env) or shutil.which(name)
    return path if path and os.path.exists(path) else None


def test_fastadc_compiles_for_the_badge():
    tool = _tool("MPY_CROSS", "mpy-cross")
    if tool is None:
        raise Skipped("no mpy-cross: set MPY_CROSS")
    with tempfile.TemporaryDirectory() as out:
        result = subprocess.run(
            [tool, "-march=armv7emsp", "-o", os.path.join(out, "f.mpy"),
             os.path.join(fakeviper.APP, "fastadc.py")],
            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


UNIX_SCRIPT = r"""
import sys, uctypes, array
sys.path.insert(0, %r)
import fastadc as F
import reader as R
import random
random.seed(7)
size = 256
buf = bytearray(2 * size)
values = []
for i in range(size):
    v = random.getrandbits(12)
    buf[2 * i] = v & 255
    buf[2 * i + 1] = v >> 8
    values.append(v)
addr = uctypes.addressof(buf)
bad = 0
for k in range(50):
    start = random.getrandbits(10)
    n = random.getrandbits(8)
    if F.ring_sum(addr, size - 1, start, n) != R.ref_sum(values, size - 1, start, n):
        bad += 1
    part = 1 + random.getrandbits(4)
    count = 1 + random.getrandbits(3)
    out = array.array("i", [0] * count)
    ref = [0] * count
    F.ring_parts(addr, size - 1, start, part, count, out)
    R.ref_parts(values, size - 1, start, part, count, ref)
    if list(out) != ref:
        bad += 1
    thr = array.array("i", [2048 * part] * 40)
    if F.ring_rise(addr, size - 1, start, part, 30, thr, 1) != \
            R.ref_rise(values, size - 1, start, part, 30, thr, 1):
        bad += 1
print("bad", bad)
"""


def test_the_kernels_under_real_viper():
    tool = _tool("MICROPYTHON", "micropython")
    if tool is None:
        raise Skipped("no MicroPython unix port: set MICROPYTHON")
    with tempfile.TemporaryDirectory() as out:
        script = os.path.join(out, "k.py")
        with open(script, "w") as handle:
            handle.write(UNIX_SCRIPT % fakeviper.APP)
        result = subprocess.run([tool, script], capture_output=True,
                                text=True)
    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stdout.strip() == "bad 0", result.stdout
