# .agents

Notes written for agents working in this repo. Two kinds, kept apart:

    docs/       how the badge works -- reference, durable; start at docs/README.md
    tasks/      one file per piece of work, self-contained
    context/    reference an in-flight task depends on

`tasks/` and `context/` are about *doing a task* and are disposable. When a
task lands, delete its brief and fold anything durable into `docs/` or
`AGENTS.md`.

Read [../AGENTS.md](../AGENTS.md) first regardless — it covers the repo, the
badge API traps, and how to run the tests.

## Open

Nothing. The phase-locking task landed and its brief was deleted; what it
produced lives in `docs/optical-data-transfer-protocol.md` under *Phase locking*
and *Where this still fails*. `tasks/` is empty until the next brief arrives.

[context/optical-link-invariants.md](context/optical-link-invariants.md) outlived
that task and is not disposable: it is what `tests/share_wifi/simulator.py`
asserts on every tick, and the right place to start before changing anything
about the optical link.
