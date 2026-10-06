# Optical coupling: how much light reaches the sensor

How bright the panel is, how much of that reaches the light sensor through a
mirror or from a second badge, and what the simulator now assumes about both.
The mirror runs measured one geometry; this is the reasoning that carries
them to others. Confidence markers follow [README.md](README.md). The
geometry is [`tests/share_wifi/optics.py`](../../tests/share_wifi/optics.py)
(`python3 tests/share_wifi/optics.py` prints the tables below).

## The panel's brightness

**About 250 cd/m² white at full backlight** **[datasheet]**, from the
`JAX28B045`'s typical luminance (see [badgeware-hardware.md](badgeware-hardware.md)).
`display.backlight(1.0)` is 100% PWM duty, which the protocol uses for every
field. The middle field measured 0.4-0.5 of the swing, so roughly 100-125
cd/m²; black, at the datasheet's 500:1, about 0.5.

## What fills the sensor's view

A lit surface of luminance L lights a sensor with illuminance E = L x P, where P
is the projected solid angle it fills (sr), at most pi for a surface filling
the whole view. So **the swing scales with P, and the ratio of two swings
needs no absolute brightness at all** -- which is why a mirror measurement can
predict a second badge without knowing the panel's real output.

The sensor sits about 1 mm outside the right edge of the 56.9 x 42.7 mm active
area, at mid height. In a mirror at distance d it sees its own screen's image
at about 2d -- 2 x (air gap + glass thickness / 1.5) for a back-silvered mirror
-- still 1 mm to one side. A peer badge at gap g is the same picture at g, but
with no mirror loss, and with its screen wherever the hold puts it.

| Geometry | P (sr), cosine sensor |
|---|---|
| mirror, held flat (image at 3.5-7 mm), the old two bands lit | 1.09-1.17 |
| mirror, held flat, whole screen lit | 1.12-1.27 |
| mirror, tilted with the sensor side ~6 mm off (image ~18 mm), whole screen | 1.11 |
| peer, aligned edge to edge (its screen 1 mm beside the sensor), 3-7 mm | 1.06-1.27 |
| peer, its screen overlapping the sensor by 2 mm | 1.9-2.4 |
| peer, overlapping by 5 mm | 2.4-2.9 |
| peer, overlapping by 10 mm | 2.7-3.05 |

**The model agrees with all three mirror runs, within their resolution.** It
predicts the tilted whole-screen run (2026-10-04) about 6% below the flat
banded one (2026-10-03), and the flat whole-screen run (2026-10-05) under 10%
above it; measured, in the dark, 64 and 80 against 80 -- the tilt one ADC
step down, the whole screen no change a step can show.

### Absolute: consistent, not needed

Taking 250 cd/m², a back-silvered mirror's ~85% and P = 1.13, the flat mirror
run's sensor received about **240 lux** and read it as 80 counts: **about 48
lux an ADC step**, and the 12-bit range about **200,000 lux** **[inferred-all]**.
Direct sunlight is about 100,000. **The sensor circuit is scaled for
daylight**, which is why a screen it is pressed against moves it four or five
steps. The lit room's 48 counts above the dark floor come out at ~145 lux,
plausible for a room with its lights on.

## Predicted swing from a second badge

Scaling the flat mirror run's 80 dark counts by P_peer / (0.85 x P_mirror):

| Hold | Predicted swing (dark) | ADC steps |
|---|---|---|
| aligned edge to edge, 3-5 mm | ~90-100 counts | 5.5-6.5 |
| peer's screen 2 mm over the sensor | ~175-200 | 11-12 |
| peer's screen 5 mm over the sensor | ~215-240 | 13-15 |
| fully covered | ~255 | 16 |

