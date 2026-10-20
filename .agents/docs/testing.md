# Tests

```
python3 tests/run_tests.py              # everything
python3 tests/run_tests.py share_wifi   # one app
python3 tests/run_tests.py -v           # list every test
```

A few tests need tools the suite does not bring, and are **skipped** without
them -- counted in the summary line (`... N skipped`) and listed under it
with what to set, among them:

- `MPY_CROSS`: `test_fastadc_kernels.test_fastadc_compiles_for_the_badge`.
- `MICROPYTHON`:
  - `test_fastadc_kernels.test_the_kernels_under_real_viper`;
  - `test_devlink.test_frames_cross_between_cpython_and_micropython`;
  - `test_devserver.test_the_server_under_micropython`;
  - `test_usbport.test_cancel_term_under_micropython`.

```
MPY_CROSS=/path/to/mpy-cross \
MICROPYTHON=/path/to/micropython \
python3 tests/run_tests.py
```

`MPY_CROSS` is MicroPython's cross-compiler, which compiles `fastadc.py`'s
viper for the badge's architecture (`-march=armv7emsp`: this mpy-cross has no
armv8m); `MICROPYTHON` is the unix port, whose x64 viper runs the ring
kernels for real, and which runs the dev link's pure modules as MicroPython
does. Either is also found on the `PATH`. A run without them has
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
constants are right. The optical tests feed a synthetic sensor -- they show
the maths is sound, not that it survives a real room. `test_measure.py` and
`test_devtools.py` are the same: the measurement subtools are run on
simulated rings (`ring_optics.py`) whose
coupling, lag and noise are whatever the test told it, so it shows the
engine recovers what it was given, not what the real sensor does.

