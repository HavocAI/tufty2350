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
| MCU | Raspberry Pi RP2350B, dual Arm Cortex-M33: **200MHz on v2** (`machine.freq()` on the badge, 2026-10-09f **[measured-v2]**), 250MHz on v3 (since 2026-06-20) **[source-v3]** |
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

### Response time

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

Measured on the v2 badge's mirror runs, a full transition settled to 90% in
16-24 ms **[measured-all]**. The optical protocol does not signal with the
panel's pixels: it holds a white screen and steps the backlight (see
section 5 of the protocol spec).

### What the hardware runs at: 60Hz on v2, 90Hz on v3

**The frame rate depends on the firmware, and the badge on the desk is the
slow one.** **[measured-v2]** by Dev Mode's *probe frame timing* on that badge,
firmware `BW-1.27.0, on 2026-04-10` (the v2.0.2 release), on 2026-10-03;
the figures are in section 2 of [data.md](../optical_data_transfer/data.md).

| | v2.x (measured) | v3.x (from source, unmeasured) |
|---|---|---|
| `display.set_framerate` | **absent** | present; `badge.mode()` calls `set_framerate(90)` on every mode change |
| Panel refresh | **16.71 ms, 59.8Hz** (`FRCTRL2` left at its init value `0x0f`) | 11.1ms, 90Hz |
| Writing one frame, vsync off | **7.78 ms** | ~7ms **[inferred-v3]** |
| GC block size | 16 bytes | 32 bytes (2026-07-31) |
| `MICROPY_GC_NO_SCAN`, `VM_IN_RAM` | no | yes (2026-06-20); the interpreter is ~20% faster |
| System clock | **200 MHz** (`machine.freq()`, 2026-10-09f **[measured-v2]**; `SYS_CLK_HZ` at `db958ce` **[source-v2]**) | 250 MHz (`board/pimoroni_tufty2350.h`, 2026-06-20) **[source-v3]** |
| `badge.mode()` HIRES ↔ LORES | **0.43 ms** the call (0.51 max); the first frame after it is no longer than a steady one: LORES 16.2 ms against 16.7, HIRES (with `run()`'s clear) 33.1 against 33.4. Switch to the frame on the panel: 16.6 LORES, 33.5 HIRES. **A switch costs no refresh of its own** (probe `mode_switch`, 10 each way, 2026-10-09f) | also calls `set_framerate(90)` each time; unmeasured |

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

**[measured-v2]** What a frame spent drawing a full HIRES screen (2026-10-03):

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
the backlight call), so fill cost is per pixel: a `LORES` frame with
`run()`'s clear off and one fill ran **16.84 ms**, one refresh, where a
redrawn HIRES screen ran 50.26, three.

**Presenting blocks for about 12.4 ms** at `LORES` and 60Hz: a frame drawn in
~4 ms waits ~4.7 ms for the next vertical blank, then 7.8 ms to write.
Nothing in Python runs meanwhile, so a light reading cannot be taken during
it -- one reason the optical protocol reads through a DMA ring, which reads
on regardless.

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
  `share_wifi`'s transfer read this way from 2026-10-05, and now reads by a
  DMA ring (below) -- see section 6 of the protocol spec, and the third loop shape in
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
  Dev Mode's since-removed `fast_reads`, 4,096 readings each, the sensor reading ~209
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

### Reading by DMA ring

`share_wifi` reads the sensor for a whole transfer by DMA into a ring,
`fastadc.Ring`: the ADC free-running at 50 kHz (`DIV` 959) into its FIFO, one
channel paced by DREQ 48 writing 16-bit readings into a 32 KB write ring
(`ring_size=15`, `ring_sel=True`), 16,384 readings, 327.7 ms, with a count of
0x0FFFFFFF -- `TRANS_COUNT` is 28 bits on RP2350 **[datasheet]**. What it
rests on, and how sure each is:

- **The heap is PSRAM behind the XIP cache**, at 0x11000000 (chip select 1)
  **[source-v3: `mpconfigboard.cmake`]**, and on v2's build too: a fresh
  bytearray landed at 0x110d0000 **[verified-v2, 2026-10-07a M1]**.
