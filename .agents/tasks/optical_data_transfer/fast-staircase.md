# Short staircases: confirm on hardware

**Owner:** human, two badges. **Depends on:**
[second-badge-measurements.md](second-badge-measurements.md) for a peer's
light. **The mirror half is done (2026-10-13, mirror B, hands-free):** the
slowest t90 2.30 ms dark and 0.67 lit, so 20 ms steps stand in both
(section 7 of `data.md`). Mirror A was not used; B
unobstructed gives at least its swing. Part of sections 8 and 9 of the protocol spec
(2026-10-11, simulation only).

## Why

The Follower now sends the pair's one staircase, of `PLATEAU_FAST_MS` (20 ms) steps --
one repetition LIT, two dark (`CAL_REPS_FAST_LIT`, `CAL_REPS_FAST`) --
sampled every 2 ms (`FINE_MS`, ~100 ring readings each), and
each level needs `MIN_HELD_FAST` (5) settled samples. Was 40 ms steps,
two and three repetitions, 4 ms samples and 8 held: the body went from
240/360 ms to 60/120. Since 2026-10-11 the Leader sends none. 16 ms steps
saved ~10 ms more and 14 lost runs, so 20 stays.

16 ms steps with one repetition lit failed in simulation: the body held
only 28 samples (the first step, LOW, merges with the opening), under the
32 `levels()` wanted, and 7 of each level. 20 ms with
`CAL_FAST_MIN_SAMPLES` 24 holds 8-10.

What rests on the mirror: the sensor's settle -- lit t90 ~1.4 ms, dark
~2.4 rising to the middle and ~4 falling [measured-all, M4] -- leaves 6-9
settled 2 ms samples a step. A peer that lights the sensor less than the
mirror settles slower **[unmeasured]**; the sweep's `k2.0`, `k3.0`, `sw56`
rows stand in for it.

## What to do

1. **Human, mirror A:** M4 (`backlight_step_ring`) and M5
   (`loopback_ring`): `run mirror dark` and `run mirror lit` over the dev
   link. Nothing new to run; the figures to read are M4's t90 per
   transition.
2. **Agent:** if any transition's t90 is over ~5 ms, 20 ms steps hold
   fewer than `MIN_HELD_FAST` settled samples: raise `PLATEAU_FAST_MS` and
   re-sweep against section 11 of `.agents/optical_data_transfer/data.md`.

   Mirror B (the box, used since 2026-10-09) will do if mirror A is not to
   hand: unobstructed it gave at least A's swing; record which mirror.
3. **Human, with a second badge:** a real transfer each way, dark and lit,
   noting the time and outcome; the 2nd-badge tool's `backlight_step_ring`
   gives a peer's settle.

## Done when

M4 on mirror A and a peer's settle are recorded against `PLATEAU_FAST_MS`
with markers, and the constant either stands or is re-swept.