`ring_optics.py` puts badges on DMA rings in virtual time: each badge's
ring (`held.ListRing`, read through the production `RingReader`) fills as
the clock moves with what reaches its sensor -- the room plus every
backlight times its coupling, behind a first-order lag, in 16-count steps.
Time moves only when every badge is waiting (in `pump()`, or between
frames), and one badge runs at a time (the protocol keeps module-level
scratch buffers), so two badges on two threads run a 2nd-badge condition
in lockstep, as on the hardware, and the results are deterministic.
`Optics(tick_us=...)` makes each clock read cost time, for code that times
its own work (the probe's ring step).

## Layout

```
tests/
  harness.py            the stubs and the loader, shared by every app
  fontmetrics.py        real glyph widths from romfs/fonts/*.ppf, and the firmware's wrapping
  run_tests.py          discovery and runner
  usbrun.py             drives a plugged-in badge over its REPL with mpremote: reset,
                        upload to /runs/, launch with DEV_AUTORUN; a tool
  share_wifi/
    autorun_host.py         the dev link with no hands: opened by the app, the
                            subtools synced, commands run; a tool (see "Fresh launches over USB")
    held.py                 ListRing, HeldReader and HeldTransfer, whose step() feeds a
                            transfer one reading at a time, and run_pair(), two of them
                            facing with the Leader's bursts played; not a test
    simulator.py            two badges on one timeline, through the production readers; not a test, a tool
    sweep.py                the simulator over 8 seeds x 6 scenarios x 3 payloads, row by row; a tool
    fakeviper.py            stand-ins for viper's ptr16/ptr8 and machine/rp2, to load fastadc.py under CPython
    # Optical Data Transfer Protocol (firmware/apps/share_wifi/protocol.py)
    test_payload.py         the fast stream: fields of any number and length, blocks by chunk, pairs
    test_optical.py         beacon detection, calibration, the slot ladder
    test_integration.py     two badges driven frame by frame, from the data phase
    test_lifecycle.py       the whole protocol, first beacon to last pattern
    test_no_dead_ends.py    every state terminates against an uncooperative peer
    test_patterns.py        the control patterns obey the rules receivers rely on
    test_module_boundary.py protocol.py stays free of badge globals and of WiFi
    test_spec_matches_code.py  the protocol and app specs' tables agree with the code
    test_schemes.py         the levels: backlight values only, the beacons' table and the data's
    test_reader.py          reader.py: the ring reader's clock, index and queries over a list ring, and held.py's HeldReader
    test_fastadc_kernels.py the viper kernels against their pure twins; mpy-cross and the unix port's viper when found
    test_slot_window.py     where a slot is decided, which slot is decided next, re-bases and demotions
    test_reply_gap.py       the one silent slot before a reply, a message held up whole
    test_calibration.py     the calibration's lengths, fit windows, where the stalls land, HeldTransfer.step() against the app's reading
    test_handshake.py       the short handshake: the Announce, the Follower's staircase and its light flag, the Leader reading it while announcing, training on the Begin, refusals, the hold and the grace
    test_break.py           the LIT flag, the flicker learner, the Leader's lead-in watch
    test_flicker_fit.py     the flicker fit, its table and its phase lock
    test_flicker.py         the room-flicker library (devtests/lib/flicker.py), against a synthetic light
    test_sampling.py        end to end: no first block rejected for start skew; the Leader keeps its own grid
    test_ring_verify.py     fastadc.Ring.verify() over a model of PSRAM, its cache and the DMA
    test_flicker_cost.py    FlickerFilter.amounts(), the hoisted correction, bit for bit the per-part one
    test_text_fit.py        every draw fits its box in the badge's real font (fontmetrics.py)
    test_recovery.py        the empty payload, the NAK budget after a demotion, ring faults, the rate check
    test_complete_ack.py    the Leader hears the Complete Ack less its first symbol
    test_steady_window.py   the flicker fit skipped in a steady room
    test_flicker_frames.py  the flicker filter's per-sample calls allocate nothing; under MicroPython when found
    test_fast_transfer.py   fast blocks in a transfer: the stream, chunks, FAST_BEGIN and its rung, the decoder's flicker, missed Begins, pairs in the simulator on every rung and past F3
    # The fast block (fastblock.py, fasttx.py)
    test_fastblock.py       the encoder and decoder, the real mirror captures, the virtual mirror; under real viper when found
    fastmodel.py            the measured channel as a model, a PAM burst through it, the floating-point reference receiver,
                            the mirror captures' reader; test_fastblock's, not a test
    test_fasttx.py          fasttx.Pacer: what it writes to the PWM slice and the DMA, and what it puts back
    test_simulator_model.py the simulator's hardware model: readers, work costs, stalls, the v2 profile
    # the measurement libraries (devtests/lib/measure.py, probe.py)
    test_measure.py         the tests on a ring (levels, noise, curve, PWM, M4, M5), their timelines, mirror and 2nd-badge runs and the handshake
    ring_optics.py          badges on DMA rings in virtual time, in lockstep on threads; not a test
    test_probe.py           each step's figures against a simulated 60 Hz panel; what it changes, it restores; the ring step
    test_devtools.py        the measurement subtools as the link runs them, on rings: mirror, two badges, room flicker, the probe
    # the share_wifi app (firmware/apps/share_wifi/__init__.py)
    test_app.py             menus, navigation, config file, credential validation, Dev Mode's menu, display and clear
    test_devmode_app.py     holding C, the link's screen, B, Ctrl-C off and on, the built-ins, api.save() into the file, autorun
    # the USB dev link (devlink.py, devserver.py, usbport.py) and its host program
    devlink_fakes.py        a fake port, the badge's hooks, a minimal host; not a test
    devlink_host.py         the host program itself (not a test): see "The USB dev link" below
    devtests/               every subtool the badge runs: mirror.py, second_badge.py,
                            frame_probe.py, room_flicker.py, fast_loopback.py,
                            files.py; reset.py, which ends a link a host opened
      lib/                  their libraries, uploaded with `new --lib` or `sync`:
                            measure.py, probe.py, flicker.py, fastloop.py, toolkit.py
    devlink_examples.py     the link's example subtools, hello, spin, ring_trace, as sources: the e2e tests
                            upload them, and `python3 devlink_examples.py DIR` writes them out for the checklist
    fast_loopback_host.py   fast_loopback's saved run, printed and judged (a tool)
    optic_fake.py           a virtual badge on a mirror: PWM slice, DMA, ring, the simulator's sensor
    test_fast_loopback.py   fast_loopback on optic_fake's virtual mirror, through the real devserver and host; one test prints FAIL lines on purpose (the host checker on a broken record)
    test_devlink.py         the wire format; frames crossed with the MicroPython unix port when found
    test_devserver.py       sessions, list, new (libraries too), runs and their cleanup order, the ring interlock, cancels, a lost host, api.save; also under MicroPython
    test_usbport.py         the port and the hard cancel against a fake of the firmware's dupterm contract; CancelTerm under MicroPython
    test_devlink_host.py    the host: rid pairing, saving and naming, a hostile name, Ctrl-C, unplug, the shell
    test_devlink_e2e.py     host Client and badge Server through an in-memory pipe: the example subtools, files, a sync of the whole tree
```

`simulator.py` is runnable on its own and prints how each scenario ends:

```
python3 tests/share_wifi/simulator.py                   # both badges on the DMA ring, v2's PWM and backlight curve
python3 tests/share_wifi/simulator.py --v3              # v3's PWM period and backlight curve
python3 tests/share_wifi/simulator.py --optics=mirror   # the sensor as measured: 16-count steps, its noise, an 80-count swing
python3 tests/share_wifi/sweep.py v2/mirror v2v3/lit47  # rows TIMING/OPTICS, 144 runs each
PROFILE=v2 python3 tests/share_wifi/sweep.py v2/lit47   # flicker calls priced as measured before amounts(); default "frameless"
```

It delivers the payload in all six scenarios, and asserts the link invariants
from [../optical_data_transfer/invariants.md](../optical_data_transfer/invariants.md)
on every tick rather than at the end — a violation is reported with the time and
both badges' state, which is the difference between "ERR_LOST_PEER at 13.3s" and
"I2 broke at 6.1s".

Neither is named `test_*`, so the runner does not pick them up: a sweep
takes minutes, and the suite is meant to stay fast. A change to the link is
checked by the 24-row standard set at 8 seeds and the twelve hard rows at
72, against the figures in section 11 of
[../optical_data_transfer/data.md](../optical_data_transfer/data.md) (the
row lists are there too):

```
python3 tests/share_wifi/sweep.py v2/mirror v2/lit v2/lit30 v2/lit47 v2/aligned v2/overlap v2/led60 v2/led50 \
    v2/half60 v2/half50 v2/k2.0 v2/k3.0 v2/self5 v2/worst v3/mirror v3/lit47 v2/gc70 v2/litgc70 \
    v2/lit47gc70 v2/half60gc70 v2v3/mirror v2v3/lit47 v3v2/lit v3v2/lit47          # ~2 min on 16 workers
SEED0=1 SEED1=73 python3 tests/share_wifi/sweep.py v2/half50 v2/half60 v2/half60gc70 v2/lit47gc70 \
    v2/k3.0 v2/worst v2/mirror v2/lit v2/led60 v2/led50 v2/lit47 v3/lit47          # ~10 min
```

Only `test_app.py` and `test_devmode_app.py` are built on the stubs (and
`test_devtools.py` borrows its globals); `test_module_boundary.py` and
`test_spec_matches_code.py` also load the app for a few checks of its names.
Everything else imports `protocol.py`, `reader.py` or a library in
`devtests/lib/` directly -- `harness.load_module()` finds both; `test_fastadc_kernels.py` loads `fastadc.py` over
`fakeviper.py`. The tests sit
under `tests/share_wifi/` because that is where the modules live; the protocol
itself is not WiFi-specific.

Add tests for another app by making `tests/<app>/test_*.py`. The runner finds
them; nothing needs registering.

## Fresh launches over USB: `usbrun.py`, `autorun_host.py`

`tests/usbrun.py` drives a badge over its USB REPL with mpremote, the
badge plugged in with its menu running: reset it, upload this tree's
share_wifi to `/runs/` (`/system` is read-only to MicroPython), and launch
it with `builtins.DEV_AUTORUN = "link"` ("Autorun over the REPL" in
`.agents/share_wifi/app-spec.md`). `autorun_host.py` uses it:

```bash
# the dev link, opened by the app itself; every subtool and library synced;
# each command; then a reset back to the menu
sg dialout -c "python3 tests/share_wifi/autorun_host.py OUTDIR 'run mirror dark' 'run frame_probe'"
```

Nothing over USB changes the light or the mirror: `run mirror dark` measures
whatever light the badge is in. The dev link takes the USB serial with
Ctrl-C off, so the host ends it with the `reset` subtool
(`devtests/reset.py`); the app compiles from source first, so the host
polls `list` until the link answers. `--no-sync` skips the upload of the
subtools, `--no-upload` that of the app.

## The USB dev link

Dev Mode's last item, `USB Dev Link`, lets a computer drive the badge over
the USB cable: list and run its subtools, upload them and their libraries to
`/devtests/`, and save whatever they measure. Since 2026-10-18 it is the
only way to measure: every tool is a subtool. The protocol is "USB dev link"
in [.agents/share_wifi/app-spec.md](../share_wifi/app-spec.md).

**Collections over the link.** A session's modules add ~20 KB and ~1 ms to a
collection [measured-v2, 2026-10-09]; the 67-81 ms collections once blamed
on sessions were the heap's extent -- wherever the app loaded -- explained
on 2026-10-13 (section 2 of
[data.md](../optical_data_transfer/data.md)). A subtool's figures
carry the link's heap and a library's; compare runs made the same way.

**Setup, once:**

```
sudo apt install python3-serial      # Debian/Ubuntu
```

Or, in a virtual environment, `pip install pyserial`. Outside one, PEP 668
makes pip refuse.

On Linux the port is usually `/dev/ttyACM0` and needs the `dialout` group
(`sudo usermod -aG dialout $USER`, then log in again); "Permission denied"
means that is missing.

**Use:**

1. On the badge: share_wifi, hold C on the main screen for 5 s, Dev Mode,
   `USB Dev Link` -- or `autorun_host.py`, with no hands.
2. On the computer, with the badge plugged in:

   ```
   python3 tests/share_wifi/devlink_host.py OUTDIR            # finds the badge by USB 2e8a:1101
   python3 tests/share_wifi/devlink_host.py OUTDIR --port /dev/ttyACM0
   ```

   Or give one command after OUTDIR to run it and exit, for scripts:

   ```
   python3 tests/share_wifi/devlink_host.py OUTDIR sync
   python3 tests/share_wifi/devlink_host.py OUTDIR run frame_probe
   python3 tests/share_wifi/devlink_host.py OUTDIR --yes remove spin hello
   ```

   A scripted command takes the same arguments and saves files the same way
   as at the prompt. Its exit status is:

   | Status | Means |
   |---|---|
   | 0 | it did what was asked |
   | 1 | it failed, was cancelled (Ctrl-C during a run included), was not confirmed, or a name in `remove` missed |
   | 2 | bad arguments: an unknown option or command, or `run`/`new`/`remove` with nothing named |
   | 3 | no badge, or no link |
   | 130 | interrupted outside a run, connecting included |

   Ctrl-C during a scripted run cancels the tool, as in the shell, and exits
   1. With no `--yes`, a confirmation is asked on a terminal and counts as no
   when stdin is not one. `--yes` never answers the host's own "send Ctrl-C
   to a run left going?", which counts as no when stdin is not a terminal.

3. At `badge> `:

| Command | Does |
|---|---|
| `list` | the badge's tools: the two built-ins (`measurements`, `clear`), the subtools in `/devtests/`, and the libraries |
| `local` | the subtools in `--tools DIR` (default `tests/share_wifi/devtests/`) |
| `run <tool> [args]` | runs one |
| `new <name or path> [--force] [--lib]` | uploads a subtool, or with `--lib` a library from `DIR/lib`; `--force` replaces one |
| `sync` | uploads every library in `DIR/lib` and every subtool in `DIR`, replacing what is there |
| `remove names...` | deletes subtools or libraries after a y/N (`--yes` skips it); a name that is neither is an error, and the rest still go |
| `cancel` | cancels a run the host stopped waiting for |
| `reconnect` | after an unplug or reset |
| `quit` / Ctrl-D | leaves |

Every file the badge sends is saved as
`OUTDIR/<subtest>.<YYYYmmdd-HHMMSS>.<ext>`, with `-2`, `-3` and so on added
on a clash. It is written as `.part` and renamed only when its size and CRC
check out. A `.part` left behind is incomplete, and the shell says why.

**Interrupts:**

- Ctrl-C once during a run cancels it. Twice stops waiting.
- The cancel also carries a raw 0x03 (Ctrl-C), which stops even a subtool
  that never polls.
- After abandoning a run, the host asks before sending another 0x03,
  and only if the badge stops answering: with the link closed, Ctrl-C would
  stop the app.
- Ctrl-C at the prompt clears the line.
- While the link is open, mpremote and MicroPico cannot get in; B on the
  badge closes the link.

**A subtool** is a file with `run(api, args)`. See
`tests/share_wifi/devtests/` and the `Api` table in the spec. It must not
write USB or allocate much while it measures, and the ring refuses both. A
library in `devtests/lib/` is imported by name while a subtool runs, and
dropped after.

### Hardware checklist

On 2026-10-08 the user ran this list on the v2 badge (`BW-1.27.0`) from a
Linux host, against the link as it then was -- with seven built-ins that
ran the app's own screens. Every step passed **[verified-v2]**, except two.
**Since 2026-10-18 the steps below are rewritten for the link as it now is
and none has run on it [unverified]**:

- step 8 (unplug) has not been run on its own **[unverified]**. It is
  covered in part by step 10's cable pull, which exercised the same VBUS check, lost-host path and host `.part`; the custom-run path itself (`api.cancelled()` seeing VBUS gone) has not been run;
- step 13 (errata) has not been checked.

**On this build (2026-10-19, v2, tethered)** [verified-v2]: step 1 (two
built-ins), step 2's `sync` (five libraries, seven subtools, all loading)
and uploads by path, step 3 (`frame_probe`, `mirror dark`, `measurements`
holding every tool's result), step 5 (`ring_trace 300`: 15,000 readings,
`health` 0), step 10's `files list /`, step 14 in part (`remove` of three by
name, no error case) and step 15's scripted runs exiting 0. Not yet on this build: the
hard cancel (step 4: a host Ctrl-C sent through `sg` was inconclusive --
run it at the interactive host), steps 7-9, 11-13. Steps 15-16 were new
on 2026-10-09.

