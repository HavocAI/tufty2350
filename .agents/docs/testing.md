# Tests

```
python3 tests/run_tests.py              # everything
python3 tests/run_tests.py share_wifi   # one app
python3 tests/run_tests.py -v           # list every test
```

Two tests need tools the suite does not bring, and are **skipped** without
them -- counted in the summary line (`560 passed, 0 failed, 2 skipped`) and
listed under it with what to set:

```
MPY_CROSS=/path/to/mpy-cross \
MICROPYTHON=/path/to/micropython \
python3 tests/run_tests.py
```

`MPY_CROSS` is MicroPython's cross-compiler, which compiles `fastadc.py`'s
viper for the badge's architecture (`-march=armv7emsp`: this mpy-cross has no
armv8m); `MICROPYTHON` is the unix port, whose x64 viper runs the ring
kernels for real. Either is also found on the `PATH`. A run without them has
not checked viper at all -- say so when reporting it. A test raises
`harness.Skipped(why)` to be counted this way.

No dependencies, no test framework, and the runner is the only supported way in.
It puts `tests/` and each `tests/<app>/` on `sys.path` itself; pytest does not,
so `from harness import ...` fails under it.

**An app's tests live in `tests/<app>/`**, named to match `firmware/apps/<app>/`.
They are kept out of `firmware/` because the firmware build packs everything
under it onto the badge. The shared harness and the runner sit directly in
`tests/`. Nothing needs registering: every folder in `tests/` is found.

## A trap when A/B testing the protocol

`load_module()` returns the **cached** module, so anything patched onto it in one
arm of a comparison is still there in the next. Patching a method to disable a
feature, running that arm, then running the "enabled" arm compares the disabled
arm against itself -- and reports the two as identical, which reads exactly like
"the change makes no difference".

That happened, and it was only caught because the change was known to alter the
slot rate and the rung distributions came back byte-identical. Capture the
original before the first arm and restore it explicitly:

    REAL = protocol.Transfer.some_method          # before anything patches it
    ...
    protocol.Transfer.some_method = REAL          # to enable
    protocol.Transfer.some_method = stub          # to disable

Setting module-level CONSTANTS per arm is safe, because each arm assigns them
again. It is methods and other patched attributes that leak.

## Two ways in

**`load_module(app, name)`** imports a pure module with no stubs whatsoever.
`firmware/apps/share_wifi/protocol.py` is the whole Optical Data Transfer Protocol and touches nothing on
the badge, so its tests just import it. Prefer this: it is faster, and the test
is exercising the real module rather than a re-exec of it.

**`load_app(name)`** is for the parts that genuinely need hardware -- drawing,
buttons, `State`, `wifi`. It stubs the injected globals and `exec()`s the app,
returning its namespace.

Two of its switches exist because firmware builds disagree with each other, and
with the documentation:

    load_app("share_wifi", alignment=True)          # a firmware that HAS
                                                    # LEFT/CENTER/ELLIPSES
    load_app("share_wifi", state_returns_flag=True) # State.load returns a bool

Anything drawing text, or reading `State`, should be exercised both ways.

`secrets=` supplies a `FakeSecrets`, standing in for the firmware module the
badge is provisioned through. It defaults to blank fields, which is what ships,
so an app that seeds itself from it is tested unprovisioned unless a test says
otherwise. `FakeState.writes` counts flash writes, for asserting that a launch
with nothing to do costs nothing.
Current firmware has the alignment constants and `State.load()` does return a
bool, so `state_returns_flag=True` is the truthful arm today; `alignment`
defaults to `False` because an app must still draw on a build without them.

**The stand-ins go into `__builtins__`, not into the app's namespace**, because
that is where the firmware puts them: `badgeware/__init__.py` is a run of
`builtins.CENTER = image.CENTER` and `setattr(builtins, k, v)`. The difference
is not cosmetic. An app probing for a firmware difference with
`globals().get("CENTER")` reads `None` on a real badge whether the constant
exists or not, and a harness that dropped `CENTER` into the module namespace
answered yes and hid it -- `share_wifi`'s alignment probe was dead code for as
long as the two disagreed. `load_app` copies the stand-ins back into the
returned namespace afterwards, so tests still reach `namespace["screen"]`.

If you find yourself reaching for `load_app` to test logic, that is usually a
sign the logic wants to move into a module of its own.

## How load_app is possible without a badge

