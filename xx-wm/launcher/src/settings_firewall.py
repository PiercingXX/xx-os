"""Firewall settings section: one row; the firewall follows the features.

One entry point per feature: SSH, wireless debugging, KDE Connect,
Syncthing and Nearby share each own their port(s) in our `inet xx_wm`
table, and the Firewall row is only an on/off switch whose subtitle
names what is currently allowed in. The ruleset is derived from
feature state (`features_to_allowed`) and re-applied through the root
helper (`SetInboundAllowed`, `SetFirewallEnabled`, `GetFirewallState`
in launcher/helper/xx_wm_helper.py): nftables directly (present
everywhere) with a ufw fallback only where nft is missing. An accept
in our own table cannot override a drop in another base chain, so
when one is detected the section says so instead of pretending the
switch filters anything.

postmarketOS ships no enabled inbound firewall, so there the status
honestly reads off and feature changes program nothing.

Migration from the old per-preset rows: the first `SetInboundAllowed`
unions the derived set with the old rows' stored allow-state, so a
previously allowed port stays allowed (its feature counts as on for
the firewall only) without starting any service; the stored keys are
read once, then left alone (`migrate_legacy_allowed` states the rule;
later calls derive purely).
"""
from __future__ import annotations

import json
from pathlib import Path

import settings_page
import settings_ui as ui


def _ports(*specs) -> tuple:
    return tuple(specs)


PRESETS: dict[str, dict] = {
    'nearby': {'title': 'Nearby share',
               'detail': 'LocalSend file sharing',
               'tcp': _ports(53317), 'udp': _ports(53317)},
    'xxdb': {'title': 'Wireless debugging',
             'detail': 'Developer xxdb port range',
             'tcp': _ports((37000, 37999)), 'udp': _ports()},
    'ssh': {'title': 'SSH',
            'detail': 'Remote shell access',
            'tcp': _ports(22), 'udp': _ports()},
    'kdeconnect': {'title': 'KDE Connect',
                   'detail': 'Phone integration',
                   'tcp': _ports((1714, 1764)), 'udp': _ports((1714, 1764))},
    'syncthing': {'title': 'Syncthing',
                  'detail': 'File synchronization',
                  'tcp': _ports(22000), 'udp': _ports(22000, 21027)},
}


def preset_ufw_specs(name: str) -> list[str]:
    """ufw rule specs for a preset (`53317/tcp`, `1714:1764/udp`).

    Raises KeyError for anything but the curated preset names."""
    preset = PRESETS[name]

    def _spec(port, proto: str) -> str:
        if isinstance(port, tuple):
            return '%d:%d/%s' % (port[0], port[1], proto)
        return '%d/%s' % (port, proto)

    return ([_spec(port, 'tcp') for port in preset['tcp']]
            + [_spec(port, 'udp') for port in preset['udp']])


def _nft_port(port) -> str:
    if isinstance(port, tuple):
        return '%d-%d' % (port[0], port[1])
    return str(port)


def render_nft_rules(allowed: set[str]) -> str:
    """The managed `inet xx_wm` table for the allowed presets.

    Accept policy: on a system with no other firewall this changes
    nothing observable (everything is already allowed) while documenting
    intent. Raises KeyError for names outside the preset allowlist."""
    unknown = set(allowed) - set(PRESETS)
    if unknown:
        raise KeyError('unknown inbound preset: %s' % sorted(unknown)[0])
    lines = ['# xx-wm inbound presets (managed; do not edit).',
             '# An accept here cannot override a drop in another base chain.',
             'table inet xx_wm {',
             '    chain input {',
             '        type filter hook input priority filter; policy accept;']
    for name in sorted(allowed):
        preset = PRESETS[name]
        for port in preset['tcp']:
            lines.append('        tcp dport %s accept comment "xxwm %s";'
                         % (_nft_port(port), name))
        for port in preset['udp']:
            lines.append('        udp dport %s accept comment "xxwm %s";'
                         % (_nft_port(port), name))
    lines += ['    }', '}']
    return '\n'.join(lines) + '\n'


# Display order for the one-line subtitle (PRESETS keys, feature order).
FEATURES = ('ssh', 'xxdb', 'kdeconnect', 'syncthing', 'nearby')


def features_to_allowed(features: dict) -> set[str]:
    """The allowed presets for feature state ``{name: on}`` (pure).

    One entry point per feature: a feature that is on owns exactly its
    preset's ports. Unknown names are ignored, so callers can pass
    their whole feature map."""
    get = features.get
    return {name for name in FEATURES if get(name)}


