"""Badges on DMA rings in virtual time, for Dev Mode's tests.

Each badge has a backlight and a ring of 20 us readings (held.ListRing,
read through a reader.RingReader as the app reads fastadc.Ring). What
reaches a badge's sensor is the room plus, from every backlight, its level
times that pair's coupling -- its own through a mirror, the other badge's
across the gap -- behind a first-order lag, with Gaussian noise, in the
ADC's 16-count steps.

Time is virtual and moves only when every badge is waiting: in a reader's
pump(), or between frames. So a badge blocked in a test never freezes the
other, as on the hardware, and two runs that share a time zero step in
lockstep -- each badge on a thread of its own, the clock advanced by
whichever waits last. Nothing here is the badge: the coupling, the lag and
the noise are whatever a test says.

Not a test; the runner skips it.
"""

import math
import random
import threading

from harness import load_module
import held

R = load_module("share_wifi", "reader")

PERIOD_US = 20
SAMPLES = 16384             # the badge's ring: 327.7 ms


class Optics:
    """The clock, and every badge's light."""

    def __init__(self, t=1000000, tick_us=0):
        """`tick_us`: what each read of the clock costs -- 0, and only waits
        move time; more, for code that times its own work."""
        self.t = t
        self.tick_us = tick_us
        self.sensors = []
        self.cond = threading.Condition()
        self.waiting = {}
        self.active = 0
        self.running = None

    def sensor(self, room=40.0, tau_us=500.0, noise=0.0, seed=1):
        """A badge. `room` is counts, or a function of the time in us --
        a flickering room."""
        sensor = Sensor(self, room, tau_us, noise, seed)
        self.sensors.append(sensor)
        return sensor

    def ticks_us(self):
        if self.tick_us:
            self.t += self.tick_us
            self.fill(self.t)
        return self.t

    def ticks_ms(self):
        return self.t // 1000

    def fill(self, t):
        for sensor in self.sensors:
            sensor.fill(t)

    # -- virtual time
    # One badge runs at a time, as each would on its own chip: the protocol
    # keeps module-level scratch buffers, which two badges sharing one
    # interpreter must not interleave in. A badge holds the baton until it
    # waits; time moves when no badge is running and none is due.

    def _advance(self):
        if self.running is not None or len(self.waiting) < self.active:
            return
        if not self.waiting:
            return
        nxt = min(self.waiting.values())
        if nxt > self.t:
            self.fill(nxt)
            self.t = nxt
        self.cond.notify_all()

    def wait_until(self, until):
        """Blocks this badge until `until`, handing the baton on."""
        with self.cond:
            if until <= self.t:
                return
            me = threading.get_ident()
            self.waiting[me] = until
            self.running = None
            self.cond.notify_all()
            while not (self.t >= until and self.running is None):
                self._advance()
                if self.t >= until and self.running is None:
                    break
                self.cond.wait()
            del self.waiting[me]
            self.running = me

    def sleep(self, us):
        self.wait_until(self.t + us)

    def run(self, *loops):
        """Each of `loops` on a thread of its own, in lockstep; raises the
        first exception any of them raised."""
        errors = []
        with self.cond:
            self.active = len(loops)
            self.running = None

        def body(loop):
            me = threading.get_ident()
            with self.cond:
                while self.running is not None:
                    self.cond.wait()
                self.running = me
            try:
                loop()
            except BaseException as error:     # noqa: BLE001 - re-raised
                errors.append(error)
            finally:
                with self.cond:
                    self.active -= 1
                    if self.running == me:
                        self.running = None
                    self._advance()
                    self.cond.notify_all()

        threads = [threading.Thread(target=body, args=(loop,))
                   for loop in loops]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.active = 0
        self.running = None
        if errors:
            raise errors[0]


class Sensor:
    """One badge: its backlight, its ring, and what reaches its sensor.
    `couple(other, counts)` adds `counts` at `other`'s full backlight."""

    def __init__(self, optics, room, tau_us, noise, seed):
        self.optics = optics
        self.room_fn = room if callable(room) else None
        self.room = 0.0 if callable(room) else room
        self.alpha = 1 - math.exp(-PERIOD_US / tau_us)
        self.noise = noise
        self.rng = random.Random(seed)
        self.ring = held.ListRing(SAMPLES, PERIOD_US)
        self.ring.written = optics.t // PERIOD_US
        # Where fastadc.Ring's buffer and its uncached alias would be.
        self.ring.cached = 0x11012000
        self.ring.addr = 0x15018000
        self.level = self.room
        self.backlight = 0.0
        self.coupling = []
        self.shown = []             # (t, value) for every change

    def couple(self, other, counts):
        self.coupling.append((other, counts))

    def target(self):
        out = self.room
        for other, counts in self.coupling:
            out += counts * other.backlight
        return out

    def fill(self, t):
        ring = self.ring
        target = self.target()
        room_fn = self.room_fn
        while ring.written * PERIOD_US < t:
            self.level += (target - self.level) * self.alpha
            value = self.level
            if room_fn is not None:
                value += room_fn(ring.written * PERIOD_US)
            if self.noise:
                value += self.rng.gauss(0, self.noise)
            ring.push(max(0, int(round(value / 16.0))))

    def show(self, value):
        """display.backlight(): live from now."""
        self.optics.fill(self.optics.t)
        self.backlight = value
        self.shown.append((self.optics.t, value))

    def reader(self):
        return Reader(self.ring, self.optics)


class Reader(R.RingReader):
    """A RingReader whose wait is the optics' virtual time."""

    def __init__(self, ring, optics):
        R.RingReader.__init__(self, ring, optics.ticks_us)
        self.optics = optics

    def pump(self, until):
        now = self.now_us()
        if until > now:
            self.optics.wait_until(self.optics.t + (until - now))
            self.now_us()


def mirror(room=40.0, gain=400.0, tau_us=500.0, noise=0.0, seed=1, t=1000000,
           tick_us=0):
    """One badge facing a mirror: its own backlight, `gain` counts at full."""
    optics = Optics(t, tick_us)
    sensor = optics.sensor(room, tau_us, noise, seed)
    sensor.couple(sensor, gain)
    return optics, sensor


def pair(room=40.0, gain=200.0, own=0.0, tau_us=500.0, noise=0.0, seed=1,
         t=1000000):
    """Two badges screen to screen: each sees the other's backlight at
    `gain` counts full, and its own at `own` -- its light off the other's
    glass."""
    optics = Optics(t)
    a = optics.sensor(room, tau_us, noise, seed)
    b = optics.sensor(room, tau_us, noise, seed + 1)
    a.couple(b, gain)
    b.couple(a, gain)
    if own:
        a.couple(a, own)
        b.couple(b, own)
    return optics, a, b
