"""The docked monitor's surfaces under xxwm (river): a top bar on every
monitor that is not the handset, the launcher on Super and Alt+Tab, and
the keybind sheet on Super+/.

xxwm's `subscribe state` stream says which outputs exist, which is the
handset, what each shows and which has focus, and hands over the verbs
`launcher`, `bar` (Super+R) and `keybinds` (Super+/). The shell window's
existing sources (clock tick, status poll, privacy monitor, call
snapshots, the shade's list) feed the bars through the set_* methods
here. Bars follow monitors in and out; the handset is never touched.

Without xxwm (phoc) nothing starts.
"""
from __future__ import annotations

from datetime import datetime

import desk_bar
import xxwm_backend
from shell_log import get_logger, log_exc

_log = get_logger('desk')


class DeskSession:

    def __init__(self, shell, watcher_factory=None, request=None) -> None:
        self._shell = shell
        self._watcher_factory = watcher_factory or xxwm_backend.XxwmStateWatcher
        self._request = request or xxwm_backend.request_async
        self._watcher = None
        self._state: dict | None = None
        self._bars: dict[str, object] = {}
        self._bars_on = True
        self._launcher = None
        self._sheet = None
        self._provider = None
        self._monitors_handler = None
        self._monitors = None
        self._clock = ('', '')
        self._status: dict = {}
        self._flags = (False, False)
        self._privacy = False
        self._call: str | None = None
        self._notifications = 0

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        from gi.repository import Gdk, Gtk
        display = Gdk.Display.get_default()
        if display is not None:
            self._provider = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_display(
                display, self._provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            try:
                from quick_actions import ensure_shade_icons
                ensure_shade_icons(display)
            except Exception:
                log_exc('desk_session.start: ensure_shade_icons')
            self._monitors = display.get_monitors()
            self._monitors_handler = self._monitors.connect(
                'items-changed', lambda *_a: self._reconcile())
        self.apply_theme()
        self._watcher = self._watcher_factory(self._on_state, self._on_shell)
        self._watcher.start()

    def shutdown(self) -> None:
        if self._watcher is not None:
            self._watcher.shutdown()
            self._watcher = None
        if self._monitors is not None and self._monitors_handler:
            self._monitors.disconnect(self._monitors_handler)
            self._monitors_handler = None
        for bar in self._bars.values():
            bar.destroy()
        self._bars.clear()
        for surface in (self._launcher, self._sheet):
            if surface is not None:
                surface.destroy()
        self._launcher = self._sheet = None

    def apply_theme(self, preset=None) -> None:
        if self._provider is None:
            return
        if preset is None:
            from window import resolve_appearance_preset
            preset = resolve_appearance_preset(self._shell.config)
        import desk_launcher
        import keybind_sheet
        css = (desk_bar.theme_css(preset) + desk_launcher.theme_css(preset)
               + keybind_sheet.theme_css(preset))
        self._provider.load_from_data(css.encode('utf-8'))

    # -- xxwm ------------------------------------------------------------
    def _on_state(self, state: dict) -> None:
        self._state = state
        self._reconcile()

    def _on_shell(self, verb: str) -> None:
        if self._locked():
            return
        if verb == 'launcher':
            self.toggle_launcher()
        elif verb == 'bar':
            self.toggle_bars()
        elif verb == 'keybinds':
            self.toggle_keybinds()
        else:
            _log.info('xxwm sent an unknown shell verb %r', verb)

    def _locked(self) -> bool:
        try:
            return bool(self._shell.is_locked())
        except Exception:
            return False

    def _monitor_list(self) -> list:
        monitors = []
        model = self._monitors
        if model is None:
            return monitors
        for i in range(model.get_n_items()):
            monitor = model.get_item(i)
            geometry = monitor.get_geometry()
            monitors.append(((monitor.get_connector() or ''), geometry.x,
                             geometry.y, geometry.width, geometry.height,
                             monitor))
        return monitors

    def _monitor_for(self, output: dict):
        monitors = self._monitor_list()
        index = desk_bar.match_monitor([m[:5] for m in monitors], output)
        return monitors[index][5] if index is not None else None

    def focused_monitor(self):
        """The monitor xxwm has focus on, else the first desk monitor."""
        outputs = (self._state or {}).get('outputs') or []
        for output in outputs:
            if output.get('focused'):
                monitor = self._monitor_for(output)
                if monitor is not None:
                    return monitor
        for output in desk_bar.desk_outputs(self._state):
            monitor = self._monitor_for(output)
            if monitor is not None:
                return monitor
        return None

    # -- bars ----------------------------------------------------------------
    def _reconcile(self) -> None:
        wanted = {}
        if self._bars_on:
            for output in desk_bar.desk_outputs(self._state):
                monitor = self._monitor_for(output)
                if monitor is not None:
                    wanted[desk_bar.output_key(output)] = (output, monitor)
        for key in list(self._bars):
            bar = self._bars[key]
            if key not in wanted or wanted[key][1] is not bar.monitor:
                bar.destroy()
                del self._bars[key]
        for key, (output, monitor) in wanted.items():
            bar = self._bars.get(key)
            if bar is None:
                bar = self._new_bar(output, monitor)
                self._bars[key] = bar
                _log.info('top bar on %s', key)
            view = bar.view
            view.set_workspaces(desk_bar.workspace_items(self._state, output))
            view.set_title(desk_bar.output_title(output))
            bar.output_name = output.get('name') or ''

    def _new_bar(self, output: dict, monitor):
        bar = desk_bar.DeskBar(monitor)
        bar.output_name = output.get('name') or ''
        bar.view._on_workspace = lambda n, b=bar: self._show_workspace(b, n)
        bar.set_application(self._shell.get_application())
        view = bar.view
        view.set_clock(*self._clock)
        view.set_status(self._status)
        view.set_flags(*self._flags)
        view.set_privacy(self._privacy)
        view.set_call(self._call)
        view.set_notifications(self._notifications)
        bar.present()
        return bar

    def _show_workspace(self, bar, number: int) -> None:
        name = getattr(bar, 'output_name', '')
        line = f'workspace {int(number)} {name}'.strip()
        self._request(line)

    def toggle_bars(self) -> None:
        self._bars_on = not self._bars_on
        self._reconcile()

    @property
    def bars(self) -> dict:
        return dict(self._bars)

    # -- launcher and keybind sheet ----------------------------------------------
    def toggle_launcher(self) -> None:
        if self._launcher is not None and self._launcher.get_visible():
            self._launcher.close_prompt()
            return
        if self._sheet is not None and self._sheet.get_visible():
            self._sheet.close_sheet()
        if self._launcher is None:
            from desk_launcher import DeskLauncher
            self._launcher = DeskLauncher(self._shell)
            self._launcher.set_application(self._shell.get_application())
        self._launcher.show_on(self.focused_monitor())

    def toggle_keybinds(self) -> None:
        if self._sheet is not None and self._sheet.get_visible():
            self._sheet.close_sheet()
            return
        monitor = self.focused_monitor()

        def show(answer) -> None:
            binds = _parse_binds(answer)
            if not binds or self._locked():
                return
            if self._launcher is not None and self._launcher.get_visible():
                self._launcher.close_prompt()
            if self._sheet is None:
                from keybind_sheet import KeybindSheet
                self._sheet = KeybindSheet()
                self._sheet.set_application(self._shell.get_application())
            self._sheet.show_on(monitor, binds)
        self._request('binds', show)

    def close_overlays(self) -> None:
        """The lock came up: nothing of ours stays over it."""
        if self._launcher is not None and self._launcher.get_visible():
            self._launcher.close_prompt()
        if self._sheet is not None and self._sheet.get_visible():
            self._sheet.close_sheet()

    # -- shell sources -------------------------------------------------------------
    def _each_view(self):
        return [bar.view for bar in self._bars.values()]

    def set_clock(self, now: datetime, clock: str) -> None:
        parts = desk_bar.clock_parts(now, clock)
        if parts == self._clock:
            return
        self._clock = parts
        for view in self._each_view():
            view.set_clock(*parts)

    def set_status(self, snapshot: dict | None) -> None:
        self._status = dict(snapshot or {})
        for view in self._each_view():
            view.set_status(self._status)

    def set_flags(self, dnd: bool, focus: bool) -> None:
        self._flags = (bool(dnd), bool(focus))
        for view in self._each_view():
            view.set_flags(*self._flags)

    def set_privacy(self, active: bool) -> None:
        self._privacy = bool(active)
        for view in self._each_view():
            view.set_privacy(self._privacy)

    def set_call(self, page: str | None) -> None:
        self._call = page
        for view in self._each_view():
            view.set_call(page)

    def set_notifications(self, count: int) -> None:
        self._notifications = int(count or 0)
        for view in self._each_view():
            view.set_notifications(self._notifications)


def _parse_binds(answer) -> list:
    import json
    if not isinstance(answer, str) or not answer.startswith('['):
        return []
    try:
        binds = json.loads(answer)
    except ValueError:
        return []
    return binds if isinstance(binds, list) else []


def start_desk_session(shell) -> DeskSession | None:
    """A running DeskSession when xxwm runs the session, else None."""
    if not xxwm_backend.present():
        return None
    session = DeskSession(shell)
    session.start()
    _log.info('desk session started (xxwm)')
    return session


def desk_of(win) -> DeskSession | None:
    """The shell window's desk session, if any (getattr-safe for test
    doubles)."""
    return getattr(win, '_desk', None)
