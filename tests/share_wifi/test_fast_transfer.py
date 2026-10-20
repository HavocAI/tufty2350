"""Fast blocks, the payload's only carrier: the stream they carry, how a
Follower keeps them, the fast Begin and its rung, the decoder's flicker, and
pairs run through the simulator -- lit on F0, dark on F1, and a Follower that
never decodes, which walks every rung and then holds the slowest until the
block's attempts are spent. The decoder itself is test_fastblock.py's. See sections 13.1 and 14.2 of the protocol spec."""

import random

import simulator as sim
import sweep
from harness import load_module
from held import HeldTransfer

P = load_module("share_wifi", "protocol")
FB = load_module("share_wifi", "fastblock")


# -- the stream

def test_a_stream_round_trips_any_fields():
    for fields in ([b"HomeNet", b"hunter2!!", b"3"], [b"open", b"", b"0"],
                   [], [b""], [b"x" * 300, b"y" * 128, b"z" * 127]):
        stream = P.fast_stream(fields)
        assert P.parse_fast_stream(stream) == fields, fields


def test_a_stream_cut_short_or_overlong_is_not_one():
    stream = P.fast_stream([b"HomeNet", b"hunter2!!"])
    for k in range(len(stream)):
        assert P.parse_fast_stream(stream[:k]) is None, k
    assert P.parse_fast_stream(stream + b"\x00") is None


def test_a_streams_blocks_cover_it_by_chunk():
    stream = P.fast_stream([b"A" * 32, b"k" * 63, b"x" * 200])
    for periods in FB.RUNGS:
        chunk, parts = 0, []
        while chunk * FB.CHUNK < len(stream):
            block = P.fast_block(FB, stream, chunk, periods)
            assert block and len(block) <= FB.block_bytes(periods)
            parts.append(block)
            chunk += (len(block) + FB.CHUNK - 1) // FB.CHUNK
        assert b"".join(parts) == stream, periods


def test_the_longest_burst_bounds_every_block():
    stream = bytes(range(256)) * 4
    for periods in FB.RUNGS:
        block = P.fast_block(FB, stream, 0, periods)
        symbols = len(FB.encode(0, block))
        assert symbols * periods * FB.COMMON_NS // 1000 \
            <= P.burst_max_us(FB, periods)


# -- the Follower keeps blocks by chunk

def _follower():
    return HeldTransfer(False, fast=FB)


