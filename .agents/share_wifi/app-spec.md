# share_wifi

Shares Wi-Fi credentials between two badges over the screen and the light
sensor. The wire protocol -- light levels, framing, calibration, timing -- is
specified in [.agents/optical_data_transfer/spec.md](../optical_data_transfer/spec.md),
with its evidence in [data.md](../optical_data_transfer/data.md);
this file is everything the app adds on top of it.

Implemented in [firmware/apps/share_wifi/__init__.py](../../firmware/apps/share_wifi/__init__.py).
Confidence markers follow [README.md](../docs/README.md).

---

## Roles

- **Share** takes the protocol's Leader role and **Receive** the Follower role.
  The menu choice fixes it before any light is exchanged.

## The transfer screen

**The whole screen is white, drawn once, and every symbol is the backlight**
-- section 5 of the protocol spec is the authority. No
instructions, no progress, no light reading, no `B - Cancel`; B still
cancels.

- `begin_transfer()` calls `begin_transfer_screen()`: `badge.default_clear =
  None`, so `run()` no longer clears the screen, the backlight off -- the
  beacons' black -- and the whole screen filled white and presented, at the
  app's own `HIRES | VSYNC`: no mode switch. It then takes the launch's
  reader (`ring_reader()`: the DMA ring, made and verified on the first
  transfer of a launch in about 0.4 s), collects, starts the reader and
  starts a `Transfer(leader, payload, reader, fast)`, `fast` the fast
  block's module (`fast_for()`).
- **A badge without the ring does not transfer.** If the ring cannot be made
  or verified, `begin_transfer()` puts the panel back and goes to the result
  screen with `NO_RING_TEXT` -- "This badge can't read its light sensor fast
  enough to transfer." -- and `ring_note()`'s reason under it. There is no
  slower fallback: every firmware the app supports, v2.0.2 and later, has
  what the ring needs **[verified-v2]**, and the ring is tried once a
  launch, so a retry fails the same way until the app is relaunched.
- **A badge without fast blocks does not transfer either** (since
  2026-10-18): with no `fastblock` module, or as Leader no pacer
  (`fasttx`), `begin_transfer()` shows `NO_FAST_TEXT` -- "This badge
  can't send fast blocks." -- with no retry. The payload goes only in
  fast blocks.
- **`do_transfer()` is a loop, not a frame.** Each pass collects if the
  protocol asked (`collect_due`) -- never inside its calls -- then
  `reader.pump(transfer.due_us())`, which waits, the ring reading
  meanwhile, until the protocol has something due, then shows
  `field_at(now)` -- `show_level()`: `LEVEL_SCHEMES[transfer.scheme][field]`
  set with `display.backlight()` only if it changed -- and only then calls
  `work(now)`, so a change lands on time. Nothing is ever presented, so the
  loop does not return to `run()` until the transfer ends. The buttons are
  polled every `POLL_MS` (50) and B is checked; the case lights are updated
  then too. `TRANSFER_PASSES` caps the passes one `update()` may take: 0 on
  the badge, no cap; the app's tests set 1. See section 6 of the protocol spec.
- **`on_exit()`** stops the reader -- and with it the ring's DMA channel and
  the ADC's free-running -- however the app is left, HOME included.
- `finish_transfer()`, the single exit, stops the reader and calls
  `restore_panel()`: `default_clear` back to black, the backlight
  back to 230/255, the case lights off.

> **Why no text.** Both screens face away from their users for the whole
> transfer, and nothing needs drawing: the levels are the backlight's, so
> once the white is up nothing is drawn at all.

- Progress and errors go to the rear case lights, `badge.caselights()`:

      slow pulse   searching for a peer
      steady on    transfer in progress
      fast blink   error, look at the screen

> Both screens face away from their users for the whole transfer, so the case
> lights are the only output either person can see while the badges are held
> up. At the transfer times under section 11 of `data.md` they are not
> optional. The result screen is what you read after separating them.

> The backlight is manual PWM, the firmware leaves it at 230/255, and nothing
> else touches it while an app runs -- so the app owns it for the transfer and
> owes the restore.

## Signalling

- Every symbol is a backlight level from `protocol.LEVEL_SCHEMES`:
  `SCHEME_BEACON` for the beacons -- off and full -- and `SCHEME_BACKLIGHT`
  from a badge's staircase on, its low the dim 0.5. `transfer.scheme` says
  which; the white pen, `WHITE_PEN`, is made once at import.

## The payload

`share_wifi` sends three fields, built by `credential_fields()`, and imposes
802.11's rules on them. Those rules are **not** part of the protocol — they live
in `valid_credential()`.

| Field | Size | Notes |
|---|---|---|
| SSID | 1–32 bytes | raw 802.11 octets, **not** required to be UTF-8 |
| Key | by security type -- see "Security types" | passphrase, or hex PSK; must decode as UTF-8 |
| Security | 1 byte | a code from "Security types" |

A two-field payload -- SSID and key -- is what a badge sent before the security
field existed, and is still accepted, its type inferred. A payload that is not
two or three fields, or whose fields break those rules, is rejected by the app
with `ERR_DECODE`. A different application would impose different rules, or
none, without touching the protocol.

- The protocol delivers an **empty payload** (`[]`) as one block holding a
  stream of no fields, and arrives as `[]`. `share_wifi` never sends one -- a
  credential always has three fields -- and would reject one received as
  `ERR_DECODE`.

## Errors

- `ERR_DECODE` is also the app's verdict on a payload that arrived intact but is
  not a valid credential.
- `ERR_CANCELLED` is B, checked every frame of a transfer.

---


## App shell

- The app folder needs `icon.png` beside `__init__.py` or it will not appear in the
  launcher.
- `badge.mode(HIRES | VSYNC)` at import, for every screen with text. VSYNC matters
  beyond tearing here: it makes frame presentation regular, and every transmitted
  symbol is one or more whole frames.
- **A transfer makes no mode switch**: it runs at the app's `HIRES |
  VSYNC`, `run()`'s clear off, behind a white screen (see "The transfer
  screen"). `badge.mode()` rebinds `screen`, so nothing keeps a reference
  to it; every draw reads the global.
- Do not set `DITHER`: the white behind a transfer is one flat colour, and dithering
  would dim the light the peer measures.
- Drive the app with `run(update)`, which clears, calls `update()`, presents and polls
  the buttons — so `update()` must not call `badge.update()`.
- Resolve ROM fonts defensively and fall back to the default rather than raising.
- Menu screens fill by setting `screen.pen` and then `screen.clear()`, in that
  order. `clear()` fills with the current pen.
- A transmitted symbol is `show_level()`: a backlight value, set only when it
  changes, over the white `begin_transfer_screen()` drew once.
- Geometry comes from `screen.width` / `screen.height`, never hardcoded.
- Centre and truncate text through `draw_text()`, never `align=` or `overflow=`
  directly. The alignment constants **do** exist here, but they cannot be probed
  with `globals()` — an injected global lives in `builtins` — so `draw_text()`
  names them once inside a `try`/`except NameError` and keeps a measure-and-place
  path for a firmware without them. It never uses `overflow=`: `ELLIPSES`
  truncates by word, which reduces a long hyphenated SSID to `"..."`. See the API
  notes.
- **The app breaks text into lines itself** (`layout_text()`), and hands the
  firmware lines that already fit. `screen.text(text, rect)` wraps and *clips*
  to the rect, so a message too long for it simply loses its end -- and the
  badge's firmware may not wrap at all, which drew the measurement
  instructions as single lines cut off at the edge. Lines break at spaces;
  runs of spaces are kept, since the help lines use them as gaps; a word wider
  than the box on its own (an SSID with no spaces) breaks between characters.
- **A space counts at the wider of its two widths**: `measure_text()` gives one
  a third of the font's glyph width (3 px in `smart`), wrapped layout a third of
  its height (5.3 px). Measured the narrow way, a line the app thought fitted
  could be wrapped again by the firmware -- how a one-line string cut to fit
  came to lose its second half.
- **A box gets only as many lines as it is tall.** Text that needs more ends its
  last line with `...`; `fit()` does the same for a box one line high. No
  instruction, help line or message is long enough to be cut on the 320x240
  screen in `smart`: `tests/share_wifi/test_text_fit.py` re-runs every app test
  with the real glyph widths from `romfs/fonts/smart.ppf` and checks every
  draw, and that no fixed string from the app's source is ever cut. Only data
  -- an SSID, a reading -- may be.
- `BUTTON_HOME` belongs to the launcher. Do not bind it.

## Screens

The display is `HIRES`, 320x240, in the font `pick_font()` resolves (`smart`,
then `memo`, then `sins`). `MARGIN` is 6 px and `LINE_HEIGHT` 16 px.

### Common layout

Every screen except the transfer screen and a measurement subtool's white is
drawn on black, in three parts:

      +--------------------------------------+
      |               Title                  |   white, centred, one line
      |                                      |
      |  > selected item                     |   white
      |    other item                        |   smoke (dim)
      |                                      |
      |  button help                         |   grey, one line, at the bottom
      +--------------------------------------+

- **Lists** (`draw_menu`): rows start one line below the title, one per
  `LINE_HEIGHT`, the selected row prefixed `> ` in white and the rest indented in
  smoke. An empty list shows its empty message centred instead.
- **Long lists are paged**, as *Display Saved Measurements* pages its sections. A page
  holds as many rows as fit between the title and the help line -- twelve at
  320x240 -- and when a list needs more than one, the title carries the page on
  its right:

      +--------------------------------------+
      |  Connect                       2/3   |
      |                                      |
      |    13th network                      |
      |  > 14th network                      |
      |    ...                               |
      |                                      |
      |  A - Connect   B - Back              |
      +--------------------------------------+

  Up and Down still move the selection one row at a time, since A acts on a
  single row. Moving past the last row of a page shows the next page with the
  selection on its first row; moving above the first row shows the previous page
  with the selection on its last. Wrapping from the end of the list to the start,
  or back, goes to the first or last page. A list that fits on one page shows no
  page number.

  The page number is drawn white, flush with the right margin.
  `draw_title(text, reserve)` keeps `reserve` pixels clear at *both* ends -- the
  number's width plus `MARGIN` -- so the title stays centred on the screen
  rather than on what is left of the line.
- **Messages** (`draw_centered`): wrapped and centred both ways in the area
  between the title and the help line.
- **Help line** (`draw_help`): the buttons the screen accepts, along the bottom.

### Navigation

- Up and Down move the selection, wrapping at both ends, and turn the page when
  a long list needs it.
- A selects. B goes back a screen, or cancels the operation in progress; on the
  main screen it does nothing.
- C is unbound except on the main screen, where it reveals Dev Mode -- see
  below -- and in *Display Saved Measurements*, where it leaves like A and B.
- `BUTTON_HOME` belongs to the launcher and is never bound.
- Case lights: off on menus and results, steady on while connecting or
  transferring, a slow pulse while looking for a peer, a fast blink on a failed
  transfer -- see "The transfer screen". A measurement subtool holds them
  off.

### Main

| | |
|---|---|
| Title | `Share WiFi` (never paged: at most four items) |
| Items | `Share`, `Receive`, `Connect` -- and `Dev Mode` once revealed |
| Help | `Up/Down - Move   A - Select   B - Exit` |
| A | Share: refresh the network list and open *Share which network?*. Receive: *Line the badges up*, then a transfer as Follower. Connect: refresh the list and open *Connect*. Dev Mode: open *Dev Mode*. |
| B | leave the app (`update()` returns `True`, after `on_exit()`'s cleanup) |
| C, held 5 s | Reveal `Dev Mode` |

- **`Dev Mode` is hidden at launch.** Holding C on the main screen for
  `DEV_HOLD_MS` (5 s, by `badge.ticks`) appends it to the list and moves the
  selection onto it; a shorter hold, or a press, does nothing, and a release
  starts the count again. It stays until the app exits, and is not
  remembered across launches.

### Share which network?

| | |
|---|---|
| Title | `Share which network?` |
| Items | every saved network, sorted by name: `name  (Open)`, `name  (WPA2)` and so on, or the bare name when the security type is unknown; paged when there are more than fit |
| Empty | `No Networks` |
| Help | `A - Share   B - Back` |
| A | *Line the badges up*, then a transfer as Leader with that credential |
| B | main screen |

### Line the badges up

Before every transfer -- Share, Receive, and a retry. HIRES, on black.

      +-------------------+-------------------+
      |                   |                   |
      |   Align edge of   |                   |   white, centred in the
      |  badge with line. |                   |   left half
      |   A to continue   |                   |
      |                   |                   |   sensor ~1 mm past the
      |                   |                   |   right edge: no text there
      +-------------------+-------------------+
                          ^ a 2 px white line, full height, at the middle

| | |
|---|---|
| Line | white, `ALIGN_LINE` (2 px) wide, the full height, centred on `screen.width // 2` |
| Message | `Align edge of badge with line. A to continue`, wrapped and centred in the left half -- the side away from the light sensor, which sits beside the right edge |
| Help | none: the message says what A does |
| A | the transfer starts |
| B | back where it came from: *Share which network?*, the main screen, or *Transfer failed* |
| Case lights | off |