- **The same PSRAM uncached is at 0x15000000**: `XIP_NOCACHE_NOALLOC_BASE`
  0x14000000 plus CS1's 16 MB **[Pico SDK `addressmap.h`; the firmware's
  `st7789.hpp`, `XIP_PSRAM_NOCACHE`]**. The ring's DMA writes and every CPU
  read go through it, so the cache can neither serve a stale line nor write
  a dirty one back over fresh DMA data. The ring sat at 0x150d0000, and
  reading PSRAM there while DMA writes it works: `verify()` passed
  **[verified-v2, 2026-10-07a]**.
- **Cache maintenance** is a byte write to `0x18000000 + (address -
  0x10000000) + op`, one per 8-byte line; op 3, clean by address, also
  invalidates on RP2350 **[Pico SDK `xip_cache.c`]**. Done once over the
  buffer's cached alias before first use; `verify()`'s marker check
  passed **[verified-v2]**.
- **`FCS.OVER` is bit 11 and `FCS.UNDER` bit 10**, sticky and write-1-to-clear
  **[Pico SDK `adc.h`, RP2350]**. Cleared by writing a known `FCS` value with
  them set, never by read-modify-write.
- **`high_pri`** in `pack_ctrl` puts the ring ahead of the panel's DMA in
  arbitration during a present **[datasheet]**; the ring lost nothing
  through 2 s of presents every pass: 49,998 readings a second, no FIFO
  overrun, no fault **[measured-v2, 2026-10-07a M3]**, and again on
  2026-10-08 and -09. A collection (28.8-29.8 ms; 68-81 on 10-08 and -09,
  with the dev link loaded; 31 on 10-09b; 53-54 on 10-09f) lost nothing either; the anchor's bracket was 12-14 µs, and
  a viper kernel read the uncached alias at 0.31 µs a reading.
- **Nothing else may use the ADC while it runs**: `light_level()`, the
  battery and the chip's temperature are all the same ADC.
- `Ring.verify()` checks all of it on each launch, and a failure leaves the
  badge reading `light_level()`. `Ring()` first dirties every line of the
  buffer's cached alias with a marker no reading can be (0xA5C3) and then
  cleans it; `verify()` checks the uncached alias reads the marker -- the
  clean wrote it back -- then that the DMA writes where and as fast as its
  count says, before and after a wrap, and then churns the cache and
  checks at once that the marker has not landed over the readings: a line
  the clean left dirty, evicted. Not zeros, the allocator's fill: every
  line held the marker before the clean, and a dark room's black, about
  raw 2 with two raw steps of noise, can read 0. Last, the viper kernels
  against their pure twins. `tests/share_wifi/test_ring_verify.py` runs it
  over a model of the cache, with a clean that works, one that does
  nothing and one that leaves lines dirty. M1 records why a ring was
  refused: "cache clean did not write back" or "a cached line written back
  over the ring" name the cache; the rest, the DMA or a kernel.

## The light sensor

`badge.light_level()` returns "the level detected by the light sensor as a raw u16
value". Tufty only.

**It is 12-bit, not 16.** **[measured-all]** Every reading a badge produced in a
mirror run was a multiple of **16**, without exception, across two passes and
every level (2026-09-23; section 3 of [data.md](../optical_data_transfer/data.md)).
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
  section 9.3 of the protocol spec.

It is a **phototransistor**, not an integrating lux chip, front-mounted on the
same face as the screen. **Its response is milliseconds, not microseconds, and
depends on how much light it has** -- see "How fast the sensor follows" below.
It is read through a DMA ring for every optical signal (see "Reading by
DMA ring").

### How fast the sensor follows

**[measured-all]** by Dev Mode's `backlight_step` (mirror; three runs, whose
90% times agree to a few tenths of a millisecond): a white screen throughout,
only the backlight switched -- `display.backlight()` is live within one PWM period, 328 µs
on v2 **[source-v2] [measured-v2]**. Ten of each step, averaged, in ms from the call (10%, 50%,
90%), reads every 26-27 µs, from 2026-10-05e, the first run with every
capture kept:

| | Off to full | Full to off | Off to middle (0.74) |
|---|---|---|---|
| dark room | 1.92, 2.76, 3.87 | 0.19, 0.63, 3.24 | 4.06, 5.18, 7.75 |
| lit room | 0.32, 0.57, 1.20 | 0.32, 0.52, 1.25 | 0.29, 0.61, 0.97 |

