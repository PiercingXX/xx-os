# XX-WM privileged helper

Some shell features need root: battery charge limits (sysfs), per-app
network blocking (nftables + cgroupv2), cgroup freezing on devices
without systemd's `FreezeUnit`, system cache cleanup (package
caches, journal vacuum), system logs, package index refresh and
upgrades, single-package removal, curated service control, rfkill
radio blocks, inbound firewall presets, the lock-screen PIN hash, and
the RTC wake alarm that lets alarms fire from suspend. The shell itself never
runs as root — those operations go through a small D-Bus system
service, `io.piercingxx.XXWM.Helper1`, and the shell talks to it via
`launcher/src/helper_client.py`.

## Layout

| File | Installed to | Purpose |
|---|---|---|
| `launcher/helper/xx_wm_helper.py` | `$libexecdir/xx-wm-helper` (mode 755) | daemon (Gio + stdlib only) |
| `launcher/data/dbus-1/system-services/io.piercingxx.XXWM.Helper1.service` | `$datadir/dbus-1/system-services/` | D-Bus activation, `User=root` |
| `launcher/data/dbus-1/system.d/io.piercingxx.XXWM.Helper1.conf` | `$datadir/dbus-1/system.d/` | bus policy: root owns, any caller may message the helper interfaces |
| `launcher/data/polkit-1/actions/io.piercingxx.XXWM.Helper.policy` | `$datadir/polkit-1/actions/` | sixteen actions, `allow_active=yes` except `…upgrade-system`, `…remove-package`, `…service-remote`, `…firewall-open` and `…firewall-enable` (`auth_admin_keep`), others `no` |
| `launcher/data/systemd/xx-wm-helper-boot.service` | `$datadir/systemd/system/` | oneshot `--apply-saved` at boot |
| `launcher/data/openrc/xx-wm-helper-boot` | `$sysconfdir/init.d/` | OpenRC equivalent |
| `launcher/src/helper_client.py` | shell datadir | async shell-side client |

Interface `io.piercingxx.XXWM.Helper1` at
`/io/piercingxx/XXWM/Helper1`: `GetCapabilities() → a{sv}`,
`SetChargeLimit(u)`, `SetAppNetwork(s, b)`, `FreezeCgroup(s, b)`,
`CleanSystem(as) → s`, `ReadLog(s) → s`, `RefreshPackageIndex() → s`,
`UpgradeSystem() → s`, `RemovePackage(s) → s`, `ManageService(s, s)`,
`SetRfkill(s, b)`, `SetPowerProfile(s)`, `SetInboundPreset(s, b)`,
`SetInboundAllowed(as)`, `SetFirewallEnabled(b)`,
`GetFirewallState() → a{sv}`, `SetWakeAlarm(t)`, `ClearWakeAlarm()`,
`HasPin() → b`, `VerifyPin(s) → (b, u, b)`, `SetPin(s, s) → (b, u)`,
`ClearPin(s) → (b, u)`, and the signal `JobFinished(s job_id, i rc,
s tail)`.
The daemon exits after 60 s without a method call, but never while a
polkit check (an open password dialog) or a package job is still
running; the bus re-activates it on demand.

## Package jobs

`UpgradeSystem`, `RemovePackage`, `RefreshPackageIndex` and
`CleanSystem` start package jobs: after polkit and validation they reply
at once with a job id and run the package manager (or the cleanups, one
after another) on a worker thread with no kill timeout (SIGKILLing apk
or pacman mid-transaction can break the system; a slow mirror or a big
cache outlasts any fixed limit). When it exits, the
helper sends `JobFinished(job_id, rc, tail)` to the calling bus name
only: `rc` is the exit status (negative for a signal, -1 when it could
not start) and `tail` the last 40 lines of its output (at most 4000
characters; package progress only, never arguments or environment).
For `CleanSystem` a successful `tail` lists the cleaned categories, one
per line; a failed step ends the job with `clean <category>: <its last
output line>`, and a step whose tool is missing (127) is skipped.
One package job runs at a time; a second is refused with Failed.
`CheckAuthorization` for interactive actions has no timeout, so the
check lasts as long as the password dialog stays open; non-interactive
checks give up after 10 s.