> **Why.** Two badges held flush and square put each sensor -- a millimetre
> outside its screen's right edge -- over the other's bezel, not its screen.
> Holding the other badge's edge on the middle line puts half its screen over
> this one's sensor, which the geometry in
> [.agents/optical_data_transfer/optical-coupling.md](../optical_data_transfer/optical-coupling.md) predicts gives two to three times
> the swing. **[inferred-all]**: Dev Mode's 2nd-badge tool measures it, from the
> same hold.

### Transfer

The whole screen white, every symbol the backlight, no text -- see "The
transfer screen". The case lights say what is happening.

| | |
|---|---|
| B | cancel: the transfer ends `ERR_CANCELLED` |
| End | backlight back to 230/255, case lights off. A Leader goes to *Done*. A Follower validates the payload and saves it, then goes straight to *Connecting* with the new network; an invalid payload goes to *Transfer failed* with `ERR_DECODE`. Any protocol error goes to *Transfer failed*. |

### Done

| | |
|---|---|
| Title | `Done` |
| Message | `Shared.` |
| Help | `Any button - Main Menu` |
| Any button | main screen |

### Transfer failed

| | |
|---|---|
| Title | `Transfer failed` |
| Message | by exit value, in smoke: `No other badge found. / Point them at each other.` (`ERR_NO_PEER`), `Can't see the other screen. / Move closer, dim the lights.` (`ERR_CONTRAST`), `Lost the other badge.` (`ERR_LOST_PEER`), `Too many errors. / Hold the badges still.` (`ERR_CRC`), `Received data was invalid.` (`ERR_DECODE`), `Cancelled.` (`ERR_CANCELLED`), `The light sensor stopped / reading. Relaunch the app.` (`ERR_RING`) |
| Help | `A - Main Menu   B - Retry` |
| A | main screen |
| B | the same transfer again, same role and payload, by way of *Line the badges up* |
| Case lights | fast blink |

### Connect

| | |
|---|---|
| Title | `Connect` |
| Items | as *Share which network?*, paged the same way |
| Empty | `No Networks` |
| Help | `A - Connect   B - Back` |
| A | connect to that network -- *Connecting*, or *Connection failed* at once for WEP |
| B | main screen |

### Connecting

| | |
|---|---|
| Title | `Connecting` |
| Message | the network's name, and beneath it one to three dots cycling every 400 ms |
| Status | `Wi-Fi status unavailable`, smoke, above the help line, only while the firmware cannot report a status |
| Help | `B - Back` |
| B | *Connect* |
| End | connected: *Connected*. A failure status, or 30 s without connecting: *Connection failed*. |

### Connected

| | |
|---|---|
| Title | `Connected` |
| Message | `Connected to` and the network's name |
| Help | `Any button - Back` |
| Any button | *Connect* |

### Connection failed

| | |
|---|---|
| Title | `Connection failed` |
| Message | the network's name, then the firmware's reason and the status code, e.g. `Incorrect password. (-3)`; `Timed out`, with `Wi-Fi status unavailable` added when the status could not be read; or `WEP networks not supported` |
| Help | `A - Retry   B - Back` |
| A | try the same network again |
| B | *Connect* |

The Dev Mode screens are under "Dev Mode", below.

## Share

- First pick which credential to share: the SSID list from the config file with each
  network's security type, Up/Down to move, A to choose, B to return. "No Networks" if
  the file is empty.
- One credential per transfer, sent as **three fields: SSID, key, security**.
  Security is a single byte.
- Then *Line the badges up*; A there takes the Leader role, and the screen is
  the field from the first frame.
- On success show *Done*; any button returns to the main screen. On error show
  *Transfer failed*: A returns to the main screen, B retries.

## Receive

- *Line the badges up* first; A there takes the Follower role, and the screen
  is the field from the first frame.
- On success, save the credential with `State.modify`, open the Connect screen with the
  new network preselected, and attempt to connect.
- A payload that arrives intact but is not a valid credential is reported as
  `ERR_DECODE` on *Transfer failed*, like any other error: A returns to the main
  screen, B retries.

## Connect

- List the SSIDs from the config file, each with its security type where one is
  known. "No Networks" if empty, B returns.
- An open network connects with an **empty key**, which is what selects
  `CYW43_AUTH_OPEN`. No password is asked for and none is needed.
- A **WEP** network is refused before the radio is touched, reporting
  `WEP networks not supported`. It is still listed, still shareable and still
  saved when received — only joining is refused, because only joining is
  impossible. A badge that cannot join one can still hand the credential to a
  phone that can.
- The refusal is **probed, not hardcoded**: `wep_supported()` asks whether
  `network.WLAN` advertises a WEP security type, so a firmware that gained one
  would stop being blocked. If it ever returns `True`, joining also needs
  `security=` to reach `wlan.connect()`, which `wifi.connect()` does not
  forward — that is the remaining work, and the probe gates the attempt rather
  than pretending the ordinary path would carry it.
- Connecting is non-blocking and driven from the frame loop: call `wifi.connect(ssid,
  key)` every frame while the attempt is live.

      wifi.connect(ssid, key)   # every frame; optional timeout= (60 s), retries= (5)
      wifi.is_connected()
      wifi.status()             # (code, description)

- Status codes 0 Idle, 1 Connecting, 2 Connected and 3 Got IP mean keep going. Anything
  else is a failure: -1 failed, -2 no such access point, -3 wrong password.
- Give up after 30 s and report a timeout.

> The radio can sit on a neutral status indefinitely when the network is out of range,
> so waiting for a failure code is not enough on its own.

- On success show a message; any button returns to the list. On failure show the code
  and description; A retries, B returns.

## Network config file

- A JSON object mapping SSIDs to their keys, held by `State` under the name
  `share_wifi`, which puts it at `/state/share_wifi.json`.

      networks = State.load("share_wifi", {})
      State.modify("share_wifi", {ssid: key})

- `State.modify` merges; `State.save` would replace the whole object and drop every
  other network.
- Write the config file once, after the transfer verifies. Never during it.

> The writable volume is ~256 blocks of 4KB and wears out, and a flash write mid
> transfer will overrun a slot.
- An SSID that is not valid UTF-8, **or that begins with `%`**, is stored as `%`
  followed by its bytes in hex.

> Otherwise the SSID `%41` is stored verbatim and read back as `A` — a different
> network, silently. A `%` anywhere but the first character is unambiguous.

- A stored name beginning with `%` whose remainder is not valid hex is treated as a
  literal name rather than raising.
- A stored value is `{"key": ..., "security": ...}`. A bare string is what earlier
  versions wrote and is still read: its type is inferred the way a two-field payload's
  is, rather than the network being dropped.

## Security types

- The codes are the **generic MicroPython auth-mode numbering**, chosen because it is
  small, conventional and readable on the wire. It is *not* what `WLAN.scan()` returns
  on this badge — see the warning below — so nothing here may be populated from a scan
  without translating first:

  | Code | Type | Key |
  |---|---|---|
  | 0 | Open | must be empty |
  | 1 | WEP | 5 or 13 characters, or 10 or 26 hex digits |
  | 2, 3, 4 | WPA, WPA2, WPA/WPA2 | 8..63 characters, or exactly 64 hex |
  | 5 | Enterprise | not carried — see below |
  | 255 | Unknown | treated as WPA for key length |

- 255 is this app's, never reported by a scan. It means the badge holds a key but was
  never told what kind of network it opens — which is every credential taken from
  `secrets.py`, since that file records no type.
- A **two-field** payload comes from a badge older than this field. Infer rather than
  refuse: an empty key can only be an open network, anything else is Unknown.
- **Enterprise is refused.** It needs an identity and usually a certificate, neither of
  which is a passphrase and neither of which this carries. Refusing beats delivering
  something that cannot connect.

> **Why a type at all, when an empty key already means open?** Because an empty key is
> otherwise ambiguous between "this network has no password" and "we lost the
> password", and because key length has no single answer — 0 for open, 5 or 13 for
> WEP, 8..63 for WPA. Without a type a WEP key looks like a corrupt WPA one and is
> rejected. The protocol carries the field for free: it takes a list of byte fields of
> any length and has never cared how many there are, so **nothing in `protocol.py`
> changed for this.**

> **A cyw43 scan does not report these numbers.** **[source-v3]** `network_cyw43.c`
> puts `res->auth_mode` straight into the scan tuple, and `cyw43_ll.h` documents that
> field as "security and authentication flags" — the `CYW43_AUTH_FLAG_*` bitmask, where
> WPA2-AES is `0x00400004` and WPA3 is `0x01000000`. Only Open (0) and WEP (1) happen
> to coincide with the numbering above. Anything reading security off a scan has to map
> the bitmask itself; `share_wifi` never scans, so this bites `wifi_scanner` rather than
> this app.