The two runs before it (10-05c, -05d) were blind for the first few hundred
µs after each change -- a fault in the test, since fixed -- so their early
lit times (0.1 ms to 10%) were interpolated across the gap, and the captures
that crossed half way inside it were dropped. Up to 0.33 ms of each 10% time
here is the backlight: a new PWM duty takes effect at the next wrap, one
328 µs period at most **[inferred-v2]**.

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
  below roughly 11% duty **[source-v3]** -- 7-9% measured on this badge by
  `channel` (2026-10-09f) **[measured-all]**; about 0.45 on v2 -- but just above
  it, at 0.5 (14% duty), the screen keeps the sensor faintly lit. On
  2026-10-06 that cut the dark edge delay from 1.2-1.9 ms to 0.3 and read under
  one ADC step above off **[measured-all]**; `share_wifi` uses it as low.

**The model, fitted to arbitrary sequences** **[measured-all]**, by the
`sensor_id` dev-link subtool on 2026-10-09f: de Bruijn sequences -- every
run of 8, 4 or 3 symbols once -- over 2, 4 and 8 backlight levels, at 8,
4, 2, 1 and 0.5 ms a symbol, from lows of 0.5, 0.6 and 0.7, each landing
placed by the PWM counter, the ring binned to 100 µs; fitted by
`sensor_fit.py` (removed 2026-10-19 with the subtool). Mirror B at 1 cm, in a box: closed
(dark), and open to the room (lit, but dim: black 48 counts, the dim low
59, under `LIT_BLACK_COUNTS`); 45 captures each, none lost. The figures
are in section 3 of [data.md](../optical_data_transfer/data.md); the captures were deleted on
2026-10-19. One raw unit is an ADC step, 16 counts.

- **The simulator's model is the right one.** Generalised to
  `dy/dt = (I − y)·max(r_min, (y − y0)/K)`, it leaves 0.24-0.49 raw of
  residual against a noise floor of 0.19-0.28 per 100 µs bin. Per-level
  linear dynamics (a superset of Hammerstein) beats it in 9 captures of
  90, by 0.006 raw at most; Wiener in 9, by 0.001-0.042 (`fit.json`; the
  printed `fit.txt` rounds most of these away). Dead time 40 µs.
- **K, in the simulator's counts × ms: 28-40 dark** (2 levels 28-31, 4
  and 8 levels 36-40), so `SENSOR_K` 36 stays. Lit 30-54, but there only
  the rate is pinned (K and y0 trade). The floor never binds, so
  `SENSOR_FLOOR` 0.18 is neither confirmed nor refuted.
- **The swing** from the dim low to 1.0: 88, 80 and 67 counts from lows
  0.5, 0.6 and 0.7 dark; 86, 77, 65 lit.

What a receiver could decode, per symbol time, with the slicer's levels and
every symbol's timing known to the host (`fit.txt`'s SER columns, worst of
the three lows, dark and lit alike) -- so a bound on a receiver that must
find both **[inferred-all]**:

| Levels | A slicer, error-free down to | A linear equaliser (taps x[i-2..i+1]) |
|---|---|---|
| 2 | **1 ms**; 0.5 ms 12-20% wrong | **0.5 ms at 0.8-3.2%** |
| 4 | **2 ms**; 1 ms 10-35% | **1 ms at 0-9.5%** |
| 8 | **8 ms**; 4 ms at most 1.6% | worse than the slicer at 4-8 ms (to 11%); 2 ms 5-24% |

