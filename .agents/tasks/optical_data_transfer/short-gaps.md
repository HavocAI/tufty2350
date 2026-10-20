# Shorter closing gaps and shared gap: confirm on hardware

**Owner:** the user (LED light, then two badges), then agent.
**Depends on:** two badges to confirm. **A real room's drift was measured
on 2026-10-14**: -10.1 and +6.2 mHz against the badge's own clock, a
wander of 15-17° in 24 s, under a 60 Hz LED light [measured-all] -- half
the simulator's 0.02 Hz (section 6 of `data.md`). **The hold
followed it (2026-10-14): `CAL_GAP_FAST_FLICKER_MS` 800 → 500**, every
flicker row delivering over 72 seeds at twice the measured drift; a
credential in flicker 2.0-2.1 → 1.7-1.8 s. What stays: the closing and
shared gaps (`CAL_GAP_FAST_MS` 110) and a real transfer between two
badges. The collection a transfer meets on v2 is settled:
~17-80 ms (2026-10-13, section 2 of `data.md`); v3's is
not.

## Why

Built 2026-10-10 (simulation); since 2026-10-11 the staircase's closing gap
is the only one. It is found 50 ms in (`CAL_CLOSE_FAST_MS` 70 less
`CAL_CLOSE_FAST_SPARE_MS` 20): past a step's 20 ms of black and the
Announce's 17 ms half. The gap is the shared one, `CAL_GAP_FAST_MS` 110,
and holds both flicker fits: the Leader's at 100 ms, the Follower's from
70. When the Leader's own fit found flicker, it holds its first block back
to `CAL_GAP_FAST_FLICKER_MS` 500 (800 until the drift was measured).

Two things set those, both from simulation:

- **The Leader collects when it finds the Follower's gap**, then starts
  the data phase. With 20 ms left, collections of 65-81 ms put its first
  block past the Follower's `LOST_PEER_SLOTS` on 8 ms (`lit47gc70`, seed 3,
  every scenario). 110 keeps the old 60 ms after finding it.
- **In flicker, both badges track the flicker's phase and drift through
  the gap.** At 110, flickering rooms demoted to 10 ms in 10-46% of runs
  (24 seeds); at 350 and 500 once-a-cycle lights (`half50`, `half60`)
  still failed 0.4-1% more than the slow handshake over 72 seeds -- the
  Leader reached the data phase with no drift rate, whose line must span
  `FLICKER_RATE_MIN_S`. Held back in the data phase it settled at 800;
  at the measured drift 500 delivers every flicker row over 72 seeds, and
  costs a flickering room ~0.39 s; a steady room pays none.

The hold never sets the gap: both badges take the epoch at
`CAL_GAP_FAST_MS`; in flicker the Leader holds its first block back by
`CAL_GAP_FAST_FLICKER_MS` less that, and a Follower always allows for it
(`FAST_START_GRACE_MS`), so a hold it did not expect costs a wait, not the
epoch; one it expected and did not get costs the Leader's reply wait and a
resend (2026-10-19).

What rests on unmeasured hardware: the collection a transfer meets on v2
(~17 to ~80 ms by where a launch leaves the sweep's extent, 2026-10-13,
section 2 of `data.md`) and on v3
**[unmeasured-v3]**, and how fast a real room's flicker drifts (the
simulator's 0.02 Hz) [unmeasured].

**Stays open:** long collections in a once-a-cycle flicker were the
worst row: `v2/half60gc70` delivers 1296 of 1296 over 72 seeds with fast
blocks, with a few I2 violations. Collections stay long on a launch that
loaded high: a stub `__init__.py` to prevent it was judged not worth it
(2026-10-13).

## What to do

1. **Human:** a real lit transfer between two badges (or the mirror's
   M5 run as a stand-in for the gap's tracking), and *measure room
   flicker* under LED light for the drift.
2. **Agent:** if collections are the long ones in a transfer, re-sweep the
   `gc70` rows at `CAL_GAP_FAST_MS` 110 and 150; set
   `CAL_GAP_FAST_FLICKER_MS` from a real room's drift (M6's mHz), and try
   seeding the rate line from the fit window instead of a longer gap.

## Done when

The gaps rest on a measured collection and a measured drift, each with a
marker, and every row of the sweep delivers at least what section 11 of
`.agents/optical_data_transfer/data.md` records.
