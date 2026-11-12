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

M0 (the fix list in [`docs/review-2026-11-11.md`](docs/review-2026-11-11.md), P0 to
P4 and the process section) is closed in code; P0.6's update signing landed as
SSH-signed release tags (`../docs/versioning.md`, "Releases"). M1 and M2 pass on headless and nested river (session lock that
survives a shell kill, calls and the power menu inside it, the keyboard resizing the
app, dock and undock, top bar, Super launcher, keybind sheet, the phone as trackpad).
None of it has run on hardware yet.

1. **Device pass.** Work through "On-device checks" below on the FLX1s and Librem 5
   (phoc), then river on the Librem 5. Debian forky has no river package, so that
   needs river 0.5 built on the device.
2. **Open items.**
   - Signed releases (P0.6): cut the first release with `scripts/release.sh`, then
     on a device run `xx-apps-update --check` and `--apply` against it (needs
     `git` and `ssh-keygen` there).
   - Per-app network blocking (P1.6 rest): persist app ids rather than pid-scoped
     cgroup paths, and show a failed block in the UI.
   - river text-input: a text field that first appears after an app's first frame
     gets no keyboard until focus moves. The fix belongs in river's
     `TextInput.create`; the alternative is xxwm asking xx-keyboard to re-create its
     input method after a tap.
   - xx-radio's player: match the seek's seqnum so a stale ASYNC_DONE from the
     preceding pause can't end a seek early.
   - Tests: a fake GLib leaks between `test_dialer_argv.py` and
     `test_telephony_modem_io.py` when they run back to back; snapshot runs still
     subscribe to logind on the host's system bus.
   - One toplevel manager shared by the window, standby and usage tracking.
   - First boot: a searchable time-zone list.
3. **M3 onwards** need the Pixel; see the design document.

## Ground rules

