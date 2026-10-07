# share_wifi

Shares Wi-Fi credentials between two badges over the screen and the light
sensor. The wire protocol -- light levels, framing, calibration, timing -- is
specified in [optical-data-transfer-protocol.md](optical-data-transfer-protocol.md);
this file is everything the app adds on top of it.

Implemented in [firmware/apps/share_wifi/__init__.py](../../firmware/apps/share_wifi/__init__.py).
Confidence markers follow [README.md](README.md).

---

## Roles

- **Share** takes the protocol's Leader role and **Receive** the Follower role.
  The menu choice fixes it before any light is exchanged.

## The transfer screen

**The whole screen is the field, at `LORES`, and nothing else is drawn** --
"Screen as a transmitter" in the protocol is the authority. No instructions,
no progress, no light reading, no `B - Cancel`; B still cancels.

- `begin_transfer()` calls `begin_signalling()`: `badge.mode(LORES | VSYNC)`
  and `badge.default_clear = None`, so `run()` no longer clears the screen
  before each frame. It fills the screen black, times the panel's refresh
  from two presents (`measure_refresh()`, the protocol's `frame_ms`), and
  starts a `Transfer(continuous=True)`.
- **`do_transfer()` is a listen loop, not a frame.** Each pass reads
  `light_level()` once and steps the protocol with it. A change of backlight
  is set there and then, with no present. A change of pixels fills the
  screen and returns to `run()`, whose present shows it -- the only time the
  loop returns before the transfer ends. The buttons are polled every
  `POLL_PASSES` (16) passes and B is checked; the case lights are updated
  then too. `TRANSFER_PASSES` caps the readings one `update()` may take: 0 on
  the badge, no cap; the app's tests set 1, one field a frame. See "Reading
  continuously" in the protocol.
- On the backlight scheme every symbol's pixels are white, so once a
  transfer is on it nothing is presented until it ends. On pixels each
  change of symbol is a present, which blocks reading for up to a refresh
  and a write.
- `finish_transfer()`, the single exit, calls `end_signalling()` -- `HIRES |
  VSYNC` and `default_clear` back to black -- and restores the backlight to
  230/255.

> **Why no text.** The screen used to be two edge bands with the status in a
> centre column between them, so a transfer could say what it was doing. On
> the badge (v2 firmware) drawing that screen took three 60 Hz refreshes a
> frame, 50 ms, which pushes the protocol off its fast rungs and can leave two
> badges on different ones. The text was also the part nobody saw: both
> screens face away from their users for the whole transfer. See "Screen as a
> transmitter" in the protocol for the measurements.

- Progress and errors go to the rear case lights, `badge.caselights()`:

      slow pulse   searching for a peer
      steady on    transfer in progress
      fast blink   error, look at the screen

> Both screens face away from their users for the whole transfer, so the case
> lights are the only output either person can see while the badges are held
> up. At the transfer times under "Throughput" in the protocol they are not
> optional. The result screen is what you read after separating them.

> The backlight is manual PWM, the firmware leaves it at 230/255, and nothing
> else touches it while an app runs -- so the app owns it for the transfer and
> owes the restore.

## Signalling

- `SIGNALLING` in `__init__.py` is the tuple of light-level schemes this badge
  offers, handed to `Transfer(schemes=...)`. The default is both,
  `(SCHEME_FIELDS, SCHEME_BACKLIGHT)`: calibration measures both, and a
  continuous badge takes the backlight whenever it calibrated -- see "Reading
  continuously" and "Choosing a scheme" in the protocol. `(SCHEME_FIELDS,)`
  alone is the original method. Either setting works against a peer on
  either setting.
- Each frame is drawn in the scheme the transfer reports in `transfer.scheme`;
  `FIELD_COLOR` and `FIELD_BACKLIGHT` hold every scheme's pens and backlight
  values, built once at import so switching allocates nothing.

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

- The protocol delivers an **empty payload** as one empty block, which parses as
  a single empty field. `share_wifi` never sends one -- a credential always has
  three fields -- and would reject one received as `ERR_DECODE`, because one
  field is not a credential.

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
- **Signalling is `LORES | VSYNC`** -- a transfer, and Dev Mode's tests --
  entered with `begin_signalling()` and left with `end_signalling()`, which also
  switch `run()`'s clear off and back on. `badge.mode()` rebinds `screen`, so
  nothing keeps a reference to it; every draw reads the global.
- Do not set `DITHER`. Transmitted fields are flat colours and dithering would modulate
  the brightness the peer measures.
- Drive the app with `run(update)`, which clears, calls `update()`, presents and polls
  the buttons — so `update()` must not call `badge.update()`.
- Resolve ROM fonts defensively and fall back to the default rather than raising.
- Menu screens fill by setting `screen.pen` and then `screen.clear()`, in that
  order. `clear()` fills with the current pen.
- A transmitted field is `show_field()` (or `show_full()` for a pen and
  backlight): the whole screen in the field colour, drawn only when it changes.
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

Every screen except the transfer screen and Dev Mode's measuring screen is
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
- **Long lists are paged**, as *display measurements* pages its sections. A page
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
  below -- and in *display measurements*, where it leaves like A and B.
- `BUTTON_HOME` belongs to the launcher and is never bound.
- Case lights: off on menus and results, steady on while connecting or
  transferring, a slow pulse while looking for a peer, a fast blink on a failed
  transfer -- see "The transfer screen". In Dev Mode they **flash** -- full
  brightness, 250 ms on and 250 ms off -- whenever a measurement screen is
  waiting for the user: *Who goes first?*, the *Dark room* and *Bright room*
  prompts, *Done*, and while *No answer from the other badge* is held. They
  are off while anything is being measured.

### Main

| | |
|---|---|
| Title | `Share WiFi` (never paged: at most four items) |
| Items | `Share`, `Receive`, `Connect` -- and `Dev Mode` once revealed |
| Help | `Up/Down - Move Selection   A - Select` |
| A | Share: refresh the network list and open *Share which network?*. Receive: *Line the badges up*, then a transfer as Follower. Connect: refresh the list and open *Connect*. Dev Mode: open *Dev Mode*. |
| C | Reveal `Dev Mode` |

- **`Dev Mode` is hidden at launch.** Pressing C on the main screen appends it to
  the list and moves the selection onto it. It stays until the app exits;
  pressing C again does nothing. It is not remembered across launches.

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

Before every transfer -- Share, Receive, and a retry -- and as Dev Mode's
2nd-badge prompt. HIRES, on black.

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
> [optical-coupling.md](optical-coupling.md) predicts gives two to three times
> the swing. **[inferred-all]**: Dev Mode's 2nd-badge tool measures it, from the
> same hold.

### Transfer

The whole screen is the protocol's field, at `LORES`, with no text -- see
"The transfer screen". The case lights say what is happening.

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
| Message | by exit value, in smoke: `No other badge found. / Point them at each other.` (`ERR_NO_PEER`), `Can't see the other screen. / Move closer, dim the lights.` (`ERR_CONTRAST`), `Lost the other badge.` (`ERR_LOST_PEER`), `Too many errors. / Hold the badges still.` (`ERR_CRC`), `Received data was invalid.` (`ERR_DECODE`), `Cancelled.` (`ERR_CANCELLED`) |
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

Tools for measuring the panel and the light sensor on real hardware, so the
unmeasured and inferred figures in the protocol and hardware notes
can be replaced with measured ones. Reached from the main screen once C has
revealed it.

Implemented, and **not yet run on hardware**: everything below is checked in
CPython against a simulated panel and sensor, whose tau, gamma and noise are
whatever the test told it. The screens are in
[`__init__.py`](../../firmware/apps/share_wifi/__init__.py); the schedule, the
readings and every calculation are in
[`measure.py`](../../firmware/apps/share_wifi/measure.py), a pure module like
`protocol.py` -- no `screen`, no `badge`, the sensor and the microsecond clock
injected -- so it is tested without stubs.

Where this section was silent the implementation chose, and the choices are
recorded below as part of the spec.

### Dev Mode menu

| | |
|---|---|
| Title | `Dev Mode` |
| Items | `measure screen w/ mirror`, `measure screen w/ 2nd badge`, `probe frame timing`, `measure room flicker`, `display measurements`, `clear saved measurements` |
| Help | `A - Select   B - Back` |
| A | open the chosen tool |
| B | main screen, with the selection back on its first item (`Share`); `Dev Mode` stays in the list |
| C | nothing |

- Leaving a tool -- Done, a cancel, or leaving *display measurements* --
  returns here with that tool selected.
- **`clear saved measurements`** deletes `/state/measurements.json` at once,
  with no confirmation, and shows *Done* with the message
  `Measurements cleared` -- the same whether there was a file or not, since
  either way there is none now. Any button returns here with this item
  selected. It deletes with `os.remove()` and then checks with `os.stat()`
  that the file is gone, rather than reading an errno, unproven on the
  badge, or using `State.delete()`, which swallows every error: only a file
  still there afterwards shows `Could not clear`. Opening *display
  measurements* afterwards recreates an empty file (see there).

### What a run measures

Both measurement tools run the same tests, under two conditions -- a **dark**
room, then a **brightly lit** one -- and record one result object per
condition. "The sensor" is the measuring badge's; "the panel" is whichever
screen it is looking at: its own reflection in mirror mode, the other badge's in
2nd-badge mode.

**While a test runs, its field fills the whole screen, with no text** -- no
bands, no centre column, no `B - Cancel` (B still cancels). More of the panel
reaches the sensor, through a mirror or across to the other badge however it
is held, and nothing drawn over the field moves the level being measured. A
badge measuring the other's tests fills its screen with its own field, black,
the same way, so its own text cannot reflect back into its sensor.

**Tests are drawn the way a transfer is**: `LORES`, `run()`'s clear off, the
field redrawn only when it changes (see "The transfer screen"). So
`frame_timing` and the settle captures measure the frames a transfer has.

Outside the tests -- the 3 s start delay, the noise second, the handshake,
waiting for a turn -- the screen is `HIRES`: two edge bands, each a quarter of
the screen wide and in the run's field, with the status in the centre column
between them. The protocol transmitted this way before it went to the whole
screen; the handshake keeps it so the user can read what is happening.

| Test | What is done | What it settles |
|---|---|---|
| `ambient` | Black field, backlight 0.0, for 1.5 s: median of the first 64 reads | the sensor's floor in this light |
| `adc_step` | the greatest common divisor of every reading in the run | `SENSOR_STEP` |
| `levels` | each scheme's black, middle and white (fields: rgb(0, 0, 0), rgb(173, 173, 173) and rgb(255, 255, 255) at 1.0; backlight: white at 0.5 / 0.82 / 1.0) held 1 s each, fields first -- six steps; median of the reads; span, and the middle's share of the span | `MIN_CONTRAST`, the middle field, the backlight scheme's levels |
| `noise` | peak to peak and standard deviation of each held level above | the noise `GAP_SIGMAS` is judged against (calibration measures its own) |
| `gamma_ramp` | grey fields of 0, 64, 128, 171, 191 and 255 in every channel -- 0, 25, 50, 67, 75 and 100% -- at backlight 1.0, 1 s each, median; and the gamma fitted to them | panel gamma |
| `backlight_curve` | white field at backlight 0.0, 0.1 ... 1.0, 0.5 s each, median | whether light is proportional to PWM duty |
| `bleed` | black field at backlight 1.0, then at 0.0, 1 s each, median | whether switching the backlight off deepens black |
| `flicker` | white field at backlight 0.25, 0.5, 0.74 and 0.9, and at 1.0 for reference, 0.5 s each: 256 `light_level()` reads back to back within one frame, half way through each; min, max, standard deviation, and a verdict | whether the sensor averages the backlight's PWM or follows it |
| `settle` | 10 black-to-white, 10 white-to-black and 10 black-to-middle transitions at backlight 1.0, 300 ms apart, reading continuously for 60 ms after each with `time.ticks_us()` timestamps; time constant fitted from the slope of `ln\|final - v\|`, median per direction, and the 90% settling time (2.3 x the slowest) | `LCD_SETTLE_MS` |
| `backlight_step` | white pixels throughout and only the backlight moving: 10 off-to-full, 10 full-to-off and 10 off-to-middle (`MID_BACKLIGHT`, 0.82), 300 ms apart -- from off, not the scheme's dim low, so runs compare. Each captured **around the `display.backlight()` call** -- reading for 2 ms before it, then on for 60 ms -- since the PWM duty is live at once and there is no present to wait for. Per direction, the captures averaged into one curve, and its 10-90% time; on the mirror, also when it crossed 10, 50 and 90% after the call. Every direction also gives the median `swing` its captures saw and how many were `skipped`, by reason |  how fast a backlight change reaches the sensor: the floor on a slot in the backlight scheme, once the panel's settle and the present are out of the way |
| `loopback` | **mirror only.** At each of 50, 32, 25, 20 and 16 ms (`LOOPBACK_RUNGS`), the badge sends `SYMBOL_BLOCK` -- 4 slots of black, a high sync symbol, the 29-symbol de Bruijn sequence -- on its own backlight, white pixels throughout, and decodes its reflection in the same blocking loop as a transfer's continuous receiver would: readings pooled into `FINE_MS` samples, locked on the sync symbol's edge (two lit samples, the first's `T_low` crossing, less `BACKLIGHT_EDGE_LAG_MS`), each slot decided by the protocol's `SlotReceiver` with the backlight's guard, against this run's backlight `levels`. Per rung: `symbols`, `errors`, `lag_ms` -- how long after the badge set the sync symbol's backlight it saw the edge -- and `reads_per_s`. Then at 25 and 16 ms (`DIM_RUNGS`) the same with **low off** -- backlight 0.0 (`DIM_LOW`) for black, 0.80 (`DIM_MID`) for the middle, the scheme before the dim low -- keyed `off_25` and `off_16`, each first holding its three levels for 150 ms apiece (`DIM_HOLD_MS`) to measure them, and reporting them as `levels` | whether the continuous receiver and the backlight carry symbols at the rungs a transfer now starts on, and the edge delay `BACKLIGHT_EDGE_LAG_MS` assumes |
| `frame_timing` | `badge.ticks_delta` over the whole run: frames, mean, min, max, standard deviation. A mirror run is tests from start to end, so every frame is a `LORES` signalling frame; a 2nd-badge run's also include its HIRES noise second and handshakes | the frame time the rung floor and the end-of-slot guard assume |
| `edge_latency` | **mirror only.** For each of the 10 `settle` rises: time from the call that presents the frame to the reading crossing `T_low`; mean, min, max, how many rises, how many were only upper bounds, and how long the present call blocked | the half-frame + `LCD_SETTLE_MS / 10` phase-lock correction |
| `symbol_errors` | **2nd badge only.** See below | whether the fast rungs carry symbols reliably |
| `averaging` | **2nd badge only.** The fields scheme's black, middle and white, 2 s each. Every frame inside the guards, 64 `light_level()`s back to back; the spread of the means of every 1, 4, 16 and 64 of them. Per level its mean, those four spreads, and how many bursts; then the span, the middle's share, and per depth the `separation` -- the narrower gap between adjacent levels in units of the wider spread | whether averaging buys resolution below one ADC step, and so what `MIN_CONTRAST` could be |
| `eye` | **2nd badge only.** See below | where in a slot a symbol can be read, at two and three levels, 50-100 ms |

- **Flicker verdict.** `averages` when at least 90% of the 256 reads at a
  backlight value fall within two `adc_step`s of their median, as they do at
  1.0; `follows_pwm` otherwise. Recorded per backlight value.
- **Symbol errors.** At each of the three fastest rungs -- 50, 65 and 80 ms --
  and in each scheme, the transmitting badge sends 4 slots of black, one high
  sync symbol, and a 29-symbol sequence containing every run of three symbols
  over black, middle and high (a de Bruijn sequence B(3, 3), opened out so
  every triple appears in a straight line). The receiver locks its slot grid
  onto the sync symbol's edge, decides each slot with the protocol's own
  `SlotReceiver` and the levels it measured earlier in the same run, and counts
  errors against the known sequence. Every triple covers every case a symbol is
  decided in: a panel's memory of earlier symbols is below 0.1% after two slots.
- **Averaging.** Spreads are standard deviations of the group means, kept as
  running statistics across the step (Welford's method, so single precision
  holds). If the noise spreads readings over neighbouring ADC steps the spread
  falls as the square root of the depth; if it is narrower than a step it
  stalls, and the mean is the step rather than the level.
- **Eye.** Two sequences in the fields scheme -- `2-level`, a de Bruijn B(2, 4)
  over black and white opened out (19 symbols: every run of four), and
  `3-level`, the B(3, 3) above -- each at 50, 65, 80 and 100 ms, sent like a
  symbol block: 4 black, a high sync symbol, the sequence. The receiver finds
  the sync symbol's edge as `symbol_errors` does, but takes **that edge as
  phase 0** -- no `edge_lag` -- so phase is time since a symbol was first
  seen. Every frame it takes 16 readings back to back and files their mean
  by the symbol showing and by which fifth of its slot the frame fell in.
  Per sequence and rung:
  - `opening`: per fifth, the narrowest gap between adjacent levels' means
    there (the lowest of the upper level less the highest of the lower), in
    counts; negative where they overlap, `null` where a level had no reading.
  - `best_bin`: the fifth with the widest opening.
  - `errors_1` and `errors_avg`: slots decided wrongly from the median of the
    slot's back-half readings, single and 16-reading means: `2-level` split
    half way between black and white, `3-level` at the protocol's
    thresholds.
  Not B(2, 5): its five whites in a row are 500 ms at the 100 ms rung,
  inside the 450-750 ms a badge still answering takes for a go.

### The schedule

A run is a fixed list of steps -- one field held for a fixed time, and what to
read while it is up -- built once at import by `build_schedule()` in
`measure.py`. A transmitter shows it on a fixed timeline and a receiver knows
from the same list when to read.

| Steps | Each | Total |
|---|---|---|
| `ambient` | 1.5 s | 1.5 s |
| `levels`: 2 schemes x 3 levels | 1 s | 6 s |
| `gamma_ramp`: 6 greys | 1 s | 6 s |
| `backlight_curve`: 11 values | 0.5 s | 5.5 s |
| `bleed`: on, off | 1 s | 2 s |
| `flicker`: 5 backlight values | 0.5 s | 2.5 s |
| `settle`: 40 steps | 300 ms | 12 s |
| `backlight_step`: 40 steps | 300 ms | 12 s |
| `loopback`: 5 rungs, **mirror only** | 35 slots + 200 ms | 6.0 s |
| `loopback` low off: 2 rungs, **mirror only** | 450 ms + 35 slots + 200 ms | 2.7 s |
| `symbol_errors`: 2 schemes x 3 rungs, **2nd badge only** | 34 slots | 13.26 s |
| `averaging`: 3 levels, **2nd badge only** | 2 s | 6 s |
| `eye`: 2 sequences x 4 rungs, **2nd badge only** | 24 or 34 slots | 17.11 s |

- **Mirror schedule: 56.2 s. 2nd-badge schedule: 83.87 s.** The two share
  everything up to the backlight step; the mirror then loops back on itself. A 2nd-badge
  *turn* adds its handshake (three call cycles and the go, about 1.7 s when
  the other badge is ready), the 1 s of black after the go, and 1 s of black
  that ends it: about 86.6 s, so a condition -- two turns -- is about three
  minutes. The schedule outlasts `HANDSHAKE_TIMEOUT_MS` (60 s), so a
  badge that missed the go gives up while the other is still sending.
- The 40 `settle` steps are a black to start from, ten white/black pairs --
  a rise and a fall each -- and ten middles with a black between each pair. The
  first black and the nine between middles are not captured: neither is a
  transition the table asks for.
- The 40 `backlight_step` steps are the same pattern on white pixels: white
  with the backlight off, ten full/off pairs, and ten middles (0.74) with an
  off between each pair. Its first step is the only one whose pixels change
  -- from `settle`'s last middle to white -- and it is not captured.
- A symbol block is 34 slots -- 4 black, the sync symbol, 29 sequence symbols
  -- so 1.7, 2.21 and 2.72 s at 50, 65 and 80 ms, fields first.
- **Held steps** take one `light_level()` a frame, ignoring the first and last
  100 ms of the step (`GUARD_MS`): the start is the panel settling and, in
  2nd-badge mode, the receiver's timeline being a frame or two off the
  transmitter's; the end is the same error the other way round. At most 256
  reads a step, 64 for `ambient`; the median is the upper median, so it is
  itself a reading.
- `adc_step` takes the GCD over every read the run makes -- held steps, bursts,
  captures and, in 2nd-badge mode, the noise second and the handshakes' reads. A run
  with no nonzero reading records `{"error": "no nonzero reading"}`, and the
  flicker verdict then falls back to `SENSOR_STEP`.
- **Between and around its fields a badge shows black at backlight 1.0** --
  during the 2nd-badge waits, before and after its own turn -- so the centre
  column's text stays readable there; during the other badge's tests the same
  black fills the whole screen.
- **The case lights stay off while measuring**, from the 3 s delay to the end
  of the condition. Lit, they would light the room the dark run's `ambient`
  and black readings are measuring. They **flash** (250 ms on, 250 ms off)
  when the screen needs the user, who is holding the badge screen-first and
  cannot see it: the role screen, the room prompts, *Done*, and the 3 s a
  timed-out handshake holds *No answer from the other badge* -- by then
  nothing is being measured.
- After each condition, and on a cancel, the backlight goes back to 230/255
  and the case lights off.

### How each result is worked out

- **`settle` time constant** (`settle_tau()`). The final level is the median of
  the capture's last quarter and the starting one the median of its first 16
  reads (a quarter of a short capture). The floor is one ADC step or a
  sixteenth of the swing, whichever is larger, and a capture whose swing is
  under two floors fits nothing. What is fitted is the contiguous run of reads
  just before the residual `|final - v|` first falls to the floor, back to
  where it last stood within half a floor of the whole swing -- the decay
  itself, without the plateau before the edge or the noise after it. At least
  three reads; `tau` is `-1 / slope` of the least-squares line through
  `ln|final - v|` against time. A capture that started late still gives the
  right slope. Each direction is the median of its fitted transitions.
  **Each direction stands alone**: one with no fitted transition records
  `{"error": "no <direction> transition fitted"}` in its own place and the
  others keep their figures -- the first hardware run lost a rise and a fall
  this way, because the middle rise had nothing to fit. `settle_90_ms` is
  2.3 x the slowest direction that fitted, so it can only understate;
  `settle` is a single error only when no direction fitted at all.
- **The capture.** Taken after the frame showing the new field is presented
  (see "Presentation timing"): `light_level()` back to back, each stamped with
  `ticks_us()` relative to the present, for 60 ms. A 2nd-badge receiver starts
  20 ms early and reads for 80 ms, because its timeline is the peer's observed
  edges and so sits on the transition rather than before it. At most 3000
  reads.
- **The backlight step's capture** is made by the app *around* the
  `display.backlight()` call rather than after a present: the app leaves the
  backlight alone when it draws that frame and passes the call to the run
  (`changed()`), which reads for 2 ms (`CHANGE_LEAD_US`), makes the call, and
  reads on for 60 ms. Times are from the call. **Nothing runs between the
  call and the next read**: the lead-in's reads are put on the call's
  timeline only once the capture is over. Done in between, as the first
  version did, it was a few hundred µs of Python blind just where a fast step
  crosses half way, and those captures were dropped as `gap`s. A 2nd-badge receiver, whose
  own backlight does not change, starts its capture **50 ms**
  (`BACKLIGHT_LEAD_MS`) before the step is due: its timeline runs late by the
  frame or two it took to hear the go, which `settle`'s 20 ms lead survives
  only because its fit needs no more than the tail of a slow decay. A
  millisecond step needs the start.
- **`backlight_step` timing.** One capture of a four- or five-step swing is
  mostly quantisation, so each direction's captures are averaged. Each
  capture's starting and final levels are taken as `settle_tau()` takes them;
  one that moved less than two floors is left out. Every read is normalised
  to 0..1 between them and added to 50 µs bins (`CURVE_BIN_US`, `bin_us` in
  the result) from 2 ms before to 20 ms after the capture's origin. On the
  mirror the origin is the call, which every capture shares. A receiver does
  not know when the peer made it, so each of its captures is lined up on its
  own half way crossing -- interpolated, and only after two reads in a row
  have left the starting level by more than a floor -- and it reports no
  crossing times, only the 10-90% time. A capture whose reads either side of
  that crossing are more than 300 µs apart (`CAPTURE_GAP_US`) was interrupted
  there, and is left out: interpolating across the gap would make the edge
  look sharper than it was. The 10, 50 and 90% crossings are the first two
  filled bins in a row at or past the fraction, interpolated from the bin
  before. Simulated through the badge's 16-count steps and noise, ten
  captures land within a quarter of the true 10-90% time from 0.3 to 5 ms;
  a step faster than the reads comes out as a bin or two. `read_us` is the
  median spacing of the captures' reads, which bounds what can be resolved.
- **What a `backlight_step` direction says when it finds little.** `swing`
  is the median of every capture's final less starting level, in counts,
  kept or not -- negative for a fall -- so a direction that found nothing
  still says whether the sensor moved. `skipped` counts the captures left
  out: `flat`, moved less than two floors (or too short to tell); `no_edge`,
  never had two reads running clear of the starting level before crossing
  half way; `gap`, interrupted across that crossing. The first run with this
  test, 2026-10-05c, found no lit fall and only 7 of 10 lit rises, and said
  nothing about why; the reasons were added for the next, whose counts were
  all `gap`s -- the test's own fault (see "The backlight step's capture"). A direction
  with no step kept is `{"error": "no <direction> step found", "swing": ...,
  "skipped": {...}}`; one whose averaged curve never reached 10% or 90% says
  `<direction> curve never reached 90%`. There is no single error for the
  whole test, even when every direction failed, so the reasons survive.
- **`loopback`** blocks the frame it begins on for the block, 35 slots, and
  the step lasts 200 ms more so the next one does not begin inside it; the
  frame after is left out of `frame_timing`. The app runs it on the frame
  `run.loopback_due`, handing over `display.backlight()`. A block whose sync
  edge does not come within two slots of when it was due is `{"error": "no
  sync symbol"}`; with no backlight `levels` this run, `{"error": "no
  levels"}`. `lag_ms` is good to about a millisecond: readings are pooled
  into 2 ms samples before the crossing is interpolated, so in a lit room,
  where the sensor is fastest, it can even read a little negative.
- **The low-off blocks** compare the scheme's dim low with the low it had
  before: the dim low keeps the sensor faintly lit between symbols, so a
  rise starts from a little current rather than none. On 2026-10-06 it cut
  the dark room's edge delay from 1.2-1.9 ms to 0.3, so the protocol took
  it. A block whose measured gaps are under `MIN_GAP` is `{"error": "levels
  too close", "levels": [...]}`.
- **`edge_latency`** uses the 10 rises only. `T_low` is the protocol's, from
  this run's fields-scheme `levels` -- half way between the black and middle
  medians. The crossing is interpolated between the reads either side; a
  capture already past `T_low` at its first read gives that read's time, an
  upper bound, and is counted in `upper_bounds`. When `upper_bounds` equals
  `rises`, every figure is only a bound. `blind_ms` is the median time the
  present call itself took -- see "Presentation timing".
- **`gamma`** is fitted as `L = black + span x f ^ gamma` with `f` = pixel
  value / 255, black and span pinned by the 0 and 255 steps. Each step in
  between gives `ln y = gamma ln f`, `y` being its share of the span, and gamma
  is the least-squares slope of that line through the origin. A step no
  brighter than black is dropped -- its log is undefined, and on a 16-count
  sensor it says only that the step was under one count; one brighter than
  white counts as white. No usable step: `{"error": "no gamma fit"}`.
- **`temperature_c`** is the chip's own temperature, in degrees C to one
  decimal place: at the start of the condition (`start`), on the frame each
  test begins (keyed by the test), and when the results are taken (`end`).
  A 2nd-badge run passes through every test twice; the turn it *sent* is
  keyed `sent_<test>`. The reading is the mean of 16 reads of the RP2350's
  sensor, through the datasheet's line -- 0.706 V at 27 C, falling 1.721 mV
  a degree, with a 3.3 V reference **[datasheet]** -- found as
  `machine.ADC(machine.ADC.CORE_TEMP)`. That name is probed rather than a
  channel number guessed: the sensor is channel 4 on an RP2350A and 8 on an
  RP2350B, where 4 is a pin that would read without complaint. A firmware
  without it records `{"error": "no temperature sensor"}`. **What it is
  for:** the panel's liquid crystal has responded markedly slower in some
  runs than others, and its speed falls as it cools. The die is not the
  panel -- it warms with the CPU's load, and these tests load it -- so the
  reading is a proxy, good for a change across a run rather than the
  panel's own figure. `ADC.CORE_TEMP` exists on the badge's v2 firmware:
  2026-10-06 read 26-28 C **[verified-v2]**.
- **`frame_timing`** leaves out the frame after a capture or a flicker burst,
  which measures the reading rather than the frame loop -- left in, `max_ms`
  would be the capture length. In 2nd-badge mode it is this badge's own frames
  over the whole condition, sending and receiving alike.
- **Symbol blocks.** The search for the sync symbol opens half way through the
  lead-in and closes two slots after the sync was due; it takes the first read
  at or above `T_low` that follows at least a slot and a half of black. The
  slot grid is locked to that edge, interpolated, less the protocol's own edge
  lag -- half a frame plus `LCD_SETTLE_MS / 10`, as `Transfer.edge_lag` -- and
  each slot is then decided by `SlotReceiver`. A slot never decided counts as
  an error. A block still running when the next begins, or when the run ends,
  is finished there, its slot in progress decided.

**Where a failure is recorded.** A failed result is `{"error": "<what
failed>"}`, in place of the narrowest result it spoils:

| Scope | Results | Errors |
|---|---|---|
| per scheme | `levels`, `noise` | `no readings`; `levels` also `white not above black` |
| per scheme and rung | `symbol_errors` | `no levels`, `no sync symbol`, `not received` |
| per rung | `loopback` | `no levels`, `no sync symbol`, `not run` |
| per direction | `settle`'s `rise_tau_ms`, `fall_tau_ms`, `mid_rise_tau_ms` | `no rise transition fitted`, and so on |
| per direction | `backlight_step`'s `rise`, `fall`, `mid_rise`, each keeping its `swing` and `skipped` | `no rise step found`, `rise curve never reached 90%`, and so on |
| the whole test | `ambient`, `adc_step`, `gamma_ramp`, `backlight_curve`, `bleed`, `flicker`, `settle` (when no direction fitted), `frame_timing`, `edge_latency` | e.g. `no readings`, `no readings at 50%`, `no gamma fit`, `no burst at 0.74`, `no transition fitted`, `no frames`, `no levels`, `no rise crossed T_low` |

**Rounding.** Readings, medians, `span` and `p2p` are integer counts, as are
`frame_timing`'s `frames`, `min_ms` and `max_ms` (`ticks_delta` is whole ms).
Standard deviations, time constants, `settle_90_ms`, `mean_ms` and
`edge_latency`'s times and `blind_ms` have 1 decimal place; `rises` and
`upper_bounds` are counts; `mid_share` 3; `gamma` 2. `backlight_step`'s times
are in ms to 2 decimal places, its `read_us` to 1; `transitions`, `bin_us`,
`swing` and the `skipped` figures are counts.

### Presentation timing

`settle` and `edge_latency` time from the call that presents the frame. The app
is driven by `run(update)`, which presents *after* `update()` returns and then
polls and clears before the next one -- `badge.clear()`, `update()`,
`display.update()`, `badge.poll()`. A capture started at the top of the next
`update()` began about **30 ms** after the present on the first hardware run:
`edge_latency` came out 30.2 ms for every rise, the time of the first read,
because every rise had already crossed.

- **On a frame with a capture due** (`run.capture_due`), the app presents the
  frame itself, inside `update()`: it stamps `ticks_us()`, calls
  `display.update()`, and hands both that stamp and the time the call
  returned to `run.presented()`, which reads at once. The firmware's own
  present that follows shows the same frame again -- a frame's cost on each
  of the 30 captured steps.
- **`display.update()` is still blind.** Nothing in Python runs while it
  writes the panel, and an edge can start during it. Its duration is recorded
  as `edge_latency`'s `blind_ms`; a rise that crossed inside it can only be
  bounded, and is counted in `upper_bounds`. "How the constants were measured"
  in the protocol put that blind spot at 22.2 ms. **[unverified]** whether
  presenting early brings the first read inside the rise on hardware.
- Times stay relative to the stamp, so `tau` is unaffected either way: the
  slope of `ln|final - v|` does not depend on when sampling began.
- **`backlight_step` presents nothing first.** On a frame with
  `run.change_due`, the app skips setting the backlight when it draws the
  field -- the pixels are already white -- and calls `run.changed()` with the
  `display.backlight()` call instead, which the run makes inside its capture.
  No present is involved and nothing is blind: a backlight change is a PWM
  duty, live within one ~263 µs PWM period **[source-v2]**. The frame
  `run()` presents afterwards carries no new pixels.

### The handshake

Every 2nd-badge turn starts with a handshake: the badge about to send
**calls** with short pulses until the other **answers** halfway between them,
then says **go**. Nothing is timed against the other badge until both have
seen each other, so neither user has to press A at any particular moment.

| | |
|---|---|
| Call | white for `PULSE_HIGH_MS` (60 ms), then black for 300 ms: a pulse every `PULSE_PERIOD_MS` (360 ms) |
| Answer | one 60 ms white pulse starting `REPLY_DELAY_MS` (180 ms, half a period) after the start of each call pulse the answering badge sees |
| Accepted | an answer whose rise the caller sees 120-270 ms after the start of its own pulse |
| Go | after `REPLIES_NEEDED` (3) accepted answers in a row, the caller shows white for `GO_MS` (600 ms) where its next pulse would have been, then black |
| Time zero | the go pulse's fall. Both badges hold black for `GO_GAP_MS` (1 s), and the sender's test sequence starts 1 s after time zero |
| Give up | `HANDSHAKE_TIMEOUT_MS` (60 s) without a go, either side; the screen then holds 3 s (`NO_ANSWER_HOLD_MS`) so `No answer from the other badge` can be read before the next prompt |

- **A pulse** is a rise of at least the threshold (below) above the baseline --
  the lowest read of the last second, frozen at the rise -- that falls back
  below half the threshold within 20-150 ms. **A go** is the same rise held
  450-750 ms. The rise time is interpolated where the reads cross half the
  threshold, the fall time at the middle of the swing the pulse reached; the
  answering badge corrects the rise for the protocol's edge lag (half a frame
  plus a tenth of `LCD_SETTLE_MS`) before timing its answer from it. The caller
  times answers from the frame it drew its own pulse in.
- **The answerer** answers every call pulse it sees, except one rising within
  -60..+90 ms of its own answer -- its own echo. It accepts a go only after
  three call pulses spaced 270-450 ms apart, the last 270-450 ms before the
  go's rise; time zero is that go's fall. **The caller** counts an answer only
  inside the accepted window -- checking it against the previous pulse too, if
  it is seen after the next pulse has started -- and starts counting again on a
  cycle without one.

> Why the go has a maximum width and the answerer wants the calls before it:
> with only "longer than three pulse widths", the settle test's 300 ms whites
> and every 1 s level step pass for a go, and the symbol blocks hold a ten-slot
> run that is never black, 500-800 ms, with single high slots that look like
> pulses. A badge that missed the real go would then measure the other's
> schedule out of step and report success.

> Why halfway: face to face, a badge's own pulse can reflect off the other
> badge's glass into its own sensor. That echo arrives with the pulse, 0-90 ms
> into the cycle with the panel's settling, while a real answer arrives at
> 180 ms plus a frame or two of latency each side -- so the window cannot
> mistake one for the other, and it ends before the caller's next pulse at
> 360 ms. 60 ms is about five frames: enough for the panel to settle (at most
> 22 ms measured) and leave several reads at full height. Three answers in a
> row rather than one, so a noise spike cannot start a run. A go, rather than
> simply stopping, so the answerer never has to judge silence by a timeout;
> its fall is a single sharp edge both badges time.

- **The threshold** comes from the badge's **noise second**: each run opens
  with a second of black, the centre column showing `Hold still`, taking a
  read every frame. A pulse must rise **half again that second's noise, and
  never less than 48 counts** (three ADC steps). The noise is **twice the
  spread from the lowest read to the median**: for noise spread evenly about a
  level that is its peak-to-peak, and the other badge's call pulses -- a sixth
  of the time, plus the panel settling either side -- cannot reach the median
  if it started calling first. With fewer than eight reads the threshold is
  48. It holds for both handshakes of the condition.

> An 80th percentile was tried first and was not robust: with three call
> pulses and their settling in the second it landed on a lit read, and set a
> threshold of 1248 on a simulated 4000-count swing -- harmless there, above
> the signal on a weaker panel. With the median it stayed at 48 across badges
> started 0.5-30 s apart in either order, on 4000- and 80-count swings.

> Not the protocol's fixed floor (`MIN_CONTRAST`, 192, since replaced): the
> measured panel swings 80, and a detector holding to 192 would never see a
> pulse. Half again
> the noise is the ratio 48 has to the 32-count noise measured on hardware.
> The noise second comes after the 3 s delay, not during it: the hand that
> pressed A may still be moving the badge then.

> This replaced a one-way start mark that the second badge had to catch: it
> worked only if the second badge was started within ~1.3 s of the first,
> needed the first to hold back its mark and wait for the other's turn to end
> at computed times, and at its first rhythm was indistinguishable from the
> settle test. A call pulse every 360 ms at a sixth duty appears nowhere in the
> schedule.

### measure screen w/ mirror

The badge measures its own screen in a mirror.

1. **Prompt:** `Dark room` title; message `Turn the lights off. Hold the badge
   screen-first against a mirror.`; help `A - Start   B - Cancel`.
2. **A**, then a 3 s delay, then the tests in the order of the table above.
   During the delay the centre column shows `Starting` and, beneath it, the
   condition's title (`Dark room`), with help `B - Cancel`. Then the tests
   run with each field filling the whole screen and no text: a mirror run is
   tests from start to end, so no progress is shown until the next prompt.
   B still cancels.
3. **Prompt:** `Bright room` title; message `Turn the lights on. Hold the badge
   against the mirror again.`; help `A - Start   B - Cancel`.
4. **A**, the 3 s delay, and the same tests again.
5. Write the results (see "Measurements file") and show **Done**.

### measure screen w/ 2nd badge

Two badges, both in this tool, held screen to screen. Each measures the other's
screen. They can be started at any time within a minute of each other.

1. **Role:** `Who goes first?` title; message `One badge must choose each.`;
   help `A - I go first   B - I go second`. The choice holds for both
   conditions. There is no Back here: B *is* "I go second", and B at the prompt
   that follows is the way out.
2. **Prompt**: *Line the badges up*, with the condition's title (`Dark room`
   or `Bright room`) above the message in the left half -- the hold a transfer
   uses, so what is measured is a transfer's swing. B cancels, as at every
   prompt. Then **A**, the 3 s delay showing `Starting`, and the run's noise
   second showing `Hold still`.