> **WEP can be carried but not joined.** **[source-v3]** The firmware's
> `wifi.connect()` calls `wlan.connect(ssid, psk)` and never forwards a `security=`
> kwarg. Reading `extmod/network_cyw43.c`, that leaves exactly two reachable modes: an
> empty key selects `CYW43_AUTH_OPEN`, and a non-empty one selects
> `CYW43_AUTH_WPA2_MIXED_PSK`. Everything else "requires the security kwarg to be set
> explicitly". So a WEP credential survives the trip and is stored correctly, and
> joining it through this app will fail.
>
> **The badge cannot join a WEP network.** The single reason is that the radio driver
> has no WEP support: `cyw43_ll_wifi_join()` matches `auth_type` against Open,
> WPA-TKIP, WPA2-AES, WPA2-mixed and the two WPA3 modes and returns `-CYW43_EINVAL`
> for anything else **before issuing a single ioctl**, and the driver contains no
> `WLC_SET_KEY` / `wsec_key_t` path at all — it only ever writes `wsec_pmk`, which is
> the WPA passphrase ioctl. Everything above that layer is downstream of it:
> `wifi.connect()` not forwarding `security=`, and MicroPython not exporting a
> `SEC_WEP`, both change the error rather than the outcome.
>
> Carrying the credential is still worth it: a badge that cannot join can still relay
> it to something that can.

## This badge's own network

- At launch, the credential the badge was provisioned with is added to the config
  file if its SSID is not already there. A badge that has never received a
  credential can then still share one.

      import secrets                     # a firmware module, not a global
      secrets.WIFI_SSID, secrets.WIFI_PASSWORD

- It goes through the same `valid_credential()` gate a received payload does, so an
  open network or a key shorter than WPA allows is not saved.
- A name already in the file **wins**. It may have been received from another badge
  or corrected by hand, and overwriting it every launch would undo that — and cost a
  flash write every launch to do it.
- A badge with neither `/secrets.py` nor `/system/secrets.py` loses the feature and
  nothing else. Do not reach for `secrets.require()`: it reports a missing value by
  calling `fatal_error()`, which resets the badge.

> **The key is only readable here.** `network.WLAN` on this hardware is MicroPython's
> CYW43 driver. Its `config()` getter answers `ssid`, `security`, `mac`, `channel`,
> `txpower`, `pm`, `antenna` and `hostname`, and raises `ValueError` on anything
> else; `key` and `password` are set-only, and set the *access point's* password at
> that. A station PSK goes into `connect()` and is never readable again. So the
> network a badge is associated with *right now* cannot be shared — only the one it
> was configured with. **[source-v3]**

---

## Dev Mode

Tools for measuring the backlight and the light sensor on real hardware, so
the unmeasured and inferred figures in `data.md` and hardware notes can
be replaced with measured ones. Reached from the main screen once holding C
has revealed it.

**Since 2026-10-18 the badge holds only the menu and the link.** Every
measurement is a dev link subtool in
[`tests/share_wifi/devtests/`](../../tests/share_wifi/devtests/), sent to
the badge's `/devtests/` over the link (`sync`) and run from a host: its
tests, readings and calculations in the libraries under `devtests/lib/` --
`measure.py`, `probe.py`, `flicker.py`, pure, tested in CPython on simulated
rings (`tests/share_wifi/ring_optics.py`), and `fastloop.py` and
`toolkit.py`, badge-only. Each subtool sends its result to the host and
saves it in the measurements file (`api.save()`), which the menu shows. The
ring-based tests as they stand since 2026-10-11 have **not been run on
hardware** as subtools; M3-M5, which they keep, have [measured-v2].

Where this section was silent the implementation chose, and the choices are
recorded below as part of the spec.

### Dev Mode menu

| | |
|---|---|
| Title | `Dev Mode` |
| Items | `Display Saved Measurements`, `Clear Saved Measurements`, `USB Dev Link`, then one per `MENU` entry of each dev tool stored on the badge, by file name (`devserver.menu_entries()`, read when Dev Mode opens, after the link closes and after a clear) |
| Help | `A - Select   B - Back` |
| A | open the chosen item |
| B | main screen, with the selection back on its first item (`Share`); `Dev Mode` stays in the list |

- **A stored tool's entry** runs it on the badge with no host
  (`devserver.run_local()`, a `LocalApi`): *Starts in 3 s* -- the hand that
  pressed A gets clear -- then the run, blocking; B held is seen between
  steps (there is no hard cancel without the link). What it sends goes
  nowhere; what it saves (`api.save()`) is in the file. *Done* then says
  `Done: see Display Saved Measurements`, `Cancelled`, or the error. A tool
  gets on the badge over the link (`sync`, `new`) and stays until cleared.
- **`Clear Saved Measurements`** deletes `/state/measurements.json` **and
  every stored dev tool and library** (`clear_all()`) at once, with no
  confirmation, and shows *Done* with the message `Measurements and dev
  tools cleared` -- the same whether there was anything or not, since
  either way there is none now. The link's `clear` does the same. Any button returns here with this item
  selected. It deletes with `os.remove()` and then checks with `os.stat()`
  that the file is gone, rather than reading an errno, unproven on the
  badge, or using `State.delete()`, which swallows every error: only a file
  still there afterwards shows `Could not clear`.

### What a run measures

The `mirror` and `second_badge` subtools run the same tests, one condition a
run -- a **dark** room or a **brightly lit** one, as the host names it -- and
record one result object per condition. "The sensor" is the measuring
badge's; "the backlight" is whichever badge's is sending: its own, off a
mirror, in mirror mode, the other badge's in 2nd-badge mode.

**Every signal is the backlight; every reading is the DMA ring** (since
2026-10-11). When a condition begins the screen is filled with the panel's
full white, rgb(255, 255, 255) -- once, `run()`'s clear off so nothing
redraws it (`toolkit.white()`) -- and from then until the condition ends
nothing is drawn: no text, no field, no mode switch. Each test then sets
`display.backlight()` and reads the sensor through the ring
(`fastadc.Ring` under a `reader.RingReader`, as a transfer reads), which the
run starts when the condition begins and stops when it ends -- held through
the link's `ring_start()` from first to last (`toolkit.held_ring()`), so no
USB write can happen and the link stops it if the run raises. Nothing may
read the ADC while it runs, so the chip's temperature is read just before it
starts and just after it stops, and nowhere else. A badge whose ring cannot
be made or verified measures nothing: every test records `{"error":
<ring_note()>}`, the condition ends at once and the run is still saved. The ring
reads the ADC's own 12-bit result (`SENSOR_STEP`, 16 counts,
[measured-all]).

Every test runs on an **absolute timeline**: its backlight changes and the
windows it reads at fixed times from its origin, whatever its analysis
costs in between, and it is done by its time in the table below. So two
badges that share a time zero run the same test in lockstep, one sending
and the other only reading -- the 2nd-badge tool -- and the mirror is one
badge doing both. A test runs one a step, `TEST_GAP_MS` (100 ms) after the
one before; between steps the subtool waits a frame's 16.7 ms
(`toolkit.FRAME_US`) and checks `api.cancelled()`.

| Test | What is done | What it settles |
|---|---|---|
| `ambient` | backlight 0.0 (`BACKLIGHT_OFF`) for 300 ms (`AMBIENT_MS`); the mean of the ring from 100 ms in (`AMBIENT_SETTLE_MS`) | the sensor's floor in this light |
| `levels` | the backlight scheme's low, middle and high (`LEVEL_SCHEMES[SCHEME_BACKLIGHT]`: 0.5, 0.82, 1.0) held 300 ms each (`LEVEL_MS`), then low again; each level's last 4,096 readings (`NOISE_READS`, 81.92 ms) copied out of the ring while the next is held: their mean; the span (high less low) and the middle's share of it | the levels a transfer's calibration will find, and whether its gaps clear `MIN_GAP_EXACT` |
| `noise` | from the same readings, per level: `sd` and `p2p` of single readings, and `means` -- the standard deviation of the means of every 4, 16, 64 and 256 consecutive readings (`NOISE_DEPTHS`; a depth with under 8 means is left out) | whether averaging buys resolution below an ADC step -- by the square root of the depth if the noise is independent, not at all if it is narrower than a step -- on the levels a transfer uses: what `averaging` and the probe's `fast_reads` measured before the ring |
| `backlight_curve` | backlight 0.0, 0.1 ... 1.0, 100 ms each (`CURVE_MS`); the mean of each one's back half | whether light is proportional to PWM duty |
| `flicker` | backlight 0.25, 0.5, 0.74, 0.9 and 1.0, 100 ms each (`FLICKER_MS`); the 256 readings that end each (`FLICKER_READS`, 5.12 ms -- about 20 PWM periods); min, max, standard deviation and a verdict | whether the sensor averages the backlight's PWM or follows it: the backlight scheme rests on it |
| `backlight_step_ring` | **Dev Mode M4.** The scheme's three levels held 150 ms each (`RING_LEVEL_MS`) and measured (their back half's mean), then 10 each (`RING_STEP_TRANSITIONS`) of every transition the scheme makes -- low to high (`rise`), low to middle (`mid_rise`), high to low (`fall`), middle to low (`mid_fall`), middle to high (`mid_high`), high to middle (`high_mid`) -- one every 142 ms (`RING_STEP_PERIOD_MS`): the first level set, the second 120 ms later (`RING_STEP_HOLD_MS`), read at 20 µs in 100 µs bins from 2 ms before that call to 20 ms after. Per transition the mean `t10_ms`, `t50_ms`, `t90_ms` from the call, and for rises out of the low `tlow_ms`, when it crossed `T_low`; `count`; `levels`; on the badge that sent, `tx_late_us`, how long the call took (mean, max) | `SETTLE_DARK_US`, `SETTLE_LIT_US`, `GUARD_DARK_US` (a rise to the middle against one to high), `EDGE_LAG_EXACT_LIT_US` and `EDGE_LAG_EXACT_DARK_US` |
| `loopback_ring` | **Dev Mode M5.** The three levels measured as M4 does; then at 16, 13, 10, 8 and 6 ms (`RING_LOOP_RUNGS`) `RING_BLOCK` -- 4 low slots, a high sync and the 29-symbol de Bruijn sequence three times -- sent on the backlight at the transfer loop's timing and decided by a listening `Transfer` in the data phase, deciding slots from their windows and phase-locking with `reader.rise()`. Per rung: `symbols`, `errors`, `locks` and `lag_us` (min, mean, max: each lock's crossing after the nearest boundary), `parts` (a window's) and, on the sender, `tx_late_us`. Plus `levels` and `lit`. Levels a transfer would refuse -- `gap_separable()` with `MIN_GAP_EXACT`, so each gap at least 18.5 counts (the quantisation floor) -- give `{"error": "levels too close", "levels": [...]}` and no rungs; **a badge that sends still sends every block**, for the one reading them | which rungs go on the ladder and where pairs start: the ring, the kernels and the lock checked end to end on hardware |
| `temperature_c` | the chip's temperature at `start` -- before the ring starts -- and at `end`, after it stops | a proxy for the board's warmth, good for a change across a run |

- **Flicker verdict.** `averages` when at least 90% of the 256 readings at a
  backlight value fall within two ADC steps (`SENSOR_STEP`) of their median,
  as they do at 1.0; `follows_pwm` otherwise. Recorded per backlight value.
  The ring reads at 50 kHz, so it resolves the v2 backlight's 3.05 kHz PWM
  [measured-v2] (3.8 kHz on v3 [source-v3]) where the 256 back-to-back
  `light_level()`s it replaced sampled at ~37 kHz.
- **On a 2nd badge** every test's times are the receiver's estimate of the
  sender's: time zero is the go's fall as this badge saw it, found again in
  the ring to 100 µs (`FALL_BIN_US`). So M4's `t10`/`t50`/`t90` and M5's
  `lag_us` there are from that estimate -- each carries the sensor's own
  fall delay, ~0.3 ms lit to ~1.5 dark [measured-all], and the two clocks'
  drift since the go (≤ 2 ms over a turn at 100 ppm [unverified]) -- and
  can read negative; `t90_ms - t10_ms` holds either way. M5's lock takes
  whatever offset remains, within two and a half slots.

### The schedule

| Test | Each | Total |
|---|---|---|
| `ambient` | 300 ms | 0.3 s |
| `levels` (and `noise`): 3 levels, then 200 ms for the last level's statistics (`LEVEL_ANALYSE_MS`) | 300 ms | 1.1 s |
| `backlight_curve`: 11 values | 100 ms | 1.1 s |
| `flicker`: 5 backlight values | 100 ms | 0.5 s |
| `backlight_step_ring`: 3 levels, 60 transitions, 100 ms margin | 150 / 142 ms | 9.07 s |
| `loopback_ring`: 3 levels, then per rung 30 ms + 98 slots, 100 ms margin | | 5.735 s |

- **Schedule: 18.305 s** (`SCHEDULE_MS`), the five 100 ms gaps included;
  the mirror's begins 100 ms after its ring starts (`MIRROR_LEAD_MS`). A
  2nd-badge *turn* adds its handshake (three call cycles and the go, about
  1.7 s when the other badge is ready), the 1 s of dark after the go and
  1 s that ends it: about 22 s, so a condition -- the noise second and two
  turns -- is about 45 s.
- **The case lights stay off while measuring.** Lit, they would light the
  room the dark run's `ambient` and low readings are measuring.
- After each run, and on a cancel, the ring stops and the link's `restore()`
  brings `run()`'s clear back, the backlight to 230/255 and the case lights
  off.

### How each result is worked out

- **Readings** are the ring's, in `light_level()` counts (a raw 12-bit
  reading x 16). A window's mean is `reader.mean()`, the means of its parts
  `reader.parts()`; a test reads a window only once its last reading is in
  the ring (`READ_MARGIN_US`, 500 µs after it ends). Single readings
  (`noise`, `flicker`) are copied out by `copy_readings()`, 32 a query; a
  copy that finds a reading already overwritten, or not yet taken, ends
  there, and the level is `{"error": "no readings"}`.
