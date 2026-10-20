"""Two badges pointed at each other, simulated end to end.

Markers such as [measured-v2] in the comments below name the firmware a claim
holds for: legend in .agents/docs/README.md.

Runs the real protocol from first beacon to final Complete Pattern: discovery,
calibration in both directions, rate selection, payload, acknowledgements --
and the real reader (firmware/apps/share_wifi/reader.py): a RingReader over a
simulated DMA ring. Faked: the physical layer, the clock, and how long the
badge's Python takes.

What is modelled:

- Each badge's backlight on its static white screen, a new duty live at the
  next PWM wrap. Every symbol is the backlight's: the pixels never change.
- The sensor following the light at a speed the light sets, its noise and
  its 16-count steps, room light, and the room's mains flicker.
- The work each pass of the app's loop does, the collections the protocol
  asks for, and the flicker fit's stall, which the ring reads through.
- Start skew, and each badge's crystal (`ppm`).

What is not modelled: ambient drift during a transfer, and the badges moving
relative to each other. A pass here means "agrees with our model of the
hardware", not "works".
"""

import math
import os
import random
import sys

# held.py, the tests' ring, and the harness it loads modules with.
sys.path[:0] = [os.path.dirname(os.path.abspath(__file__)),
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
import held                                 # noqa: E402

# reader.py, the app's own: the simulator runs the production readers.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "firmware", "apps",
    "share_wifi"))
import reader as readers                    # noqa: E402
import fastblock                            # noqa: E402 - every transfer's


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


# --- continuous invariant checking ------------------------------------------
# From .agents/optical_data_transfer/invariants.md. These are asserted on every
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
        self.payload = [bytes(f) for f in payload]
        self.split_since = None
        self.last_received = None
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
        return ("t=%.0fms leader[phase=%d sub=%d rung=%dms epoch=%d chunk=%d "
                "res=%s] follower[phase=%d sub=%d rung=%dms epoch=%d "
                "chunks=%d res=%s]"
                % (now,
                   self.leader.phase, self.leader.sub,
                   self.leader.clock.duration // 1000,
                   self.leader.clock.epoch // 1000,
                   self.leader.fast_chunk, self.leader.result,
                   self.follower.phase, self.follower.sub,
                   self.follower.clock.duration // 1000,
                   self.follower.clock.epoch // 1000,
                   len(self.follower.fast_chunks), self.follower.result))

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
        # A fast Follower allows for a Leader holding its first block back
        # (start_grace), and one that found flicker for a Leader sending it
        # again (first_grace, until a Begin frames): no edge to lock to.
        window = self.protocol.LOST_PEER_SLOTS + getattr(
            self.follower, "start_grace", 0) + getattr(
            self.follower, "first_grace", 0)
        if now - self.skew_since > window * duration / 1000.0:
            self._fail(now, "I2",
                       "slot grids %.2fms apart on a %.0fms slot for %.0fms "
                       "(%.2f slots, limit 0.25)"
                       % (phase_error / 1000.0, duration / 1000.0,
                          now - self.skew_since,
                          phase_error / float(duration)))

    def _check_framing(self, now):
        # I4 -- no block is accepted twice. Fast blocks are kept by chunk, so
        # none is; the payload is received whole, once the stream from chunk
        # 0 parses, and must then be the Leader's -- and stay it.
        received = self.follower.received
        if received is None or received == self.last_received:
            return
        if self.last_received is not None:
            self._fail(now, "I4", "the received payload changed")
        if list(received) != self.payload:
            self._fail(now, "I4",
                       "fast payload assembled as %d fields, not the "
                       "Leader's %d" % (len(received), len(self.payload)))
        self.last_received = received

    def _check_termination(self, now):
        # I7 -- no false success. The Leader's OK must imply the Follower
        # actually holds the payload. Of everything on the invariant page this
        # is the one that matters most: violating it means the app claims to
        # have shared a credential it did not share.
        if self.leader.result != self.protocol.OK:
            return
        got = self.follower.received
        if got is None or list(got) != self.payload:
            self._fail(now, "I7",
                       "leader reports OK but the follower holds %r, not the "
                       "payload" % (got,))


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
# field_at(now), calls work(now), and waits for due_us() -- its ring reading
# throughout. A field is a display.backlight() call on a white screen, live
# at once. Modelled from the badge as measured:
#
# - the ring takes a reading every 20 us whatever the badge is doing; here
#   one entry every RING_ENTRY_US holds the sum of RING_READS of them, each
#   with the DMA's noise, RING_SD [measured-v2: 7.6-9.2 counts a reading,
#   fast_reads 2026-10-06a/b];
# - a pass costs PASS_WORK_MS, SLOT_WORK_MS a slot it decided and
#   SAMPLE_WORK_MS a fine sample it took, each give or take, `work_scale`
#   times over [unmeasured -- Dev Mode M3 ring slot_work]; a collection the
#   protocol asks for GC_MS [measured-v2: 20-46 ms; 67-81 inside a dev link
#   session; 52 once outside one, unexplained];
# - a backlight change takes effect at the next PWM wrap, up to a PWM period
#   later [source-v2]: PWM_PERIOD_MS on v2, PWM_PERIOD_MS_V3 with LOOP_V3;
# - with the measured optics, the sensor follows the light at a rate set by
#   the light itself, ds/dt = (I - s) * max(s, SENSOR_FLOOR) / SENSOR_K, I and
#   s in counts of light above the ADC's offset. One constant reproduces
#   backlight_step on 2026-10-05e [measured-all]: from off in the dark half
#   way at 2.8 ms (2.76 measured), to the middle at 5.8 (5.18), a fall at 0.45
#   to half and 4.05 to 90% (0.63, 3.24), and a lit rise at 0.37 (0.57 with
#   the PWM wait). [inferred-all]

