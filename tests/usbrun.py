"""Driving a badge over USB with mpremote: reset it, put this tree's apps
on it, launch one with an autorun. Used by tests/share_wifi/autorun_host.py;
see "Fresh launches over USB" in .agents/docs/testing.md.

The badge stays plugged in with its menu running. `/system`, where the
menu's apps live, is read-only to MicroPython and written only in USB disk
mode, so this tree's .py files go to RUNS_DIR on the badge's own
filesystem and are launched from there, compiled from source as the
menu's are. Connecting interrupts the menu and soft-resets to a clean
heap; `launch()` is the call v2's main.py makes. An app that reads
`builtins.DEV_AUTORUN` runs what it names and quits by itself.

mpremote 0.4 has no `reset`: machine.reset() goes through exec. Needs the
dialout group (`sg dialout -c "..."`).
"""

import json
import os
import subprocess
import time

RUNS_DIR = "/runs/"
APPS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                    "firmware", "apps")
RESET_WAIT_S = 6            # the badge re-enumerating after a reset


def launch_code(app, autorun):
    """What runs at the REPL: DEV_AUTORUN set, a clean heap, the app
    launched as v2's main.py does."""
    return "\n".join([
        "import builtins, gc",
        "builtins.DEV_AUTORUN = %s" % json.dumps(autorun),
        "gc.collect()",
        "launch(%s)" % json.dumps(RUNS_DIR + app),
        "",
    ])


class Badge:
    def __init__(self, port):
        self.port = port

    def mpremote(self, *args, timeout=60):
        return subprocess.run(["mpremote", "connect", self.port] + list(args),
                              capture_output=True, text=True,
                              timeout=timeout)

    def reset(self):
        """machine.reset(); returns once the port is back. The port drops,
        so mpremote's exit status says nothing."""
        try:
            self.mpremote("exec", "import machine; machine.reset()",
                          timeout=15)
        except subprocess.TimeoutExpired:
            pass
        self.wait()

    def wait(self):
        deadline = time.time() + 30
        time.sleep(RESET_WAIT_S)
        while not os.path.exists(self.port) and time.time() < deadline:
            time.sleep(0.5)

    def upload(self, app):
        """The app's .py files to RUNS_DIR/app/, replacing what is there."""
        folder = os.path.join(APPS, app)
        files = sorted(os.path.join(folder, name)
                       for name in os.listdir(folder) if name.endswith(".py"))
        for path in (RUNS_DIR.rstrip("/"), RUNS_DIR + app):
            self.mpremote("fs", "mkdir", ":" + path)    # fails if it exists
        done = self.mpremote(*(["fs", "cp"] + files
                               + [":" + RUNS_DIR + app + "/"]), timeout=300)
        if done.returncode != 0:
            raise SystemExit("upload of %s failed: %s" % (
                app, done.stderr.strip()))

    def start(self, app, autorun):
        """Launches the app and lets go of the port at once (--no-follow):
        for an app that takes the USB serial itself, as the dev link does."""
        return self.mpremote("exec", "--no-follow",
                             launch_code(app, autorun), timeout=30)
