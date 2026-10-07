"""Two badges pointed at each other, simulated end to end.

Markers such as [measured-v2] in the comments below name the firmware a claim
holds for: legend in .agents/docs/README.md.

Runs the real protocol from first beacon to final Complete Pattern: discovery,
calibration in both directions, rate selection, payload, acknowledgements --
and the real readers (firmware/apps/share_wifi/reader.py): an EXACT badge's
RingReader over a simulated DMA ring, a POLLED badge's PolledReader fed the
readings its loop would take. Faked: the physical layer, the clock, and how
long the badge's Python takes.

What is modelled:

- Each badge's LCD as a first-order lag, behind a present that waits for the
  panel's vertical blank; the backlight live at the next PWM wrap.
- The sensor following the light at a speed the light sets, its noise and
  its 16-count steps, room light, and the room's mains flicker.
- The work each pass of the app's loop does, the collections the protocol
  asks for, and the flicker fit's stall: an EXACT badge keeps reading
  through all of them, a POLLED one is blind.
- Start skew, and each badge's crystal (`ppm`).

What is not modelled: anything about the panel that is not a first-order lag,
ambient drift during a transfer, and the badges moving relative to each other.
A pass here means "agrees with our model of the hardware", not "works".
"""

import math
import os
import random
import sys

# reader.py, the app's own: the simulator runs the production readers.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "firmware", "apps",
    "share_wifi"))
import reader as readers                    # noqa: E402


# --- the backlight ------------------------------------------------------------
# What display.backlight(b) does on v3, from st7789_set_backlight() and
# ST7789::set_backlight() in modules/c/st7789/: the float is truncated to a
# uint8, 0 is off, and anything else is gamma-2.8 mapped onto a PWM duty between
# the floor and 100%. [source-v3] The badge's v2 has no floor -- the floor
# arrived on 2026-08-08 -- so there the duty is plain (b/255)^2.8 and the
# backlight is dark below about 0.45. [source-v2]
BACKLIGHT_GAMMA = 2.8
BACKLIGHT_FLOOR = 8192 / 65535


def backlight_duty(value):
    """PWM duty, 0.0-1.0, that `display.backlight(value)` produces."""
    brightness = int(value * 255) & 0xFF
    if brightness == 0:
        return 0.0
    level = (brightness / 255.0) ** BACKLIGHT_GAMMA
    return (8192 + int(level * (65535 - 8192) + 0.5)) / 65535


# The pixels' share of the panel's swing: black, white, and the fields
# scheme's middle, which measured 40% on hardware. Beacons are drawn in it.
PIXEL_SHARE = {"black": 0.0, "white": 1.0}
MID_FIELD_SHARE = 0.45


# --- continuous invariant checking ------------------------------------------
# From .agents/context/optical-link-invariants.md. These are asserted on every
# tick rather than at the end, because every bug that has cost real time in this
# protocol was a divergence that only surfaced as a mystery timeout seconds
# later. A violation reports the simulated time and both badges' state, which is
# the difference between "ERR_LOST_PEER at 13.3s" and "I2 broke at 6.1s".


class InvariantError(AssertionError):
    """An invariant broke. The message carries the time and both states."""


