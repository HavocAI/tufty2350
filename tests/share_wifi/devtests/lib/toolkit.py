"""What the dev link's measurement subtools share on the badge: the white
screen a test signals behind, the ring held for a run, and numbered keys
in the measurements file. Badge-only: it reads the injected globals."""

import json

MEASUREMENTS_PATH = "/state/measurements.json"
FRAME_US = 16700         # a frame of the v2 panel's 60 Hz [measured-v2]


def white(api):
    """The whole screen the panel's full white, drawn once and presented,
    run()'s clear off and the case lights out: every signal from here is the
    backlight. The link's restore() puts the panel back."""
    badge.default_clear = None          # noqa: F821 - injected
    badge.caselights(0.0)               # noqa: F821
    screen.pen = color.rgb(255, 255, 255)   # noqa: F821
    screen.clear()                      # noqa: F821
    api.present()


def held_ring(api):
    """The ring's reader for a run that starts and stops it itself, or
    None. Taken through api.ring_start(), so no USB write or ADC read can
    happen until api.ring_stop() -- and the link stops it if the run
    raises -- then stopped: the run starts it when it is ready."""
    reader = api.ring_start()
    if reader is not None:
        reader.stop()
    return reader


def next_key(tool):
    """The next number under `tool` in the measurements file, as a string,
    so runs show in the order taken."""
    try:
        with open(MEASUREMENTS_PATH) as handle:
            entry = json.loads(handle.read()).get(tool)
    except (OSError, ValueError):
        entry = None
    most = 0
    if isinstance(entry, dict):
        for key in entry:
            try:
                most = max(most, int(key))
            except ValueError:
                pass
    return str(most + 1)
