"""Dev link subtool: probe frame timing (lib/probe.py). Times the panel's
refresh and a frame's write, an empty loop, the `ring` step (M3: a
transfer's listener through the ring for 2 s, quiet, presenting and with a
fitted flicker, a slot's two bounds), allocation, the flicker filter (M7)
and garbage collection, with the firmware (M1) and heap readings. About
30 s; the screen flashes. Saved under `frame_probe`.

The badge-side glue the probe is handed lives here: its listeners, the
fitted filter, the transmitter's path and the heap reader.
"""

DESCRIPTION = "Probe frame timing: display, ring, flicker filter, gc"
ARGS = ""
EXT = "json"
MENU = (("probe frame timing", []),)

import gc
import os
import time

# How far above the room a probe listener puts its middle level, and twice
# that its high, in counts: the room, its flicker and the fitted correction
# all read low. The room is read through the ring for ROOM_US first.
PROBE_LEVEL_GAP = 128
ROOM_US = 16000
TX_PATH_CALLS = 200


def probe_listener(P, reader, peer_lit=False):
    """A Follower in the data phase on the lit 8 ms rung, listening through
    `reader` -- started here -- to a peer that is dark: every slot LOW,
    learnt from and tracked once fitted, the upper bound on a slot's cost.
    `peer_lit` puts the room at the middle level: nothing tracked, the
    lower bound. What it hears is thrown away."""
    reader.start()
    start = reader.now_us()
    reader.pump(start + ROOM_US + 500)
    black = reader.mean(start, start + ROOM_US)
    if peer_lit:
        black -= PROBE_LEVEL_GAP
    levels = P.Levels()
    levels.fit(black, black + PROBE_LEVEL_GAP, black + 2 * PROBE_LEVEL_GAP)
    transfer = P.Transfer(False, reader=reader)
    transfer.levels = levels
    transfer.scheme = P.SCHEME_BACKLIGHT
    transfer.start_data(0, P.EXACT_LIT_INDEX)
    return transfer


def give_fitted_filter(P, probe, listener):
    """`listener` with a FlickerFilter fitted on the probe's synthetic room
    (120 Hz and its 240 Hz harmonic), its tables built before anything is
    timed: built on first use, one cost ~17 ms on v2. Returns the verdict."""
    ts, vs, spans = probe.filter_room(listener.reader.now_us(),
                                      probe.FILTER_SAMPLES)
    flicker = P.FlickerFilter()
    verdict = flicker.fit(ts, vs, spans)
    listener.flicker = flicker
    listener.set_window()
    span = (listener.w_close - listener.w_open) // listener.w_parts
    for k in range(1, listener.w_parts + 1):
        flicker.amount(0, span * k)
    flicker.amount(0, 100)
    return verdict


