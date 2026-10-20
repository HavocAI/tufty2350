# The short handshake: confirm on hardware

**Owner:** human, two badges. **Depends on:**
[second-badge-measurements.md](second-badge-measurements.md). **Status:**
landed in simulation 2026-10-11; the mirror half done 2026-10-13.

## What it is

One Announce from the Leader (14 ms halves), one staircase from the
Follower, the Follower training its levels on every Begin; sections 8 and
9 of [the protocol spec](../../optical_data_transfer/spec.md), its evidence
in section 8 of [data.md](../../optical_data_transfer/data.md). From both
badges facing it took a credential from 2.6 to 2.0 s steady lit, 3.5 to
2.7 in flicker and 4.4 to 3.8 dark, before fast blocks [simulation].

On the mirror (2026-10-13, hands-free) the Announce matched 44 ms in dark
(swing 103) and 42 lit (swing 110), and never at twice the period
[measured-v2].

## What to do

**Human, two badges:** transfers each way, dark, lit and under LED light;
note the time and outcome. What only two badges can show: the Leader
reading a staircase while its own Announce flashes (self-coupling,
[unmeasured]), and a Follower training on a real peer's Begin.

## Done when

Two badges have paired through it in each room, recorded per firmware in
`data.md`.
