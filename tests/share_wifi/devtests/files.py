"""Dev link subtool: the badge's files. `list PATH` returns a folder's
entries; `get PATH...` sends each file as it is, a chunk at a time, saved
by the host as <name>.<time>.<ext> -- /system included, read-only there.
"""

DESCRIPTION = "List the badge's folders, or send its files"
ARGS = "list PATH | get PATH..."
EXT = "bin"

CHUNK = 4096
IS_DIR = 0x4000


def entries(path):
    import os
    out = []
    for name in sorted(os.listdir(path)):
        full = path.rstrip("/") + "/" + name
        st = os.stat(full)
        out.append([name + ("/" if st[0] & IS_DIR else ""), st[6]])
    return out


NAME_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"


def stem_ext(name):
    """A file name as the link's (subtest, ext), within its rules."""
    base = name.rsplit("/", 1)[-1].lower()
    dot = base.rfind(".")
    stem, ext = (base[:dot], base[dot + 1:]) if dot > 0 else (base, "bin")
    clean = "".join(ch if ch in NAME_CHARS else "_" for ch in stem)[:24]
    if not clean or clean[0] not in NAME_CHARS[:26]:
        clean = ("f" + clean)[:24]
    ext = "".join(ch for ch in ext if ch in NAME_CHARS)[:8] or "bin"
    return clean, ext


def run(api, args):
    if len(args) < 2 or args[0] not in ("list", "get"):
        return {"error": "say list PATH or get PATH..."}
    if args[0] == "list":
        return {"path": args[1], "entries": entries(args[1])}
    sent = []
    for path in args[1:]:
        if api.cancelled():
            break
        stem, ext = stem_ext(path)
        with open(path, "rb") as handle:
            with api.stream(ext, stem) as out:
                while True:
                    data = handle.read(CHUNK)
                    if not data:
                        break
                    out.write(data)
        sent.append(path)
    return {"sent": sent}
