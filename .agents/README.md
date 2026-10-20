# .agents

Notes written for agents working in this repo. **New here? Read this page,
then [../AGENTS.md](../AGENTS.md)**, which covers the repo, the badge API
traps and how to run the tests.

    docs/                    the badge and the test harness: reference, durable
    optical_data_transfer/   the protocol: spec, reasons, tests, data, invariants
    share_wifi/              the app built on it: its spec and the briefing
    tasks/                   one file per piece of work, self-contained, disposable;
                             the protocol's in tasks/optical_data_transfer/

When a task lands, delete its brief and fold anything durable into the
folder it belongs to or `AGENTS.md`.

## The work in five lines

- **share_wifi** is a Tufty 2350 badge app that passes a Wi-Fi network
  from one badge to another with light: one badge steps its backlight
  behind a white screen, the other reads its light sensor through a DMA
  ring.
- It is the **proof of concept for the optical data transfer protocol**,
  which carries any byte strings. **The protocol is complete** (the user,
  2026-10-19); **files are the goal**, and their layer is next.
- **In simulation** every scenario delivers: a credential takes 1.4 s lit,
  1.7-1.8 s in flickering light and 2.1 s dark; a 2 KB payload moves at
  110 B/s lit.
- **On hardware**, one badge facing a mirror has run every piece a
  transfer uses and decoded it. **No second badge is available**, so no
  transfer between two badges has run.
- **Everything since commit `fdb5ac2` on branch `terrys_toys` is
  uncommitted.**

## How to behave

- **Short replies, Orwell's six rules.** Do the work yourself; spawn
  other agents only for a narrow specialty, read-only on git, never on
  the badge's serial port.
- **No commits** unless asked; **no state-changing git** at all.
- **When the work is done, check every doc against the code and the
  results**, and say what you checked.
- **The badge runs firmware v2.0.2; this repo's source and the emulator
  are v3.** Neither is evidence about the badge. Every figure carries a
  marker naming its firmware ([docs/README.md](docs/README.md)).
- **The user does the physical steps** (copying the app, opening the dev
  link, placing the badge, the light). When they ask for measurements, run
  `sg dialout -c "python3 tests/share_wifi/devlink_host.py OUTDIR run TOOL ARGS"`.
- **share_wifi is unreleased**: no backward compatibility; signal on the
  backlight only; read through the DMA ring; fast blocks only.
- **Ignore `wifi_scanner` and `sand_squirrel`**: not this work.
- **Sweep at least 8 seeds** before concluding anything about the link.
- **`.bin` captures stay out of git.**

## Read in this order

1. [share_wifi/state.md](share_wifi/state.md) -- the briefing: the app,
   the protocol, the evidence, a code map, tools, open work, traps.
2. [../AGENTS.md](../AGENTS.md) -- how to work in this repo, the v2/v3
   differences, the rules that have already cost hardware cycles.
3. [tasks/README.md](tasks/README.md) -- the open tasks, who owns each,
   what blocks it.
4. [optical_data_transfer/spec.md](optical_data_transfer/spec.md) -- the
   protocol, before changing anything in it.

## All the documents

**The protocol** ([optical_data_transfer/](optical_data_transfer/))

| Document | What it gives |
|---|---|
| [spec.md](optical_data_transfer/spec.md) | **the authority**: what the protocol does, every constant, the interface |
| [context.md](optical_data_transfer/context.md) | why each value is what it is |
| [tests.md](optical_data_transfer/tests.md) | how to check an implementation; runnable scripts |
| [data.md](optical_data_transfer/data.md) | **the only record of the data**: every measurement and sweep a decision rests on, a decision index, what is not known, how to measure again |
| [invariants.md](optical_data_transfer/invariants.md) | the link invariants the simulator asserts on every tick: start here before changing the link |
| [.agents/optical_data_transfer/optical-coupling.md](optical_data_transfer/optical-coupling.md) | the geometry behind the predicted swing between two badges |
| [captures/](optical_data_transfer/captures/) | the F0 mirror captures `test_fastblock.py` decodes (`.bin`, local only) |

The spec, context and tests link to nothing outside themselves, so they
stand alone.

**The app and the badge**

| Document | What it gives |
|---|---|
| [share_wifi/state.md](share_wifi/state.md) | the briefing: everything in one place, with links into code and docs |
| [share_wifi/app-spec.md](share_wifi/app-spec.md) | the app's screens, storage, Wi-Fi, Dev Mode, the USB dev link and its subtools |
| [../firmware/apps/share_wifi/README.md](../firmware/apps/share_wifi/README.md) | the app as a proof of concept, and a short technical overview |
| [docs/README.md](docs/README.md) | which badge doc answers which question, and the confidence markers |
| [docs/badgeware-hardware.md](docs/badgeware-hardware.md) | the badge: panel, sensor, timing, DMA ring, memory |
| [docs/badgeware-api-v2.md](docs/badgeware-api-v2.md), [docs/badgeware-api.md](docs/badgeware-api.md) | every call v2 has; the v3 API |
| [docs/badgeware-patterns.md](docs/badgeware-patterns.md) | how to write badge code; traps already hit |
| [docs/testing.md](docs/testing.md) | the suite, the file layout, the simulator, the USB dev link and its hardware checklist, driving the badge with no hands |
| [../README.md](../README.md), [../APPS.md](../APPS.md) | the developer quick start; how an app is installed |

**The code**

| File | What it is |
|---|---|
| [../firmware/apps/share_wifi/protocol.py](../firmware/apps/share_wifi/protocol.py) | the protocol: pure Python, no badge globals |
| [../firmware/apps/share_wifi/reader.py](../firmware/apps/share_wifi/reader.py) | the ring reader |
| [../firmware/apps/share_wifi/fastblock.py](../firmware/apps/share_wifi/fastblock.py), [fasttx.py](../firmware/apps/share_wifi/fasttx.py) | the fast block's codec and its DMA transmitter |
| [../firmware/apps/share_wifi/fastadc.py](../firmware/apps/share_wifi/fastadc.py) | the ADC and DMA registers behind the ring |
| [../firmware/apps/share_wifi/__init__.py](../firmware/apps/share_wifi/__init__.py) | the app: menus, storage, the transfer loop, Dev Mode |
| [../firmware/apps/share_wifi/devlink.py](../firmware/apps/share_wifi/devlink.py), [devserver.py](../firmware/apps/share_wifi/devserver.py), [usbport.py](../firmware/apps/share_wifi/usbport.py) | the USB dev link, badge side |
| [../tests/share_wifi/](../tests/share_wifi/) | the tests, the simulator and sweep, the dev link's host program and its subtools ([docs/testing.md](docs/testing.md) lists every file) |
| [../ci/ruff.toml](../ci/ruff.toml) | the lint CI runs over `firmware/` |
