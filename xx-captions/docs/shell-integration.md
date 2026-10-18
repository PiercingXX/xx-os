# Shell integration contract (lane 3 service side → xx-wm shell lane)

This is the exact contract for adding the captions quick-settings
tile and the volume-HUD entry in the xx-wm repo. Everything below is
implemented and tested on this side; the shell lane only consumes it.

## Names (pinned, do not guess)

- Service bus name: `io.piercingxx.Captions1`
- Object path: `/io/piercingxx/Captions1`
- Interface: `io.piercingxx.Captions1`
- Normative copy: `src/xx_captions/dbus_api.py` (`INTROSPECTION_XML`).
- Overlay application id: `io.piercingxx.Captions` — deliberately
  different from the service name, so the overlay's
  `Gtk.Application` never collides with the daemon's bus ownership.

## Turning captions on and off

Async calls on the service (never `call_sync` from the GTK thread):

- Start playback captions: `Start` with params
  `GLib.Variant("(s)", ("playback",))`, no reply type (`None`).
- Stop: `Stop` with no params and no reply type.
- `Start` with anything but `playback`, `call`, or `mic` fails with
  `org.freedesktop.DBus.Error.InvalidArgs`; `Start("call")` without
  the per-call opt-in fails with
  `io.piercingxx.Captions1.Error.NotPermitted`.

Reference client: `src/xx_captions/client.py` (`CaptionsClient`
does exactly the calls below; `tests/test_overlay_client.py` pins
the payloads with a fake bus).

## Watching state

- Subscribe to the `Text` signal with **sender =
  `io.piercingxx.Captions1`** (signals carry the sender's unique
  name; without the sender filter the tile sees nothing). Handler
  shape is `(conn, sender, path, iface, signal, params)` — pass no
  trailing `user_data`, or the handler gets an extra argument.
  Payload is `(s text, b final)`: render partials muted, replace the
  last line on the matching final.
- Read `Active` (b) and `Source` (s) through
  `org.freedesktop.DBus.Properties` `GetAll` with reply type
  `GLib.VariantType.new("(a{sv})")`, and watch `PropertiesChanged`
  (same sender filter) for live updates.
- Every reply unpacks to a **tuple** of out-args: use
  `reply.unpack()[0]`, never the bare unpacked value. `reply_type`
  is always `GLib.VariantType.new("(…)")` or `None`, never a plain
  string (real PyGObject raises `TypeError`).

## Launching the overlay

- Command: `xx-captions --overlay` (installed to bindir by
  `meson.build`). It is a separate process from the service.
- First run with no model installed shows a download screen, not
  captions: model id `sherpa-onnx-streaming-zipformer-en-20M-2023-02-17`
  (the phone default, `config.DEFAULT_CONFIG["model"]`), title
  `Captions need a model`, size `42 MB`, progress bar, Download/Cancel
  (no Start until a model is installed).
  Download runs on a worker thread through the lane-1 model manager
  (`models.download`, SHA-256 verified); cancel aborts and keeps the
  partial file for resume; a cancel shows no message, a failure reads
  `Download failed`.
- The overlay re-execs itself once with `LD_PRELOAD` pointed at
  `libgtk4-layer-shell.so.0` (same trick as `xx-wm.in`), so the
  shell lane must not preload anything itself.
- Overlay geometry: layer OVERLAY, keyboard NONE, anchored bottom +
  left + right, exclusive zone 0, bottom margin 72 px by default
  (above the gesture area), draggable vertically on the status row;
  position persists in `~/.config/xx-captions/config.json`
  (`overlay_margin_bottom`). Input passes through everywhere except
  the drag handle and the overflow button. The handle holds
  Start/Stop (disabled while starting) plus a `⋯` menu (Settings,
  Close); the overlay is full width less 12 px each side;
  engine, language, model download and history live in
  `xx-captions --settings`. Colors come from the shared `xx_ui`
  design system, which follows `~/.config/xx-wm/config.json` with
  hot-reload; `CAPTIONS ON` (and an error) reads in the shell danger
  red `#ff6b6b`.

## Tile states

Map service state to exactly one of these five tile visuals:

| Tile shows        | Condition                                              |
| ----------------- | ------------------------------------------------------ |
| `off`             | service idle, model installed, no error                |
| `starting`        | `Start` sent, `Active` not yet true                    |
| `on`              | `Active` is true                                       |
| `no model installed` | `~/.local/share/xx-captions/models/` holds no complete catalog model (mirror `models.list_installed`: every file in the catalog entry present) — tapping it launches `xx-captions --overlay` so the user gets the download screen |
| `error`           | last `Start`/`Stop` failed; show the error text        |

Precedence when several hold: `error` > `no model installed` >
`on` > `starting` > `off` (pure helper
`overlay_state.tile_state(active, starting, installed, error)`).

Suggested gating, mirroring the `record` tile: the tile is visible
only while `io.piercingxx.Captions1` owns its bus name; `get_state`
reads `Active`, `set_state` calls `Start("playback")` / `Stop()`.
Launching the overlay process when turning on is the shell's job;
`xx-captions --overlay` is idempotent-safe to start once per
session (single `Gtk.Application` instance).

## Volume HUD and dialer rows (service side ready)

- HUD: same toggle as the tile in a smaller footprint next to the
  volume row; no service change needed.
- Dialer call details: `ListTranscripts` returns `(id, peer,
  started_unix, ended_unix)` newest-first (`started`/`ended` are
  int64, `x`); `GetTranscript(id)` returns `(peer, started, ended,
  lines)` with `(text, offset_ms)` per final line. Unknown ids fail
  with `...Error.NotFound`. Transcripts are opt-in, so most calls
  have none and the row stays hidden.
