"""Agreeing a rung: the reader tier and the light, carried by the staircases.

Each badge reads the sensor through the DMA ring (EXACT) or light_level()
(POLLED), and is LIT or not. Where a pair starts depends on both badges'
flags, so the staircases carry them with no message of their own: the order
is the tier -- REVERSED for EXACT -- and the repetitions the light. The
Leader's staircase carries its own flags; the Follower's carries the pair's,
which the Leader adopts. Only the Follower decides, so a Follower misreading
the Leader moves the pair as a whole, and only a Leader misreading the
Follower can split it. See "Agreeing a rung" in the protocol.
"""

import simulator as sim
from harness import load_module

P = load_module("share_wifi", "protocol")
R = load_module("share_wifi", "reader")


def staircase_samples(order=P.ASCENDING, reps=P.CAL_REPS, drop_highs=0,
                      open_ms=P.CAL_OPEN_MS):
    """The values a reader would take from a clean staircase, one every
    CAL_SAMPLE_MS. `drop_highs` holds the middle through that many high
    steps, as a noisy read could miss them."""
    level = {P.LOW: 100, P.MID: 400, P.HIGH: 900}
    values = []
    dropped = 0
    last = None
    dropping = False
    t = 0
    while t < P.staircase_ms(reps, open_ms):
        field = P.staircase_field(t, order, reps, open_ms)
        if field == P.HIGH and last != P.HIGH:
            dropping = dropped < drop_highs
            if dropping:
                dropped += 1
        last = field
        if field == P.HIGH and dropping:
            field = P.MID
        values.append(level[field])
        t += P.CAL_SAMPLE_MS
    return values


def levels_for(values):
    reader = P.StaircaseReader()
    reader.values = list(values)
    levels = reader.levels()
    assert levels is not None
    return levels


def flags(order, reps):
    values = staircase_samples(order, reps)
    rising, falling, highs = P.staircase_steps(values, levels_for(values))
    return falling > rising, highs <= P.LIT_STEPS_MAX


def test_each_staircase_reads_back_its_tier_and_light():
    for order, exact in ((P.ASCENDING, False), (P.REVERSED, True)):
        for reps, lit in ((P.CAL_REPS, False), (P.CAL_REPS_LIT, True)):
            assert flags(order, reps) == (exact, lit), (order, reps)


def test_the_levels_come_from_the_body_not_the_long_gaps():
    # 40 ms steps between 250-400 ms gaps: without the trim the held high
    # level is under the 0.90 quantile and white reads as the middle.
    for open_ms in (P.CAL_OPEN_MS, P.CAL_OPEN_LEADER_MS):
        values = staircase_samples(P.ASCENDING, P.CAL_REPS_LIT, 0, open_ms)
        reader = P.StaircaseReader()
        for k, value in enumerate(values):
            reader.feed(k * P.CAL_SAMPLE_MS, value)
        levels = reader.levels()
        assert levels is not None
        assert abs(levels.white - 900) <= 50, levels.white
        assert abs(levels.grey - 400) <= 50, levels.grey


def calibrated_follower(own_exact, own_lit_black, leader_order, leader_reps):
    """A Follower that has just read a Leader's staircase."""
    reader = R.PolledReader()
    reader.exact = own_exact
    t = P.Transfer(False, reader=reader)
    t.phase = P.PHASE_CAL_RECV
    t.phase_started = 0
    black = own_lit_black
    level = {P.LOW: black, P.MID: black + 300, P.HIGH: black + 800}
    now = 0
    while t.phase == P.PHASE_CAL_RECV:
        field = P.staircase_field(now, leader_order, leader_reps,
                                  P.CAL_OPEN_LEADER_MS)
        t.step_cal_recv(now, level[field])
        now += P.CAL_SAMPLE_MS
        assert now < 5000, "never ended"
    return t


def test_the_follower_decides_the_pair():
    for own_exact in (True, False):
        for own_black, own_lit in ((32, False), (96, True)):
            for order, peer_exact in ((P.ASCENDING, False),
                                      (P.REVERSED, True)):
                for reps, peer_lit in ((P.CAL_REPS, False),
                                       (P.CAL_REPS_LIT, True)):
                    t = calibrated_follower(own_exact, own_black, order, reps)
                    assert t.phase == P.PHASE_CAL_SEND, t.result
                    assert (t.peer_exact, t.peer_lit) == (peer_exact, peer_lit)
                    assert t.pair_exact == (own_exact and peer_exact)
                    assert t.pair_lit == (own_lit and peer_lit)
                    # ...and its staircase says so.
                    assert t.cal_order() == (P.REVERSED if t.pair_exact
                                             else P.ASCENDING)
                    assert t.cal_reps() == (P.CAL_REPS_LIT if t.pair_lit
                                            else P.CAL_REPS)


def test_the_leader_adopts_the_followers_flags_as_read():
    # An EXACT, LIT Leader reading a POLLED, dark pair from the Follower
    # starts where the Follower does, not where its own flags would.
    reader = R.PolledReader()
    reader.exact = True
    t = P.Transfer(True, (b"n", b"k"), reader=reader)
    t.lit = True
    t.phase = P.PHASE_CAL_RECV
    t.phase_started = 0
    level = {P.LOW: 100, P.MID: 400, P.HIGH: 900}
    now = 0
    while t.phase == P.PHASE_CAL_RECV:
        t.step_cal_recv(now, level[P.staircase_field(
            now, P.ASCENDING, P.CAL_REPS, P.CAL_OPEN_MS)])
        now += P.CAL_SAMPLE_MS
    assert not t.pair_exact and not t.pair_lit
    assert P.SLOT_LADDER[t.start_index()] == 32


def test_a_missed_high_step_reads_dark_as_lit():
    # The risk the Follower's deciding contains: a DARK staircase with one
    # high step missed has two, and reads as LIT. Read by the Follower, the
    # pair moves together; read by the Leader, it splits.
    values = staircase_samples(P.ASCENDING, P.CAL_REPS, drop_highs=1)
    _, _, highs = P.staircase_steps(values, levels_for(values))
    assert highs <= P.LIT_STEPS_MAX


def test_where_each_pair_starts():
    assert P.SLOT_LADDER[P.EXACT_LIT_INDEX] == 8
    assert P.SLOT_LADDER[P.EXACT_INDEX] == 16
    assert P.SLOT_LADDER[P.BACKLIGHT_LIT_INDEX] == 16
    assert P.SLOT_LADDER[P.BACKLIGHT_INDEX] == 32
    assert P.SLOT_LADDER[P.PROVEN_INDEX] == 50


def test_mixed_pairs_meet_on_the_polled_rung_and_deliver():
    for leader_reader, follower_reader in (("ring", "polled"),
                                           ("polled", "ring")):
        kw = dict(sim.SCENARIOS["realistic"])
        kw.update(sim.LOOP_V2)
        kw.update(sim.LOOP_SENSOR_V2, contrast=sim.SWING_MIRROR)
        link = sim.build_loop(P, sim.TYPICAL, seed=1, reader=leader_reader,
                              peer_reader=follower_reader, **kw)
        link.run(300)
        leader, follower = link.leader.transfer, link.follower.transfer
        assert leader.result == P.OK, leader.result
        assert leader.clock.index == follower.clock.index == P.BACKLIGHT_INDEX