3. **First turn.** The first badge calls; the second answers (see "The
   handshake"). After the go, the first badge sends every test's field in
   order on a fixed schedule from time zero, then holds black for 1 s. The
   second badge takes each test's readings as that test's field goes up.
   `frame_timing` is each badge's own.
4. **Second turn.** The second badge, knowing from time zero when the first
   badge's schedule and closing black end, then calls; the first badge, which
   has been listening since its own schedule ended, answers; and after the go
   the second badge sends and the first measures.
5. **Both finish** when the second turn's schedule and its closing black end:
   the sender knows it from its own time zero, the measurer from the same go.
6. **Bright room** prompt on both, and steps 3-5 again.
7. Each badge writes its own results and shows **Done**.

- Outside the tests, the centre column carries the phase on its first line --
  `Hold still` (the noise second), `Calling the other badge`,
  `Answering the other badge`, or `Sending` or `Measuring the other badge`
  for the second of black before a turn's first test and after its last --
  and beneath it the
  condition's title. `No answer from the other badge` is added below once a
  handshake has timed out. During the tests themselves, on either badge, the
  whole screen is the field and no text is drawn.
- **A handshake that times out** records `{"error": "no handshake"}` for every
  test the badge had not yet measured in that condition, except
  `frame_timing`, which is its own and is kept, and ends the condition for that
  badge: without a handshake neither turn that remains can be trusted.

### Ending a run

- **B cancels** at any prompt or mid-test, saves nothing, and returns to the
  Dev Mode menu. A cancel at the `Bright room` prompt drops the dark result
  too: nothing is saved unless both conditions finish.
- **A test that fails** -- no handshake, a sync symbol never seen, a step
  with no readings, a transition nothing could be fitted to -- is recorded as
  `{"error": "<what failed>"}` in place of its result, and the run carries on.
  What finished is saved.
- **Done:** title `Done`, message `Measurements saved`, help
  `Any button - Back`; any button returns to the Dev Mode menu.
- **A write that fails** -- an `OSError` from the flash, such as a full
  volume -- shows `Could not save measurements` in place of
  `Measurements saved`, with the same title and help. Nothing of the run is
  kept; the file is as it was. Any other error while saving shows the same
  message rather than reaching `run()`, which would reset the badge.

> The app writes the file itself, with `open()` and `json.dumps()`, rather
> than through `State.save()`: that catches `OSError` and returns quietly
> whenever `/state` exists, so a failed write would read as a saved one.
> **[source-v3]** from `modules/common/badgeware/state.py`.

### probe frame timing

Finds out where a frame's time goes on this badge. It was written when the
first mirror run measured `frame_timing` at a 53.9 ms mean, against the
11.1 ms the hardware notes expected. Its first run answered why:
the v2 firmware's panel runs at 60 Hz, and a HIRES frame that filled the
screen twice and drew two bands and text needed three refreshes. That run is
in [hardware-measurement-2026-10-03.json](hardware-measurement-2026-10-03.json),
and what it found is under "What the hardware runs at" in
[badgeware-hardware.md](badgeware-hardware.md). It needs no mirror and no
second badge.

The steps, and what the badge provides for them, are in
[`probe.py`](../../firmware/apps/share_wifi/probe.py). Like `measure.py`, it
is a pure module: everything that touches the badge (presenting, vsync, the
clear, the poll, the screen's parts, the read loops, the collector) is passed
in through `Hooks`.

1. **Prompt:** `Probe frame timing` title; message `Times the display, each
   part of a measuring frame and how fast the sensor reads. Takes about 25 s;
   the screen flashes and goes dark.`; help `A - Start   B - Back`. B returns
   to the Dev Mode menu.
2. **A** runs the eight steps in order, one per frame. Before each, the screen
   shows `Probe frame timing`, then `<step> n/8` (for example
   `Refresh rate 1/8`) and `B - Cancel`, and presents that frame itself. The
   step then blocks until it ends, so this is what stays on screen while it
   runs. After each step the backlight goes back to normal, so the next
   step's status can be read.
3. The results are written under `frame_probe` (see "Measurements file") and
   **Done** is shown, as at the end of a run, including
   `Could not save measurements`.

| Step | What is done | What it settles |
|---|---|---|
| `refresh` | 60 back-to-back `display.update()`s with vsync on, after one to line up; the intervals, and `hz` = 1000 / mean | the panel's real refresh rate: 16.7 ms is 60 Hz, 11.1 ms is 90 Hz |
| `write` | the same with `display.set_vsync(False)`, then vsync back on | what writing one frame costs on its own; about 7 ms is expected |
| `breakdown` | 60 frames of each of two screens, drawn the way `run()` draws them: `clear` (`badge.clear()`), the screen's parts, `present`, `poll`. **`field`** is the signalling frame -- a transfer's, or a Dev Mode test's -- at LORES with `run()`'s clear off, its parts `field` (backlight and a full-screen fill) and `measure`. It fills every frame, so it is the frame that changes the field; one that does not skips `field`. **`status`** is Dev Mode's HIRES handshake screen: `bands` (black clear and two bands), `column` (the text), `lights` (backlight and case lights), `measure`. `measure` is `MirrorRun.frame()` and the status update, from a fresh `MirrorRun` per screen. Each part's mean and max, and the frame's stats. Then 8 more frames of each, untimed, for `alloc_bytes`: the mean heap growth per frame, where `gc.mem_alloc` exists | which part of a frame costs the time, and how much it allocates |
| `reads` | four loops run back to back with **nothing presented**, each until 5,000 passes (`READ_CALLS`) or 1 s (`READ_MS`), whichever is first, timing every pass with `time.ticks_us()`: **`loop`** does nothing, so it is the loop's own cost (the clock read and the bookkeeping) to take off the others; **`read`** is one `badge.light_level()`; **`sample`** is `sample_light()`, the median of three reads every transfer frame takes; **`listen`** is a whole pass of a listener that never presents -- `time.ticks_ms()`, `sample_light()`, a data-phase `SlotReceiver.feed()` on the 50 ms rung (levels 32/64/112, a 17 ms frame), `badge.poll()` and `badge.pressed(BUTTON_B)`; **`transfer`** is a pass of the transfer's own listen loop -- `read_sample()`, the sensor read back to back for 2 ms, its mean handed to a continuous `Transfer`'s `sample()` listening in the data phase on the backlight's 32 ms rung, the buttons every `POLL_PASSES` -- with its silence budgets reset each pass, since nobody is sending; **`transfer_timed`** is the same pass on a second such listener whose `step()` is timed, and **`detail`** -- not a loop -- is where that time went: `sample`, the protocol's call, and its parts `filter_sample`, `pool_coarse` and `step_data`, each timed; `reads_per_sample` and `read_us`, how many readings a sample held and how long each took in `read_sample()`'s loop; and the listener's mean `light`, its `phase` at the end and the `flicker` it learnt. Each loop gives `calls`, `per_s`, and the pass's `mean_us`, `max_us` and `sd_us` | how many readings a receiver could take per slot if it held its screen static instead of presenting every frame -- a present blocks reading for ~12.3 ms of each 16.7 ms frame on v2 -- and whether anything (a collection, the receiver's growing lists) stalls such a loop: its `max_us` |
| `fast_reads` | the sensor read straight from the ADC's registers, in [`fastadc.py`](../../firmware/apps/share_wifi/fastadc.py), 4,096 raw 12-bit readings (`FAST_READS`) per capture, three captures: **`viper`**, a `@micropython.viper` loop starting each conversion and waiting for it; **`dma_500k`** and **`dma_100k`**, the ADC converting free-running at 500k and 100k a second into its FIFO and a `rp2.DMA` channel moving each result to a buffer. Each first checks that the ADC's control register holds the sensor's input (3: GPIO43) and that its result register matches a `badge.light_level()` just taken, and refuses otherwise -- every address is from the Pico SDK's RP2350 headers, and a wrong one would fault. `fastadc` is imported by the first capture, so a firmware without viper fails the step, not the app; a DMA capture puts the ADC back as it found it. Each gives `reads`, `us`, `per_s`, `mean` in `light_level()` counts, and `noise`: for 1, 4, 16, 64 and 256 readings, the standard deviation of the means of that many consecutive readings, in counts | how fast the sensor can be read at all -- a `badge.light_level()` call costs ~14 µs over its loop, a conversion 2 µs -- and whether averaging many readings beats the noise: by the square root of their number if it is independent, not at all if it is in the light or the sensor. Together, how short a slot could be |
| `alloc` | 1,000 calls each of a function returning nothing, a 2-tuple, a 4-tuple and an 8-tuple, built from the function's arguments so they really allocate; µs per call, with the bare call subtracted from the tuples | whether allocations bigger than one GC block are the slow kind |
| `flicker_filter` | the protocol's `FlickerFilter` (handed in as `Hooks.flicker_filter`) on a synthetic room, made before anything is timed: 2 ms means every 3 ms, as a transfer keeps them, of 700 counts flickering 12 at 120 Hz and 4 at 240, with noise of about 4 from a fixed generator -- the shape of the 2026-10-06 room that flickered 12.5 counts. **`fit_first_us`**, one fit of the Leader's 200 ms window (66 samples), timed on its own because the first fit since launch also fills the protocol's gain tables; then **`fit`**, three more of it, and **`fit_follower`**, three of the Follower's 100 ms (33), each on a fresh filter; `verdict`, the first fit's. Then, on the first filter, as a transfer goes on from its fit: **`correct_first_us`**, the first correction, which builds the table for its span; **`correct`**, 500 more; **`track`**, a phase-lock point, and the updates after each 8: **`update`** for those under 0.3 s from the fit, and **`update_rate`** for those 0.7 s on, by when the rate's line spans the protocol's `FLICKER_RATE_MIN_S` and every update fits it. Each of the last five gives `calls`, `per_s`, `mean_us`, `max_us`, `sd_us`; `samples` and `follower_samples` say how many the fits had | what the filter costs on the badge: the simulator guesses the fit stalls a staircase step by 150 ms, the unix port took 3.3 ms, and badge protocol work has run up to 100 times slower |
| `gc` | three timed `gc.collect()`s; heap in use and free (KiB); then 120 `status` frames with the collector on, and 120 with it off (`gc.disable()`, always followed by `gc.enable()`) | what a collection costs, and whether the long frames are collections |

- **`temperature_c`** reads the chip's temperature before each step and at
  the end, as the measuring tools do (see "How each result is worked out").
- **`firmware`** records `os.uname().version` (the build) and
  `set_framerate`, which is whether `display` has that method at all. A badge
  that reports `false` runs its panel at the power-on 60 Hz.
- **The frame time in `breakdown` and `gc`** runs from the start of one frame
  to the start of the next, timed with `time.ticks_us()` rather than
  `ticks_delta`, so it has sub-millisecond resolution. The first frame of a
  screen starts part way through a refresh and can run short or long.
- **Allocation is counted in frames of its own.** `gc.mem_alloc()` walks the
  whole heap's block table: about 110 ms a call on the v2 firmware. The
  probe's first version called it twice inside every timed frame, and its
  frame times (267 ms) were the probe's own; see the measurement file's
  `error` entries.
- **A step that raises** is recorded as `{"error": "<Type>: <message>"}`, and
  the probe moves on. A firmware without `set_vsync` gets
  `{"error": "no set_vsync"}` for `write`. Vsync and the collector are always
  turned back on.
- **B** is checked after every frame of `breakdown` and `gc`, after each of
  the `reads` loops and `fast_reads` captures, and between steps. It cancels: nothing is saved, the panel is restored, and the screen
  returns to the Dev Mode menu with this tool selected. `refresh`, `write` and
  `alloc` take about a second each and are not checked part way through.
- **Not a measurement of the panel:** what is on screen during `refresh` and
  `write` is the status frame, and the breakdown's fields start with
  `ambient`'s black at backlight 0.0. The sensor plays no part.

### Measurements file

`/state/measurements.json` -- the name `measurements`, as `State` would keep it. One
object per tool; a run replaces only its own, so a mirror run never touches the
2nd-badge results and the other way round. Written once, when a run ends: the
file is read with `State.load()`, the run's object merged in with
`merge_results()`, and the whole file written by `write_measurements()`, which
creates `/state` if it is missing and lets `OSError` through (see "Ending a
run"). `State.load()` of a missing file saves the defaults it was given, so
the first run on a badge costs two writes.

    {
      "mirror": {
        "dark": { <tests> },
        "lit":  { <tests> }
      },
      "second_badge": {
        "role": "first" | "second",
        "dark": { <tests> },
        "lit":  { <tests> }
      },
      "frame_probe": { <probe> }
    }

Each `<tests>` object has one key per test in the table above, named as there.
Readings are raw `light_level()` counts, times in ms, gamma and shares as
decimals, rounded as under "How each result is worked out":

    "ambient": 32,
    "adc_step": 16,
    "levels": {
      "fields":    {"black": 32, "mid": 64, "white": 112, "span": 80, "mid_share": 0.4},
      "backlight": {"black": 16, "mid": 64, "white": 112, "span": 96, "mid_share": 0.5}
    },
    "noise": {
      "fields":    {"black": {"p2p": 32, "sd": 9.1}, "mid": {...}, "white": {...}},
      "backlight": {...}
    },
    "gamma_ramp": {"0": 32, "25": 32, "50": 48, "67": 64, "75": 80, "100": 112, "gamma": 2.32},
    "backlight_curve": {"0.0": 16, "0.1": 32, ..., "1.0": 112},
    "bleed": {"black_on": 32, "black_off": 16},
    "flicker": {
      "0.25": {"min": 16, "max": 112, "sd": 41.0, "verdict": "follows_pwm"},
      ...,
      "1.0":  {"min": 96, "max": 112, "sd": 6.2, "verdict": "averages"}
    },
    "settle": {"rise_tau_ms": 4.1, "fall_tau_ms": 3.8,
               "mid_rise_tau_ms": {"error": "no mid rise transition fitted"},
               "settle_90_ms": 9.4},
    "backlight_step": {
      "rise":     {"transitions": 10, "t10_90_ms": 0.66, "t10_ms": 0.07, "t50_ms": 0.21, "t90_ms": 0.73,
                   "swing": 80, "skipped": {"flat": 0, "no_edge": 0, "gap": 0}},
      "fall":     {...},
      "mid_rise": {"error": "no mid rise step found", "swing": 16,
                   "skipped": {"flat": 10, "no_edge": 0, "gap": 0}},
      "bin_us": 50, "read_us": 52.4
    },
    "frame_timing": {"frames": 2400, "mean_ms": 11.1, "min_ms": 9, "max_ms": 13, "sd_ms": 0.6},
    "loopback": {"50": {"symbols": 29, "errors": 0, "lag_ms": 2.3, "reads_per_s": 2100},
                 "32": {...}, "25": {...}, "20": {...}, "16": {"error": "no sync symbol"},
                 "off_25": {"symbols": 29, "errors": 0, "lag_ms": 1.6, "reads_per_s": 7700,
                            "levels": [32, 64, 112]},
                 "off_16": {...}},
    "edge_latency": {"mean_ms": 7.2, "min_ms": 2.4, "max_ms": 13.1,
                     "rises": 10, "upper_bounds": 3, "blind_ms": 21.6},
    "temperature_c": {"start": 24.3, "ambient": 24.3, "levels": 24.4, ..., "end": 25.1},
    "symbol_errors": {
      "fields":    {"50": {"symbols": 29, "errors": 0}, "65": {...}, "80": {...}},
      "backlight": {...}
    },
    "averaging": {
      "black": {"mean": 40.3, "sd": {"1": 6.1, "4": 3.0, "16": 1.6, "64": 0.8}, "bursts": 108},
      "mid":   {...}, "white": {...},
      "span": 72.4, "mid_share": 0.45,
      "separation": {"1": 5.3, "4": 10.7, "16": 20.1, "64": 40.2}
    },
    "eye": {
      "2-level": {"50": {"symbols": 19, "errors_1": 0, "errors_avg": 0,
                         "opening": [-12.0, 35.5, 52.1, 55.0, 41.2], "best_bin": 3},
                  "65": {...}, "80": {...}, "100": {...}},
      "3-level": {...}
    }

The values above illustrate the format only. A failed test holds
`{"error": "..."}` instead. `loopback` and `edge_latency` appear only under `mirror`, and
`symbol_errors`, `averaging` and `eye` only under `second_badge`.

`<probe>` has one key per step of *probe frame timing*, plus `firmware` and
`temperature_c`.
Times are in ms to two decimals, except `reads`, `fast_reads`, `alloc` and
`flicker_filter`, which are in µs:

    "firmware": {"version": "<os.uname().version>", "set_framerate": false},
    "refresh": {"frames": 60, "mean_ms": 16.67, "min_ms": 16.6, "max_ms": 16.74, "sd_ms": 0.03, "hz": 60.0},
    "write":   {"frames": 60, "mean_ms": 7.02, ...},
    "breakdown": {
      "field":  {"frame": {<as refresh, less hz>}, "clear": {"mean_ms": 0.02, "max_ms": 0.05},
                 "field": {...}, "measure": {...},
                 "present": {...}, "poll": {...}, "alloc_bytes": 412},
      "status": {"frame": {...}, "clear": {...}, "bands": {...}, "column": {...},
                 "lights": {...}, "measure": {...}, "present": {...}, "poll": {...},
                 "alloc_bytes": 1890}
    },
    "reads": {"loop":   {"calls": 5000, "per_s": 52000, "mean_us": 19.2, "max_us": 61, "sd_us": 2.1},
              "read":   {...}, "sample": {...},
              "listen": {"calls": 3100, "per_s": 3100, "mean_us": 322.5, "max_us": 9400, "sd_us": 410.0},
              "transfer": {...}, "transfer_timed": {...},
              "detail": {"sample": {"calls": 330, "per_s": 1100, "mean_us": 905.0, "max_us": 2900, "sd_us": 310.0},
                         "filter_sample": {...}, "pool_coarse": {...}, "step_data": {...},
                         "reads_per_sample": 31.0, "read_us": 64.5,
                         "light": 48, "phase": 5, "flicker": 0.0}},
    "fast_reads": {"viper": {"reads": 4096, "us": 9830, "per_s": 416700, "mean": 52.3,
                             "noise": {"1": 9.8, "4": 5.1, "16": 2.6, "64": 1.4, "256": 0.9}},
                   "dma_500k": {...},
                   "dma_100k": {"error": "RuntimeError: DMA did not finish in 90 ms"}},
    "alloc": {"calls": 1000, "call_us": 6.1, "pair_us": 3.2, "quad_us": 31.0, "octet_us": 35.5},
    "flicker_filter": {"samples": 66, "follower_samples": 33, "fit_first_us": 1813,
                       "verdict": "flicker",
                       "fit": {"calls": 3, "per_s": 688, "mean_us": 1453.7, "max_us": 1478, "sd_us": 18.6},
                       "fit_follower": {...}, "correct_first_us": 327, "correct": {...},
                       "track": {...}, "update": {...}, "update_rate": {...}},
    "gc": {"collect": {"mean_ms": 95.0, "max_ms": 101.0}, "alloc_kb": 210, "free_kb": 7900,
           "screen": "status", "frames_on": {<as refresh, less hz>}, "frames_off": {...}},
    "temperature_c": {"refresh": 24.8, "write": 24.8, ..., "gc": 25.3, "end": 25.4}

A cancelled probe writes nothing. A *probe frame timing* run replaces only
`frame_probe`.


### display measurements

Shows everything in the file, one section per screen, in a form that can be
typed into a file like `hardware-measurement-2026-09-23.json` by hand.

    +--------------------------------------+
    |  mirror.dark.levels.fields    3/41   |   title: the section's JSON path, and the page
    |                                      |
    |  black: 32                           |   one "key: value" line per value,
    |  mid: 64                             |   in the file's own order
    |  white: 112                          |
    |  span: 80                            |
    |  mid_share: 0.4                      |
    |                                      |
    |  Up/Down - Page   A/B/C - Exit       |
    +--------------------------------------+

- **A section** is the plain values of one object -- for example
  `mirror.dark.levels.fields` or `second_badge.lit.frame_timing`. Its title is
  its full JSON path, so the line `black: 32` under `mirror.dark.levels.fields`
  is `"mirror": {"dark": {"levels": {"fields": {"black": 32}}}}` in the file.
- **An object holding both plain values and objects** gets a section of its own
  for the plain values, ahead of its children's: `mirror.dark` holds `ambient`
  and `adc_step` beside `levels`, and `second_badge` holds `role`. Plain values
  at the top of the file would be titled `measurements`.
- **"The file's own order" is rebuilt**, because MicroPython's `dict` keeps no
  insertion order and `State` hands back a fresh one anyway: numeric keys by
  value (ramp percentages, backlight values, rungs), then the keys this spec
  names in the order it names them, then anything else alphabetically.
- Values are shown as JSON writes them, by `measure.py`'s own formatter:
  numbers as numbers, strings in double quotes. A failed test is a section
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
  `...dark.symbol_errors.fields.50`.
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

### measure room flicker

Measures how much the room's lights flicker, as the light sensor sees them.
Mains lighting -- LED drivers, fluorescent tubes -- can flicker at twice the
mains frequency, 100 or 120 Hz, or much faster, and to a link whose slots
are 16-32 ms long that is noise inside the band it signals in. Nothing had
measured it. Its own tool, independent of the others: no mirror, no second
badge, no schedule. The capture and the analysis are in
[`flicker.py`](../../firmware/apps/share_wifi/flicker.py), a pure module like
`measure.py`.

| | |
|---|---|
| Title | `Room flicker` |
| Message | before any measurement: `Hold the badge where it will be used, sensor towards the room. A measures for about 30 s.`; after: `<n> measured. Last: <peak> Hz, <size> counts on <mean>` |
| Help | `A - Measure   B - Save & exit` |
| A | take a measurement: the screen shows `Measuring for about 30 s - hold still. The screen goes dark; hold B to stop.`, presented at once; then the backlight goes **off** and the case lights are held off -- the badge's own light must not be what is measured -- for the capture, its analysis and the drift windows, and the backlight returns to 230/255 when they end. As many as wanted; there is no cap |
| B | on this screen: save every measurement taken and show *Done*, then back to the Dev Mode menu on this tool; with none taken, straight back to the menu, nothing written. **During the drift windows**: held as a window ends, stop them; the measurement is kept, its `drift` `{"error": "cancelled"}` |

- **A capture** reads `light_level()` back to back for 250 ms
  (`CAPTURE_MS`), adding each reading to the 0.5 ms bin (`BIN_US`) it falls
  in: two arrays of 500 allocated once when the tool opens, nothing per
  reading. About 9,000 readings at the measured ~27 µs each. A bin's mean is
  a sample every 0.5 ms, so up to 1 kHz can be seen; an empty bin takes the
  one before's.
- **The analysis**: the mean of the binned series, its standard deviation and
  peak to peak; the amplitude, in counts, of every 5 Hz from 20 to 1,000 Hz
  (Goertzel's algorithm, one multiply-add a bin), and the strongest as
  `peak_hz` and `peak_counts`; and under `hz` the amplitude at every
  harmonic the fit uses -- 1-4 x 50 and 60 Hz: 50, 60, 100, 120, 150, 180,
  200 and 240 (`HARMONIC_HZ`) -- whatever the strongest. `read_us` is the mean time a reading
  took. `temp_c` is the chip's temperature, as the other tools take it.
  Averaging ~18 readings a bin brings back flicker smaller than an ADC step,
  dithered by the sensor's own noise: 6 counts of 100 Hz in 16-count steps
  comes back as 3-9 in simulation.
- **The fit**: the transfer's own `FlickerFilter.fit()` on the capture, fed
  as a transfer feeds it -- the mean of each 2 ms (`FINE_MS`, four bins)
  every 3 ms (six bins), timed at its middle, span 2,000 µs -- from the
  first 200 ms (`FLICKER_FIT_MS`, 67 samples). A window it refuses is fitted
  again on its newest half, as `Transfer.fit_flicker()` does. Under `filter`:
  `verdict` (`"flicker"`, `"steady"` or `null`, refused), `reason` (why it
  refused, else `null`), `window_ms` (the window the
  verdict came from; `reason` is `"few"` under 12 samples, `"unfit"` when
  no mains could be fitted, `"dof"` when the harmonics leave under 4
  degrees of freedom, `"noise"` past `FLICKER_FIT_MAX_NOISE`), `mains` (the
  region it preferred), `noise` (counts
  left unexplained), `harmonics` (each harmonic's size in counts, keyed by
  its Hz, before the significance test) and, for `"flicker"`, `kept` (the Hz
  taken off). Then, on the capture's last ~50 ms, which the fit never saw,
  `held_out` samples' standard deviation before (`sd_before`) and after
  (`sd_after`) `correct()`: the flicker left in a transfer's samples.
  `FlickerFilter.fit_info` carries the noise, mains and sizes out of `fit()`
  whatever it returns; the transfer does not read it.
- **The drift**: after the capture, 80 more windows of 250 ms
  (`DRIFT_WINDOWS`), one a frame -- `run()` presents and polls between them,
  so B can stop them, with its clear off. Each window's bins are projected
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
    a window with no reading cannot keep the screen dark.
  - **B is not latched.** `poll()` reads the buttons' GPIO levels once a
    frame and `pressed` is the change since the last poll, on v2
    **[source-v2]** (`picovector/micropython/input.cpp` at `db958ce`) and v3
    **[source-v3]** (`modules/c/input/input.cpp`). A frame here is a 250 ms
    window, so a tap between polls is missed: the screen says *hold B*, and
    the frame checks `held(BUTTON_B)`.
  - **Precision.** For 12 counts of flicker and a 0.3-degree phase scatter a
    window, the line's slope is good to far better than 0.01 Hz; in
    simulation `wander_deg` under 3 with 4 counts of noise. What it
    measures is the mains' own wander over the ~25 s.
- **What a measurement takes**: the capture's quarter second, the analysis
  -- one to two seconds of Goertzel on the v2 badge [inferred-v2] -- and the
  drift: 80 x (250 ms, the window's phase, and a frame of 17-33 ms), about
  25 s. The screen says about 30.
- **Storage**: a saved measurement is about 550 bytes of JSON, so 20 are
  about 11 KB; with the other tools the file is about 21 KB, six of the
  flash's ~256 4 KB blocks, written once a session.
- **What it found** (2026-10-06): every LED light flickered at 120 Hz, by 1%
  of its light in one room, 30-47% in an open area, 0.1% in another
  **[measured-all]**. Enough that the protocol now filters it -- see "Mains
  flicker is filtered" in the protocol.
- **What it cannot tell**: which light flickers, or whether the sensor sees
  the room or a lamp; hold the badge where a transfer would be. The capture is
  a quarter second, so the spectrum resolves frequencies to about 4 Hz; the
  drift, not the spectrum, gives the mains to the mHz.

**In the measurements file** the tool's, one numbered entry per
measurement, in the order taken:

    "room_flicker": {
      "1": {"reads": 9310, "read_us": 26.9, "mean": 696.4, "sd": 9.1, "p2p": 32.0,
            "peak_hz": 120, "peak_counts": 12.1,
            "hz": {"50": 0.3, "60": 0.0, "100": 0.1, "120": 12.1,
                   "150": 0.4, "180": 0.2, "200": 0.1, "240": 3.7},
            "filter": {"verdict": "flicker", "reason": null, "window_ms": 200,
                       "mains": 60, "noise": 0.9,
                       "harmonics": {"60": 0.2, "120": 12.3, "180": 0.2, "240": 3.9},
                       "kept": [120], "held_out": 16,
                       "sd_before": 8.3, "sd_after": 1.7},
            "drift": {"hz": 120, "windows": 80, "ambiguous": false, "seconds": 23.8,
                      "offset_mhz_vs_clock": 13.0, "wander_deg": 0.4},
            "temp_c": 24.6},
      "2": {..., "drift": {"error": "no mains flicker"}}
    }

A *measure room flicker* session saved replaces only `room_flicker`; one
left with no measurement writes nothing.

### Known gaps

Left open for a decision; none is a crash.

- **What a `badge.mode()` switch costs is unmeasured** -- Dev Mode switches
  at every test boundary, and a transfer at its start and end. `LORES`
  signalling itself is measured (2026-10-04): a 3.02 ms fill and 16.8 ms
  frames, one refresh.
- **A SLOW badge costs both badges the 100 ms rung.** Calibration agrees
  the starting rung from both badges' frame-rate classes, so a v2 badge whose
  frames slip to two refreshes no longer splits the pair -- but it halves the
  rate for both. A v2 badge at `LORES` measured FAST: 16.8 ms frames, the
  slowest 34. See "Agreeing a rung" in the protocol.

- **The black-to-middle fit fails on the measured panel.** At the measured
  80-count swing a middle sits one or two ADC steps above black, which leaves
  no read between the floor and the swing to fit, so `mid_rise_tau_ms`
  records `{"error": "no mid rise transition fitted"}`. The rise and fall are
  kept, so `settle_90_ms` comes from those alone and may understate a slower
  middle.
- **`edge_latency` may still be only an upper bound** if a rise crosses inside
  `display.update()` itself -- `upper_bounds` and `blind_ms` will say. See
  "Presentation timing". **[unverified]**