PASS_WORK_MS = 0.15
# A decided slot's work with no flicker to correct, on the lit 8 ms rung
# through the ring: 2.35-2.58 ms mean, 3.2 max, over 2026-10-07a to -09 --
# slots decided middle or high [measured-v2]. Flicker work is priced on top
# by FLICKER_COSTS. On 2026-10-09b the probe's two bounds landed either side
# of the model: a fitted slot decided middle 2.62 ms (2.98 max) against the
# model's 2.95, one decided LOW and tracked 4.23 (4.93) against 4.4
# [measured-v2].
SLOT_WORK_MS = 2.5
SAMPLE_WORK_MS = 0.3
# A collection: 27.7-31.6 ms from the menu, tethered or not, the dev
# link's modules loaded or not (2026-10-07a, -09b, -09c, -09d); 66.7-80.7
# when the probe ran over an open dev link session (2026-10-08, -09); and
# 51.7-54.1 from the menu on a fresh launch, the link never opened
# (2026-10-09f) [measured-v2]. Explained 2026-10-13: a collection sweeps
# to the highest live block, which is wherever the launch left the app, so
# a transfer meets ~17-80 ms [measured-v2]. The default is the low end;
# GC_MS_HW1, the `gc70` sweep rows, bounds it from above (section 2 of
# .agents/optical_data_transfer/data.md).
GC_MS_SHORT = (25.0, 46.0)
GC_MS_HW1 = (65.0, 81.0)
GC_MS = GC_MS_SHORT
# From the slot boundary to the display.backlight() call in the
# transmitter's loop: 346-358 us mean, 470-489 max in the mirror's
# loopback_ring [measured-v2, 2026-10-07a], which calls it straight from a
# table, and a transfer's field_at() and show_level() ~150 us more
# (ring.quiet.tx_path, 160-166 us over the bare call, 2026-10-08 and -09
# [measured-v2]). The duty then waits for the PWM's wrap.
TX_LATE_MS = (0.35, 0.65)
# What the flicker filter's calls cost a badge, ms, by profile: "v2" as
# measured on 2026-10-07a M7, before amounts() hoisted the fitted table and
# shift out of each part -- every amount() 0.58 (correct() is amount() and
# a clamp), every track() 0.21, every update() 0.39 [measured-v2] -- and
# "inlined", the code from amounts() until 2026-10-09. Its correct() is
# 0.43-0.73 ms a call, though amount() alone is 0.16-0.20: two heap-allocated
# frames a call, correct() timed first [inferred-v2] -- see "frameless"
# below, the code as it stands; track()
# 0.23-0.24, update() 0.40-0.46 [measured-v2, 2026-10-08 and -09 M7]. Its
# window is one amounts() a window and a few integer operations a part: a
# fitted ring listener's slot cost 2.58 ms against 2.55 unfitted, a
# lock attempt's amounts() included, so a window is at most ~0.3
# [measured-v2 bound, 2026-10-09 M3]. "none" charges nothing, the model
# before the run.
# The learner -- the filter before or without a fit -- prices its amount()
# at amount_learner's 0.11-0.13 ms (M7, 2026-10-08 to -09b) and a point it
# learns from a dark slot, with the slot's close, at ~0.3: an unfitted LOW
# slot cost 4.10 ms on 2026-10-09b (M3 `quiet`, a fresh filter -- the
# learner, never fitted) against 2.5 for a middle one, over five parts
# [measured-v2]. Whether its sines had become active (flicker of 4 counts
# or more) was not recorded; if they had, the 4.10 already holds their
# amount() calls, which the model charges again as `learner` -- a double
# count, on the safe side. A steady filter's calls return at once, and are
# in SLOT_WORK_MS.
FLICKER_COSTS = {
    "v2": dict(amount=0.58, window=0.0, part=0.58, track=0.21, update=0.39,
               learner=0.12, collect=0.3),
    "inlined": dict(amount=0.58, window=0.3, part=0.03, track=0.24,
                    update=0.42, learner=0.12, collect=0.3),
    # "frameless", the code as it stands since 2026-10-09: correct(),
    # amount(), track() and collect() keep every frame on the stack (see
    # amount() in protocol.py) where correct() allocated two a call. A
    # fitted amount() is priced at what amount() alone measured with both
    # frames still in it, 0.16-0.20 ms (M7 `correct_amount`, 2026-10-09 to
    # -09d [measured-v2]) -- an upper bound if the frames were what made
    # correct() dearer [inferred-v2]; M7's `correct` will say. track() keeps
    # its measured price, though it lost its two frames too.
    "frameless": dict(amount=0.20, window=0.3, part=0.03, track=0.24,
                      update=0.42, learner=0.12, collect=0.3),
    "none": dict(amount=0.0, window=0.0, part=0.0, track=0.0, update=0.0,
                 learner=0.0, collect=0.0),
}
COST_PROFILE = "frameless"   # the code as it stands
WAKE_MS = 0.06              # a spin pass, ~40-60 us on v2 [measured-v2]
RING_ENTRY_US = 100
RING_READS = 5
RING_ENTRIES = 4096
# The badge's ring, 16,384 readings of 20 us: 327.68 ms. The list holds
# 409.6 ms of entries; past RING_LENGTH each push overwrites the oldest, so
# a read later than the badge's ring allows finds newer readings, as there.
# Until 2026-10-12 nothing was overwritten, and a Follower that trained
# mid-burst for longer than the ring still read its bins [measured-v2,
# 2026-10-12; data.md section 10.4].
RING_LENGTH = 3277
RING_USABLE_US = 318000     # the badge's: 327.7 ms less RING_SAFE_US
RING_SD = 8.5
# The flicker's fit, once a transfer, stalls the loop that long: on a
# transfer's 50 samples 105 ms on the badge -- four harmonics, the line
# refitted each pass -- and 141 with six [measured-v2, M7 2026-10-14]. A
# light four cannot explain fits twice, ~245 ms; no row here needs it.
LOOP_FIT_MS = 150.0
# The steadiness check that stands in for the fit in a steady room: a scan
# of the kept samples for the window and two passes of a line through them.
# The line's passes cost 1.23 ms on the Leader's 50 samples and 0.83 on the
# Follower's 30 on the badge [measured-v2, M7 2026-10-13]; the scan before
# them is untimed, priced at ~30 us a kept sample over up to 80
# [inferred-v2]. Was 25 ms, a guess.
FIT_CHECK_MS = 4.0
# What replaying one kept sample through the staircase reader costs: a
# correction, a pool and, every other sample, a feed over up to 512 values.
# 640 samples took 224.5 ms on the badge [measured-v2, 2026-10-07a M7], and
# 226.6-235.9 on 2026-10-08 and -09 [measured-v2]: 0.35-0.37 a sample.
REPLAY_MS_PER_SAMPLE = 0.35
# The backlight's PWM period, 65536 system clocks: 328 us at v2's 200 MHz
# [measured-v2: sys_hz 200000000, 2026-10-09f; source-v2], 262 us at v3's
# 250 MHz [source-v3].
PWM_PERIOD_MS = 0.328
# The fast block's common PWM period: each badge sets its divider so a
# period is 327.68 us whatever its clock (fastblock.common_div()) [source-v2,
# source-v3; the fractional divider unverified on hardware].
FAST_COMMON_MS = 0.32768
# A fast block's decoding on the Follower: a training (three a block, one
# a start shift) and each bin fed -- ~5 us a symbol, 6.55 bins a symbol, in
# viper [inferred-v2]. A block's three trainings took 315-434 ms on the
# badge, in two clusters near 320 and 425 [measured-v2, fast_loopback
# 2026-10-12]: a training is one of these, chosen per block. They were
# 75 ms, scaled from the unix port, until then.
FAST_TRAIN_MS = (106.0, 142.0)
# A later block on the same rung retunes the trained taps instead
# (Decoder.retune): 54-70 ms on the badge, dark and lit [measured-v2,
# fast_loopback 2026-10-12, 28 retunes]; one of these, chosen per block.
FAST_RETUNE_MS = (56.0, 61.0, 66.0)
FAST_BIN_MS = 0.0008
PWM_PERIOD_MS_V3 = 0.262
# sensor_id's fit (2026-10-09f) puts K at 28-40 in the dark, and the model
# fits as well as per-level linear or Wiener ones: each beats it in 9 of 90
# captures, by 0.042 raw at most [measured-all].
SENSOR_K = 36.0             # counts x ms, for the mirror's 80-count swing
SENSOR_FLOOR = 0.18         # counts: the dark current that starts a rise


