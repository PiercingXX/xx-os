# xx-weather Linux port — spec

Public Play edition ("Weather", `com.xx.weather`, v1.2.0) ported to a
native Linux phone app (`io.piercingxx.XXWeather`) beside the other
PiercingXX apps. Android source: `ref/xx-weather`.

## Android feature list → Linux mapping

| # | Android (Play edition) | Linux mapping |
|---|---|---|
| 1 | ZIP in → NWS `api.weather.gov` primary (points → forecast/hourly → station obs), identifying User-Agent | Same chain over urllib on a worker thread; same UA `(xx-weather, https://github.com/PiercingXX/xx-weather)`; `common/xx_phone/weather.py` owns fetch/parse/cache |
| 2 | Open-Meteo enrichment (UV, visibility, pressure, dew point, sun) + standalone fallback when NWS is down | Same: extras call after NWS, full OM parse as fallback; `best_match` URL unchanged |
| 3 | ZIP → lat/lon via zippopotam.us (`/us/`, `/vi/`, `/pr/` order), cached; offline USVI table (008xx) | Same endpoints and order, `ZipGeocoder` ported; USVI table kept verbatim; geocode results cached in prefs store |
| 4 | Current hero (temp, condition, H/L, feels-like), 24h carousel, 10-day strip, 8-tile details grid, footer credits | Same sections in one scrollable GTK page: hero card, hourly strip (`Gtk.ScrolledWindow` horizontal), daily rows, details grid, footer credit line |
| 5 | Day dialog (tap date/day): H/L, PoP, hourly rows for that date | Same as an `Adw.Dialog`-style sheet with hourly rows for the tapped date |
| 6 | Saved locations: multi-ZIP list, swipe pager, add/remove, selected ZIP persists, per-ZIP raw-response caches + 15-min freshness, stale-on-offline with "Updated" stamp | Same: locations page, bottom tabs for saved ZIPs, per-ZIP cache files, `STALE_MS = 15 min`, stale stays on screen with "Updated …" stamp; airplane/offline never moves the stamp |
| 7 | Settings: add ZIP, °F/°C, per-ZIP rain (PoP ≥ 50% in next N h, N ∈ 3/6/12) + freeze (at/below °F) triggers, licenses | Same rows in a settings page; notification permission step dropped (Linux needs none); licenses dialog kept with the same three data credits + GPL |
| 8 | Alert evaluation on the 15-min refresh pass, one notification per trigger per fetch window, tap opens that ZIP | Same evaluator; `Gio.Notification` via the GTK app; `--refresh-once` headless pass + opt-in systemd user timer replaces WorkManager |
| 9 | Sunrise/sunset via local NOAA solar equations | Same algorithm ported (`SunCalc`) |
| 10 | °F/°C units, shared H/L renderer (missing bound omitted, never "--") | Same conversions and `hilo` rule, ported with tests |
| 11 | Failed-refresh placeholder (no forever spinner), no double-fetch on cold start | Same UI-state machine (`apply_refresh_result`, `zip_shows_spinner`, refresh-once guard), ported with tests |
| 12 | Home-screen widgets (Compact 2×2, Forecast 4×2, Glance feels-like+wind), tap opens that ZIP | Counterpart is the XX-WM home weather line: tapping it opens xx-weather; widget paint rules (muted PoP, no accent blue) carry over to the app's PoP text |
| 13 | Suite theme sync (Compose palette + painted widgets follow the family theme) | Counterpart is `common/xx_ui`: app applies the live suite preset, all themes repaint live, no hardcoded colors |

## Out of scope and why

- **home-server login / home-server talk / log upload** (`log/`, `LogsUi`, `LogDumpProvider`, `SuiteBackupProvider`, `WeatherBackupProvider`, `Snapshot.kt`): named out of scope by the task; the Play edition does not use them either. Settings keeps a licenses row, not a logs row.
- **Android widgets as Linux widgets**: Linux has no equivalent surface; the shell home line + tap-to-open is the port (row 12).
- **`POST_NOTIFICATIONS` runtime permission flow**: Linux notifications need no permission; deny-branch copy is dropped, forecasts never depend on it.
- **Device QA checklist** (first-ZIP flow on device, overnight widgets, GrapheneOS revoke): device-only by the Android TODO; covered here by fixtures, error-path tests and seeded screenshots.
- **Family theme-sync receiver** (`ThemeSyncReceiver`, hex palette protocol): replaced by `xx_ui` live presets, which is the suite mechanism on Linux.
- **Second poller**: same as Android — one 15-min cadence evaluates alerts; no extra poller.

## Linux shape

- `xx-weather/src/xx_weather/` (`main.py` window + app, `store.py` prefs/cache, `worker.py` threaded refresh, `notify.py` alert → `Gio.Notification`), `install_subdir`, launcher `xx-weather`, desktop `Name=xx-weather` / `GenericName=Weather`, `DBusActivatable=false` (xx-auto pattern), metainfo, sun-mark icon derived from the Android `ic_launcher_foreground` vector (brand dir absent in this lane).
- Pure logic lives in `common/xx_phone/weather.py` so the shell widget and the app share one fetch/parse/cache implementation; the widget keeps its cache format and line format, and now prefers opening `io.piercingxx.XXWeather` on tap.
- Tests never touch the network: recorded `ref/xx-weather` JSON fixtures copied to `common/tests/fixtures/weather/`; app tests use fakes + a private bus.
