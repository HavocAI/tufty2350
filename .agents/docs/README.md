# Badge documentation

Notes taken while building `share_wifi` and `wifi_scanner` (now in
[../../firmware/apps/](../../firmware/apps/)), from four sources: the official docs at
<https://badgewa.re/docs>, the firmware source at
<https://github.com/pimoroni/tufty2350> — which this repo is a fork of, so
[../../modules/](../../modules/) is a local copy — the simulator at
<https://try.badgewa.re> — the same MicroPython build and the same `badgeware/`
modules, so an API question can be answered in a minute — and what the hardware
actually did.

**The firmware source is the tiebreaker.** It has already settled several things
the official docs got wrong or left out: which names are actually globals, the panel's
frame rate, whether the light sensor drives the backlight, what `State.load()`
returns, and why WEP cannot be joined.

It has also settled things *these notes* got wrong, which is the more useful
lesson. Every entry below marked **[source-v3]** that begins "an earlier version
said" was a claim written down confidently, believed for a while, and then
disproved by reading the source or running the code. Prefer checking to
reasoning: the simulator answers most API questions in a minute.

## Which file

| Question | File |
|---|---|
| How do I write this? What does working code look like? | [badgeware-patterns.md](badgeware-patterns.md) |
| What is this call, exactly? | [badgeware-api.md](badgeware-api.md) for v3; **[badgeware-api-v2.md](badgeware-api-v2.md) for the badge on the desk** -- every v2 call, read from the v2.0.2 source |
| How do I draw, animate, transform? | [badgeware-graphics.md](badgeware-graphics.md) |
| What can the Tufty 2350 physically do? | [badgeware-hardware.md](badgeware-hardware.md) |
| What did the badge actually measure? | [../optical_data_transfer/data.md](../optical_data_transfer/data.md): every measurement and sweep the optical link rests on -- the only record; the raw files were deleted on 2026-10-19 |
| How do two badges talk over the screen and light sensor? | [../optical_data_transfer/spec.md](../optical_data_transfer/spec.md), **the authority**; why each value is what it is, [context.md](../optical_data_transfer/context.md); how to check an implementation, [tests.md](../optical_data_transfer/tests.md). The three link to nothing else, so they stand alone |
| How much light reaches the sensor, and what would a second badge give? | [../optical_data_transfer/optical-coupling.md](../optical_data_transfer/optical-coupling.md) |
| New to share_wifi and the optical link? Where does it all stand? | [../share_wifi/state.md](../share_wifi/state.md): the briefing for a new agent |
| What does `share_wifi` do on top of that protocol? | [../share_wifi/app-spec.md](../share_wifi/app-spec.md) |
| How do I test an app without a badge? | [testing.md](testing.md) |
| How do I measure on the badge (the USB dev link, its host program, the measurement subtools)? | "USB dev link" in [../share_wifi/app-spec.md](../share_wifi/app-spec.md); the how-to and hardware checklist in [testing.md](testing.md); the CDC facts under "USB serial" in [badgeware-hardware.md](badgeware-hardware.md) |
| What is being worked on right now? | [../tasks/README.md](../tasks/README.md) |

**Start with the patterns file.** The others are reference: accurate, but they
will not tell you which of two documented approaches to pick, or which
documented thing is wrong on this firmware.

## How sure is any of this

Nothing here was tested on the physical badge by the agent that wrote it, so
the distinction matters. Some of it *has* been run: the Badgeware simulator at
<https://try.badgewa.re> is the same MicroPython build and the same
`badgeware/` modules as the firmware, minus `network`, and it will answer an
API question in a minute where a hardware round trip takes an afternoon. It
says nothing about the panel, the light sensor or real frame timing. Claims are
marked:

**Every marker names the firmware it holds for**, because the badge on the
desk runs v2.0.2 and this repo's source and the emulator are v3 -- see the
first table in [../../AGENTS.md](../../AGENTS.md) for what differs.

