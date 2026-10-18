"""Charge-limit settings section: cap battery charging below 100%.

The privileged write is the root helper's SetChargeLimit (helper_client,
async, clamped server-side to 50..100); the shell never touches sysfs as
root. Support is probed read-only: the helper capability first, a local
/sys scan second (covers a helper that is not installed yet). Without a
supported knob the section says so and offers no control. Current charge
state comes from UPower through system_settings.battery_status.
"""
from __future__ import annotations

import settings_page
import settings_ui as ui

import os

CHARGE_MIN = 50
CHARGE_MAX = 100

CHARGE_PRESETS: tuple = (('Off', None), ('80%', 80), ('85%', 85),
                         ('90%', 90))

NO_KNOB_TEXT = 'Not supported on this phone'

# Sysfs knobs that cap the charge level, read-only probed: the standard
# power_supply threshold plus the vendor names seen in the wild.
CHARGE_KNOBS = ('charge_control_end_threshold', 'charge_stop_threshold',
                'charge_control_limit')

_SYSFS_POWER = os.path.join('/sys', 'class', 'power_supply')


def clamp_charge_limit(percent: int) -> int:
    """Clamp into the safe 50..100 window, mirroring the helper's
    clamp_charge_limit (a test pins the two together)."""
    try:
        value = int(percent)
    except (TypeError, ValueError):
        raise ValueError('charge limit is not a number')
    return max(CHARGE_MIN, min(CHARGE_MAX, value))


def _is_battery_supply(base: str, name: str) -> bool:
    """True only for `type == Battery` supplies: an AC/Mains/USB supply
    with a similarly named knob must never count as charge-limit support."""
    try:
        with open(os.path.join(base, name, 'type'),
                  encoding='utf-8') as handle:
            return handle.read().strip() == 'Battery'
    except OSError:
        return False


def find_charge_knobs(sysfs: str = '/sys') -> list[str]:
    """Battery dirs exposing a known charge-cap knob (read-only)."""
    base = os.path.join(sysfs, 'class', 'power_supply')
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    found = []
    for name in names:
        if not _is_battery_supply(base, name):
            continue
        for knob in CHARGE_KNOBS:
            if os.path.isfile(os.path.join(base, name, knob)):
                found.append(os.path.join(base, name))
                break
    return found


def knob_supported(sysfs: str = '/sys') -> bool:
    return bool(find_charge_knobs(sysfs))


def parse_custom_limit(text: str) -> int | None:
    """Digits from a custom entry, clamped; None when there is no number."""
    digits = ''.join(ch for ch in str(text or '') if ch.isdigit())
    if not digits:
        return None
    return clamp_charge_limit(int(digits))


class ChargingSection(settings_page._Section):
    """Settings section: charge-limit control gated on sysfs support."""

    key = 'charging'
    title = 'Charging'

    def __init__(self) -> None:
        self._row = None
        self._note = None
        self._current: int | None = None
        self._supported: bool | None = None

    def build(self, win) -> list:
        self._current = getattr(win.config, 'charge_limit', None)
        self._row = ui.nav_row('Charge limit', '',
                               lambda r: self._pick(win, r))
        built = ui.group(self.title, note='')
        self._card = built[1]
        self._note = built[2]
        self._render_options(win)
        return built

    def refresh(self, win) -> None:
        from settings_page import _run_bg
        self._current = getattr(win.config, 'charge_limit', None)
        state: dict = {}

        def _maybe_render() -> None:
            if 'local' not in state or 'caps' not in state:
                return
            local_supported, battery = state['local']
            ok, caps = state['caps']
            if ok:
                supported = bool(isinstance(caps, dict)
                                 and caps.get('charge_limit_supported'))
            else:
                supported = bool(local_supported)
            self._on_state(win, (supported, battery))

        try:
            import helper_client
            helper_client.get_capabilities(
                lambda ok, caps: (state.update(caps=(ok, caps)),
                                  _maybe_render()))
        except Exception:
            state['caps'] = (False, None)
        _run_bg(self._probe_local, lambda res: (state.update(local=res),
                                                _maybe_render()))

    # ------------------------------------------------------------ behavior

    def _probe_local(self):
        """Worker thread: (sysfs knob present, battery text). Never raises."""
        try:
            supported = knob_supported()
        except Exception:
            supported = False
        try:
            import system_settings
            battery = system_settings.battery_status()
        except Exception:
            battery = None
        return (supported, battery)

    def _on_state(self, win, res) -> None:
        try:
            supported, _battery = res
        except (TypeError, ValueError):
            return
        self._supported = bool(supported)
        self._render_options(win)

    def _render_options(self, win) -> None:
        """One row: the limit as a value row when the battery driver has
        a knob, the honest no-knob sentence (no control) otherwise."""
        if self._row is None:
            return
        current = getattr(win.config, 'charge_limit', None)
        if self._supported is True:
            ui.set_subtitle(self._row, 'Off · charges to 100%'
                            if current is None else 'Stops at %d%%' % current)
            ui.fill(self._card, [self._row])
            return
        text = (NO_KNOB_TEXT if self._supported is False
                else 'Checking battery support…')
        ui.fill(self._card, [ui.row('Charge limit', text)])

    def _pick(self, win, anchor) -> None:
        choices = [(label, lambda v=value: self._apply(win, v))
                   for label, value in CHARGE_PRESETS]
        choices.append(('Custom…', lambda: self._open_custom(win, anchor)))
        current = getattr(win.config, 'charge_limit', None)
        values = [value for _label, value in CHARGE_PRESETS]
        # A custom limit marks Custom…; a preset marks itself.
        checked = (values.index(current) if current in values
                   else len(values))
        win.item_actions._show_action_menu(anchor, 'Charge limit', choices,
                                           checked=checked)

    def _open_custom(self, win, anchor):
        current = getattr(win.config, 'charge_limit', None)
        return ui.form_sheet(
            win, anchor, 'Custom charge limit',
            [('limit', 'Stop charging at (50–100 %)',
              '' if current is None else current, 'number')],
            'Set limit', lambda values: self._on_custom(win, values))

    def _on_custom(self, win, values: dict) -> str | None:
        picked = parse_custom_limit(values.get('limit', ''))
        if picked is None:
            return 'Enter a number from 50 to 100.'
        self._apply(win, picked)
        return None

    def _apply(self, win, value: int | None) -> None:
        percent = 100 if value is None else clamp_charge_limit(value)
        try:
            import helper_client
        except ImportError as err:
            self._say(f'Could not set limit: {err}')
            return

        def _done(ok: bool, detail) -> None:
            if not ok:
                self._say(f'Could not set limit: {detail}')
                return
            try:
                win.config.set_charge_limit(value)
            except Exception:
                pass
            self._current = getattr(win.config, 'charge_limit', None)
            self._render_options(win)
            self._say('')

        try:
            helper_client.set_charge_limit(percent, _done)
        except Exception as err:
            self._say(f'Could not set limit: {err}')

    def _say(self, text: str) -> None:
        ui.say(self._note, text)
