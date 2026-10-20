"""Sweep the simulator over seeds, scenarios and payloads. A tool, not a test.

    python3 tests/share_wifi/sweep.py v2/mirror v3/lit ...

Each row is TIMING/OPTICS: TIMING a key of TIMING below -- the firmware's
PWM period and backlight curve -- and OPTICS a key of OPTICS. Every badge
reads through the DMA ring; rows written READERS/TIMING/OPTICS before
2026-10-11 (`ring/v2/mirror`) are the same row. A row runs 8 seeds (SEED0 to
SEED1 in the environment) x the simulator's 6 scenarios x 3 payloads -- a
WPA2 credential, an open one with an empty key, the longest -- and prints
how many delivered, the median time, the median skew-free time and each
phase's skew-free median, the rungs runs ended on, the failures and the
invariants broken.

One seed is not evidence: AGENTS.md. 16 workers by default (WORKERS).
PATCH=NAME=value,... sets protocol constants; DRIFT=Hz the drifting lights'
mains drift (their sign kept).
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
TIMING = {"v2": sim.LOOP_V2, "v3": sim.LOOP_V3,
          # a v2 Leader with a v3 Follower, and the reverse
          "v2v3": sim.mixed_firmware(sim.LOOP_V2, sim.LOOP_V3),
          "v3v2": sim.mixed_firmware(sim.LOOP_V3, sim.LOOP_V2)}
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
    "half50": _lit(flicker=0.47, light="half50", mains_drift_hz=-0.02),
    "bright47": dict(MEASURED, room_light=150, flicker=0.47),
    # Swings under the mirror's 80, for MIN_GAP: HW1's 64 (gaps of 27-30
    # on the badge with the sensor partly blocked, 2026-10-09
    # [measured-all]) and dimmer, dark and lit.
    "sw48": dict(MEASURED, contrast=48),
    "sw56": dict(MEASURED, contrast=56),
    "sw64": dict(MEASURED, contrast=64),
    "litsw48": _lit(contrast=48),
    "litsw56": _lit(contrast=56),
    "litsw64": _lit(contrast=64),
    "sw56slow": dict(MEASURED, contrast=56, k_scale=2.0),
    "sw56noisy": dict(MEASURED, contrast=56, sensor_sd=9.0, ring_sd=12.0),
    "litsw56lit47": _lit(contrast=56, flicker=0.47),
    "sw56worst": dict(MEASURED, contrast=56, k_scale=2.0, sensor_sd=8.0,
                      ring_sd=12.0),
    "k1.5": dict(MEASURED, k_scale=1.5),
    "k2.0": dict(MEASURED, k_scale=2.0),
    "k3.0": dict(MEASURED, k_scale=3.0),
    "slowsensor": dict(MEASURED, k_scale=2.0),
    "noisy": dict(MEASURED, sensor_sd=9.0, ring_sd=12.0),
    "worst": dict(MEASURED, k_scale=2.0, sensor_sd=8.0, ring_sd=12.0),
    # Three times the measured work: 7.5 ms a decided slot (up to 11.3 with
    # the simulator's jitter) of a dark 16 ms slot, past the 3.2 ms most the
    # badge measured. A badge's work runs on after the boundary it must
    # transmit at, so its symbols go out that late; the dark window opening
    # at 6.4 ms rather than 10 (SETTLE_DARK_US) leaves less room for that,
    # 66 of 144 then 50. A stress past any plausible badge, not a target.
    "work3": dict(MEASURED, work_scale=3.0),
    "fit300": dict(MEASURED, fit_ms=300.0),
    "ppm60": dict(MEASURED, ppm=(60.0, -60.0)),
    # Each badge's own screen reaching its own sensor, 5% of its output
    # [unmeasured], in the lit 47% room where the Leader tracks the flicker
    # through its own block.
    "self5": _lit(flicker=0.47, self_coupling=0.05),
    # Room 5 (2026-10-07a M6), scaled to the light a hold lets in: 120 Hz
    # at 46% of the light and 240 Hz at 0.58 of that, harmonics 2 and 4 of
    # 60 Hz mains, drifting as measured (-14 mHz at the mains). Scaled by
    # the lit mirror hold's 80 counts against room 5's 3637 facing the
    # lamp -- a light leaking past the hold scales with the room, so this
    # likely understates it [inferred-all]. Four harmonics, as fitted now.
    "room5hold": _lit(flicker=0.46, light="room5", mains_drift_hz=-0.014),
    # The same with a 6th harmonic (360 Hz), and both at two and four times
    # the room light, for a hold that lets in more (bright47's way).
    "room5hold6": _lit(flicker=0.46, light="room5x6", mains_drift_hz=-0.014),
    "room5x2": dict(MEASURED, room_light=96, flicker=0.46, light="room5",
                    mains_drift_hz=-0.014),
    "room5x4": dict(MEASURED, room_light=192, flicker=0.46, light="room5",
                    mains_drift_hz=-0.014),
    "room5x6x2": dict(MEASURED, room_light=96, flicker=0.46, light="room5x6",
                      mains_drift_hz=-0.014),
    "room5x6x4": dict(MEASURED, room_light=192, flicker=0.46,
                      light="room5x6", mains_drift_hz=-0.014),
    # Collections as long as 2026-10-08 and -09's, 65-81 ms -- measured
    # inside an open dev link session -- against the default's 25-46. Since
    # 2026-10-09f's 52 ms outside any session, the upper bound of what a
    # transfer may meet, not only a stress row. See GC_MS.
    "gc70": dict(MEASURED, gc_ms=sim.GC_MS_HW1),
    "litgc70": _lit(gc_ms=sim.GC_MS_HW1),
    "lit47gc70": _lit(flicker=0.47, gc_ms=sim.GC_MS_HW1),
    "half60gc70": _lit(flicker=0.47, light="half60", mains_drift_hz=0.02,
                       gc_ms=sim.GC_MS_HW1),
}


def job(args):
    row, scenario, seed, payload = args
    parts = row.split("/")
    if len(parts) == 3:
        assert parts[0] == "ring", "every badge reads through the ring"
    timing, optics = parts[-2:]
    protocol = load_module("share_wifi", "protocol")
    # PATCH="NAME=value,...": protocol constants to try, set every job (a
    # worker reuses the module).
    for item in filter(None, os.environ.get("PATCH", "").split(",")):
        name, value = item.split("=")
        setattr(protocol, name, int(value))
    kw = dict(sim.SCENARIOS[scenario])
    kw.update(TIMING[timing])
    kw.update(OPTICS[optics])
    # DRIFT=Hz: every drifting light's mains drift at this size, its sign
    # kept -- 0.01 is 2026-10-14's measured worst against the badge's clock.
    if os.environ.get("DRIFT") and kw.get("mains_drift_hz"):
        size = float(os.environ["DRIFT"])
        kw["mains_drift_hz"] = size if kw["mains_drift_hz"] > 0 else -size
    # PROFILE: the simulator's flicker cost profile (simulator.FLICKER_COSTS).
    if os.environ.get("PROFILE"):
        kw["cost_profile"] = os.environ["PROFILE"]
    # Both badges send and decode fast blocks (fastblock.py), as the app does.
    kw["fast"] = load_module("share_wifi", "fastblock")
    link = sim.build_loop(protocol, payload, seed=seed, **kw)
    total = link.run(300)
    leader = link.leader.transfer
    follower = link.follower.transfer
    ok = (leader.result == protocol.OK
          and follower.received == [bytes(f) for f in payload])
    firsts = link.phase_times()
    skew = kw.get("start_skew_ms", 0) / 1000.0
    cal = firsts.get(protocol.PHASE_CAL_SEND) or firsts.get(protocol.PHASE_CAL_GAP)
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