## Authorization

One mechanism: every call goes through `policy_for(method, args) →
(action_id, interactive)`, which maps each method to its polkit action.
Plain methods map statically; argument-dependent ones decide from the
already-validated arguments (see the table below). Every mutating
method then resolves the caller's uid with
`org.freedesktop.DBus.GetConnectionUnixUser` and checks polkit
`CheckAuthorization` for subject `system-bus-name` = sender against
that action, all async.
Denial (or an unresolvable sender) returns
`io.piercingxx.XXWM.Helper1.Error.NotAuthorized`.
`GetCapabilities` and `GetFirewallState` are intentionally
unauthenticated — they only report state so the shell can render it.
The PIN methods take no polkit action either: they resolve the caller's
uid the same way and act only on that uid's own record (see PIN store
below).

`CheckAuthorization` is called with flags 0 (non-interactive) for the
`allow_active` actions. The `auth_admin_keep` actions —
`…upgrade-system`, `…remove-package`, `…service-remote` (ssh/adbd
start/enable, which opens remote access), `…firewall-open` (opening an
inbound port, disabling the firewall), `…firewall-enable` (turning the
drop policy on, which can cut new inbound connections), `…service-critical`
(stopping or disabling the modem or network), `…read-log` (system logs and
dmesg) and `…clean-journal` (journal vacuum) — pass flags 1
(AllowUserInteraction) so the in-shell polkit agent prompts for the
device password. Closing a port and every other allowlisted service
change stay non-interactive.

Validation (allowlists) happens before authorization: `policy_for`
returns None for unknown methods and out-of-allowlist arguments, and
the service refuses those with InvalidArgs without contacting polkit.

