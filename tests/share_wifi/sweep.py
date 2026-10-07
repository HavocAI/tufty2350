"""Sweep the simulator over seeds, scenarios and payloads. A tool, not a test.

    python3 tests/share_wifi/sweep.py ring/v2/mirror polled/v2/lit ...

Each row is READERS/TIMING/OPTICS: READERS one of `ring`, `polled`, `mixed`
(an EXACT Leader with a POLLED Follower) or `mixedr` (the reverse); TIMING a
key of TIMING below; OPTICS a key of OPTICS. A row runs 8 seeds (SEED0 to
SEED1 in the environment) x the simulator's 6 scenarios x 3 payloads -- a
WPA2 credential, an open one with an empty key, the longest -- and prints
how many delivered, the median time, the median skew-free time and each
phase's skew-free median, the rungs runs ended on, the failures and the
invariants broken.

One seed is not evidence: AGENTS.md. 16 workers by default (WORKERS).
"""

import collections
import multiprocessing
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), HERE]

import simulator as sim                     # noqa: E402
from harness import load_module             # noqa: E402

OPEN = (b"OpenCafe", b"")
PAYLOADS = (sim.TYPICAL, OPEN, sim.WORST)
TIMING = {"v2": sim.LOOP_V2, "v2slow": sim.LOOP_V2_SLOW_PANEL,
          "v3": sim.LOOP_V3}
MEASURED = dict(sim.LOOP_SENSOR_V2, contrast=sim.SWING_MIRROR)


def _lit(**kw):
    return dict(MEASURED, room_light=48, **kw)


OPTICS = {
    "ideal": {},
    "mirror": MEASURED,
    "aligned": dict(MEASURED, contrast=sim.SWING_PEER_ALIGNED),
    "overlap": dict(MEASURED, contrast=sim.SWING_PEER_OVERLAP),
    "lit": _lit(),
    "lit30": _lit(flicker=0.30),
    "lit47": _lit(flicker=0.47),
    "led60": _lit(flicker=0.47, light="led60", mains_drift_hz=0.02),
    "led50": _lit(flicker=0.47, light="led50", mains_drift_hz=-0.02),
    "half60": _lit(flicker=0.47, light="half60", mains_drift_hz=0.02),
    "bright47": dict(MEASURED, room_light=150, flicker=0.47),
    "k1.5": dict(MEASURED, k_scale=1.5),
    "k2.0": dict(MEASURED, k_scale=2.0),
    "k3.0": dict(MEASURED, k_scale=3.0),
    "slowsensor": dict(MEASURED, k_scale=2.0),
    "noisy": dict(MEASURED, sensor_sd=9.0, ring_sd=12.0),
    "slowread": dict(MEASURED, read_ms=1.0),
    "veryslowread": dict(MEASURED, read_ms=2.0),
    "worst": dict(MEASURED, read_ms=1.0, k_scale=2.0, sensor_sd=8.0,
                  ring_sd=12.0),
    "work3": dict(MEASURED, work_scale=3.0),
    "fit300": dict(MEASURED, fit_ms=300.0),
    "ppm60": dict(MEASURED, ppm=(60.0, -60.0)),
    # Each badge's own screen reaching its own sensor, 5% of its output
    # [unmeasured], in the lit 47% room where the Leader tracks the flicker
    # through its own block.
    "self5": _lit(flicker=0.47, self_coupling=0.05),
    "lit47slow": _lit(flicker=0.47, read_ms=2.0),
}
READERS = {"ring": ("ring", "ring"), "polled": ("polled", "polled"),
           "mixed": ("ring", "polled"), "mixedr": ("polled", "ring")}


def job(args):
    row, scenario, seed, payload = args
    readers, timing, optics = row.split("/")
    protocol = load_module("share_wifi", "protocol")
    # PATCH="NAME=value,...": protocol constants to try, set every job (a
    # worker reuses the module).
    for item in filter(None, os.environ.get("PATCH", "").split(",")):
        name, value = item.split("=")
        setattr(protocol, name, int(value))
    kw = dict(sim.SCENARIOS[scenario])
    kw.update(TIMING[timing])
    kw.update(OPTICS[optics])
    kw["reader"], kw["peer_reader"] = READERS[readers]
    link = sim.build_loop(protocol, payload, seed=seed, **kw)
    total = link.run(300)
    leader = link.leader.transfer
    follower = link.follower.transfer
    ok = (leader.result == protocol.OK
          and protocol.parse_payload(follower.received) == list(payload))
    firsts = link.phase_times()
    skew = kw.get("start_skew_ms", 0) / 1000.0
    cal = firsts.get(protocol.PHASE_CAL_SEND)
    data = firsts.get(protocol.PHASE_DATA)
    phases = None
    if cal is not None and data is not None:
        phases = (cal - skew, data - cal, total - data)
    return (row, ok, protocol.SLOT_LADDER[leader.clock.index], total,
            total - skew, phases, (leader.result, follower.result),
            (link.violation or "")[:3])


def median(values):
    values = sorted(values)
    return values[len(values) // 2] if values else 0.0


def main(rows):
    seeds = range(int(os.environ.get("SEED0", 1)),
                  int(os.environ.get("SEED1", 9)))
    jobs = [(row, sc, seed, pl) for row in rows for sc in sim.SCENARIOS
            for seed in seeds for pl in PAYLOADS]
    with multiprocessing.Pool(int(os.environ.get("WORKERS", 16))) as pool:
        results = pool.map(job, jobs, chunksize=1)
    by_row = collections.OrderedDict((row, []) for row in rows)
    for result in results:
        by_row[result[0]].append(result)
    for row, runs in by_row.items():
        good = [r for r in runs if r[1]]
        phases = [r[5] for r in good if r[5]]
        rungs = collections.Counter(r[2] for r in runs)
        fails = collections.Counter(r[6] for r in runs if not r[1])
        broken = collections.Counter(r[7] for r in runs if r[7])
        print("%-24s %3d/%d median %.1fs skew-free %.1fs "
              "(discovery %.2f cal %.2f data %.2f) ended %s fails %s viol %s"
              % (row, len(good), len(runs), median(r[3] for r in good),
                 median(r[4] for r in good),
                 median(p[0] for p in phases), median(p[1] for p in phases),
                 median(p[2] for p in phases), dict(rungs), dict(fails),
                 dict(broken)))
        sys.stdout.flush()


if __name__ == "__main__":
    main(sys.argv[1:])
