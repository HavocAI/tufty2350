"""Two badges pointed at each other, simulated end to end.

Markers such as [measured-v2] in the comments below name the firmware a claim
holds for: legend in .agents/docs/README.md.

Runs the real protocol from first beacon to final Complete Pattern: discovery,
calibration in both directions, rate selection, payload, acknowledgements. The
only things faked are the physical layer and the clock.

What is modelled:

- Each badge's LCD as a first-order lag, so a field takes time to appear. This
  is the thing that decides whether a slot rate works, and leaving it out makes
  every rate look fine.
- The backlight the app sets with each symbol, through the firmware's own
  value-to-PWM-duty curve, and optionally the PWM ripple (`--flicker`). Either
  of the protocol's LEVEL_SCHEMES; `--auto` lets calibration choose between them.
- Ambient light, sensor noise, and the two badges seeing slightly different
  ambient levels.
- Frame jitter, because badge.ticks_delta is not constant.
- Start skew: nobody presses Share and Receive at the same instant.

What is not modelled: anything about the panel that is not a first-order lag,
ambient drift during a transfer, and the badges moving relative to each other.
A pass here means "agrees with our model of the hardware", not "works".
"""

import math
import random


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


# The LED is gated by that PWM at sys_clk / 65536 -- about 3.8kHz at 250MHz, a
# 260us period. The light leaving the panel therefore averages to `duty` but is
# really full-on or off from one instant to the next. [inferred-all]
#
# Whether the sensor sees the average or the instant is `pwm_ripple`:
# 0.0 = averaged (a backlight driver that smooths PWM into a steady current, or
#       a sensor far slower than 260us)
# 1.0 = instantaneous: each read lands on an on- or off-phase, chosen by duty.
#       What the hardware notes predict -- the sensor is a phototransistor that
#       responds in microseconds, and light_level() is a single ADC conversion.
# Measured since: the badge's sensor averages, at every duty from 0.25 to 1.0
# [measured-v2] -- a PWM frequency both firmwares share. The default assumes
# averaging; only
# the backlight scheme depends on it, and `--flicker` shows it failing there.
# The default fields scheme never sets a partial backlight, so it is immune.

# The pixels' share of the panel's swing. The middle field's is an ASSUMPTION
# about what the app transmits, and it has been wrong before: the app once sent
# color.grey, 7% of the swing, while this modelled 45% and every scenario
# passed. MID_FIELD_RGB measured at 40% on hardware, as the tinted
# rgb(151, 162, 146) it was then; its full-white successor keeps the share.
PIXEL_SHARE = {"black": 0.0, "white": 1.0}
MID_FIELD_SHARE = 0.45


def scheme_model(protocol, scheme=None):
    """(levels, backlight) for Panel, from protocol.LEVEL_SCHEMES.

    The same table the app draws from, so the simulator cannot drift from what
    the app transmits except through the pixel shares above.
    """
    levels, backlight = {}, {}
    table = protocol.LEVEL_SCHEMES[scheme or protocol.SCHEME_FIELDS]
    for symbol, (pixels, light) in table.items():
        levels[symbol] = PIXEL_SHARE.get(pixels, MID_FIELD_SHARE) \
            if isinstance(pixels, str) else MID_FIELD_SHARE
        backlight[symbol] = light
    return levels, backlight


