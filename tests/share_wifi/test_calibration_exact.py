"""The one calibration: lengths, the fit windows, and where the stalls land.

One backlight staircase each way. The Leader's opening gap is long so the
Follower's flicker fit, which it runs inside it, ends there too; the Leader
fits at the start of its own closing gap, where it holds LOW. A fit stalls
the badge that runs it, so it must land where that badge reads only dark or
shows only LOW. See "One calibration" in the protocol.
"""

from harness import load_module

P = load_module("share_wifi", "protocol")


def test_the_staircases_are_as_long_as_the_design_says():
    for reps, body in ((P.CAL_REPS_LIT, 240), (P.CAL_REPS, 360)):
        assert reps * 3 * P.PLATEAU_MS == body
        leader = P.staircase_ms(reps, P.CAL_OPEN_LEADER_MS)
        follower = P.staircase_ms(reps, P.CAL_OPEN_MS)
        # The Follower starts CAL_CLOSE_MS - 60 into the Leader's closing gap.
        total = leader - P.CAL_CLOSE_MS + (P.CAL_CLOSE_MS - 60) + follower
        assert total == {240: 1570, 360: 1810}[body], total


def test_the_followers_fit_window_lies_in_the_leaders_dark():
    # The Leader's last crossing c bounds its gap start to [c, c + 100]; it
    # reaches the sensor dark 43 ms later (present 19, panel 24), until its
    # opening gap and first step end.
    start = P.FLICKER_FOLLOWER_FIT_START_MS
    end = start + P.FLICKER_FOLLOWER_FIT_MS
    latest_dark = P.ANNOUNCE_HALF_MS + 43
    earliest_light = P.CAL_OPEN_LEADER_MS + P.PLATEAU_MS
    assert start >= latest_dark, (start, latest_dark)
    assert end <= earliest_light
    # And a stall of the simulator's pessimistic 150 ms ends inside it too.
    assert end + 150 <= earliest_light


def _follower(exact):
    from harness import load_module as load
    reader = load("share_wifi", "reader").PolledReader()
    reader.exact = exact
    return P.Transfer(False, reader=reader)


def test_a_polled_follower_fits_once_the_staircase_must_have_ended():
    # Blind for its stall, a POLLED Follower fits the same window later:
    # when its reader ends, or by FLICKER_FOLLOWER_LATE_FIT_MS at the
    # latest, where its stall lands in the Leader's closing gap.
    t = _follower(False)
    t.phase = P.PHASE_CAL_RECV
    t.phase_started = 1000
    t.quiet_at = 900
    tried = []
    t.fit_flicker = lambda window_ms, end_us=None: tried.append(
        (window_ms, end_us)) or setattr(t, "fit_tried", True)
    window_end = 900 + P.FLICKER_FOLLOWER_FIT_START_MS + \
        P.FLICKER_FOLLOWER_FIT_MS
    t.step_cal_recv(window_end, 32)
    assert not tried, "a POLLED Follower fitted inside the Leader's gap"
    late = 900 + P.FLICKER_FOLLOWER_LATE_FIT_MS
    t.step_cal_recv(late - 1, 32)
    assert not tried
    t.step_cal_recv(late, 32)
    assert tried == [(P.FLICKER_FOLLOWER_FIT_MS, window_end * 1000)]
    # The Leader's body ends by then, dark or lit.
    assert P.FLICKER_FOLLOWER_LATE_FIT_MS >= P.ANNOUNCE_HALF_MS + \
        P.CAL_OPEN_LEADER_MS + P.CAL_REPS * 3 * P.PLATEAU_MS


def test_the_follower_fits_when_its_window_has_passed():
    t = _follower(True)
    t.phase = P.PHASE_CAL_RECV
    t.phase_started = 1000
    t.quiet_at = 900
    tried = []
    t.fit_flicker = lambda window_ms: tried.append(window_ms)
    due = 900 + P.FLICKER_FOLLOWER_FIT_START_MS + P.FLICKER_FOLLOWER_FIT_MS
    t.step_cal_recv(due - 1, 32)
    assert not tried
    t.step_cal_recv(due, 32)
    assert tried == [P.FLICKER_FOLLOWER_FIT_MS]


def test_the_leader_fits_as_its_closing_gap_begins():
    t = P.Transfer(True, (b"n", b"k"))
    t.phase = P.PHASE_CAL_SEND
    t.phase_started = 0
    tried = []
    t.fit_flicker = lambda window_ms: tried.append(window_ms) or \
        setattr(t, "fit_tried", True)
    body_end = P.CAL_OPEN_LEADER_MS + P.CAL_REPS * 3 * P.PLATEAU_MS
    assert t.step_cal_send(body_end - 4, 32) != P.LOW or not tried
    assert not tried
    assert t.step_cal_send(body_end, 32) == P.LOW
    assert tried == [P.FLICKER_FIT_MS]
    # The window, the body's last FLICKER_FIT_MS, starts after the Follower
    # has gone dark: quiet at 130 ms, then its present and panel.
    assert body_end - P.FLICKER_FIT_MS > P.ANNOUNCE_HALF_MS + 30 + 45


def test_collections_are_asked_for_where_nothing_is_sent():
    follower = P.Transfer(False)
    follower.phase = P.PHASE_CAL_SEND
    follower.phase_started = 0
    body_end = P.CAL_OPEN_MS + P.CAL_REPS * 3 * P.PLATEAU_MS
    follower.step_cal_send(body_end - 4, 32)
    assert not follower.collect_due
    assert follower.step_cal_send(body_end, 32) == P.LOW
    assert follower.collect_due, "the Follower collects in its closing gap"


def _read_leader_staircase(sparse):
    """A Follower reading a clean, dark, ascending Leader staircase: through
    step() a reading every CAL_SAMPLE_MS (the tests' sparse path), or as the
    app reads -- a POLLED reading every 400 us and work() every ms."""
    from harness import load_module as load
    R = load("share_wifi", "reader")
    reader = R.PolledReader()
    reader.start()
    t = P.Transfer(False, reader=reader)
    t.phase = P.PHASE_CAL_RECV
    t.phase_started = 0
    level = {P.LOW: 32, P.MID: 64, P.HIGH: 112}

    def light(ms):
        return level[P.staircase_field(ms, P.ASCENDING, P.CAL_REPS,
                                       P.CAL_OPEN_LEADER_MS)]
    if sparse:
        now = 0
        while t.phase == P.PHASE_CAL_RECV and now < 5000:
            t.step(now, light(now))
            now += P.CAL_SAMPLE_MS
    else:
        us = 0
        while t.phase == P.PHASE_CAL_RECV and us < 5000000:
            reader.add(us, light(us // 1000))
            if us % 1000 == 0:
                t.work(us)
            us += 400
    assert t.phase == P.PHASE_CAL_SEND, (sparse, t.result)
    return t


def test_the_step_shim_reads_a_staircase_as_the_app_does():
    # step() pools differently (see Transfer.sparse); this is the check that
    # the difference changes nothing a calibration decides.
    shim = _read_leader_staircase(True)
    app = _read_leader_staircase(False)
    for name in ("black", "grey", "white"):
        assert abs(getattr(shim.levels, name) - getattr(app.levels, name)) <= 2, \
            (name, getattr(shim.levels, name), getattr(app.levels, name))
    assert (shim.peer_exact, shim.peer_lit) == (app.peer_exact, app.peer_lit)
    assert abs(shim.reader_cal.ended_at - app.reader_cal.ended_at) \
        <= P.CAL_SAMPLE_MS + 1, (shim.reader_cal.ended_at,
                                 app.reader_cal.ended_at)