**[inferred-all]**, and the uncertainty is mostly in the inputs: the sensor's
angular response (a narrower one than cosine favours a peer overlapping it,
whose light arrives head on), how far it sits behind the front, the mirror's
glass and reflectance, and where the screen is in the case. Call it ±30%.
**The hold matters more than anything else here**: two badges held flush and
square put each sensor over the other's bezel, not its screen. So every
transfer -- and Dev Mode's 2nd-badge tool -- now starts on a screen with a
line down the middle, and the other badge's edge goes on it: half its screen
over this badge's sensor. Dev Mode's `averaging` test measures the swing
that hold gives, and the `eye` test what it can carry.

## What the simulator now assumes

`tests/share_wifi/simulator.py` used to model the sensor as a 9000-count
swing with ±80 of uniform noise, every count resolved -- about 100 times
cleaner than the badge. It now also has the sensor as measured
(`SENSOR_V2`, `--optics=mirror|aligned|overlap`): readings floored to 16-count
steps, Gaussian noise of standard deviation 6 (held levels measured 2-8), the
app's median of three immediate readings, and the floor centred at 40 (a
reading of 32 is anything from 32 to 47). The swing is `SWING_MIRROR` (80),
`SWING_PEER_ALIGNED` (95) or `SWING_PEER_OVERLAP` (210). `--v2` adds the
measured timing. The idealised model is still the default, so every earlier
figure reproduces.

8 seeds x 6 scenarios x 3 payloads, 144 runs each, the fields scheme:

| Swing | Timing | `MIN_CONTRAST` 192 (the old floor) | 96 | 48 |
|---|---|---|---|---|
| 80, the mirror | ideal: 90 Hz, no present delay | 0 | 0 | **130** |
| 95, peer aligned | v2 as measured | 0 | 0 | **130** |
| 210, peer overlapping | v2 as measured | 0 | 102 | 102 |
| 210, peer overlapping | ideal | 0 | **141** | 141 |

- **`MIN_CONTRAST` was what refused, not the physics.** At 192 nothing
  delivers -- not even the best predicted hold, and mostly as `ERR_NO_PEER`,
  since the staircase reader and the beacons never see enough swing to start.
  At 48 the mirror's own 80-count swing decodes 130 of 144 at ideal timing,
  three levels and all, with the noise and quantisation as measured.
- **Then timing costs a further 10-40 runs.** At v2's measured timing the
  overlapping hold does *worse* than the aligned one (102 against 130), and 38
  of its failures are the Leader giving up after the Follower already had the
  payload (`ERR_LOST_PEER` / `OK`) -- the closing handshake, not the data.
  Not yet understood.
- **Since changed.** `MIN_CONTRAST` has been replaced by a check of each
  gap against the link's own measured noise -- "Separability" in the
  protocol. It was set at six times the
  single-reading noise to refuse links that would fail later and less
  legibly; whether 48 refuses enough is a question for the mirror loopback
  tests (eye diagram, symbol errors), not the simulator.

What this model still leaves out: noise is independent from reading to
reading (the flicker bursts support it; mains flicker under room lights would
not be), the sensor's response is linear, the swing does not vary with how
steadily the badges are held, and the peer swings are predictions.

## Could a webcam measure the panel?

**Relative brightness, yes; absolute, not without a reference.** A webcam
pressed against the screen is out of focus, which is fine -- it averages a
patch, like the sensor -- and its 8-bit pixels resolve far finer than the
sensor's four steps. With its exposure, gain and white balance locked
(`v4l2-ctl`, in the `v4l-utils` package, not installed here; `ffmpeg` is) and
its output's sRGB gamma undone, it could measure:

- the levels' shares: the middle field, every grey of the gamma ramp, and the
  backlight curve at a resolution the sensor cannot reach;
- the backlight's PWM frequency, from the bands a rolling shutter draws across
  a frame;
- possibly the panel's settling, from how a transition smears across the rows
  of one frame (each row is exposed tens of microseconds after the last).

It cannot measure **cd/m²** without something of known luminance to compare
against, and it cannot measure the two biggest unknowns at all: how the
badge's own sensor responds, and how much of a peer's screen it sees. So it
would sharpen the simulator's panel, not its sensor or its geometry.