A genie DFE (the previous three levels told to it) is not consistently
better than the linear equaliser. The eye at 2 levels is open to 1 ms
(0.13-0.53 of the levels' spacing), at 4 to 2 ms (0.18-0.45), at 8 at 8 ms
(0.14-0.37) and barely at 4 (-0.06-0.16). So 2 levels at 1 ms or 4 at
2 ms is ~1 kbit/s raw, against the 125 bit/s of today's 8 ms rung; what of it survives framing, timing
recovery and a second badge is the file-transfer task's stage 2.

**With the duty paced by DMA on the PWM wrap, 2 levels at 2 periods
(0.66 ms) decode cleanly** **[measured-all, on the v2 badge, 2026-10-10]**:
`pam_link` on mirror B at ~1 cm, 0.5 and 1.0, runs of the low bounded at
4, made 0 errors in 32496 symbols per room, dark and dimly lit, with a
linear equaliser or a DFE trained on a 64-symbol preamble -- an error
rate ≤ 1e-4 at 95%. Unbounded runs gave 2 and 1 in the dark, each a
high read as low after six and after eight or more lows: the dark low's
slow tail, as simulation predicted. A slicer at that
rate is 0.2-48% wrong, depending on where the grid falls. See section 10.1 of [data.md](../optical_data_transfer/data.md).

**The channel's frequency response** **[measured-all]**, by the `channel`
subtool the same evening (both removed 2026-10-19; the duty written by DMA on the
PWM wrap, no Python in the timing), at the stock PWM, 3.05 kHz:

| | dark | lit (dim) |
|---|---|---|
| -3 dB | **276 Hz** | **406 Hz** |
| at 1 kHz | -12.4 dB | -10.4 dB |
| delay, 20 Hz → 1 kHz | 750 → 250 µs | 530 → 230 µs |
| static: threshold, worst off a line, up/down | 9.0% duty, 5.2%, 0.31 raw | 7.2%, 3.8%, 0.13 raw |
| swing, 0-100% duty | 6.05 raw (97 counts) | 5.93 (95) |

Delay is uncertain by one PWM period; gain is not. The
bandwidth follows the light on the sensor, as the model says; the
simulator's model put it at ~180 Hz dark at mid duty **[inferred-all]**.
At 12.2 and 24.4 kHz PWM (TOP 16383, 8191) the bandwidth is no better --
250 and 233 Hz dark, 331 and 276 lit -- and the static curve is worse:
flat to 16-20% duty, saturated above ~70%, 17-24% off a line. A smoothed
PWM at the driver's enable would do that (the 2040's 1 kΩ and 22 nF, 22
µs) **[inferred-all]**. **The stock PWM is the one to use.** At 24.4 kHz
most tones' phase is ambiguous (the counter wrapped during the trigger).

### The circuit around the sensor and the backlight

No Tufty 2350 schematic was found published (2026-10-09): a user asks for
one in the forum's documentation thread
(forums.pimoroni.com/t/tufty-2350-documentation/28645, page 4), and the
shop links the 2040's. The Tufty 2040's (PIM624, 2022, sheet 3) has the
sensor as a PT19-21C phototransistor, an emitter follower into **1 kΩ** to ground, and the backlight as an AP2502
four-channel current sink whose EN takes the PWM through 1 kΩ and 22 nF --
a 22 µs RC **[inferred-all: the 2040's circuit; the 2350 differs at least
in having no `SENSOR_POWER` pin]**. With a 1 kΩ load the RP2350's ~50 kΩ
pad pull-down would change the load ~2%, so the software pull-down
experiment was not run; revisit only if a 2350 schematic shows a load of
tens of kΩ. For the record, RP2350-E9: with a pad's input buffer enabled
and the pin between logic levels, leakage up to ~120 µA can hold it near
2 V, more than the internal pull-down can overcome -- keep the input
buffer off (as for any ADC pin) or pull down externally with 8.2 kΩ or
less **[datasheet]**.

### The backlight's PWM

