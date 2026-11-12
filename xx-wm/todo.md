# XX-WM — work order

The live work order. It is not a changelog: closed workstreams are in git history
(the tablet cutover and build-out lists that used to be here are at `1b40570` and
earlier). Read `README.md`, `design.md` (the UI contract) and `launcher/README.md`
before touching anything.

## Direction (2026-11-12)

The target is a **Google Pixel 9 Pro** running this shell on Linux, with a docked
desktop on an external monitor. The design, the decisions behind it and the milestones
are in [`../docs/pixel-9-pro-design.md`](../docs/pixel-9-pro-design.md). In short:

- A window-management core in Rust (handset policy, dwindle tiling, keybinds, dock
  behaviour) on an unmodified river compositor; sway is the contingency.
- This shell stays Python/GTK. It gains a compositor backend layer (`phoc`, `river`,
  `sway`), a top bar for the monitor, the launcher as a centered prompt, and a dock
  controller that turns the handset into a trackpad.
- phoc stays supported for the FLX1s and Librem 5.

## Now

1. **Fix list.** [`docs/review-2026-11-11.md`](docs/review-2026-11-11.md), P0 then
   P1. Several P0/P1 items were already closed by the lock, DnD and modem work now on
   `main`; verify each before starting it.
2. **M1 — compositor prototype.** The Rust core on river, run nested on a workstation:
   handset policy, dwindle, the piercing-dots binds. Then this shell on top of it:
   lock, on-screen keyboard resizing the app, layer surfaces.
3. **M2 — docked session.** Top bar, launcher on Super, phone screen as trackpad, on a
   real monitor.
4. **M3 onwards** need the Pixel; see the design document.

## Ground rules

