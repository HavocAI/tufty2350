# Tufty 2350 hardware

What this specific badge can do, as opposed to what the API exposes. Read this
before relying on any limit: timing, the display, the light sensor, memory,
flash. Confidence markers are explained in [README.md](README.md).

The device is the Pimoroni **Tufty 2350** (`WARE002`). Badgeware also runs on
Badger and Blinky hardware; none of that is covered here, because none of it is
what this repo targets. Anything you read in the official docs marked "Badger
only" does not apply.

---

## The device

Pimoroni Tufty 2350, product code `WARE002`.
[Product page](https://shop.pimoroni.com/en-us/products/tufty-2350) ·
[firmware and examples](https://github.com/pimoroni/tufty2350)

| | |
|---|---|
| MCU | Raspberry Pi RP2350B, dual Arm Cortex-M33 @ 250MHz |
| RAM | 520KB SRAM + 8MB PSRAM |
| Flash | 16MB QSPI |
| Screen | 2.8" IPS TFT, 320x240 (`HIRES`) or 160x120 (`LORES`), adjustable backlight |
| Buttons | Five front (A, B, C, UP, DOWN), plus HOME and reset/sleep |
| Light sensor | Front-mounted phototransistor |
| Case lights | Four white LEDs, rear, 4-zone mono |
| Radio | 802.11 b/g/n and Bluetooth, via RM2 (CYW43439) |
| Battery | 1000mAh LiPo, MCP73831 charger at 455mA |
| Size | 84 x 76 x 20 mm |

---

## The display panel

This is the part with real numbers, because the panel is sold separately as a
[replacement part](https://shop.pimoroni.com/en-us/products/2-8-tft-lcd-panel)
and carries a manufacturer datasheet.

| | |
|---|---|
| Manufacturer | Shenzhen Jiaanxin Technology Co., Ltd |
| Part | `JAX28B045` (Pimoroni `COM3502`) |
| Driver IC | **ST7789V** |
| Panel | 2.8", 240 x RGB x 320, IPS |
| Interface | 22-pin FPC |
| Luminance | 250 cd/m2 |
| Contrast | 500:1 typ |
| Viewing angle | 80 degrees each way at CR>=10 |
| Operating temperature | -20 to +70 C |

[Datasheet](https://cdn.shopify.com/s/files/1/0174/1800/files/JAX28B045.pdf) ·
[mechanical drawing](https://cdn.shopify.com/s/files/1/0174/1800/files/JAX28B408.pdf)
(the drawing is the one to trust for pinout: Pimoroni's FPC and casing are
custom, so the datasheet's mechanics are wrong for this badge).

### Response time, and what it means for optical signalling

**[datasheet]** From the optical characteristics table:

| Item | Symbol | Condition | Min | Typ | Max | Unit |
|---|---|---|---|---|---|---|
| Response Time | Ton+Toff | theta=phi=0 | - | **16** | - | ms |

Read that carefully before using it:

- **It is a round trip.** `Ton+Toff` is white to black *and back*. A single
  transition is about **8ms** typical.
- **It is a full swing.** Measured black to white. Badgeware apps that
  discriminate a middle grey are doing grey-to-grey transitions, which on IPS
  are commonly around twice as slow as a full swing.
- **It is typical, not maximum.** No max is given.
- **It is at 25 C.** LC response degrades sharply when cold, often 2-3x near
  freezing, and this panel is rated down to -20 C.

**[inferred-all]** So a defensible settling budget for a single transition is
**16ms** — the datasheet's full round-trip figure spent on one transition, which
buys roughly 2x margin for grey-to-grey and for typ-versus-max. That is the
value the Optical Data Transfer Protocol uses for `LCD_SETTLE_MS`. It is
sourced, not measured on this
unit; measuring the real thing would still be better.

### What the hardware runs at: 60Hz on v2, 90Hz on v3

**The frame rate depends on the firmware, and the badge on the desk is the
slow one.** **[measured-v2]** by Dev Mode's *probe frame timing* on that badge,
firmware `BW-1.27.0, on 2026-04-10` (the v2.0.2 release), and recorded in
[hardware-measurement-2026-10-03.json](hardware-measurement-2026-10-03.json)
under `frame_probe`.

| | v2.x (measured) | v3.x (from source, unmeasured) |
|---|---|---|
| `display.set_framerate` | **absent** | present; `badge.mode()` calls `set_framerate(90)` on every mode change |
| Panel refresh | **16.71 ms, 59.8Hz** (`FRCTRL2` left at its init value `0x0f`) | 11.1ms, 90Hz |
| Writing one frame, vsync off | **7.78 ms** | ~7ms **[inferred-v3]** |
| GC block size | 16 bytes | 32 bytes (2026-07-31) |
| `MICROPY_GC_NO_SCAN`, `VM_IN_RAM` | no | yes (2026-06-20); the interpreter is ~20% faster |

`set_framerate` in `modules/c/st7789/st7789.cpp` writes the ST7789V `FRCTRL2`
register, picking the `RTNA` value nearest the request from the datasheet
table; it arrived on 2026-06-30. Before that nothing set the frame rate, and
the panel runs at the 60Hz its init sequence gives it. **Read the release off
the badge** (`os.uname().version`, or `frame_probe.firmware`) rather than
assuming either column.

**With `VSYNC`, a frame takes a whole number of refreshes.**
`display.update()` waits for the panel's TE line, which is high only in the
short vertical blank, then writes the frame (7.8ms). So a frame whose work
plus write fits in one refresh runs at the panel's rate, and one that misses
by a millisecond waits a whole extra refresh. On v2 the budget for everything
`run()` and `update()` do between presents is about **8.9ms** (16.7 - 7.8).
The first mirror run's frames -- 34, 134 and 151 ms at the extremes, 53.9 ms
mean -- are whole multiples of 16.7 ms, not of 11.1.

### Drawing costs more than the bus

**[measured-v2]** What a frame spends, from the probe's breakdown of the
measuring screen as it was drawn then (HIRES, every frame redrawn):

| Part | Cost |
|---|---|
| `screen.clear()` of the whole 320x240 HIRES screen | **11.4 ms** (~150 ns a pixel) |
| two 80x240 rectangles | ~6 ms, the same rate per pixel |
| four lines of text through `screen.text(rect, align=)` | 2.6 ms, 6.3 ms when a layout is new |
| `display.backlight()` and `badge.caselights()` | 0.64 ms |
| `display.update()`, vsync off | 7.78 ms |

**`run()` clears the screen before every `update()`**, to `badge.default_clear`,
so an app that fills the screen itself pays for two full-screen fills a frame
-- 22.8 ms at HIRES, more than a whole refresh before anything else is drawn.
Setting `badge.default_clear = None` skips it (`badge.clear()` tests for
None), and the framebuffer then keeps whatever was drawn last, so a screen
that has not changed need not be redrawn at all. Restore it on the way out;
HOME resets the badge, so nothing leaks into the launcher.

**`LORES` does make drawing faster -- not the bus.** It pixel-doubles into the
same 320x240 panel, so the write is the same 153,600 bytes:

| | HIRES | LORES |
|---|---|---|
| Columns pushed | 320 | 160 |
| DMA per column | 480 B | 960 B |
| **Total per frame** | **153,600 B** | **153,600 B** |
| Pixel conversions | 76,800 | 19,200 |
| **Pixels a full-screen fill touches** | **76,800** | **19,200** |

Earlier versions of this file said `LORES` "buys nothing" because the bus
traffic is identical. That was true of the write and wrong about the frame:
on v2 the write is 7.8 ms of a frame that drew for 25-34 ms. A quarter of the
pixels made a full-screen fill **3.02 ms** **[measured-v2]** (2026-10-04, with
the backlight call), so fill cost is per pixel. A `LORES` signalling frame --
`run()`'s clear off, one fill, the measurement engine -- ran **16.84 ms**, one
refresh, where the HIRES band screen ran 50.26, three. A whole mirror run at
`LORES` averaged 16.8 ms a frame against 53.9 before.

**Presenting blocks for about 12.4 ms** at `LORES` and 60Hz: a frame drawn in
~4 ms waits ~4.7 ms for the next vertical blank, then 7.8 ms to write.
Nothing in Python runs meanwhile, so a light reading cannot be taken during
it. A mirror run timed a rise reaching a tenth of its swing 13-19 ms after the
present call began, and across seven runs a rise has crossed the protocol's
`T_low` 13-23 ms after it -- later in runs where the panel itself was
slower, which none of them explained (see "Edge latency" in the protocol).

### Reading without presenting

**[measured-v2]** by *probe frame timing*'s `reads` step (2026-10-05b): loops
run back to back with nothing presented, 5,000 passes or one second each.
`loop` does nothing, so it is the loop's own cost (the clock read and the
probe's bookkeeping), taken off the others:

| Loop | Per pass | Less the loop | Passes a second |
|---|---|---|---|
| nothing (`loop`) | 46.2 µs | -- | 21,636 |
| `badge.light_level()` | 54.1 µs | **~8 µs** | 18,495 |
| `sample_light()`, the median of three reads | 131.5 µs | **~85 µs** | 7,602 |
| a listener: `time.ticks_ms()`, `sample_light()`, `SlotReceiver.feed()`, `badge.poll()`, `badge.pressed()` | 461.8 µs, at most 782 | **~416 µs** | **2,166** |

- **The ADC read is cheap and the Python around it is not.** The median of
  three costs ten times its three reads; the slot receiver, the clock and the
  buttons about 330 µs more.
- **A screen that is not presented holds its image.** The ST7789 refreshes
  the panel from its own memory **[inferred-all]**, and `badge.poll()` is
  separate from `display.update()` **[source-v2]**, so the buttons and
  `badge.ticks` keep working in such a loop.
- **So a receiver that holds its screen still gets ~108 readings a 50 ms slot**,
  about 40 of them inside the slot's read window, where presenting every frame
  leaves about one: the present blocks reading for 12.4 ms of each 16.7.
  `share_wifi`'s transfer now reads this way -- see "Reading continuously" in
  the protocol, and the third loop shape in
  [badgeware-patterns.md](badgeware-patterns.md). The probe's
  `reads.transfer` times a pass of that loop.
- **A second run agrees** (2026-10-05c): 2,362 listener passes a second,
  ~381 µs net, none over 771 µs.
- **Nothing stalled in that second** -- no pass over 782 µs, so no collection.
- **The ADC itself is far faster than any of these.** A conversion is 96
  cycles of its 48 MHz clock, 2 µs, so 500k a second **[datasheet]**, where
  `badge.light_level()` is `machine.ADC.read_u16()` -- a method call into a
  C call, ~8-14 µs over its loop **[measured-v2]**. The light sensor is
  GPIO43, ADC input 3 on the RP2350B; its registers are at `0x400A0000`
  and the ADC's DMA request is 48 (Pico SDK, RP2350 headers). `read_u16()`
  returns `raw << 4 | raw >> 8`, which for the sensor's readings, all under
  256 raw, is a multiple of 16 **[source-v2]**: the 16-count step.
- **Read straight from the registers it is 18 times faster** (2026-10-06a,
  Dev Mode's `fast_reads`, 4,096 readings each, the sensor reading ~209
  counts) **[measured-v2]**:

  | Capture | Rate | sd of 1 reading | of 4 | of 16 | of 64 | of 256 |
  |---|---|---|---|---|---|---|
  | viper, one conversion at a time | 359k a second, 2.8 µs | 4.3 counts | 2.2 | 1.1 | 0.7 | 0.5 |
  | DMA, asked 500k | 249k (a divider bug, since fixed: half the rate asked) | 8.4 | 4.1 | 2.0 | 1.0 | 0.5 |
  | DMA, 100k | 99.8k | 7.6 | 3.8 | 1.9 | 0.9 | 0.6 |

- **Averaging works.** The noise falls as the square root of the readings
  averaged up to 16-64 of them, then flattens at about half a count -- a
  thirtieth of an ADC step -- what is left being in the light, or slow.
  So averaging 64 viper readings, 180 µs, puts a reading within about a
  count, against a panel swing of 64-96: noise is no longer what limits a
  slot's length; the sensor's settle is **[inferred-all]**. Single
  readings were noisier free-running under DMA than one at a time under
  viper, 7.6-8.4 counts against 4.3; why is unknown.
  A transfer runs 20-60 s, and a collection costs a slot or more (see
  "Performance tiers"); whether one lands in a transfer is not yet known.

### What that means for optical signalling

| | v2, 60Hz | v3, 90Hz |
|---|---|---|
| frame, if the work fits one refresh | 16.7 ms | 11.1 ms |
| 2.5 x frame (samples per symbol) | 42 ms | 28 ms |
| `SETTLE_SLOTS` x `LCD_SETTLE_MS` | 48 ms | 48 ms |
| fastest rung | **50 ms** | **50 ms** |

Both pick the 50 ms rung -- but only if the frame really is one refresh. A
v2 frame that takes two (33 ms) puts 2.5 x frame at 83 ms and the rung at
100 ms. Two badges on different rungs could not talk -- a 90Hz badge paired
with a 30Hz one delivered 35 and 0 of 144 in simulation -- so calibration now
agrees the starting rung from both badges' frame-rate classes ("Agreeing a
rung" in the protocol), and the same pairs deliver 143 and 144. A SLOW badge
still costs both badges the 100 ms rung, so `share_wifi` transmits at `LORES`,
full screen, with `run()`'s clear off and the field redrawn only when it
changes, to keep a v2 frame to one refresh. See "Screen as a transmitter" in
the protocol.

## The light sensor

`badge.light_level()` returns "the level detected by the light sensor as a raw u16
value". Tufty only.

**It is 12-bit, not 16.** **[measured-all]** Every reading a badge produced in a
mirror run was a multiple of **16**, without exception, across two passes and
every level — see [hardware-measurement-2026-09-23.json](hardware-measurement-2026-09-23.json).
So the ADC has 4096 steps presented in a 16-bit frame, and **one step is 16
counts**. Nothing finer can be distinguished, which makes 16 the unit any
contrast figure is really denominated in; a threshold that is not a multiple of
it is claiming precision the hardware does not have.

**What it sees of a screen, measured.** **[measured-all]** Same run, badge held
screen-first against a mirror:

| | dark room | lit room |
|---|---|---|
| black | 32 (2 steps) | 288 (18 steps) |
| middle grey | 64 (4) | 320 (20) |
| white | 112 (7) | 368 (23) |
| **full swing** | **80 (5 steps)** | **80 (5 steps)** |
| noise, peak-to-peak | 32 (2 steps) | 32 (2 steps) |

Two things follow, and both matter more than any software choice:

- **The swing is five ADC steps.** A panel at 250 cd/m² a few millimetres away
  moves this sensor by five counts. Room light alone contributed sixteen. The
  coupling is poor — the sensor sits ~1mm *outside* the active area, so it sees
  a screen only at a glancing angle.
- **Noise is two steps of that five, peak to peak** -- but a standard
  deviation of 2-10 counts a reading, and that is what a decision is made
  against. An earlier version of this note called the 1.2x ratio of gap to
  peak-to-peak noise "a physics problem"; it confused the two measures, and
  the measured gaps are four to eight standard deviations. See
  "Separability" in the protocol.

It is a **phototransistor**, not an integrating lux chip, front-mounted on the
same face as the screen. **Its response is milliseconds, not microseconds, and
depends on how much light it has** -- see "How fast the sensor follows" below.
That is still far faster than the panel's pixels, and no bottleneck for
anything sampling once a frame.

### How fast the sensor follows

**[measured-all]** by Dev Mode's `backlight_step` (mirror; three runs, whose
90% times agree to a few tenths of a millisecond): white pixels throughout,
only the backlight switched, so neither the present nor the liquid crystal
is in the way -- `display.backlight()` is live within one ~263 µs PWM period
**[source-v2]**. Ten of each step, averaged, in ms from the call (10%, 50%,
90%), reads every 26-27 µs, from 2026-10-05e, the first run with every
capture kept:

| | Off to full | Full to off | Off to middle (0.74) |
|---|---|---|---|
| dark room | 1.92, 2.76, 3.87 | 0.19, 0.63, 3.24 | 4.06, 5.18, 7.75 |
| lit room | 0.32, 0.57, 1.20 | 0.32, 0.52, 1.25 | 0.29, 0.61, 0.97 |

The two runs before it (10-05c, -05d) were blind for the first few hundred
µs after each change -- a fault in the test, since fixed -- so their early
lit times (0.1 ms to 10%) were interpolated across the gap, and the captures
that crossed half way inside it were dropped. Up to 0.26 ms of each 10% time
here is the backlight: a new PWM duty takes effect at the next wrap
**[inferred-v2]**.

- **Slower the less light it has.** From off in the dark a rise waits 1.9 ms
  before it starts, and 4.1 ms when it is only going to the middle; a fall
  starts in 0.2 ms, while it is still bright, and trails to 3.2 as it dims.
  With the room lit, everything reaches 90% in 1.0-1.3 ms. A phototransistor's own photocurrent
  charges its capacitance, so this is what one does **[inferred-all]** -- and
  the backlight cannot know the room was lit, so it is the sensor, not the
  LED or its driver.
- **The lit falls that went missing on 10-05c** were the test's blind
  stretch: a fast fall crosses half way inside it. 10-05d's skip counts,
  added for the purpose, put them all down as `gap`s.
- **A dim low speeds the dark start.** The backlight driver gives no light
  below roughly 11% duty **[source-v3]** -- about 0.45 on v2 -- but just above
  it, at 0.5 (14% duty), the screen keeps the sensor faintly lit. On
  2026-10-06 that cut the dark edge delay from 1.2-1.9 ms to 0.3 and read under
  one ADC step above off **[measured-all]**; `share_wifi` uses it as low.

### Room light flickers

**[measured-all]** by Dev Mode's *measure room flicker* (2026-10-06): every LED
light measured flickered at 120 Hz -- twice the 60 Hz mains -- by 1% of its
light in one room, 30-47% in an open area and 0.1% in another. At a hold's
room light, 47% is more than an ADC step, at a frequency inside the slots a
transfer uses; `share_wifi` estimates and removes it. The sensor reads it
plainly once readings are binned every 0.5 ms; sampling once a 16.7 ms frame,
nearly in step with it, hides it as an offset.

### The chip's temperature

`machine.ADC(machine.ADC.CORE_TEMP)` exists on v2 **[verified-v2]**, read
through the RP2350 datasheet's line (0.706 V at 27 C, -1.721 mV a degree,
3.3 V reference) **[datasheet]**: 26-28 C on 2026-10-06. It barely moved
through a run, and did not differ between the runs where the panel's edges
were slow and fast.
- **Against the pixels:** a pixel change waits ~19 ms for the present and then
  takes 16-24 ms to settle to 90%. The slowest backlight step here, to the
  middle in the dark, is 7.8 ms; a peer's screen over the sensor, predicted at
  ~2.5x the mirror's light, should be faster still **[inferred-all]**.

**Where it is, which matters more than it sounds.** **[verified-all]** About **1mm
outside the right edge of the active area, at mid height** — not central, and not
on the glass. The active area is 56.9 x 42.7 mm for 320 x 240, so 5.6 px/mm and
that 1mm is about six pixels. Screen coordinates put (0, 0) at the top left, so
the sensor sits just past `(screen.width, screen.height // 2)`.

Two consequences for anything optical:

- **It does not read the average brightness of the whole screen.** It reads a
  patch near itself. The earlier note below, and the first drafts of the optical
  protocol, assumed otherwise and derated contrast by area on the strength of it.
  That was wrong: what matters is what is in the patch, and the answer is close to
  all-or-nothing rather than proportional.
- **Two badges facing each other are mirrored.** Held screens-together and both
  upright, a right-edge sensor looks at the peer's *left* edge. Turn one badge end
  over end and it looks at the peer's right. Both are natural holds and they want
  opposite sides of the screen, which is why `share_wifi` transmits on the
  whole screen (it once used a band down each edge).

### The auto-backlight question, resolved

Earlier versions of this file flagged a possible feedback loop: the sensor's
advertised job is adjusting the backlight, so two badges measuring each other's
screens might chase each other's brightness. **[source-v3]** **That loop does not
exist in this firmware.**

Reading `pimoroni/tufty2350`:

- `badge.light_level()` is a bare ADC read — `LIGHT_SENSOR.read_u16()`, nothing
  more.
- The backlight is manual PWM: `display.backlight(0.0-1.0)`, wrapped as
  `badgeware.set_brightness()`.
- The driver sets 230/255 once at init and never touches it again.
- **No code anywhere reads `light_level()` to drive the backlight.** The only
  other caller of `backlight()` in the whole firmware is the `tomato` app, which
  dims to 0.5 while its timer runs.

So "adjustable backlight" on the product page means manually adjustable. Optical
signalling between badges is safe from self-interference, and you also get an
explicit lever: `share_wifi` holds the backlight at 1.0 for a transfer by
default, and can instead set it per symbol -- off for black, 0.74 for its
middle level, 1.0 for high -- when calibration finds that separates the levels
better. It puts it back afterwards either way. See "Light levels and symbols"
in [optical-data-transfer-protocol.md](optical-data-transfer-protocol.md).

**Partial backlight is PWM, not a steady current.** On v3,
`ST7789::set_backlight()` maps 0.0-1.0 through gamma 2.8 onto a duty between
12.5% and 100% (0 is off) **[source-v3]**. The badge's v2 has no floor: duty is
plain `v^2.8`, which Pimoroni's own commit adding the floor describes as the
backlight "turning off ~0.45" **[source-v2]**, and the mirror runs read
backlight 0.0-0.4 the same as off **[measured-v2]**. On both, the slice runs
at `sys_clk / 65536`, about 3.8kHz. A phototransistor that responds in
microseconds could see each on- and off-phase rather than the average; this
one does not -- it averages at every duty from 0.25 to 1.0 **[measured-v2]**.

**[unverified]** What has *not* been ruled out is the panel or its driver doing
something in hardware. Nothing in the schematic suggests it, but only a bench
test proves it.

### Practical notes for optical work

- The peer's sensor reads whatever is in front of **it**, which is a patch near
  the right edge of its own badge — see above, and note this bullet used to say
  "the average brightness of the whole screen". Text or any non-uniform content
  inside that patch changes the transmitted level, so the region a peer is
  actually looking at must be a solid fill.
- The sensor is a single unfiltered channel. Colour gives no extra bandwidth —
  it only sees total brightness, so RGB multiplexing does not work.
- Three amplitude levels (black / dark grey / white) were the design
  assumption. **[measured-all]** **The one run on hardware does not support it**: a
  five-step swing with two steps of noise leaves 1.2x between adjacent levels.
  Two levels would give 2.5x, which is still thin. More levels trade directly
  against distance and ambient tolerance, and there is none to trade.
- **The middle level is in the right place**, whatever else is wrong: it
  measured at 0.400 of the swing, inside the 0.25–0.75 window calibration
  demands, and the ideal fraction of white came out at 0.701 against the 0.68
  in use. **[measured-all]** That is the default fields scheme; the alternative
  backlight scheme was not measured.

---

## Timing and frame rate

`badge.ticks` is "the number of ticks (milliseconds) since the badge was powered on
**when `update()` was called**". `badge.ticks_delta` is the milliseconds since the
previous `update()`.

Two consequences that have already caused a bug in this repo:

- **`badge.ticks` does not advance between `update()` calls.** It is "the number of
  milliseconds since Badgeware was powered on, as of when the current frame started",
  so every read inside one frame returns the same value. Code that checks a deadline
  *after* presenting the frame is reading a newer clock than the code that drew it —
  see the "decide once per frame" rule in [../../AGENTS.md](../../AGENTS.md).
- **`time.ticks_ms()` and `time.ticks_us()` read live.** They are the way to get
  sub-frame resolution, which `badge.ticks` cannot give. Anything timing a pulse or
  an optical symbol needs these. Subtract with `time.ticks_diff()`, because raw tick
  values wrap.
- **`ticks_delta` is the honest measure of achievable frame rate**, and it varies. Use
  it to adapt, not a hardcoded FPS.

The official docs give no FPS figure. The firmware decides it: **60Hz on v2,
90Hz on v3**, and a frame is a whole number of refreshes. See
[what the hardware runs at](#what-the-hardware-runs-at-60hz-on-v2-90hz-on-v3)
above.

That is a ceiling, not a promise — your own drawing still has to fit inside the
frame, and on v2 a HIRES screen that clears and redraws everything does not. Read
`badge.ticks_delta` and adapt rather than assuming you hit it.

---

## Performance tiers

**[measured-v2]** Allocation, from the probe's `alloc` and `gc` steps in seven
runs (2026-10-03, -10-04, -10-05, -10-05b, -05c, -05d, -05e):

| | Cost | Runs |
|---|---|---|
| a 2-item tuple (one 16-byte GC block) | **2.9 µs** | 2.83-2.94, every run |
| a 4-item tuple (two blocks) | **65-145 µs**, 23-50x | 136, 128, 145, 69, 66, 67, 102 |
| an 8-item tuple (three blocks) | **240-410 µs** | 283, 364, 412, 256, 241, 243, 356 |
| `gc.collect()`, 180-214 KB in use | **16-64 ms** | 18.1, 25.3, 63.9, 16.2, 21.6, 22.5, 22.2 |
| `gc.mem_alloc()` | **~110 ms** **[inferred-v2]** from frames it inflated | |

**Only the one-block allocation is steady.** The heap in use barely moved
between runs, so what varies is likely the heap's layout: a multi-block
allocation scans the allocation table from wherever the last search stopped,
and a collection sweeps it **[inferred-v2]**. Why 2026-10-05 collected four
times slower than 2026-10-05b is not known; what had run since boot is the
candidate, and neither run recorded it. Budget for the top of each range.

v2 MicroPython advances its free-block hint only after a one-block allocation,
so every bigger one rescans the allocation table of an 8MB heap in PSRAM. A
`rect`, a 3- or 4-tuple: each is one of those. v3's 32-byte
blocks move the threshold, not the cost. Keep multi-block allocations out of
per-frame code, and never call `gc.mem_alloc()` or `gc.mem_free()` in a
timed path -- both walk the whole table.

From the performance guide:

- Plain Python pixel loops are hopeless. A 19,200-pixel frame across four channels is
  "over half a million object operations and dispatches every frame".
- `@micropython.native` — "typically around 2x faster for no code changes at all".
- `@micropython.viper` — "tens of times faster for the right code, close to
  hand-written C". Arguments typed as raw pointers, no bounds checks.
- **Never allocate inside a hot loop.** "Call back into Python or allocate an object
  inside your Viper function and you slide back toward native speeds."
- Viper cannot call `math.sin()` — bake a lookup table.
- Work over small areas and scale up: a 32x24 buffer rebuilt per frame is cheap, and
  `blit()` stretches it to full screen "for free".

---

## Power and sleep

```python
badge.battery_level()     # percentage, 0-100
badge.battery_voltage()   # float
badge.usb_connected()     # bool
badge.is_charging()       # bool
badge.sleep()             # low-power, optional duration
badge.woken_by_button()
badge.woken_by_reset()
badge.pressed_to_wake()
badge.wake_reason()
badge.caselights(level)   # rear LEDs, 0-1
```

No accelerometer or other analog sensors are documented.

`caselights()` is the only output a user can see while the screen faces away from
them — worth remembering for any app where two badges point at each other.

---

## Memory and storage

**8MB heap, no swap.** "Large chunks of data or a slow leak will eventually trip a
`MemoryError`." Object creation in a tight loop triggers GC pauses — preallocate,
and call `gc.collect()` at a chosen moment.

Floats are single precision.

**No threading or multiprocessing.** There is no mechanism to move a blocking call
off the frame loop.

**16MB flash** in three volumes: `/` (1MB, app-writable), `/system` (12MB, host-only
via USB disk mode), `/rom` (1MB, read-only fonts). `State` writes to
`/state/<name>.json` on `/`.

`/` holds only ~256 blocks of 4KB, so a few hundred files at most, and a 10-byte file
still costs 4KB. Flash wears out: buffer in RAM and write occasionally, never per
frame.

**[inferred-all]** App layout is not documented anywhere I could find. From this repo:
an app is a folder containing `__init__.py` (the entry point, run on launch) and
`icon.png` (the launcher icon). Note that the folder must contain `__init__.py`
with the dot — a file named `__init__py` is not a module and the app will not load.
