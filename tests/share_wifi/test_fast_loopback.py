"""The fast_loopback subtool (devtests/fast_loopback.py) on optic_fake's
virtual mirror, through the real dev link server and host: the app's sender
-- fasttx.Pacer on the virtual PWM slice and DMA -- and a Follower Transfer
on a RingReader over the virtual ring, decoding as the burst plays. That the
subtool works before it reaches the badge; nothing here is evidence about
the badge's optics or timing, which virtual time does not model."""

import json
import os
import subprocess
import tempfile

import optic_fake as of
import fast_loopback_host as host
from harness import Skipped, load_module

P = load_module("share_wifi", "protocol")
R = load_module("share_wifi", "reader")
FB = load_module("share_wifi", "fastblock")


class OpticRing:
    """optic_fake's ring as a RingReader wants one: its kernels the
    reader's reference twins, over the same buffer the virtual DMA fills."""

    def __init__(self, badge):
        self.badge = badge
        self.inner = badge.ring
        self.samples = of.RING_SAMPLES
        self.mask = self.samples - 1
        self.period_us = of.PERIOD_US

    def start(self):
        self.badge.running = True

    def stop(self):
        self.badge.running = False

    def position(self):
        return self.inner.position()

    def health(self):
        return 0

    def sum(self, start, n):
        return R.ref_sum(self.inner.addr, self.mask, start, n)

    def parts(self, start, part, n, out):
        R.ref_parts(self.inner.addr, self.mask, start, part, n, out)

    def rise(self, start, bin_len, bins, thr, thr_step):
        return R.ref_rise(self.inner.addr, self.mask, start, bin_len, bins,
                          thr, thr_step)


class Divided:
    """optic_fake's slice with a writable divider, as the RP2350's is."""

    def __enter__(self):
        self.saved = of.Mem.__setitem__
        set_item = self.saved

        def setitem(mem, addr, value):
            if addr == of.PWM_DIV:
                mem.badge.advance(of.COST_US["mem"])
                mem.badge.div = value & 0xFFF
                return
            set_item(mem, addr, value)
        of.Mem.__setitem__ = setitem
        return self

    def __exit__(self, *exc):
        of.Mem.__setitem__ = self.saved


def run_loopback(args, room=0.0, seed=1, flicker=0.0):
    """Uploads and runs fast_loopback on a virtual badge. (last frame,
    the saved JSON or None, the badge)."""
    badge = of.Badge(seed=seed, room=room, flicker=flicker)
    badge.cc_buf = badge.cc = 0x00050000 | 30000
    port = of.FakePort()
    hooks = of.Hooks()
    hooks.ring_reader = R.RingReader(OpticRing(badge), badge.ticks_us)
    hooks.backlight = badge.backlight
    hooks.ticks_ms = badge.ticks_ms
    hooks.ticks_us = badge.ticks_us
    server = of.ds.Server(port, tempfile.mkdtemp(prefix="devtests"), hooks,
                          {})
    server.scan()
    outdir = tempfile.mkdtemp(prefix="out")
    out = []
    client = of.dh.Client(of.Pipe(server, port),
                          of.dh.Saver(outdir, out=out.append),
                          out=out.append)
    with of.installed(badge), Divided():
        client.hello()
        reply = client.upload(of.tool_path("fast_loopback"))
        assert reply["t"] == "saved", reply
        frame = client.run("fast_loopback", args)
    data = None
    for entry in os.listdir(outdir):
        if entry.endswith(".json"):
            with open(os.path.join(outdir, entry)) as handle:
                saved = json.load(handle)
            if "captures" in saved:     # not the run's returned summary
                data = saved
    return frame, data, badge


def test_blocks_cross_the_virtual_mirror_dark_on_two_rungs():
    # F1 and F2: a dark pair starts on F1. Dark F0 fails here more than on
    # the badge (test_the_lag_is_measured_from_the_landing).
    frame, data, badge = run_loopback(["1,2", "1", "32"])
    assert data is not None, frame
    assert data["summary"]["failed"] == [], data["summary"]
    assert data["summary"]["rungs"] == {"1": [1, 1], "2": [1, 1]}
    for cap in data["captures"]:
        assert cap["ok"] and cap["matched"] and cap["regs_restored"], cap
        assert cap["timers"]["begin"]["n"] == 1, "one begin(), three starts"
        assert cap["timers"]["feed"]["n"] >= 1
        assert cap["timers"]["bins"]["n"] >= 1
        assert 0 <= cap["late_us"] < 1000, cap["late_us"]
        # The rise: the start's lateness, a PWM period at most for the DMA's
        # first wrap, and the virtual sensor's lag -- inside the search.
        assert 0 < cap["rise_us"] < P.FAST_SEARCH_US, cap["rise_us"]
    assert data["div16"] == 16 and data["freq"] == 200000000
    assert data["blocking"]["check"] and not data["blocking"]["lost"]
    assert data["blocking"]["clock_us"] >= data["blocking"]["on_air_us"]
    # The driver's divider, and channel B's half of CC, as found.
    assert badge.div == 16 and badge.cc_buf >> 16 == 0x0005
    assert not data["lit"]
    assert host.main(["x", write(data)]) == 0


