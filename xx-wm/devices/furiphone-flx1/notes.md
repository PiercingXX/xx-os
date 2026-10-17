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

Per-device phoc.ini fragment: `launcher/data/phoc/furiphone-flx1.ini` (output `HWCOMPOSER-1`, scale **1.2**: hwcomposer's mode is 720x1600, so 600x1333 logical; stock FuriOS's scale 2 was 40% too large for the owner). `install.sh` prompts for the device and copies the fragment over the installed `phoc.ini`.

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
