# XX-WM — launcher

GTK4/libadwaita launcher + shell surfaces for Linux phones. Despite the "WM" name, this is a *shell*, not a window manager — it runs on top of phoc (which does the window management) as a full Wayland session replacement (no GNOME, no Phosh). Text-first, gesture-driven, monochrome — the PiercingXX design language; `../design.md` is the UI spec.

## What's built

- **Home surface** (`window.py`, `home_launcher.py`) — BOTTOM layer. 8-slot home model with inline folder drop-downs, long-press edit mode, config-driven widget row (time/date/weather/battery with tap actions), gesture dispatch (`launch:<app_id>` bindings), config hot reload.
- **App drawer** (`window.py`, `app_index.py`) — ~85% bottom sheet, bottom search with bottom-anchored results, A–Z jump strip, A–Z ↔ install-date sort, pinned-first ordering, inline folder rows, `!` web search, auto-launch option, long-press action menus (`app_item_actions.py`).
- **Lock screen** (`lock_screen.py`) — OVERLAY layer, swipe-up unlock revealing the PIN keypad (6+ digits), escalating lockout, fingerprint, notification list (summary/count/off, DnD-aware).
- **Notification shade** (`notification_shade.py`) — TOP layer, date/time header with inline month calendar (`calendar_grid.py`), Settings entry, in-process daemon (`notif_daemon.py`), tap-to-launch, swipe-to-dismiss, clear all, quick actions embedded.
- **DnD & Focus** (`dnd.py`, `focus_mode.py`) — Pixel-model Do Not Disturb (schedules, starred contacts, repeat callers) and Focus Mode (paused apps, held notifications, take-a-break), wired through tiles, calls, and the notification path.
- **Quick actions** (`quick_actions.py`) — WiFi, BT, mobile data, airplane, torch, DnD, Focus; brightness/volume sliders; hardware-gated tiles hide themselves.
- **App switcher** (`app_switcher.py`, `toplevel_manager.py`, `wayland_bindings.py`, `thumbnail_store.py`, `output_capture.py`) — full-screen recents over `wlr-foreign-toplevel-management-unstable-v1`: `python-pywayland` on a GLib pump plus a dedicated wayland thread (never blocks the GTK loop), bindings generated at runtime by the installed pywayland's own scanner from the protocol XMLs under `data/` into a version-keyed cache. Cards show the resolved app name, window title, and last-seen stills; tap → activate (hands the screen to the app), ✕ or swipe-up on a card → close; tap dim / swipe down / back → resume; own and keyboard surfaces filtered out; graceful empty state when the compositor doesn't offer the protocol.
- **Volume/brightness HUD** (`hud.py`) — in-shell overlay on the layer-shell OVERLAY layer: flashes the new volume/brightness level on hardware key presses, auto-hides after ~1 s. No external process involved, and it silently no-ops where GTK or the layer shell can't init.
- **Sounds** (`sound.py`, `data/sounds/`) — ringtone loop + notification sound via paplay/pw-play, gated by config, DnD, mutes.
- **Weather** (`weather.py`) — Open-Meteo current conditions, 15-min cache, silent offline fallback.
- **System settings** (`system_settings.py`) — WiFi scan/connect (nmcli), Bluetooth scan/pair (BlueZ D-Bus), sound output picker (pactl), battery (UPower); the settings page is system-only — every shell preference lives in `~/.config/xx-wm/` (`../docs/config.md`).
- **Backup** (`backup.py`) — versioned JSON export/restore with validate-before-write.
- **Call UI / Dialer / SMS** (`call_ui.py`, `dialer.py`, `sms.py`) — mmcli/ModemManager telephony surfaces; dialer rows star contacts for DnD.
- **First-boot wizard** (`first_boot.py`) — PIN, theme, timezone, then the interactive gesture walkthrough (replay with `xx-wm --welcome`).
- **IPC server** (`ipc.py`) — Unix socket at `$XDG_RUNTIME_DIR/xx-wm.sock` (`lock`, `shade.*`, `switcher.*`, `gesture.*`, `welcome`, plus page-driving `page.home` / `page.apps` / `page.settings` and `settings.open[:section]` for rigs and scripts — no gesture timing needed; `settings.open` reuses the `open_settings(section)` deep-link).
- **Modem monitor** (`modem_monitor.py`) — ModemManager DBus watcher for call events.
- **Back arrow overlay** (`back_gesture.py`) — visual feedback only; gesture detection is lisgd's job, delivered via `gesture.*` IPC commands.
- **Bottom gesture strip** (`bottom_gesture.py`) — invisible OVERLAY strip on the bottom edge: flick home, swipe-and-pause recents, sideways neighbour app; taps pass through via a virtual pointer. lisgd keeps the top and side edges.
- **Theming** — every surface derives its CSS from the active theme preset (`theme_css(preset)`); intentional semantic colors (danger red, warning orange, destructive tint) live as named constants in `config.py`.
- **GDM Colemak OSK** (`data/gnome-osk/us.json`) — Colemak layout in GNOME Shell's osk-layouts JSON schema (derived from the squeekboard YAMLs, which remain the source of truth), installed to `datadir/xx-wm/gnome-osk/` with an install.sh step guarded to GDM hosts (stock `us.json` backed up before overwrite).

## Privileged helper (root D-Bus service)