- **Spec**: `design.md` wins. Where this file and design.md disagree, design.md is right; flag the conflict in the commit message.
- **Style**: Python 3.12+, GTK4/libadwaita via `gi`, match the existing code's idiom. No comments unless the WHY is non-obvious. Text-first UI: no icon grids, no images, monochrome per theme.
- **CSS invariants** (`launcher/src/style.css`): uniform background from the active theme on every surface; children transparent; no borders anywhere except inside `.settings-page`; the configured font applies launcher-wide via `font_theme.apply_global_font` (default JetBrains Mono Nerd — surfaces must not hardcode a family); invisible Paned separators. Don't regress these.
- **Verify before every commit**: `sh scripts/check.sh` (py_compile + ruff + main-loop check + pyright + pytest + cargo + shellcheck). Recreate `.venv` with `--system-site-packages` if it is missing (`python3 -m venv --system-site-packages .venv && .venv/bin/pip install pytest ruff pyright`). A missing tool fails the gate; `XX_CHECK_SKIP="tool ..."` skips one on purpose. If you add a runtime behavior, exercise it — on the laptop (`cd launcher && PYTHONPATH=src python3 src/main.py`) and, once the tablet session is XX-WM, on the tablet.
- **Commits**: one commit per task or coherent group, imperative subject, body says what changed and how it was verified. Never commit `__pycache__`, `build/`, or `devices/*/downloads/`.
- **Config compatibility**: `~/.config/xx-wm/config.json` auto-migrates from `~/.config/piercing-shell` (rename, once, only if the xx-wm dir does not exist). Every schema change needs a silent migration path (missing keys → defaults; never crash on old configs). `docs/config.md` is the public API — update it with every key change.
- **Decisions already made** (don't relitigate): product is **XX-WM** (app id `io.piercingxx.XXWM`, binaries `xx-wm` / `xx-wm-ipc` / `xx-wm-session`); the compositor is phoc on the FLX1s and Librem 5 and river with xxwm (`wm/`) for the Pixel, per `../docs/pixel-9-pro-design.md`; lisgd owns system-level gestures via IPC; keyboard is **xx-keyboard** (built on Stevia; squeekboard stays as the fallback); the lock screen stays ours (no phrog/phosh code); DnD and Focus Mode copy the Pixel's behavior; in-shell Settings is **system-only**; backgrounds are solid colors only; `aura` stays as a Linux-only bonus theme; volume/brightness HUD is in-shell; design.md is current.
- **Minimalism**: the tablet has **1.8 GiB RAM** and a 29 G eMMC. Prefer text over textures. Leave `preload_gesture_apps` off. Do **not** install Waydroid on the tablet.

## On-device checks (reference-fixes + tier2)

Landed in code with headless tests only; each needs a device to confirm.

- [ ] **phoc `-S` shield** on phoc 0.56 and on the FLX1: boot comes up covered, lowers onto the lock (PIN) or shell; kill the Python shell and the crash-restart comes back covered, then locked.
  - FLX1s (2026-11-12, phoc 0.44): passes on a cold boot and on `kill -9` of the shell: phoc logs "Enabling shell mode", grim frames are pure black until `reported shell state UP` (about 3 s), then the PIN lock. Not yet seen: a crash from an unlocked session with apps open (needs the PIN on glass); phoc 0.56.
- [ ] **Phosh after a crash loop** (passes on the pmOS VM with greetd): on the FLX1s (`phosh.service` with the `99-xx-wm.conf` ExecStart override) and the Librem 5 (greetd + phrog), make the shell exit at start (e.g. a `main.py` that exits 3), reboot: after six crashes Phosh comes up on its lock screen, calls work, `journalctl -t xx-wm` names the reason; a restart of the service in the same boot goes straight to Phosh; the next boot is XX-WM.
- [ ] **Lost config with a helper PIN**: with a PIN set, delete (then corrupt) `~/.config/xx-wm/config.json` and restart the shell: the lock comes up first (shield on phoc, nothing shown on river), asks the old PIN, then the wizard runs without a PIN step. Stop the helper from answering (e.g. `chmod -x` its binary) and restart: locked, "Could not check PIN"; restore it and the same keypad unlocks. Boot time with no PIN set grows only by the helper's activation time.
  - FLX1s: not run; it needs the owner's PIN typed on glass. The owner's PIN is a helper record (`pin_hash` is `helper$`, record in `/var/lib/xx-wm-helper/pin/`) and main's startup gate takes it (`startup PIN gate: locked`).
- [x] **pywayland bindings from the stripped `phosh-private.xml`** generate on the device's pywayland and `set_shell_state` reaches phoc (log: `reported shell state UP to phoc`); `deploy.sh` now ships the XML.
  - FLX1s (pywayland 0.4.18, phosh_private v7): `reported shell state UP to phoc` on every start. Loading every cached set at once used to fail at random (output power lost for the run); loads are now serialized.
- [ ] **scrypt time on the SoC**: `VerifyPin` with N = 2^15 stays a few hundred ms (FLX1, Librem 5), not seconds.
  - FLX1s: the helper's own `PinStore` takes 139–140 ms per verify at N = 2^15; a warm `HasPin` round trip over D-Bus is 8–12 ms. Librem 5 still open.
- [ ] **Helper calls under OpenRC** (pmOS/OpenRC with tinydm or greetd): the session is an active elogind session on seat0, the shell has `XDG_SESSION_ID`, `pkcheck` grants `…set-charge-limit` and challenges `…upgrade-system`; steps in `docs/helper.md` "Sessions and polkit".
- [ ] **`SetLockedHint` under elogind** (pmOS/OpenRC): `loginctl show-session` shows `LockedHint=yes` while locked; `loginctl lock-session` shows the lock.
  - FLX1s (systemd-logind): the session shows `LockedHint=yes` while the lock is up. The elogind half needs pmOS/OpenRC.
- [ ] **Lock-screen swipe feel** on glass (drag threshold, fling, keypad reveal).
- [ ] **Suspend waits for lock paint**: resume never flashes the unlocked session.
  - FLX1s cannot suspend through logind (FuriOS `AllowSuspend=no`, `CanSuspend` "no"); Librem 5 or the pmOS VM.
- [ ] **Video keeps the screen on**: Firefox / mpv fullscreen holds the ScreenSaver cookie through phoc's idle-inhibit forwarding; the screen blanks after the player quits.
  - FLX1s: a `org.freedesktop.ScreenSaver.Inhibit` cookie held the panel lit for 95 s past the 60 s blank, and it blanked (backlight 0, output powered off) about 60 s after `UnInhibit`. Still to see: a real player through phoc's idle-inhibit forwarding.
- [ ] **logind `Suspend` without a prompt** from the session (systemd and elogind); a refusal is logged and retried.
  - FLX1s: refused by config with no prompt ("Sleep verb 'suspend' is disabled by config"); automatic suspend now checks `CanSuspend` and stays off there, and Settings hides its row.
- [ ] **Calls, SMS and the power key wake the phone from suspend** (modem ring / SMS IRQ as wake sources on Halium and mainline).
  - Not testable on the FLX1s (no logind suspend).
- [ ] **RTC wake fires the alarm**: with an xx-clock alarm 10 min out, automatic suspend arms `SetWakeAlarm` (check `/sys/class/rtc/rtc0/wakealarm`), the phone wakes a minute early and the alarm rings on time; the wake is cleared after resume.
  - FLX1s: the RTC sits in 2010 (`since_epoch` 529,902,397 s behind the system clock). The helper now writes the wake in RTC time: `SetWakeAlarm(now + 600)` left `wakealarm` at `since_epoch + 600`, `ClearWakeAlarm` emptied it. No suspend here, so the ring itself needs the Librem 5 or the VM.
- [ ] **ModemManager comes back after resume** (post-resume modem check log line shows the modem registered).
  - No resume on the FLX1s. At boot ofono2mm re-registers after the shell starts (`ModemManager went away` once, then the modem is back on the bus: `/org/freedesktop/ModemManager1/Modem/0`, state failed, sim-missing).
- [ ] **USSD / MMI**: `*100#` balance, `*#06#` IMEI sheet, a menu USSD with replies and cancel, `**21*…#` call forwarding handed to the network.
- [ ] **PIN / PUK SIM flow**: locked SIM prompts after unlock, attempts left shown, wrong PIN count drops, PUK + new PIN, "Later" then Settings → Mobile data → SIM PIN reopens it.
- [ ] **Busy / network-error end notices** show briefly (and stay off while GNOME Calls owns calls, back once it quits).
- [ ] **Waiting call during DnD** counts as the repeat caller; the second call rings through.
- [ ] **Session lock on a real river** (Librem 5, then the Pixel): boot with a PIN comes up locked with nothing shown first; a wizard resumed behind the lock and finished within 5 min goes to home without a second PIN prompt; `kill -9` the shell while locked and every output stays locked; the restarted shell takes the lock over; touch swipe and keypad work on the lock surface; a docked monitor gets the plain cover. A real call while locked: the incoming screen shows on the lock surface, Answer keeps the phone locked with the in-call controls there, ending the call (either side) returns to the lock; the power-key long-press menu opens on the lock surface; no notification banner appears while locked.
- [ ] **Session lock on a real river** (Librem 5, then the Pixel): boot with a PIN comes up locked with nothing shown first; `kill -9` the shell while locked and every output stays locked; the restarted shell takes the lock over; touch swipe and keypad work on the lock surface; a docked monitor gets the plain cover. A real call while locked: the incoming screen shows on the lock surface, Answer keeps the phone locked with the in-call controls there, ending the call (either side) returns to the lock; the power-key long-press menu opens on the lock surface; no notification banner appears while locked.
- [ ] **Docked surfaces on a real monitor** (river + xxwm, Librem 5 or laptop): plugging a monitor in shows the top bar there and never on the handset, tiles start below it, unplugging removes it; a tap of a real Super key opens the launcher on the focused monitor and a second tap closes it; Super+/ shows the sheet with the wm.conf overrides; the bar's battery, signal, call and notification state match the phone's; a monitor with fractional scale still matches its bar (connector name from GTK equals xxwm's).
- [x] **FLX1 modem stack**: is it oFono or ModemManager? `busctl --system list | grep -E 'ofono|ModemManager'` — everything above assumes ModemManager.
  - oFono (`ofonod`) behind ofono2mm, which owns `org.freedesktop.ModemManager1`; details in `devices/furiphone-flx1/tier2-device-facts.md`.
- [ ] **Firewall on the FLX1s**: with the helper fixed (PATH, polkit), `SetFirewallEnabled true` as root gives `inet xx_wm` with policy drop plus the keep-ssh rule; a new ssh login still works; false removes the table and the ruleset is as before. Open: the Settings switch with the polkit password prompt on glass, and the Android container: Andromeda's `lxcbr0` DNS/DHCP into the host (the `inet lxc` table accepts udp 53/67 there) would be dropped while the firewall is on, since a drop in any input base chain is final; not yet tried from inside the container. Decide whether the chain accepts those from container bridges.
- [ ] **Shell RSS on the FLX1s**: 258–267 MB RSS (PSS 167 MB) and 27–32 threads with the gl renderer, against the 170 MB / 24 noted before. The shell maps Mesa's libLLVM (45 MB) next to the Mali driver; check whether pointing libglvnd at the hybris EGL vendor only drops it.
- [ ] **One glibc abort on the FLX1s** (preview build, 2026-11-11): `pthread_mutex_lock ... PTHREAD_MUTEX_ERRORCHECK_NP` right after the switcher opened with the toplevel manager unavailable. Not seen on main; watch for it.
