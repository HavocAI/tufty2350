---
name: verify-badgeware-app
description: Use this skill to actually run and interact with a Badgeware app (anything under BadgeWarePaste/apps/) in the live try.badgewa.re simulator via Playwright MCP, instead of only reasoning about whether the code would work. Activates whenever the user asks to verify, test, validate, or check a badge app "on try.badgewa.re" or "in the simulator", or after writing/editing a new app in BadgeWarePaste/apps/.
---

Static review of a Badgeware app (cross-referencing other apps in this repo,
reading `try-badgeware-verification-findings.md`) is not a substitute for
actually running it. This skill drives the real simulator at
https://try.badgewa.re/index.html with Playwright MCP and reports what
genuinely happened — errors, screenshots, console output — not a prediction.

Full background on why this workaround is needed, and what's already been
confirmed about the live API, lives in
[`try-badgeware-verification-findings.md`](../../../try-badgeware-verification-findings.md)
at the repo root. Read it first if this is the first verification pass in a
session; it documents a confirmed API drift (`rom_font` → `font`) and other
gotchas so you don't rediscover them from scratch.

## The core problem this works around

Every app in `BadgeWarePaste/apps/<name>/` bootstraps with something like:

```python
APP_DIR = "/system/apps/<name>"
import sys, os
os.chdir(APP_DIR)
sys.path.insert(0, APP_DIR)
```

The simulator's "New file" scratch buffer mounts at `/scratch/...`, not at
`/system/apps/<name>/...`. Running an app straight from a scratch file makes
`os.chdir(APP_DIR)` fail with `OSError: 44`, because that directory doesn't
exist in the simulator's filesystem — the app was never actually installed
there. This is not a bug in the app under test; it's just a mismatch between
where the code expects to live and where the scratch buffer actually is.

**The fix:** open one of the simulator's *real* shipped system apps (its
files are genuinely mounted at `/system/apps/<that-app>/...`), overwrite its
in-memory Monaco editor buffer with the app under test, and point that
buffer's own `APP_DIR` line at the *borrowed* real path instead of the app's
eventual real path. Running it then satisfies `os.chdir` for real. This edit
lives only in the browser tab's memory — it never touches disk, the repo, or
any backend, and disappears on reload.

`badge` (`/system/apps/badge/__init__.py`) is a good default app to borrow:
it's small and has no side effects worth preserving during the test. Any
other shipped app under `/system/apps/` works the same way if `badge` is
already open for something else.

## Step-by-step

1. **Navigate** to `https://try.badgewa.re/index.html?file=%2Fsystem%2Fapps%2Fbadge%2F__init__.py`
   (URL-encoded form of `/system/apps/badge/__init__.py`). This opens the real
   `badge` app already mounted at its genuine path — confirm the status bar
   at the bottom of the editor reads `/system/apps/badge/__init__.py`.

2. **Read the app under test** and rewrite only its `APP_DIR` line for this
   test run, e.g.:
   ```
   APP_DIR = "/system/apps/sand_squirrel"   →   APP_DIR = "/system/apps/badge"
   ```
   Do this on a copy of the source string, never edit the real file on disk.

3. **Inject it into the borrowed app's editor buffer** with
   `mcp__playwright__browser_evaluate`, finding the right Monaco model by URI
   suffix and calling `setValue`:
   ```js
   () => {
     const models = window.monaco.editor.getModels();
     const m = models.find(x => x.uri.toString().endsWith('/badge/__init__.py'));
     m.setValue(/* the app-under-test source, APP_DIR rewritten as above */);
     return m.getValue().length; // sanity-check the length matches
   }
   ```
   The file shows as read-only in the UI (it has a lock icon), but that only
   blocks manual keystrokes in the editor widget — `setValue()` on the model
   still works, and Run executes the edited buffer, not the original disk
   content.

   The source string needs proper JS-string escaping (newlines, quotes,
   backslashes). Building it with a real JSON encoder is far less error-prone
   than hand-escaping — e.g. from the Bash tool:
   ```bash
   python -c "
   import json
   with open('BadgeWarePaste/apps/sand_squirrel/__init__.py', encoding='utf-8') as f:
       src = f.read().replace('APP_DIR = \"/system/apps/sand_squirrel\"', 'APP_DIR = \"/system/apps/badge\"')
   print(json.dumps(src))
   "
   ```
   then paste that JSON string directly as the `setValue(...)` argument — a
   JSON string literal is already a valid JS string literal.

4. **Run it.** Find and click the "Run" button (it shows a refresh icon when
   re-running something already loaded), or use whatever click ref the
   current snapshot gives it. Take a screenshot and read
   `mcp__playwright__browser_console_messages` (`level: "error"`) to check
   for exceptions. The Output panel at the bottom right shows Python
   tracebacks directly (e.g. `NameError`, `OSError`) even when nothing hits
   the browser console.

5. **Interact with it** to actually exercise behavior, not just confirm it
   didn't crash on load:
   - Click the 3D badge figure once first to give it keyboard focus.
   - Keyboard mapping: `ArrowLeft` = `BUTTON_A`, `Space` = `BUTTON_B`,
     `ArrowRight` = `BUTTON_C`, `ArrowUp`/`ArrowDown` = `BUTTON_UP`/`BUTTON_DOWN`,
     `Escape` = `BUTTON_HOME`.
   - `mcp__playwright__browser_press_key` only does an immediate tap
     (press+release). To test a **held/long-press** interaction, use
     `mcp__playwright__browser_run_code_unsafe` with
     `page.keyboard.down('Space')` … `page.waitForTimeout(ms)` …
     `page.keyboard.up('Space')` instead.
   - Screenshot after each meaningful interaction and actually look at the
     result (e.g. did a sand pile form with sloped sides, did the HUD label
     change, did the cursor move the right direction) rather than treating
     "no exception" as sufficient proof the feature works.

6. **If something doesn't resolve** (a `NameError` on some global), don't
   assume the local `BadgeWarePaste` checkout is right — it's a snapshot and
   has already been caught drifting from the live API at least once
   (`rom_font` → `font`). Cross-check against the *live* source of a real
   system app that uses the same identifier: open it via the same `?file=`
   URL trick and read `model.getValue()` (no `setValue` needed for reading).
   Fix the app-under-test's real file on disk, then re-inject and re-run to
   confirm the fix.

7. **When done**, click "Stop" in the simulator. If you switched rom_font → 
   font, be sure to switch it back to rom_font. Nothing needs to be
   reverted — the borrowed system app's edit only ever lived in that
   browser tab's memory.

## Guidelines

- Never report an app as "verified" or "working" without having actually run
  it through this flow in the current session — a prior session's
  verification doesn't carry over, and static code review is not
  verification.
- Every fix found this way belongs in the real file under
  `BadgeWarePaste/apps/<name>/`, not just in the throwaway test buffer.
- If a new API mismatch is found beyond `rom_font` → `font`, add it to the
  "Findings" list in `try-badgeware-verification-findings.md` so the next
  session doesn't have to rediscover it.
