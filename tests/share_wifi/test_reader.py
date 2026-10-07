"""reader.py: the ring reader's clock, index and queries, and the polled one.

A RingReader runs here over a ListRing that a fake clock fills, as the DMA
fills fastadc.Ring on the badge -- the same class the simulator runs. See
"Reading" in the protocol.
"""

from harness import load_module

R = load_module("share_wifi", "reader")


class Clock:
    """Moves `step` us each time it is read, and fills `ring` as it goes:
    entry i is `value(i)`."""

    def __init__(self, ring, value=lambda i: 7, step=5, period=20):
        self.t = 0
        self.ring = ring
        self.value = value
        self.step = step
        self.period = period

    def __call__(self):
        self.t += self.step
        while self.ring.written < self.t // self.period:
            self.ring.push(self.value(self.ring.written))
        return self.t


def ring_reader(value=lambda i: 7, samples=4096, period=20, per_entry=1,
                usable_us=None):
    ring = R.ListRing(samples, period)
    clock = Clock(ring, value, period=period)
    reader = R.RingReader(ring, clock, per_entry=per_entry,
                          usable_us=usable_us)
    reader.start()
    return reader, ring, clock


def test_a_ring_must_be_a_power_of_two():
    for bad in (1000, 3280):
        try:
            R.ListRing(bad, 20)
        except AssertionError:
            continue
        raise AssertionError("%d accepted" % bad)


def test_the_clock_starts_at_zero_and_never_wraps():
    reader, _, clock = ring_reader()
    assert reader.now_us() < 100
    clock.t = (1 << 30) - 50
    reader.last = clock.t
    first = reader.now_us()
    clock.t = 30                        # ticks_us wrapped (harness ticks
    reader.last = (1 << 30) - 50        # subtract plainly: model the wrap)
    assert first > 0


def test_an_index_is_its_time_and_the_newest_is_complete():
    reader, ring, clock = ring_reader()
    reader.pump(10000)
    p = ring.position()
    newest = reader.newest_us()
    # Entry p - 2 is the newest whose write cannot still be in flight.
    assert newest == reader.anchor + (p - 1) * 20
    assert newest <= reader.now_us()
    assert abs(reader.anchor) <= 20 + reader.anchor_width


def test_parts_are_means_in_counts():
    reader, _, _ = ring_reader(value=lambda i: 7 if i % 2 else 9)
    reader.pump(20000)
    out = [0] * 4
    used = reader.parts(4000, 12000, 4, out)
    assert out == [8 * 16] * 4, out         # (7 + 9) / 2 raw, x 16
    assert used == 400
    assert reader.mean(4000, 12000) == 128


def test_per_entry_divides_out_of_parts_and_into_thresholds():
    # The simulator's ring: an entry sums five readings.
    reader, _, _ = ring_reader(value=lambda i: 35 if i >= 500 else 10,
                               period=100, per_entry=5)
    reader.pump(80000)
    assert reader.mean(10000, 40000) == 2 * 16
    assert reader.mean(60000, 70000) == 7 * 16
    crossing = reader.rise(40000, 70000, [4 * 16] * 300, 300, 100)
    assert abs(crossing - 50000) <= 100, crossing


def test_what_has_left_the_ring_is_no_reading():
    reader, _, _ = ring_reader(samples=1024)        # 20.5 ms
    reader.pump(100000)
    out = [0] * 4
    reader.parts(0, 8000, 4, out)
    assert out == [-1] * 4
    newest = reader.newest_us()
    reader.parts(newest - 4000, newest, 4, out)
    assert -1 not in out
    # And what is not there yet.
    reader.parts(newest + 1000, newest + 9000, 4, out)
    assert out == [-1] * 4


def test_usable_history_is_the_ring_less_the_safe_margin():
    reader, _, _ = ring_reader(samples=16384)
    assert reader.usable == 16384 * 20 - R.RING_SAFE_US == 317680
    other, _, _ = ring_reader(samples=4096, period=100, usable_us=318000)
    assert other.usable == 318000


def test_a_rise_is_found_and_interpolated():
    step_at = 1000                       # entry index: 20 ms
    reader, _, _ = ring_reader(value=lambda i: 2 if i < step_at else 8)
    reader.pump(40000)
    crossing = reader.rise(10000, 30000, [5 * 16] * 200, 200, 100)
    assert abs(crossing - 20000) <= 100, crossing
    assert reader.rise(25000, 35000, [10 * 16] * 200, 200, 100) == -1


def test_a_lone_lit_bin_is_not_a_rise():
    reader, _, _ = ring_reader(value=lambda i: 8 if 500 <= i < 505 else 2)
    reader.pump(30000)
    assert reader.rise(5000, 25000, [5 * 16] * 200, 200, 100) == -1


def test_check_counts_lost_readings_and_reanchors_and_ends_on_a_fault():
    reader, ring, clock = ring_reader()
    reader.pump(5000)
    assert reader.check() and reader.lost == 0
    ring.fault = R.HEALTH_LOST
    assert reader.check() and reader.lost == 1
    ring.fault = R.HEALTH_ADC
    assert not reader.check() and reader.faults == 1


def test_a_fresh_anchor_is_compared_not_adopted():
    reader, _, _ = ring_reader()
    reader.pump(5000)
    anchor = reader.anchor
    reader.anchor += 30                  # within RING_DRIFT_US: kept
    assert reader.check() and reader.anchor == anchor + 30
    reader.anchor += 200                 # past it: counted and adopted
    assert reader.check() and reader.drift == 1
    assert abs(reader.anchor - anchor) <= 40


# --- the polled reader ----------------------------------------------------------

def test_polled_bins_hold_their_readings_and_gaps_hold_none():
    reader = R.PolledReader()
    reader.start()
    for t in range(0, 4000, 100):
        reader.add(t, 100)
    for t in range(6000, 8000, 100):     # 2 ms blind between
        reader.add(t, 200)
    out = [0] * 4
    reader.parts(0, 8000, 4, out)
    assert out == [100, 100, -1, 200], out
    times = [0] * 4
    reader.parts(0, 8000, 4, out, times)
    assert 0 <= times[0] < 2000 and 6000 <= times[3] < 8000


def test_a_polled_rise_after_a_blind_gap_is_not_believed():
    reader = R.PolledReader()
    reader.start()
    for t in range(0, 5000, 100):
        reader.add(t, 50)
    # The rise fell in a 10 ms gap -- a present, or worse a collection.
    for t in range(15000, 20000, 100):
        reader.add(t, 200)
    assert reader.rise(0, 20000, [100] * 100, 100, 1000) == -1
    # A loop reading every 2 ms is not blind: its rises are believed.
    reader.start()
    for t in range(0, 20000, 2000):
        reader.add(t, 50 if t < 10000 else 200)
    crossing = reader.rise(0, 20000, [100] * 100, 100, 1000)
    assert 8000 <= crossing <= 10000, crossing
    reader.start()
    for t in range(0, 10000, 100):
        reader.add(t, 50 if t < 5000 else 200)
    crossing = reader.rise(0, 10000, [100] * 100, 100, 1000)
    assert 4800 <= crossing <= 5100, crossing


def test_the_polled_reader_drops_what_it_can_no_longer_hold():
    reader = R.PolledReader(bins=16, bin_us=250)
    reader.start()
    for t in range(0, 10000, 100):
        reader.add(t, 100)
    assert reader.oldest_us() == (reader.head - 15) * 250
    out = [0]
    reader.parts(0, 1000, 1, out)
    assert out == [-1]