Step 10 (files) passed on 2026-10-09 **[verified-v2]**, every sub-step,
including the cable pull mid-send, which left a `.part`.

The hard cancel (step 4) works. In step 6, tethered and battery mirror
figures agreed within its threshold **[measured-v2]**.

On the v2 badge, from a Linux host. Steps 2, 4, 5, 8, 9 and 14 use the
link's example subtools: write them out first with `python3
tests/share_wifi/devlink_examples.py EX` and upload each with `new
EX/hello.py` (and `spin.py`, `ring_trace.py`).

1. **Connect.** Open the link and start the host. `hello` shows `BW-1.27.0`
   and `tethered`, and `list` shows two built-ins.
2. **Upload.**
   - `sync`: every library and subtool saved; `list` shows them, none
     failed to load, and the libraries.
   - `new` a file with a syntax error: it is refused with its line.
   - `new hello --force`, then `list`: the replace worked.
3. **Measure.** `run frame_probe`, and compare with the last saved probe.
   `run mirror dark` on the mirror. `run measurements`: both are in the
   file, and *Display Saved Measurements* on the badge shows them.
4. **Hard cancel.**
   - `run spin` (it never polls). Press B: `cancelled (by B)` within a
     second, and the link screen returns.
   - Run it again and press Ctrl-C in the host: `cancelled (by host)`.

   This checks B's polarity, dupterm from a soft IRQ on v2, and 0x03 while
   armed. If B does nothing, report it: the run then only ends on HOME.
5. **The ring.** `run ring_trace 300`: a CSV of 15,000 readings, with
   `health` 0 in its JSON.
6. **Tethered against untethered** [measured-v2, 2026-10-08, the old link]:
   the swing agreed within the 2-10 count noise; not to be repeated unless a
   tethered figure looks odd.
7. **Cancel mid-measure.** During `run mirror dark`, Ctrl-C in the host
   gives `cancelled (by host)`, nothing saved, and the badge returns to the
   link screen with its panel back.
8. **Unplug** **[unverified]**. Pull the cable during `run ring_trace`. The
   host keeps a `.part` and says disconnected. Replug, `reconnect`, `list`.
9. **Restart the host mid-run.** `run spin`, Ctrl-C twice, then `list`. The
   plain `hello` gets no reply, and the host asks before sending Ctrl-C.
   Answer y: `spin` is cancelled and `list` answers.
10. **Files.** `run files list /` lists `system/` among the rest. `run files
    get /system/apps/share_wifi/icon.png /state/measurements.json`: OUTDIR
    gets `files_icon.<time>.png`, which opens, and the measurements.
11. **mpremote.** With the link open, `mpremote` hangs and the app survives.
    B closes the link, and mpremote can then get in.
12. **Leaving.** B on the idle link screen returns to Dev Mode. HOME from the
    link resets as usual.
13. **Errata.** Check the RP2350 datasheet's errata for USB device items
    **[unverified]**.
14. **Remove by name.** `remove hello nope toolkit`. Answer n: nothing
    changes. Again, answering y: `removed: hello, toolkit`, plus an error
    for `nope` (no such subtool); then `sync` puts the library back.
15. **Scripted.**
    - `python3 tests/share_wifi/devlink_host.py OUTDIR run frame_probe`: it
      runs, saves `frame_probe.<time>.json` in OUTDIR, and exits 0.
    - `... OUTDIR run nope` exits 1.
    - With the badge unplugged, `... OUTDIR list` exits 3.

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
  transmits.** `tests/share_wifi/simulator.py` once modelled a middle level
  six times brighter than the one the app sent, so every transfer on hardware
  would have failed calibration while all six scenarios passed. A number shared between an app and a model of that app needs an
  assertion joining them, not a matching comment.
- A CRC variant that looked right and was not -- caught by the published check
  value for `"123456789"`, which is the only cheap way to tell the CRC-16
  variants apart.
