"""Connected devices › When docked: what the phone screen does while a
monitor is connected (dock_controller). Only on river with xxwm, the
one session that docks; elsewhere the section has no rows."""
from __future__ import annotations

import settings_page
import settings_ui as ui
from dock_controller import CONFIG_KEY, MODE_LABELS, MODES, current, mode_of


def docking_available() -> bool:
    try:
        import xxwm_backend
        return xxwm_backend.present()
    except Exception:
        return False


def choose_mode(win, mode: str) -> None:
    """Save ``mode`` and apply it now when a dock controller runs."""
    if mode not in MODES:
        return
    controller = current()
    if controller is not None:
        controller.set_mode(mode)
        return
    try:
        win.config.set(CONFIG_KEY, mode)
    except Exception:
        pass


class DockSection(settings_page._Section):
    key = 'dock'
    title = 'When docked'

    def __init__(self, available=docking_available) -> None:
        self._available = available
        self._row = None

    def build(self, win) -> list:
        if not self._available():
            return []
        self._row = ui.nav_row(
            'Phone screen', MODE_LABELS[mode_of(win.config)],
            lambda row: self._pick(win, row))
        return ui.group(self.title, [self._row])

    def _pick(self, win, anchor) -> None:
        win.item_actions._show_action_menu(
            anchor, 'Phone screen',
            [(MODE_LABELS[mode], lambda mode=mode: self._set(win, mode))
             for mode in MODES])

    def _set(self, win, mode: str) -> None:
        choose_mode(win, mode)
        if self._row is not None:
            ui.set_subtitle(self._row, MODE_LABELS[mode_of(win.config)])
