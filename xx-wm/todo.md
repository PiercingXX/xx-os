# XX-WM — Smoke work order

You are Skippy, working on **XX-WM**: a minimalist, text-first Wayland shell for Linux phones. Read `README.md` (identity), `design.md` (the UI spec — treat it as the contract), and `launcher/README.md` (code layout) before touching anything.

This file is the live work order. It is not a changelog. Git history holds the closed workstreams. Skippy implements from `docs/build-spec.md` (how); tick boxes here (what). **Start at Workstream 3b.** Workstream 1, 1b, and 2 (except 2.4 GLASS Colemak) are closed. Do not reopen them.

**Sequence:** finish tablet feature-completeness (3b), remaining tablet daily-driver extras (4), then FLX1, then Librem 5. Fairphone 5 is parked. The tablet is up and SSH-able; drive as much as possible over SSH. Operator-at-glass steps are marked **GLASS**.

**Build-out (2026-10-15):** `docs/buildout.md` is the current plan — Waves 1–4 (make it work, UI pass, telephony, performance) run before the device workstreams below. Its findings supersede the "Current state" section where they disagree.

## Ground rules

- **Spec**: `design.md` wins. Where this file and design.md disagree, design.md is right; flag the conflict in the commit message.
- **Style**: Python 3.12+, GTK4/libadwaita via `gi`, match the existing code's idiom. No comments unless the WHY is non-obvious. Text-first UI: no icon grids, no images, monochrome per theme.
- **CSS invariants** (`launcher/src/style.css`): uniform background from the active theme on every surface; children transparent; no borders anywhere except inside `.settings-page`; the configured font applies launcher-wide via `font_theme.apply_global_font` (default JetBrains Mono Nerd — surfaces must not hardcode a family); invisible Paned separators. Don't regress these.
- **Verify before every commit**: `sh scripts/check.sh` (py_compile + ruff + pytest + shellcheck). Recreate `.venv` with `--system-site-packages` if it is missing (`python3 -m venv --system-site-packages .venv && .venv/bin/pip install pytest ruff`). Missing shellcheck prints a loud `SKIPPED`; found-but-failing still fails. If you add a runtime behavior, exercise it — on the laptop (`cd launcher && PYTHONPATH=src python3 src/main.py`) and, once the tablet session is XX-WM, on the tablet.
- **Commits**: one commit per task or coherent group, imperative subject, body says what changed and how it was verified. Never commit `__pycache__`, `build/`, or `devices/*/downloads/`.
- **Config compatibility**: `~/.config/xx-wm/config.json` auto-migrates from `~/.config/piercing-shell` (rename, once, only if the xx-wm dir does not exist). Every schema change needs a silent migration path (missing keys → defaults; never crash on old configs). `docs/config.md` is the public API — update it with every key change.
- **Decisions already made** (don't relitigate): product is **XX-WM** (app id `io.piercingxx.XXWM`, binaries `xx-wm` / `xx-wm-ipc` / `xx-wm-session`); phoc is the compositor; lisgd owns system-level gestures via IPC; keyboard is **squeekboard** with the PiercingXX Colemak layouts; the lock screen stays ours (no phrog/phosh code); DnD and Focus Mode copy the Pixel's behavior; in-shell Settings is **system-only**; backgrounds are solid colors only; `aura` stays as a Linux-only bonus theme; volume/brightness HUD is in-shell; design.md is current.
- **Minimalism**: the tablet has **1.8 GiB RAM** and a 29 G eMMC. Prefer text over textures. Leave `preload_gesture_apps` off. Do **not** install Waydroid on the tablet.

## Current state — 2026-09-06 (night)

XX-WM is the live GDM session on the tablet. 3b **Build** is in-tree: `osk.py` helper + Entry inventory, unresolved-slot compact, lisgd live-rebind from Settings. Recents list real windows. Shade owns notifications — grim of `notify-send` in the shade. JetBrains Mono Nerd is installed (`fc-list`). check.sh: **721 passed, 3 skipped**. Rollback session id: `piercingxx`.

### Glass (operator at the panel)

| Surface | Status |
|---|---|
| Shade over apps | **works** |
| WiFi scan/connect UI | **works** (PSK field had no OSK — fix deployed, needs retest) |
| Settings over apps + Back | **works** |
| Drawer Search → Colemak OSK | **works** |
| Power lock + swipe-to-unlock | worked, then froze (main-loop block), then unfrozen — **retest** |
| Volume hardware key | **works** |
| Recents sheet over an app | **lists Calculator** (grim). Tap / ✕ still GLASS |
| `notify-send` in shade | **works** (grim: Hello from Grok). Swipe-dismiss / lock list still GLASS |
| Brightness slider on shade | deployed always-visible — **retest** |
| Appearance theme presets | on Settings — **retest** |
| Bezel swipe-right vs Files | edge guard deployed — **retest** |
| GDM greeter Colemak (2.4) | **open** (gnome-shell 50 gresource) |

### Code holes that make design.md untrue today

Closed in 3b Build: recents bind v1 + dedicated thread; shade owns Notifications (unique-name compare); `osk.py` helper + Entry inventory test; lisgd live-rebind from Settings; JetBrainsMono Nerd Font is on the tablet (`fc-list`). Remaining holes are GLASS (finger on panel) plus 2.4 greeter Colemak.

---

### Tablet (`dr3k@192.168.1.129`) — cutover snapshot (kept for history)

Live session is XX-WM (`d3fe7ef`+). Touch this boot: `FTSC1000` `/dev/input/event6`. Config is `~/.config/xx-wm`. IPC `$XDG_RUNTIME_DIR/xx-wm.sock`. The table below is the pre-cutover inventory; do not treat `piercing-shell` paths as current.

| Fact | Value |
|---|---|
| OS | PiercingXX Arch (`PRETTY_NAME`), kernel 7.1.4-arch1-1, x86_64, systemd, Python 3.14.6 |
| Memory / disk | 1.8 GiB RAM (~875 MiB available), 29 G eMMC, 10 G free |
| Login | GDM (enabled). AccountsService Session=`xx-wm`. Waiting on greeter login. |
| Session | installed: `/usr/libexec/xx-wm-session` → phoc `-C /usr/share/xx-wm/phoc.ini` `-E /usr/bin/xx-wm`. Rollback desktop id: `piercingxx` |
| Groups | `dr3k` is in `input` and `wheel` |
| Sudo | `/etc/sudoers.d/xx-wm-smoke` (`NOPASSWD: ALL`, 0440). Revoke after smoke |
| Touch | `FTSC1000` on `/dev/input/event3`, `INPUT_PROP_DIRECT`. lisgd already bound to it |
| Panel | DRM `DSI-1`. Installed piercing-shell `phoc.ini` already has `scale = 1.5` |
| Backlight | `intel_backlight` present. **No IIO illuminance node** — Auto-brightness tile must stay hidden |
| Wayland | `WAYLAND_DISPLAY=wayland-0` under `/run/user/1000`. IPC socket is `piercing-shell.sock` |
| Config | `~/.config/piercing-shell/{config.json,gestures.json}` live. **No PIN.** `default_layout_applied: true`. Theme `amoled`, font `jetbrains-mono-nerd` (family **not** installed — `fc-list` has neither JetBrains nor Space Mono) |
| Gestures (json) | design.md defaults, plus custom `swipe_left_home=launch:htop.desktop`, `swipe_right_home=launch:org.gnome.Nautilus.desktop`. Those `launch:` slots are **in-shell** (not lisgd). Live piercing-shell lisgd still uses the old verbs; after 2.5, XX-WM 1.4 mapping applies. Do **not** rewrite the custom home-swipes |
| Stack present | phoc 0.56, gtk4 4.22, libadwaita 1.9, gtk4-layer-shell 1.3, python-gobject, python-pywayland 0.4.18, squeekboard, lisgd (source-built at `/usr/bin/lisgd`, not a pacman package), geoclue, NM, PipeWire, gnome-calculator, neovim, whiptail |
| Stack absent | firefox, waydroid, tailscale, fprintd, wlopm, wlr-randr |
| Repo | `~/xx-wm` rsynced (2.2). `~/piercing-dots` intact; its `install.sh` has **no** `--profile phone` |
| logind | `/etc/systemd/logind.conf.d/10-piercing-power.conf` already ignores power keys |

SSH from this machine works with key auth. Import the graphical session with:

```sh
export XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0
```

`grim` can screenshot. `xx-wm-ipc` will work only after cutover (socket name `xx-wm.sock`).

### Phones

- **FLX1** — next after the tablet is green. Last notes: MediaTek Preloader/BROM, recover via mtkclient. FuriOS (Debian 13 / Halium), Phosh+phoc, working VoLTE. Do not `apt upgrade` held packages until FuriLabs fixes systemd 261-rc3 `systemd-sysusers`.
- **Librem 5** — third. In hand, still Phosh. PureOS, weakest hardware (performance canary). Replace Phosh in place; no flash.
- **Fairphone 5** — parked. Image + `flash.sh` ready; not in this sequence.

---

## Workstream 1 — Preflight (dev machine)

Do this before touching the tablet session. None of these need the phone. Goal: `meson install` produces a bootable shell, `install.sh` can run on Arch, and the first login is not a double-session or a missing-module crash.

- [x] **1.1 Install every runtime module.** `launcher/meson.build` ships `src/*.py` by hand and **omits** `hud.py`, `lock_lines.py`, and `toplevel_manager.py`. A meson install of `main` crashes in `_show_shell` (`from hud import Hud`), then again on lock and switcher. Add the three files. Add a test that the meson `install_data` Python set equals `launcher/src/*.py` plus the vendored `wayland_proto` package so this cannot regress. `scripts/check.sh` py_compile currently globs only `launcher/src/*.py` — also compile `launcher/src/wayland_proto/*.py`.
- [x] **1.2 Arch / pacman in `install.sh`.** The smoke tablet is Arch. The installer currently exits `Unsupported distro: need apk or apt.` Add a `pacman` path: deps (`python-gobject gtk4 libadwaita gtk4-layer-shell meson ninja rsync git squeekboard phoc python-pywayland wl-clipboard geoclue networkmanager`), `pkg_install` that does not hard-fail the menu, and **do not** try to `pacman -S lisgd` (not in Arch repos; already at `/usr/bin/lisgd` on this tablet; on a fresh Arch box print a loud skip with the `~mil/lisgd` build note). Install JetBrains Mono Nerd + Space Mono (pacman/AUR names as available; never fail the whole install on a font). Same font step on apk/apt as far as the distro packages allow.
- [x] **1.3 One shell per session.** `install.sh` `enable_service` always `systemctl --user enable xx-wm`. The user unit `ExecStart=` is `xx-wm` (the Python shell, not phoc). The wayland-session file already starts `xx-wm-session` → phoc → `xx-wm`. Enabling the user unit on a GDM host double-starts the shell. On systemd hosts that install the wayland-session file, **do not** enable the user unit. Keep the user unit installed for the “already inside a compositor” case, but it is opt-in. OpenRC still starts `xx-wm-session` (full session from init) — leave that, but stop hardcoding `/usr/libexec/xx-wm-session`; use the meson-configured libexec path or detect it.
- [x] **1.4 Gesture action names must reach lisgd.** `gestures.json` stores actions (`home`, `app_switcher`, `notification_shade`, `back`, `search`). `gesture_bindings.resolve_verb` only accepts IPC verbs, so every default action falls through to `DEFAULT_VERBS` (`swipe_up_short` → `gesture.keyboard`, `swipe_up_long` → `gesture.home`). The tablet’s live json already says short=home, long=app_switcher — and lisgd is firing keyboard/home instead. Map `home→gesture.home`, `app_switcher→gesture.switcher`, `notification_shade→gesture.shade`, `back→gesture.back`, `search→gesture.keyboard`. Keep explicit IPC verbs as-is. Invalid values still fall back to `DEFAULT_VERBS`. Update `tests/test_gesture_bindings.py` (the “byte-identical to former hardcoded” pin is now wrong for defaults that go through action names). Update `docs/config.md` so the lisgd column matches. **Do not** change the user’s custom `launch:` home-swipe bindings.
- [x] **1.5 `deploy.sh` must update the running shell.** It rsyncs to `${TARGET}:~/xx-wm/src/` and restarts a service. Meson installs to `/usr/share/xx-wm/` and the wrapper execs that path. The documented iterate loop is a no-op on an installed device. Rsync `launcher/src/` (and `style.css`, `wayland_proto/`) to `/usr/share/xx-wm/` (needs root — see 2.1). Restart: `systemctl --user restart xx-wm` only if that unit is the one running the shell; on the tablet GDM path, SIGHUP/kill the `python3 .../main.py` child of phoc, or restart the session. `--dry-run` must print the real paths. Default `XX_WM_USER` for this tablet is `dr3k`, not `user`.
- [x] **1.6 Session naming.** Install success text says “Select the PiercingOS session”. The desktop `Name=` is `XX-WM`. GDM will also still show **PiercingXX**. Make the message name the entry GDM actually lists (`XX-WM`). Do not rename the old piercing-shell session from this repo.
- [x] **1.7 Device prompt must not silently keep an FP5 scale.** Cancelling `select_phoc_scale` leaves the meson-installed default (`DSI-1` scale 2.5). That is wrong for this tablet. Require a selection, or detect `DSI-1` 1200×1920 and prefer `tablet`. The tablet fragment is `launcher/data/phoc/tablet.ini` (`DSI-1` @ 1.5).
- [x] **1.8 Live re-theme fan-out.** `_retheme_surfaces` only walks shade / dialer / call UI / call bar / power menu. HUD, app switcher, lock screen, back-gesture overlay, and the quick-actions panel provider are construction-time correct and then stale. Thread them in. Cover with `tests/test_theme_hot_reload.py`.
- [x] **1.9 Recreate the gate and record the real count.** There is no `.venv` on the laptop and no CI. Recreate it, run `sh scripts/check.sh`, put the actual pytest count in this file’s verify line. Fix anything the gate fails.

Done when: `meson install` (staged prefix) contains `hud.py`, `lock_lines.py`, `toplevel_manager.py`; `sh -n scripts/install.sh` and the new meson-completeness test pass; `sh scripts/check.sh` is green. check.sh: 660 passed, 3 skipped.

---

## Workstream 1b — Review follow-up (laptop)

Review of the WS1 landing (`6a0b2c3` vs `docs/build-spec.md`): the boot path is correct. Do **not** reopen 1.1–1.9. Do **not** meson-install the tablet for these items. None need the phone.

`docs/build-spec.md` is still the how for Workstream 2+, but its Status/Overview still describe WS1 holes as current. **Do 1.10 first.**

- [x] **1.10 `docs/build-spec.md` must stop describing WS1 as open.** Status still says `work-order items 1.1–1.9 still open`. Overview and the "Current state (repo)" table still speak in the present tense about missing meson modules, apk/apt-only `install.sh`, unmapped gesture actions, `~/xx-wm/src/` deploy, and a five-surface re-theme walk. Set Status to: WS1 landed on main (`6a0b2c3`); check.sh 660 passed, 3 skipped; next is 1b then Workstream 2. Label that table **Before WS1** or past-tense it. Leave the live tablet snapshot (still piercing-shell). Do not restore WS1–28 history.

- [x] **1.11 `docs/config.md` lisgd fallbacks.** The notes on `swipe_up_short` / `swipe_up_long` say missing/invalid JSON still uses `gesture.keyboard` / `gesture.home`. That is `DEFAULT_VERBS`, which apply to **valid unmapped** values (`camera`, `none`, `launch:…`). Missing or corrupt JSON loads `_DEFAULTS` then `ACTION_TO_VERB` (`test_corrupt_config_file_falls_back` → `DEFAULT_BINDINGS`, short → `gesture.home`). Document both fallbacks. Restore "Verb-rebindable" on the four lisgd rows.

- [x] **1.12 Pin the tablet's live home-swipes.** Spec 1.4 required `test_launch_home_swipes_do_not_change_lisgd`: json `swipe_left_home=launch:htop.desktop`, `swipe_right_home=launch:org.gnome.Nautilus.desktop` → `generate_bindings` still `DEFAULT_BINDINGS`. Today's test puts `launch:` on **`swipe_up_short`** (a lisgd slot) and only checks `gesture.keyboard`. Production is fine (`swipe_left_home` is not in `_LISGD_GEOMETRY`); the test does not pin the tablet. Assert all five `ACTION_TO_VERB` keys. Rename `TestDefaultByteEquivalence` / `test_defaults_match_former_hardcoded_bindings` to `TestDefaultActionNameMapping` / `test_defaults_map_action_names_to_verbs` and drop the "former hardcoded" module docstring.

- [x] **1.13 Tighten string-presence tests.** `tests/test_session_wiring.py` `test_wrapper_supervises_python_instead_of_exec` passes on the lisgd `while IFS= read` plus comments that contain `138`/`137`. Pin the supervisor: `while :;`, `wait "$_child"`, `[ "$_st" -eq 138 ] || [ "$_st" -eq 137 ]`, `[ "$_st" -eq 0 ] && exit 0`. `tests/test_bootstrap_dots.py` `test_rm_rf_is_only_for_cache_clone` treats everything after the first `else` as the cache branch (including after `fi`). Assert `rm -rf` sits between `DEST="${HOME}/.cache/piercing-dots"` and the closing `fi` of that if, never on `${HOME}/piercing-dots`. 2.3 runs this script; the tablet already has `~/piercing-dots`.

- [x] **1.14 Config dir migration tests (spec 2.6, never landed).** `tests/test_config.py` `test_migration_from_old_config` only covers missing keys, not the piercing-shell **directory rename**. Add, with isolated `HOME`:
  - `test_migrates_legacy_dir_when_xx_wm_absent`: create `~/.config/piercing-shell/{config.json,gestures.json}` with `theme=amoled`, `default_layout_applied=true`, `swipe_left_home=launch:htop.desktop`; construct `ShellConfig()`; assert legacy gone, xx-wm present, theme/slots/flag preserved, gestures file moved, no `pin_hash` invented.
  - `test_does_not_migrate_when_xx_wm_exists`: both dirs present → piercing-shell left intact.

Done when: those tests fail on a revert of the behavior they pin; `docs/config.md` matches `ACTION_TO_VERB` + `DEFAULT_VERBS`; `docs/build-spec.md` Status does not say 1.1–1.9 are open; `PATH="$PWD/.venv/bin:$PATH" sh scripts/check.sh` is green. check.sh: 663 passed, 3 skipped.

---

## Workstream 2 — Tablet cutover

SSH: `dr3k@192.168.1.129`. Graphical session is still piercing-shell until 2.5. 2.3 still depends on 1.1, 1.2, 1.3, **1.5**, 1.6, 1.7 — those have landed.

### Privileges (blocks 2.3+)

- [x] **2.1 Smoke sudoers.** `dr3k` has `(ALL) ALL` but not NOPASSWD. From this laptop, run `sh scripts/grant-tablet-sudo.sh` (or `sudo sh scripts/grant-tablet-sudo.sh` — it re-execs as `$SUDO_USER` so SSH keys still work). Type the tablet sudo password once. That installs `/etc/sudoers.d/xx-wm-smoke` (`NOPASSWD: ALL`). Revoke later with `sh scripts/grant-tablet-sudo.sh --revoke`. Do not store the sudo password in this repo.

- [x] **2.2 Put this tree on the tablet.** Clone or rsync to `dr3k@192.168.1.129:~/xx-wm` (unprivileged). This is the source for install and for later `deploy.sh`.

- [x] **2.3 Install XX-WM.** Deps + meson `--prefix=/usr` + **tablet** phoc fragment + squeekboard layout symlink + logind drop-in `10-xx-wm-power.conf` (alongside or replacing the piercing one; both ignore power keys — fine). **Do not** enable the systemd user unit (1.3). `input` group already applied. Confirm:
  - `/usr/bin/xx-wm`, `/usr/bin/xx-wm-ipc`, `/usr/libexec/xx-wm-session`
  - `/usr/share/wayland-sessions/xx-wm.desktop` with `Name=XX-WM`
  - `/usr/share/xx-wm/{main,hud,lock_lines,toplevel_manager}.py` all present
  - `/usr/share/xx-wm/phoc.ini` is the **tablet** fragment (`DSI-1` scale 1.5)
  - `LD_PRELOAD` resolution in `xx-wm` finds `/usr/lib/libgtk4-layer-shell.so.0`

- [ ] **2.4 GDM Colemak OSK.** GDM is enabled. gnome-shell 50 packs layouts in `gnome-shell-osk-layouts.gresource` — there is no `/usr/share/gnome-shell/osk-layouts/us.json` to overwrite. A filesystem copy plus a gresource extract backup are in place (`us.json` and `us.json.xx-wm-backup`); the greeter may still be QWERTY. **GLASS**: at the greeter, OSK types Colemak. If it does not, this is a GNOME 50 gresource follow-up, not a missed `cp`.

- [x] **2.5 Switch the session. GLASS.** AccountsService/`~/.dmrc` Session=`xx-wm`. User logged in. Grim shows DE XX-WM / phoc / DSI-1 @ 1.5. Rollback: `piercingxx`.

- [x] **2.6 Config migration.** First XX-WM start must rename `~/.config/piercing-shell` → `~/.config/xx-wm` (only if xx-wm dir is absent). Preserve home slots, custom swipe-left/right `launch:` bindings, theme `amoled`. No PIN was set — lock is swipe-to-unlock. `default_layout_applied` is true — **do not re-seed**. First-boot wizard will skip; replay the tour with `xx-wm --welcome` during smoke, and once with a throwaway config (or a moved `config.json`) to exercise the wizard itself.

- [x] **2.7 Single shell.** After re-login: one `phoc`, one `python3 /usr/share/xx-wm/main.py`, one `lisgd` talking to `xx-wm-ipc`, squeekboard running, IPC socket `$XDG_RUNTIME_DIR/xx-wm.sock`. **No** second `piercing-shell` / `piercing-ipc`. `gsettings get sm.puri.phoc auto-maximize` is `true`.

Done when: SSH `pgrep -af xx-wm` shows the new shell, `WAYLAND_DISPLAY=wayland-0 grim` captures the XX-WM home surface, and piercing-shell is not in the process list.

---

## Workstream 3 — Tablet smoke (remaining GLASS)

Code for these boxes is largely landed. Tick only after a finger on the panel (or a grim). File a 3b item if a box fails.

- [x] Shade over apps, Settings from the shade, WiFi list
- [x] Drawer Search raises Colemak OSK
- [x] `deploy.sh` SIGUSR1 path (1.5) — used all evening
- [x] phoc.ini tablet fragment `DSI-1` scale 1.5
- [ ] lisgd system gestures **over a running app** (bottom recents, top shade, edge back)
- [ ] Recents: swipe down dismisses; cards activate / ✕ close **real** windows
- [ ] Home swipe-left `htop` / swipe-right Nautilus (mid-display; bezel is back)
- [ ] Power short-press blanks + lock; long-press power menu; swipe-up unlock
- [ ] WiFi PSK field raises OSK; Connect works
- [ ] Brightness + volume sliders on the unexpanded shade; HUD on HW keys
- [ ] Appearance presets apply instantly
- [ ] Tiles: WiFi/BT/Data/Airplane always on; Torch + Auto-brightness **hidden** (no LED, no ALS)
- [ ] DnD / Focus tiles; home edit mode; drawer folder expand+center
- [ ] Light theme (paper, mist) readable, then back to amoled
- [ ] JetBrains Mono Nerd is the clock/slots family (`fc-list`)
- [ ] `xx-wm-ipc lock|gesture.shade|gesture.switcher|gesture.home|welcome`
- [ ] Wizard once (move `config.json` aside, restore after); `xx-wm --welcome` on the live config
- [ ] Lock PIN path (set 6-digit, keypad only after swipe, wrong PIN, then leave as user wants)

### What this box will not prove

- Telephony, fingerprint, ALS curve — no modem, no fprintd, no illuminance. Re-run on FLX1.
- Waydroid — 1.8 GiB RAM / 10 G free. Do not install it here.

---

## Workstream 3b — Feature completeness (START HERE)

Make every `design.md` surface **true** on this tablet. Skippy builds over SSH; GLASS boxes wait on a finger. One commit per item or coherent group. `PIERCING_DEVICE=192.168.1.129 XX_WM_USER=dr3k sh scripts/deploy.sh` after each landing. Do not reopen WS1/2. Do not install Waydroid. Telephony stays Workstream 6.

### 3b.1 Recents must list, switch, and close real windows  ← first

- [x] **Build.** Recents lists Calculator. Root cause: phoc sends handle event 7 (`parent`); vendored protocol only knows 0–6, so binding v3 crashed the burst. Bind v1. A second `Display` on the GTK thread never saw registry events; a dedicated thread with blocking dispatch does. Do not block the GTK thread. Grim: OPEN APPS card **Calculator**.
- [ ] **GLASS.** Open Calculator, swipe up from the bottom: a card named Calculator (or its title). Tap it → Calculator focuses and the sheet hides. ✕ or swipe-up on the card → Calculator closes.

### 3b.2 On-screen keyboard on every text field

- [x] **Build.** `osk.py` owns EXCLUSIVE + async SetVisible. Wired: Search, APN, rename/new-folder dialogs, wizard keyboard page, SMS composer. PIN keypad is buttons. `tests/test_osk.py` fails if a new `Gtk.Entry` is constructed without the helper.
- [ ] **GLASS.** Search, WiFi PSK, APN, Rename all raise Colemak. Tap-outside hides it. Terminal/email/url purpose variants in real apps (browser URL bar is an app, not us).

### 3b.3 The shade owns notifications

- [x] **Build.** Unique-name compare; `REPLACE|ALLOW_REPLACEMENT`; daemon starts synchronously. SSH grim: `notify-send` rows in the shade (`Hello from Grok`).
- [ ] **GLASS.** Swipe-dismiss a banner; lock shows the summary when a PIN is not set.

### 3b.4 Drawer vs keyboard

- [x] **Build.** Exclusive zone 0 already. Search hops TOP while the OSK is armed and drops BACK unless Settings owns the layer.
- [ ] **GLASS.** Type in Search; the field stays visible above Colemak; results stay thumb-reachable.

### 3b.5 Quick settings tiles — honest hardware

- [x] **Build.** Tier-1 WiFi/BT/Data/Airplane always. Torch hidden without LED; Auto hidden without ALS (this tablet: no IIO). Location/Hotspot gated. Sliders always visible; `sync_sliders` on shade show. Tests pin the hide rules. Grim of the shade shows no Torch/Auto.
- [ ] **GLASS.** Expand/collapse; sliders move backlight and sink volume; HUD flashes.

### 3b.6 DnD and Focus are real, not just tiles

- [x] **Build.** Tiles toggle config-backed state. `set_schedules` round-trips. Starred + repeat-caller stay in code (FLX1 proof). Focus pause/dim/block/hold + take a break 5/10/15 already wired.
- [ ] **GLASS.** Toggle DnD: `notify-send` is silent but in the shade. Focus-pause Calculator from the drawer; launching it is blocked until break/off.

### 3b.7 Lock, power, HUD

- [x] **Build.** Power short-press blanks + lock via DisplayManager `idle_add`. Long-press power menu. HUD on vol/bright keys. Swipe-up unlock; PIN keypad after swipe when a PIN exists. Lock notification list is fed by the shade daemon.
- [ ] **GLASS.** Those three, plus lock notification list after a `notify-send`.

### 3b.8 Home, folders, edit mode

- [x] **Build.** 8-slot cap; inline folders; edit mode. `compact_unresolved_home` on start (no re-seed). This tablet's Calendar/Camera/Photos `.desktop` ids all resolve — compact is a no-op here.
- [ ] **GLASS.** Long-press home, add/remove a slot, open a folder, launch a member, confirm collapse.

### 3b.9 Settings + Appearance + backup

- [x] **Build.** System-only page + Appearance presets. Back / edge-swipe drops to BOTTOM. Backup validate-before-write; restore does not write PIN hash.
- [ ] **GLASS.** Change theme on-device; export a backup; WiFi PSK OSK; Back returns to the app.

### 3b.10 Gestures stay as bound

- [x] **Build.** lisgd slots unchanged; home `launch:htop` / `launch:Nautilus` not rewritten. Bezel 72 px skip. Settings rebind hot-restarts lisgd; UI says they apply immediately, with a next-session fallback status.
- [ ] **GLASS.** Mid-home swipe-right opens Files; from the right bezel only the back arrow. Bottom swipe over Calculator opens recents (3b.1).

### 3b.11 Fonts and light themes

- [x] **Build.** Tablet `fc-list` has `JetBrainsMono Nerd Font` (matches `FONT_FAMILIES`). CSS already falls back to Monospace. Paper/mist contrast tests already exist.
- [ ] **GLASS.** Pick Paper in Appearance; every overlay still readable; return to AMOLED.

### 3b.12 First-boot and welcome

- [x] **Build.** Wizard skipped when `default_layout_applied`. `--welcome` is tour-only. Keyboard page Entry uses `osk.attach`; wizard layer-shell starts NONE (PIN is buttons).
- [ ] **GLASS.** `--welcome` overlay; optional full wizard on a moved `config.json`.

Done when: 3b.1–3b.3 are green on glass (recents, OSK, notifications), the rest of 3b is ticked or has a filed follow-up in this file, and a grim of home + shade + recents-with-a-card is off-device.

---

## Workstream 4 — Tablet daily-driver extras

Still the tablet. Keep it light.

- [ ] **4.1 Browser.** `apps.sh` laptop half landed (pacman: Waterfox if a repo package exists, else `firefox`, never `firefox-esr` / yay; apk/apt stay ESR). This box is the on-device install after 2.7: default browser via `xdg-settings`, confirm it appears in the drawer and in the Tools folder if seeded.
- [ ] **4.2 Tailscale.** `apps.sh` laptop half landed (pacman `tailscale` then tgz fallback; Waydroid skipped when `MemTotal < 3145728`). This box is on-device: `tailscale up` is **GLASS** / user auth. Then Skippy PWA: host defaults to `skippy`; confirm the `.desktop` Exec escaping and that the entry launches. Needs the user’s tailnet.
- [ ] **4.3 piercing-dots phone profile.** `scripts/bootstrap-dots.sh` is a loud stub until `./install.sh --profile phone` exists **in the piercing-dots repo**. Tablet already has `~/piercing-dots` without that flag. This repo only consumes the profile: kitty, nvim + `piercing-note`, yazi, bash+starship, maintenance script — POSIX, apk **and** apt **and** pacman, no x86/GNOME/systemd assumptions. Land the profile in piercing-dots, then unstub `bootstrap-dots.sh`. Notes default slot should resolve to `piercing-note` once that entry exists (today it is `org.gnome.TextEditor.desktop`).
- [ ] **4.4 Calculator** is already installed. Camera / Photos / Calendar `.desktop` ids in the migrated layout must resolve or the slots must compact (no empty gaps, no empty folders).

---

## Workstream 5 — FLX1 recovery

Needs the phone in hand and a USB cable. Agent cannot BROM-recover over WiFi.

- [ ] **5.1 Recover from MediaTek Preloader/BROM via mtkclient.** User at the desk. Get to a bootable FuriOS with SSH.
- [ ] **5.2 Record SSH:** user, IP / tailnet name, whether `sudo` is passwordless. Put it in `devices/furiphone-flx1/notes.md`.
- [ ] **5.3 Do not `apt upgrade`.** systemd 261-rc3 `systemd-sysusers` breaks postinsts (pipewire, wpasupplicant). Workaround on record: `|| true` in the failing `.postinst`, then `dpkg --configure -a`. Hold until FuriLabs ships a fix.

Done when: `ssh` into the FLX1 works and Phosh is still the session (we replace it next).

---

## Workstream 6 — FLX1 bring-up and daily-driver

FuriOS is Debian-based (apt path of `install.sh`). Output fragment: `launcher/data/phoc/furiphone-flx1.ini` (`HWCOMPOSER-1` scale **3**).

### Bring-up

- [ ] **6.1 Can Phosh be replaced?** Before investing: does FuriOS pin `phosh.service` / the greeter? If the session cannot be swapped, stop and write the finding in `devices/furiphone-flx1/notes.md`.
- [ ] **6.2 Packages.** `gtk4-layer-shell`, `python3-gi`, `phoc`, `squeekboard`, `lisgd`, `python3-pywayland` (or equivalent), ModemManager / `mmcli`. Note exact apt names.
- [ ] **6.3 Install XX-WM** (apt path). Device prompt = `furiphone-flx1`. Same “do not enable user unit” rule as 1.3. `input` group. logind power drop-in.
- [ ] **6.4 Hardware inventory** into `devices/furiphone-flx1/notes.md`: output name (`wlr-randr` or equivalent — may be `HWCOMPOSER-1`), evdev nodes (`libinput list-devices`), IIO sensors, fingerprint node (`fprintd` / sysfs; `FP_INPUT_DEV` override exists), backlight, torch.
- [ ] **6.5 Session cutover.** Pick XX-WM, single shell, lisgd on the real touchscreen, scale 3 actually fits.

### Telephony (this is the VoLTE device)

- [ ] Modem stack is ModemManager (our surfaces speak mmcli / MM1). If FuriOS wraps a custom layer, document it and adapt — do not assume.
- [ ] Incoming call: CallBar shows, ringtone loops, **Accept** and **Hangup** are real MM1 calls (async, UI transitions on success). Ringtone stops on every terminal path (accept, hangup, remote drop, ignore).
- [ ] Outgoing dialer: create → parse D-Bus path → start. Failure is visible, not swallowed.
- [ ] SMS send/receive against a live modem.
- [ ] DnD: silence + shade still collects; starred contacts and repeat-caller (same number twice within 15 min) still ring. **GLASS** with a second phone.
- [ ] Built-in dialer is fallback only; Comms folder Phone/Text should bind GNOME Calls / Chatty if those are what FuriOS actually uses.

### Sensors, FP, ALS, keyboard

- [ ] Fingerprint unlock (FLX1 has one; this is the device that can prove `fprintd-verify` with no username argv).
- [ ] Auto-brightness tile on a real illuminance node; hide if absent.
- [ ] lisgd threshold calibration on this panel. **GLASS**
- [ ] squeekboard Colemak + purpose variants in real apps (SMS, browser URL, terminal). **GLASS**

### Android layer

- [ ] FuriOS **vd** container vs a non-Phosh shell — does it still launch? Document.
- [ ] **Waydroid + microG** lives here, not on the tablet. `apps.sh` 17.6 installs the package; `waydroid init`, microG image (waydroid_script / MinDroid — exact steps in `devices/furiphone-flx1/notes.md`), sign-in, YouTube Music, Synology Drive/Photos/Chat, Google Calendar + Gmail **via microG** (not GNOME Online Accounts). Waydroid-exported `.desktop` entries appear in our drawer and in search.
- [ ] Skippy PWA over the live tailnet. Same host as 4.2 unless the user says otherwise.

### piercing-dots

- [ ] `bootstrap-dots.sh --profile phone` on Debian. Same contract as 4.3.

Done when: calls and SMS work on VoLTE, XX-WM is the session, and Waydroid apps show in the drawer.

---

## Workstream 7 — Librem 5 (performance canary)

Third. PureOS, already phoc/Phosh, 3 GiB RAM / 32 G eMMC, 720×1440 → fragment `librem-5.ini` (`DSI-1` scale **2**). No flash.

- [ ] **7.1 SSH + apt inventory.** GTK4 / libadwaita / gtk4-layer-shell versions, session mechanism (wayland-sessions vs `phosh.service` override), ModemManager, killswitches.
- [ ] **7.2 Install XX-WM** (Debian path). Device prompt = `librem-5`. Replace Phosh in place: session file + disable phosh, do not leave both.
- [ ] **7.3 Hardware notes** in `devices/librem-5/notes.md`: output name, evdev, IIO, modem.
- [ ] **7.4 Full tablet-equivalent smoke** (Workstream 3 checklist) on this panel. If it janks here, it janks everywhere — treat stutter, input lag, and RAM as bugs, not “it’s a Librem.”
- [ ] **7.5 Telephony** against this modem (no VoLTE expectation). Confirm our mmcli paths still work.
- [ ] **7.6 piercing-dots** phone profile on PureOS.

Done when: Phosh is gone, XX-WM is the session, and the canary smoke is written down (smooth / usable / not).

---

## On-device checks (reference-fixes + tier2)

Landed in code with headless tests only; each needs a device to confirm.

- [ ] **phoc `-S` shield** on phoc 0.56 and on the FLX1: boot comes up covered, lowers onto the lock (PIN) or shell; kill the Python shell and the crash-restart comes back covered, then locked.
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
- [ ] **FLX1 modem stack**: is it oFono or ModemManager? `busctl --system list | grep -E 'ofono|ModemManager'` — everything above assumes ModemManager.

## Parked (do not attempt in this sequence)

- **Fairphone 5 flash and bring-up** — image in `devices/fairphone-5/downloads/`, `flash.sh` ready. pmOS: VoLTE not working, fingerprint dead, USB data drops while charging (WiFi SSH only), default user `user`. Pick it up after Librem 5, not before.
- **Switcher snapshot thumbnails** — against the minimalism directive; only if the user reconfirms. Capture-on-leave + cache if they do.
- **Hyprland / Hyprgrass** — parked until Hyprgrass matures. Tablet already has hyprland session files; ignore them.
- **Publishing / releases** — none until a phone (FLX1 or L5) has booted XX-WM as the daily session.
- **Installing Waydroid on the tablet** — RAM/disk.
- **SMS theme fan-out** — `SMSConversation.apply_theme` exists but nothing constructs that window and `_retheme_surfaces` does not walk it. Wire a window ref when FLX1 SMS is opened from the shell (Workstream 6). Not a tablet item.

## Blocked — needs the user

- **GLASS 2.4:** greeter OSK Colemak. gnome-shell 50 may ignore the filesystem `us.json`.
- **GLASS 3 leftover boxes** (power, recents cards, PSK OSK, HUD, folders, edit, light theme, wizard).
- FLX1 USB recovery (5.1).
- piercing-dots `--profile phone` in the **piercing-dots** repo (4.3 / 6 / 7). This repo stays the consumer.
- Tailscale login + Skippy host if not `skippy` (4.2).
- Audiobookshelf URL (apps.sh 17.5) — skip if blank.

## Device facts (quick reference)

- **Tablet:** `dr3k@192.168.1.129`, Arch, GDM, `DSI-1` 1200×1920 scale **1.5**, touch `FTSC1000` (lisgd bound `event6` this boot), no wlopm, lisgd from source, 1.8 GiB RAM. Session is XX-WM. `wlopm` not installed — DisplayManager tracks blank state internally. `gtk4-layer-shell` must be `LD_PRELOAD`ed before libgtk-4. SSH keys also in `/etc/ssh/authorized_keys/dr3k`.
- **phoc 0.56 / wlroots 0.20:** `wlr-foreign-toplevel-management` supported. Auto-maximize is GSetting `sm.puri.phoc auto-maximize`, not a phoc.ini key.
- **FLX1:** FuriOS, `HWCOMPOSER-1` scale **3**, VoLTE, fingerprint, Halium, vd container. BROM recovery first. Hold apt upgrades.
- **Librem 5:** PureOS, `DSI-1` scale **2**, Phosh-replace, performance canary.

## Suggested order

1. Workstream 1–2 closed except 2.4 GLASS Colemak. Do not reopen 1.1–1.14 or 2.1–2.3/2.5–2.7.
2. **Workstream 3b.1** (recents list/switch/close) — the shell is unusable as a daily driver without it.
3. 3b.2 OSK everywhere, 3b.3 notification ownership, then 3b.4–3b.12 in order. GLASS the matching 3 leftover boxes as each lands.
4. Workstream 4 on-device extras (browser, Tailscale, dots, slot compact) after 3b.1–3b.3.
5. Workstream 5 FLX1 recovery (user + cable), then 6.
6. Workstream 7 Librem 5.
7. FP5 stays parked until that list is done.