def tx_path(P, probe, transfer):
    """A transmitter's own path between the pump returning and the
    backlight changing -- field_at(), the table lookup, a set only if
    changed -- against the bare display.backlight() call the loopback
    makes, alternating high and middle so every call changes the duty."""
    path = probe.TimeStats()
    bare = probe.TimeStats()
    lights = P.LEVEL_SCHEMES[P.SCHEME_BACKLIGHT]
    transfer.send([P.HIGH, P.MID] * (TX_PATH_CALLS // 2 + 1))
    clock = transfer.clock
    transfer.field_at(transfer.reader.now_us())     # fixes its first slot
    first = transfer.tx_slot
    if first is None:
        transfer.tx = []
        return {"error": "not transmitting (result %s)" % transfer.result}
    display.backlight(lights.get(P.MID, 1.0))       # noqa: F821 - injected
    shown = [None]
    changed = 0
    last = None
    for i in range(TX_PATH_CALLS):
        t = clock.start_of(first + i) + 1
        a = time.ticks_us()
        field = transfer.field_at(t)
        level = lights.get(field, 1.0)
        if shown[0] != level:
            display.backlight(level)                # noqa: F821
            shown[0] = level
        path.add(time.ticks_diff(time.ticks_us(), a))
        if field != last:
            changed += 1
            last = field
        a = time.ticks_us()
        display.backlight(lights.get(field, 1.0))   # noqa: F821
        bare.add(time.ticks_diff(time.ticks_us(), a))
    transfer.tx = []
    return {"field_and_show": path.rate(), "backlight_call": bare.rate(),
            "changes": changed}


def ring_step(api, P, probe, toolkit, mode):
    """M3: the listener on the lit 8 ms rung for RING_LISTEN_MS. "quiet"
    holds the white screen; "presenting" presents every pass; "fitted"
    holds it with a fitted flicker filter, so a slot pays every correction
    and tracking; "fitted_lit" hears its peer lit. `room` is the room."""
    reader = toolkit.held_ring(api)
    if reader is None:
        return {"error": api.ring_note()}
    toolkit.white(api)
    listener = probe_listener(P, reader, peer_lit=mode == "fitted_lit")
    verdict = None
    if mode in ("fitted", "fitted_lit"):
        verdict = give_fitted_filter(P, probe, listener)
    try:
        out = probe.ring_listen(
            listener, time.ticks_us,
            present=api.present if mode == "presenting" else None,
            poll=badge.poll, collect=gc.collect,            # noqa: F821
            mem_free=getattr(gc, "mem_free", None),
            cancelled=lambda: badge.pressed(BUTTON_B))      # noqa: F821
        out["room"] = listener.levels.grey if mode == "fitted_lit" \
            else listener.levels.black
        if mode == "quiet":
            try:
                out["tx_path"] = tx_path(P, probe, listener)
            except Exception as error:     # noqa: BLE001 - keeps the ring's
                out["tx_path"] = {"error": "%s: %s" % (
                    type(error).__name__, error)}
    finally:
        api.ring_stop()
    if verdict is not None:
        out["fit_verdict"] = verdict
    return out


def heap_hook(probe):
    """probe.heap_reader() over mem_info(), with uctypes placing the sweep's
    extent; None where the firmware cannot give it. mem_info() also prints
    to the USB serial, which the host reads as noise between frames."""
    try:
        import io
        import micropython
    except ImportError:
        return None
    try:
        import uctypes
        addressof = uctypes.addressof
    except ImportError:
        addressof = None
    return probe.heap_reader(getattr(os, "dupterm", None),
                             getattr(micropython, "mem_info", None),
                             io.IOBase,
                             probe.block_bytes(hasattr(display,     # noqa: F821
                                                       "set_framerate")),
                             addressof)


def firmware_info(api):
    """The build, whether it sets the panel's frame rate, and M1: what the
    DMA ring needs, and whether it worked -- None -- or why not."""
    info = {}
    try:
        info["version"] = os.uname().version
    except Exception:   # noqa: BLE001 - informational only
        pass
    info["set_framerate"] = getattr(display, "set_framerate",  # noqa: F821
                                    None) is not None
    for name in ("rp2", "uctypes"):
        try:
            __import__(name)
            info[name] = True
        except ImportError:
            info[name] = False
    try:
        import rp2
        info["rp2_dma"] = hasattr(rp2, "DMA")
    except ImportError:
        info["rp2_dma"] = False
    info["ring"] = api.ring_note()
    ring = api.hooks.ring()
    if ring is not None:
        info["heap_addr"] = "0x%08x" % ring.ring.cached
        info["ring_addr"] = "0x%08x" % ring.ring.addr
    return info


def run(api, args):
    import protocol as P
    import probe
    import toolkit
    state = {}

    def status():
        p = state["probe"]
        screen.pen = color.black                    # noqa: F821
        screen.clear()                              # noqa: F821
        screen.pen = color.white                    # noqa: F821
        screen.text("Probe: %s %d/%d" % (probe.STEP_TITLES[p.step_name],
                                         p.index + 1, len(probe.STEPS)),
                    10, 100)                        # noqa: F821

    gc.collect()
    hooks = probe.Hooks(
        present=api.present,
        set_vsync=getattr(display, "set_vsync", None),  # noqa: F821
        poll=badge.poll, clear=badge.clear,             # noqa: F821
        frame=(("status", status),),
        cancelled=lambda: badge.pressed(BUTTON_B),      # noqa: F821
        collect=gc.collect, enable=gc.enable, disable=gc.disable,
        mem_alloc=getattr(gc, "mem_alloc", None),
        mem_free=getattr(gc, "mem_free", None),
        ticks_us=time.ticks_us, temp=api.hooks.temp,
        flicker_filter=P.FlickerFilter,
        ring=lambda mode: ring_step(api, P, probe, toolkit, mode),
        heap=heap_hook(probe))
    state["probe"] = frame = probe.FrameProbe(hooks, firmware_info(api))
    while not frame.done:
        if frame.cancelled or api.cancelled():
            return None
        status()
        api.present()
        frame.step()
        api.hooks.restore()
    result = frame.results()
    result["tethered"] = api.tethered()
    api.save(result)
    return result
