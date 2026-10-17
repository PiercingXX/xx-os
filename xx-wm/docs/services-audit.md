# Services audit: firewall, wireless debugging, SSH, KDE Connect, Syncthing

Traced end to end on the FuriPhone FLX1 (FuriOS, Debian forky, apt),
with pmOS (Alpine, apk, OpenRC or systemd) and Arch (pacman, systemd)
as the other targets. Orchestrator facts: openssh-server installed and
active; nftables and iptables present, ufw and firewalld absent;
kdeconnect, syncthing and adbd NOT installed.

| feature | backend | works today on apt / apk / pacman | what was broken | fix |
|---|---|---|---|---|
| Firewall | helper `SetInboundPreset` / `SetFirewallEnabled` / `GetFirewallState`; UI `settings_firewall.FirewallSection` | apt: yes (nftables fallback) / apk: yes / pacman: yes | Backend preferred ufw when installed (`apps.sh` installed and enabled ufw), so a phone with ufw never used the nftables path that is present everywhere. Owned table was `inet xxwm-inbound`, not the `inet xx_wm` contract. Presets for ssh/kdeconnect/syncthing existed but the backend order hid them behind ufw. | Backend prefers nftables when present, ufw only as fallback; owned table is `inet xx_wm` (never flushes other tables); ssh/kdeconnect/syncthing presets kept and covered by tests; `apps.sh` ensures nftables instead of enabling ufw. |
| SSH | `settings_services` ssh entry -> helper `ManageService('ssh', …)` (systemd `sshd.service`/`ssh.service` probe, OpenRC `sshd`); polkit `service-remote` | apt: yes / apk: yes / pacman: yes | Row showed only On/Off, never the listening address. Key-only was incidentally preserved (helper never writes `sshd_config`) but nothing stated or checked it. | Row subtitle appends the listening address (`<lan-ip>:22`) probed on the worker thread; helper still never touches `sshd_config`, and the UI reports key-only vs password-allowed from the config parse without ever changing it. ssh start/enable stays `auth_admin_keep` interactive. |
| Wireless debugging (xxdb) | built-in `xxdb` daemon via `XxdbController` (`settings_page.DeveloperSection`), ports 37000-37999 | apt: yes / apk: yes / pacman: yes | Works already (no package needed). It is NOT adbd: there was no adbd toggle at all. | Kept as-is. No dead adbd toggle is shown (see next row). |
| Wireless debugging (adbd) | NEW `adbd` system service via helper `ManageService('adbd', …)` (`adbd.service` / `adbd`), row in Services | apt: after suite install / apk: hidden / pacman: hidden | No row, no backend, no package. Debian provides the daemon as `adbd` (current) with `android-tools-adbd` as the older name; Alpine `android-tools` ships no adbd binary (upstream issue) and Arch `android-tools` is client-only, so a toggle there would always be dead. | `adbd` added to the helper allowlist (start/enable uses the interactive `service-remote` action like ssh); the Services row renders only when the `adbd` binary is present and says nothing otherwise. Suite installs `adbd` (fallback `android-tools-adbd`) on apt only. |
| KDE Connect | NEW user service `kdeconnectd` via `systemctl --user` (no root); logic in `settings_connect` | apt: after suite install / apk: after suite install / pacman: after suite install | No row, no backend, package not installed. | New Services rows: true state (`Not installed` / `Off` / `On · <devices>`) probed on the worker thread with the failure reason inline; Start/Stop/Enable/Disable run `systemctl --user` (argv lists, allowlisted units/actions only); device list from `kdeconnect-cli` shown in the subtitle/menu. Suite installs `kdeconnect` on all three distros. |
| Syncthing | NEW user service `syncthing` via `systemctl --user`; row opens `http://127.0.0.1:8384` | apt: after suite install / apk: after suite install / pacman: after suite install | No row, no backend, package not installed. | Same pattern as KDE Connect with the `syncthing` unit; the row menu has `Open web UI` (`Gio.AppInfo.launch_default_for_uri`), no popups. Suite installs `syncthing` on all three distros. |

## Toggle traces (what each switch calls)

- Firewall preset switch -> `helper_client.set_inbound_preset(name, allowed)`
  -> helper `SetInboundPreset` (allowlist `INBOUND_PRESETS`; open =
  `firewall-open`/`auth_admin_keep` interactive, close =
  `firewall-restrict`) -> nftables `inet xx_wm` reconcile + drop-in +
  persisted set (or ufw fallback). Status -> `GetFirewallState` (no
  auth) rendered async.
- Firewall on/off -> `helper_client.set_firewall_enabled(enabled)` ->
  helper `SetFirewallEnabled` (enable = `firewall-restrict`, disable =
  `firewall-open`/`auth_admin_keep` interactive).
- SSH Start/Stop/Restart/Enable/Disable -> `helper_client.manage_service(
  'ssh', action)` -> helper `ManageService` (allowlisted; start/enable =
  `service-remote`/`auth_admin_keep` interactive, rest =
  `service-manage`) -> `systemctl <action> sshd.service|ssh.service`
  or `rc-service`/`rc-update`. Address comes from `ss -tln` + `ip
  -brief addr show` parsed on the worker thread, never the main loop.
- KDE Connect / Syncthing switches and menus -> `settings_connect`
  `systemctl --user` argv lists directly (unprivileged, no helper, no
  polkit, no shell strings). Status from `is-active`/`is-enabled` plus
  `kdeconnect-cli --list-devices` for pairing, all on the worker
  thread; failures land inline in the row subtitle.
- adbd switch -> `helper_client.manage_service('adbd', action)` like
  ssh (start/enable interactive). Hidden entirely when `adbd` is not
  on PATH.

## Verified package names

Checked against the distro indexes named (web verification, 2026-10-17;
the lane sandbox has no apt/apk databases):

| distro | ssh | kdeconnect | syncthing | adbd daemon | nftables |
|---|---|---|---|---|---|
| apt (Debian forky / FuriOS) | `openssh-server` | `kdeconnect` | `syncthing` | `adbd` (fallback `android-tools-adbd`) | `nftables` |
| apk (pmOS/Alpine) | `openssh` | `kdeconnect` | `syncthing` | unavailable (`android-tools` ships no adbd binary) | `nftables` |
| pacman (Arch) | `openssh` | `kdeconnect` | `syncthing` | unavailable (`android-tools` is client-only) | `nftables` |

Install path: the suite (`scripts/apps.sh`) installs the backends with
these names (guarded, never fatal); there was no opt-in
install-through-helper pattern in the tree (only `RemovePackage`), so
no new privileged install method was added. Rows still report the true
`Not installed` state when a binary is missing, and the adbd row hides.