A badge app is a plain Python module that reads globals the firmware injects --
`badge`, `screen`, `color`, `image`, `run`, `State`, `wifi`, the `BUTTON_*`
constants. None of them exist in CPython, but nothing stops us supplying our own.
`harness.load_app("share_wifi")` builds a set of stand-ins, `exec()`s the app,
and hands back the resulting namespace. Every function, class and constant in the
app is then directly callable.

Two details make it work:

- `run()` is a stub that does nothing by default, so importing an app does not
  start an endless loop. Apps that drive their own `while True` loop instead are
  escaped by `FrameLimit`, which the fake `badge.update()` raises after a set
  number of frames.
- `time.ticks_ms`, `ticks_us`, `ticks_diff` and `ticks_add` are MicroPython
  additions. The harness installs them onto CPython's `time` module.

## What this does and does not prove

It exercises logic: codecs, CRCs, framing, parsers, state machines, and the
navigation between screens. `FakeScreen` records every string passed to `text()`,
so a test can assert what a screen says without rendering anything.

It proves nothing about appearance, real frame timing, or whether the hardware
constants are right. `LCD_SETTLE_MS` in `share_wifi` could only be bounded on a
badge, not fitted, and the optical tests feed a synthetic sensor -- they show
the maths is sound, not that it survives a real room. `test_measure.py` is the
same: Dev Mode's engine is run against a simulated panel whose tau, gamma and
noise are whatever the test told it, so it shows the engine recovers what it
was given, not what the real panel does.

## Layout

```
tests/
  harness.py            the stubs and the loader, shared by every app
  fontmetrics.py        real glyph widths from romfs/fonts/*.ppf, and the firmware's wrapping
  run_tests.py          discovery and runner
  share_wifi/
    simulator.py            two badges on one timeline, through the production readers; not a test, a tool
    sweep.py                the simulator over 8 seeds x 6 scenarios x 3 payloads, row by row; a tool
    fakeviper.py            stand-ins for viper's ptr16/ptr8 and machine/rp2, to load fastadc.py under CPython
    pattern_survey.py       control-pattern designs, measured; not a test, a tool
    # Optical Data Transfer Protocol (firmware/apps/share_wifi/protocol.py)
    test_crc.py             CRC-16/CCITT-FALSE, including the standard check value
    test_payload.py         byte fields, block splitting, payloads of any length
    test_framing.py         symbol-level loopback, transmitter into receiver
    test_optical.py         beacon detection, calibration, the slot ladder
    test_integration.py     two badges driven frame by frame, from the data phase
    test_lifecycle.py       the whole protocol, first beacon to last pattern
    test_no_dead_ends.py    every state terminates against an uncooperative peer
    test_patterns.py        the control patterns obey the rules receivers rely on
    test_module_boundary.py protocol.py stays free of badge globals and of WiFi
    test_spec_matches_code.py  the protocol and app specs' tables agree with the code
    test_schemes.py         the one data scheme: the backlight from calibration on, fields for the beacons
    test_reader.py          reader.py: the ring reader's clock, index and queries over a list ring, and the polled one
    test_fastadc_kernels.py the viper kernels against their pure twins; mpy-cross and the unix port's viper when found
    test_slot_window.py     where a slot is decided, which slot is decided next, re-bases and demotions
    test_reply_gap.py       the one silent slot before a reply, a message held up whole
    test_calibration_exact.py  the one calibration's lengths, fit windows, and where the stalls land
    test_tier.py            the tier and light each staircase carries, and pairs agreeing a rung
    test_break.py           the LIT flag, breaking in
    test_flicker_fit.py     the flicker fit, its table and its phase lock
    test_handshake_gap.py   a POLLED Follower dropping a coarse sample its present cut short
    test_flicker.py         Dev Mode's room-flicker tool, against a synthetic light
    test_sampling.py        Dev Mode's SlotReceiver (measure.py): which samples decide a slot
    test_ring_verify.py     fastadc.Ring.verify() over a model of PSRAM, its cache and the DMA
    test_text_fit.py        every draw fits its box in the badge's real font (fontmetrics.py)
    test_recovery.py        framing-failure recovery, the empty payload, the retry budget after a demotion
    test_simulator_model.py the simulator's hardware model: readers, work costs, stalls, the v2 profile
    # Dev Mode's measurement engine (firmware/apps/share_wifi/measure.py)
    test_measure.py         schedule, calculations, mirror and 2nd-badge runs (averaging and the eye diagram too), the measurements file and its pages
    # Dev Mode's frame timing probe (firmware/apps/share_wifi/probe.py)
    test_probe.py           each step's figures against a simulated 60 Hz panel; what it changes, it restores
    # the share_wifi app (firmware/apps/share_wifi/__init__.py)
    test_app.py             menus, navigation, config file, credential validation, Dev Mode's screens
```

