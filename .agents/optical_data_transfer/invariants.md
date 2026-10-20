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

Context: [the protocol spec](spec.md)
and [`data.md`](data.md)

## Clock

**I1 — the badges are on the same rung.**
`leader.clock.index == follower.clock.index`, except for a bounded window while
a rate change is propagating. A permanent split has happened before: a Leader on
65ms against a Follower on 50ms, neither able to hear the other, both eventually
reporting `ERR_LOST_PEER`. Another starts one: two badges whose frame times
differ -- v2 firmware against v3, or a frame slipped to two refreshes -- used
to start on different rungs. The rung a transfer starts on is now agreed in
calibration -- the Follower decides the pair's light and the Leader adopts it
(section 9.6 of the protocol spec); `tests/share_wifi/test_handshake.py`
holds it. Until 2026-10-11 the staircases also carried a reader tier, and a
mixed pair met on the polled rungs; every badge reads through the ring now,
so no tier is carried or negotiated.
No flicker flag travels since 2026-10-11 (the short handshake): the Leader's
own fit decides the hold, which never moves the epoch, and a Follower always
allows for it -- a hold the Follower did not expect costs a wait, tested
both ways: a Leader that did not hold, facing a Follower that expected
one, sends its first block again after its reply wait, and the Follower
waits for it (since 2026-10-19). Two more
went with slot blocks and breaking in on 2026-10-18: a Leader-started
demotion the Follower never heard (F-B1), and a break read as a CRC.
And any split
still made -- an error sent into the Leader's block -- now heals: a
Follower that demoted on its own measures the Leader's next message and
moves to its rung (`test_a_follower_a_rung_off_the_leader_moves_to_its_rung`).

**I2 — the slot grids agree.**
The epochs may differ, but not by enough to matter:
`abs(leader.clock.epoch - follower.clock.epoch) % duration` is within a quarter
of a slot of zero. Half a slot is fatal, because the receiver samples the back
half and lands on the transmitter's transition.

Epochs are in µs since each badge's transfer began, so the simulator puts
both on one timeline (each badge's start, and its crystal's error) before
comparing them.

Two badges' grids sit apart by the difference between the edge delay
assumed (`EDGE_LAG_*_US`) and the one seen. On the measured sensor that is
tens of µs; on one slower than measured it is a few ms, which on an 8 or 16
ms slot can pass a quarter of it without a symbol going wrong: at three times
the measured sensor time constant, I2 tripped in 24 of 144 runs that all
delivered. Read it as a diagnostic, and delivery as the figure. With
fast blocks (2026-10-11) it trips in 40 of 144 `k3.0` runs, all
delivered: the Leader locks on the Follower's NAK, whose first symbol is a
middle, and a slow sensor crosses `T_low` late on a rise to the middle;
the Follower re-locks on the next Begin. And in a few once-a-cycle runs
while a burst is on air, when no slot is decided.

A Follower (2026-10-10) allows `start_grace` more slots before its
first lock: a Leader that read flicker holds its first block back as black
lead-in, so there is no edge to lock to until it comes. The simulator's I2
window grows by the same, and only until the Follower first decides a lit
slot, where `start_grace` returns to 0: a split later in the transfer is
reported within `LOST_PEER_SLOTS` as before
(`test_the_start_grace_ends_at_the_first_light_and_a_later_split_is_seen`).

**I3 — a rate change never moves the epoch.**
Only `duration` changes. Re-basing the clock on a badge's own idea of "now"
leaves the two grids a slot apart.

## Framing

**I4 — no block is accepted twice.**
The Follower keeps fast blocks by chunk index, so a block resent after a
lost ACK replaces itself; `follower.received` is the stream from chunk 0
once it parses, never a block appended twice. A duplicate silently corrupts the payload
while every CRC passes.

**I5 — the Leader's chunk index only advances.**
It may stall on a retry; it must not go backwards or skip.

**I6 — exactly one badge transmits at a time.**
Both driving a non-black field in the same slot means the half-duplex turn-taking
has broken.

## Termination

**I7 — no false success.**
If the Leader reports `OK`, the Follower must have accepted every block. This has
been violated: the Leader reported `OK` with the Follower holding nothing,
which for this app means silently claiming to have shared a credential it did
not share. Of everything on this page, this is the one that matters most.
Since 2026-10-10 the Leader takes the Complete Ack less its first symbol
(`COMPLETE_ACK_LATE_TAIL`, `_- __-`); it still needs a black between lit
symbols, which neither a rung's bits nor a Data Sync Error carry
(`tests/share_wifi/test_complete_ack.py`).

**I8 — both badges finish.**
Neither is left with `result is None` once the other has finished.
A repeat of Complete always lands while the Follower still lingers: the
Leader waits out lit slots before repeating (a late answer may be arriving)
but never past `COMPLETE_REPEAT_MAX_SLOTS` = `LINGER_SLOTS` - 2 after the
last Complete (2026-10-09; `test_stray_light_cannot_hold_the_repeat_past_the_followers_linger`).

## Progress

**I9 — the link is getting somewhere.**
No stretch longer than `STALL_SLOTS` slots in which a badge is listening and the
transfer does not advance — a pattern matched, a block kept, a reply
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
many times one block may go out, NAKs and Data Sync Errors alike, because
the NAK count resets on every rung change -- which left resends bounded by
nothing at all.
