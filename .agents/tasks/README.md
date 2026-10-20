# Open tasks

One file per task; delete it when it lands and fold anything durable into
the `.agents/` folder it belongs to or `AGENTS.md`. The protocol's tasks
are in [optical_data_transfer/](optical_data_transfer/). The sweep figures the
agent tasks compare against are in section 11 of
[../optical_data_transfer/data.md](../optical_data_transfer/data.md).

| Task | Owner | Depends on | Status |
|---|---|---|---|
| [file-transfer-speed](optical_data_transfer/file-transfer-speed.md) | agent | nothing for the file layer (the protocol complete, the user, 2026-10-19) | the file layer designed, not built |
| [fast-staircase](optical_data_transfer/fast-staircase.md) | human, two badges | a peer's settle | mirror done 2026-10-13: slowest t90 2.30 ms dark, 0.67 lit; 20 ms steps stand |
| [short-handshake](optical_data_transfer/short-handshake.md) | human, two badges | two badges | mirror done 2026-10-13: the Announce matched dark and lit |
| [fast-blocks-data](optical_data_transfer/fast-blocks-data.md) | the user (light), then two badges | a flicker-fitted run where the fit succeeds | 2026-10-14 under a hard-flickering LED: 0 of 8, the fit refused (360 Hz outside its model) |
| [short-gaps](optical_data_transfer/short-gaps.md) | two badges | a second badge | drift measured and the flicker hold set from it, 800 → 500 ms (2026-10-14); the gaps await a real transfer |
| [apply-hw1-results](optical_data_transfer/apply-hw1-results.md) | agent | a second badge | the dark starts: 10 ms slots, and F0 fast blocks |
| [second-badge-measurements](optical_data_transfer/second-badge-measurements.md) | human | a second badge -- **none available** | blocked |
| [v3-dim-low](optical_data_transfer/v3-dim-low.md) | human, then agent | a badge on v3 | v3's lifted dim low unmeasured |

**USB driving (2026-10-13).** With the badge plugged in and its menu
running, an agent can reset it, upload this tree's apps to `/runs/`
without disk mode, launch share_wifi from a clean heap with
`builtins.DEV_AUTORUN = "link"`, and run any subtool over the link
(`tests/usbrun.py`, `tests/share_wifi/autorun_host.py`;
`.agents/docs/testing.md`). Since 2026-10-18 every measurement is a
subtool: uploaded over the link, then run over it or from Dev Mode's menu
untethered. What still needs hands: the light (the box lit, LED room light),
which mirror, a second badge, and a firmware change. When a second badge
comes, both plugged into the host could run transfers with no hands but
the alignment.

**No second badge is available** (2026-10-12). Long collections closed on
2026-10-13: a transfer on v2 collects in ~17-80 ms by where its launch
loaded, which the simulator's rows already bracket (section 2 of
`data.md`). The robustness checks -- M8 (relaunch after HOME) and
the dev link checklist's steps 8 and 13-16 -- were dropped on 2026-10-09
and stay **[unverified]**.
All hardware figures are per firmware: the badge runs v2.0.2, and a run on
v3 is a separate result (see the markers in `AGENTS.md`).