def test_fit_fits_a_flickering_room_and_every_block_decodes_with_it():
    # An LED light on 50 Hz mains, 30% of the room's light: the fit finds
    # it, and the decoder's flicker terms are on at twice the mains.
    frame, data, _ = run_loopback(["0", "2", "32", "defer", "fit"],
                                  room=256.0, seed=3, flicker=0.3)
    assert data is not None, frame
    assert data["fit"]["verdict"] == "flicker", data["fit"]
    assert abs(data["fit"]["mhz"] - 100000) < 2000, data["fit"]
    assert data["summary"]["failed"] == [], data["summary"]
    for cap in data["captures"]:
        assert cap["flicker_mhz"] == data["fit"]["mhz"], cap


def test_fit_leaves_a_steady_room_unfitted():
    frame, data, _ = run_loopback(["0", "1", "32", "defer", "fit"],
                                  room=256.0, seed=2)
    assert data is not None, frame
    assert data["fit"]["verdict"] == "steady" and data["fit"]["mhz"] == 0
    assert data["summary"]["failed"] == [], data["summary"]


def test_a_lit_room_decodes_too():
    frame, data, _ = run_loopback(["0", "1", "32"], room=256.0, seed=2)
    assert data is not None, frame
    assert data["lit"]
    assert data["summary"]["failed"] == [], data["summary"]


def test_the_summary_names_every_rule_that_failed():
    ns = {"__name__": "fast_loopback"}
    with open(of.tool_path("fast_loopback")) as handle:
        exec(compile(handle.read(), "fast_loopback", "exec"), ns)
    base = {"rung": 0, "seed": 1, "ok": True, "matched": True,
            "regs_restored": True, "check": True, "lost": 0}
    out = {"captures": [base,
                        dict(base, seed=2, matched=False),
                        dict(base, seed=3, regs_restored=False),
                        dict(base, seed=4, lost=1)],
           "blocking": {"check": True, "lost": 2}}
    summary = ns["summarise"](out)
    assert summary["rungs"] == {"0": (3, 4)}
    text = " | ".join(summary["failed"])
    for needle in ("seed 2: not decoded", "seed 3: DIV or CC",
                   "seed 4: ring check", "blocking burst"):
        assert needle in text, (needle, text)
    assert host.main(["x", write(dict(out, summary=summary))]) == 1


def test_the_lag_is_measured_from_the_landing():
    # The first word lands at the second wrap after the trigger, one to two
    # PWM periods; the rise follows it by the sensor's lag. lag_us is the
    # rise less the landing: positive, and within a slot's settle. The
    # virtual sensor is slower than the badge's at v2's dim low (the link
    # history, "Toward files: stage 3": 12 of 16 dark there, 0 errors in
    # 32,496 on the mirror), so its dark lag is not the badge's.
    _, data, _ = run_loopback(["1", "2", "32"])
    period = FB.COMMON_NS / 1000.0
    for cap in data["captures"]:
        assert period <= cap["landing_us"] - cap["late_us"] <= 2 * period + 1, cap
        assert 0 < cap["lag_us"] < P.SETTLE_DARK_US, cap


def test_a_dma_fault_puts_the_slice_back_and_stops_the_ring():
    of.DMA.fail_on = 2          # the first burst's config() raises
    frame, data, badge = run_loopback(["0", "1", "32"])
    assert data is None, "nothing is sent from a run that raised"
    assert frame["t"] == "end" or frame.get("ok") is False or \
        "error" in json.dumps(frame), frame
    assert badge.div == 16 and badge.cc_buf >> 16 == 0x0005
    assert not badge.running, "the ring was stopped"


def test_the_subtool_compiles_for_the_badge():
    mpy = os.environ.get("MPY_CROSS")
    if not mpy or not os.path.exists(mpy):
        raise Skipped("no mpy-cross: set MPY_CROSS")
    path = of.tool_path("fast_loopback")
    with tempfile.TemporaryDirectory() as out:
        result = subprocess.run(
            [mpy, "-march=armv7emsp", "-o", os.path.join(out, "t.mpy"), path],
            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert os.path.getsize(path) < 16384, "the link uploads 16 KB at most"


def write(data):
    path = os.path.join(tempfile.mkdtemp(prefix="fl"), "fast_loopback.json")
    with open(path, "w") as handle:
        json.dump(data, handle)
    return path
