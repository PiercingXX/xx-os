# FLX1s — device facts for the tier2 on-device checklist

The phone is an **FLX1s** (codename `radon`, model `FLX1s`), not the original FLX1
(`krypton`). The directory name and the rest of the repo still say "flx1".

Read off the phone over SSH on 2026-11-11 (read-only probes; nothing installed or changed).
The phone was running `main` (`d217514`), **not** `tier2` — every installed file in
`/usr/share/xx-wm` matched `main` byte for byte. Nothing below is a test of tier2 code
on the device except where it says so.

Each section answers one item of "On-device checks (reference-fixes + tier2)" in `todo.md`.

## Answers that change tier2 code

### 1. Modem stack: oFono behind an MM1 shim (not ModemManager)

- `org.ofono` is owned by `ofonod`; `org.freedesktop.ModemManager1` is owned by
  **`ofono2mm`** (1.8.0), not by ModemManager. The `modemmanager` 1.24.2 package is
  installed but its daemon does not own the name.
- Modem object: `/org/freedesktop/ModemManager1/Modem/0` (`plugin: ofono2mm`,
  `primary port: /ril_0`, drivers `binder`). `mmcli` works but prints
  `mm_object_peek_modem: runtime check failed` warnings.
- Interfaces the shim exposes on the modem: `Modem`, `Simple`, `Voice`, `Messaging`,
  `Modem3gpp`, `Modem3gpp.Ussd`, `Modem3gpp.ProfileManager`, `Signal`, `Time`,
  `Location`, `Firmware`, `CellBroadcast`, `ModemCdma`, `Oma`, `Sar`.
- `Voice`: methods `CreateCall`, `DeleteCall`, `ListCalls`, `HangupAll`,
  `HangupAndAccept`, `HoldAndAccept`, `Transfer`, `CallWaitingQuery`,
  `CallWaitingSetup`; signals `CallAdded`, `CallDeleted`; properties `Calls`,
  `EmergencyOnly`.
- `Ussd`: `Initiate`, `Respond`, `Cancel`; properties `State`, `NetworkNotification`,
  `NetworkRequest`.
- So the MM1 assumption holds at the API level. Not verified: whether the shim's
  per-call `StateChanged` reasons, `Sim.SendPin/SendPuk`, `UnlockRetries` and USSD
  session states behave like real ModemManager. Treat every shim behaviour as
  unproven until a call is placed.
- oFono side, if a fallback is ever needed: `/ril_0` has `org.ofono.Modem`,
  `SimManager`, `VoiceCallManager`, `org.nemomobile.ofono.CellInfo`, `SimInfo`,
  `org.ofono.FuriLabs.AT`.

### 2. No SIM in the phone — telephony items cannot be run yet

- oFono `SimManager.Present = false`. MM shim: `State = -1` (failed),
  `StateFailedReason = 2` (SIM missing), `UnlockRequired = 1` (none),
  `UnlockRetries = {}`, `Voice.Calls = []`, `EmergencyOnly = false`.
- `mmcli --3gpp-ussd-status` answers `modem not enabled yet`.
- Blocked until the owner inserts a SIM: USSD/MMI, PIN/PUK flow, busy/network-error
  notices, waiting call during DnD, calls/SMS waking the phone.
- This no-SIM state is itself a test case for the emergency-call path.

### 3. logind cannot suspend this phone

- `login1.Manager.CanSuspend` → `"no"`, `CanHibernate` → `"no"`.
- `/etc/systemd/sleep.conf.d/10-furios-sleep.conf` sets `AllowSuspend=no` (and the
  other three) with the comment "FuriOS doesn't use conventional ways to sleep.
  Whether you use stated or batman, you still want to avoid triggering the standard
  sleep states."
- Power is managed by `batman.service` (44) — it also owns
  `org.freedesktop.UPower.PowerProfiles` — plus `io.FuriOS.BatmanPowerConfig`.
- Kernel: `/sys/power/state` = `freeze mem`, `mem_sleep` = `[s2idle] deep`,
  `autosleep` = `off`, `/sys/power/wake_lock` is root-only (Android wakelocks).
