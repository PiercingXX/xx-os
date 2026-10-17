from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app_index import AppEntry


def _open_settings(win) -> None:
    """Show settings with the TOP hop when the window offers it.

    Bare test doubles predate _open_settings and only switch the stack
    page; real windows always carry the method, so behavior there is the
    hop that raises settings above a focused app.
    """
    opener = getattr(win, '_open_settings', None)
    if callable(opener):
        opener()
    else:
        win.stack.set_visible_child_name('settings')


class HomePage:
    """Home page builder and widget/slot behavior for ShellWindow.

    Holds only a reference to the window; all state (labels, launcher,
    config) stays on the window so bare-instance headless tests keep
    working. Method names mirror the ShellWindow delegators in window.py.
    """

    def __init__(self, win) -> None:
        self._win = win

    def _build_home_page(self):
        from gi.repository import Gtk
        from home_launcher import HomeLauncher

        win = self._win
        # Home rows draw under .shell-root: their sheet lives in the window's
        # theme provider (reloaded on every hot reload from the resolved
        # preset), never as a per-build display provider that would leak one
        # registration per construction and miss custom themes.

        # --- Top 1/3: widget block — config-driven, vertically centered ---
        self._apply_widget_alignment()

        win._clock_tap_timer = None

        clock_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        clock_inner.set_halign(Gtk.Align.FILL)
        clock_inner.set_hexpand(True)
        win._widget_block = clock_inner
        win._widget_taps_wired = set()
        self._build_widget_block()

        self._init_weather()

        # Equal spacers above and below clock_inner → vertically centered in top pane
        top_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top_pane.set_hexpand(True)
        top_pane.set_vexpand(True)
        sp_top = Gtk.Box()
        sp_top.set_vexpand(True)
        sp_bot = Gtk.Box()
        sp_bot.set_vexpand(True)
        top_pane.append(sp_top)
        top_pane.append(clock_inner)
        top_pane.append(sp_bot)

        # --- Bottom 2/3: launcher — vertically centered ---
        def _get_home_slots() -> list[dict]:
            # Android "Home Slot Count": the first N slots show; the rest
            # stay stored. Edit mode works on the same visible prefix.
            visible = getattr(win.config, 'visible_home_slots', None)
            return visible() if callable(visible) else win.config.home_slots

        def _empty_slots() -> int:
            empty = getattr(win.config, 'empty_home_slots', None)
            return empty() if callable(empty) else 0

        def _launch_slot(slot: dict) -> None:
            win._launch_slot(slot)

        win._home_launcher = HomeLauncher(
            open_dialer_fn=win._open_dialer,
            get_slots_fn=_get_home_slots,
            on_launch_slot=_launch_slot,
            on_member_long_press=lambda w, s, m: win.item_actions.show_member_menu(w, s, m),
            on_slot_move=win._move_slot,
            on_slot_remove=win._remove_slot,
            on_slot_rename=win._rename_slot,
            on_edit_action=win._on_edit_action,
            is_paused_fn=win.focus_state.is_paused_app,
            get_alignment_fn=lambda: win.config.home_alignment,
            on_status_fn=win._show_status,
            get_empty_count_fn=_empty_slots,
            on_empty_tap=lambda: self._pick_empty_slot(),
        )

        launcher_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        launcher_inner.set_valign(Gtk.Align.CENTER)
        launcher_inner.set_vexpand(True)
        launcher_inner.set_hexpand(True)
        launcher_inner.append(win._home_launcher)

        launcher_scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        launcher_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        launcher_scroll.set_has_frame(False)
        launcher_scroll.add_css_class('home-scroll')
        launcher_scroll.set_child(launcher_inner)

        bot_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bot_pane.set_hexpand(True)
        bot_pane.set_vexpand(True)
        bot_pane.set_margin_bottom(24)
        bot_pane.append(launcher_scroll)

        # Background gestures dispatch gestures.json: double-tap runs
        # double_tap_home, long-press runs long_press_home (edit = slot
        # edit mode, settings = open Settings, none = nothing).
        top_press = Gtk.GestureLongPress.new()
        top_press.set_touch_only(False)
        top_press.connect('pressed', win._on_home_long_press)
        top_pane.add_controller(top_press)
        top_tap = Gtk.GestureClick.new()
        top_tap.set_touch_only(False)
        top_tap.connect(
            'pressed',
            lambda _g, n, _x, _y: win._on_home_background_tap(n))
        top_pane.add_controller(top_tap)

        # Long-press anywhere on home → configured long_press_home action
        edit_press = Gtk.GestureLongPress.new()
        edit_press.set_touch_only(False)
        edit_press.connect('pressed', win._on_home_long_press)
        bot_pane.add_controller(edit_press)
        bot_tap = Gtk.GestureClick.new()
        bot_tap.set_touch_only(False)
        bot_tap.connect(
            'pressed',
            lambda _g, n, _x, _y: win._on_home_background_tap(n))
        bot_pane.add_controller(bot_tap)

        # --- 1/3 widgets / 2/3 launcher — a homogeneous grid, so there is
        # no Paned divider bar and no realize-time ratio hack. The widget
        # block centers in the top third; the slot list centers in the rest.
        grid = Gtk.Grid()
        grid.set_row_homogeneous(True)
        grid.set_hexpand(True)
        grid.set_vexpand(True)
        grid.attach(top_pane, 0, 0, 1, 1)
        grid.attach(bot_pane, 0, 1, 1, 2)
        return grid

    def _launch_slot(self, slot: dict) -> None:
        """Launch an app or command from a home slot."""
        win = self._win
        app_id = slot.get('app_id')
        if app_id and win.focus_state.is_paused_app(str(app_id)):
            win._show_focus_notice(slot.get('label', 'App'))
            return
        # Record usage
        if app_id:
            win.config.record_launch(app_id)

        # Launch based on type
        slot_type = slot.get('type')
        if slot_type == 'app':
            android_pkg = slot.get('app_id')
            cmd = slot.get('cmd')
            label = slot.get('label', '')

            if label == 'Phone' and not android_pkg and not cmd:
                # Built-in dialer
                win._open_dialer()
            elif android_pkg:
                # Desktop-file ids (incl. waydroid.*.desktop wrappers) launch
                # via Gio; only bare Android package names go through waydroid
                if str(android_pkg).endswith('.desktop'):
                    win._launch_app_id(str(android_pkg))
                else:
                    from home_launcher import _launch_android
                    _launch_android(android_pkg)
                    win.drop_to_background()
            elif cmd:
                # Command
                from home_launcher import _launch_cmd
                _launch_cmd(cmd)
                win.drop_to_background()

    def build_status_bar(self):
        """Android "Show Status Bar": a slim top bar (time left, network
        and battery right) over the launcher pages. Hidden by default,
        like Android's launcher hiding the system status bar."""
        from gi.repository import Gtk, Pango

        win = self._win
        bar = Gtk.CenterBox()
        bar.add_css_class('launcher-status-bar')
        bar.set_hexpand(True)
        time_label = Gtk.Label(xalign=0)
        time_label.add_css_class('dim-label')
        info = Gtk.Label(xalign=1)
        info.add_css_class('dim-label')
        info.set_ellipsize(Pango.EllipsizeMode.END)
        bar.set_start_widget(time_label)
        bar.set_end_widget(info)
        win.status_bar_time = time_label
        win.status_bar_info = info
        win._status_bar = bar
        self.apply_status_bar()
        return bar

    def apply_status_bar(self) -> None:
        win = self._win
        bar = getattr(win, '_status_bar', None)
        if bar is None:
            return
        try:
            bar.set_visible(bool(getattr(win.config, 'status_bar_visible',
                                         False)))
        except Exception:
            pass

    def _apply_widget_alignment(self) -> None:
        """Widget lines follow the home alignment pref (centered like the
        slots by default, left-aligned like theme-presets.jpg when set)."""
        win = self._win
        try:
            xalign = {'left': 0.0, 'right': 1.0}[win.config.home_alignment]
        except (AttributeError, KeyError, TypeError):
            xalign = 0.5
        for widget in (win.clock_label, win.date_label,
                       win.weather_label, win.status_strip):
            try:
                widget.set_xalign(xalign)
            except Exception:
                pass

    def _build_widget_block(self) -> None:
        win = self._win
        block = win._widget_block
        child = block.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            block.remove(child)
            child = nxt
        self._apply_widget_alignment()
        widget_map = {
            'time': win.clock_label,
            'date': win.date_label,
            'weather': win.weather_label,
            'battery': win.status_strip,
        }
        # Android "Date/Time Mode": Off hides the clock and date, Date
        # only hides the clock, Date and time shows both.
        mode = getattr(win.config, 'date_time_mode', 'date_time')
        for key, _conf in win.config.ordered_widgets():
            widget = widget_map.get(key)
            if widget is None:
                continue
            if key == 'time' and mode != 'date_time':
                continue
            if key == 'date' and mode == 'off':
                continue
            if key not in win._widget_taps_wired:
                self._wire_widget_tap(widget, key)
                win._widget_taps_wired.add(key)
            block.append(widget)

    def _current_tap(self, key: str) -> object:
        # Read at dispatch time so a hot-reloaded config takes effect
        return self._win.config.widgets.get(key, {}).get('tap', 'default')

    def _wire_widget_tap(self, widget, key: str) -> None:
        from gi.repository import Gtk

        win = self._win
        if key == 'time':
            # The clock keeps double-tap-to-lock; a single tap (after the
            # double-tap window passes) runs the configured action
            tap_gesture = Gtk.GestureClick.new()
            tap_gesture.connect(
                'pressed', lambda _g, n, _x, _y: win._on_clock_tapped(n))
            widget.add_controller(tap_gesture)
            return
        gesture = Gtk.GestureClick.new()
        gesture.connect(
            'pressed',
            lambda _g, _n, _x, _y, k=key: win._dispatch_widget_tap(k, win._current_tap(k)))
        widget.add_controller(gesture)

    def _on_clock_tapped(self, n_press: int) -> None:
        from gi.repository import GLib

        win = self._win
        if n_press == 2:
            if win._clock_tap_timer is not None:
                GLib.source_remove(win._clock_tap_timer)
                win._clock_tap_timer = None
            win._show_lock_screen()
            return
        if n_press == 1 and win._clock_tap_timer is None:
            def _fire() -> bool:
                win._clock_tap_timer = None
                tap = win._current_tap('time')
                if tap != 'none':
                    win._dispatch_widget_tap('time', tap)
                return GLib.SOURCE_REMOVE
            win._clock_tap_timer = GLib.timeout_add(280, _fire)

    def _dispatch_widget_tap(self, key: str, tap: object) -> None:
        win = self._win
        if isinstance(tap, dict) and tap.get('app'):
            win._launch_app_id(str(tap['app']))
            return
        if key == 'weather':
            if tap == 'refresh':
                win._refresh_weather(force=True)
                return
            if tap == 'default':
                win._refresh_weather()
                self._open_weather_app()
                return
            return
        if tap == 'refresh':
            win._refresh_weather(force=True)
            return
        if tap != 'default':
            return
        if key == 'time':
            win._launch_first_matching('clock')
        elif key == 'date':
            win._launch_first_matching('calendar')
        elif key == 'battery':
            _open_settings(win)

    def _open_weather_app(self) -> None:
        """Default weather tap: an installed weather app, else a web
        search for "weather". The refresh already runs in the background."""
        win = self._win
        try:
            entries = list(win.app_index.entries)
        except (AttributeError, TypeError):
            entries = []
        for entry in entries:
            app_id = str(getattr(entry, 'app_id', ''))
            if app_id.removesuffix('.desktop').casefold() == (
                    'org.gnome.weather'):
                win._launch_entry(entry)
                return
        for entry in entries:
            if 'weather' in str(getattr(entry, 'name', '')).casefold():
                win._launch_entry(entry)
                return
        win._open_web_search('weather')

    def _launch_first_matching(self, needle: str) -> None:
        win = self._win
        entry = next(
            (e for e in win.app_index.entries if needle in e.name.casefold()), None)
        if entry is not None:
            win._launch_entry(entry)

    def _init_weather(self) -> None:
        from gi.repository import GLib
        from weather import WeatherProvider

        win = self._win
        win._weather = WeatherProvider(win.config)
        # No placeholder: the line stays hidden until real data arrives.
        win.weather_label.set_visible(False)
        win._refresh_weather()
        GLib.timeout_add_seconds(900, win._tick_weather)

    def _tick_weather(self) -> bool:
        self._refresh_weather()
        return True

    def _refresh_weather(self, force: bool = False) -> None:
        from weather import PLACEHOLDER

        win = self._win

        def _apply(text: str) -> None:
            if text == PLACEHOLDER:
                win.weather_label.set_visible(False)
                return
            win.weather_label.set_text(text)
            win.weather_label.set_visible(True)

        win._weather.refresh(_apply, force=force)

    def _refresh_clock(self) -> None:
        from clock_format import format_clock, resolve_24h

        win = self._win
        now = datetime.now()
        use_24h = resolve_24h(win.config)
        win.clock_label.set_text(format_clock(now, use_24h))
        win.date_label.set_text(now.strftime('%a, %b %d').replace(' 0', ' '))
        bar_time = getattr(win, 'status_bar_time', None)
        if bar_time is not None:
            try:
                bar_time.set_text(format_clock(now, use_24h))
            except Exception:
                pass

    def _tick_clock(self) -> bool:
        self._refresh_clock()
        return True

    def _refresh_status(self) -> None:
        from system_status import status_line_async

        win = self._win

        def _done(line: str) -> None:
            from privacy_monitor import format_indicator
            try:
                indicator = format_indicator(
                    getattr(win, '_privacy_snapshot', None) or {})
            except Exception:
                indicator = ''
            if indicator:
                line = f'{line}  ·  {indicator}' if line else indicator
            try:
                win.status_strip.set_text(line)
            except Exception:
                pass
            bar_info = getattr(win, 'status_bar_info', None)
            if bar_info is not None:
                try:
                    bar_info.set_text(line)
                except Exception:
                    pass
        status_line_async(_done)

    def set_privacy_snapshot(self, snapshot: dict | None) -> None:
        """Feed the privacy monitor snapshot; the status strip gains the
        `● mic · cam · loc` tail while something is in use."""
        win = self._win
        try:
            win._privacy_snapshot = {
                kind: set(apps)
                for kind, apps in dict(snapshot or {}).items()}
        except Exception:
            win._privacy_snapshot = {}
        try:
            win._refresh_status()
        except Exception:
            pass

    def _tick_status(self) -> bool:
        self._refresh_status()
        return True

    # -- home edit mode ----------------------------------------------------

    def _on_home_background_tap(self, n_press: int) -> None:
        """Double-tap on the home background runs double_tap_home."""
        if n_press != 2:
            return
        win = self._win
        action = win.gesture_config.get('double_tap_home') or 'none'
        if action == 'none':
            return
        win._dispatch_gesture_action(action)

    def _on_home_long_press(self, gesture, _x: float, _y: float) -> None:
        from gi.repository import Gtk

        win = self._win
        action = win.gesture_config.get('long_press_home') or 'edit'
        if action == 'none':
            return
        if action == 'edit':
            if win._home_launcher.edit_mode:
                return
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            win._home_launcher.set_edit_mode(True)
            return
        win._dispatch_gesture_action(action)

    def _on_edit_action(self, action: str, widget) -> None:
        win = self._win
        if action == 'done':
            win._home_launcher.set_edit_mode(False)
        elif action == 'settings':
            win._home_launcher.set_edit_mode(False)
            _open_settings(win)
        elif action == 'add_app':
            win._pick_slot_mode = True
            win.stack.set_visible_child_name('apps')
            win._show_status('Tap an app to add it to home.')
        elif action == 'new_folder':
            win.item_actions._show_entry_dialog(
                widget, 'New folder', '', 'Create', win._create_folder_slot)
        elif action == 'rearrange':
            win.item_actions.show_home_reorder(widget)

    def _visible_insert_index(self) -> int | None:
        """Where a new home slot goes so it shows: the first empty
        visible slot, else right after the visible ones (the count then
        grows, like Android's picker filling an empty slot). None when
        home is full (8 slots, the stored cap)."""
        win = self._win
        slots = win.config.home_slots
        if len(slots) >= 8:
            return None
        count = getattr(win.config, 'home_slot_count', None)
        if not isinstance(count, int) or len(slots) < count:
            return len(slots)
        return count if count < 8 else None

    def _grow_slot_count(self, index: int) -> None:
        """A slot inserted past the visible count makes the count grow."""
        win = self._win
        count = getattr(win.config, 'home_slot_count', None)
        if isinstance(count, int) and index >= count:
            win.config.set_home_slot_count(index + 1)

    def _pick_empty_slot(self) -> None:
        """An empty "· · ·" slot goes straight to the app picker."""
        win = self._win
        win._pick_slot_mode = True
        win._pick_slot_target = None
        win.stack.set_visible_child_name('apps')
        win._show_status('Tap an app to add it to home.')

    def _add_app_slot(self, entry: AppEntry) -> None:
        win = self._win
        slots = win.config.home_slots
        index = self._visible_insert_index()
        if index is None:
            win._show_status('Home is full — remove a slot first.')
            return
        display = win.config.label_for(entry.app_id, entry.name)
        slots.insert(index, {'type': 'app', 'label': display,
                             'app_id': entry.app_id, 'cmd': None,
                             'folder': None})
        win.config.set_home_slots(slots)
        self._grow_slot_count(index)
        win.stack.set_visible_child_name('home')
        win._home_launcher.refresh()
        win._populate_apps(win.apps_search.get_text())
        win._show_status(f'{display} added to home.')

    def _create_folder_slot(self, name: str) -> None:
        from app_item_actions import validate_folder_name

        win = self._win
        slots = win.config.home_slots
        error = validate_folder_name(slots, name)
        if error is not None:
            win._show_status(error)
            return
        index = self._visible_insert_index()
        if index is None:
            win._show_status('Home is full — remove a slot first.')
            return
        slots.insert(index, {'type': 'folder', 'label': name.strip(),
                             'app_id': None, 'cmd': None, 'folder': []})
        win.config.set_home_slots(slots)
        self._grow_slot_count(index)
        win._home_launcher.refresh()
        win._home_launcher.set_edit_mode(True)

    def _move_slot(self, idx: int, delta: int) -> None:
        win = self._win
        slots = win.config.home_slots
        new_idx = idx + delta
        if not (0 <= idx < len(slots) and 0 <= new_idx < len(slots)):
            return
        slots[idx], slots[new_idx] = slots[new_idx], slots[idx]
        win.config.set_home_slots(slots)
        win._home_launcher.refresh(preserve_folder=True)

    def _remove_slot(self, idx: int, widget=None) -> None:
        win = self._win
        slots = win.config.home_slots
        if not (0 <= idx < len(slots)):
            return
        if slots[idx].get('type') == 'folder' and widget is not None:
            win.item_actions.confirm_delete_folder(widget, idx)
            return
        removed = slots.pop(idx)
        win.config.set_home_slots(slots)
        count = getattr(win.config, 'home_slot_count', None)
        if isinstance(count, int) and idx < count:
            # Android's removeSlot shifts later slots up and shrinks the
            # count, so no empty "· · ·" row is left behind.
            win.config.set_home_slot_count(count - 1)
        win._home_launcher.refresh(preserve_folder=True)
        win._populate_apps(win.apps_search.get_text())
        win._show_status(f'{removed.get("label", "Slot")} removed from home.')

    def _rename_slot(self, widget, idx: int) -> None:
        """Edit-mode slot name tap: folders rename directly; app slots get
        a Rename / Change app / App info menu like the Android picker."""
        win = self._win
        slots = win.config.home_slots
        if not (0 <= idx < len(slots)):
            return
        if slots[idx].get('type') != 'app':
            self._rename_slot_dialog(widget, idx)
            return
        win.item_actions._show_action_menu(
            widget, slots[idx].get('label', ''),
            [('Rename', lambda: self._rename_slot_dialog(widget, idx)),
             ('Change app', lambda: self._pick_replace_slot(idx)),
             ('App info', lambda: self._show_slot_info(widget, idx))])

    def _pick_replace_slot(self, idx: int) -> None:
        """Change app: drawer pick mode retargeted at slots[idx]."""
        win = self._win
        win._pick_slot_mode = True
        win._pick_slot_target = idx
        win.stack.set_visible_child_name('apps')
        win._show_status('Tap an app to replace this slot.')

    def _show_slot_info(self, widget, idx: int) -> None:
        win = self._win
        slots = win.config.home_slots
        if not (0 <= idx < len(slots)):
            return
        win.item_actions._show_member_info(widget, dict(slots[idx]))

    def _replace_app_slot(self, idx: int, entry: AppEntry) -> None:
        """Swap the picked app into slots[idx], keeping its position."""
        win = self._win
        slots = win.config.home_slots
        if not (0 <= idx < len(slots)) or slots[idx].get('type') != 'app':
            self._add_app_slot(entry)
            return
        display = win.config.label_for(entry.app_id, entry.name)
        slots[idx] = {'type': 'app', 'label': display, 'app_id': entry.app_id,
                      'cmd': None, 'folder': None}
        win.config.set_home_slots(slots)
        win.stack.set_visible_child_name('home')
        win._home_launcher.refresh()
        win._populate_apps(win.apps_search.get_text())
        win._show_status(f'{display} added to home.')

    def _real_app_name(self, app_id: str) -> str | None:
        index = getattr(self._win, 'app_index', None)
        for entry in getattr(index, 'entries', []) or []:
            if entry.app_id == app_id:
                return entry.name
        return None

    def _rename_slot_dialog(self, widget, idx: int) -> None:
        win = self._win
        slots = win.config.home_slots
        if not (0 <= idx < len(slots)):
            return
        slot = slots[idx]
        app_id = slot.get('app_id')

        def _commit(text: str) -> None:
            if slot.get('type') == 'folder':
                from app_item_actions import validate_folder_name
                error = validate_folder_name(slots, text, exclude=idx)
                if error is not None:
                    win._show_status(error)
                    return
                slots[idx]['label'] = text.strip()
                win.config.set_home_slots(slots)
            elif slot.get('type') == 'app' and app_id:
                # A blank rename clears the override and restores the
                # app's real name; any other rename fans out to every
                # slot and folder member holding the app.
                key = str(app_id)
                real = self._real_app_name(key)
                if not text:
                    win.config.set_app_label(key, None)
                    label = real if real is not None else slot.get('label', '')
                else:
                    win.config.set_app_label(
                        key, None if text == real else text)
                    label = text
                slots[idx]['label'] = label
                win.item_actions._rename_in_slots(key, label)
                win.config.set_home_slots(slots)
            elif text:
                slots[idx]['label'] = text
                win.config.set_home_slots(slots)
            win._refresh_after_item_action()

        win.item_actions._show_entry_dialog(
            widget, 'Rename', slots[idx].get('label', ''), 'Rename', _commit,
            allow_empty=True)
