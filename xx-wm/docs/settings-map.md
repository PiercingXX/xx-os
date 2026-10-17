# Settings map

Every setting has one home. The menu follows Android's Settings, and
each page holds only its own sections (`SETTINGS_CATEGORIES` in
`launcher/src/settings_page.py`). Section keys are in backticks. They
are the deep-link targets for `open_settings(section)`, a quick-tile
long-press and IPC `settings.open:<section>`. No key or config key
changed in this pass, so every existing deep link still resolves.

The rules (owner, "minimal"):

- One entry point per feature. A row is never a second copy of controls
  that live elsewhere. Where a pointer would really help, it is a single
  link row, but the default is to have no second row at all.
- No empty-state lines: an empty list hides its card. A radio that is
  unavailable says so in its own row's subtitle.
- No confirmation popup for a direct action that was already picked from
  a menu. Destructive bulk actions (mirror backup, restore over the
  current layout, reset home) keep theirs.
- No decorative glyphs.

## Tree

- **Network & internet**: connectivity only
  - `wifi`: Wi-Fi switch (state in its subtitle; Start network service
    while NetworkManager is down), Networks (scan results, Scan, Add
    hidden), Saved networks
  - `mobile`: Access point (APN)
- **Connected devices**
  - `bluetooth`: Bluetooth switch (Unblock and Start service while
    blocked or down), Devices (known and found, Scan)
  - `nearby`: Nearby share (opens LocalSend)
- **Display & appearance**
  - `appearance`: Theme swatches; Look (Mode, Font, Import font, Text
    size)
  - `display`: Night light, Performance (power profile), Status bar
    height
- **Home & launcher**: the Android launcher's own preference screen
  (`docs/launcher-settings-parity.md`)
  - `launcher`: Home screen, Widgets, App drawer, Gestures (home swipes)
- **Sound**
  - `sound`: Output, Microphone (each hides when there are no devices)
  - `captions`: Live captions (status; the Captions tile switches it)
  - `recording`: Screen recording (status; the Record tile runs it)
- **Battery**
  - `battery`: level, health, cycles, temperature, voltage
  - `standby`: Battery saver, App standby
  - `charging`: Charge limit
  - `batteryuse`: Battery use since full charge
- **Apps**
  - `screentime`: Screen time today, App time and limits
  - `apps`: Installed apps (review and uninstall)
- **Privacy & security**
  - `lock`: Change PIN, Change device password
  - `smart_lock`: Smart Lock, trusted devices and networks
  - `permissions`: Privacy indicators, Recently used, By permission, By
    app
  - `security`: Malware scan, Security check
  - `findmy`: Find My Device (always off)
  - `medical`: Medical ID, Emergency contacts
- **Storage & backup**
  - `storage`: Storage, Free up space, Other space
  - `backup`: Backup & restore (export, restore, reset home layout),
    File backup
- **System**
  - `system`: Software update
  - `keyboard`: Keyboard layout
  - `gestures`: Navigation gestures (long-press home, the system swipes)
  - `developer`: Wireless debugging, Pair with code
  - `services`: ModemManager, EG25 modem helper, NetworkManager, BlueZ,
    SSH server, Tailscale, Waydroid container; Failed units
  - `firewall`: Incoming connections (Firewall plus one allow switch per
    preset)
  - `about`: About phone (version, device, OS, kernel, uptime, CPU,
    memory, temperatures, modem, IMEI, IP address), Logs
  - `shell`: the `~/.config/xx-wm/` footnote, last

Quick-tile long-press targets: Wi-Fi → `wifi`, Bluetooth →
`bluetooth`, Mobile data and Airplane → `mobile`, Night light →
`display`, Tailscale → `services`.

## What moved or went, and why

| Was | Now | Why |
| --- | --- | --- |
| `firewall` (Incoming connections: Firewall, Nearby share, Wireless debugging, SSH, KDE Connect, Syncthing) on Network & internet | On System, after `developer` and `services` | The reported duplicate. Network & internet listed SSH and Wireless debugging, which System also listed (Services › SSH server, Developer › Wireless debugging). Android's Network & internet holds connectivity only. What the phone serves (wireless debugging, its services, who may connect in) is Android's Developer options and Running services, under System. Each of those features now lives on one page. |
| Services rows titled Network, Bluetooth, Modem | NetworkManager, BlueZ, ModemManager (same keys, units and actions) | The rows manage the daemons. Their old titles read as second copies of Wi-Fi, Bluetooth and Mobile data. The Wi-Fi and Bluetooth pages already name the daemons ("Starts NetworkManager", "Starts bluetoothd"). |
| About phone › More › Storage (link row) | Removed | It was a second way into Storage & backup, which has its own menu row. Its background storage probe went with it. |
| About phone › More (caption) | Logs | Logs was the only row left in the card. |
| About phone › Network (IP list) | IP address | Android's About phone label. The old title read as a network setting. |
| Wi-Fi "Forget X?" and Bluetooth "Forget X?" popups | Forget runs straight from the network's or device's menu | Picking Forget in that menu is already the decision; Android forgets a Wi-Fi network at once too. |
| "No networks found.", "Wi-Fi is unavailable on this device." | Just the Scan rows | The Wi-Fi row's subtitle already says Off, Unavailable or Not connected. |
| "No devices yet. Scan to find some.", "Bluetooth is unavailable on this device." | Just the Scan row | The Bluetooth row's subtitle already gives the state. |
| Sound "No output devices." / "Audio system unavailable." | The card hides | Empty-state line. |
| Apps "No apps indexed yet.", Screen time "No screen time yet today.", Permissions "No use in the last 24 h." | The card (and its caption and footnote) hides | Empty-state lines. |

## Still overlapping

These overlaps remain on purpose: they are different controls, they sit
in rows that other lanes are building, or both.

- **System › Incoming connections lists SSH, Wireless debugging and
  Nearby share.** Those are firewall allow rules (open port 22, the xxdb
  range, the LocalSend port), not copies of the SSH server, Wireless
  debugging or Nearby share controls. They sit on the same page as the
  first two now. The finished shape is one row per feature: the SSH
  server row and the Wireless debugging switch would open their own
  ports, and Firewall would be a single on/off row. That belongs to the
  Firewall, SSH and Wireless debugging rows' own work. This pass only
  moved them.
- **Home & launcher › Show Status Bar and Display › Status bar height.**
  These are different settings: one controls the clock and glyphs, the
  other the strip's height. Show Status Bar is pinned to the launcher's
  parity screen. First boot also sets the height (the Status bar step),
  and that is a setup step, not a second Settings entry.