**[source-v3] [source-v2]** `LCD_BACKLIGHT` is GPIO26: PWM slice 5,
channel A; channel B's pin, GPIO27, is the LCD's chip select under SIO.
GPIO10 and 11 (BUTTON_C, BUTTON_UP) map to slice 5 too, harmlessly: they
stay SIO inputs. The
display driver sets the slice up once in `ST7789::init()` -- wrap 65535,
divider 1.0 (`modules/c/st7789/st7789.cpp:73-75`) -- and afterwards writes
only CC, in `set_backlight()` (`:271-283`); v2 is the same (`db958ce`).
The badge's v2 runs the system clock at **200 MHz** -- `machine.freq()`
read by the `sensor_id` and `channel` subtools, 2026-10-09f
**[measured-v2]**, and `SYS_CLK_HZ` at `db958ce` **[source-v2]** -- so the
backlight's PWM is **3,052 Hz, a 328 µs period**. v3's source raised the
clock to 250 MHz on 2026-06-20 (`board/pimoroni_tufty2350.h`): 3,815 Hz,
262 µs **[source-v3]**. An earlier version of these notes gave v3's figure
for the badge. CC and TOP latch at the wrap
**[datasheet]**, which is why a `display.backlight()` lands up to a period
late. The slice's registers are at 0x400A8064 -- CSR, DIV, CTR, CC, TOP,
4 bytes apart (`PWM_BASE` 0x400A8000 + 5 x 0x14) [Pico SDK rp2350
`regs/pwm.h`]. Nothing else touches slice 5, so a TOP or CC written from Python
stays until put back -- but `display.backlight()` keeps scaling to 65535,
so it saturates while TOP is lower. The slice's wrap is a DMA request
(`DREQ_PWM_WRAP5`, 37), so a DMA channel can pace duties exactly a period
apart; `devtests/channel.py` does that and restores the slice in a
`finally`, which it did after both of 2026-10-09f's runs (`saved_top`
65535, `health` 0) **[verified-v2]**. Its results are under "How fast the
sensor follows": the stock 3.05 kHz is the best of the three rates tried.

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
3.3 V reference) **[datasheet]**: 26-28 C on 2026-10-06. It barely moved through a run.
- The slowest backlight step here, to the middle in the dark, is 7.8 ms; a
  peer's screen over the sensor, predicted at ~2.5x the mirror's light,
  should be faster still **[inferred-all]**.

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
explicit lever: `share_wifi` signals with it, setting it per symbol -- 0.5,
0.82 and 1.0 for its three levels, off and full for its beacons -- behind a
white screen, and puts it back afterwards. See section 4 of
[the protocol spec](../optical_data_transfer/spec.md).

**Partial backlight is PWM, not a steady current.** On v3,
`ST7789::set_backlight()` maps 0.0-1.0 through gamma 2.8 onto a duty between
12.5% and 100% (0 is off) **[source-v3]**. The badge's v2 has no floor: duty is
plain `v^2.8`, which Pimoroni's own commit adding the floor describes as the
backlight "turning off ~0.45" **[source-v2]**, and the mirror runs read
backlight 0.0-0.4 the same as off **[measured-v2]**. On both, the slice runs
at `sys_clk / 65536`: 3.05 kHz on v2 **[measured-v2]**, 3.8 on v3
**[source-v3]**. A phototransistor that responds in
microseconds could see each on- and off-phase rather than the average; this
one does not -- it averages at every duty from 0.25 to 1.0 **[measured-v2]**.

**[unverified]** What has *not* been ruled out is the panel or its driver doing
something in hardware. Nothing in the schematic suggests it, but only a bench
test proves it.

### Practical notes for optical work

- The peer's sensor reads whatever is in front of **it**, which is a patch near
  the right edge of its own badge — see above, and note this bullet used to say
  "the average brightness of the whole screen". Text or any non-uniform content
  inside that patch changes the light it sees, so the screen behind a signal is
  one solid white.
- The sensor is a single unfiltered channel. Colour gives no extra bandwidth —
  it only sees total brightness, so RGB multiplexing does not work.