- **`levels`** is `{"error": "no readings"}` if any level's readings were
  lost, `{"error": "high not above low", "low", "mid", "high"}` if the span is
  not positive.
- **`noise`**: `sd` is the population standard deviation (two-pass, so
  single precision holds), `p2p` the spread; each `means` entry is the
  standard deviation of the depth's group means about the overall mean.
- **`backlight_step_ring` timing.** A transition's 220 bins are read after
  its capture ends; its starting and final levels are the means of its first
  and last 20 bins (2 ms each), and one that moved less than an ADC step is
  not counted. The 10, 50 and 90% crossings, and `T_low` (half way between
  the measured low and middle), are each the first bin at or past the level
  after the call, interpolated from the bin before. The analysis runs in the
  next transition's hold, which it shortens: the call itself is on time.
- **`loopback_ring`**: the listener is a Follower whose grid starts on the
  block's first boundary, its silence counted from there, so the high sync
  after four low slots is the lock. `errors` counts the sequence's slots
  after the first high decided, against the sequence; a block with no high
  decided is `{"error": "no sync symbol"}`. A rung that raises is its own
  error; the others are kept.
- **`temperature_c`** is the chip's own temperature, in degrees C to one
  decimal place: the mean of 16 reads of the RP2350's sensor, through the
  datasheet's line -- 0.706 V at 27 C, falling 1.721 mV a degree, with a
  3.3 V reference **[datasheet]** -- found as
  `machine.ADC(machine.ADC.CORE_TEMP)`. That name is probed rather than a
  channel number guessed: the sensor is channel 4 on an RP2350A and 8 on an
  RP2350B, where 4 is a pin that would read without complaint. A firmware
  without it records `{"error": "no temperature sensor"}`. It is the die,
  which warms with the CPU's load: good for a change across a run.
  `ADC.CORE_TEMP` exists on the badge's v2 firmware: 2026-10-06 read
  26-28 C **[verified-v2]**.

**Where a failure is recorded.** A failed result is `{"error": "<what
failed>"}`, in place of the narrowest result it spoils:

| Scope | Results | Errors |
|---|---|---|
| per level | `noise` | `no readings` |
| per rung | `loopback_ring` | `no sync symbol`, or a rung that raised |
| the whole test | every test | `<ring_note()>` (no ring), `ring failed` (`reader.check()` false as it began), `no handshake` (2nd badge), a test that raised (`<Type>: <message>`), and its own: `no readings`, `high not above low`, `no readings at <value>`, `levels too close` |

A test that raises is recorded under each of its keys and the run carries
on.

**Rounding.** Readings, means, `span` and `p2p` are integer counts;
`sd` 1 decimal place and `means` 2; `mid_share` 3; M4's times in ms to 2
decimals; `lag_us` and `tx_late_us` whole µs.

### The handshake

Every 2nd-badge turn starts with a handshake: the badge about to send
**calls** with short backlight pulses until the other **answers** halfway
between them, then says **go**. Nothing is timed against the other badge
until both have seen each other, so neither user has to press A at any
particular moment. Between pulses both backlights are **off**: the largest
swing the backlight has, for a detector that must find it across two badges.

| | |
|---|---|
| Call | backlight 1.0 for `PULSE_HIGH_MS` (60 ms), then off for 300 ms: a pulse every `PULSE_PERIOD_MS` (360 ms) |
| Answer | one 60 ms pulse starting `REPLY_DELAY_MS` (180 ms, half a period) after the rise of each call pulse the answering badge sees |
| Accepted | an answer whose rise the caller sees 120-270 ms after the start of its own pulse |
| Go | after `REPLIES_NEEDED` (3) accepted answers in a row, the caller holds 1.0 for `GO_MS` (600 ms) where its next pulse would have been, then off |
| Time zero | the go's fall: the caller's as it set the backlight off, the answerer's as it found it in the ring. Both hold the backlight off for `GO_GAP_MS` (1 s), and the turn's schedule starts then |
| Give up | `HANDSHAKE_TIMEOUT_MS` (60 s) without a go, either side: the ring stops, and the run holds 3 s (`NO_ANSWER_HOLD_MS`) before it ends |

- **The detector** is fed the ring's means of every 4 ms (`SAMPLE_US`),
  each timed at its centre, in ms on the badge's own ring clock -- every one
  since the last frame, so a pulse's timing does not depend on the frame
  rate. **A pulse** is a rise of at least the threshold (below) above the
  baseline -- the lowest sample of the last second, frozen at the rise --
  that falls back below half the threshold within 20-150 ms. **A go** is the
  same rise held 450-750 ms. The rise time is interpolated where the samples
  cross half the threshold, the fall at the middle of the swing the pulse
  reached. The answerer then finds the go's fall again in 100 µs bins of the
  ring within 6 ms of that (`FALL_SEARCH_US`): its time zero. The caller
  times answers from the frame it set its own pulse in.
- **The answerer** answers every call pulse it sees, except one rising within
  -60..+90 ms of its own answer -- its own light off the other's glass. It
  accepts a go only after three call pulses spaced 270-450 ms apart, the
  last 270-450 ms before the go's rise. **The caller** counts an answer only
  inside the accepted window -- checking it against the previous pulse too,
  if it is seen after the next pulse has started -- and starts counting
  again on a cycle without one.
- Backlight changes are made on frames, so a pulse is 60 ms to a frame
  more; the windows are wide enough for frames up to ~40 ms.

> Why the go has a maximum width and the answerer wants the calls before
> it: with only "longer than three pulse widths", a badge that missed the
> real go would take any long high in the schedule -- a 300 ms level, a
> backlight curve's top -- for one, and measure the other's schedule out of
> step. M4's transitions are pulse-shaped too (a high held 22 ms or more,
> one every 142 ms): the period check keeps them from passing for calls.

> Why halfway: face to face, a badge's own pulse can reflect off the other
> badge's glass into its own sensor. That echo arrives with the pulse, while
> a real answer arrives at 180 ms plus a frame each side -- so the window
> cannot mistake one for the other, and it ends before the caller's next
> pulse at 360 ms. Three answers in a row rather than one, so a noise spike
> cannot start a run. A go, rather than simply stopping, so the answerer
> never has to judge silence by a timeout; its fall is a single sharp edge
> both badges time.

- **The threshold** comes from the badge's **noise second**: each run opens
  with a second with both backlights off, the ring's 4 ms samples kept. A
  pulse must rise **half again that second's noise, and never less than 48
  counts** (three ADC steps). The noise is **twice the spread from the
  lowest sample to the median**: for noise spread evenly about a level that
  is its peak-to-peak, and the other badge's call pulses -- a sixth of the
  time -- cannot reach the median if it started calling first. With fewer
  than eight samples the threshold is 48. It holds for both handshakes of
  the condition. The noise second opens the run, once the ring has
  started.

### `mirror`

The badge measures its own backlight in a mirror: `run mirror dark` or
`run mirror lit`, the badge on its mirror in that light. The white, the
ring, and the tests in the order of the table above; nothing is drawn until
the run ends. Saved under `mirror.dark` or `mirror.lit` -- the other
condition's entry kept -- with `tethered`, and sent as `mirror.json`.

### `second_badge`

Two badges, each on its own link, held screen to screen. Each measures the
other's backlight, and its own off the other's glass: `run second_badge
first dark` on one, `run second_badge second dark` on the other, within a
minute of each other.