- polkit `org.freedesktop.login1.suspend` is `implicit active: yes`, so there would
  be no prompt — the call is simply refused.
- Consequence: tier2's opt-in automatic suspend must check `CanSuspend == "yes"`
  and hide or disable itself here. The checklist items "logind Suspend without a
  prompt", "suspend waits for lock paint", "calls/SMS/power key wake from suspend",
  "RTC wake fires the alarm" and "ModemManager comes back after resume" are not
  testable on the FLX1 through logind. They need the Librem 5 or the pmOS VM.
- Sleep inhibitors present: NetworkManager and UPower (both `delay`).

### 4. The RTC does not hold wall-clock time — `SetWakeAlarm` writes the wrong value

- `/sys/class/rtc/rtc0` is `mt6359-rtc`; `wakealarm` exists, mode 0644 root,
  `power/wakeup` = `enabled`. `/dev/alarm` (Android alarm driver) also exists.
- `since_epoch` read **1264538101** (2010-01-26) while the system clock read
  1794440497 (2026-11-11). `timedatectl` confirms `RTC time: Tue 2010-01-26`.
  The offset is about 530 million seconds.
- tier2's helper (`set_wake_alarm`) writes the absolute system epoch into
  `wakealarm` and compares the pending value against `time.time()`. On this RTC
  that alarm is ~17 years in the RTC's future and never fires, and the
  "already set by another program" comparison is against the wrong clock.
- Fix direction: convert with the live offset
  (`rtc_target = epoch - (time.time() - int(since_epoch))`) or write the relative
  `+<seconds>` form, and read back/compare in RTC time. Add a test with an RTC
  that is years behind.
- Related, seen in the device log: `shell.log` stamped the shell's start
  `2026-10-23T22:27` on a boot that happened on 2026-11-11 — the wall clock jumps
  forward by weeks when NTP lands after boot. Anything timed on wall clock (mute
  expiry, lockout, smart-lock 4 h window, usage stats day buckets, DnD schedule,
  update-check stamp) sees that jump on every boot; use `CLOCK_BOOTTIME`/monotonic
  for durations.

## Answers that confirm tier2 as written

### 5. phoc `-S` shield and `phosh_private`

- phoc is **0.44.0** (FuriOS fork, `0.44.0-1+furios7`), not the 0.56 the docs assume.
  `phoc --help` lists `-S, --shell  Whether to expect a shell to attach`.
- The compositor advertises `phosh_private` **v7** (tier2 needs ≥ 6 for
  `set_shell_state`; the vendored XML is v8, so `add_thumbnail_view` and the
  `phosh_private_thumbnail` interface are not available here).
- tier2's own `wayland_bindings.ensure_phosh_private_bindings()` was run on the
  phone against a throwaway cache dir: it generated and loaded `PhoshPrivate` in
  0.29 s on pywayland **0.4.18**. `set_shell_state` is request opcode 5.
- Not done: actually starting phoc with `-S` and sending `set_shell_state`. The live
  session runs `phoc -C /usr/share/xx-wm/phoc.ini -E /usr/bin/xx-wm` (no `-S`).

### 6. scrypt cost

- `hashlib.scrypt(n=2**15, r=8, p=1)` took **136 ms** in three runs out of three.
  PBKDF2-SHA256 at 100k iterations took 38 ms for comparison.

### 7. `SetLockedHint`

- systemd 261~rc3 logind (no elogind). Graphical session: seat0, tty7,
  `Type=wayland`, `Class=user`, `Active=yes`, `Service=phosh`, `LockedHint=no`.
  The property is there to set; the elogind half of this item needs pmOS/OpenRC.

### 8. Video keeps the screen on

- The compositor offers `zwp_idle_inhibit_manager_v1` v1 and `ext_idle_notifier_v1`
  v1. `org.gnome.ScreenSaver` is activatable on the session bus but had no owner
  while `main` was running.

## Reference: what the phone has