def allowed_titles(allowed) -> list[str]:
    """Display titles for an allowed set, in feature order."""
    have = set(allowed or ())
    return [PRESETS[name]['title'] for name in FEATURES if name in have]


def subtitle_for_allowed(allowed, *, enabled: bool = True,
                         backend: str = 'nftables') -> str:
    """The Firewall row's one-line subtitle: what is allowed in."""
    if backend == 'none':
        return 'Off · no inbound firewall, all incoming allowed'
    if not enabled:
        return 'Off'
    titles = allowed_titles(allowed)
    if titles:
        return 'Allowing %s' % ', '.join(titles)
    return 'Blocking all incoming'


def migrate_legacy_allowed(legacy_allowed, features: dict) -> set[str]:
    """Merge the old per-preset rows' stored allow-state into features.

    A port that was allowed before keeps its feature treated as on
    for the firewall only — no service is ever started here; the
    helper never starts services either. The stored keys are read
    once, then left alone: the helper unions them on its first
    whole-set write and derives purely after that. Accepts the
    persisted list or a ``{name: allowed}`` state mapping."""
    if isinstance(legacy_allowed, dict):
        legacy = {name for name, on in legacy_allowed.items() if on}
    else:
        try:
            legacy = set(legacy_allowed or ())
        except TypeError:
            legacy = set()
    legacy &= set(PRESETS)
    return features_to_allowed(features) | legacy


def share_visible(home=None) -> bool:
    """True while the xx-share receiver is visible (its own switch).

    Read from xx-share's own config file, never the network; missing
    or corrupt reads as off. Worker only (file read)."""
    try:
        base = Path(home) if home is not None else Path.home()
        raw = json.loads(
            (base / '.config' / 'xx-share' / 'config.json').read_text(
                encoding='utf-8'))
    except (OSError, ValueError):
        return False
    if not isinstance(raw, dict):
        return False
    return raw.get('visible') is True


def _probe_ssh_active() -> bool:
    """True while the SSH service is running (worker only)."""
    try:
        import settings_services as svc_mod
        import system_settings
        entry = next(e for e in svc_mod.SERVICES if e.key == 'ssh')
        init = system_settings.detect_init()
        if init == 'systemd':
            state = svc_mod.ServicesSection._systemd_state(
                system_settings, entry)
        elif init == 'openrc':
            state = svc_mod.ServicesSection._openrc_state(
                system_settings, entry)
        else:
            return False
    except Exception:
        return False
    try:
        return svc_mod.state_text(state).startswith('On')
    except Exception:
        return False


def probe_features(*, ssh=None, xxdb: bool = False, kdeconnect=None,
                   syncthing=None, nearby=None, home=None) -> dict:
    """Gather the five firewall features (worker only).

    Each argument pins that feature (tests and callers that just
    changed it); None probes it now: SSH from its service state,
    KDE Connect / Syncthing from their user-unit probes, Nearby share
    from xx-share's own visible flag. Never raises."""
    features: dict = {}
    try:
        features['ssh'] = bool(ssh) if ssh is not None \
            else _probe_ssh_active()
    except Exception:
        features['ssh'] = False
    features['xxdb'] = bool(xxdb)
    if kdeconnect is None or syncthing is None:
        try:
            import settings_connect as connect
        except ImportError:
            connect = None
        if connect is not None:
            if kdeconnect is None:
                try:
                    kdeconnect = connect.feature_active(
                        connect.kdeconnect_probe())
                except Exception:
                    kdeconnect = False
            if syncthing is None:
                try:
                    syncthing = connect.feature_active(
                        connect.syncthing_probe())
                except Exception:
                    syncthing = False
        if kdeconnect is None:
            kdeconnect = False
        if syncthing is None:
            syncthing = False
    features['kdeconnect'] = bool(kdeconnect)
    features['syncthing'] = bool(syncthing)
    try:
        features['nearby'] = bool(nearby) if nearby is not None \
            else share_visible(home)
    except Exception:
        features['nearby'] = False
    return features


def _xxdb_enabled(win) -> bool:
    ctrl = getattr(win, 'xxdb_controller', None)
    return bool(ctrl is not None and (
        getattr(ctrl, 'enabled', False)
        or getattr(ctrl, 'starting', False)))


