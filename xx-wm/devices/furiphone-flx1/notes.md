# Furi Phone FLX1 — test device

FuriLabs FLX1. In hand, not yet used for XX-WM testing.

| Feature | Detail |
|---|---|
| SoC | MediaTek Dimensity 900 |
| RAM / storage | 6 GB / 128 GB |
| Display | 6.67" LCD, 1080×2400, ~395 PPI → scale ~2.5 |
| OS | FuriOS — Debian 13-based with Halium-style android driver layer, ships **Phosh on phoc** |
| Telephony | **Working VoLTE + WiFi calling** — the standout vs the other two |
| Extras | IP68, NFC, fingerprint, headphone jack; Android app layer via their "vd" container |

## phoc scale (20.5)

Per-device phoc.ini fragment: `launcher/data/phoc/furiphone-flx1.ini` (output `HWCOMPOSER-1`, scale **1.6**: hwcomposer's mode is 720x1600, so 450x1000 logical; the owner found stock 2 too large and 1.2 too small). `install.sh` prompts for the device and copies the fragment over the installed `phoc.ini`.

## Why it matters here

- The only test phone with daily-driver telephony (VoLTE). This is the device that proves the call UI / dialer / SMS surfaces against a real, complete modem stack.
- Debian-based like the Librem 5, but with a Halium kernel like the future Pixel targets — closest analogue to the eventual Droidian path.
- Actively developed by FuriLabs with frequent OTA updates.

## To verify on device (Phase 4)

- [ ] Can the Phosh session be replaced cleanly, or does FuriOS pin it? (Open question #3 in `todo.md` — check before investing.)
- [ ] Modem stack: ModemManager or FuriOS custom layer? Our surfaces target mmcli.
- [ ] GTK4/gtk4-layer-shell availability on FuriOS repos
- [ ] Output name, evdev nodes, IIO sensors (same checklist as other devices)
- [ ] Android app container ("vd") interaction with a non-Phosh shell

## Startup cost (review P3, 2026-11-12)

Seen on `main` before: shell RSS 170 MB and 24 threads two minutes after
boot, `vkCreateWaylandSurfaceKHR(): VK_ERROR_EXTENSION_NOT_PRESENT` at every
start, and two `StartServiceByName for org.freedesktop.impl.portal.desktop.gtk:
Timeout was reached` lines.

- **Renderer.** `xx-wm-session` sets `GSK_RENDERER=ngl` when libhybris is
  installed (`libhybris-common.so*` under `/usr/lib`), unless the variable is
  already set; the wrapper passes it on to D-Bus-activated apps. Check: the
  shell's `/proc/<pid>/environ` has it, the Vulkan line is gone from
  `~/.local/share/xx-wm/stderr.log`, and RSS and threads against the numbers
  above. If FuriOS ships libhybris under another name nothing changes: set
  `GSK_RENDERER=ngl` in the login environment instead.
- **Portal timeout.** The shell runs with `GDK_DEBUG=no-portals`; the timeout
  is the portal broker waiting for its GTK backend, which an app's first
  Settings read triggers. Still to check here: `systemctl --user status
  xx-wm-session.target graphical-session.target xdg-desktop-portal
  xdg-desktop-portal-gtk`, `journalctl --user -b -u xdg-desktop-portal-gtk`,
  and the time from a drawer tap to a GTK app's first frame, cold and warm.
  If the backend starts but slowly, start the portals from
  `_xx_session_begin` (`--no-block`) so no app launch waits for them; if it
  never starts, fix the unit ordering first.

  Checked 2026-11-12: the GTK backend starts in time. The stall is
  xdg-desktop-portal-wlr 0.8.2, which exits at start on the hwcomposer
  phoc ("Compositor supports neither ext_image_copy_capture or
  wlr_screencopy") although screencopy v3 is advertised; the broker
  waits 25 s on it for Screenshot and 25 s for ScreenCast, 50.2 s in
  all, before it answers anyone. install.sh now puts
  `launcher/data/portals/halium-xx-wm-portals.conf` in
  `/etc/xdg/xdg-desktop-portal/` on libhybris systems (both off); the
  broker then starts in 0.11 s.
- **Renderer, checked 2026-11-12.** `GSK_RENDERER` reaches the shell and
  the Vulkan line is gone; GTK 4.22 warned that ngl is now called gl, so
  the session sets gl. Memory did not drop: 258–267 MB RSS (PSS 167 MB)
  and 27–32 threads; see `todo.md`.