`simulator.py` is runnable on its own and prints how each scenario ends:

```
python3 tests/share_wifi/simulator.py                   # EXACT pair (both on the DMA ring), v2 timing
python3 tests/share_wifi/simulator.py --polled          # both on light_level()
python3 tests/share_wifi/simulator.py --mixed           # an EXACT Leader with a POLLED Follower
python3 tests/share_wifi/simulator.py --v3              # v3 timing; --slow-panel for a slow v2 panel
python3 tests/share_wifi/simulator.py --optics=mirror   # the sensor as measured: 16-count steps, its noise, an 80-count swing
python3 tests/share_wifi/sweep.py ring/v2/mirror polled/v2/lit47   # rows READERS/TIMING/OPTICS, 144 runs each
python3 tests/share_wifi/optics.py                      # how much of the sensor's view a screen fills; see optical-coupling.md
```

It delivers the payload in all six scenarios, and asserts the link invariants
from [../context/optical-link-invariants.md](../context/optical-link-invariants.md)
on every tick rather than at the end — a violation is reported with the time and
both badges' state, which is the difference between "ERR_LOST_PEER at 13.3s" and
"I2 broke at 6.1s".

`pattern_survey.py` is the other tool. It puts candidate control-pattern designs
through a static legality check and then through the real protocol, and prints
what happened:

```
python3 tests/share_wifi/pattern_survey.py            # the standard survey
python3 tests/share_wifi/pattern_survey.py --quick    # legality only, instant
python3 tests/share_wifi/pattern_survey.py --seeds 16
```

Neither is named `test_*`, so the runner does not pick them up: both take
minutes, and the suite is meant to stay fast.

Only `test_app.py` is built on the stubs; `test_module_boundary.py` and
`test_spec_matches_code.py` also load the app for a few checks of its names.
Everything else imports `protocol.py`, `reader.py`, `measure.py`, `probe.py` or
`flicker.py` directly; `test_fastadc_kernels.py` loads `fastadc.py` over
`fakeviper.py`. The tests sit
under `tests/share_wifi/` because that is where the modules live; the protocol
itself is not WiFi-specific.

Add tests for another app by making `tests/<app>/test_*.py`. The runner finds
them; nothing needs registering.

## Bugs this suite has actually caught

Worth knowing what it is good for:

- **The transmitter emitting one symbol per frame into a receiver decoding one
  symbol per slot**, so every pattern went out inside a single slot and no
  transfer could ever have completed. The codec tests passed throughout: they
  feed symbol lists straight into the receiver and never run the frame loop.
  `test_integration.py` exists because of this, and anything spanning the frame
  loop belongs there rather than in the codec tests.
- A slot ladder that stepped the wrong way, so "slow down" sped up.
- The End Block Pattern's first two symbols being eaten as payload bits, because
  they are ordinary data symbols and only its third symbol is black.
- **Nothing at all about the drawing API.** Several badge crashes came from
  stubs that modelled the documentation rather than the firmware: `**kwargs`
  unpacking, `State.load()`'s return value, and the alignment constants. Each
  passed the whole suite and died on the first frame. `StrictStub` and the
  `alignment=` / `state_returns_flag=` switches close those; the lesson
  generalises further than the fixes do.
- **A stub can be wrong in the opposite direction too, and then it hides a fix
  rather than a bug.** This harness used to put the injected globals in the
  app's module namespace, so `globals().get("CENTER")` worked here and could
  never work on the badge. The app's alignment path was unreachable for as long
  as that held, and every test agreed it was fine.
- **What the app transmits, as opposed to what the optical simulator assumes it
  transmits.** `tests/share_wifi/simulator.py` once modelled the middle field at 45% of
  the panel's swing; the app sent `color.grey`, which is 7% of it, so every
  transfer on hardware would have failed calibration while all six scenarios
  passed. A number shared between an app and a model of that app needs an
  assertion joining them, not a matching comment.
- A CRC variant that looked right and was not -- caught by the published check
  value for `"123456789"`, which is the only cheap way to tell the CRC-16
  variants apart.