class Panel:
    """An LCD whose luminance chases the field it has been told to show.

    `settle_ms` is the time to reach ~90% of a full swing. The datasheet gives
    16ms for a black->white->black round trip, so ~8ms for one transition, and
    grey-to-grey on IPS is usually about twice that.

    `levels` is the pixels' share of the swing per symbol, and `backlight` the
    `display.backlight()` value set with it. backlight=None holds it constant,
    which is how the app transmitted before it drove the backlight per symbol.
    Unlike the liquid crystal, the backlight has no lag: it changes the moment
    the field does.
    """

    def __init__(self, settle_ms, brightness=1.0, backlight=None, levels=None,
                 pwm_ripple=0.0, rng=None, schemes=None):
        self.tau = max(0.5, settle_ms / 2.3)
        self.brightness = brightness
        self.level = 0.0
        # scheme -> (levels, duty). None is the fallback: `levels` and
        # `backlight` as given, used for any scheme `schemes` does not name.
        self.models = {None: (levels or self.LEVELS, self._duty(backlight))}
        for name, (scheme_levels, scheme_backlight) in (schemes or {}).items():
            self.models[name] = (scheme_levels, self._duty(scheme_backlight))
        self.pwm_ripple = pwm_ripple
        # Its own stream, so a run with the backlight held constant draws
        # exactly the random numbers it always did.
        self.rng = rng or random.Random(0)

    # The fields scheme, for a Panel built without `levels`. build() always
    # passes them, from scheme_model().
    LEVELS = {" ": 0.0, "_": MID_FIELD_SHARE, "-": 1.0}

    # The badge on the desk is v2, and MID_BACKLIGHT is set for its curve.
    @staticmethod
    def _duty(backlight):
        if backlight is None:
            return None
        return dict((s, backlight_duty_v2(v)) for s, v in backlight.items())

    def show(self, field, dt_ms, scheme=None):
        """Light leaving the panel, showing `field` in `scheme`."""
        levels, duty = self.models.get(scheme, self.models[None])
        target = levels[field]
        self.level += (target - self.level) * (1 - math.exp(-dt_ms / self.tau))
        return self.level * self.light(field, duty) * self.brightness

    def light(self, field, duty):
        """The backlight's output as the sensor sees it, 0.0-1.0."""
        if duty is None:
            return 1.0
        duty = duty[field]
        if duty in (0.0, 1.0) or not self.pwm_ripple:
            return duty
        instant = 1.0 if self.rng.random() < duty else 0.0
        return duty + self.pwm_ripple * (instant - duty)


