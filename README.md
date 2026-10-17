# XX-Linux-Phone

A complete, minimal phone experience for Linux phones: the **XX-WM** shell and the PiercingXX apps, built for postmarketOS, Mobian, FuriOS and other phoc-based systems. Python + GTK4/libadwaita, phone-first (360×720, works down to 320 px wide).

| Directory | What it is |
|---|---|
| [`xx-wm/`](xx-wm/) | The shell: home, app drawer, shade, lock screen, calls, notifications, settings, power, privacy, screen time, captions tile |
| [`xx-contacts/`](xx-contacts/) | Contacts. Owns the phone's contact store and serves it over D-Bus |
| [`xx-dialer/`](xx-dialer/) | Phone app with call screening and spam blocking (the shell asks it before ringing) |
| [`txxt/`](txxt/) | SMS/MMS messaging (owns SMS on the phone) |
| [`xx-calendar/`](xx-calendar/) | Calendar. Owns its events (lossless iCalendar) |
| [`xx-calculator/`](xx-calculator/) | Expression calculator with history and scientific keys (offline) |
| [`xx-email/`](xx-email/) | Gmail client with offline outbox, snooze and background sync |
| [`xx-vitals/`](xx-vitals/) | Health and steps, synced to a self-hosted server |
| [`xx-share/`](xx-share/) | Nearby Share, compatible with LocalSend |
| [`xx-captions/`](xx-captions/) | On-device live captions and call transcription |
| [`xx-find/`](xx-find/) | Self-hosted Find My Device (built, not enabled) |
| [`xx-auto/`](xx-auto/) | Driving screen for a phone in a car mount (MPRIS media, maps, starred-contact calls) |
| [`xx-radio/`](xx-radio/) | Unofficial YouTube Music player: search, radio, queue, background playback, offline downloads (spec + scaffold) |
| [`xx-audiobook/`](xx-audiobook/) | Self-hosted audiobooks from your own server: library, player, downloads, zones |
| [`xx-note/`](xx-note/) | Markdown notes as plain files, with labels, archive, trash and optional WebDAV sync |
| [`xx-weather/`](xx-weather/) | ZIP-code weather from the National Weather Service (US only), with saved locations and rain/freeze alerts |
| [`xx-files/`](xx-files/) | Tree-first local file manager: list/tree/grid, Trash with restore, share via xx-share |
| [`xx-auth/`](xx-auth/) | Offline TOTP/HOTP two-factor codes, encrypted at rest, Aegis vault files |
| [`xx-clock/`](xx-clock/) | Alarms, timers and clock with a schedule-owning user service that rings when the window is closed |

Each directory is a self-contained project with its own `meson.build`, tests and README. How the pieces talk to each other is in [`docs/dbus.md`](docs/dbus.md).

### Coming later

Ports of the Android apps still to come, after the current batch:

- **xx-maps:** offline navigation
- **xx-camera:** pro camera (burst HDR, manual, RAW)
- **xx-photos:** Immich client

## Install

The shell: see [`xx-wm/README.md`](xx-wm/README.md) (`sh xx-wm/scripts/install.sh`). Each app installs with meson from its directory:

```sh
cd xx-contacts && meson setup build --prefix=/usr && sudo meson install -C build
```

## Develop

```sh
python3 -m venv --system-site-packages .venv && .venv/bin/pip install pytest ruff
sh scripts/check-all.sh          # every project's tests + lint
sh scripts/check-all.sh txxt     # just one
```

GTK widget tests run on a virtual display (`gtk4-broadwayd`) and skip cleanly when it isn't installed.

## License

GPL-3.0. See [LICENSE](LICENSE). The Wayland protocol XML files under `xx-wm/launcher/data/` keep their own MIT notices.