- **Spec**: `design.md` wins. Where this file and design.md disagree, design.md is right; flag the conflict in the commit message.
- **Style**: Python 3.12+, GTK4/libadwaita via `gi`, match the existing code's idiom. No comments unless the WHY is non-obvious. Text-first UI: no icon grids, no images, monochrome per theme.
- **CSS invariants** (`launcher/src/style.css`): uniform background from the active theme on every surface; children transparent; no borders anywhere except inside `.settings-page`; the configured font applies launcher-wide via `font_theme.apply_global_font` (default JetBrains Mono Nerd — surfaces must not hardcode a family); invisible Paned separators. Don't regress these.
- **Verify before every commit**: `sh scripts/check.sh` (py_compile + ruff + pytest + shellcheck). Recreate `.venv` with `--system-site-packages` if it is missing (`python3 -m venv --system-site-packages .venv && .venv/bin/pip install pytest ruff`). Missing shellcheck prints a loud `SKIPPED`; found-but-failing still fails. If you add a runtime behavior, exercise it — on the laptop (`cd launcher && PYTHONPATH=src python3 src/main.py`) and, once the tablet session is XX-WM, on the tablet.
- **Commits**: one commit per task or coherent group, imperative subject, body says what changed and how it was verified. Never commit `__pycache__`, `build/`, or `devices/*/downloads/`.
- **Config compatibility**: `~/.config/xx-wm/config.json` auto-migrates from `~/.config/piercing-shell` (rename, once, only if the xx-wm dir does not exist). Every schema change needs a silent migration path (missing keys → defaults; never crash on old configs). `docs/config.md` is the public API — update it with every key change.
- **Decisions already made** (don't relitigate): product is **XX-WM** (app id `io.piercingxx.XXWM`, binaries `xx-wm` / `xx-wm-ipc` / `xx-wm-session`); the compositor is phoc on the FLX1s and Librem 5 and river with xxwm (`wm/`) for the Pixel, per `../docs/pixel-9-pro-design.md`; lisgd owns system-level gestures via IPC; keyboard is **xx-keyboard** (built on Stevia; squeekboard stays as the fallback); the lock screen stays ours (no phrog/phosh code); DnD and Focus Mode copy the Pixel's behavior; in-shell Settings is **system-only**; backgrounds are solid colors only; `aura` stays as a Linux-only bonus theme; volume/brightness HUD is in-shell; design.md is current.
- **Minimalism**: the tablet has **1.8 GiB RAM** and a 29 G eMMC. Prefer text over textures. Leave `preload_gesture_apps` off. Do **not** install Waydroid on the tablet.

## On-device checks (reference-fixes + tier2)

Landed in code with headless tests only; each needs a device to confirm.

- [ ] **phoc `-S` shield** on phoc 0.56 and on the FLX1: boot comes up covered, lowers onto the lock (PIN) or shell; kill the Python shell and the crash-restart comes back covered, then locked.
- [ ] **Lost config with a helper PIN**: with a PIN set, delete (then corrupt) `~/.config/xx-wm/config.json` and restart the shell: the lock comes up first (shield on phoc, nothing shown on river), asks the old PIN, then the wizard runs without a PIN step. Stop the helper from answering (e.g. `chmod -x` its binary) and restart: locked, "Could not check PIN"; restore it and the same keypad unlocks. Boot time with no PIN set grows only by the helper's activation time.
- [ ] **pywayland bindings from the stripped `phosh-private.xml`** generate on the device's pywayland and `set_shell_state` reaches phoc (log: `reported shell state UP to phoc`); `deploy.sh` now ships the XML.
- [ ] **scrypt time on the SoC**: `VerifyPin` with N = 2^15 stays a few hundred ms (FLX1, Librem 5), not seconds.
- [ ] **`SetLockedHint` under elogind** (pmOS/OpenRC): `loginctl show-session` shows `LockedHint=yes` while locked; `loginctl lock-session` shows the lock.
- [ ] **Lock-screen swipe feel** on glass (drag threshold, fling, keypad reveal).
- [ ] **Suspend waits for lock paint**: resume never flashes the unlocked session.
- [ ] **Video keeps the screen on**: Firefox / mpv fullscreen holds the ScreenSaver cookie through phoc's idle-inhibit forwarding; the screen blanks after the player quits.
- [ ] **logind `Suspend` without a prompt** from the session (systemd and elogind); a refusal is logged and retried.
- [ ] **Calls, SMS and the power key wake the phone from suspend** (modem ring / SMS IRQ as wake sources on Halium and mainline).
- [ ] **RTC wake fires the alarm**: with an xx-clock alarm 10 min out, automatic suspend arms `SetWakeAlarm` (check `/sys/class/rtc/rtc0/wakealarm`), the phone wakes a minute early and the alarm rings on time; the wake is cleared after resume.
- [ ] **ModemManager comes back after resume** (post-resume modem check log line shows the modem registered).
- [ ] **USSD / MMI**: `*100#` balance, `*#06#` IMEI sheet, a menu USSD with replies and cancel, `**21*…#` call forwarding handed to the network.
- [ ] **PIN / PUK SIM flow**: locked SIM prompts after unlock, attempts left shown, wrong PIN count drops, PUK + new PIN, "Later" then Settings → Mobile data → SIM PIN reopens it.
- [ ] **Busy / network-error end notices** show briefly (and stay off while GNOME Calls owns calls, back once it quits).
- [ ] **Waiting call during DnD** counts as the repeat caller; the second call rings through.
- [ ] **Session lock on a real river** (Librem 5, then the Pixel): boot with a PIN comes up locked with nothing shown first; a wizard resumed behind the lock and finished within 5 min goes to home without a second PIN prompt; `kill -9` the shell while locked and every output stays locked; the restarted shell takes the lock over; touch swipe and keypad work on the lock surface; a docked monitor gets the plain cover. A real call while locked: the incoming screen shows on the lock surface, Answer keeps the phone locked with the in-call controls there, ending the call (either side) returns to the lock; the power-key long-press menu opens on the lock surface; no notification banner appears while locked.
- [ ] **Session lock on a real river** (Librem 5, then the Pixel): boot with a PIN comes up locked with nothing shown first; `kill -9` the shell while locked and every output stays locked; the restarted shell takes the lock over; touch swipe and keypad work on the lock surface; a docked monitor gets the plain cover. A real call while locked: the incoming screen shows on the lock surface, Answer keeps the phone locked with the in-call controls there, ending the call (either side) returns to the lock; the power-key long-press menu opens on the lock surface; no notification banner appears while locked.
- [ ] **Docked surfaces on a real monitor** (river + xxwm, Librem 5 or laptop): plugging a monitor in shows the top bar there and never on the handset, tiles start below it, unplugging removes it; a tap of a real Super key opens the launcher on the focused monitor and a second tap closes it; Super+/ shows the sheet with the wm.conf overrides; the bar's battery, signal, call and notification state match the phone's; a monitor with fractional scale still matches its bar (connector name from GTK equals xxwm's).
- [ ] **FLX1 modem stack**: is it oFono or ModemManager? `busctl --system list | grep -E 'ofono|ModemManager'` — everything above assumes ModemManager.