Battery charge limits, per-app network blocks, and cgroup freezing need
root, so they live in `io.piercingxx.XXWM.Helper1`
(`helper/xx_wm_helper.py`, installed to `$libexecdir/xx-wm-helper`,
D-Bus activated, idle-exits after 60 s). Per-method polkit
authorization plus caller-uid cgroup confinement; state in
`/var/lib/xx-wm-helper/state.json`, re-applied at boot by
`xx-wm-helper-boot` (systemd + OpenRC units under `data/`). The shell
side is `src/helper_client.py` (async, cached capability probe,
graceful when the helper isn't installed). Full contract:
`../docs/helper.md`.

## What's not done yet (device-gated)

- Device bring-up: flashing, evdev paths, IIO sensor path, wlopm output name (`../devices/*/notes.md`)
- lisgd/squeekboard runtime verification, gesture threshold calibration, telephony testing
- Waydroid init + microG + Android app installs (`../todo.md` Workstream 17.6/17.7)
- Performance baseline — needs device testing (Librem 5 is the canary)

## Local build (dev machine)

```bash
# Install deps (Arch)
sudo pacman -S python-gobject gtk4 libadwaita gtk4-layer-shell meson ninja

# Run directly (no layer-shell compositor needed)
cd launcher
PYTHONPATH=src python3 src/main.py

# Build + install
meson setup build --prefix=/usr
meson install -C build
```

## Tests & the pre-commit gate

`scripts/check.sh` is the gate: py_compile + ruff + pytest + shellcheck.
pytest needs PyGObject, so the venv must see system site packages:

```bash
python3 -m venv --system-site-packages .venv   # .venv is gitignored
.venv/bin/pip install pytest ruff
PATH="$PWD/.venv/bin:$PATH" sh scripts/check.sh
```

shellcheck comes from the distro (`pacman -S shellcheck` / `apk add
shellcheck`); missing tools are reported as SKIPPED rather than failing the
gate, and ruff resolves via the venv python first so it needn't be on PATH.

## Deploy to device

```bash
export PIERCING_DEVICE=<device-ip>
export XX_WM_USER=dr3k   # tablet default; pmos default is user — check per device
./scripts/deploy.sh
```

`deploy.sh` stages `launcher/src/` (`*.py`, `style.css`) plus the Wayland
protocol XMLs in `/tmp/xx-wm-deploy/` on the device, then `sudo rsync`s
it into the installed datadir `/usr/share/xx-wm/` (root-owned; no
`--delete` there, so `phoc.ini` and sounds survive) and restarts the
running shell: `systemctl --user restart xx-wm` only when that unit is
active, otherwise `SIGUSR1` to the unique `python3
/usr/share/xx-wm/main.py` child (the `/usr/bin/xx-wm` supervisor respawns
it; never `TERM`/`HUP` — `Gio.Application` maps those to quit and the
session would end). It never updates `/usr/bin/xx-wm` itself — wrapper
changes need `meson install`. Needs passwordless sudo on the device
(`scripts/grant-tablet-sudo.sh`). `--dry-run` prints the real paths
without touching anything. Full installs go through `scripts/install.sh`
(whiptail menu: Install / Update / Install phone apps).

## Device checks to run first

Confirm the device has the required packages over SSH (`apk` on postmarketOS, `apt` on PureOS/FuriOS):

```bash
apk search gtk4-layer-shell        # or: apt list gtk4-layer-shell
pactl info | grep -i server
ps -p 1                            # systemd or OpenRC?
```

## Troubleshooting

- Shell stderr (tracebacks): the session wrapper redirects the shell's
  stderr to `~/.local/share/xx-wm/stderr.log`, rotated to `stderr.log.1`
  past 1 MB at each restart — phoc's own stderr is `/dev/tty7`, so
  without this tracebacks vanish on a phone. The structured shell log
  stays at `~/.local/share/xx-wm/shell.log`.
- From a paired host, `xxdb logcat` tails both logs (`[shell.log]` and
  `[stderr.log]` sources) plus the journal snapshot.
- Blank screen after a restart: fixed by the output-power reconciler
  (forces outputs on at bind); portals missing: check
  `systemctl --user status xx-wm-session.target` and that
  `XDG_CURRENT_DESKTOP` is `XX-WM`.

## Session config

- Wayland session: `wayland-sessions/xx-wm.desktop` (display managers only scan `*.desktop`)
- Session launcher: `libexec/xx-wm-session` (phoc wrapper, sets GTK_THEME from config); the in-session `bin/xx-wm` starts squeekboard before the shell
- systemd user service: `share/systemd/user/xx-wm.service` (`Restart=on-failure`); OpenRC: `data/openrc/xx-wm` → `/etc/init.d/` (OpenRC-based images only — pmOS v26.06 Phosh runs systemd; install.sh detects via `/run/systemd/system`)
- Keyboard layouts: `data/squeekboard/` (PiercingXX Colemak, incl. terminal/email/url variants) → `datadir/xx-wm/squeekboard`, symlinked by install.sh to `~/.local/share/squeekboard/keyboards/`
- phoc.ini: default `data/phoc.ini` plus per-device fragments under `data/phoc/` (`fairphone-5`, `furiphone-flx1`, `librem-5`, `tablet`, `qemu` for the Virtual-1 VM display) selected by install.sh; scale notes per device in `devices/*/notes.md`

## Fonts

JetBrains Mono Nerd (default), Space Mono, JetBrains Mono, System Light, plus user-imported custom fonts (`config.install_custom_font`). Install per distro; overlay dir fallback.
