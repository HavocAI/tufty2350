"""Dev link subtool: the screens measured between two badges in lockstep
(lib/measure.py's SecondBadgeRun), one condition a run, each badge on its
own link: the first sends first. Saved under `second_badge`.
"""

DESCRIPTION = "Measure the screens between two badges, one condition"
ARGS = "first|second dark|lit"
EXT = "json"
MENU = (("2nd badge: first, dark", ["first", "dark"]),
        ("2nd badge: second, dark", ["second", "dark"]),
        ("2nd badge: first, lit", ["first", "lit"]),
        ("2nd badge: second, lit", ["second", "lit"]))


def run(api, args):
    import measure
    import toolkit
    if len(args) < 2 or args[0] not in ("first", "second") \
            or args[1] not in ("dark", "lit"):
        return {"error": "say first|second dark|lit"}
    reader = toolkit.held_ring(api)
    run = measure.SecondBadgeRun(args[0] == "first", reader, api.ring_note(),
                                 api.backlight, api.hooks.temp)
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
    api.save({"role": args[0], args[1]: result})
    return result