- Three backlight levels (dim, middle, full) carry the signal. Through a
  mirror the whole swing is five ADC steps with a standard deviation of 2-10
  counts a reading **[measured-all]**; calibration checks each gap against the
  noise it measures (section 9.3 of the protocol spec).

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
| `gc.collect()`, 466-475 KB in use (share_wifi with the dev link loaded, tethered, 2026-10-08 and -09) | **67-81 ms** | 78.3, 69.1, 66.7; 68.0-80.7 mid-listen. 397 KB on 10-07a: 27.7-29.8 |
| `gc.collect()`, 450 KB in use (from the menu, 2026-10-09b to -d, tethered or not) | **29-32 ms** | 29.7, 29.2, 29.2; 30.5-31.6 mid-listen. The 67-81 was an open dev link session's |
| `gc.collect()`, 449 KB in use (from the menu, 2026-10-09f, tethered, a fresh launch with the link never opened -- the user, 2026-10-10) | **52-54 ms**: an extent of ~4 MB (2026-10-13, [inferred-v2]) | 51.7; 52.9-54.1 mid-listen. A bulk call 6.2 µs, against 5.1 in every other run |
| `gc.collect()`, 2026-10-13, each a fresh launch from the menu, tethered | **26.6 ms** at 272 KB (share_wifi); **59.5-67.4 ms** at 77-234 KB (`heap_probe`) | share_wifi 26.3, 26.6; 27.5-28.5 mid-listen. `heap_probe`: its sweep reaching the heap's top |
| `gc.collect()`, 2026-10-13b, launched over USB from a clean heap with 0-6 MB of garbage first | share_wifi **17.0-57.3 ms** at 261 KB; `heap_probe` 4.5-54.6 at 68-226 KB | 0.31 + 0.049/KB live + 0.00708/KB extent, within 0.28 ms; repeats within 0.1 |
| `gc.mem_alloc()` | **~110 ms** **[inferred-v2]** from frames it inflated | |

**The interpreter's speed did not change; a collection follows the live
heap** **[measured-v2]**. A bulk-timed call (`alloc.call_us`) was 5.1 µs
in every run from 2026-10-05 to -09d, in share_wifi and in the
`heap_probe` app alike. The one exception, 2026-10-09f -- 6.2 µs, with a
collection of 52 ms at 449 KB, and 4- and 8-tuples of 155 and 647 µs
against 09d's 105 and 471 -- was a fresh launch with the dev link never
opened (the user, 2026-10-10), so a collection can be slow outside a
session. **2026-10-13 explained it** (section 2 of [data.md](../optical_data_transfer/data.md)): a collection costs 0.31 ms + 0.049 ms per KB live +
0.00708 ms per KB of extent, within 0.28 ms over 42 readings
**[measured-v2]**, the sweep running to the highest live block
(`py/gc.c:626` in bw-1.27.0 **[source-v2]**). The extent is wherever the
app loaded: garbage allocated before `launch()` set it to the KB
(the `heap_probe` tooling, deleted 2026-10-18) **[verified-v2]**, since v2's
`launch()` does not collect first; ~4 MB on 10-09f **[inferred-v2]**.
`top_kb` is only a floor for it; the heap readings' `extent_kb` measures
it. share_wifi's probe `loop` rose 41 → 81 µs a pass
over those runs, and not because of the heap: `heap_probe` read 81 µs
with 72 KB in use. Most likely it times its own bookkeeping,
`TimeStats.add()`, rewritten twice **[inferred-v2: the timeline only]**.
A collection sweeps the block table up to the live high-water mark
(`gc_last_used_block`), not the whole heap -- `py/gc.c:626`, raised on
allocation (`:863`, `:1127`), reset at the sweep's end to the highest
live block (`:657`), read in the pimoroni/micropython fork at
`f173bb7b6`, older than both pins; that the v2 pin keeps it is
**[inferred-v2]** -- so it scales with what is live, and with where it
sits. From the menu:

| live heap | `gc.collect()` |
|---|---|
| 72-92 KB (`heap_probe`) | 10.3-11.9 ms |
| 190-216 KB (share_wifi, 10-05) | 16-22 ms |
| 397-450 KB (share_wifi, 10-07a to -09d) | 28-31 ms |

but 67-81 ms inside an open dev link session, and 52 at 449 KB on
2026-10-09f's fresh launch: the bytes in use are not the whole story
**[measured-v2]**. Keep the live heap
small where collections are timed, and do not import what a mode does not
use. Tethered runs read loops ~5 µs a pass slower than battery ones
[inferred-v2: the USB stack].

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

## USB serial (CDC)

Read from MicroPython `bw-1.27.0` (f173bb7, v2) and `bw-1.28.0-3`
(b29530a, v3), and from tinyusb at their submodule commits (aa0fc2e,
dae3f9a), for share_wifi's USB dev link ("USB dev link" in
[.agents/share_wifi/app-spec.md](../share_wifi/app-spec.md)). These are the same on both
firmwares **[source-v2] [source-v3]** unless a marker says otherwise.

**Writing.**