| Method / branch | Action | Default |
|---|---|---|
| `SetChargeLimit` | `…set-charge-limit` | `allow_active=yes` |
| `SetAppNetwork` | `…app-network` | `allow_active=yes` |
| `FreezeCgroup` | `…freeze` | `allow_active=yes` |
| `CleanSystem` caches only | `…clean-system` | `allow_active=yes` |
| `CleanSystem` with `journal-vacuum` | `…clean-journal` | `allow_active=auth_admin_keep` |
| `ReadLog` | `…read-log` | `allow_active=auth_admin_keep` |
| `RefreshPackageIndex` | `…refresh-packages` | `allow_active=yes` |
| `UpgradeSystem` | `…upgrade-system` | `allow_active=auth_admin_keep` |
| `RemovePackage` | `…remove-package` | `allow_active=auth_admin_keep` |
| `ManageService` ssh/adbd start/enable | `…service-remote` | `allow_active=auth_admin_keep` |
| `ManageService` modem/eg25/network stop/disable | `…service-critical` | `allow_active=auth_admin_keep` |
| `ManageService` other allowlisted service/action | `…service-manage` | `allow_active=yes` |
| `SetRfkill` | `…rfkill` | `allow_active=yes` |
| `SetPowerProfile` | `…power-profile` | `allow_active=yes` |
| `SetInboundPreset` open (`allowed` true) | `…firewall-open` | `allow_active=auth_admin_keep` |
| `SetInboundPreset` close (`allowed` false) | `…firewall-restrict` | `allow_active=yes` |
| `SetInboundAllowed` non-empty (leaves ports open) | `…firewall-open` | `allow_active=auth_admin_keep` |
| `SetInboundAllowed` empty (closes everything) | `…firewall-restrict` | `allow_active=yes` |
| `SetFirewallEnabled` disable (`enabled` false) | `…firewall-open` | `allow_active=auth_admin_keep` |
| `SetFirewallEnabled` enable (`enabled` true) | `…firewall-enable` | `allow_active=auth_admin_keep` |
| `SetWakeAlarm`, `ClearWakeAlarm` | `…wake-alarm` | `allow_active=yes` |
| `HasPin`, `VerifyPin`, `SetPin`, `ClearPin` | none (caller's own uid) | — |
| `GetCapabilities`, `GetFirewallState` | none (no auth) | — |

## Sessions and polkit

`allow_active` and `auth_admin_keep` only apply to a caller that polkit
finds in an *active* logind session: a session on `seat0` whose VT is in
the foreground. With no session every action falls to `allow_any=no`, so
every mutating helper call returns NotAuthorized, and the in-shell agent
has no session to register for (`XDG_SESSION_ID` is unset).

Only a PAM login creates that session. On systemd hosts the greeter or
the display manager does it. On OpenRC (older postmarketOS images) the
pieces are:

- **elogind**, running (`rc-update add elogind default`), and Alpine's
  `/etc/pam.d/base-session`, which runs `pam_elogind.so`.
- **`polkit-elogind`**: Alpine's plain `polkit` build tracks no
  sessions at all, so nobody is ever active. install.sh installs the
  elogind build on OpenRC hosts.
- **A session starter that goes through PAM**: tinydm (postmarketOS's
  autologin, through `autologin` and its PAM service) or greetd.
  install.sh points tinydm at `xx-wm.desktop` with `tinydm-set-session`,
  or enables greetd and offers autologin, and drops the
  `/etc/init.d/xx-wm` service from the default runlevel. That service
  runs the session from supervise-daemon with no PAM login; install.sh
  only enables it when neither starter exists, with a warning, and
  the script warns at every start.

Verify on the device, from an ssh login while the shell runs:

```sh
apk info -e polkit-elogind elogind          # both installed
rc-status default | grep -E 'elogind|tinydm|greetd|xx-wm'
loginctl list-sessions                      # a row for the user on seat0 with a TTY
sid=$(loginctl list-sessions --no-legend | awk -v u="$USER" '$3 == u && $4 == "seat0" {print $1; exit}')
loginctl show-session "$sid" -p Active -p State -p Seat -p VTNr -p Type -p Class
# Active=yes State=active Seat=seat0 Type=wayland Class=user
pid=$(pgrep -u "$USER" -f /usr/share/xx-wm/main.py)
tr '\0' '\n' < /proc/"$pid"/environ | grep XDG_SESSION_ID   # same id as $sid
pkcheck --action-id io.piercingxx.XXWM.Helper1.set-charge-limit --process "$pid"; echo $?
# 0 (allow_active=yes)
pkcheck --action-id io.piercingxx.XXWM.Helper1.upgrade-system --process "$pid"; echo $?
# 2: authentication required, which the in-shell agent would ask for
```

Then in the shell: change the battery charge limit (no prompt; the helper
log has no NotAuthorized), and start a system upgrade (the device
password prompt appears). Switching to another VT makes the session
inactive, and the same `pkcheck` then exits 1.

## Confinement

- `SetChargeLimit` clamps to 50..100, writes every
  `charge_control_end_threshold` (plus `start = limit − 5` where the
  kernel exposes it), and persists to
  `/var/lib/xx-wm-helper/state.json` (atomic tmp + rename, mode 600).
- `SetAppNetwork`/`FreezeCgroup` accept only canonical absolute paths
  under `/sys/fs/cgroup/user.slice/user-<caller uid>.slice/` — `..`,
  redundant separators, trailing slashes, NUL bytes, other users'
  slices, and symlink escapes are all rejected as NotAuthorized. Every
  component below the slice must match `[A-Za-z0-9_.:@-]+` (no quotes,
  spaces, `;`, braces, backslashes or newlines), and a path to block or
  freeze must be an existing directory.
- No caller string is ever nft grammar, and nft never sees argv
  rulesets. The per-app table is one script on nft's stdin
  (`add table inet xxwm; delete table inet xxwm; table inet xxwm {…}`
  with an `output` chain and one `socket cgroupv2 level N "rel/path"
  drop` rule per blocked cgroup). nft applies stdin as a single
  transaction: if it refuses, the previous table stays live, so
  existing blocks never fall away. Text, not JSON, because nft's JSON
  parser has no `socket cgroupv2`; the path literal holds only the
  validated characters. Other tables are untouched, and there is no
  `shell=True` anywhere in the daemon.
- nft's stderr goes to the helper's own log, never into a D-Bus error
  (it can echo input back); callers get a fixed message.
- The match level is the cgroup's depth below the cgroup2 mount, and
  the match path is mount-relative.
- Blocked scopes carry the app's pid, so they go stale when the app
  exits. Every apply drops paths whose directory is gone or that no
  longer validate, and persists only what was applied; the shell blocks
  again on each launch of a denied app.
- Freeze state is transient and never persisted; charge limit, power
  profile and the blocked set are re-applied at boot by `--apply-saved`
  (each leg reports errors independently so one failure cannot skip
  the other). Persisted blocked paths are validated again there, for
  the uid their own slice names; tampered or stale entries are dropped,
  never handed to nft.
- `CleanSystem` accepts only its fixed allowlist (`apk-cache`,
  `paccache-keep`, `paccache-uninstalled`, `apt-clean`,
  `journal-vacuum`) as argv lists — never a package removal, never a
  free-form command. `apk-cache` runs only when `/etc/apk/cache`
  exists, `journal-vacuum` only on systemd; missing binaries are
  skipped, and `JobFinished` lists what actually ran. Non-destructive, so
  its action stays `allow_active=yes` with the non-interactive check.
- `ReadLog` accepts only its fixed allowlist (`messages`, `xx-wm`,
  `dmesg`) — never a free-form path. Read-only and size-capped, so
  its action stays `allow_active=yes`.
- `RefreshPackageIndex` runs only `apk update` (never a partial
  `pacman -Sy`) as a package job; harmless, so its action stays
  `allow_active=yes`.
- `UpgradeSystem` runs only `apk upgrade` as a package job (the reply
  is the job id, the output tail comes with `JobFinished`);
  interactive `auth_admin_keep`.
- `RemovePackage` accepts only a safe single package name (lowercase
  alnum plus `.+-_`, max 128 chars) and always refuses the protected
  list (kernels, privilege escalation, the shell and its stack);
  interactive `auth_admin_keep`.
- `ManageService` accepts only the curated keys (`modem`, `eg25`,
  `network`, `bluetooth`, `ssh`, `adbd`, `tailscale`, `waydroid`) and
  the five actions (start/stop/restart/enable/disable); anything else
  is InvalidArgs, so xx-wm, dbus and logind units are unreachable.
  Units resolve per init (systemd unit names with the `sshd`/`ssh`
  spelling probe, OpenRC `/etc/init.d` names) via argv lists, never a
  shell. `ssh`/`adbd` start/enable (remote access) use the interactive
  `service-remote` action; the helper never writes `sshd_config`, so
  enabling SSH never flips password auth on silently.
- `SetRfkill` accepts only `wifi`/`bluetooth` (`rfkill block|unblock`).
- `SetPowerProfile` accepts only `power-saver`/`balanced`/`performance`
  and maps each to the preferred cpufreq governor the policy offers
  (`powersave`/`conservative`, `schedutil`/`ondemand`, `performance`);
  policies offering none of the preferred governors are skipped, and
  the choice persists for `--apply-saved`. Governor writes are
  non-destructive, so the action stays `allow_active=yes`.
- `SetInboundPreset` accepts only the five presets (nearby, xxdb, ssh,
  kdeconnect, syncthing): with nftables they open ports in the live
  `inet xx_wm` table while the firewall is on (`ufw allow|delete` only
  where nft is missing), plus the `/etc/nftables.d/10-xxwm.nft` drop-in
  and a persisted allowed-set that `--apply-saved` re-applies at boot.
  An accept there cannot override a drop in another base chain;
  `GetFirewallState` reports such a `foreign_drop` so the shell says
  so.
- The `inet xx_wm` input chain is a real policy: `policy drop`, then
  accept `ct state established,related`, `iif lo`, ICMP and ICMPv6,
  DHCP (`udp sport 67 dport 68`) and DHCPv6 (`udp sport 547 dport
  546`) replies, then the allowed presets. It is built as an `nft -j`
  document of constants and fed on stdin as one transaction that
  replaces our table whole.
- `GetFirewallState` parses `nft -j list ruleset`: `enabled` is true
  only while our input chain is live with `policy drop` (ufw: active
  with a deny or reject incoming default), and a preset counts as
  allowed only when every one of its exact ports or ranges is accepted
  (`22` never matches `22000`).
- `SetInboundAllowed` replaces the whole owned set with the features'
  derived presets (one row per feature: the feature's own control
  owns its port; the shell re-applies this on every feature or
  Firewall-switch change). Same allowlist and backends as
  `SetInboundPreset` (ufw reconciled to the set). Migration: the
  first call unions the derived set with the old per-preset rows'
  stored allow-state, so a previously allowed port stays allowed
  (its feature counts as on for the firewall only) without starting
  any service; the stored keys are read once, then left alone.
- `SetFirewallEnabled` enables (nftables installs the drop-policy
  chain; ufw fallback: `default deny incoming` / `allow outgoing`, then
  `--force enable`) or disables (nftables removes the owned table and
  drop-in: off means no table, live or at boot). Only an enabled
  firewall gets a chain: `SetInboundAllowed` and `SetInboundPreset`
  never turn the drop policy on by themselves.
- Enabling keeps TCP port 22 open while something listens on it
  (`/proc/net/tcp{,6}`) or the SSH service is enabled at boot
  (`systemctl is-enabled` sshd/ssh service or ssh.socket; OpenRC
  default runlevel). The helper is D-Bus activated, so the caller's
  `SSH_CONNECTION` is never visible to it. On nftables that rule is
  commented `xxwm keep-ssh` and not read back as the SSH feature.

- `SetWakeAlarm(t epoch)` accepts only an integer absolute epoch
  between 5 s and 48 h from now (InvalidArgs otherwise, checked against
  the helper's clock). It writes `/sys/class/rtc/rtc0/wakealarm`: `0`
  first (the kernel refuses a new alarm while one is pending), then the
  epoch. The helper owns at most one wake, recorded in
  `/var/lib/xx-wm-helper/wake.json` (0600) because the daemon exits
  when idle; re-arming replaces it. A pending wake some other program
  set (still in the future, not ours) is never overwritten: the call
  fails and automatic suspend falls back to staying awake for the alarm.
  No RTC (`rtc0` missing) fails too. `ClearWakeAlarm()` writes `0` only
  when the pending wake is still the one recorded, then drops the
  record. The `…wake-alarm` action is `allow_active=yes`, non-interactive:
  only the active local session may schedule wakes.

## PIN store

`HasPin`, `VerifyPin`, `SetPin` and `ClearPin` keep the lock-screen PIN
out of the user-readable config, so no process running as the user can
read the hash and brute-force it offline:

- One record per uid at `/var/lib/xx-wm-helper/pin/<uid>.json` (dir
  0700, file 0600, atomic tmp + rename), holding a scrypt hash
  (N = 2^15, r = 8, p = 1; ~32 MiB, a few hundred ms on a phone SoC,
  paid by the helper, never the shell's main loop) and the failure
  state. A corrupt record fails closed (a PIN exists that nothing
  matches).
- The uid comes from `GetConnectionUnixUser` on the caller's bus name,
  never from an argument, so a user can only query or change their own
  PIN. Argument shapes are checked before the uid lookup.
- `VerifyPin(pin) → (matched, retry_after, has_pin)`; `SetPin(old, new)`
  and `ClearPin(old)` verify `old` first (empty when no PIN is set) and
  return `(done, retry_after)`. PINs are 1–64 characters.
- Lockout escalates like the shell's (`lock_lines._lockout_secs`, pinned
  together by a test): 30 s at the 5th wrong PIN, 30 s more for each
  one after, capped at 300 s. It is persisted root-side and checked before any KDF runs,
  so restarting the helper or the shell does not reset it.
- PIN text never appears in a reply, error message or log.
- The shell's config keeps only a marker (`pin_hash` = the helper
  marker) so "is a PIN set?" stays synchronous; without the helper the
  local pbkdf2 hash keeps working and migrates on the next good unlock.
- Startup (`helper_client.startup_pin_gate`, run by `main.py` before the
  first-boot wizard or the shell shows anything): a config without the
  marker asks `HasPin`. A PIN there restores the marker and the session
  comes up locked; the wizard after a lost `config.json` runs only after
  that PIN is entered, and skips its PIN step. No answer (an error, or
  nothing within 3 s) fails closed **when the helper is installed**,
  meaning its activation file `io.piercingxx.XXWM.Helper1.service`
  exists in one of the system bus service dirs
  (`/usr/local/share`, `/usr/share`, `/lib`, `/etc` + `dbus-1/system-services`):
  the marker is assumed for that process (not written), the lock comes
  up, and the PIN pad verifies through the helper once it answers (a
  helper that then reports no PIN unlocks on the first entry, and a late
  `HasPin` that says no PIN turns the lock back to swipe-only). A helper
  that is not installed cannot hold a PIN, so its silence never locks; a
  helper that is installed but broken keeps the session locked until it
  is fixed (over ssh or a TTY). Dev runs (`XX_WM_PIN_HELPER=0`, or no
  `XX_WM_SESSION`) never ask.
- A local pbkdf2 hash in config at startup is asked about too: when the
  helper holds a PIN, the helper's PIN wins and the local hash is
  replaced by the marker, so a hash written into `config.json` while the
  marker was gone never becomes the PIN. A helper with no record leaves
  the local hash in charge; a silent installed helper assumes the marker
  as above, and if it then turns out to hold no PIN the local hash, not
  "no PIN", is what unlocks.
- Once this process has seen the helper hold the PIN, every config
  instance reports the marker until the PIN is removed: a hot reload
  that drops it, sets it to `null` or swaps in a local hash changes
  nothing.

## Shell client

`helper_client` exposes async `get_capabilities` (cached per session,
`refresh=True` / `invalidate_capabilities()` to re-probe),
`set_charge_limit`, `set_app_network`, `freeze_cgroup`,
`clean_system`, `read_log`, `refresh_package_index`,
`upgrade_system`, `remove_package`, `manage_service`, `set_rfkill`,
`set_inbound_preset`, `set_inbound_allowed`, `set_firewall_enabled`,
`get_firewall_state`, `set_wake_alarm`, `clear_wake_alarm`, `has_pin`,
`verify_pin`, `set_pin`, `clear_pin` (wrapped by `pin_verify`,
`pin_change`, `pin_sync`, `migrate_pin`) —
each calling `on_done(ok, result)` exactly once on the main loop. An
uninstalled or unactivatable helper reports `HELPER_NOT_INSTALLED`
(`'helper not installed'`) instead of raising.
`upgrade_system`, `remove_package`, `refresh_package_index` and
`clean_system` subscribe to `JobFinished` (sent
by the helper's name) before the call and fire `on_done` when their
job's signal arrives, not when the call returns; the helper vanishing
mid-job fails it. Timeouts are per method: 10 s for quick calls (state
probes, PIN, charge limit, rfkill, power profile, wake alarm), none for
calls that may wait on the password dialog (`UpgradeSystem`,
`RemovePackage`, `ManageService`, `SetInboundPreset`,
`SetInboundAllowed`, `SetFirewallEnabled`), 25 s for the rest.
