"""Dev link subtool: the screen measured on a mirror (lib/measure.py's
MirrorRun), one condition a run, in the room's light as it is: ambient,
levels, noise, the backlight curve, flicker, M4's steps and M5's loopback.
Saved under `mirror`, beside the other condition's.
"""

DESCRIPTION = "Measure the screen on a mirror, in the room's light as it is"
ARGS = "dark|lit"
EXT = "json"
# Dev Mode runs these with no host once the tool is on the badge.
MENU = (("mirror: dark room", ["dark"]), ("mirror: lit room", ["lit"]))


def run(api, args):
    import measure
    import toolkit
    condition = args[0] if args else "dark"
    if condition not in ("dark", "lit"):
        return {"error": "say dark or lit"}
    reader = toolkit.held_ring(api)
    run = measure.MirrorRun(reader, api.ring_note(), api.backlight,
                            api.hooks.temp)
    toolkit.white(api)
    run.begin()
    while not run.done:
        if api.cancelled():
            run.stop()
            return None
        run.frame()
        # Paced as the menu's frames paced it: one test, or one step, a
        # frame. The ring reads meanwhile.
        reader.pump(reader.now_us() + toolkit.FRAME_US)
    result = run.results()
    api.ring_stop()
    result["tethered"] = api.tethered()
    api.save({condition: result})
    return result