def test_blocks_assemble_in_any_order_and_a_resend_is_kept_once():
    fields = [b"A" * 40, b"k" * 70]
    stream = P.fast_stream(fields)
    pieces = [(k, stream[k * FB.CHUNK:(k + 1) * FB.CHUNK])
              for k in range((len(stream) + FB.CHUNK - 1) // FB.CHUNK)]
    t = _follower()
    for chunk, piece in reversed(pieces[1:]):
        t.fast_accept(chunk, piece)
        assert not t.received, "not until chunk 0 is in"
    t.fast_accept(*pieces[0])
    assert t.received == fields
    t.fast_accept(*pieces[1])   # a resend after a lost ACK
    assert t.blocks_accepted == len(pieces)
    assert t.received == fields


# -- the fast Begin and its rung

def _nine(t, values):
    """`values` as the last nine slots `t` decided, oldest first."""
    t.rx_n = 9
    for k, value in enumerate(values):
        t.rx_values[(t.rx_n + k) % 9] = value


def test_a_begin_trains_only_on_its_own_shape():
    level = {"-": 112, "_": 70, " ": 32}
    t = _follower()
    t.levels = P.Levels()
    t.levels.fit(32, 70, 112)
    _nine(t, [level[c] for c in "-_ -_ -_ "])     # not FAST_BEGIN
    assert t.begin_levels() is None
    _nine(t, [level[c] for c in P.FAST_BEGIN])
    trained = t.begin_levels()
    assert trained is not None


def test_the_rung_travels_in_its_bits():
    # Every value the bits can take is a rung: the Follower checks none.
    assert len(FB.RUNGS) == 1 << P.FAST_RUNG_BITS
    for rung in range(len(FB.RUNGS)):
        symbols = P.bits_symbols(rung, P.FAST_RUNG_BITS)
        assert len(symbols) == P.FAST_RUNG_BITS
        value = 0
        for symbol in symbols:
            value = value * 2 + (symbol == P.HIGH)
        assert value == rung
        assert set(symbols) <= {P.HIGH, P.MID}, "never black, never a gap"
    assert P.FAST_START_LIT < P.FAST_START_DARK < len(FB.RUNGS)


# -- the decoder's flicker

def test_the_decoders_flicker_is_the_stronger_harmonic():
    t = _follower()
    assert t.fast_flicker_mhz() == 0, "nothing fitted"
    f = t.flicker
    f.mains, f.rate_m = 50, 0
    f.harmonics = [[3.0, 0.0], [1.0, 0.0]]
    assert t.fast_flicker_mhz() == 50000, "once a cycle: the mains"
    f.harmonics = [[1.0, 0.0], [3.0, 0.0]]
    assert t.fast_flicker_mhz() == FB.flicker_mhz(50, 0), "an LED: twice"


# -- pairs in the simulator

def _run(optics, payload=sim.TYPICAL, seed=1, fast=(FB, FB), seconds=30.0,
         follower_fails=False, scenario="realistic", setup=None):
    kw = dict(sim.SCENARIOS[scenario])
    kw.update(sweep.TIMING["v2"])
    kw.update(sweep.OPTICS[optics])
    link = sim.build_loop(P, payload, seed=seed, fast=fast, **kw)
    leader = link.leader.transfer
    follower = link.follower.transfer
    bursts, falls = [], []
    sent = leader.burst_started

    def burst_started(t_us):
        bursts.append(leader.fast_periods)
        return sent(t_us)
    leader.burst_started = burst_started
    asked = []
    ask = follower.send_sync_error

    def send_sync_error():
        asked.append(follower.now_us)
        return ask()
    follower.send_sync_error = send_sync_error
    leader.asked = asked
    if follower_fails:
        follower.decoder.feed = lambda bins, n: FB.ERR_CRC
    # When the decoder is first fed, from the burst's anchor, against the
    # burst's length on air: (fed after, length), one per burst decoded.
    released = []
    leader.released = released
    feed = follower.decoder.feed if follower.decoder is not None else None
    first = {}

    def fed(bins, n):
        key = follower.fast_anchor
        if key not in first:
            first[key] = True
            released.append((follower.now_us - follower.fast_anchor,
                             leader.fast_end - leader.fast_start))
        return feed(bins, n)
    if follower.decoder is not None:
        follower.decoder.feed = fed
    if setup is not None:
        setup(link)
    link.run(seconds)
    if follower_fails:
        return leader, bursts, falls
    assert link.violation is None, link.violation
    assert leader.result == P.OK, leader.result
    assert follower.result == P.OK, follower.result
    assert follower.received == list(payload)
    return leader, bursts, falls


def test_a_lit_pair_sends_on_f0():
    for payload in sweep.PAYLOADS:
        leader, bursts, falls = _run("lit", payload)
        assert not falls
        assert bursts and bursts[0] == FB.RUNGS[P.FAST_START_LIT], bursts


def test_the_follower_feeds_its_decoder_only_once_the_burst_is_over():
    # Training takes longer than the badge's ring holds (315-434 ms against
    # 317.7, measured-v2): fed mid-burst, the readings not yet binned were
    # overwritten and half the blocks lost. Every payload here is one
    # block, so one burst each, none NAKed.
    for payload in sweep.PAYLOADS:
        for optics in ("lit", "lit47"):
            leader, bursts, _ = _run(optics, payload)
            assert len(bursts) == 1, (optics, bursts)
            assert leader.released, "the decoder was fed"
            for after, length in leader.released:
                assert after >= length, (optics, after, length)


def test_a_dark_pair_starts_on_f1():
    leader, bursts, falls = _run("mirror")
    assert not falls
    assert bursts[0] == FB.RUNGS[P.FAST_START_DARK], bursts


def test_a_follower_that_never_decodes_walks_every_rung_then_gives_up():
    # Past F3 there is no slower carrier: the Leader stays on F3 until the
    # block's attempts are spent, and ends ERR_CRC.
    leader, bursts, _ = _run("lit", follower_fails=True, seconds=300.0)
    walked = [p for p in FB.RUNGS[P.FAST_START_LIT:]
              for _ in range(P.FAST_TRIES)]
    assert bursts[:len(walked)] == walked, bursts
    assert set(bursts[len(walked):]) <= {FB.RUNGS[-1]}
    assert len(bursts) == P.MAX_BLOCK_ATTEMPTS
    assert leader.result == P.ERR_CRC, leader.result


def test_a_follower_that_misses_a_fast_begin_asks_again():
    # Seed 13 in a once-a-cycle 60 Hz light: the Follower misses the fast
    # Begin, nothing frames, and once the burst is over -- every part of
    # RESEND_QUIET_SLOTS slots dark, not only their medians -- it asks,
    # rather than ending ERR_LOST_PEER. (Seed 24 until 2026-10-14, when
    # the timing moved.)
    leader, bursts, _ = _run("half60", seed=13, scenario="ideal")
    assert leader.asked and len(bursts) >= 2


def test_a_low_rung_bit_reads_as_zero():
    # Seed 22 at 50 Hz: the fast Begin frames, its first rung bit reads 2
    # counts over T_low and the band holds it low. That was a framing
    # failure, whose Data Sync Error went out over the burst.
    for payload in sweep.PAYLOADS:
        _run("half50", payload, seed=22, scenario="ideal")
    t = _follower()
    t.fast_bits = []
    t.in_block = True
    t.rx = [P.LOW]
    t.deciding = 0
    t.collect_block()
    assert t.fast_bits == [0], "a low is a 0, not a framing failure"


def test_a_fast_follower_checks_the_ring_before_each_block():
    for where in ("start", "reply"):
        t = HeldTransfer(False, fast=FB)
        t.levels = P.Levels()
        t.levels.fit(32, 70, 112)
        if where == "reply":
            t.start_data(0, P.EXACT_LIT_INDEX)
        t.reader.check = lambda: False
        if where == "start":
            t.start_data(0, P.EXACT_LIT_INDEX)
        else:
            t.fast_reply(True, 0)
        assert t.result == P.ERR_RING, where


def test_a_payload_of_many_blocks_retunes_after_its_first():
    # 600 bytes: five F0 blocks. The first trains, the rest retune the same
    # taps (Decoder.retune), and a NAK makes the next one train afresh.
    payload = (bytes(random.Random(7).randrange(256) for _ in range(600)),)
    kw = dict(sim.SCENARIOS["realistic"])
    kw.update(sweep.TIMING["v2"])
    kw.update(sweep.OPTICS["lit"])
    link = sim.build_loop(P, payload, seed=1, fast=(FB, FB), **kw)
    follower = link.follower.transfer
    modes = []
    reply = follower.fast_reply

    def fast_reply(ok, end):
        modes.append((follower.decoder.mode, ok))
        return reply(ok, end)
    follower.fast_reply = fast_reply
    link.run(60)
    assert link.violation is None, link.violation
    assert link.leader.transfer.result == P.OK
    assert follower.received == list(payload)
    assert modes[0][0] == "train" and len(modes) >= 5, modes
    assert sum(1 for m, _ in modes if m == "retune") >= 3, modes
    for (_, ok), (mode, _) in zip(modes, modes[1:]):
        if not ok:
            assert mode == "train", modes


def test_a_nak_makes_the_next_block_train():
    t = _follower()
    t.decoder.tuned = ("anything",)
    t.fast_reply(False, 0)
    assert t.decoder.tuned is None


# -- stepping back up

def test_acks_below_the_start_rung_step_back_up_and_a_failed_step_waits_longer():
    t = HeldTransfer(True, (b"n", b"k"), fast=FB)
    t.fast_top, t.fast_rung = 0, 2
    for _ in range(P.FAST_UP_ACKS - 1):
        t.fast_acked()
    assert t.fast_rung == 2, "not yet"
    t.fast_acked()
    assert t.fast_rung == 1 and t.fast_trial and t.fast_acks == 0
    # The rung stepped up to fails: FAST_TRIES NAKs step down, and the next
    # step up waits twice as long.
    t.send_block = lambda *a: None
    for _ in range(P.FAST_TRIES):
        t.rx = list(P.CRC_BAD_TAIL)
        t.fast_reply_rx()
    assert t.fast_rung == 2 and not t.fast_trial
    assert t.fast_up_after == 2 * P.FAST_UP_ACKS
    for _ in range(2 * P.FAST_UP_ACKS - 1):
        t.fast_acked()
    assert t.fast_rung == 2
    t.fast_acked()
    assert t.fast_rung == 1


def test_a_rung_that_holds_ends_its_trial_and_never_climbs_past_the_start():
    t = HeldTransfer(True, (b"n", b"k"), fast=FB)
    t.fast_top, t.fast_rung = 1, 2
    for _ in range(P.FAST_UP_ACKS):
        t.fast_acked()
    assert t.fast_rung == 1 and t.fast_trial
    for _ in range(P.FAST_UP_ACKS):
        t.fast_acked()
    assert t.fast_rung == 1, "the dark start is the top"
    assert not t.fast_trial, "held FAST_UP_ACKS: no longer on trial"


def test_a_pair_demoted_by_a_passing_fault_climbs_back_to_f0():
    # 1,500 bytes, about a dozen F0 blocks. The Follower's first two bursts
    # fail -- a hand across the badges -- so the Leader steps to F1; after
    # FAST_UP_ACKS there it is back on F0 for the rest.
    payload = (bytes(random.Random(3).randrange(256) for _ in range(1500)),)
    failed = []

    def build(link):
        follower = link.follower.transfer
        feed = follower.decoder.feed

        def fails_twice(bins, n):
            anchor = follower.fast_anchor
            if anchor not in failed and len(failed) < 2:
                failed.append(anchor)
            if anchor in failed:
                return FB.ERR_CRC
            return feed(bins, n)
        follower.decoder.feed = fails_twice
    leader, bursts, falls = _run("lit", payload, seconds=120.0, setup=build)
    f0, f1 = FB.RUNGS[0], FB.RUNGS[1]
    assert not falls
    assert bursts[:P.FAST_TRIES] == [f0] * P.FAST_TRIES, bursts
    assert bursts[P.FAST_TRIES:P.FAST_TRIES + P.FAST_UP_ACKS] \
        == [f1] * P.FAST_UP_ACKS, bursts
    assert set(bursts[P.FAST_TRIES + P.FAST_UP_ACKS:]) == {f0}, bursts
