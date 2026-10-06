# Invariants of a healthy optical link

Properties that must hold throughout a transfer between two badges. They are
written as things to *assert continuously in the simulator*, not as things to
check at the end — every bug that has cost real time in this protocol was a
divergence that only showed up as a mystery timeout several seconds later.

Where each one is checked: I1, I2, I4 and I7 are asserted on every simulated
tick by `tests/share_wifi/simulator.py`, which records the first violation with
the time and both badges' state. I11 is a termination property rather than a
per-tick one, so it is checked by `tests/share_wifi/test_no_dead_ends.py`, which
drives each side into each state against a peer that has stopped co-operating.
The rest are not yet mechanically checked; say so rather than assuming a green
suite covers them.

Context: [../docs/optical-data-transfer-protocol.md](../docs/optical-data-transfer-protocol.md)

## Clock

**I1 — the badges are on the same rung.**
`leader.clock.index == follower.clock.index`, except for a bounded window while
a rate change is propagating. A permanent split has happened before: a Leader on
65ms against a Follower on 50ms, neither able to hear the other, both eventually
reporting `ERR_LOST_PEER`. Another starts one: two badges whose frame times
differ -- v2 firmware against v3, or a frame slipped to two refreshes -- used
to start on different rungs. The rung a transfer starts on is now agreed in
calibration from both badges' frame-rate classes ("Agreeing a rung" in the
protocol); `tests/share_wifi/test_rate_class.py` runs such pairs.

**I2 — the slot grids agree.**
The epochs may differ, but not by enough to matter:
`abs(leader.clock.epoch - follower.clock.epoch) % duration` is within a quarter
of a slot of zero. Half a slot is fatal, because the receiver samples the back
half and lands on the transmitter's transition.

With a present delay (`simulator.py --v2`, or `present_ms=`), I2 trips in
almost every run: the receiver's grid lands later than the transmitter's by
the part of the delay `edge_lag` does not correct, which phase locking does
on purpose. Under that model I2 measures the offset, not a broken link, and
delivery is the figure to read. A check that compares each receiver's read
window with the transmitter's visible symbols would hold under both.

Continuous badges (`simulator.py --loop`) mostly hold it -- their edge delay
is corrected -- but a sensor slower than the one measured leaves a few ms
between the delay assumed and the one seen, which on a 32 ms slot can pass a
quarter of it without a symbol going wrong: at twice the measured sensor
time constant, I2 tripped in 59 of 144 runs that all delivered. Read it as a
diagnostic there too.

**I3 — a rate change never moves the epoch.**
Only `duration` changes. Re-basing the clock on a badge's own idea of "now"
leaves the two grids a slot apart.

## Framing

**I4 — no block is accepted twice.**
`follower.received` grows only by whole blocks, and never re-appends a block the
Leader has already had acknowledged. A duplicate silently corrupts the payload
while every CRC passes.

**I5 — the Leader's block index only advances.**
It may stall on a retry; it must not go backwards or skip.

**I6 — exactly one badge transmits at a time.**
Both driving a non-black field in the same slot means the half-duplex turn-taking
has broken.

## Termination

**I7 — no false success.**
If the Leader reports `OK`, the Follower must have accepted every block. This has
been violated: the Leader reported `OK` with the Follower holding zero tokens,
which for this app means silently claiming to have shared a credential it did
not share. Of everything on this page, this is the one that matters most.

**I8 — both badges finish.**
Neither is left with `result is None` once the other has finished.

## Progress

**I9 — the link is getting somewhere.**
No stretch longer than `STALL_SLOTS` slots in which a badge is listening and the
transfer does not advance — a pattern matched, a token collected, a verdict
acted on, something queued to transmit.

This used to be written as "neither badge decodes a non-black symbol", and that
wording is what let the dead ends through. A peer stuck showing grey, or one
whose symbols are being decoded on a grid that no longer lines up, is not silent
at all: every slot decodes to something, so a counter watching for silence is
reset every slot and never fires. Both badges then wait for a pattern that
cannot arrive, indefinitely. Driving each state against an uncooperative peer,
18 of 56 combinations never terminated.

Hearing something is not the same as getting somewhere, and only the second one
is liveness. `LOST_PEER_SLOTS` still bounds silence; `STALL_SLOTS` bounds the
absence of progress. A link needs both.

**I10 — demotion is monotonic and bounded.**
Rate only steps slower during a transfer, never faster, and never below the
slowest rung.

**I11 — every wait ends.**
From any state, against any peer behaviour including one that has stopped
co-operating, a badge reaches a result within `BLOCK_TIMEOUT_MS` of its last
accepted block. A transfer is allowed to fail; it is not
allowed to sit there. Retrying is bounded too: `MAX_BLOCK_ATTEMPTS` caps how
many times one block may go out, because `MAX_BLOCK_RETRIES` only counts CRC
failures and the Data Sync Error path deliberately resets it — which left
sync-error resends bounded by nothing at all.