class Invariants:
    """Checks I1, I2, I4 and I7 against a live Leader/Follower pair.

    I1 and I2 tolerate a bounded window; I4 and I7 never do.

    A rate change legitimately reaches the two badges a few slots apart, and the
    grids are legitimately out of phase between entering the data phase and the
    first edge a receiver has to lock to -- the epoch calibration hands over is
    only an estimate on one side. Both are violations once they PERSIST, which
    is the failure that has actually happened here: a permanent split, and a
    permanent half-slot offset that decodes nothing.
    """

    # A demotion propagates within one pattern plus a lead-in. This is a
    # generous ceiling on that, not a tuning knob -- the failure it exists to
    # catch is a PERMANENT split, which lasts until ERR_LOST_PEER.
    RATE_WINDOW_MS = 1200

    def __init__(self, protocol, leader, follower, payload):
        self.skew_since = None
        self.protocol = protocol
        self.leader = leader
        self.follower = follower
        self.blocks = list(leader.blocks)
        self.payload = list(payload)
        self.split_since = None
        self.accepted = 0               # whole blocks the Follower has taken
        self.last_received = 0
        self.violations = []
        # The two LoopBadges, when there are: each badge's clock starts at
        # its own start, so epochs are compared on the shared timeline.
        self.badges = None

    def _epoch(self, which):
        transfer = (self.leader, self.follower)[which]
        if self.badges is None:
            return transfer.clock.epoch
        badge = self.badges[which]
        return badge.start_at * 1000 + transfer.clock.epoch / badge.rate

    def _state(self, now):
        return ("t=%.0fms leader[phase=%d sub=%d rung=%dms epoch=%d blk=%d "
                "res=%s] follower[phase=%d sub=%d rung=%dms epoch=%d "
                "tokens=%d res=%s]"
                % (now,
                   self.leader.phase, self.leader.sub,
                   self.leader.clock.duration // 1000,
                   self.leader.clock.epoch // 1000,
                   self.leader.block_index, self.leader.result,
                   self.follower.phase, self.follower.sub,
                   self.follower.clock.duration // 1000,
                   self.follower.clock.epoch // 1000,
                   len(self.follower.received), self.follower.result))

    def _fail(self, now, code, detail):
        message = "%s violated: %s\n  %s" % (code, detail, self._state(now))
        self.violations.append(message)
        raise InvariantError(message)

    def check(self, now):
        self._check_clock(now)
        self._check_framing(now)
        self._check_termination(now)

    # I1 and I2 only mean anything once both badges are running slot clocks.
    def _both_in_data(self):
        p = self.protocol
        return (self.leader.phase == p.PHASE_DATA
                and self.follower.phase == p.PHASE_DATA)

    def _check_clock(self, now):
        if not self._both_in_data():
            self.split_since = None
            self.skew_since = None
            return

        # I1 -- the badges are on the same rung, allowing a bounded window for a
        # rate change to propagate.
        if self.leader.clock.index != self.follower.clock.index:
            if self.split_since is None:
                self.split_since = now
            elif now - self.split_since > self.RATE_WINDOW_MS:
                self._fail(now, "I1",
                           "rungs split for %.0fms: leader %dms, follower %dms"
                           % (now - self.split_since,
                              self.leader.clock.duration // 1000,
                              self.follower.clock.duration // 1000))
            return              # I2 is meaningless across different durations
        self.split_since = None

        # I2 -- the slot grids agree. The epochs may differ by whole slots; what
        # must not differ is the phase within a slot. Half a slot is fatal,
        # because the receiver samples the back half and lands on the
        # transmitter's transition.
        #
        # A receiver phase-locks onto the peer's first edge out of black, so it
        # is out of phase until there is one to lock to. The window is the same
        # number of slots the protocol itself waits before declaring the peer
        # lost: a link that has not locked by then is not acquiring, it is
        # broken -- which is exactly what the uncorrected calibration skew did,
        # sitting half a slot out until ERR_LOST_PEER.
        duration = self.leader.clock.duration          # us
        skew = (self._epoch(0) - self._epoch(1)) % duration
        phase_error = min(skew, duration - skew)
        if phase_error <= duration / 4.0:
            self.skew_since = None
            return
        if self.skew_since is None:
            self.skew_since = now
            return
        if now - self.skew_since > \
                self.protocol.LOST_PEER_SLOTS * duration / 1000.0:
            self._fail(now, "I2",
                       "slot grids %.2fms apart on a %.0fms slot for %.0fms "
                       "(%.2f slots, limit 0.25)"
                       % (phase_error / 1000.0, duration / 1000.0,
                          now - self.skew_since,
                          phase_error / float(duration)))

    def _check_framing(self, now):
        # I4 -- no block is accepted twice. The Follower's token list may only
        # grow, may only grow by exactly the next block the Leader is sending,
        # and may never exceed the payload.
        received = self.follower.received
        if len(received) == self.last_received:
            return
        if len(received) < self.last_received:
            self._fail(now, "I4",
                       "follower's tokens shrank from %d to %d"
                       % (self.last_received, len(received)))

        added = received[self.last_received:]
        if self.accepted >= len(self.blocks):
            self._fail(now, "I4",
                       "follower accepted a %d-token block after all %d blocks "
                       "were already in" % (len(added), len(self.blocks)))

        expected = self.blocks[self.accepted]
        if added != expected:
            self._fail(now, "I4",
                       "block %d arrived as %d tokens, expected the %d of the "
                       "Leader's block %d"
                       % (self.accepted + 1, len(added), len(expected),
                          self.accepted + 1))

        self.accepted += 1
        self.last_received = len(received)

    def _check_termination(self, now):
        # I7 -- no false success. The Leader's OK must imply the Follower
        # actually holds the payload. Of everything on the invariant page this
        # is the one that matters most: violating it means the app claims to
        # have shared a credential it did not share.
        if self.leader.result != self.protocol.OK:
            return
        got = self.protocol.parse_payload(self.follower.received)
        if got != self.payload:
            self._fail(now, "I7",
                       "leader reports OK but the follower holds %d tokens "
                       "parsing to %r, not the payload"
                       % (len(self.follower.received), got))


class Link:
    """Two badges on a shared timeline."""

    def __init__(self, leader, follower, step_ms=1.0, invariants=None):
        self.leader = leader
        self.follower = follower
        self.step_ms = step_ms
        self.now = 0.0
        self.log = []
        # Checked on every tick, not at the end. `violation` holds the message
        # of the FIRST one to break -- the moment of divergence, rather than the
        # timeout it causes seconds later.
        self.invariants = invariants
        self.violation = None

    # Raises with the time and both badges' state if anything broke, so a test
    # can be a bare `link.run(...); link.assert_invariants()`.
    def assert_invariants(self):
        if self.violation:
            raise InvariantError(self.violation)


# --- the badges ---------------------------------------------------------------
# The app as it runs a transfer: a loop that, each pass, shows
# field_at(now), calls work(now), and waits for due_us() -- reading, while
# it waits, through its reader. Only a change of pixels goes back to run()
# to be presented; a change of backlight is a display.backlight() call, live
# at once. Modelled from the badge as measured:
#
# - an EXACT badge's ring takes a reading every 20 us whatever the badge is
#   doing; here one entry every RING_ENTRY_US holds the sum of RING_READS of
#   them, each with the DMA's noise, RING_SD [measured-v2: 7.6-9.2 counts a
#   reading, fast_reads 2026-10-06a/b];
# - a POLLED badge reads every LOOP_READ_MS while it waits, and nothing while
#   it works or presents;
# - a pass costs PASS_WORK_MS, SLOT_WORK_MS a slot it decided and
#   SAMPLE_WORK_MS a fine sample it took, each give or take, `work_scale`
#   times over [unmeasured -- Dev Mode M3 ring slot_work]; a collection the
#   protocol asks for GC_MS [measured-v2: 20-46 ms];
# - a present waits for the panel's next vertical blank, then writes for
#   WRITE_MS, and blocks reading throughout [measured-v2];
# - the liquid crystal starts to move `pixel_dead_ms` after the blank, then
#   settles as a first-order lag. A rise crossed T_low 8.5-18.5 ms after the
#   write began in different runs [measured-v2]: 7 ms of dead time and a fast
#   panel, or 16 and a slow one;
# - a backlight change takes effect at the next PWM wrap, up to PWM_PERIOD_MS
#   later [source-v2];
# - with the measured optics, the sensor follows the light at a rate set by
#   the light itself, ds/dt = (I - s) * max(s, SENSOR_FLOOR) / SENSOR_K, I and
#   s in counts of light above the ADC's offset. One constant reproduces
#   backlight_step on 2026-10-05e [measured-all]: from off in the dark half
#   way at 2.8 ms (2.76 measured), to the middle at 5.8 (5.18), a fall at 0.45
#   to half and 4.05 to 90% (0.63, 3.24), and a lit rise at 0.37 (0.57 with
#   the PWM wait). [inferred-all]

# A reading in read_sample()'s tight loop: unmeasured, but the probe's own
# loop around a read was 50-70 us, so this is a floor on the sample's
# readings, ~20, not a guess at them. The simulation steps 0.1 ms anyway.
LOOP_READ_MS = 0.1
LOOP_CALL_MS = 1.0          # kept for callers; the work model replaced it
PASS_WORK_MS = 0.15
SLOT_WORK_MS = 1.2
SAMPLE_WORK_MS = 0.3
GC_MS = (20.0, 46.0)
WAKE_MS = 0.06              # a spin pass, ~40-60 us on v2 [measured-v2]
RING_ENTRY_US = 100
RING_READS = 5
RING_ENTRIES = 4096
RING_USABLE_US = 318000     # the badge's: 327.7 ms less RING_SAFE_US
RING_SD = 8.5
# The flicker's fit, once a transfer, stalls the loop that long: 3.3 ms on
# MicroPython's unix port, and protocol work has run up to ~50 times slower
# on the badge [inferred-v2]. Pessimistic on purpose.
LOOP_FIT_MS = 150.0
# What replaying one kept sample through the staircase reader costs: a
# correction, a pool and, every other sample, a feed over up to 512 values.
# 640 samples at this is 160 ms -- the review's 50-150 ms estimate for v2,
# rounded up [unmeasured -- M7 flicker_filter.replay].
REPLAY_MS_PER_SAMPLE = 0.25
WRITE_MS = 7.8
PWM_PERIOD_MS = 0.263
SENSOR_K = 36.0             # counts x ms, for the mirror's 80-count swing
SENSOR_FLOOR = 0.18         # counts: the dark current that starts a rise


# The backlight driver gives no light below about 11% duty [source-v3], and
# above it light rises with duty. Taking light as duty past that threshold
# fits the mirror's backlight_curve to an ADC step at every value measured
# (0.7, 0.8, 0.9 read 16, 32, 48-64 counts of 80 in the dark) and puts the
# dim low, 0.5, under one step, as the badge read it [measured-all].
LED_THRESHOLD_DUTY = 0.11


def backlight_duty_v2(value):
    """The light display.backlight(value) gives on v2, 0.0-1.0: PWM duty
    with no floor [source-v2], less the LED driver's threshold."""
    brightness = int(value * 255) & 0xFF
    duty = (brightness / 255.0) ** BACKLIGHT_GAMMA
    return max(0.0, duty - LED_THRESHOLD_DUTY) / (1 - LED_THRESHOLD_DUTY)


class LoopPanel:
    """Pixels behind a lag, and a backlight that switches at a PWM wrap."""

    def __init__(self, settle_ms, rng):
        self.tau = max(0.3, settle_ms / 2.3)
        self.rng = rng
        self.share = 0.0            # the liquid crystal, 0.0-1.0
        self.target = 0.0
        self.duty = 1.0             # the backlight, as a duty
        self.changes = []           # (when, "share" | "duty", value)

    def schedule(self, when, kind, value):
        self.changes.append((when, kind, value))
        self.changes.sort(key=lambda c: c[0])

    def output(self, now, dt):
        while self.changes and self.changes[0][0] <= now:
            _, kind, value = self.changes.pop(0)
            if kind == "share":
                self.target = value
            else:
                self.duty = value
        self.share += (self.target - self.share) * (1 - math.exp(-dt / self.tau))
        return self.share * self.duty


class LoopSensor:
    """The sensor reading the peer, with its own light-dependent lag.

    `offset` is the ADC's floor (40 in the dark through a mirror, centred in
    its 16-count step), `light` the room's own light on it in counts, and
    `contrast` the peer's full swing. `k` is SENSOR_K scaled to that swing, or
    None for a sensor that follows instantly.
    """

    def __init__(self, offset, light, contrast, rng, sd=0.0, noise=0,
                 quantum=1, k=None, flicker=0.0, flicker_hz=120, light_shape=None,
                 mains_hz=60.0, mains_drift_hz=0.0):
        self.offset = offset
        self.light = light
        # The room's light flickers at `flicker_hz` by this share of itself:
        # 120 Hz on every LED light measured, up to 47% deep [measured-all].
        self.flicker = flicker
        self.flicker_hz = flicker_hz
        self.phase = rng.uniform(0, 2 * math.pi)
        # Or, with `light_shape` -- ((harmonic, weight), ...) of the mains --
        # a light that flickers at several harmonics of a mains running
        # `mains_drift_hz` off nominal, the largest by `flicker`. See
        # FLICKER_LIGHTS.
        self.shape = None
        if light_shape:
            self.mains = mains_hz + mains_drift_hz
            self.shape = tuple((h, w, rng.uniform(0, 2 * math.pi))
                               for h, w in light_shape)
        self.t = 0.0
        self.contrast = contrast
        self.rng = rng
        self.sd = sd
        self.noise = noise
        self.quantum = quantum
        self.k = k
        self.s = light

    def settle(self, peer_output, dt):
        self.t += dt
        room = self.light
        if self.flicker and self.shape:
            cycle = 2 * math.pi * self.mains * self.t / 1000.0
            wave = 0.0
            for h, w, phase in self.shape:
                wave += w * math.sin(h * cycle + phase)
            room *= 1 + self.flicker * wave
        elif self.flicker:
            room *= 1 + self.flicker * math.sin(
                2 * math.pi * self.flicker_hz * self.t / 1000.0 + self.phase)
        target = room + peer_output * self.contrast
        if self.k is None:
            self.s = target
            return
        rate = max(self.s, SENSOR_FLOOR * self.contrast / 80.0) / self.k
        self.s += (target - self.s) * (1 - math.exp(-dt * rate))

    def read(self):
        value = self.offset + self.s
        if self.sd:
            value += self.rng.gauss(0, self.sd)
        if self.noise:
            value += self.rng.uniform(-self.noise, self.noise)
        value = int(value) // self.quantum * self.quantum
        return max(0, min(65535, value))


class LoopBadge:
    """A Transfer driven the way the app's transfer loop drives it, through
    the production reader: `reader_kind` "ring" (EXACT) or "polled"."""

    def __init__(self, protocol, transfer, panel, sensor, rng, reader,
                 start_at=0.0, refresh_ms=16.7, pixel_dead_ms=7.0,
                 read_ms=LOOP_READ_MS, duty=None, work_scale=1.0, ppm=0.0,
                 ring_sd=None, fit_ms=LOOP_FIT_MS):
        self.protocol = protocol
        self.transfer = transfer
        self.panel = panel
        self.sensor = sensor
        self.rng = rng
        self.reader = reader
        self.ring = getattr(reader, "ring", None)
        self.start_at = start_at
        self.refresh_ms = refresh_ms
        self.phase_ms = rng.uniform(0, refresh_ms)     # where its blanks fall
        self.pixel_dead_ms = pixel_dead_ms
        self.read_ms = read_ms
        self.duty = duty or backlight_duty_v2
        self.work_scale = work_scale
        self.rate = 1.0 + ppm * 1e-6
        self.ring_sd = ring_sd
        self.fit_ms = fit_ms
        self.local = 0.0            # this badge's own clock, ms since start
        self.next_entry = RING_ENTRY_US / 1000.0
        self.next_read = start_at
        self.next_pass = start_at
        self.busy_until = start_at
        self.started = False
        self.pixels = None
        self.backlight = 1.0
        self.presents = 0
        self.fitted = False
        self.collections = 0

    def local_us(self):
        return int(self.local * 1000)

    def next_blank(self, now):
        k = math.ceil((now - self.phase_ms) / self.refresh_ms)
        return self.phase_ms + k * self.refresh_ms

    def raw(self):
        """One reading as the ring holds it: 12 bits, light_level() / 16."""
        if self.ring_sd is None:
            return self.sensor.read() >> 4
        sensor = self.sensor
        value = sensor.offset + sensor.s + self.rng.gauss(0, self.ring_sd)
        value = int(value) // 16
        return 0 if value < 0 else value

    def tick(self, now, dt):
        if now < self.start_at:
            return
        if not self.started:
            self.started = True
            self.reader.start()
        self.local += dt * self.rate
        # Sensing.
        if self.ring is not None:
            while self.local >= self.next_entry:
                total = 0
                for _ in range(RING_READS):
                    total += self.raw()
                self.ring.push(total)
                self.next_entry += RING_ENTRY_US / 1000.0
        elif now >= self.busy_until and now >= self.next_read:
            self.reader.add(self.local_us(), self.sensor.read())
            self.next_read = now + self.read_ms * self.rng.uniform(0.9, 1.1)
        # A pass of the loop.
        if now < self.busy_until or now < self.next_pass:
            return
        transfer = self.transfer
        if transfer.collect_due:
            transfer.collect_due = False
            self.collections += 1
            self.busy_until = now + self.rng.uniform(*GC_MS)
            self.next_pass = self.busy_until
            return
        t = self.reader.now_us()
        field = transfer.field_at(t)
        self.show(now, field)
        transfer.work(t)
        cost = (PASS_WORK_MS + SLOT_WORK_MS * transfer.work_slots
                + SAMPLE_WORK_MS * transfer.work_samples)
        cost *= self.rng.uniform(0.7, 1.5) * self.work_scale
        if transfer.fit_tried and not self.fitted:
            self.fitted = True
            cost += self.fit_ms
        # A POLLED Follower's late fit replays the staircase it read: each
        # kept sample corrected and pooled again [unmeasured -- M7 replay].
        replayed = getattr(transfer, "replayed", 0)
        if replayed:
            transfer.replayed = 0
            cost += REPLAY_MS_PER_SAMPLE * replayed
        self.busy_until = max(self.busy_until, now + cost)
        due = self.start_at + transfer.due_us() / 1000.0 / self.rate
        self.next_pass = max(self.busy_until, due) \
            + self.rng.uniform(0, WAKE_MS)

    def show(self, now, field):
        protocol = self.protocol
        pixels, backlight = protocol.LEVEL_SCHEMES[self.transfer.scheme][field]
        if backlight != self.backlight:
            self.backlight = backlight
            self.panel.schedule(now + self.rng.uniform(0, PWM_PERIOD_MS),
                                "duty", self.duty(backlight))
        if pixels != self.pixels:
            self.pixels = pixels
            blank = self.next_blank(now)
            share = PIXEL_SHARE.get(pixels, MID_FIELD_SHARE) \
                if isinstance(pixels, str) else MID_FIELD_SHARE
            self.panel.schedule(blank + self.pixel_dead_ms, "share", share)
            self.busy_until = max(self.busy_until, blank + WRITE_MS)
            self.presents += 1


class LoopLink(Link):
    """Two badges on a shared timeline, in STEP_MS steps."""

    STEP_MS = 0.1

    def run(self, limit_seconds=180):
        deadline = limit_seconds * 1000
        dt = self.STEP_MS
        leader, follower = self.leader, self.follower
        last = None
        check_every = 10
        n = 0
        while self.now < deadline:
            l_out = leader.panel.output(self.now, dt)
            f_out = follower.panel.output(self.now, dt)
            # Each badge's own screen leaks back into its sensor, off the
            # peer's glass, by `self_coupling` of its output.
            c = self.self_coupling
            leader.sensor.settle(f_out + c * l_out if c else f_out, dt)
            follower.sensor.settle(l_out + c * f_out if c else l_out, dt)
            leader.tick(self.now, dt)
            follower.tick(self.now, dt)
            state = (leader.transfer.phase, follower.transfer.phase)
            if state != last:
                self.log.append((self.now, state))
                last = state
            n += 1
            if (self.invariants is not None and self.violation is None
                    and n % check_every == 0):
                try:
                    self.invariants.check(self.now)
                except InvariantError as broken:
                    self.violation = str(broken)
            if leader.transfer.result and follower.transfer.result:
                break
            self.now += dt
        return self.now / 1000

    def phase_times(self):
        """Seconds from the start to each phase's first appearance on the
        Leader: discovery ends at calibration, calibration at data."""
        firsts = {}
        for now, (phase, _) in self.log:
            firsts.setdefault(phase, now / 1000.0)
        return firsts

def build_loop(protocol, payload, settle_ms=16, ambient=(200, 240),
               contrast=9000, noise=80, start_skew_ms=0, seed=1, check=True,
               refresh_ms=16.7, pixel_dead_ms=7.0, sensor_sd=None, quantum=1,
               offset=None, room_light=0, sensor_lag=False,
               duty=backlight_duty_v2, read_ms=LOOP_READ_MS, k_scale=1.0,
               flicker=0.0, flicker_hz=120, light=None, mains_drift_hz=0.0,
               reader="ring", peer_reader=None, work_scale=1.0, ppm=(0.0, 0.0),
               ring_sd=None, fit_ms=LOOP_FIT_MS, self_coupling=0.0,
               **_ignored):
    """A Leader and a Follower, as the app runs them.

    `reader` is the Leader's ("ring" or "polled"), `peer_reader` the
    Follower's (the same if not given). With `sensor_sd` -- the measured
    optics -- `offset` is the ADC floor, `room_light` the room's light in
    counts, and `sensor_lag` turns on the light-dependent response,
    `k_scale` times as slow as measured; the ring's readings then carry
    `ring_sd` (RING_SD by default). `ppm` is each badge's crystal error.
    `self_coupling` is the share of a badge's own output that reaches its
    own sensor, off the peer's glass [unmeasured]: 0 by default.
    """
    rng = random.Random(seed)
    kinds = (reader, peer_reader or reader)

    def sensor(which):
        k = SENSOR_K * k_scale * contrast / 80.0 if sensor_lag else None
        if sensor_sd is None:
            return LoopSensor(ambient[which], 0, contrast, rng, noise=noise, k=k)
        return LoopSensor(ambient[which] if offset is None else offset,
                          room_light, contrast, rng, sd=sensor_sd,
                          quantum=quantum, k=k, flicker=flicker,
                          flicker_hz=flicker_hz, mains_drift_hz=mains_drift_hz,
                          **FLICKER_LIGHTS.get(light, {}))

    holder = {}

    def make_reader(which):
        def ticks_us():
            return holder[which].local_us()
        if kinds[which] == "ring":
            ring = readers.ListRing(RING_ENTRIES, RING_ENTRY_US)
            return readers.RingReader(ring, ticks_us, per_entry=RING_READS,
                                      usable_us=RING_USABLE_US)
        return readers.PolledReader(None, ticks_us)

    def badge(which, payload_or_none, start):
        reader_ = make_reader(which)
        transfer = protocol.Transfer(which == 0, payload_or_none, reader_)
        sd = None
        if sensor_sd is not None:
            sd = RING_SD if ring_sd is None else ring_sd
        made = LoopBadge(protocol, transfer,
                         LoopPanel(settle_ms, random.Random(seed * 2 + which)),
                         sensor(which), random.Random(seed * 7 + which),
                         reader_, start_at=start, refresh_ms=refresh_ms,
                         pixel_dead_ms=pixel_dead_ms, read_ms=read_ms,
                         duty=duty, work_scale=work_scale, ppm=ppm[which],
                         ring_sd=sd, fit_ms=fit_ms)
        holder[which] = made
        return made

    lead = badge(0, payload, 0.0)
    follow = badge(1, None, start_skew_ms)
    checker = None
    if check:
        checker = Invariants(protocol, lead.transfer, follow.transfer, payload)
        checker.badges = (lead, follow)
    link = LoopLink(lead, follow, invariants=checker)
    link.self_coupling = self_coupling
    return link


# Every badge now runs the transfer loop; build() is the same thing.
build = build_loop

# The v2 badge for build_loop(): 60 Hz, and the liquid crystal starting 7 ms
# after the blank, as on the runs whose rise crossed T_low 13 ms after the
# present call; LOOP_V2_SLOW_PANEL the runs where it took 23.
LOOP_V2 = dict(refresh_ms=16.7, pixel_dead_ms=7.0)
LOOP_V2_SLOW_PANEL = dict(refresh_ms=16.7, pixel_dead_ms=16.0, settle_ms=20)
LOOP_V3 = dict(refresh_ms=11.1, pixel_dead_ms=7.0)

# Room lights for build_loop(light=...), with `flicker` the size of the
# largest component: the mains harmonics each flickers at, and their weights.
# "led" is what every light measured on 2026-10-06 was: twice the mains, and
# not a sine -- the brightest rooms' peak-to-peak was 3.4-3.5 times their
# standard deviation, where a sine's is 2.8 [measured-all], which a 30%
# fourth harmonic gives. "half" flickers once a cycle, as a light rectifying
# half the mains would: none measured did. `mains_drift_hz` moves the mains
# off nominal: grids hold within about 0.02 Hz [unmeasured here].
FLICKER_LIGHTS = {
    "sine60": dict(mains_hz=60.0, light_shape=((2, 1.0),)),
    "led60": dict(mains_hz=60.0, light_shape=((2, 1.0), (4, 0.3))),
    "led50": dict(mains_hz=50.0, light_shape=((2, 1.0), (4, 0.3))),
    "half60": dict(mains_hz=60.0, light_shape=((1, 1.0), (2, 0.5), (3, 0.2))),
    "half50": dict(mains_hz=50.0, light_shape=((1, 1.0), (2, 0.5), (3, 0.2))),
}

# The sensor as measured, for build_loop(): the ADC floor centred at 40 in
# the dark, a reading every pass rather than the median of three, and its own
# light-dependent lag. `room_light` 48 is the lit room, 80 counts read less
# the 32-count dark floor.
LOOP_SENSOR_V2 = dict(sensor_sd=6.0, quantum=16, offset=40, sensor_lag=True)


# --- running it -------------------------------------------------------------

SCENARIOS = {
    "ideal":        dict(settle_ms=1,  noise=0,   start_skew_ms=0,    ambient=(200, 200)),
    "datasheet":    dict(settle_ms=8,  noise=80,  start_skew_ms=0,    ambient=(200, 200)),
    "realistic":    dict(settle_ms=8,  noise=80,  start_skew_ms=1500, ambient=(200, 240)),
    "slow panel":   dict(settle_ms=16, noise=80,  start_skew_ms=1500, ambient=(200, 240)),
    "bright room":  dict(settle_ms=8,  noise=200, start_skew_ms=1500, ambient=(30000, 31000)),
    "cold panel":   dict(settle_ms=24, noise=80,  start_skew_ms=1500, ambient=(200, 240)),
}

# The sensor as measured, in counts. Through a mirror, held flat, the panel's
# The sensor as measured, in counts. Through a mirror, held flat, the panel's
# whole black-to-white swing read 80 counts in the dark (2026-10-03) over a
# 32-count floor, with held levels' readings scattered at a standard
# deviation of 2-8 counts and every reading a whole 16-count step. A peer
# badge is predicted, not measured, from the geometry of what the sensor sees
# (.agents/docs/optical-coupling.md): about the mirror's swing when the two
# screens are aligned edge to edge, two to three times it when the peer's
# screen overlaps the sensor by 2-5 mm, because the mirror's own loss goes
# and the lit region fills more of the sensor's view.
# A reading of 32 is anything from 32 to 47, so the floor is centred at 40.
SENSOR_V2 = dict(ambient=(40, 40), sensor_sd=6.0, quantum=16, reads=3)
SWING_MIRROR = 80
SWING_PEER_ALIGNED = 95
SWING_PEER_OVERLAP = 210        # the peer's screen 2-5 mm over the sensor

TYPICAL = (b"HomeNet", b"hunter2!!")
WORST = (b"A" * 32, b"k" * 63)


def report(protocol, payload=TYPICAL, seconds=200, seed=1, **transmit):
    """Run every scenario and print how each one ends. `transmit` is passed
    to build_loop()."""
    names = {v: k[6:].lower() for k, v in vars(protocol).items()
             if k.startswith("PHASE_")}
    print("%-14s %8s  %-16s %-16s %s"
          % ("scenario", "elapsed", "leader", "follower", "payload"))
    broken = []
    for label, kw in SCENARIOS.items():
        link = build_loop(protocol, payload, seed=seed, **dict(kw, **transmit))
        elapsed = link.run(seconds)
        got = protocol.parse_payload(link.follower.transfer.received)
        reached = names.get(link.follower.transfer.phase, "?")
        print("%-14s %7.1fs  %-16s %-16s %s"
              % (label, elapsed,
                 link.leader.transfer.result or "(reached %s)" % reached,
                 link.follower.transfer.result or "(reached %s)" % reached,
                 "OK" if got == list(payload) else "-"))
        if link.violation:
            broken.append((label, link.violation))

    # An invariant breaks at the moment of divergence; a result code reports the
    # timeout it causes seconds later. Print the former under the table.
    for label, message in broken:
        print("\n%s:\n  %s" % (label, message.replace("\n", "\n  ")))


if __name__ == "__main__":
    # This imports from firmware/apps/, which is packed onto the badge -- so
    # leave no __pycache__ behind there. See tests/run_tests.py.
    sys.dont_write_bytecode = True
    HERE = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.dirname(HERE))
    from harness import load_module
    # --v3, --slow-panel: the panel's timing. --optics=mirror|aligned|overlap:
    # the sensor as measured, with the swing a mirror gave or a peer badge is
    # predicted to. --polled: both badges on light_level(), as a badge whose
    # ring was refused; --mixed: the Follower only.
    protocol = load_module("share_wifi", "protocol")
    extra = dict(LOOP_V3 if "--v3" in sys.argv else
                 LOOP_V2_SLOW_PANEL if "--slow-panel" in sys.argv else
                 LOOP_V2)
    swings = {"mirror": SWING_MIRROR, "aligned": SWING_PEER_ALIGNED,
              "overlap": SWING_PEER_OVERLAP}
    for arg in sys.argv:
        if arg.startswith("--optics="):
            extra.update(LOOP_SENSOR_V2, contrast=swings[arg.split("=", 1)[1]])
    if "--polled" in sys.argv:
        extra["reader"] = "polled"
    if "--mixed" in sys.argv:
        extra["peer_reader"] = "polled"
    report(protocol, **extra)
