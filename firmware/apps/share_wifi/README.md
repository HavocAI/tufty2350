# Share WiFi

Hand a Wi-Fi network from one Tufty to another with nothing but light: one
badge's screen flashes, the other's light sensor reads it. No radio, no
pairing, no cable.

Share WiFi is the proof of concept for the
[optical data transfer protocol](../../../.agents/optical_data_transfer/spec.md).
A credential is the smallest useful payload, so it is where the protocol
proves itself. The protocol is the goal: it carries any list of byte
strings, and files are the next step.

**Status:** transfers complete end to end in simulation, over every room the
simulator models. On hardware, one badge facing a mirror has sent and
decoded every rung of the fast data path. Two badges have not yet completed
a transfer.

## Using it

Install by copying this whole `share_wifi` folder into the badge's `apps`
folder ([Install and launch](../../../APPS.md#4-install-and-launch)). Both
badges need the same build: there is no version negotiation.

- **Share**: pick a saved network, line the badges up, press A.
- **Receive**: line the badges up, press A. A network that arrives is saved
  and joined.
- **Connect**: join any saved network.

*Line the badges up* draws a line down the middle of the screen. Hold the
other badge's edge on it, screens facing. The light sensor sits about a
millimetre outside the right edge of the screen, so a flush hold puts each
sensor over the other badge's bezel; this hold puts it over the other's
screen. During a transfer both screens are plain white and the case lights
show progress. B cancels.

Saved networks live in `/state/share_wifi.json`. On first launch the app
adds the badge's own network from `secrets`, so it always has one to share.
Open networks share with an empty key. WEP networks can be shared and
saved, but this firmware cannot join them.

Holding **C** for 5 s on the main screen reveals **Dev Mode**: the saved
hardware measurements, shown or cleared, and a USB dev link through which a
computer runs the measurement tools on the badge -- they live in
`tests/share_wifi/devtests/`, not here.

## How it transfers data

The signal is **the backlight alone, behind a white screen drawn once**.
Nothing is drawn or presented during a transfer, so no panel refresh gets
in the way: each symbol is a `display.backlight()` value, live at the next
PWM period. Three levels carry data: dim (0.5), middle (0.82) and full.
Dim, not off, keeps the receiving sensor faintly lit, which shortens its
response in a dark room.

**Reading through a DMA ring.** The receiver never polls the sensor. The
ADC free-runs at 50 kHz, and a DMA channel writes every reading into a
16,384-entry ring in PSRAM: 327 ms of history, each reading timed by its
index. Viper kernels sum and search it. The protocol runs once per slot and
decides each slot after the fact, from the median of its window's parts, so
a collection or a slow pass delays the decision but loses nothing.

**Discovery and calibration.**
- The sender (the Leader) flashes an *Announce* beacon, full and off at
  14 ms halves. It is recognised by its period, not its brightness: nothing
  is calibrated yet.
- The receiver (the Follower) answers with a *staircase*, each level held
  20 ms. From it the Leader takes its thresholds. Its repetitions say
  whether the room is lit, which picks the starting speed.
- Each badge fits the room's mains flicker (50 or 60 Hz LED ripple, up to
  47% of the light measured) in a dark window. From then on it corrects
  every reading from a table, tracking the mains' drift with a phase lock.
- The Follower learns the Leader's levels from each block's opening
  pattern, the fast Begin.

**Slots, rungs and phase locking.** Symbols live on a slot clock, 8 ms in a
lit room and 16 ms in the dark, on a ladder down to 32 ms. The slots
carry only the control patterns; the payload goes in fast blocks. A receiver
never trusts the calibrated epoch: at the start of every message it
re-locks onto the sender's real first edge out of black, so the two
badges' grids never drift apart. Rate changes move the slot's length,
never its epoch.

**Fast blocks.** 2-level PAM at 2-6 PWM periods a symbol (rungs F0-F3,
0.66-1.97 ms). The symbols are paced by DMA on the
  backlight's PWM wrap, so every boundary falls on the sender's crystal.
  The receiver bins the ring at 100 µs and decodes with a linear equaliser
trained on a known preamble. Bit stuffing keeps runs of dark short, and
each block carries a CRC-32. A block goes out on a slot-rate lead-in and
is answered with a slot-rate ACK or NAK. Up to 128 bytes a block, under
1 s on air.

**When things go wrong.**
- Fast blocks step down F0 to F3 after repeated NAKs, and stay on F3
  until the block's attempts are spent.
- A receiver that misses a block asks for it again, one slot rung slower.
- Every wait has a deadline, so a transfer always ends with a result.

**Speed.** A credential takes about 1.4 s from facing to done in a steady
lit room, 1.8 s with flickering light and 2.1 s in the dark, in
simulation. For larger payloads
the fast path is predicted at about 110 bytes/s lit and 47 dark, in
simulation. The receiver trains its decoder on a rung's first block,
300-400 ms on v2, and only retunes it on the blocks after, 54-70 ms.

## How the technique got here

Roughly in order, earliest first. Each step kept what worked and replaced
what the measurements or the simulator showed did not.

1. **Flat fields of pixels.** The first transmitter drew the whole screen
   black, grey or white for each symbol, and the receiver read
   `light_level()` once a frame. Black was reserved for framing, so no
   payload could forge a control pattern. Slots ran from 250 ms down to
   16; the beacons had 250 and 400 ms halves, the staircases 200 ms steps.
   The obvious way to start: the screen is what a badge has to show.
2. **Symbols indexed by slot, not by frame.** The transmitter emitted one
   symbol per frame into a receiver decoding one per slot, so no transfer
   could complete, while every unit test passed.
3. **A named middle grey and full-range white.** The palette's grey sat
   too near black for calibration to accept it, and its white is tinted,
   which dims the light the peer sees.
4. **Phase locking on every message.** The epoch calibration derives is
   only an estimate on the badge that measured the staircase; uncorrected,
   the grids sat half a slot apart until the link was lost. Each receiver
   re-locks on the sender's first edge out of black.
5. **Frame-rate classes in calibration.** A badge's frames bounded how
   often it could read, so the staircase carried whether it was fast or
   slow, and a slow pair met on slower rungs.
6. **A backlight scheme beside the pixels**, the screen white and the
   level set by `display.backlight()`. A pixel field took 13-23 ms to
   reach the sensor after the frame that drew it, and the panel 16-24 ms
   to settle; a backlight change is live within one PWM period, 328 µs.
7. **Reading continuously.** During a transfer the screen stopped
   presenting and the app read the sensor back to back in 2 ms samples.
   Presenting a frame blocks reading for 12.4 ms of every 16.7: one
   reading a slot became about a hundred.
8. **Low dim, not off.** Low went from black pixels with the backlight
   off, to white with it off, to white at 0.5. Keeping the sensor faintly
   lit cut the dark room's edge delay from 1.2-1.9 ms to 0.3.
9. **Filtering mains flicker**, first with a learner -- sines at 100 and
   120 Hz learnt from dark slots -- then with a fit before calibration,
   correction from a table, and a phase lock on the mains' drift. LED
   lights flickered by up to 47% of their light, more than an ADC step;
   the learner delivered 77 of 144 simulated transfers with the mains
   0.02 Hz off.
10. **The DMA ring.** The ADC free-runs at 50 kHz into a ring the CPU
    never has to read on time, and the protocol decides each slot after
    the fact. Calling the protocol once a reading cost about 1 ms on the
    badge, and a polled loop managed 2,166 readings a second at best. A
    polled reader stayed as a fallback, its tier carried in calibration.
11. **Recovery that converges.** A receiver that loses framing breaks in
    (shines full) and asks for the block again, one rung slower, after the
    sender has gone quiet. Answering at once landed inside the block still
    being sent, and the two badges walked down the ladder a rung apart for
    five minutes.
12. **Faster starts and a faster handshake**: 8 ms slots lit and 16 dark,
    from the measured settle; 40 and 64 ms beacons; 20 ms staircase
    steps. Every slot's timing rests on the ring and on M4/M5 measurements
    of the sensor through it.
13. **The backlight alone, and the ring alone.** The pixel scheme, the
    frame-rate classes and the polled reader were removed. Every
    supported firmware has the ring, the polled tier cost a mixed pair
    half its rate, and pixels were slower than the backlight at every
    step.
14. **The short handshake**: one Announce beacon at 14 ms halves, and one
    staircase, the Follower's, read by the Leader while it announces. The
    Leader's staircase only told the Follower its levels, which the first
    block's opening pattern shows anyway. A credential went from 2.6 s to
    2.0 lit, 4.4 to 3.8 dark.
15. **Fast blocks.** The payload moves in bursts of 2-level PAM, a symbol
    2-6 PWM periods long, paced by DMA and decoded by an equaliser trained
    on a preamble, with flicker regressors. One bit a slot gives 125
    bit/s; the sensor's bandwidth (276-406 Hz) allows about ten times
    that. F0 decoded 0 errors in 32,496 symbols per room on a mirror.
    More levels, MLSE decoding, OFDM and a faster PWM were measured or
    simulated and lost to it.
16. **Decoding after the burst, not during it.** Training the decoder
    takes 300-434 ms on the badge, longer than the ring holds, so decoding
    mid-burst lost the readings not yet binned and about half the blocks.
    Waiting for the burst's end decoded every block.
17. **Training once a rung.** Later blocks keep the first block's
    equaliser and refit only the flicker terms: 54-70 ms instead of
    300-400, so a 2 KB payload moves at 110 bytes/s instead of 86.
18. **Fast blocks only.** The slot-symbol blocks, the fallback to them,
    slot rungs slower than 32 ms and breaking in were removed. In
    simulation every transfer that used them ended the same way without
    them, or sooner.

Assessed and not built: one-period symbols (0.33 ms) with a Reed-Solomon
code. The mirror says they would work, but the gain depends on how long a
block two badges' clocks allow, which needs a second badge.

## What has run on hardware

All on the v2 firmware (v2.0.2), one badge facing a mirror:

- The DMA ring: 50 kHz with no loss through presents and collections.
- Slot-rate blocks decoded with 0 errors down to 6 ms slots.
- Fast blocks through the transfer's own sender and receiver: every rung,
  dark and under LED light, 24 of 24.
- The sensor's response, the backlight's curve and the channel's bandwidth:
  measured. The simulator is fitted to them.

Not yet run: a transfer between two badges, so the two crystals' offset and
a real peer's light are unmeasured.

## Where things are

| File | What it is |
|---|---|
| `__init__.py` | the app: menus, storage, connecting, the transfer loop, Dev Mode's three items |
| `protocol.py` | the protocol's state machine and wire format; pure Python, no badge globals |
| `reader.py` | the ring reader: times and means from the DMA ring |
| `fastadc.py` | the ADC and DMA registers behind the ring, and its viper kernels |
| `fastblock.py`, `fasttx.py` | the fast block: encoder and decoder, and its DMA-paced transmitter |
| `devlink.py`, `devserver.py`, `usbport.py` | the USB dev link |

The protocol is specified in
[.agents/optical_data_transfer/spec.md](../../../.agents/optical_data_transfer/spec.md),
with its reasons in the
[context](../../../.agents/optical_data_transfer/context.md),
its checks in the [tests](../../../.agents/optical_data_transfer/tests.md),
and every measurement and sweep its decisions rest on in
[data.md](../../../.agents/optical_data_transfer/data.md).
The app's own behaviour is in
[.agents/share_wifi/app-spec.md](../../../.agents/share_wifi/app-spec.md). The test
suite runs on a laptop with `python3 tests/run_tests.py`, and
`tests/share_wifi/simulator.py` runs two virtual badges through the
production code.