# The backlight driver gives no light below about 11% duty [source-v3] --
# 7-9% measured by channel's stairs (2026-10-09f) [measured-all], not
# adopted here: changing it wants a re-sweep -- and
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


def backlight_duty_v3(value):
    """The same on v3: its 12.5% floor (backlight_duty) [source-v3], less
    the LED driver's threshold."""
    duty = backlight_duty(value)
    return max(0.0, duty - LED_THRESHOLD_DUTY) / (1 - LED_THRESHOLD_DUTY)


class LoopBacklight:
    """A backlight whose new duty takes effect when scheduled: at the next
    PWM wrap after the call."""

    def __init__(self):
        self.duty = 0.0             # the backlight, as a duty
        self.changes = []           # (when, duty)

    def schedule(self, when, duty):
        self.changes.append((when, duty))
        self.changes.sort(key=lambda c: c[0])

    def output(self, now):
        while self.changes and self.changes[0][0] <= now:
            self.duty = self.changes.pop(0)[1]
        return self.duty


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
    the production RingReader over a simulated ring."""

    def __init__(self, protocol, transfer, panel, sensor, rng, reader,
                 start_at=0.0, duty=None, work_scale=1.0, ppm=0.0,
                 ring_sd=None, fit_ms=LOOP_FIT_MS, cost_profile=None,
                 gc_ms=None, pwm_ms=PWM_PERIOD_MS):
        self.protocol = protocol
        self.transfer = transfer
        self.panel = panel
        self.sensor = sensor
        self.rng = rng
        self.reader = reader
        self.ring = reader.ring
        self.start_at = start_at
        self.duty = duty or backlight_duty_v2
        self.work_scale = work_scale
        self.rate = 1.0 + ppm * 1e-6
        self.ring_sd = ring_sd
        self.fit_ms = fit_ms
        self.gc_ms = gc_ms or GC_MS
        self.cost_profile = cost_profile or COST_PROFILE
        self.pwm_ms = pwm_ms
        self.local = 0.0            # this badge's own clock, ms since start
        self.next_entry = RING_ENTRY_US / 1000.0
        self.next_pass = start_at
        self.busy_until = start_at
        self.started = False
        self.backlight = None
        self.fitted = False
        self.collections = 0
        self.count_flicker_calls()

    def local_us(self):
        return int(self.local * 1000)

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
        while self.local >= self.next_entry:
            total = 0
            for _ in range(RING_READS):
                total += self.raw()
            self.ring.push(total)
            self.next_entry += RING_ENTRY_US / 1000.0
        # A pass of the loop.
        if now < self.busy_until or now < self.next_pass:
            return
        transfer = self.transfer
        if transfer.collect_due:
            transfer.collect_due = False
            self.collections += 1
            self.busy_until = now + self.rng.uniform(*self.gc_ms)
            self.next_pass = self.busy_until
            return
        t = self.reader.now_us()
        field = transfer.field_at(t)
        self.show(now, field)
        transfer.work(t)
        cost = (PASS_WORK_MS + SLOT_WORK_MS * transfer.work_slots
                + SAMPLE_WORK_MS * transfer.work_samples
                + self.flicker_cost())
        cost *= self.rng.uniform(0.7, 1.5) * self.work_scale
        # Each steadiness check (two on a half-window retry), and the fit's
        # stall if one ran.
        checks = getattr(transfer, "fit_checks", 0)
        if checks:
            transfer.fit_checks = 0
            cost += FIT_CHECK_MS * checks
        if transfer.fit_tried and not self.fitted:
            self.fitted = True
            if transfer.fit_ran:
                cost += self.fit_ms
        # A Follower's fit replays the staircase it has read: each kept
        # sample corrected and pooled again [measured-v2, M7 replay].
        replayed = getattr(transfer, "replayed", 0)
        if replayed:
            transfer.replayed = 0
            cost += REPLAY_MS_PER_SAMPLE * replayed
        # The Follower decoding a fast block: its trainings and its bins
        # [inferred-v2, scaled from the unix port].
        trainings = getattr(transfer, "fast_trainings", 0)
        if trainings:
            transfer.fast_trainings = 0
            cost += self.rng.choice(FAST_TRAIN_MS) * trainings
        retunes = getattr(transfer, "fast_retunes", 0)
        if retunes:
            transfer.fast_retunes = 0
            cost += self.rng.choice(FAST_RETUNE_MS) * retunes
        fed = getattr(transfer, "fast_bins_fed", 0)
        if fed:
            transfer.fast_bins_fed = 0
            cost += FAST_BIN_MS * fed
        self.busy_until = max(self.busy_until, now + cost)
        # The Leader's burst: played by DMA from the next PWM wrap, the
        # loop blocked until it is out (fasttx.Pacer.transmit()).
        burst = getattr(transfer, "fast_burst", None)
        if burst is not None and t >= burst[0] - 50:
            self.play_burst(now, burst[1], burst[2])
        due = self.start_at + transfer.due_us() / 1000.0 / self.rate
        self.next_pass = max(self.busy_until, due) \
            + self.rng.uniform(0, WAKE_MS)

    def play_burst(self, now, symbols, periods):
        protocol = self.protocol
        start = now + self.rng.uniform(0, FAST_COMMON_MS)
        symbol_ms = periods * FAST_COMMON_MS
        high = self.duty(1.0)
        low = self.duty(protocol.LOW_BACKLIGHT)
        for i, s in enumerate(symbols):
            self.panel.schedule(start + i * symbol_ms, high if s else low)
        end = start + len(symbols) * symbol_ms
        # The pacer puts CC back: the slot rung's low, which it showed.
        self.panel.schedule(end, low)
        self.backlight = protocol.LOW_BACKLIGHT
        self.transfer.burst_started(int((start - self.start_at)
                                        * self.rate * 1000))
        self.busy_until = max(self.busy_until, end)

    def count_flicker_calls(self):
        """Counts the transfer's flicker calls, so each pass is charged for
        them by COST_PROFILE: wrappers on the filter instance, which its
        own methods call through too (correct() -> amount())."""
        flicker = self.transfer.flicker
        counts = self.flicker_calls = {"amount": 0, "window": 0, "part": 0,
                                       "track": 0, "update": 0, "learner": 0,
                                       "collect": 0}
        amount, amounts = flicker.amount, flicker.amounts
        track, update = flicker.track, flicker.update
        collect = flicker.collect

        def counted_amount(now_us, span_us=0):
            # Priced by the path it takes: a fitted table's, the learner's
            # sines, or -- steady, or nothing learnt -- an early return the
            # measured slot work already holds. Until 2026-10-09 every call
            # was priced as a fitted one, 0.58 ms: ~2.9 ms a slot charged
            # to every EXACT badge in a steady room.
            if flicker.harmonics is not None:
                counts["amount"] += 1
            elif flicker.active_now():
                counts["learner"] += 1
            return amount(now_us, span_us)

        def counted_collect(now_us, value, span_us):
            # The learner's point; a fitted filter's is a track(), counted
            # there, and a steady one's returns at once.
            if flicker.harmonics is None and not flicker.steady:
                counts["collect"] += 1
            return collect(now_us, value, span_us)

        def counted_amounts(times, n, span_us, out):
            if flicker.harmonics is not None:
                counts["window"] += 1
                counts["part"] += n
                # Its calls are the hoist's, not amount()'s.
                saved = counts["amount"]
                amounts(times, n, span_us, out)
                counts["amount"] = saved
            else:
                amounts(times, n, span_us, out)

        def counted_track(now_us, value, span_us):
            counts["track"] += 1
            return track(now_us, value, span_us)

        def counted_update():
            counts["update"] += 1
            return update()
        flicker.amount = counted_amount
        flicker.amounts = counted_amounts
        flicker.track = counted_track
        flicker.update = counted_update
        flicker.collect = counted_collect

    def flicker_cost(self):
        counts = self.flicker_calls
        prices = FLICKER_COSTS[self.cost_profile]
        cost = 0.0
        for name in counts:
            if counts[name]:
                cost += prices[name] * counts[name]
                counts[name] = 0
        return cost

    def show(self, now, field):
        backlight = self.protocol.LEVEL_SCHEMES[self.transfer.scheme][field]
        if backlight != self.backlight:
            self.backlight = backlight
            self.panel.schedule(now + self.rng.uniform(*TX_LATE_MS)
                                + self.rng.uniform(0, self.pwm_ms),
                                self.duty(backlight))


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
            l_out = leader.panel.output(self.now)
            f_out = follower.panel.output(self.now)
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

def _each(value, which):
    """`value` for badge `which`: a (Leader, Follower) pair, or one for both."""
    return value[which] if isinstance(value, tuple) else value


def build_loop(protocol, payload, ambient=(200, 240), contrast=9000,
               noise=80, start_skew_ms=0, seed=1, check=True, sensor_sd=None,
               quantum=1, offset=None, room_light=0, sensor_lag=False,
               duty=backlight_duty_v2, k_scale=1.0, flicker=0.0,
               flicker_hz=120, light=None, mains_drift_hz=0.0,
               work_scale=1.0, ppm=(0.0, 0.0), ring_sd=None,
               fit_ms=LOOP_FIT_MS, self_coupling=0.0, cost_profile=None,
               gc_ms=None, pwm_ms=PWM_PERIOD_MS, fast=None):
    """A Leader and a Follower, as the app runs them.

    With `sensor_sd` -- the measured optics -- `offset` is the ADC floor,
    `room_light` the room's light in counts, and `sensor_lag` turns on the
    light-dependent response, `k_scale` times as slow as measured; the
    ring's readings then carry `ring_sd` (RING_SD by default). `ppm` is each
    badge's crystal error. `self_coupling` is the share of a badge's own
    output that reaches its own sensor, off the peer's glass [unmeasured]: 0
    by default. `cost_profile` prices the flicker filter's calls
    (FLICKER_COSTS; COST_PROFILE by default), and `gc_ms` a collection's
    stall (GC_MS by default; GC_MS_HW1 the long collections of 2026-10-08
    and -09); `pwm_ms` the backlight's PWM period (v2's by default). `pwm_ms`
    and `duty` may each be a (Leader, Follower) pair, for badges on
    different firmware (mixed_firmware()). `fast` is the fast block's
    module (fastblock.py) for both badges, or a (Leader, Follower) pair with
    None for a badge without it; None: neither.
    """
    rng = random.Random(seed)

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
        ring = held.ListRing(RING_ENTRIES, RING_ENTRY_US, RING_LENGTH)
        return readers.RingReader(ring, ticks_us, per_entry=RING_READS,
                                  usable_us=RING_USABLE_US)

    def badge(which, payload_or_none, start):
        reader_ = make_reader(which)
        transfer = protocol.Transfer(which == 0, payload_or_none, reader_,
                                     fast=_each(fast, which) or fastblock)
        sd = None
        if sensor_sd is not None:
            sd = RING_SD if ring_sd is None else ring_sd
        made = LoopBadge(protocol, transfer, LoopBacklight(),
                         sensor(which), random.Random(seed * 7 + which),
                         reader_, start_at=start, duty=_each(duty, which),
                         work_scale=work_scale, ppm=ppm[which],
                         ring_sd=sd, fit_ms=fit_ms,
                         cost_profile=cost_profile, gc_ms=gc_ms,
                         pwm_ms=_each(pwm_ms, which))
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

# The firmware for build_loop(): v2's PWM period and backlight curve by
# default, v3's with LOOP_V3.
LOOP_V2 = dict()
LOOP_V3 = dict(pwm_ms=PWM_PERIOD_MS_V3, duty=backlight_duty_v3)
FIRMWARE_KEYS = ("pwm_ms", "duty")
FIRMWARE_DEFAULTS = dict(pwm_ms=PWM_PERIOD_MS, duty=backlight_duty_v2)


def mixed_firmware(lead, follow):
    """A pair on different firmware: build_loop() keywords taking each
    badge's PWM period and backlight curve from its own profile (LOOP_V2,
    LOOP_V3). Interpreter costs are not split: the simulator prices v2's
    for both [unmeasured-v3]."""
    out = {}
    for key in FIRMWARE_KEYS:
        out[key] = (lead.get(key, FIRMWARE_DEFAULTS[key]),
                    follow.get(key, FIRMWARE_DEFAULTS[key]))
    return out

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
    # Room 5 of 2026-10-07a, facing its lamp: 120 Hz 1688 counts, 240 Hz
    # 983 of a 3637 mean [measured-all]; 360 Hz not measured, so absent.
    "room5": dict(mains_hz=60.0, light_shape=((2, 1.0), (4, 0.58))),
    # The same with 360 Hz at 0.18 of 120 Hz: what the fit's residual there,
    # 99.9 counts rms, back-calculates to if it were all 360 Hz seen through
    # a 2 ms mean (sinc 0.34) [inferred-all]: room 5 in section 6 of
    # .agents/optical_data_transfer/data.md.
    "room5x6": dict(mains_hz=60.0,
                    light_shape=((2, 1.0), (4, 0.58), (6, 0.18))),
}

# The sensor as measured, for build_loop(): the ADC floor centred at 40 in
# the dark, a reading every pass rather than the median of three, and its own
# light-dependent lag. `room_light` 48 is the lit room, 80 counts read less
# the 32-count dark floor.
LOOP_SENSOR_V2 = dict(sensor_sd=6.0, quantum=16, offset=40, sensor_lag=True)


# --- running it -------------------------------------------------------------

# Each runs every row. With the measured optics (`sensor_sd`), `ambient` and
# `noise` give way to the sensor's own, and a scenario is its start skew:
# when the Follower starts after the Leader, in ms.
SCENARIOS = {
    "ideal":        dict(noise=0,   start_skew_ms=0,    ambient=(200, 200)),
    "datasheet":    dict(noise=80,  start_skew_ms=0,    ambient=(200, 200)),
    "realistic":    dict(noise=80,  start_skew_ms=1500, ambient=(200, 240)),
    "close start":  dict(noise=80,  start_skew_ms=300,  ambient=(200, 240)),
    "bright room":  dict(noise=200, start_skew_ms=1500, ambient=(30000, 31000)),
    "late start":   dict(noise=80,  start_skew_ms=4000, ambient=(200, 240)),
}

# The sensor as measured, in counts. Through a mirror, held flat, the
# backlight's whole swing on a white screen read 80 counts in the dark (2026-10-03) over a 32-count floor,
# with held levels' readings scattered at a standard deviation of 2-8 counts
# and every reading a whole 16-count step [measured-all]. A peer badge is
# predicted, not measured, from the geometry of what the sensor sees
# (.agents/optical_data_transfer/optical-coupling.md): about the mirror's swing when the two
# screens are aligned edge to edge, two to three times it when the peer's
# screen overlaps the sensor by 2-5 mm, because the mirror's own loss goes
# and the lit region fills more of the sensor's view.
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
        got = link.follower.transfer.received
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
    # --v3: v3's PWM period and backlight curve. --optics=mirror|aligned|
    # overlap: the sensor as measured, with the swing a mirror gave or a peer
    # badge is predicted to.
    protocol = load_module("share_wifi", "protocol")
    extra = dict(LOOP_V3 if "--v3" in sys.argv else LOOP_V2)
    swings = {"mirror": SWING_MIRROR, "aligned": SWING_PEER_ALIGNED,
              "overlap": SWING_PEER_OVERLAP}
    for arg in sys.argv:
        if arg.startswith("--optics="):
            extra.update(LOOP_SENSOR_V2, contrast=swings[arg.split("=", 1)[1]])
    report(protocol, **extra)
