"""The short handshake every pair runs: one beacon, one staircase.

A Leader announces at ANNOUNCE_FAST_HALF_MS in SCHEME_BEACON and reads for
a staircase while it does. A Follower holds black until it sees the
Announce, then sends its staircase at once: its repetitions carry the
pair's light -- the Follower's own -- which the Leader adopts. The Leader
fits the flicker on the Follower's closing gap and decides the hold itself;
the Follower fits later in the gap and trains its levels on each Begin
Block. See sections 8 and 9 of the protocol spec.
"""

import simulator as sim
from harness import load_module
from held import HeldTransfer

P = load_module("share_wifi", "protocol")

LEVEL = {P.LOW: 100, P.MID: 400, P.HIGH: 900}


def transfer(leader):
    return HeldTransfer(leader, [b"x"] if leader else None)


def beacon(detector, half, start=0, until=None):
    """Pushes coarse samples of a black-and-white beacon of `half` ms."""
    t = start
    until = until or start + 12 * half
    while t < until:
        detector.push(t, 300 if (t // half) % 2 else 100)
        t += P.FINE_MS
    return t


def test_the_announce_is_clear_of_mains_flicker():
    """Its tolerance band, 11.2-16.8 ms, is clear of 100 and 120 Hz
    flicker's halves and a once-a-cycle light's, 8.3-10 ms."""
    lo = P.ANNOUNCE_FAST_HALF_MS * (1 - P.BEACON_TOLERANCE)
    assert lo > 1000 / 50 / 2


def test_the_announce_is_found_and_other_periods_are_not():
    d = P.BeaconDetector()
    beacon(d, P.ANNOUNCE_FAST_HALF_MS, 0, 600)
    assert d.matches(P.ANNOUNCE_FAST_HALF_MS)
    for half in (5, 8, 10, 22, 40):
        d = P.BeaconDetector()
        beacon(d, half, 0, 600)
        assert not d.matches(P.ANNOUNCE_FAST_HALF_MS), half


def test_the_detector_reads_the_beacons_black_and_full():
    d = P.BeaconDetector()
    beacon(d, P.ANNOUNCE_FAST_HALF_MS, 0, 300)
    assert (d.low(), d.high()) == (100, 300)


def test_noise_is_rarely_a_beacon():
    """Noise crosses its middle about as often as a beacon does; only
    regular crossings match. Noise past MIN_SWING in 2 ms samples is a
    stress, not the badge: a ring's are ~1 count."""
    import random
    hits = total = 0
    for seed in range(100):
        rng = random.Random(seed)
        d = P.BeaconDetector()
        for k in range(300):
            d.push(k * P.FINE_MS, 200 + rng.randint(-80, 80))
            total += 1
            if d.matches(P.ANNOUNCE_FAST_HALF_MS):
                hits += 1
    assert hits < total * 0.002, (hits, total)


def test_the_leader_announces_and_the_follower_holds_black():
    leader, follower = transfer(True), transfer(False)
    for t in (leader, follower):
        assert t.scheme == P.SCHEME_BEACON
        assert P.LEVEL_SCHEMES[t.scheme][P.LOW] == P.BEACON_LOW_BACKLIGHT
    assert leader.field_at(0) == P.LOW
    assert leader.field_at(P.ANNOUNCE_FAST_HALF_MS * 1000) == P.HIGH
    assert follower.phase == P.PHASE_LISTEN
    for t_ms in (0, 14, 28, 500):
        assert follower.field_at(t_ms * 1000) == P.LOW


def follower_after(half, black=100, white=300):
    t = transfer(False)
    now = 0
    while t.phase == P.PHASE_LISTEN and now < 2000:
        t.step_listen(now, white if (now // half) % 2 else black)
        now += P.FINE_MS
    return t


def test_the_follower_answers_the_announce_with_its_staircase():
    t = follower_after(P.ANNOUNCE_FAST_HALF_MS)
    assert t.phase == P.PHASE_CAL_SEND
    assert t.scheme == P.SCHEME_BACKLIGHT
    for half in (22, 40, 100):
        assert follower_after(half).phase == P.PHASE_LISTEN, half


def test_the_follower_decides_the_pairs_light_from_the_announce():
    for black, lit in ((32, False), (63, False), (64, True), (96, True)):
        t = follower_after(P.ANNOUNCE_FAST_HALF_MS, black, black + 80)
        assert t.lit == t.pair_lit == lit, black
        assert t.cal_reps() == (P.CAL_REPS_FAST_LIT if lit
                                else P.CAL_REPS_FAST)


def test_the_followers_provisional_levels():
    t = follower_after(P.ANNOUNCE_FAST_HALF_MS, 100, 300)
    levels = t.levels
    assert (levels.black, levels.white) == (100, 300)
    assert levels.grey == 100 + 200 * P.PROVISIONAL_GREY_PERCENT // 100


def staircase(lit, levels=(100, 400, 900)):
    reps = P.CAL_REPS_FAST_LIT if lit else P.CAL_REPS_FAST
    level = dict(zip((P.LOW, P.MID, P.HIGH), levels))
    reader = P.StaircaseReader()
    t = 0
    while not reader.feed(t, level[P.staircase_field(
            t, P.STAIRCASE, reps, P.CAL_OPEN_FAST_MS, P.PLATEAU_FAST_MS)]):
        t += P.FINE_MS
        assert t < 2000
    return reader, t


def test_a_staircase_reads_back_its_levels_and_light():
    for lit in (True, False):
        reader, _ = staircase(lit)
        levels = reader.levels()
        assert levels is not None, lit
        assert (levels.black, levels.grey, levels.white) == (100, 400, 900)
        highs = P.staircase_highs(reader.values, levels)
        assert (highs <= P.LIT_STEPS_MAX_FAST) == lit


def test_a_closing_gap_is_found_before_it_ends():
    for lit in (True, False):
        reps = P.CAL_REPS_FAST_LIT if lit else P.CAL_REPS_FAST
        reader, t = staircase(lit)
        body_end = P.CAL_OPEN_FAST_MS + reps * 3 * P.PLATEAU_FAST_MS
        assert reader.ended_at - body_end <= P.FINE_MS
        assert t < body_end + P.CAL_CLOSE_FAST_MS


def test_the_close_outlasts_every_dark_it_could_be_taken_for():
    found = P.CAL_CLOSE_FAST_MS - P.CAL_CLOSE_FAST_SPARE_MS
    # A LOW step, and the announce's dark half, are shorter.
    assert found > 2 * P.PLATEAU_FAST_MS
    assert found > P.ANNOUNCE_FAST_HALF_MS * (1 + P.BEACON_TOLERANCE)


def leader_reading(lit, levels=(100, 400, 900), before=60):
    """A Leader announcing, reading a Follower's staircase that starts
    `before` ms in, then stepped through the gap until it settles."""
    t = transfer(True)
    reps = P.CAL_REPS_FAST_LIT if lit else P.CAL_REPS_FAST
    level = dict(zip((P.LOW, P.MID, P.HIGH), levels))
    now = 0
    while t.phase == P.PHASE_ANNOUNCE and now < 3000:
        e = now - before
        field = P.LOW if e < 0 else P.staircase_field(
            e, P.STAIRCASE, reps, P.CAL_OPEN_FAST_MS, P.PLATEAU_FAST_MS)
        t.step_announce(now, level[field])
        now += P.FINE_MS
    while t.phase == P.PHASE_CAL_GAP and t.levels is None and now < 3000:
        t.step_cal_gap(now, level[P.LOW])
        now += P.FINE_MS
    return t


def test_the_leader_reads_the_staircase_while_announcing():
    for lit in (True, False):
        t = leader_reading(lit)
        assert t.phase == P.PHASE_CAL_GAP
        assert t.scheme == P.SCHEME_BACKLIGHT
        levels = t.levels
        assert (levels.black, levels.grey, levels.white) == (100, 400, 900)
        assert t.pair_lit == lit
        reps = P.CAL_REPS_FAST_LIT if lit else P.CAL_REPS_FAST
        gap = 60 + P.CAL_OPEN_FAST_MS + reps * 3 * P.PLATEAU_FAST_MS
        assert t.epoch == gap + P.CAL_GAP_FAST_MS, (t.epoch, gap)


def test_the_leader_adopts_the_followers_light_whatever_its_own():
    # A Leader in a lit room (black 100) reading a dark pair's staircase
    # starts where the Follower does: on 16 ms.
    t = leader_reading(False, (100, 400, 900))
    assert not t.pair_lit and t.lit
    assert P.SLOT_LADDER[t.start_index()] == 16


def test_what_is_not_a_staircase_sends_the_leader_back_to_announcing():
    # Two levels, gapped like a staircase: no middle to read.
    t = leader_reading(False, (100, 100, 900))
    assert t.phase == P.PHASE_ANNOUNCE
    assert t.scheme == P.SCHEME_BEACON


def test_the_staircase_has_one_order_and_no_flag_but_its_light():
    """One order, low, high, middle: every Follower decodes fast blocks, so
    the order carries no flag."""
    t = follower_after(P.ANNOUNCE_FAST_HALF_MS)
    t.cal_pending = False
    seen = [t.field_at((t.phase_started + ms) * 1000)
            for ms in range(P.CAL_OPEN_FAST_MS, P.CAL_OPEN_FAST_MS + 60, 20)]
    assert seen == list(P.STAIRCASE) == [P.LOW, P.HIGH, P.MID]
    for name in ("PRESENT_FAST_HALF_MS", "PHASE_PRESENT",
                 "PHASE_CAL_RECV", "CAL_OPEN_LEADER_FAST_MS",
                 "FLICKER_FAST_LEADER_FIT_MS", "FLICKER_FAST_FIT_START_MS",
                 "CAL_RECV_MAX_MS", "QUIET_SPARE_MS",
                 "FAST_HANDSHAKE", "CAL_REPS", "PLATEAU_MS", "ASCENDING",
                 "REVERSED", "BEGIN_BLOCK", "PROVEN_INDEX"):
        assert not hasattr(P, name), name
    for name in ("exact", "pair_exact", "peer_lit", "saw_announce",
                 "quiet_at", "pair_fast", "cal_order"):
        assert not hasattr(t, name), name


def test_a_missed_high_step_reads_dark_as_lit():
    # The risk the Follower's deciding contains: a dark staircase with one
    # high step missed reads as LIT, and the Leader starts the pair faster
    # than the Follower's light allows: the first block demotes.
    values = []
    dropping = False
    for t in range(0, P.staircase_ms(P.CAL_REPS_FAST, P.CAL_OPEN_FAST_MS,
                                     P.PLATEAU_FAST_MS, P.CAL_CLOSE_FAST_MS),
                   P.FINE_MS):
        field = P.staircase_field(t, P.STAIRCASE, P.CAL_REPS_FAST,
                                  P.CAL_OPEN_FAST_MS, P.PLATEAU_FAST_MS)
        if field == P.HIGH and t < 100:
            dropping = True
        elif field != P.HIGH:
            dropping = False
        values.append(LEVEL[P.MID if dropping else field])
    levels = P.Levels()
    levels.fit(LEVEL[P.LOW], LEVEL[P.MID], LEVEL[P.HIGH])
    highs = P.staircase_highs(values, levels)
    assert highs <= P.LIT_STEPS_MAX_FAST


def test_a_staircase_starts_on_the_pass_after():
    t = transfer(False)
    t.pass_ms = 5000
    t.enter(P.PHASE_CAL_SEND, 4800)
    assert t.cal_pending and t.field_at(5000 * 1000) == P.LOW
    t.reader.newest_us = lambda: 0
    t.work(5040 * 1000)
    assert not t.cal_pending and t.phase_started == 5040


def test_where_each_pair_starts():
    assert P.SLOT_LADDER[P.EXACT_LIT_INDEX] == 8
    assert P.SLOT_LADDER[P.EXACT_INDEX] == 16
    assert P.SLOT_LADDER[0] == 32, "three demotions below the dark start"


def test_the_gap_holds_both_fits():
    """The Leader settles before the epoch. The Follower's window starts
    after the Leader has found the gap -- 50 ms in, half a smoothing window
    late -- and stopped announcing, and its steady check ends before the
    earliest Begin, a lead-in after the epoch on the 8 ms rung."""
    found = P.CAL_CLOSE_FAST_MS - P.CAL_CLOSE_FAST_SPARE_MS
    assert P.LEADER_SETTLE_MS < P.CAL_GAP_FAST_MS
    smooth = P.LEADER_CAL_SMOOTH * P.FINE_MS // 2
    assert P.FOLLOWER_FIT_START_MS >= found + smooth + 5
    earliest_begin = P.CAL_GAP_FAST_MS + P.RESYNC_LEAD_IN * 8
    assert P.FOLLOWER_FIT_START_MS + P.FOLLOWER_CHECK_MS <= earliest_begin
    assert P.FOLLOWER_FIT_START_MS + P.FLICKER_FAST_FIT_MS \
        <= P.CAL_GAP_FAST_FLICKER_MS


def test_a_leader_in_flicker_holds_its_first_block_back():
    for flicker in (False, True):
        t = transfer(True)
        t.pair_flicker = flicker
        t.pair_lit = True
        t.start_data(1000, P.EXACT_LIT_INDEX)
        lead = 0
        while t.tx[lead] == P.LOW:
            lead += 1
        held = t.hold_slots() if flicker else 0
        assert lead == P.RESYNC_LEAD_IN + held
        assert held * P.SLOT_LADDER[P.EXACT_LIT_INDEX] >= \
            P.CAL_GAP_FAST_FLICKER_MS - P.CAL_GAP_FAST_MS or not flicker


def test_a_late_leader_trims_its_hold_not_its_block():
    """Late to the data phase -- its fit's stall in the gap -- the Leader
    starts its first block where the epoch puts it, the hold shortened."""
    t = transfer(True)
    t.pair_flicker = True
    t.pair_lit = True
    t.start_data(1000, P.EXACT_LIT_INDEX)
    d = P.SLOT_LADDER[P.EXACT_LIT_INDEX] * 1000
    # On time, the queue starts on slot 1: the Begin on 1 + lead-in + hold.
    begin = 1 + P.RESYNC_LEAD_IN + t.hold_slots()
    late = 10                       # slots past the epoch
    t.data_field(1000 * 1000 + late * d + 1)
    assert t.tx_slot == late + 1
    assert t.tx_slot + t.tx.index(P.HIGH) == begin


def test_a_follower_always_waits_out_the_hold():
    t = transfer(False)
    t.start_data(1000, P.EXACT_LIT_INDEX)
    d = P.SLOT_LADDER[P.EXACT_LIT_INDEX]
    assert t.idle_grace == t.start_grace == -t.stall_slots
    assert (t.idle_grace * d >= P.CAL_GAP_FAST_FLICKER_MS - P.CAL_GAP_FAST_MS
            + P.FAST_START_GRACE_MS)


def data_follower(levels=(100, 300, 500)):
    t = transfer(False)
    t.start_data(0, P.EXACT_LIT_INDEX)
    t.levels = P.Levels()
    t.levels.fit(*levels)
    return t


def push_values(t, values):
    for v in values:
        t.rx_values[t.rx_n % 9] = v
        t.rx_n += 1


def test_a_follower_trains_its_levels_on_a_begin():
    t = data_follower((100, 190, 300))      # provisional: the middle a guess
    # FAST_BEGIN, "-_ -_ _- ".
    push_values(t, [500, 302, 104, 498, 298, 101, 301, 503, 99])
    levels = t.begin_levels()
    assert levels is not None
    # Black stays the dark measured before; high and middle are trained.
    assert (levels.black, levels.grey, levels.white) == (100, 301, 500)


def test_training_refuses_what_is_not_a_begin():
    t = data_follower()
    for values in ([500, 500, 104, 500, 500, 101, 500, 500, 99],   # no middle
                   [500, 300, 300, 500, 300, 300, 500, 300, 300],  # no low
                   [300, 500, 104, 300, 500, 101, 300, 500, 99],   # order
                   [500, 110, 104, 500, 110, 101, 500, 110, 99],   # middle at black
                   [500, 480, 104, 500, 480, 101, 500, 480, 99]):  # middle at high
        push_values(t, values)
        assert t.begin_levels() is None, values


def test_training_allocates_nothing():
    """begin_levels() runs every slot a Follower decides low outside a
    block: comparisons and a fixed ring, no list or tuple built."""
    import ast
    import inspect
    import textwrap
    source = textwrap.dedent(inspect.getsource(P.Transfer.begin_levels))
    for node in ast.walk(ast.parse(source)):
        assert not isinstance(node, (ast.List, ast.ListComp, ast.Tuple,
                                     ast.GeneratorExp)) or \
            isinstance(getattr(node, "ctx", None), ast.Store), \
            ast.dump(node)[:60]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in ("sorted", "min", "max", "list")


def test_the_follower_decides_no_slot_before_its_fit():
    t = data_follower()
    t.fit_due = 50 * 1000
    t.reader.newest_us = lambda: 100 * 1000
    decided = []
    t.decide_slot = decided.append
    t.work_data(100 * 1000)
    assert not decided and t.fit_due is not None
    assert t.next >= t.clock.number(100 * 1000)


def test_in_flicker_light_before_the_hold_ends_is_not_taken():
    t = data_follower()
    d = t.clock.duration
    t.hold_until = t.clock.start_of(20)
    t.window_value = lambda k: 400          # lit, as a flicker peak reads
    t.can_phase_lock = lambda: True
    locked = []
    t.try_lock = lambda k: locked.append(k) or False
    t.decide_slot(5)
    assert not locked and t.rx[-1] == P.LOW
    t.decide_slot(20)
    assert locked == [20]
    assert d


def build(optics, seed=1):
    kw = dict(sim.SCENARIOS["realistic"])
    kw.update(sim.LOOP_V2)
    kw.update(sim.LOOP_SENSOR_V2, contrast=sim.SWING_MIRROR)
    kw.update(optics)
    return sim.build_loop(P, sim.TYPICAL, seed=seed, **kw)


def test_a_pair_calibrates_end_to_end():
    for optics, rung, flicker in (({}, 16, False),
                                  ({"room_light": 48}, 8, False),
                                  ({"room_light": 48, "flicker": 0.47}, 8,
                                   True)):
        link = build(optics)
        link.run(30)
        link.assert_invariants()
        leader, follower = link.leader.transfer, link.follower.transfer
        assert leader.result == P.OK and follower.result == P.OK, optics
        assert leader.pair_flicker == flicker
        assert (follower.flicker.harmonics is not None) == flicker
        assert P.SLOT_LADDER[leader.clock.index] == rung


def test_a_hold_the_follower_did_not_expect_costs_a_wait():
    """The Leader decides the hold from its own fit. One that holds in a
    room the Follower found steady still delivers. One that sends at once
    in a room the Follower found flickering can be missed whole: the
    Follower takes no light before the hold it expects. Then the Leader
    sends its first block again after its reply wait, and the Follower,
    which waits that out before its first Begin, takes it -- seed 1 ended
    ERR_LOST_PEER until 2026-10-19."""
    for optics, flip, seeds in (({"room_light": 48}, True, (1, 2)),
                                ({"room_light": 48, "flicker": 0.47}, False,
                                 (1, 2))):
        for seed in seeds:
            link = build(optics, seed)
            leader = link.leader.transfer
            real = leader.settle_staircase

            def settle(now, real=real, leader=leader, flip=flip):
                real(now)
                leader.pair_flicker = flip
            leader.settle_staircase = settle
            link.run(30)
            link.assert_invariants()
            assert leader.result == P.OK, (optics, seed)
            assert link.follower.transfer.result == P.OK, (optics, seed)


def test_a_follower_that_found_flicker_waits_for_a_resend_until_a_begin():
    """first_grace covers a Leader's reply wait and the resend's lead-in,
    from the Follower's fit until it frames a Begin -- not until its first
    light, which may be the tail of the burst it missed. A Follower that
    found the room steady never needs it."""
    for optics, flicker in (({"room_light": 48, "flicker": 0.47}, True),
                            ({"room_light": 48}, False)):
        link = build(optics)
        follower = link.follower.transfer
        fast = follower.fast
        graces = []
        real = follower.decide_slot

        def decide(k, real=real, follower=follower, graces=graces):
            graces.append(follower.first_grace)
            return real(k)
        follower.decide_slot = decide
        link.run(30)
        assert follower.result == P.OK, optics
        assert graces[-1] == 0
        if flicker:
            wait = P.FAST_REPLY_MS * 1000 + P.burst_max_us(
                fast, fast.RUNGS[P.FAST_START_LIT])
            assert max(graces) * follower.clock.duration > wait, max(graces)
        else:
            assert max(graces) == 0


def test_the_start_grace_ends_at_the_first_light_and_a_later_split_is_seen():
    """start_grace widens the simulator's I2 window only until the Follower
    first decides a lit slot; a split after the first block is reported."""
    link = build({"room_light": 48, "flicker": 0.47})
    follower = link.follower.transfer
    leader = link.leader.transfer
    graces = []
    real = follower.decide_slot

    def decide(k):
        out = real(k)
        graces.append((follower.blocks_accepted, follower.start_grace))
        return out
    follower.decide_slot = decide
    while link.now < 30000 and not follower.blocks_accepted:
        link.run((link.now + 50) / 1000.0)
    assert follower.blocks_accepted
    assert graces[0][1] > 0 and follower.start_grace == 0
    # Shift the Leader's grid by half a slot: I2 must report it within
    # LOST_PEER_SLOTS, not LOST_PEER_SLOTS + the old grace.
    leader.clock.epoch += leader.clock.duration // 2
    before = link.now
    leader.rebase = lambda boundary, crossing: None
    follower.rebase = lambda boundary, crossing: None
    while link.violation is None and link.now < before + 3000 \
            and not (leader.result and follower.result):
        link.run((link.now + 50) / 1000.0)
    assert link.violation and link.violation.startswith("I2"), link.violation
    window = P.LOST_PEER_SLOTS * leader.clock.duration / 1000.0
    assert link.now - before < 2 * window + 100, link.now - before


def test_a_refused_staircase_is_announced_again_then_ends_err_contrast():
    t = leader_reading(False, (100, 100, 900))
    assert t.phase == P.PHASE_ANNOUNCE and t.cal_refusals == 1
    # A second refusal is past CAL_RESTARTS.
    now = 3000
    level = {P.LOW: 100, P.MID: 100, P.HIGH: 900}
    while t.result is None and now < 6000:
        e = now - 3060
        field = P.LOW if e < 0 else P.staircase_field(
            e, P.STAIRCASE, P.CAL_REPS_FAST, P.CAL_OPEN_FAST_MS,
            P.PLATEAU_FAST_MS)
        if t.phase == P.PHASE_ANNOUNCE:
            t.step_announce(now, level[field])
        else:
            t.step_cal_gap(now, level[P.LOW])
        now += P.FINE_MS
    assert t.result == P.ERR_CONTRAST


def test_a_pair_that_cannot_separate_the_middle_ends_err_contrast_both_ways():
    """The data's middle at the low: the Leader refuses the staircase and
    announces again; the Follower sees that, sends another, and both end
    ERR_CONTRAST in seconds -- not ERR_NO_PEER after the minute's timeout."""
    table = P.LEVEL_SCHEMES[P.SCHEME_BACKLIGHT]
    middle = table[P.MID]
    table[P.MID] = 0.52
    try:
        link = build({})
        elapsed = link.run(20)
    finally:
        table[P.MID] = middle
    leader, follower = link.leader.transfer, link.follower.transfer
    assert leader.result == follower.result == P.ERR_CONTRAST, \
        (leader.result, follower.result)
    assert leader.cal_refusals == 2 and follower.cal_refusals == 1
    assert elapsed < 10


def test_a_complete_before_any_block_is_the_announce_not_complete():
    t = data_follower()
    for symbol in P.COMPLETE[:-1]:
        t.rx.append(symbol)
    t.follower_rx()
    assert t.sub == t.STEP_BLOCK and not t.tx
