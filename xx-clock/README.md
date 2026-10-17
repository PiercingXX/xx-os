# xx-clock

Alarms, timers and clock for Linux phones. Linux port of the PiercingXX
Android xx-clock app ("Alarms and timers" on Google Play): offline, no
accounts, no network.

- **Clock**: big time, date, next-alarm card, digital/analog toggle.
- **Alarms**: repeat days, labels, snooze, per-alarm sound, vibration.
  Tap edits, long-press deletes, switch toggles.
- **Timers**: several at once, 1/3/5/10 min presets + custom, pause/resume,
  reset, +1 min.
- **Setup**: service state, start-at-login, ringing prefs, and the honest
  limit — alarms do not fire while the phone is suspended or off.

A small user service owns the schedule and rings even when the window is
closed:

```sh
xx-clock --daemon          # runs io.piercingxx.XXClock.Daemon
systemctl --user enable --now xx-clock-daemon.service
```

Ringing shows a full-screen always-ink card (over the XX-WM lock screen,
same layer-shell surface as the call UI) plus an urgency-critical
notification with Snooze/Dismiss (alarms) or Stop/+1 min (timers). Sound
plays through GStreamer with a one-minute fade-in; vibration goes through
feedbackd when present.

See [PORT_SPEC.md](PORT_SPEC.md) for the Android → Linux mapping, the
D-Bus API, and the wake-from-suspend investigation.

## Install

```sh
cd xx-clock && meson setup build --prefix=/usr && sudo meson install -C build
```

## Develop

```sh
sh tools/snap_all.sh lane_shots   # screenshots in amoled + paper
```

## License

GPL-3.0. See [../LICENSE](../LICENSE).
