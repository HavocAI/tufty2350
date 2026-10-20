# A per-firmware dim low for v3

**Owner:** human (a v3 badge's mirror run), then agent. **Depends on:** a
badge running v3.

## Why

v3's backlight floor (12.5% duty) lifts the dim low
(`display.backlight(0.5)`) to ~0.16 of full light against ~0.04 on v2,
narrowing the low-to-middle gap to ~34 counts on an 80-count swing
**[inferred-v3, simulation]**. See section 3 of `data.md`.

What made it urgent is gone: the rows that lost runs to it (138 of 144,
`ERR_CONTRAST`) were a **polled** v2 badge
reading a v3 peer, against the polled reader's 32-count gap, and the polled
reader went on 2026-10-11. Every badge reads through the ring, whose least
gap is `MIN_GAP_EXACT` (16), and every v2/v3 row delivers 144 of 144
(rows `v2v3`, `v3v2`, `v3`; section 11 of `data.md`) [simulation].

What remains: the lift itself is unmeasured, and the fast block
(`file-transfer-speed.md`, "v3's optics") reads whatever low the backlight
gives.

## What to do

1. **Human:** on a v3 badge, tethered on its mirror: the `mirror` subtool
   (`dark`, then `lit`) and `frame_probe`, over the dev link.
2. **Agent:** if the v3 dim low is lifted as modelled and that costs a
   ring pair or the fast block anything, give the low a per-firmware value
   (about 0.25 on v3 matches v2's ~14% duty), sweep the v3 rows (8 seeds),
   and update the spec, protocol and `LEVEL_SCHEMES`.

## Done when

The measured v3 low is recorded in `data.md` with a marker, and in
the protocol spec as a per-firmware low or the reason none is needed.
