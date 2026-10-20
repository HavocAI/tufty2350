"""Dev link subtool: fast blocks on a mirror (lib/fastloop.py): the app's
sender and a Follower's receiver, through the DMA ring.

Upload with `sync`; run `fast_loopback` (every rung, three each),
`fast_loopback 0,1 5`, or `fast_loopback 0 8 0 defer fit` (F0, eight
blocks, the room's flicker fitted first). Saved under `fast_loopback`;
tests/share_wifi/fast_loopback_host.py reads a saved run.
"""

DESCRIPTION = "Fast blocks on the mirror: the app's sender, the Follower's receiver"
ARGS = "[rungs 0,1,2,3] [repeats 3] [bytes: the rung's most] [stream|defer] [fit]"
EXT = "json"
MENU = (("fast blocks on mirror", ["0", "8", "0", "defer", "fit"]),)


def run(api, args):
    import fastloop
    return fastloop.run(api, args)


def summarise(out):
    """Per rung, decoded of captured, and every rule that failed: pure, the
    host's check runs it too (fast_loopback_host.py)."""
    import fastloop
    return fastloop.summarise(out)
