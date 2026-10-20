"""Dev link subtool: resets the badge.

While the dev link is open, Ctrl-C is off, so a host cannot interrupt the
app; B on the badge closes the link. A host that opened the link itself,
launching share_wifi with DEV_AUTORUN "link" (tests/share_wifi/autorun_host.py),
ends it with `run reset`: machine.reset(), and the badge boots to its
menu. The reply never comes; the port drops.

Upload with `new reset`.
"""

DESCRIPTION = "Resets the badge, back to its menu"
ARGS = ""
EXT = "txt"


def run(api, args):
    import machine
    api.log("resetting")
    machine.reset()
