# The dark starts: 10 ms slots, and F0 fast blocks

**Owner:** agent. **Depends on:** [second-badge-measurements.md](second-badge-measurements.md).
Context: [../optical_data_transfer/data.md](../../optical_data_transfer/data.md)
(sections 7 and 10.7, and section 11 for the sweep to compare against),
[../optical_data_transfer/invariants.md](../../optical_data_transfer/invariants.md).

HW1 and HW2 (2026-10-09) are applied. Closed then, each answered in
`data.md`:

- `correct()` against `amount()`: two heap-allocated frames a call,
  removed (section 2); M7's next `correct` figure checks the 0.20 ms price.
- The least gap for a ring pair: `MIN_GAP_EXACT` 16 (section 7).
- The interpreter: closed; the probe's empty loop times its own
  bookkeeping [inferred-v2] (section 2).

## Still open

Both wait on a second badge's light and speed:

- **The 10 ms dark start** (`EXACT_DARK_START_MS`, 16 now): 10 ms holds on
  the measured sensor (144 of 144 in every dark row) but not on one 2x
  slower (138) or 3x (52) [simulation]. M5 decoded 10 ms dark with no
  error on the mirror [measured-v2].
- **F0 for dark fast blocks** (`FAST_START_DARK`, F1 now): simulation had
  dark F0 decoding 5 of 16 bursts; on the mirror it decoded 11 of 11 with
  the deferred decoder, dark edge lags of up to 1061 µs included
  [measured-v2, 2026-10-12]. On a dark 2 KB payload F1 gives 47 B/s.

## Done when

Both are set from a second badge's figures, or moved to their own task.
