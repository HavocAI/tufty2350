"""Dev link subtool: the room's flicker (lib/flicker.py), about 30 s a
measurement, the badge's own light off: the strongest frequency and its
size, the harmonics the filter fits, the transfer's own FlickerFilter fit
on the capture, and the mains' drift against the badge's clock. Each
measurement saved under `room_flicker`, numbered in the order taken.
"""

DESCRIPTION = "Measure the room's flicker (about 30 s each)"
ARGS = "[measurements 1]"
EXT = "json"
MENU = (("measure room flicker", ["1"]),)


def measure_once(api, flicker, capture):
    temp = api.hooks.temp
    temp_c = temp() if temp is not None else None
    reader = api.ring_start()
    if reader is None:
        return {"error": api.ring_note()}
    try:
        capture.take(reader)
        result = flicker.analyse(capture)
        if temp_c is not None:
            result["temp_c"] = temp_c
        hz = flicker.drift_hz(result)
        if hz is None:
            result["drift"] = {"error": "no mains flicker"}
            return result
        drift = flicker.Drift(hz)
        while drift.attempts < flicker.DRIFT_WINDOWS:
            if api.cancelled():
                result["drift"] = {"error": "cancelled"}
                return result
            capture.take(reader)
            drift.add(capture)
        result["drift"] = drift.result()
        return result
    finally:
        api.ring_stop()


def run(api, args):
    import flicker
    import toolkit
    count = max(1, int(args[0])) if args else 1
    badge.caselights(0.0)       # noqa: F821 - its own light must not be measured
    api.backlight(0.0)
    capture = flicker.Capture()
    results = []
    for _ in range(count):
        result = measure_once(api, flicker, capture)
        result["tethered"] = api.tethered()
        api.save({toolkit.next_key(flicker.TOOL_FLICKER): result},
                 flicker.TOOL_FLICKER)
        results.append(flicker.summary(result))
        if api.cancelled():
            break
    return {"measured": results}
