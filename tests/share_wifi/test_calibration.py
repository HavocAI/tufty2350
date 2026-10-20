"""The calibration: one staircase, the fit windows, and where the stalls
land.

The Follower sends the only staircase. The Leader reads it while it
announces and fits the flicker on its closing gap; the Follower fits later
in the gap, from the ring. Each stall runs while that badge only reads, so
the ring reads through it. See sections 8 and 9 of the protocol spec.
"""

from harness import load_module
from held import HeldTransfer
import held

P = load_module("share_wifi", "protocol")
R = load_module("share_wifi", "reader")


def staircase(reps):
    return P.staircase_ms(reps, P.CAL_OPEN_FAST_MS, P.PLATEAU_FAST_MS,
                          P.CAL_GAP_FAST_MS)


def test_the_staircase_is_as_long_as_the_design_says():
    for reps, body in ((P.CAL_REPS_FAST_LIT, 60), (P.CAL_REPS_FAST, 120)):
        assert reps * 3 * P.PLATEAU_FAST_MS == body
        # From detecting the Announce to the epoch.
        assert staircase(reps) == {60: 210, 120: 270}[body]


def test_the_leader_settles_in_the_gap():
    t = HeldTransfer(True, (b"n", b"k"))
    t.phase = P.PHASE_CAL_GAP
    t.cal_ended = 1000
    t.epoch = 1000 + P.CAL_GAP_FAST_MS
    t.levels = None
    settled = []
    t.settle_staircase = lambda now: settled.append(now)
    t.step_cal_gap(1000 + P.LEADER_SETTLE_MS - 2, 32)
    assert not settled
    t.step_cal_gap(1000 + P.LEADER_SETTLE_MS, 32)
    assert settled == [1000 + P.LEADER_SETTLE_MS]


def test_the_leaders_fit_window_is_the_followers_gap():
    t = HeldTransfer(True, (b"n", b"k"))
    t.cal_ended = 1000
    windows = []

    def fit(window_ms, end_us=None):
        windows.append((window_ms, end_us))
        t.fit_tried = True
    t.fit_flicker = fit
    t.settle_staircase(1100)
    span = P.LEADER_SETTLE_MS - P.LEADER_FIT_SKIP_MS
    assert windows == [(span, (1000 + P.LEADER_SETTLE_MS) * 1000)]
    # It starts LEADER_FIT_SKIP_MS into the gap, after the last fall.
    assert (1000 + P.LEADER_SETTLE_MS) - span == 1000 + P.LEADER_FIT_SKIP_MS


def test_the_follower_fits_when_its_window_has_passed():
    reader = held.HeldReader()
    reader.start()
    t = HeldTransfer(False, reader=reader)
    t.start_data(0, P.EXACT_LIT_INDEX)
    t.levels = P.Levels()
    t.levels.fit(32, 70, 112)
    t.fit_due = 10 * 1000
    for ms in range(0, 300):
        reader.add(ms * 1000, 32)
    check_end = t.fit_due + P.FOLLOWER_CHECK_MS * 1000
    t.follower_fit(check_end - 1)
    assert not t.fit_checked
    t.follower_fit(check_end)
    assert t.fit_checked and t.fit_result == "steady" and t.fit_due is None
    # The check's median is the Leader's data low: the provisional black.
    assert t.levels.black == 32


def test_collections_are_asked_for_where_nothing_is_sent():
    follower = HeldTransfer(False)
    follower.phase = P.PHASE_CAL_SEND
    follower.phase_started = 0
    body_end = P.CAL_OPEN_FAST_MS + P.CAL_REPS_FAST * 3 * P.PLATEAU_FAST_MS
    follower.step_cal_send(body_end - 2, 32)
    assert not follower.collect_due
    assert follower.step_cal_send(body_end, 32) == P.LOW
    assert follower.collect_due, "the Follower collects in its closing gap"


def _read_staircase(shim):
    """A Leader reading a clean, dark Follower staircase while announcing:
    through step(), a reading every FINE_MS, or as the app reads
    -- the ring filling every 100 us and work() every ms."""
    reader = held.HeldReader()
    reader.start()
    t = HeldTransfer(True, (b"n",), reader=reader)
    level = {P.LOW: 32, P.MID: 64, P.HIGH: 112}
    before = 80

    def light(ms):
        if ms < before:
            return 32
        return level[P.staircase_field(ms - before, P.STAIRCASE,
                                       P.CAL_REPS_FAST, P.CAL_OPEN_FAST_MS,
                                       P.PLATEAU_FAST_MS)]
    if shim:
        now = 0
        while t.levels is None and now < 5000:
            t.step(now, light(now))
            now += P.FINE_MS
    else:
        us = 0
        while t.levels is None and us < 5000000:
            reader.add(us, light(us // 1000))
            if us % 1000 == 0:
                t.work(us)
            us += 100
    assert t.levels is not None, (shim, t.result)
    return t


def test_the_step_shim_reads_a_staircase_as_the_app_does():
    shim = _read_staircase(True)
    app = _read_staircase(False)
    for name in ("black", "grey", "white"):
        assert abs(getattr(shim.levels, name) - getattr(app.levels, name)) <= 2, \
            (name, getattr(shim.levels, name), getattr(app.levels, name))
    assert shim.pair_lit == app.pair_lit
    assert abs(shim.cal_ended - app.cal_ended) <= 2 * P.FINE_MS, \
        (shim.cal_ended, app.cal_ended)