- `sys.stdout.buffer.write()` is raw: no `\n` to `\r\n`
  (`shared/runtime/sys_stdio_mphal.c`). It flushes after every write
  (`mp_usbd_cdc_tx_strn`, `shared/tinyusb/mp_usbd_cdc.c`).
- **No host, or the port closed (DTR low):** a write never blocks and never
  fails. `tud_cdc_n_connected()` is `tud_ready() && DTR`, and tinyusb makes
  the 256-byte TX FIFO overwritable while DTR is low, so the data is dropped
  except its last 256 bytes. **v2** keeps that tail and sends it to the next
  host that raises DTR **[source-v2]**. **v3** clears the FIFO when DTR drops,
  and clears a stalled IN endpoint when DTR rises **[source-v3]**.
- **Physical unplug and replug** is different: a bus reset, and `cdcd_reset`
  clears both CDC FIFOs. MicroPython's own 512-byte stdin ring survives it.
  An app sees an unplug at once through `badge.usb_connected()` (VBUS)
  **[verified-v2]**, 2026-10-09: the dev link's `files` tool ended a send
  cleanly when the cable was pulled.
- **A host that holds DTR and does not read:** the write waits, and gives up
  after 500 ms without progress (`MICROPY_HW_USB_CDC_TX_TIMEOUT`,
  `ports/rp2/mphalport.h`). `write()` then returns less than the length
  (`mp_stream_rw`, `py/stream.c`). This is the only failure a badge can see.
- **`select` cannot tell a host is gone.** `POLLOUT` is reported when not
  connected *or* when there is room. There is no DTR getter, so
  `badge.usb_connected()` (VBUS) is the cheap unplug signal. VBUS is also
  present on a charger.

**Reading.**

- `sys.stdin.buffer.read(n)` **blocks until all n bytes arrive**: there is no
  "what is there" read. Poll first and read one byte at a time; `readinto` a
  preallocated `bytearray(1)` does not allocate.
- `tud_cdc_rx_cb` turns the interrupt character into `KeyboardInterrupt` and
  clears the stdin ring. It is Ctrl-C while `main.py` runs (`pyexec.c`), and
  `micropython.kbd_intr(-1)` makes 0x03 ordinary data, and with it mpremote
  could not interrupt an app **[verified-v2]** (2026-10-08). `kbd_intr(3)`
  puts it back.
- Host-to-badge data beyond the ring and tinyusb's 256-byte RX FIFO is held
  back by USB NAKs. It is delayed, never lost.

**Interrupts and timing.**

- USB work runs as a scheduled task between bytecodes (`tud_event_hook_cb`
  -> `mp_usbd_schedule_task`), so a write leaves a few ms of transfer-complete
  events behind it.
- An idle, enumerated link raises no SOF interrupts. They are on only at
  bus-ready, for 16 frames after DTR rises, and for the RP2040-E15
  workaround, which is RP2040-only and not built for the RP2350
  **[inferred-all]**.
- v2's `display.update()` spins in C, so USB waits for it to finish. v3's
  wait services USB (`modules/c/st7789/st7789.cpp`) **[source-v3]**.

**Interrupting Python from an IRQ.**

- An exception raised in a soft Pin IRQ handler is printed and swallowed
  (`mp_call_function_1_protected`).
- What does reach the running code is the firmware's own
  `mp_sched_keyboard_interrupt()`. Python reaches it through a dupterm
  stream that returns the interrupt character to `os.dupterm_notify()`
  (`extmod/os_dupterm.c`; rp2 has `MICROPY_PY_OS_DUPTERM` and `_NOTIFY`).
  The stream's `readinto(buf)` must write `buf[0]` and **return an int**.
  Returning bytes is a `TypeError` there, which deactivates the stream with a
  printed message and no interrupt (`py/modio.c`).
- Native and viper code never checks for pending exceptions, so an interrupt
  waits for it to return (`py/vm.c`; none in `py/emitnative.c`).
- It works from a soft handler on the badge **[verified-v2]** (2026-10-08).
  In the dev link's checklist step 4, B (a falling-edge Pin IRQ on
  `BUTTON_B`) and the host's raw 0x03 each stopped a subtool that never
  polls. v3 is unrun **[unverified-v3]**.

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