1. The white, the ring and the run's noise second.
2. **First turn.** The first badge calls; the second answers (see "The
   handshake"). After the go the first badge runs every test as the mirror
   does -- sending, and reading its own sensor -- while the second runs the
   same tests in step, reading only, its backlight off; then both hold the
   backlight off for 1 s.
3. **Second turn.** The second badge, knowing from time zero when the first
   turn's schedule and closing dark end, then calls; the first badge, which
   has been listening since its own schedule ended, answers; and after the
   go the second badge sends and the first measures.
4. **Both finish** when the second turn's schedule and its closing dark end,
   and each saves `{"role": ..., "<condition>": ...}` under `second_badge`.

- **Nothing is drawn while a condition runs.** The backlight is off between
  pulses, so text could not be read anyway.
- **What each badge saves**: the tests it measured, read off the other's
  backlight, under their names; and under **`self`** the same tests from its
  own turn -- its own sensor reading its own light off the other badge's
  glass, with the other's backlight off: the self-coupling the simulator
  stresses at 5% and nothing has measured [unmeasured]. Its `levels` are
  usually too close for M5 to decide (`levels too close`), and that is the
  answer: it sent every block regardless.
- **A handshake that times out** records `{"error": "no handshake"}` for
  every test the badge had not yet measured in that condition, and for
  `self` if it had not yet sent, and ends the condition for that badge:
  without a handshake neither turn that remains can be trusted.

### Ending a run

- **A cancel** -- B held, the host's `cancel`, a lost host -- is seen between
  steps (`api.cancelled()`); B's IRQ and the host's raw 0x03 also interrupt
  a step outright (the hard cancel). Nothing is saved, the ring stops, and
  the run ends `cancelled`.
- **A test that fails** -- no handshake, a sync symbol never seen, a level
  with no readings -- is recorded as `{"error": "<what failed>"}` in place of
  its result, and the run carries on. What finished is saved.
- **A write that fails** -- an `OSError` from the flash, such as a full
  volume -- makes `api.save()` return False; the result still goes to the
  host.

> The app writes the file itself, with `open()` and `json.dumps()`, rather
> than through `State.save()`: that catches `OSError` and returns quietly
> whenever `/state` exists, so a failed write would read as a saved one.
> **[source-v3]** from `modules/common/badgeware/state.py`.

### `frame_probe`

Times what the badge's own work costs it. Written when the first mirror run
measured 54 ms frames against the 11.1 ms the hardware notes expected; its
first run found why -- the v2 firmware's panel runs at 60 Hz, and a HIRES
frame that filled the screen twice and drew text needed three refreshes
(see "What the hardware runs at" in
[badgeware-hardware.md](../docs/badgeware-hardware.md)). It now times the panel,
the transfer's listener through the ring, the flicker filter, allocation
and collection. It needs no mirror and no second badge.

`run frame_probe`. The steps are in
[`devtests/lib/probe.py`](../../tests/share_wifi/devtests/lib/probe.py), a
pure module: everything that touches the badge (presenting, vsync, the
clear, the poll, its status frame, the ring step, the collector) is passed
in through `Hooks`, by the subtool `devtests/frame_probe.py`, which also
holds the probe's listeners and the transmitter's path.

1. The seven steps run in order. Before each, the screen shows
   `Probe: <step> n/7` (for example `Probe: Refresh rate 1/7`), presented;
   the step then blocks until it ends. After each step the backlight goes
   back to normal.
2. The results are saved under `frame_probe` (see "Measurements file") and
   sent as `frame_probe.json`.

| Step | What is done | What it settles |
|---|---|---|
| `refresh` | 60 back-to-back `display.update()`s with vsync on, after one to line up; the intervals, and `hz` = 1000 / mean | the panel's real refresh rate: 16.7 ms is 60 Hz, 11.1 ms is 90 Hz -- which firmware this is |
| `write` | the same with `display.set_vsync(False)`, then vsync back on | what writing one frame costs on its own |
| `reads` | **`loop`**: an empty loop, each pass timed with `time.ticks_us()`, until 5,000 passes (`READ_CALLS`) or 1 s (`READ_MS`); `calls`, `per_s`, and the pass's `mean_us`, `max_us` and `sd_us`. Nothing presented | the interpreter's own pace and what timing a pass costs, comparable run to run only between runs of the same `TimeStats.add()` |
| `ring` | **Dev Mode M3.** The transfer's listener -- a Follower in the data phase on the lit 8 ms rung, reading through `fastadc.Ring` by `reader.RingReader` -- run as `do_transfer()` runs it for 2 s (`RING_LISTEN_MS`), four times: **`quiet`**, the screen held; **`presenting`**, the same pixels presented every pass, the worst the panel's own DMA can do to the ring's; **`fitted`**, the screen held with a flicker filter fitted on the probe's synthetic room (`fitted_filter()`), its tables built before the timed loop, so each decided slot pays the corrections and tracking a flickering room costs, `fit_verdict` saying the fit was "flicker"; and **`fitted_lit`**, `fitted` with the room put at the middle level. Each: `rate_hz`, readings the ring took a second (50,000 expected); `anchor_spread_us`; `fifo_over`, `faults` and `drift`, the reader's counts of lost readings, faults and anchors that moved past `RING_DRIFT_US`; `lag_min_us` and `lag_max_us`, how far the protocol's clock ran ahead of the newest reading, signed; `work` and `slot_work` (a pass's `work()`, and that over the slots it decided), `slots`, `passes` and `long_passes` (a pass whose own time, less the pump's wait for what is due, passed 5 ms); `rung_ms`; `kernel_us_per_1000`, one `ring.sum()` over 1,000 readings in the uncached alias; `gc_ms` and `gc_overran`, a `gc.collect()` half way through and whether the ring lost readings across it; `mem_free_start` and `garbage_per_slot` where `gc.mem_free` exists. With the screen held, `tx_path`: a transmitter's own path from the pump returning to the backlight changing -- `field_at()` over a queued message, one slot a call, and `show_backlight()` -- against the bare `display.backlight()` call (`field_and_show`, `backlight_call`, 200 calls each, and `changes`); the white is drawn once before anything is timed. **The listener's peer is dark whatever the room**: the ring starts, its first 16 ms (`ROOM_US`) is the room (`room`), and the levels are put around it (`probe_listener()`: middle and high `PROBE_LEVEL_GAP`, 128, and 256 counts above), so every slot decides low -- an upper bound on a slot; `fitted_lit` puts the room at the middle, every slot middle, the lower bound. The slots that closed while the listener was set up are skipped (`catch_up()`). A listener that stops listening says so: `ended`. A firmware without a ring gets `{"error": <why>}` under each mode. The ring is stopped after each mode, before the probe reads the temperature again | whether the ring works on this firmware at all; `SLOT_WORK_MS` and `PASS_WORK_MS` in the simulator; whether the lit 8 ms rung's work fits; `RING_SAFE_US` against the longest stall; what a real transmitter adds to `EDGE_LAG_EXACT_*` |
| `alloc` | 1,000 calls each of a function returning nothing, a 2-tuple, a 4-tuple and an 8-tuple, built from the function's arguments so they really allocate; µs per call, with the bare call subtracted from the tuples | whether allocations bigger than one GC block are the slow kind |
| `flicker_filter` | the protocol's `FlickerFilter` on a synthetic room made before anything is timed: 2 ms means every 2 ms (`FILTER_EVERY_US`) of 700 counts flickering 12 at 120 Hz and 4 at 240, with noise of about 4. **`fit_first_us`**, one fit of 160 ms (80 samples, `FILTER_SAMPLES`), timed on its own because the first fit since launch fills the gain tables; **`fit`**, three more; **`fit_follower`**, three of 100 ms (50, `FILTER_FOLLOWER_SAMPLES`; **Dev Mode M7**); `verdict`, the first fit's; **`steady_check`** and **`steady_check_follower`**, `FlickerFilter.steady_window()` ten times each on the Leader's 100 ms window (50 samples) and the Follower's 60 ms one (30), the check that stands in for a fit in a steady room. Then, on the first filter: **`correct_first_us`**, the first correction; **`correct`**, 500 more; `correct_step_at`, `correct_table` and `correct_amount`, each part of its call chain timed alone 500 times; `amount_learner`, `amount()` on a filter that learnt sines from dark slots; **`track`**, a phase-lock point, and the updates after each 64: **`update`** under 0.3 s from the fit, **`update_rate`** 0.7 s on. Each of those gives `calls`, `per_s`, `mean_us`, `max_us`, `sd_us`; `samples` and `follower_samples` say how many the fits had; `mem_free_start`, the heap as the step began | what the filter costs on the badge |
| `gc` | an untimed `gc.collect()`, three timed; heap in use and free (KiB); **`heap`**, a heap reading and an untimed collection after it; then 120 frames of the probe's status screen with the collector on, and 120 with it off (`gc.disable()`, always followed by `gc.enable()`) | what a collection costs, and whether the long frames are collections |

- **`start`**, before the first step: an untimed collection, **`gc_ms`**
  one timed, and **`heap`**, a heap reading, then an untimed collection: the
  heap as the probe found it, against the `gc` step's.
- **A heap reading** is `micropython.mem_info()`'s GC figures caught through
  `os.dupterm` -- `total`, `used`, `free` in bytes, `blocks_1`, `blocks_2`,
  `max_block`, `max_free` in blocks -- with **`block_bytes`**, the
  firmware's GC block (16 on v2 [source-v2], 32 on v3 [source-v3], told by
  whether `display.set_framerate` exists), and **`top_kb`**, `total` less
  `max_free` blocks of that size: how far up the heap live blocks reach at
  least, exactly when the largest free run is the top one. With `uctypes`
  (since 2026-10-13), `probe.heap_extent()` places that run: it allocates
  the run less a block, reads where it landed and drops it, and adds
  **`run_start_kb`**, **`run_end_kb`** and **`extent_kb`** -- how far up a
  collection sweeps, the run's start if it reaches the heap's end
  (**`extent_exact`** true), else the run's end, a floor -- or
  `extent_error`. It
  zeroes ~8 MB of PSRAM, so a collection follows every reading. The readings walk
  the whole table (~110 ms on v2) and are never inside a timed call.
  `mem_info()` prints to the USB serial too, between the link's frames,
  where the host reads it as noise [unverified on the badge].
- **`temperature_c`** reads the chip's temperature before each step and at
  the end -- never while the ring step's ring runs.
- **`firmware`** records `os.uname().version` (the build) and
  `set_framerate`, which is whether `display` has that method at all. A badge
  that reports `false` runs its panel at the power-on 60 Hz. **Dev Mode M1**
  adds what the ring needs: `rp2`, `uctypes` and `rp2_dma`, whether each
  imports or exists; `ring`, `null` if the ring was made and verified, else
  why not (`ring_note()`); `heap_addr`, where the ring's buffer landed, and
  `ring_addr`, its uncached alias, both in hex -- the ring needs its buffer
  in PSRAM (0x11000000-0x117FFFFF) to use the 0x15000000 alias.
- **The frame time in `gc`** runs from the start of one frame to the start
  of the next, timed with `time.ticks_us()`, so it has sub-millisecond
  resolution. The first frame starts part way through a refresh.
- **Timing allocates nothing.** `TimeStats.add()` keeps its sum of squares
  as two small ints (see its docstring for why).
- **A step that raises** is recorded as `{"error": "<Type>: <message>"}`, and
  the probe moves on. A firmware without `set_vsync` gets
  `{"error": "no set_vsync"}` for `write`. Vsync and the collector are always
  turned back on.
- **B** is checked after every frame of `gc` and after each of the `ring`
  modes, and a cancel between steps; it ends the run with nothing saved.
  `refresh`, `write`, `reads` and `alloc` take about a second each and are
  not checked part way through.
- The ring's rate and the mirror tool's `noise` say how fast and how finely
  the sensor can be read; the probe does not time other ways of reading it.

### Measurements file

`/state/measurements.json` -- the name `measurements`, as `State` would keep it. One
object per tool. A subtool's `api.save(entry, name)` hands the badge its
entry (`save_measurement()` in the app): the file is read with
`State.load()`, the entry merged **key by key** into the tool's object --
so `mirror.lit` keeps `mirror.dark`, and one room-flicker measurement the
others -- and the whole file written by `write_measurements()`, which
creates `/state` if it is missing and lets `OSError` through.
`State.load()` of a missing file saves the defaults it was given, so the
first run on a badge costs two writes.

    {
      "mirror": {
        "dark": { <tests> },
        "lit":  { <tests> }
      },
      "second_badge": {
        "role": "first" | "second",
        "dark": { <tests>, "self": { <tests> } },
        "lit":  { <tests>, "self": { <tests> } }
      },
      "frame_probe": { <probe> }
    }

Every result also has **`tethered`** (since the USB dev link, 2026-10-08):
whether USB power was present when it was saved (`badge.usb_connected()`,
VBUS), so the power path a figure was measured on is known; `null` where the
firmware cannot say. It sits inside each condition (`mirror.dark.tethered`)
and each numbered room-flicker measurement.

