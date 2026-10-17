# XX-WM privileged helper

Some shell features need root: battery charge limits (sysfs), per-app
network blocking (nftables + cgroupv2), cgroup freezing on devices
without systemd's `FreezeUnit`, system cache cleanup (package
caches, journal vacuum), system logs, package index refresh and
upgrades, single-package removal, curated service control, rfkill
radio blocks, and inbound firewall presets. The shell itself never
runs as root — those operations go through a small D-Bus system
service, `io.piercingxx.XXWM.Helper1`, and the shell talks to it via
`launcher/src/helper_client.py`.

## Layout

| File | Installed to | Purpose |
|---|---|---|
| `launcher/helper/xx_wm_helper.py` | `$libexecdir/xx-wm-helper` (mode 755) | daemon (Gio + stdlib only) |
| `launcher/data/dbus-1/system-services/io.piercingxx.XXWM.Helper1.service` | `$datadir/dbus-1/system-services/` | D-Bus activation, `User=root` |
| `launcher/data/dbus-1/system.d/io.piercingxx.XXWM.Helper1.conf` | `$datadir/dbus-1/system.d/` | bus policy: root owns, any caller may message the helper interfaces |
| `launcher/data/polkit-1/actions/io.piercingxx.XXWM.Helper.policy` | `$datadir/polkit-1/actions/` | fourteen actions, `allow_active=yes` except `…upgrade-system`, `…remove-package`, `…service-remote` and `…firewall-open` (`auth_admin_keep`), others `no` |
| `launcher/data/systemd/xx-wm-helper-boot.service` | `$datadir/systemd/system/` | oneshot `--apply-saved` at boot |
| `launcher/data/openrc/xx-wm-helper-boot` | `$sysconfdir/init.d/` | OpenRC equivalent |
| `launcher/src/helper_client.py` | shell datadir | async shell-side client |

Interface `io.piercingxx.XXWM.Helper1` at
`/io/piercingxx/XXWM/Helper1`: `GetCapabilities() → a{sv}`,
`SetChargeLimit(u)`, `SetAppNetwork(s, b)`, `FreezeCgroup(s, b)`,
`CleanSystem(as) → as`, `ReadLog(s) → s`, `RefreshPackageIndex()`,
`UpgradeSystem() → s`, `RemovePackage(s)`, `ManageService(s, s)`,
`SetRfkill(s, b)`, `SetPowerProfile(s)`, `SetInboundPreset(s, b)`,
`SetFirewallEnabled(b)`, `GetFirewallState() → a{sv}`.
The daemon exits after 60 s without a method call; the bus re-activates
it on demand.

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
| `GetCapabilities`, `GetFirewallState` | none (no auth) | — |

## Confinement

- `SetChargeLimit` clamps to 50..100, writes every
  `charge_control_end_threshold` (plus `start = limit − 5` where the
  kernel exposes it), and persists to
  `/var/lib/xx-wm-helper/state.json` (atomic tmp + rename, mode 600).
- `SetAppNetwork`/`FreezeCgroup` accept only canonical absolute paths
  under `/sys/fs/cgroup/user.slice/user-<caller uid>.slice/` — `..`,
  redundant separators, trailing slashes, NUL bytes, other users'
  slices, and symlink escapes are all rejected as NotAuthorized.
- nftables is driven exclusively through argv lists (`nft add table
  inet xxwm`, our own `output` chain + one
  `socket cgroupv2 level N "rel/path" drop` rule per blocked cgroup,
  reconciled by flush + re-add). Only our chain is ever flushed; other
  tables are untouched. No `shell=True` anywhere in the daemon.
- The match level is the cgroup's depth below the cgroup2 mount, and
  the match path is mount-relative, double-quoted inside its argv
  element so systemd escapes survive nft's parsing.
- Freeze state is transient and never persisted; charge limit, power
  profile and the blocked set are re-applied at boot by `--apply-saved`
  (each leg reports errors independently so one failure cannot skip
  the other).
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
- `UpgradeSystem` runs only `apk upgrade` and returns the output tail;
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

## Shell client

`helper_client` exposes async `get_capabilities` (cached per session,
`refresh=True` / `invalidate_capabilities()` to re-probe),
`set_charge_limit`, `set_app_network`, `freeze_cgroup`,
`clean_system`, `read_log`, `refresh_package_index`,
`upgrade_system`, `remove_package`, `manage_service`, `set_rfkill`,
`set_inbound_preset`, `set_inbound_allowed`, `set_firewall_enabled`,
`get_firewall_state` —
each calling `on_done(ok, result)` exactly once on the main loop. An
uninstalled or unactivatable helper reports `HELPER_NOT_INSTALLED`
(`'helper not installed'`) instead of raising.
