# Measure with a second badge

**Owner:** human. **Depends on:** a second Tufty 2350 with this branch's
share_wifi on it -- **none is available** (the user, 2026-10-12).
**Unblocks:** the dark starts, 10 ms slots and F0 fast blocks
([apply-hw1-results.md](apply-hw1-results.md)); the fast blocks' hardware
step ([fast-blocks-data.md](fast-blocks-data.md)); the block cap, and with
it whether one-period symbols with a block code are worth building
([file-transfer-speed.md](file-transfer-speed.md)); the short handshake's
hardware step ([short-handshake.md](short-handshake.md)).

## Why

Everything optical so far is a mirror. Three things a mirror cannot give:

- **A peer's swing and the sensor's speed under it.** Predicted ~90-100
  counts aligned, ~175-240 with the peer's screen over the sensor
  **[inferred-all]**, ±30%. The sensor is faster with more light, so this
  gates the 10 ms dark start: it holds on the measured sensor in
  simulation, but a sensor 2x slower gives 138 of 144 and 3x 52, so
  `EXACT_DARK_START_MS` stays 16.
- **Self-coupling:** a badge's own screen on its own sensor through the
  peer's glass. The simulator assumes 0 and stresses 5% (`self5`)
  **[unmeasured]**.
- **A real transmitter's lag:** a transfer's `field_at()` + `show_level()`
  path, seen by another badge, not a loopback.
- **The pair's clock offset**, for the fast path: every rung decoded on
  the mirror (2026-10-10, 2026-10-12) but the 1 s block cap rests on
  ≤ 100 ppm differential **[unverified]**, and a mirror has one crystal.
  No tool measures it directly yet; real transfers in fast blocks show
  whether 1 s holds, and `pam_link` between two badges would give the
  figure.

## What to do

Both badges, same firmware if possible (record each one's version; if one
is v3, every result is per-firmware):

1. Both tethered, each on its own dev link, held screen to screen with
   one's edge on the other's alignment line (the hold a transfer uses).
   `run second_badge first dark` on one and `run second_badge second dark`
   on the other, within a minute.
2. The same with `lit`, in a lit room.
3. Each run's JSON is saved by its host; `run measurements` on each gets
   the file too.
4. Real transfers each way, dark, lit and under LED light: Share on one,
   Receive on the other, a credential and (once built) a larger payload;
   note the time and outcome. These exercise fast blocks, deferred
   decoding and retuning between real badges.

The dev link can start the tool (`run second_badge first|second`);
`devlink_host.py OUTDIR run <tool>` runs one tool and exits non-zero on
failure.

## What it settles

Since 2026-10-11 the tool is the backlight and the ring throughout (see
"measure screen w/ 2nd badge" in the spec): each badge runs the mirror's
tests in step with the other, one sending and one reading.

- Peer swing (`levels`), its `noise` and averaging (`noise.*.means`), and
  `backlight_step_ring` (M4) under a peer's light: whether
  `EXACT_DARK_START_MS` can drop to 10. M4's times there are from the go's
  fall as the reader saw it, so read `t90_ms - t10_ms`.
- Self-coupling: `self.levels`, a badge's own light off the other's glass.
- The real lag and decoding: `loopback_ring` (M5) across the gap -- errors
  per rung, and `lag_us` against the reader's estimate of the sender's
  clock, so its drift across the turn's rungs bounds the pair's clock
  offset too.

## Done when

Both runs' figures are recorded in `.agents/optical_data_transfer/data.md`
with markers, and the simulator's `aligned`/`overlap` swings and `self5`
are checked against them.

Docs: `.agents/optical_data_transfer/optical-coupling.md`; sections 3, 7
and 13 of `.agents/optical_data_transfer/data.md`; the `second_badge`
subtool in `.agents/share_wifi/app-spec.md`.