Each `<tests>` object has one key per result in the table above, named as
there; a file saved before 2026-10-11 holds keys of tools since removed,
which no tool writes now. Readings are `light_level()` counts, times in ms or µs as
named, shares as decimals:

    "ambient": 40,
    "levels": {"low": 240, "mid": 368, "high": 440, "span": 200, "mid_share": 0.64},
    "noise": {
      "low":  {"sd": 6.1, "p2p": 32, "means": {"4": 3.1, "16": 1.5, "64": 0.8, "256": 0.4}},
      "mid":  {...}, "high": {...}
    },
    "backlight_curve": {"0.0": 40, "0.1": 80, ..., "1.0": 440},
    "flicker": {
      "0.25": {"min": 128, "max": 160, "sd": 6.2, "verdict": "averages"},
      ...,
      "1.0":  {"min": 432, "max": 448, "sd": 5.9, "verdict": "averages"}
    },
    "backlight_step_ring": {"levels": [240, 368, 440],
                            "rise": {"count": 10, "t10_ms": 0.02, "t50_ms": 0.31, "t90_ms": 1.1, "tlow_ms": 0.16},
                            "mid_rise": {...}, "fall": {...}, "mid_fall": {...},
                            "mid_high": {...}, "high_mid": {...},
                            "tx_late_us": {"mean": 35, "max": 80}},
    "loopback_ring": {"levels": [240, 368, 440], "lit": true,
                      "16": {"symbols": 87, "errors": 0, "locks": 7,
                             "lag_us": {"min": 150, "mean": 260, "max": 420},
                             "tx_late_us": {"mean": 60, "max": 140}, "parts": 5},
                      "13": {...}, "10": {...}, "8": {...}, "6": {"error": "no sync symbol"}},
    "temperature_c": {"start": 24.3, "end": 25.1}

The values above illustrate the format only. A failed test holds
`{"error": "..."}` instead. On a 2nd badge `tx_late_us` appears only under
`self` -- the badge that sent -- and `self` holds the same keys as the
tests, but `temperature_c`.

`<probe>` has one key per step of `frame_probe`, plus `start`,
`firmware` and `temperature_c`. Times are in ms to two decimals, except
`reads`, `alloc` and `flicker_filter`, which are in µs:

    "start": {"gc_ms": 29.2,
              "heap": {"total": 8196480, "used": 460800, "free": 7735680,
                       "blocks_1": 5210, "blocks_2": 930, "max_block": 566,
                       "max_free": 453039, "block_bytes": 16, "top_kb": 925}},
    "firmware": {"version": "<os.uname().version>", "set_framerate": false,
                 "rp2": true, "uctypes": true, "rp2_dma": true, "ring": null,
                 "heap_addr": "0x11012000", "ring_addr": "0x15018000"},
    "refresh": {"frames": 60, "mean_ms": 16.67, "min_ms": 16.6, "max_ms": 16.74, "sd_ms": 0.03, "hz": 60.0},
    "write":   {"frames": 60, "mean_ms": 7.02, ...},
    "reads": {"loop": {"calls": 5000, "per_s": 13000, "mean_us": 76.0, "max_us": 140, "sd_us": 2.1}},
    "ring": {"quiet": {"rate_hz": 50000, "anchor_spread_us": 22, "fifo_over": 0, "faults": 0,
                       "drift": 0, "lag_min_us": -40, "lag_max_us": 1800,
                       "work": {"calls": 900, "per_s": 1400, "mean_us": 700.0, "max_us": 2300, "sd_us": 300.0},
                       "slot_work": {...}, "slots": 250, "passes": {...}, "long_passes": 0,
                       "rung_ms": 8, "room": 32, "kernel_us_per_1000": 900,
                       "gc_ms": 44.0, "gc_overran": false, "mem_free_start": 7790000,
                       "garbage_per_slot": 140.0,
                       "tx_path": {"field_and_show": {...}, "backlight_call": {...},
                                   "changes": 200}},
             "presenting": {...}, "fitted": {..., "fit_verdict": "flicker"},
             "fitted_lit": {..., "fit_verdict": "flicker"}},
    "alloc": {"calls": 1000, "call_us": 6.1, "pair_us": 3.2, "quad_us": 31.0, "octet_us": 35.5},
    "flicker_filter": {"samples": 80, "follower_samples": 50, "fit_first_us": 1813,
                       "verdict": "flicker",
                       "fit": {"calls": 3, "per_s": 688, "mean_us": 1453.7, "max_us": 1478, "sd_us": 18.6},
                       "fit_follower": {...}, "steady_check": {...},
                       "steady_check_follower": {...},
                       "correct_first_us": 327, "correct": {...},
                       "correct_step_at": {...}, "correct_table": {...},
                       "correct_amount": {...}, "amount_learner": {...},
                       "track": {...}, "update": {...}, "update_rate": {...},
                       "mem_free_start": 7600000},
    "gc": {"collect": {"mean_ms": 30.0, "max_ms": 31.0}, "alloc_kb": 450, "free_kb": 7500,
           "heap": {...}, "screen": "status", "frames_on": {<as refresh, less hz>}, "frames_off": {...}},
    "temperature_c": {"refresh": 24.8, "write": 24.8, ..., "gc": 25.3, "end": 25.4}

A cancelled probe writes nothing. A probe run replaces the steps it saved,
key by key; every run is also sent to the host, which keeps them all.


### Display Saved Measurements

Shows everything in the file, one section per screen, in a form that can be
typed into a file by hand.

    +--------------------------------------+
    |  mirror.dark.levels           3/41   |   title: the section's JSON path, and the page
    |                                      |
    |  low: 240                            |   one "key: value" line per value,
    |  mid: 368                            |   in the file's own order
    |  high: 440                           |
    |  span: 200                           |
    |  mid_share: 0.64                     |
    |                                      |
    |  Up/Down - Page   A/B/C - Exit       |
    +--------------------------------------+

- **A section** is the plain values of one object -- for example
  `mirror.dark.levels` or `second_badge.lit.self.levels`. Its title is its
  full JSON path, so the line `low: 240` under `mirror.dark.levels` is
  `"mirror": {"dark": {"levels": {"low": 240}}}` in the file.
- **An object holding both plain values and objects** gets a section of its own
  for the plain values, ahead of its children's: `mirror.dark` holds `ambient`
  beside `levels`, and `second_badge` holds `role`. Plain values
  at the top of the file would be titled `measurements`.
- **The order is rebuilt**, because MicroPython's `dict` keeps no insertion
  order and `State` hands back a fresh one anyway: numeric keys by value
  (backlight values, rungs, depths), then the rest alphabetically
  (`ordered_keys()`).
- Values are shown as `json.dumps()` writes them: numbers as numbers,
  strings in double quotes. A failed test is a section
  with the single line `error: "<what failed>"`.
- **A line too wide for the screen wraps** onto the rows below it rather than
  being cut, so a long value -- a firmware version, an error message -- can
  be read and typed back in full. It breaks at spaces where it can and
  between characters where a run has none (`wrap_value()`). The first row
  takes the full width; the rest are indented by `HANG` (`2 * MARGIN`, 12 px)
  so they do not read as lines of their own:

      version: "v1.26.0-preview.123.gabcdef0 on
        2026-02-26 (GNU 14.2.1 MinSizeRel)"

  Pages hold rows, not lines: a wrapped line stays on one page, moving to
  the next (`(cont.)`) if its rows do not fit beside what is already there,
  and is split across pages only when it has more rows than a page holds.
- **A long title is cut from the front** (`fit_tail()`), so its last parts --
  the ones that say which section this is -- stay visible:
  `...dark.loopback_ring.16.lag_us`.
- **A section longer than the screen** continues on the next page, its title
  repeated with `(cont.)`.
- The page number follows the common layout: white, on the right of the title,
  and **hidden when there is only one page**.
- **Up** goes to the previous page and **Down** to the next, wrapping at both
  ends like every list. **A, B or C** return to the Dev Mode menu.
- Every page is built once, when the screen opens, not per frame.
- **Nothing saved:** title `Measurements`, message `No measurements saved`,
  help `Any button - Back`; any button returns to the Dev Mode menu. A file
  that cannot be read or laid out is shown the same way.
- Opening this screen with no file makes `State.load()` create an empty
  `/state/measurements.json` -- one flash write -- because a missing file is
  saved with the defaults it was asked for. **[source-v3]** from
  `modules/common/badgeware/state.py`.

### `room_flicker`

Measures how much the room's lights flicker, as the light sensor sees them.
Mains lighting -- LED drivers, fluorescent tubes -- can flicker at twice the
mains frequency, 100 or 120 Hz, or much faster, and to a link whose slots
are 16-32 ms long that is noise inside the band it signals in. No mirror, no
second badge, no schedule: `run room_flicker [n]` takes `n` measurements
(1 by default), the badge held where a transfer would be. The capture and
the analysis are in
[`devtests/lib/flicker.py`](../../tests/share_wifi/devtests/lib/flicker.py),
a pure module. For each the backlight is **off** and the case lights are
held off -- the badge's own light must not be what is measured -- and each
is saved as it ends, numbered after what the file already holds
(`toolkit.next_key()`), and summarised in the run's `room_flicker.json`.
A cancel during the drift windows keeps the measurement, its `drift`
`{"error": "cancelled"}`.

- **A capture** is 250 ms (`CAPTURE_MS`) of the DMA ring -- 12,500
  readings at 50 kHz, a reading every 20 µs -- read once its last bin is in,
  as the means of 0.5 ms bins (`BIN_US`): two arrays of 500 allocated once
  when the run starts. The ring starts, after the temperature is read, and
  runs on through the drift windows -- one clock for all
  of them -- until the measurement ends; a badge without a ring records
  `{"error": <ring_note()>}` for the measurement (since 2026-10-11: before,
  `light_level()` back to back, ~27 µs a reading). A bin's mean is a sample
  every 0.5 ms, so up to 1 kHz can be seen; an empty bin takes the one
  before's.
