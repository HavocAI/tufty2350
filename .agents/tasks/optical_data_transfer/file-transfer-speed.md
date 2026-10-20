# Files over the optical link

**Owner:** agent, human for the hardware runs. **Status: open.** Deferred
on 2026-10-13 until the protocol was complete; the user declared it
complete on 2026-10-19. **Depends on:** nothing for the file layer;
[second-badge-measurements.md](second-badge-measurements.md) for the
clock offset that sets the block length.

## Where it stands

The fast path is built and is the only payload since 2026-10-18: F0-F3,
2-level PAM at 2-6 PWM periods a symbol, paced by DMA, decoded by a linear
equaliser with tracked flicker regressors, deferred until the burst is
over, retuned after a rung's first block (spec 13.1, 14.2, Appendix A).
Credentials go in one block. A 2 KB payload moves at 110 B/s lit, 107 in
flicker, 47 dark [simulation]; F0 decoded 0 errors in 32,496 symbols per
room on the mirror [measured-v2]. How the scheme was chosen, and what was
measured and rejected (more levels, MLSE, OFDM, a faster PWM): section 10
of [data.md](../../optical_data_transfer/data.md).

Not built: the file layer below. `fastblock.py`'s first draft of it
(`FileIn`, the announcement, the bitmap runs) was removed on 2026-10-18 as
unused; the design stays here.

## The file layer (design)

- **Resumable**: the Leader announces the file once -- name, length,
  CRC-32 of the whole, chunk size 32 -- as slot-rate fields before the
  first block, and blocks are numbered by their first 32-byte chunk, as
  fast blocks already are. The receiver keeps a bitmap of the chunks
  received. On a lost peer it keeps the partial file and the bitmap. A
  later transfer of the same file (same name, length and CRC) starts with
  the receiver's bitmap, sent run-length coded at the slow rung (unsigned
  LEB128 runs, alternating, the first counting received chunks, summing
  to the chunk count), and the Leader sends only the gaps.
- **Storing**: received blocks are held in RAM up to a limit (the heap is
  8 MB, so a 64 KB file fits) and written once, whole, when the file's
  CRC passes: one file, written once, not per block (flash wear,
  AGENTS.md). A file too big for RAM is written in 4 KB-aligned chunks of
  whole flash blocks, each once, with the bitmap saved beside it only on a
  lost peer. The file's CRC is checked before it is renamed into place.
- **Refuse** an announcement in any other form, or over 0xFFFF chunks.

## Open items

- **The pair's clock offset** (second badge): the 1 s block cap rests on
  ≤ 100 ppm differential [unverified]; at 230 ppm F0 breaks in
  simulation. Lifting the cap needs the receiver to measure the
  transmitter's period (the Begin's edge and a sync word at the block's
  end) and scale its grid -- not built.
- **One-period symbols with a block code** (3 kbit/s raw) look feasible on
  the mirror; worth building only if 1 s blocks hold between two badges
  (`data.md` 10.8).
- **Real mains flicker**: a flicker-fitted fast-block decode has not run
  on hardware (`fast-blocks-data.md`).
- **v3**: its DIV 1.25 and fractional divider have run only on the
  virtual mirror; its lifted dim low is `v3-dim-low.md`.
- **A sleep resets the PWM divider**: `badge.sleep()` re-runs the panel's
  `init()` [source-v3]; a transfer that could sleep mid-way must set DIV
  again on wake, or end.

## Done when

Files of up to 64 KB move between two badges, resumably, the design above
written into the protocol spec and its evidence into `data.md`.