| Thing | Value |
|---|---|
| OS | FuriOS forky 14.0, kernel 4.19.325-furiphone-radon (Halium), systemd, Python 3.13.12 |
| RAM | 7.6 GiB (the older notes say 6) |
| Toolkit | GTK 4.22.4, libadwaita 1.9.0, gtk4-layer-shell 1.3.0, PyGObject 3.56.2, pywayland 0.4.18 |
| Shell stack | phoc 0.44.0, lisgd 0.3.7, phosh 0.55.0 still installed, squeekboard 1.43.1 installed |
| Keyboard | `xx-keyboard` is the running OSK. `sm.puri.OSK0` has no owner, so the shell's `SetVisible` call logs a `ServiceUnknown` warning |
| Output | `HWCOMPOSER-1`, 63×129 mm. `wlr-randr` shows `Enabled: no` and a `0x0` mode while blanked |
| Layer shell | `zwlr_layer_shell_v1` v3. No `ext_session_lock_manager_v1` |
| Other globals | `zwlr_foreign_toplevel_manager_v1` v3, `zwlr_screencopy_manager_v1` v3, `zwlr_output_power_manager_v1` v1, `zwlr_virtual_pointer_manager_v1` v2, `zwlr_gamma_control_manager_v1` v1, `zwp_input_method_manager_v2`, `zwp_virtual_keyboard_manager_v1`, `zphoc_layer_shell_effects_v1` v3, `zphoc_device_state_v1` v2, `xdg_activation_v1`, `gtk_shell1` v3, `android_wlegl` v2 |
| Input | touch `fts_ts` = event3; `mtk-kpd` = event2; `custom_keys_input` = event1; `ACCDET` (headset) = event0. User is in `input` |
| Backlight | `/sys/class/leds/lcd-backlight`, max 2047. `/sys/class/backlight` is empty |
| Torch | `/sys/class/flashlight/mt-flash-led1`, `mt-flash-led2`. Nothing torch-like under `/sys/class/leds` |
| Vibration | `/sys/class/leds/vibrator`; `feedbackd` 0.8.9 running (`org.sigxcpu.Feedback`) |
| Sensors | `net.hadess.SensorProxy` (served by `hadess-sensorfw`): ambient light, proximity and accelerometer all present |
| Fingerprint | `net.reactivated.Fprint` served by `biomd-fprintd`, one device, no fingers enrolled |
| Battery | `/sys/class/power_supply/{ac,battery,charger,usb}`. No `charge_control_end_threshold` or similar |
| Call audio | `callaudiod` 0.1.10 installed, `org.mobian_project.CallAudio` activatable, not running |
| Phone apps | GNOME Calls 48.1 and Chatty 0.8.7 installed and bus-activatable, neither running. xx-dialer, TxxT, xx-contacts installed; `io.piercingxx.XXDialer.Policy` and `io.piercingxx.TxxT.Daemon` are activatable, not running |
| Android layer | `andromeda` container running (`io.furios.Andromeda.*`) |
| Helper | `/usr/libexec/xx-wm-helper` installed; `/var/lib/xx-wm-helper` does not exist yet. cgroup v2 |
| Portals | `xdg-desktop-portal` inactive; the shell runs with `GDK_DEBUG=no-portals`. `xx-wm-portals.conf` is installed |
| logind | Two `HandlePowerKey=ignore` drop-ins: FuriOS's own and `10-xx-wm-power.conf` |
| Packages | `apt-mark showhold` is empty; do not `apt upgrade` (systemd 261-rc3 note in `todo.md`) |

## Seen in the device logs (running `main`)

- `set-OSK-visible(False) over D-Bus failed … sm.puri.OSK0 was not provided` (3×).
- `FreezeUnit failed, stopping pid …: Unit app-xxwm-io.piercingxx.XXWeather.desktop-N.scope not loaded`.
- `vkCreateWaylandSurfaceKHR(): VK_ERROR_EXTENSION_NOT_PRESENT` at each start (GSK falls back).
- `Creating a portal monitor failed … StartServiceByName for org.freedesktop.impl.portal.desktop.gtk: Timeout was reached` (2×).
- `GtkGizmo (progress) reported min width -2` (12×).
- Launched apps write into the shell's `stderr.log` (camera QML, gnome-calculator).
- Shell process: 170 MB RSS, 24 threads, 94 fds two minutes after boot.
