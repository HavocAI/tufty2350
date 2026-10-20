# Fast blocks in transfers: one badge done, two badges next

**Owner:** human, two badges (the agent runs the dev-link tools).
**Depends on:** [second-badge-measurements.md](second-badge-measurements.md):
no second badge is available. **Status:** built and swept in simulation
(2026-10-11/12); the transfer's own fast-block code run on one badge facing
a mirror, every rung decoded (2026-10-12).

## What it is

When both badges can, a transfer sends its payload in fast blocks:
2-level PAM bursts on the backlight, rungs F0-F3 (2, 3, 4, 6 PWM periods a
symbol; 128, 64, 32, 32 bytes a block), paced by DMA, decoded by an
equaliser trained on each burst's preamble. The spec is sections 13.1 and
14.2 and Appendix A of
[the protocol spec](../../optical_data_transfer/spec.md); the
data is §10 of [.agents/optical_data_transfer/data.md](../../optical_data_transfer/data.md).

- **Only fast blocks** since 2026-10-18: every badge sends and decodes
  them; one without the pacer cannot share.
- **Rungs**: F0 lit, F1 dark; two NAKs on a rung step down; past F3, F3
  until the block's 48 attempts are spent.
- **The Follower decodes only after the burst is over**, and **retunes**
  later blocks on a rung instead of training them.

## What has been shown

**One badge, mirror B, `devtests/fast_loopback.py`** [measured-v2,
2026-10-12; sections 10.3-10.6 of `data.md`]:

| Run | Result |
|---|---|
| The sender (`fasttx`, DMA on the PWM wrap) | burst 46-77 µs after its slot boundary; DIV and CC put back every time; the ring healthy after a 0.82 s blocking burst |
| The old Follower, decoding as bins arrived | about half lost: training (300-434 ms) outlasts the ring (317.7 ms) |
| Decoding once the burst is binned | 36 of 36, dark, dimly lit, lit |
| The protocol's new code | 24 of 24, lit and dark; reply 355-481 ms after the burst, 800 allowed |
| Training once a rung | 40 of 40; a retune 54-70 ms against 300-403 for a training; 4 of 16 dark retunes refused (trained instead), none lit |
| Edge lag of a burst | dark mostly 800-920 µs (750 assumed), lit 365-457 (400 assumed); absorbed by the decoder's three starts |

**Simulation** (the badge's ring and costs; section 11 of `data.md`):
every standard row 144 of 144; over 72 seeds every hard row 1296 of 1296
(2026-10-19, fast blocks only). A credential from facing to done: 1.4 s lit,
1.7-1.8 in flicker, 2.1 dark (the slot blocks removed 2026-10-18: 2.0, 2.7,
3.8). A 2 KB payload: 110 B/s lit,
107 in flicker, 47 dark.

**Known costs**: I2 (the
grid check) trips in a third of `k3.0` runs, all delivered: the Leader
locks late on a NAK's opening middle and the next Begin corrects it.

## What to do

1. **One badge, optional**: `fast_loopback 0 8 0 defer fit` under a
   light that flickers. Run 2026-10-13 under the user's LED light: 8 of 8
   decoded, but the fit found the room steady, so the flicker terms were
   never on; again under the mains LED room light alone (8 of 8, steady):
   neither of the user's current lights flickers enough to fit. Needs a light the fit finds flickering (any placement where
   `room_flicker` reports a peak). Since 2026-10-18 the tools are dev link
   subtools: `sync` them once over the link, then on its mirror under the
   light run Dev Mode's *measure room flicker* and *fast blocks on mirror*
   untethered (or `run` them tethered); saved under `room_flicker` and
   `fast_loopback` in the measurements file. **Run 2026-10-14
   under a hard-flickering LED: 0 of 8** -- flicker ~10x the mirror's swing,
   the fit refused (360 Hz outside its 4 harmonics), no flicker terms
   (section 6 of `data.md`). **2026-10-18, too far from the
   light: 8 of 8**, but the flicker only 3.8 counts and the fit steady.
   Still wanted: a run where the fit finds flicker -- room light ~150-500
   counts at the sensor, flicker ~20-250 (capture 2's distance fitted to
   2.9 counts); `room_flicker` gives the verdict first.
2. **Done 2026-10-13**: fast transfers step back up a rung after
   `FAST_UP_ACKS` ACKs, doubling the wait after a failed step (spec
   13.1); after an early passing fault a 2-8 KB file moves 1.5-1.7x
   faster [simulation].
3. **Human, two badges**: transfers each way, dark, lit and under LED
   light. Only two badges show the two crystals over a burst (the 1 s cap
   rests on ≤ 100 ppm [unmeasured]), a real peer's light and lag, and
   whether dark pairs can start on F0 (11 of 11 dark F0 bursts decoded on
   the mirror, against 5 of 16 in simulation).

## Done when

Two badges have moved a credential in fast blocks in each room, recorded
per firmware, and no row of the sweep delivers less than section 11 of
`data.md` records.