def reapply(win, on_done=None) -> None:
    """Re-apply the derived ruleset for the current features.

    Worker-gathers feature state, then writes the whole allowed set
    through the helper. Skips the write when the live set already
    matches (and programs nothing where no inbound firewall exists).
    Never raises; ``on_done(ok, detail)`` reports failures."""
    try:
        from settings_page import _run_bg
        import helper_client
    except ImportError as err:
        if callable(on_done):
            on_done(False, str(err))
        return
    try:
        xxdb = _xxdb_enabled(win)
    except Exception:
        xxdb = False

    def _work():
        try:
            return probe_features(xxdb_enabled=xxdb)
        except Exception:
            return None

    def _after(features) -> None:
        if not isinstance(features, dict):
            if callable(on_done):
                on_done(False, 'feature probe failed')
            return
        allowed = sorted(features_to_allowed(features))
        try:
            helper_client.get_firewall_state(
                lambda ok, state: _maybe_apply(
                    ok, state, allowed, on_done))
        except Exception as err:
            if callable(on_done):
                on_done(False, str(err))

    try:
        _run_bg(_work, _after)
    except Exception as err:
        if callable(on_done):
            on_done(False, str(err))


def _maybe_apply(ok: bool, state, allowed: list, on_done) -> None:
    try:
        import helper_client
    except ImportError as err:
        if callable(on_done):
            on_done(False, str(err))
        return
    if ok and isinstance(state, dict):
        if state.get('backend') == 'none':
            if callable(on_done):
                on_done(True, None)
            return
        current = state.get('allowed') or {}
        have = {name for name, on in current.items() if on} \
            if isinstance(current, dict) else set()
        if have == set(allowed):
            if callable(on_done):
                on_done(True, None)
            return
    try:
        helper_client.set_inbound_allowed(
            allowed, on_done or (lambda _ok, _detail: None))
    except Exception as err:
        if callable(on_done):
            on_done(False, str(err))


class FirewallSection(settings_page._Section):
    """Settings section: one Firewall row with an on/off switch."""

    key = 'firewall'
    title = 'Firewall'

    def __init__(self) -> None:
        self._row = None
        self._switch = None
        self._status = None
        self._note = None
        self._updating = False
        self._state: dict | None = None

    def build(self, win) -> list:
        row = ui.switch_row(
            'Firewall', 'Checking firewall…', False,
            lambda active: self._on_toggle(win, active))
        self._row = row
        self._switch = row.switch
        self._status = row.subtitle_label
        built = ui.group('Firewall', [row], note='')
        self._note = built[2]
        return built

    def refresh(self, win) -> None:
        try:
            import helper_client
            helper_client.get_firewall_state(
                lambda ok, state: self._show_state(
                    win, state if ok else None))
        except Exception:
            self._show_state(win, None)

    # ------------------------------------------------------------ behavior

    def _show_state(self, win, state) -> None:
        self._state = state if isinstance(state, dict) else None
        if self._row is None:
            return
        allowed = (self._state or {}).get('allowed') or {}
        have = {name for name, on in allowed.items() if on} \
            if isinstance(allowed, dict) else set()
        backend = (self._state or {}).get('backend', 'none')
        if self._status is not None:
            try:
                self._status.set_text(subtitle_for_allowed(
                    have, enabled=self._is_on(), backend=backend))
            except Exception:
                pass
        if self._switch is not None:
            self._updating = True
            try:
                self._switch.set_active(self._is_on())
            except Exception:
                pass
            finally:
                self._updating = False
        ui.say(self._note, self._note_text())

    def _is_on(self) -> bool:
        return bool(self._state and self._state.get('enabled'))

    def _status_text(self) -> str:
        state = self._state
        if not isinstance(state, dict):
            return 'Status unavailable'
        allowed = state.get('allowed') or {}
        have = {name for name, on in allowed.items() if on} \
            if isinstance(allowed, dict) else set()
        return subtitle_for_allowed(
            have, enabled=self._is_on(),
            backend=state.get('backend', 'none'))

    def _note_text(self) -> str:
        state = self._state
        if not isinstance(state, dict):
            return ''
        if state.get('foreign_drop'):
            return ('Another firewall is dropping incoming connections; '
                    'this switch cannot override its drops.')
        return ''

    def _on_toggle(self, win, active: bool) -> None:
        if self._updating:
            return
        self._apply_enabled(win, bool(active))

    def _apply_enabled(self, win, enabled: bool) -> None:
        try:
            import helper_client
        except ImportError as err:
            win._show_status(f'Could not change firewall: {err}')
            return

        def _done(ok: bool, detail) -> None:
            if not ok:
                win._show_status(f'Could not change firewall: {detail}')
            self.refresh(win)
            if ok and enabled:
                reapply(win, _re_done)

        def _re_done(ok: bool, detail) -> None:
            if not ok:
                try:
                    win._show_status(
                        f'Could not follow features: {detail}')
                except Exception:
                    pass

        try:
            helper_client.set_firewall_enabled(bool(enabled), _done)
        except Exception as err:
            win._show_status(f'Could not change firewall: {err}')
