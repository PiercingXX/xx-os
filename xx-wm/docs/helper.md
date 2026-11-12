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
| `launcher/data/polkit-1/actions/io.piercingxx.XXWM.Helper.policy` | `$datadir/polkit-1/actions/` | fifteen actions, `allow_active=yes` except `…upgrade-system`, `…remove-package`, `…service-remote` and `…firewall-open` (`auth_admin_keep`), others `no` |
| `launcher/data/systemd/xx-wm-helper-boot.service` | `$datadir/systemd/system/` | oneshot `--apply-saved` at boot |
| `launcher/data/openrc/xx-wm-helper-boot` | `$sysconfdir/init.d/` | OpenRC equivalent |
| `launcher/src/helper_client.py` | shell datadir | async shell-side client |

Interface `io.piercingxx.XXWM.Helper1` at
`/io/piercingxx/XXWM/Helper1`: `GetCapabilities() → a{sv}`,
`SetChargeLimit(u)`, `SetAppNetwork(s, b)`, `FreezeCgroup(s, b)`,
`CleanSystem(as) → as`, `ReadLog(s) → s`, `RefreshPackageIndex()`,
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

`UpgradeSystem` and `RemovePackage` start package jobs: after polkit
and validation they reply at once with a job id and run the package
manager on a worker thread with no kill timeout (SIGKILLing apk or
pacman mid-transaction can break the system). When it exits, the
helper sends `JobFinished(job_id, rc, tail)` to the calling bus name
only: `rc` is the exit status (negative for a signal, -1 when it could
not start) and `tail` the last 40 lines of its output (at most 4000
characters; package progress only, never arguments or environment).
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
inbound port, disabling the firewall) — pass flags 1
(AllowUserInteraction) so the in-shell polkit agent prompts for the
device password. Closing a port, enabling the firewall and every other
allowlisted service change stay non-interactive.

Validation (allowlists) happens before authorization: `policy_for`
returns None for unknown methods and out-of-allowlist arguments, and
the service refuses those with InvalidArgs without contacting polkit.

| Method / branch | Action | Default |
|---|---|---|
| `SetChargeLimit` | `…set-charge-limit` | `allow_active=yes` |
| `SetAppNetwork` | `…app-network` | `allow_active=yes` |
| `FreezeCgroup` | `…freeze` | `allow_active=yes` |
| `CleanSystem` | `…clean-system` | `allow_active=yes` |
| `ReadLog` | `…read-log` | `allow_active=yes` |
| `RefreshPackageIndex` | `…refresh-packages` | `allow_active=yes` |
| `UpgradeSystem` | `…upgrade-system` | `allow_active=auth_admin_keep` |
| `RemovePackage` | `…remove-package` | `allow_active=auth_admin_keep` |
| `ManageService` ssh/adbd start/enable | `…service-remote` | `allow_active=auth_admin_keep` |
| `ManageService` other allowlisted service/action | `…service-manage` | `allow_active=yes` |
| `SetRfkill` | `…rfkill` | `allow_active=yes` |
| `SetPowerProfile` | `…power-profile` | `allow_active=yes` |
| `SetInboundPreset` open (`allowed` true) | `…firewall-open` | `allow_active=auth_admin_keep` |
| `SetInboundPreset` close (`allowed` false) | `…firewall-restrict` | `allow_active=yes` |
| `SetInboundAllowed` non-empty (leaves ports open) | `…firewall-open` | `allow_active=auth_admin_keep` |
| `SetInboundAllowed` empty (closes everything) | `…firewall-restrict` | `allow_active=yes` |
| `SetFirewallEnabled` disable (`enabled` false) | `…firewall-open` | `allow_active=auth_admin_keep` |
| `SetFirewallEnabled` enable (`enabled` true) | `…firewall-restrict` | `allow_active=yes` |
| `SetWakeAlarm`, `ClearWakeAlarm` | `…wake-alarm` | `allow_active=yes` |
| `HasPin`, `VerifyPin`, `SetPin`, `ClearPin` | none (caller's own uid) | — |
| `GetCapabilities`, `GetFirewallState` | none (no auth) | — |

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
  skipped, and the reply lists what actually ran. Non-destructive, so
  its action stays `allow_active=yes` with the non-interactive check.
- `ReadLog` accepts only its fixed allowlist (`messages`, `xx-wm`,
  `dmesg`) — never a free-form path. Read-only and size-capped, so
  its action stays `allow_active=yes`.
- `RefreshPackageIndex` runs only `apk update` (never a partial
  `pacman -Sy`); harmless, so its action stays `allow_active=yes`.
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
  kdeconnect, syncthing): the live `inet xx_wm` table (accept-only
  input chain, reconciled by flush + re-add — other tables untouched)
  when nftables is present, `ufw allow|delete` only where nft is
  missing, plus the `/etc/nftables.d/10-xxwm.nft` drop-in and a
  persisted allowed-set that `--apply-saved` re-applies at boot. An
  accept there cannot override a drop in another base chain;
  `GetFirewallState` reports such a `foreign_drop` so the shell says
  so.
- `SetInboundAllowed` replaces the whole owned set with the features'
  derived presets (one row per feature: the feature's own control
  owns its port; the shell re-applies this on every feature or
  Firewall-switch change). Same allowlist and backends as
  `SetInboundPreset` (ufw reconciled to the set). Migration: the
  first call unions the derived set with the old per-preset rows'
  stored allow-state, so a previously allowed port stays allowed
  (its feature counts as on for the firewall only) without starting
  any service; the stored keys are read once, then left alone.
- `SetFirewallEnabled` enables (nftables installs the preset chain;
  ufw fallback: `default deny incoming` / `allow outgoing`, keeping
  port 22 when `SSH_CONNECTION` is set, then `--force enable`) or
  disables (nftables removes the owned table and drop-in: off means
  no table, live or at boot).

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
`upgrade_system` and `remove_package` subscribe to `JobFinished` (sent
by the helper's name) before the call and fire `on_done` when their
job's signal arrives, not when the call returns; the helper vanishing
mid-job fails it. Timeouts are per method: 10 s for quick calls (state
probes, PIN, charge limit, rfkill, power profile, wake alarm), none for
calls that may wait on the password dialog (`UpgradeSystem`,
`RemovePackage`, `ManageService`, `SetInboundPreset`,
`SetInboundAllowed`, `SetFirewallEnabled`), 25 s for the rest.
