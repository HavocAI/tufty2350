"""The dev link's example subtools, kept as sources: the end-to-end tests
upload them (test_devlink_e2e.py), and the hardware checklist in
.agents/docs/testing.md puts them on a real badge --

    python3 tests/share_wifi/devlink_examples.py DIR

writes each to DIR/<name>.py, for `new DIR/spin.py`. Not a test.
"""

import os
import sys

# Example subtools, the link's own: the smallest useful one; one that
# never polls, which only the hard cancel stops; one that streams the DMA
# ring's raw readings once the ring has stopped.
EXAMPLES = {
    "hello": '"""Example dev link subtool: the smallest useful one.\n\nUpload with `new hello`, then `run hello some words`. Sends a text file and\nreturns a dict, which arrives as hello.<timestamp>.json.\n"""\n\nDESCRIPTION = "Says hello; sends a .txt and a .json"\nARGS = "[words...]"\nEXT = "txt"\n\n\ndef run(api, args):\n    api.log("hello is running")\n    api.send("hello from the badge: %s\\n" % " ".join(args))\n    return {"args": args, "tethered": api.tethered(), "light": api.light()}\n',
    "spin": '"""Example dev link subtool that never polls: the hard cancel\'s test.\n\nIt loops without calling api.cancelled() or writing anything, so only the\nhard cancel can stop it: B on the badge (a Pin IRQ that injects Ctrl-C), or\nCtrl-C in the host program (a raw 0x03). Give a number of seconds to make it\nstop by itself instead.\n"""\n\nDESCRIPTION = "Loops until cancelled, never polling (tests B and Ctrl-C)"\nARGS = "[seconds]"\nEXT = "json"\n\n\ndef run(api, args):\n    limit = int(args[0]) * 1000 if args else -1\n    start = api.ticks_ms()\n    n = 0\n    while limit < 0 or api.ticks_ms() - start < limit:\n        n += 1\n    return {"loops": n}\n',
    "ring_trace": '"""Example dev link subtool: the DMA ring\'s raw readings, streamed as CSV.\n\nStarts the ring (fastadc.Ring, 50,000 readings a second), waits while it\nfills, stops it, and only then sends: nothing may write USB while the ring\nhas the ADC, and api refuses to. The readings stay in the ring\'s memory\nafter it stops, so they are read out afterwards with no race against the\nDMA. Each row is index (20 us apart) and the raw 12-bit reading.\n"""\n\nfrom array import array\n\nDESCRIPTION = "Raw ring readings for up to 300 ms, as CSV"\nARGS = "[ms, default 100]"\nEXT = "csv"\n\n\ndef run(api, args):\n    ms = int(args[0]) if args else 100\n    ms = max(1, min(ms, 300))\n    reader = api.ring_start()\n    if reader is None:\n        raise RuntimeError("no ring: %s" % api.ring_note())\n    ring = reader.ring\n    n = ms * 1000 // ring.period_us\n    values = array("H", range(n))           # allocated before the wait\n    start = api.ticks_ms()\n    while api.ticks_ms() - start < ms + 5:\n        pass\n    end = ring.position()\n    health = ring.health()\n    api.ring_stop()\n    first = end - n\n    for i in range(n):\n        values[i] = ring.read(first + i)\n    with api.stream("csv") as out:\n        out.write("index,raw\\n")\n        for i in range(n):\n            out.write("%d,%d\\n" % (i, values[i]))\n    return {"readings": n, "period_us": ring.period_us, "health": health,\n            "tethered": api.tethered()}\n',
}


if __name__ == "__main__":
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    for name, source in EXAMPLES.items():
        path = os.path.join(out, name + ".py")
        with open(path, "w") as handle:
            handle.write(source)
        print(path)