| Marker | Means |
|---|---|
| *unmarked* | Quoted or paraphrased from the official docs, which describe v3 |
| **[measured-v2]** | A number measured on the badge running v2 |
| **[verified-v2]** | Seen to happen, or not, on the badge running v2 |
| **[measured-v3]**, **[verified-v3]** | The same on hardware running v3. Nothing carries these yet |
| **[measured-all]**, **[verified-all]** | Measured or seen on the v2 badge, but a physical property -- the sensor, the panel, the light between them -- so it holds whatever the firmware |
| **[source-v3]** | Read out of this repo's firmware source, which is v3 |
| **[source-v2]** | Read out of this repo's git history as it stood at v2.0.2 (2026-04-10) |
| **[emulator-v3]** | Run on the emulator, which is v3 |
| **[inferred-v2]**, **[inferred-v3]**, **[inferred-all]** | Reasoned from other facts, not stated anywhere, for that firmware or for any |
| **[unmeasured]** | A number nobody has measured on either firmware; any value given is a starting point. **[unmeasured-v3]** is one measured on v2 only |
| **[unverified]** | Not known for either firmware. **[unverified-v3]** is known on v2 only |
| **[datasheet]** | From a component's manufacturer datasheet, cited -- hardware, so any firmware; trustworthy, but read the test conditions |

A claim checked on more than one carries each marker: **[source-v3]**
**[verified-v2]** is read in the v3 source and seen on the v2 badge.

Things already checked against the firmware source **[source-v3]**, and in
conflict with the official docs:

- **`wifi` is not an injected global**, it is a module you import. The docs use
  it bare. Getting this wrong is a `NameError` at launch.
- **The panel's frame rate depends on the firmware: 60Hz on v2, 90Hz on
  v3.** **[measured-v2]** on a v2 badge, which has no `set_framerate`. LORES
  writes the same bytes to the panel, but **does** make drawing faster: on v2
  a HIRES full-screen fill is 11.4 ms, and `run()` does one every frame. See
  "What the hardware runs at" in [badgeware-hardware.md](badgeware-hardware.md).
- **Nothing reads the light sensor to drive the backlight.** The docs' phrasing
  implies an auto-brightness loop; there isn't one, and `display.backlight()`
  gives you manual control the docs never mention.
- **Alignment constants exist under both spellings on v3, and not at all on
  the badge's v2** — on v3 `image.CENTER` and bare `CENTER` are the same object,
  because the firmware hoists each one into `builtins` (since 2026-07-29).
  **[source-v3]** But **`globals()` cannot see any injected global**, so a probe
  written that way reads "absent" on a badge that has it. Name the constant and
  catch `NameError`.
- `font.smart` works on v3 and `rom_font` is not a real name. **[emulator-v3]**

**This repo's source and the emulator are v3; the badge runs v2.0.2.** What
differs, and which claims were measured on which, is the first table in
[../../AGENTS.md](../../AGENTS.md).
- `badge.ticks` does not advance between `update()` calls, which the docs state
  but which is easy to read past. It caused a real bug.
- **`WLAN.scan()`'s security field is a bitmask**, not the 0-5 auth enum. WPA2
  reads as `0x00400004`. `wifi_scanner` still assumes 0-5 and mislabels almost
  every network. **[source-v3]**
- **The light sensor does not see the whole screen.** It sits ~1mm outside the
  right edge at mid height and reads a patch near itself. Two badges facing each
  other are mirrored, so right meets left. **[verified-all]**
- **WEP cannot be joined.** The cyw43 driver rejects the auth type before
  touching the radio and has no code to install a WEP key. **[source-v3]**
- **`light_level()` is 12-bit in a 16-bit frame** — every reading is a multiple
  of 16. The panel's full swing measured five of those steps against two of
  noise, which is not enough for three-level signalling.
  **[measured-all]**, see section 3 of
  [../optical_data_transfer/data.md](../optical_data_transfer/data.md).

## Keeping these honest

If you learn something these files do not cover, or that contradicts them,
update them in the same change. If an `[unmeasured]` value gets measured,
replace it and give it the marker for the firmware it was measured on. When
the badge moves to v3, a `-v2` marker is not evidence about it: re-check, and
mark what was re-checked `-v3`. An out-of-date reference is worse than none, because the
next agent will trust it.