- **The analysis**: the mean of the binned series, its standard deviation and
  peak to peak; the amplitude, in counts, of every 5 Hz from 20 to 1,000 Hz
  (Goertzel's algorithm, one multiply-add a bin), and the strongest as
  `peak_hz` and `peak_counts`; and under `hz` the amplitude at every
  harmonic the fit uses -- 1-4 x 50 and 60 Hz: 50, 60, 100, 120, 150, 180,
  200 and 240 -- and the 5th and 6th, reported only: 250, 300 and 360
  (`HARMONIC_HZ`) -- whatever the strongest. `reads` is the readings the
  capture used and `read_us` the time between them: 20.0 on the ring.
  `temp_c` is the chip's temperature, read before the ring starts.
  Averaging 25 readings a bin brings back flicker smaller than an ADC step,
  dithered by the sensor's own noise: 6 counts of 100 Hz in 16-count steps
  comes back as 3-9 in simulation.
- **The fit**: the transfer's own `FlickerFilter.fit()` on the capture, fed
  as a transfer feeds it -- the mean of each 2 ms (`FINE_MS`, four bins)
  every 3 ms (six bins), timed at its middle, span 2,000 µs -- from the
  first 160 ms (the library's `FLICKER_FIT_MS`, 53 samples); the other
  ~90 ms, 30 samples,
  are held out. A window it refuses is fitted
  again on its newest half, as `Transfer.fit_flicker()` does. Under `filter`:
  `verdict` (`"flicker"`, `"steady"` or `null`, refused), `reason` (why it
  refused, else `null`), `window_ms` (the window the
  verdict came from; `reason` is `"few"` under 12 samples, `"unfit"` when
  no mains could be fitted, `"dof"` when the harmonics leave under 4
  degrees of freedom, `"noise"` past `FLICKER_FIT_MAX_NOISE`), `mains` (the
  region it preferred), `noise` (counts
  left unexplained), `harmonics` (each harmonic's size in counts, keyed by
  its Hz, before the significance test) and, for `"flicker"`, `kept` (the Hz
  taken off). Then, on the capture's last ~90 ms, which the fit never saw,
  `held_out` samples' standard deviation before (`sd_before`) and after
  (`sd_after`) `correct()`: the flicker left in a transfer's samples. And
  `window_residual` (Dev Mode M6): 3.5 ms windows every 8 ms over the same part --
  a pair's window on the lit 8 ms rung -- their means' spread before and
  after correction (`windows`, `sd_before`, `sd_after`): whether that rung
  survives a flickering room.
  `FlickerFilter.fit_info` carries the noise, mains and sizes out of `fit()`
  whatever it returns; the transfer does not read it.
- **The drift**: after the capture, 80 more windows of 250 ms
  (`DRIFT_WINDOWS`), `api.cancelled()` checked before each. Each window's
  bins are projected
  on the filter's own sine table (320 steps of 1.125 degrees, which average
  out across the bins) at their time since the first window began, at
  `drift.hz`: twice the fitted mains if the fit found flicker, else the
  stronger of 100 and 120 Hz in `hz`; under 2 counts there
  (`DRIFT_MIN_COUNTS`), `drift` is `{"error": "no mains flicker"}` and no
  windows are taken. The phases are unwrapped against the line through the
  first and latest, and a least-squares line through them all gives
  `offset_mhz_vs_clock`: the mains' offset from nominal, in mHz. `seconds`
  is the time from the first window to the last, `windows` how many,
  `wander_deg` the phases' standard deviation about the line.
  - **Against the badge's own clock, not absolute.** `time.ticks_us()` is
    the RP2350's timer, counting a tick generator run from the crystal
    oscillator **[inferred-all]** from the RP2350 datasheet's clocking; the
    crystal's tolerance on this board is not documented in this repo
    **[unverified]**. A typical ±30 ppm crystal moves 50 or 60 Hz by up to
    0.0018 Hz -- 1.8 mHz -- so a figure that small cannot be told from the
    clock. Two badges agree on the mains only to their crystals.
  - **Range.** Unwrapping holds while the flicker moves under half a cycle
    between the first two windows: a mains offset under 1 / (4 x their
    gap), 0.93 Hz at windows 270 ms apart. Past it the answer aliases and
    looks clean -- 0.95 Hz reads as -0.9 with little wander -- so a window
    landing more than a quarter cycle (`DRIFT_AMBIGUOUS_TURNS`) from where
    the line predicted marks the result `"ambiguous": true`: any offset
    over about half the limit, 0.46 Hz. Grids hold ±0.2 Hz, so an
    ambiguous result is a bad window or a light that is not on the mains,
    not a figure to use.
  - **Ending.** The run ends after `DRIFT_WINDOWS` attempts, read or not, so
    a window with no reading cannot keep it going.
  - **Precision.** For 12 counts of flicker and a 0.3-degree phase scatter a
    window, the line's slope is good to far better than 0.01 Hz; in
    simulation `wander_deg` under 3 with 4 counts of noise. What it
    measures is the mains' own wander over the ~25 s.
- **What a measurement takes**: the capture's quarter second, the analysis
  -- one to two seconds of Goertzel on the v2 badge [inferred-v2] -- and the
  drift: 80 x (250 ms and the window's phase), about 22 s.
- **Storage**: a saved measurement is about 760 bytes of JSON since
  2026-10-14 (it reports the 7th and 8th harmonics of each mains, 350-480
  Hz), so 20 are about 15 KB; with the other tools the file is about
  23 KB, six of the flash's ~256 4 KB blocks, written once a session.
- **What it found** (2026-10-06): every LED light flickered at 120 Hz, by 1%
  of its light in one room, 30-47% in an open area, 0.1% in another
  **[measured-all]**. Enough that the protocol now filters it -- see section 15 of the protocol spec.
- **What it cannot tell**: which light flickers, or whether the sensor sees
  the room or a lamp; hold the badge where a transfer would be. The capture is
  a quarter second, so the spectrum resolves frequencies to about 4 Hz; the
  drift, not the spectrum, gives the mains to the mHz.

**In the measurements file** one numbered entry per measurement, in the
order taken:

    "room_flicker": {
      "1": {"reads": 12500, "read_us": 20.0, "mean": 696.4, "sd": 9.1, "p2p": 32.0,
            "peak_hz": 120, "peak_counts": 12.1, "tethered": true,
            "hz": {"50": 0.3, "60": 0.0, "100": 0.1, "120": 12.1,
                   "150": 0.4, "180": 0.2, "200": 0.1, "240": 3.7,
                   "250": 0.1, "300": 0.2, "350": 0.1, "360": 0.4,
                   "400": 0.1, "420": 0.0, "480": 0.1},
            "filter": {"verdict": "flicker", "reason": null, "window_ms": 160,
                       "mains": 60, "noise": 0.9,
                       "harmonics": {"60": 0.2, "120": 12.3, "180": 0.2, "240": 3.9,
                                     "300": 0.1, "360": 0.4},
                       "kept": [120], "held_out": 30,
                       "sd_before": 8.3, "sd_after": 1.7,
                       "window_residual": {"window_ms": 3.5, "windows": 11,
                                           "sd_before": 3.8, "sd_after": 0.6}},
            "drift": {"hz": 120, "windows": 80, "ambiguous": false, "seconds": 23.8,
                      "offset_mhz_vs_clock": 13.0, "wander_deg": 0.4},
            "temp_c": 24.6},
      "2": {..., "drift": {"error": "no mains flicker"}}
    }

A measurement replaces nothing: it is the next number under
`room_flicker`.

### `fast_loopback`

`devtests/lib/fastloop.py`: `run fast_loopback 0 8 0 defer fit` fits the
room's flicker first (100 ms, the backlight low), then sends eight F0
blocks, each the rung's most, by the app's pacer, decoded by a Follower
`Transfer` through the ring, its flicker terms on if the fit found flicker
-- about 30 s on a white screen; other arguments pick other rungs and
counts (`ARGS`). The result is sent as `fast_loopback.json` and saved under
`fast_loopback`, with `tethered`; `tests/share_wifi/fast_loopback_host.py`
checks either. Put the badge on its mirror in the light to test.

### Autorun over the REPL

A host that launches the app over the REPL -- `tests/usbrun.py`, with the
menu interrupted and the heap clean -- sets `builtins.DEV_AUTORUN` first,
and the app reads it on its first frame (`autorun()`, bare-name lookup):

| `DEV_AUTORUN` | Runs | Quits when |
|---|---|---|
| `"link"` | the USB dev link | the link closes (B); a host ends it with the `reset` subtool, Ctrl-C being off |
| anything else | nothing | at once |

From the menu it is absent, and nothing changes.
`tests/share_wifi/autorun_host.py` drives it (`testing.md`).

### USB dev link

The last Dev Mode item. It opens a screen that owns the USB serial port, so a
host program can list, run and upload tools and their libraries and receive
what they measure.
The host program is `tests/share_wifi/devlink_host.py`; how to use it, and
the hardware checklist, are under "The USB dev link" in
[testing.md](../docs/testing.md). The design and its reviews are summarised here,
and this section is the authority.

**Files.**

| File | What |
|---|---|
| `devlink.py` | the wire format: pure, and imported by the host program too |
| `devserver.py` | the server: sessions, dispatch, the `/devtests` scan, `new`, runs, the subtool `Api`, the instant built-ins. Pure; the port and the badge are handed in |
| `usbport.py` | badge-only, like `fastadc.py`: `sys.stdin.buffer`/`stdout.buffer`, `select.poll`, `micropython.kbd_intr`, and the hard cancel |
| `__init__.py` | the screen, the two built-ins, and the hooks (`LinkHooks`: the ring, the panel, `save()`) |

**The screen.**

| | |
|---|---|
| Title | `USB dev link` |
| Body | `Host: ` waiting (or power only) / connected / gone (no reply to a write) / unplugged; the tool count; the last thing done |
| Help | `B - Exit` |
| B | closes the link: a best-effort `bye`, Ctrl-C back on, then Dev Mode with this item selected. After a run, B is ignored until a frame sees it released, so the press that cancelled a run cannot also close the link |

Opening it turns Ctrl-C off (`kbd_intr(-1)`), throws away whatever was
already queued on stdin, and scans `/devtests/`. Until a `hello` arrives the
server answers every other frame `error no_session`, so a command queued
while the link was closed never runs when it opens. HOME still resets, and
`on_exit()` turns Ctrl-C back on first. While the link is open, mpremote and
MicroPico cannot interrupt the app; B gives the REPL back **[verified-v2]**
(2026-10-08).

**On hardware.** On 2026-10-08 the whole checklist under "The USB dev link"
in [testing.md](../docs/testing.md) passed on the v2 badge **[verified-v2]**, except
three items. On 2026-10-09 the `files` tool -- then a built-in browser on the
badge's screen -- passed every sub-step **[verified-v2]**, including a cable
pull mid-send that left a `.part` on the host. The link as it stands since
2026-10-18 -- instant built-ins, libraries, `api.save()`, the subtools -- has
not run on the badge **[unverified]**. Unplugging during another tool's run (step 8) has not been run on its
own: it is covered in part by step 10's cable pull, which exercised the same VBUS check, lost-host path and host `.part`; the custom-run path itself (`api.cancelled()` seeing VBUS gone) has not been run, so it stays **[unverified]**. The RP2350 errata
check has not been done **[unverified]**.

**Framing.** One frame per line, in both directions:
`\n#DL <crc32, 8 lowercase hex> <json>\n`. The CRC is `binascii.crc32` over
exactly the JSON bytes sent, masked to 32 bits. The JSON is an object whose
`t` is its type. Binary goes in a `b` field as standard base64 with padding.
The leading newline ends any fragment already on the wire, and `#DL ` is also
accepted part way through a line, the text before it being noise. No byte
below 0x20 except the two newlines is ever in a frame. Non-ASCII text is
legal: MicroPython's json writes it as raw UTF-8. A line without `#DL ` is
noise, which the host shows and the badge ignores. A bad CRC or bad JSON is
counted, never answered. The badge accepts lines up to 2,048 bytes and the
host up to 65,536. The badge caps tool descriptions, args and load errors at
120 characters, and log and error text at 1,024 (keeping the end). `PROTO` is
1.

**Requests and replies.** Every host frame carries `rid`, a host counter, and
every reply echoes it. The host drops any reply whose `rid` it is not waiting
for. The host sends one frame and waits for its reply before sending the
next. Frames of a run (`open`, `chunk`, `end`, `log`, `status`) carry the
run's `rid`.

| Host sends | Badge answers |
|---|---|
| `hello` `id` `proto` | `hello`: `id` echoed, `proto`, `app`, `firmware`, `tethered`, `builtins`, `custom`. Starts a session and drops any partial upload |
| `list` | `tools`: `{name, desc, args, ext, builtin, error}` each; `libs`, the library names |
| `run` `tool` `args` | `started` (`hint`), then the run's frames, then exactly one of `result`, `cancelled` or `error` |
| `new` `file` `size` `crc` `force` `lib` | `ack` with seq -1, or `error` |
| `data` `seq` `b` (512 raw bytes at most) | `ack`; after the last, `saved` or `error` |
| `cancel` | **nothing**: the run's last frame is the answer, and with no run it is ignored |
| `remove` `names` | `removed` (`removed`, the names deleted; `errors`, `[name, why]` for each that could not be); no names is `bad_args` |
| `bye` | `bye` |

A stream is `open` (`sid`, `name`, `ext`), then `chunk`s (`sid`, `seq`, `b`,
768 raw bytes at most), then `end` (`sid`, `size`, `crc` of the whole
stream, `ok`). The error codes are `no_session`, `busy`, `unknown_tool`,
`bad_args` (also an empty `new`), `bad_name`, `exists`, `too_big`, `crc`
(a whole upload's), `bad_frame` (an upload chunk out of order or not
base64, or an unknown type), `syntax`, `raised`, `io`, `internal` and
`proto`. `bye` from the badge has `reason`
`"B"`. Anything other than `data` while an upload is pending gets `busy`.

**Removing subtools.** `remove` deletes files from `/devtests/` and
`/devtests/lib/` and nothing else. Each name is checked on its own, and only
looked up, never made into a path: a subtool of that name is deleted, else a
library, else `no such subtool`. One bad name does not stop the rest; the
reply lists both. Each delete is one `os.remove`, the only flash write. The
host asks `Remove <names> from the badge? [y/N]` first, and `--yes` answers
it; after it lists the tools again, so its completion is current.

**Sessions.** The host sends `hello` with a fresh nonce and ignores every
frame until one echoes it, which disposes of v2's stale TX tail.

- A `hello` that arrives during a run cancels it (`by="hello"`) and is
  answered after it. A built-in answers at once, so none is ever running.
- After `no_session` the host says `hello` again and does **not** resend the
  command.

**Subtools.** `/devtests/<name>.py` on the writable LittleFS, and their
libraries, `/devtests/lib/<name>.py`:

- `run(api, args)` is required. `DESCRIPTION`, `ARGS`, `EXT` and `MENU` are
  optional; `MENU = (("label", [args]), ...)` puts each entry in Dev Mode's
  menu, run there with no host.
- The name is `[a-z][a-z0-9_]{0,23}` and must not be a built-in's.
- Each file is executed when the link opens and after a `new`. `list` rescans
  only a file whose size or mtime changed. So **do no work at module level**.
- A run executes the file fresh, with `__name__` set to the stem and `print`
  sent to `api.log`, then calls `run` **blocking** inside one frame, with
  `/devtests/lib` on `sys.path` (the app's folder is already there, so a
  library imports `protocol` as the app does). The libraries a run
  imported are dropped from `sys.modules` after it, so the next run loads a
  new upload and a transfer's heap does not keep them.
- A value it returns is sent as `<name>.json`.
- `Exception` and `SystemExit` are reported as `error raised` with the last
  1 KB of the traceback.

**`new`.**

- The upload is at most 64 KB (`NEW_MAX`), collected in RAM. The name is
  checked first. A file that exists needs `force`. `lib` makes it a library.
- When complete, the whole-file CRC is checked, then the source is
  `compile`d (a `SyntaxError` writes nothing), then -- a subtool, not a
  library -- executed to check it has a callable `run`. The host waits 30 s
  for that last reply (`COMPILE_WAIT_S`).
- It is written to `<name>.py.tmp` and `os.rename`d over the final name
  (atomic on LittleFS **[inferred-all]**; replacing an existing subtool
  works **[verified-v2]**, 2026-10-08). A failed write is `error io` and
  removes the `.tmp`; a stale `.tmp` is removed at scan.
- One `new` is one flash write.

**The `Api`.**

| Member | Does |
|---|---|
| `send(data, ext, name)` | one file: bytes or str as they are, a dict or list as JSON |
| `stream(ext, name)` | `.write()` sends a chunk each time 768 bytes fill; `.close()` sends the rest and `end`. A context manager |
| `log(text)`, `status(text)` | a `log` frame; the status screen redrawn and presented |
| `cancelled()` | True once B is held, the host cancelled, the host is gone or VBUS is. Reads at most 256 bytes and never writes; a frame other than `cancel` or `hello` read here is dropped |
| `save(entry, name)` | `entry` merged into the measurements file under the tool's name, or `name`; True if written. Refused while the ring runs |
| `light()`, `temp()` | the sensor and the chip's temperature |
| `ring_start()`, `ring_stop()`, `ring_note()` | the DMA ring's reader, or None and why |
| `ticks_us`, `ticks_ms`, `backlight(v)`, `present()`, `tethered()`, `name`, `args`, `hooks` (the badge's `LinkHooks`: `temp` and `ring()` raw, for a run that holds the ring itself) | |

A file is saved as `<tool>`, or `<tool>_<name>` when `name` is given. The
name and `ext` (`[a-z0-9]{1,8}`) are checked at the call.

**While the ring runs:**

- `light()` and `temp()` raise `RingBusy`, because all three share the ADC.
- `send`, `stream`, `status` and the server's own writes raise `RingBusy`.
  `Stream.write` checks before its buffer changes.
- `log` (and so `print`) drops the line and reports the count after
  `ring_stop()`.

Every writing call also polls the buttons. Not enforceable, so documented:
writes, `cancelled()` and large allocations belong in the gaps between
captures. Leave about 20 ms after one before a timed capture: a USB write
raises transfer-complete interrupts for a few ms, and a large allocation
rescans v2's block table.

**Ending a run.** The order is pinned, because each step depends on the one
before:

1. Disarm the hard cancel.
2. Stop the ring.
3. Restore the panel.
4. End the open streams, `ok` false unless `run` returned normally and nothing
   cancelled it.
5. Send the last frame.
6. Run the collector.

**The hard cancel.** It is armed for **custom runs only**, around
`run(api, args)`, so even a subtool that never polls can be stopped. Arming
does two things:

- **B:** `BUTTON_B` gets a falling-edge Pin IRQ. Its handler is one-shot
  (`armed = False` first). It attaches a dupterm stream, `CancelTerm`, whose
  `readinto(buf)` writes 0x03 into `buf[0]` and returns `1` once (then
  `None`), and calls `os.dupterm_notify()`. Then it puts back the stream it
  found. The firmware sees 0x03 equal the interrupt character and schedules
  `KeyboardInterrupt` (`extmod/os_dupterm.c`, `py/modio.c`) **[source-v2]
  [source-v3]**. It works on the badge **[verified-v2]** (2026-10-08): B
  stopped a subtool that never polls. A soft IRQ handler cannot raise into the code it
  interrupted, because the scheduler runs it as a protected call. This
  indirect route is the one that can.
- **Host:** `kbd_intr(3)` is set, so the host's raw 0x03 interrupts the
  run directly. The host sends it after its `cancel` frame on every Ctrl-C
  while it waits on a run. Outside a run it waits on, it sends 0x03 only for a
  custom run it stopped waiting for, only after a plain `hello` got no
  reply, and only once the user says yes. Outside an armed run, 0x03 is
  Ctrl-C to whatever is running: the app with its link closed, or the
  launcher after a reset. A new port (a replug or reset) forgets the
  abandoned run. The host's Ctrl-C stopped a subtool that never polls
  **[verified-v2]** (2026-10-08).

Disarming happens in this order:

1. `kbd_intr(-1)`. From here neither route can make a new interrupt.
2. `armed = False`.
3. The IRQ is removed.
4. A 1 ms sleep inside `try`, which takes delivery of an interrupt already on
   its way.

The whole disarm is retried past a `KeyboardInterrupt`, so a second one
cannot escape to `run()`, whose `except Exception` it would pass. The run is
reported `cancelled` `by="B"` if B's handler fired, else `"host"`.

The cancel has limits:

- Native and viper code has no pending checks, so a `fastadc` capture
  finishes first.
- On v2 it also waits for `display.update()` to finish.
- A bare `except:` in a subtool swallows it.

**Built-ins** are two instant commands of the app's, answered inside the
`run` that asks: `started`, the file if any, `result` -- never armed, never
waited on.

| name | does | sends |
|---|---|---|
| `measurements` | reads `/state/measurements.json` | `measurements.json` |
| `clear` | deletes it and every stored dev tool, as *Clear Saved Measurements* does; the host asks y/N first | -- (`result`'s `saved` and `text`) |

**The `files` subtool** gets a file off the badge without the REPL --
`/state` is on the volume disk mode does not show. `files list PATH`
returns a folder's entries (`name/` for a folder, and the size); `files get
PATH...` sends each file 4 KB at a time on a stream, never whole in RAM,
saved by the host as `files_<stem>.<timestamp>.<ext>`: the stem lowercased,
anything but `[a-z0-9]` made `_`, a leading non-letter given an `f`, 24
characters at most; the extension `[a-z0-9]{1,8}` or `bin`. Nothing is
excluded: `/secrets.py`, `/system/secrets.py` and `/state/share_wifi.json`
hold WiFi credentials in clear, and any of them can be sent, to the user's
own host, no more than `mpremote fs cp` could already do.

**`tethered`.** Every measurement subtool records whether USB power was
present (`badge.usb_connected()`, VBUS), and so do `hello` and `result`. A
tethered badge runs from VBUS rather than the battery. On 2026-10-08 a
mirror run on battery and one tethered over the link agreed within the
checklist's threshold, the 2-10 count noise **[measured-v2]**: tethering
does not move the swing beyond that. A tethered figure needs no note of its
own, though the flag stays, in case a later badge or room says otherwise.

**Disconnects.** The firmware facts behind these rules are under "USB serial"
in [badgeware-hardware.md](../docs/badgeware-hardware.md).

- **Unplug:** VBUS goes, and the server stops writing and cancels a custom
  run that checks.
- **A host that holds the port but stops reading:** a write comes back short
  after 500 ms. The host is marked lost, nothing more is written, and
  `cancelled()` is true.
- **A host that closes the port with the cable in:** cannot be detected,
  because writes vanish into an overwritable FIFO. The subtools still save
  to `/state`, so `run measurements` gets the data back later.

Disk mode and the link are never active together: `main.py` launches no app
on a double-tap wake **[source-v2] [source-v3]**.

### Known gaps

Left open for a decision; none is a crash.

- **A badge without a working DMA ring cannot transfer at all** (since
  2026-10-11) -- accepted: every supported firmware, v2.0.2 and later, has
  it, and the slower polled reader that covered for one cost the pair half
  its rate. It says so on screen (see "The transfer screen"). If the ring
  is ever refused on hardware that should have it, `ring_note()` -- shown
  with the refusal and recorded by Dev Mode M1 -- says why.