class Sensor:
    """A phototransistor reading the peer's panel, plus ambient and noise.

    By default an idealised one: `noise` is the half-width of uniform noise
    and every count is resolved. With `sd` set it is the badge's as measured:
    Gaussian noise of that standard deviation, readings floored to whole
    `quantum`s (16 -- a 12-bit ADC read as u16), and the app's median of
    `reads` immediate readings (sample_light() takes three).
    """

    def __init__(self, ambient, contrast, noise, rng, sd=None, quantum=1,
                 reads=1):
        self.ambient = ambient
        self.contrast = contrast
        self.noise = noise
        self.rng = rng
        self.sd = sd
        self.quantum = quantum
        self.reads = reads

    def read(self, peer_level):
        if self.sd is None:
            value = self.ambient + peer_level * self.contrast
            value += self.rng.uniform(-self.noise, self.noise)
            return max(0, min(65535, int(value)))
        values = sorted(self._one(peer_level) for _ in range(self.reads))
        return values[len(values) // 2]

    def _one(self, peer_level):
        value = self.ambient + peer_level * self.contrast
        value += self.rng.gauss(0, self.sd)
        value = int(value) // self.quantum * self.quantum
        return max(0, min(65535, value))


class SimBadge:
    """A Transfer plus the hardware it would be running on.

    `present_ms` is how long after a frame starts its field reaches the panel.
    Zero, the default, puts it there at once, as every result quoted before
    2026-10-04 assumed. On the badge it is the frame's drawing, the wait for
    vertical blank and the write -- see PRESENT_MS_V2 -- and it can be longer
    than a frame, so changes queue.
    """

    def __init__(self, transfer, panel, sensor, frame_ms, rng, start_at=0,
                 present_ms=0.0):
        self.transfer = transfer
        self.panel = panel
        self.sensor = sensor
        self.frame_ms = frame_ms
        self.rng = rng
        self.start_at = start_at
        self.present_ms = present_ms
        self.field = " "            # what the Transfer last decided
        self.scheme = None
        self.shown = " "            # what the panel has been told to show
        self.shown_scheme = None
        self.pending = []           # (when, field, scheme), oldest first
        self.next_frame = start_at
        self.started = False

    def present(self, now):
        """Hand the panel every field whose present has completed by `now`."""
        while self.pending and self.pending[0][0] <= now:
            _, self.shown, self.shown_scheme = self.pending.pop(0)

    def due(self, now):
        return now >= self.next_frame

    def tick(self, now, peer_level):
        if not self.started:
            self.transfer.started = now
            self.transfer.phase_started = now
            self.started = True
        light = self.sensor.read(peer_level)
        self.field = self.transfer.step(int(now), light)
        self.scheme = self.transfer.scheme
        if self.present_ms:
            self.pending.append((now + self.present_ms, self.field, self.scheme))
        else:
            self.shown, self.shown_scheme = self.field, self.scheme
        # Frame times wobble; badge.ticks_delta is not a constant.
        jitter = self.rng.uniform(-0.15, 0.15) * self.frame_ms
        self.next_frame = now + self.frame_ms + jitter
        return self.field


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

    def _state(self, now):
        return ("t=%.0fms leader[phase=%d sub=%d rung=%dms epoch=%d blk=%d "
                "res=%s] follower[phase=%d sub=%d rung=%dms epoch=%d "
                "tokens=%d res=%s]"
                % (now,
                   self.leader.phase, self.leader.sub,
                   self.leader.clock.duration, self.leader.clock.epoch,
                   self.leader.block_index, self.leader.result,
                   self.follower.phase, self.follower.sub,
                   self.follower.clock.duration, self.follower.clock.epoch,
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
                              self.leader.clock.duration,
                              self.follower.clock.duration))
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
        duration = self.leader.clock.duration
        skew = (self.leader.clock.epoch - self.follower.clock.epoch) % duration
        phase_error = min(skew, duration - skew)
        if phase_error <= duration / 4.0:
            self.skew_since = None
            return
        if self.skew_since is None:
            self.skew_since = now
            return
        if now - self.skew_since > self.protocol.LOST_PEER_SLOTS * duration:
            self._fail(now, "I2",
                       "slot grids %.1fms apart on a %dms slot for %.0fms "
                       "(%.2f slots, limit 0.25)"
                       % (phase_error, duration, now - self.skew_since,
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
    """Drives two SimBadges against each other on a shared timeline."""

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

    def run(self, limit_seconds=180):
        deadline = limit_seconds * 1000
        last = None
        while self.now < deadline:
            # Each badge's panel keeps settling whether or not it drew a frame.
            self.leader.present(self.now)
            self.follower.present(self.now)
            l_level = self.leader.panel.show(self.leader.shown, self.step_ms,
                                             self.leader.shown_scheme)
            f_level = self.follower.panel.show(self.follower.shown,
                                               self.step_ms,
                                               self.follower.shown_scheme)

            if self.leader.due(self.now):
                self.leader.tick(self.now, f_level)
            if self.follower.due(self.now):
                self.follower.tick(self.now, l_level)

            state = (self.leader.transfer.phase, self.follower.transfer.phase)
            if state != last:
                self.log.append((self.now, state))
                last = state

            if self.invariants is not None and self.violation is None:
                try:
                    self.invariants.check(self.now)
                except InvariantError as broken:
                    # Record the FIRST divergence and stop checking, but keep
                    # running: the result codes the run ends with are still
                    # worth seeing, and every later violation is downstream of
                    # this one.
                    self.violation = str(broken)

            if self.leader.transfer.result and self.follower.transfer.result:
                break
            self.now += self.step_ms
        return self.now / 1000

    # Raises with the time and both badges' state if anything broke, so a test
    # can be a bare `link.run(...); link.assert_invariants()`.
    def assert_invariants(self):
        if self.violation:
            raise InvariantError(self.violation)


# `contrast` is the swing the sensor sees across the peer's full black-to-white
# range: 64-80 counts through a mirror [measured-all]; from a second badge,
# [unmeasured] -- see .agents/docs/optical-coupling.md for the prediction.
#
# It is NOT a function of how much of the screen transmits. The sensor sits
# about 1mm outside the right edge at mid height and reads a patch near itself,
# so the swing depends on whether that patch lands on a transmit band -- near
# full if it does, near nothing if it does not, and somewhere between if it
# straddles an edge. 9000 models the intended case: a band under the sensor.
#
# Sweep it rather than trusting it. The link still delivers 94-95 of 96 runs
# down to 1600, so the number below is not what the protocol's margin rests on.
def build(protocol, payload, settle_ms=16, ambient=(200, 240), contrast=9000,
          noise=80, frame_ms=11.1, start_skew_ms=0, seed=1, check=True,
          schemes=None, backlight=None, levels=None, pwm_ripple=0.0,
          follower_schemes=None, present_ms=0.0, sensor_sd=None, quantum=1,
          reads=1):
    """A Leader and Follower ready to be run from their very first frame.

    `schemes` is what each Transfer may use -- the protocol's default, fields
    only, if omitted -- and each panel shows whichever scheme its Transfer is
    in, modelled from protocol.LEVEL_SCHEMES. `backlight` and `levels` replace
    that model for every scheme, for trying ones the protocol does not have;
    `pwm_ripple` is the sensor model. See Panel. `follower_schemes`, if
    given, is the Follower's own setting, for pairing two badges set up
    differently.
    """
    rng = random.Random(seed)
    if levels or backlight:
        models = None
    else:
        models = dict((name, scheme_model(protocol, name))
                      for name in protocol.LEVEL_SCHEMES)
    allowed = schemes or (protocol.SCHEME_FIELDS,)

    def panel(which):
        return Panel(settle_ms, backlight=backlight, levels=levels,
                     pwm_ripple=pwm_ripple, schemes=models,
                     rng=random.Random(seed * 2 + which))
    leader = protocol.Transfer(True, payload, frame_ms=lambda: int(frame_ms),
                               schemes=allowed)
    follower = protocol.Transfer(False, frame_ms=lambda: int(frame_ms),
                                 schemes=follower_schemes or allowed)

    def sensor(which):
        return Sensor(ambient[which], contrast, noise, rng, sd=sensor_sd,
                      quantum=quantum, reads=reads)

    sim_leader = SimBadge(
        leader, panel(0), sensor(0),
        frame_ms, rng, start_at=0, present_ms=present_ms)
    sim_follower = SimBadge(
        follower, panel(1), sensor(1),
        frame_ms, rng, start_at=start_skew_ms, present_ms=present_ms)
    checker = Invariants(protocol, leader, follower, payload) if check else None
    return Link(sim_leader, sim_follower, invariants=checker)


# --- continuous badges --------------------------------------------------------
# The app as it now runs a transfer: a loop that reads the sensor back to
# back for a sample, hands the protocol their mean, and never presents while
# it listens. Only a change of pixels goes back to run() to be presented; a
# change of backlight is a display.backlight() call, live at once. Modelled
# from the badge as measured:
#
# - reads every LOOP_READ_MS for FINE_MS, or up to the next symbol boundary
#   while transmitting; then the protocol's call, LOOP_CALL_MS give or take,
#   reading nothing [measured-v2: 0.8-1.5 ms, 2026-10-06b]; its field takes
#   effect when it returns; none while the badge is presenting;
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
LOOP_CALL_MS = 1.0
# The flicker's fit, once a transfer, stalls the loop that long: 3.3 ms on
# MicroPython's unix port, and protocol work has run up to ~50 times slower
# on the badge [inferred-v2]. Pessimistic on purpose.
LOOP_FIT_MS = 150.0
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
    """A Transfer driven the way the app's listen loop drives it."""

    def __init__(self, protocol, transfer, panel, sensor, rng, start_at=0.0,
                 refresh_ms=16.7, pixel_dead_ms=7.0, read_ms=LOOP_READ_MS,
                 duty=backlight_duty_v2, models=None, call_ms=LOOP_CALL_MS):
        self.protocol = protocol
        self.transfer = transfer
        self.panel = panel
        self.sensor = sensor
        self.rng = rng
        self.start_at = start_at
        self.refresh_ms = refresh_ms
        self.phase_ms = rng.uniform(0, refresh_ms)     # where its blanks fall
        self.pixel_dead_ms = pixel_dead_ms
        self.read_ms = read_ms
        self.duty = duty
        self.models = models
        self.next_read = start_at
        self.busy_until = start_at
        self.started = False
        self.pixels = None          # what has been drawn
        self.backlight = 1.0
        self.presents = 0
        self.call_ms = call_ms
        self.fitted = False
        # The sample being read: its start, its readings' sum, count and
        # sum of times, and the deadline the protocol gave for it.
        self.window = start_at
        self.total = self.count = 0
        self.times = 0.0
        self.deadline = None

    def next_blank(self, now):
        k = math.ceil((now - self.phase_ms) / self.refresh_ms)
        return self.phase_ms + k * self.refresh_ms

    def due(self, now):
        return now >= self.next_read and now >= self.busy_until

    def tick(self, now):
        transfer = self.transfer
        protocol = self.protocol
        if not self.started:
            transfer.started = int(now)
            transfer.phase_started = int(now)
            self.started = True
            self.window = now
        if not self.count:
            self.window = now
        self.total += self.sensor.read()
        self.count += 1
        self.times += now
        step = self.read_ms * self.rng.uniform(0.9, 1.1)
        done = now - self.window >= protocol.FINE_MS
        if self.deadline is not None and int(now) >= self.deadline:
            done = True
        if not done:
            self.next_read = now + step
            return
        # read_sample() ends: the protocol's call, reading nothing.
        t = self.times / self.count
        span = now - self.window + step
        field = transfer.sample(int(now), int(t), self.total // self.count,
                                int(t * 1000), int(span * 1000))
        self.total = self.count = 0
        self.times = 0.0
        self.deadline = transfer.deadline()
        now += self.call_ms * self.rng.uniform(0.7, 1.5)
        if getattr(transfer, "fit_tried", False) and not self.fitted:
            self.fitted = True
            now += LOOP_FIT_MS
        pixels, backlight = protocol.LEVEL_SCHEMES[transfer.scheme][field]
        if backlight != self.backlight:
            # Set first, in update(), before any present: live at the wrap.
            self.backlight = backlight
            self.panel.schedule(now + self.rng.uniform(0, PWM_PERIOD_MS),
                                "duty", self.duty(backlight))
        self.busy_until = now
        if pixels != self.pixels:
            self.pixels = pixels
            blank = self.next_blank(now)
            share = PIXEL_SHARE.get(pixels, MID_FIELD_SHARE) \
                if isinstance(pixels, str) else MID_FIELD_SHARE
            self.panel.schedule(blank + self.pixel_dead_ms, "share", share)
            self.busy_until = blank + WRITE_MS
            self.presents += 1
        self.next_read = self.busy_until


class LoopLink(Link):
    """Two continuous badges on a shared timeline, in STEP_MS steps."""

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
            leader.sensor.settle(f_out, dt)
            follower.sensor.settle(l_out, dt)
            if leader.due(self.now):
                leader.tick(self.now)
            if follower.due(self.now):
                follower.tick(self.now)
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


def build_loop(protocol, payload, settle_ms=16, ambient=(200, 240),
               contrast=9000, noise=80, start_skew_ms=0, seed=1, check=True,
               schemes=None, follower_schemes=None, refresh_ms=16.7,
               pixel_dead_ms=7.0, sensor_sd=None, quantum=1, offset=None,
               room_light=0, sensor_lag=False, duty=backlight_duty_v2,
               read_ms=LOOP_READ_MS, k_scale=1.0, flicker=0.0,
               flicker_hz=120, call_ms=LOOP_CALL_MS, light=None,
               mains_drift_hz=0.0, **_ignored):
    """A Leader and Follower that read continuously, as the app now does.

    Takes the same scenario keywords as build(). With `sensor_sd` -- the
    measured optics -- `offset` is the ADC floor, `room_light` the room's
    light in counts, and `sensor_lag` turns on the light-dependent response,
    `k_scale` times as slow as measured.
    """
    rng = random.Random(seed)
    allowed = schemes or (protocol.SCHEME_FIELDS,)
    leader = protocol.Transfer(True, payload, frame_ms=lambda: int(refresh_ms),
                               schemes=allowed, continuous=True)
    follower = protocol.Transfer(False, frame_ms=lambda: int(refresh_ms),
                                 schemes=follower_schemes or allowed,
                                 continuous=True)

    def sensor(which):
        k = SENSOR_K * k_scale * contrast / 80.0 if sensor_lag else None
        if sensor_sd is None:
            return LoopSensor(ambient[which], 0, contrast, rng, noise=noise, k=k)
        return LoopSensor(ambient[which] if offset is None else offset,
                          room_light, contrast, rng, sd=sensor_sd,
                          quantum=quantum, k=k, flicker=flicker,
                          flicker_hz=flicker_hz, mains_drift_hz=mains_drift_hz,
                          **FLICKER_LIGHTS.get(light, {}))

    def badge(transfer, which, start):
        return LoopBadge(protocol, transfer,
                         LoopPanel(settle_ms, random.Random(seed * 2 + which)),
                         sensor(which), random.Random(seed * 7 + which),
                         start_at=start, refresh_ms=refresh_ms,
                         pixel_dead_ms=pixel_dead_ms, read_ms=read_ms,
                         duty=duty, call_ms=call_ms)
    checker = Invariants(protocol, leader, follower, payload) if check else None
    return LoopLink(badge(leader, 0, 0.0), badge(follower, 1, start_skew_ms),
                    invariants=checker)


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

# The v2 badge as measured on 2026-10-04, at LORES with the whole screen:
# 60 Hz frames; a field reaching the panel ~19 ms after its frame starts (draw
# ~4 ms, wait ~4.7 ms for vertical blank, write 7.8 ms, and the measured rise
# reaching a tenth of its swing 13-19 ms after the present began -- so 16 to
# 22 ms, of which 19 is the middle); and 90% settling in 24 ms (rise tau
# 10.5 ms). Applied over a scenario, it replaces the scenario's own settle.
FRAME_MS_V2 = 16.7
PRESENT_MS_V2 = 19.0
SETTLE_MS_V2 = 24
V2_MEASURED = dict(frame_ms=FRAME_MS_V2, present_ms=PRESENT_MS_V2,
                   settle_ms=SETTLE_MS_V2)

# v3 as it would be, unmeasured [inferred-v3]: 90 Hz frames, and the same
# chain -- a few ms of drawing, the wait for vertical blank, the 7.8 ms write
# -- putting a field on the panel about 11 ms after its frame starts. The
# panel is the same, so it settles as v2's does.
FRAME_MS_V3 = 11.1
PRESENT_MS_V3 = 11.0
V3_MODEL = dict(frame_ms=FRAME_MS_V3, present_ms=PRESENT_MS_V3,
                settle_ms=SETTLE_MS_V2)

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


def report(protocol, payload=TYPICAL, seconds=200, seed=1, builder=None,
           **transmit):
    """Run every scenario and print how each one ends.

    `transmit` is passed to build(): schemes=, backlight=, levels=, pwm_ripple=.
    `builder` is build() unless given -- build_loop() for continuous badges.
    """
    builder = builder or build
    names = {v: k[6:].lower() for k, v in vars(protocol).items()
             if k.startswith("PHASE_")}
    print("%-14s %8s  %-16s %-16s %s"
          % ("scenario", "elapsed", "leader", "follower", "payload"))
    broken = []
    for label, kw in SCENARIOS.items():
        link = builder(protocol, payload, seed=seed, **dict(kw, **transmit))
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
    import os
    import sys
    # This imports from firmware/apps/, which is packed onto the badge -- so
    # leave no __pycache__ behind there. See tests/run_tests.py.
    sys.dont_write_bytecode = True
    # Runnable on its own, so it puts the shared harness on the path itself
    # rather than relying on the runner having done it. `tests/` is where the
    # harness lives -- beside the runner, shared by every app's tests.
    HERE = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.dirname(HERE))
    from harness import load_module
    # --flicker: the sensor follows the backlight's PWM rather than averaging it.
    # --auto: both badges may use the backlight, and calibration chooses.
    # --v2: the badge as measured -- 60 Hz, the present delay, a 24 ms panel.
    # --optics=mirror|aligned|overlap: the sensor as measured, in counts, with
    # the swing a mirror gave or a peer badge is predicted to.
    protocol = load_module("share_wifi", "protocol")
    allowed = ((protocol.SCHEME_FIELDS, protocol.SCHEME_BACKLIGHT)
               if "--auto" in sys.argv else (protocol.SCHEME_FIELDS,))
    # --v3: v3's timing as it would be -- 90 Hz, an 11 ms present delay.
    extra = dict(V2_MEASURED) if "--v2" in sys.argv else {}
    if "--v3" in sys.argv:
        extra = dict(V3_MODEL)
    swings = {"mirror": SWING_MIRROR, "aligned": SWING_PEER_ALIGNED,
              "overlap": SWING_PEER_OVERLAP}
    if "--loop" in sys.argv:
        # --loop: the app as it now runs a transfer, reading continuously --
        # see build_loop(). --v3 and --slow-panel pick its timing; --optics
        # the measured sensor, with its light-dependent lag.
        extra = dict(LOOP_V3 if "--v3" in sys.argv else
                     LOOP_V2_SLOW_PANEL if "--slow-panel" in sys.argv else
                     LOOP_V2)
        for arg in sys.argv:
            if arg.startswith("--optics="):
                extra.update(LOOP_SENSOR_V2,
                             contrast=swings[arg.split("=", 1)[1]])
        report(protocol, schemes=allowed, builder=build_loop, **extra)
        sys.exit(0)
    for arg in sys.argv:
        if arg.startswith("--optics="):
            extra.update(SENSOR_V2, contrast=swings[arg.split("=", 1)[1]])
    report(protocol, schemes=allowed,
           pwm_ripple=1.0 if "--flicker" in sys.argv else 0.0, **extra)
