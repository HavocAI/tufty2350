"""Host check for fast_loopback (devtests/lib/fastloop.py, by the dev
link): reads a saved run, prints each capture, and says whether every rule
held.

    python3 tests/share_wifi/fast_loopback_host.py OUTDIR/fast_loopback.<ts>.json
    python3 tests/share_wifi/fast_loopback_host.py measurements.json

Exit 0 when every capture decoded and matched, DIV and CC were put back
after every burst, the ring kept every reading, and the blocking burst left
the ring healthy; 1 otherwise. The figures beside them -- lateness, the rise
against EDGE_LAG_FAST_*_US, training and feed times, collections -- are for
reading, not judged here: what they must be is the protocol's to decide.
"""

import json
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
# The subtool is a wrapper: its code is the library fastloop.py, over the
# app's protocol.
sys.path.insert(0, os.path.join(HERE, "..", "..", "firmware", "apps",
                                "share_wifi"))
sys.path.insert(0, os.path.join(HERE, "devtests", "lib"))


def subtool():
    path = os.path.join(HERE, "devtests", "fast_loopback.py")
    namespace = {"__name__": "fast_loopback"}
    with open(path) as handle:
        exec(compile(handle.read(), path, "exec"), namespace)
    return namespace


def main(argv):
    with open(argv[1]) as handle:
        data = json.load(handle)
    # The badge's measurements file: its entry.
    if "fast_loopback" in data:
        data = data["fast_loopback"]
    summary = subtool()["summarise"](data)
    print("clock %s Hz, DIV %s, low CC %s, high CC %s, levels %s, %s"
          % (data.get("freq"), data.get("div16"), data.get("low_cc"),
             data.get("high_cc"), data.get("levels"),
             "lit" if data.get("lit") else "dark"))
    for cap in data.get("captures", []):
        timers = cap.get("timers", {})
        mode = cap.get("mode") or "-"
        fit = timers.get(mode, {}).get("ms")
        print("F%s seed %-2s %-4s late %5s us  lag %5s us (assumed %s)"
              "  %-6s %s ms  feed %s ms (max %s)  gc %.1f ms"
              % (cap["rung"], cap["seed"],
                 "ok" if cap["ok"] and cap["matched"] else "FAIL",
                 cap.get("late_us"), cap.get("lag_us"),
                 cap.get("lag_assumed_us"), mode, fit,
                 timers.get("feed", {}).get("ms"),
                 timers.get("feed", {}).get("max_ms"), cap.get("gc_ms", 0)))
    block = data.get("blocking")
    if block:
        print("blocking burst: %s us on air, loop blocked %s us, ring check "
              "%s, lost %s" % (block.get("on_air_us"), block.get("blocked_us"),
                               block.get("check"), block.get("lost")))
    for rung, (good, total) in sorted(summary["rungs"].items()):
        print("F%s: %d of %d" % (rung, good, total))
    for line in summary["failed"]:
        print("FAIL", line)
    return 0 if summary["rungs"] and not summary["failed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
