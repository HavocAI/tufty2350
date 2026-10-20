#!/usr/bin/env python3
"""Runs share_wifi's dev link subtools with no hands, over USB.

    python3 tests/share_wifi/autorun_host.py OUTDIR "CMD" ["CMD" ...]
        [--no-upload] [--no-sync] [--port /dev/ttyACM0]

The badge stays plugged in with its menu running, in whatever light and on
whichever mirror the run wants: nothing here can change those. First this
tree's share_wifi goes to the badge's /runs/ (tests/usbrun.py) unless
--no-upload. Then the app is launched from a reset with
`builtins.DEV_AUTORUN = "link"` (`autorun()` in the app), which opens the
USB dev link; this lets go of the port, uploads every subtool and library
in tests/share_wifi/devtests/ (`sync`) unless --no-sync, and runs each CMD
through tests/share_wifi/devlink_host.py OUTDIR -- `"run mirror dark"`,
`"run frame_probe"`, `"run fast_loopback 0 8 0 defer fit"`. Files the
subtools send land in OUTDIR. Last it uploads and runs the `reset`
subtool, since Ctrl-C is off while the link is open, so every run ends
with the badge at its menu. Needs mpremote and pyserial, and the dialout
group (`sg dialout -c "..."`).
"""

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from usbrun import Badge     # noqa: E402

APP = "share_wifi"
LINK_WAIT_S = 60            # the app compiling, launching, opening the link
DEVLINK = os.path.join(HERE, "devlink_host.py")


def devlink(outdir, port, command, timeout=600):
    """One devlink_host.py command; its exit status."""
    argv = [sys.executable, DEVLINK, "--port", port, "--yes", outdir]
    argv += command.split()
    print("  devlink: %s" % command, flush=True)
    try:
        return subprocess.run(argv, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        print("  timed out")
        return 1


def wait_link(outdir, port):
    """Polls `list` until the link answers: the app compiles share_wifi
    from source first, which takes several seconds."""
    deadline = time.time() + LINK_WAIT_S
    while time.time() < deadline:
        time.sleep(3)
        if devlink(outdir, port, "list", timeout=20) == 0:
            return True
    return False


def run_link(badge, outdir, port, commands):
    badge.reset()
    badge.start(APP, "link")
    ok = wait_link(outdir, port)
    if not ok:
        print("  the link never answered")
        commands = ()
    for command in commands:
        if devlink(outdir, port, command) != 0:
            ok = False
            break
    # Ctrl-C is off while the link is open: the badge resets itself.
    devlink(outdir, port, "new reset --force", timeout=60)
    devlink(outdir, port, "run reset", timeout=30)
    badge.wait()
    return ok


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("outdir")
    parser.add_argument("commands", nargs="*",
                        help="devlink_host commands, each one argument")
    parser.add_argument("--port", default="/dev/ttyACM0")
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--no-sync", action="store_true")
    args = parser.parse_args(argv)
    os.makedirs(args.outdir, exist_ok=True)
    badge = Badge(args.port)
    if not args.no_upload:
        print("uploading %s" % APP, flush=True)
        badge.upload(APP)
    commands = ([] if args.no_sync else ["sync"]) + list(args.commands)
    return 0 if run_link(badge, args.outdir, args.port, commands) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
