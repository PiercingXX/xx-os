"""
Long-press action menus shared by the app drawer and home folder members —
the shell's counterpart to the Android launcher's AppItemActions.

Menus, pickers and prompts show as in-window bottom sheets
(action_sheet.py), like the Android launcher's ActionSheet: a GtkPopover
on the shell's layer-shell surface is dismissed by the compositor
whenever the shell changes keyboard mode. Text entries commit on Enter,
same as the confirm button (design.md "Dialogs & menus").
"""
from __future__ import annotations

import time
from typing import Callable

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Pango

from app_index import AppEntry
from config import ShellConfig

_DISABLE_DURATIONS = [('1 hour', 1), ('2 hours', 2), ('4 hours', 4), ('8 hours', 8)]


def muted_menu_subtitle(config: ShellConfig, app_id: str | None,
                        now: float | None = None) -> str | None:
    """`Muted until HH:MM` for a currently muted app, else None. Pure
    apart from the clock-format read, which never raises."""
    if not app_id:
        return None
    try:
        until = config.muted_until(app_id, now=now)
    except (AttributeError, TypeError):
        return None
    if until is None:
        return None
    from datetime import datetime
    from clock_format import format_clock, resolve_24h
    try:
        use_24h = resolve_24h(config)
    except Exception:
        use_24h = True
    return f'Muted until {format_clock(datetime.fromtimestamp(until), use_24h)}'

# Phone-narrow popover cap: the shell runs at 320–360 px, so menu text must
# never size the popover past the screen. Item labels wrap (WORD_CHAR still
# breaks dot-chains like long app ids); titles stay one line and ellipsize.
_MENU_TEXT_WIDTH = 28
# Menus longer than this scroll inside a capped height (px).
_MENU_SCROLL_AFTER = 8
_MENU_MAX_HEIGHT = 440


def validate_folder_name(slots: list[dict], name: str,
                         exclude: int | None = None) -> str | None:
    """Folder-name rule shared by every create/rename path: blank names and
    duplicate names (case-insensitive) are rejected with a short message."""
    if not name.strip():
        return 'Folder name cannot be blank.'
    wanted = name.strip().casefold()
    for idx, slot in enumerate(slots):
        if slot.get('type') != 'folder' or idx == exclude:
            continue
        if str(slot.get('label', '')).casefold() == wanted:
            return 'A folder with that name already exists.'
    return None


def add_standby_exempt(config, app_id: str) -> bool:
    """Add an app to the never-freeze exemptions. True when added."""
    try:
        from app_standby import normalize_app_id
    except ImportError:
        def normalize_app_id(value: str) -> str:
            text = str(value or '').strip()
            if text.lower().endswith('.desktop'):
                text = text[:-8]
            return text.casefold()
    try:
        exempt = list(config.standby_exempt or [])
    except Exception:
        return False
    wanted = normalize_app_id(app_id)
    if any(normalize_app_id(entry) == wanted for entry in exempt):
        return False
    exempt.append(app_id)
    try:
        config.set_standby_exempt(exempt)
    except Exception:
        return False
    return True


def open_app_page(win, page: str, entry: AppEntry) -> bool:
    """Open the existing per-app settings page for an info-popover button:
    the permissions app detail (which also holds network access), the
    standby section (adding the exemption first), or the screen-time
    section with the app selected. Duck-typed so bare doubles record it."""
    app_id = getattr(entry, 'app_id', '') or ''
    if not app_id:
        return False
    try:
        sections = win._settings_page
        if page in ('permissions', 'network access'):
            win.open_settings('permissions')
            section = sections.section('permissions')
            if section is not None:
                section._open_app_view(win, app_id)
        elif page == 'standby exemption':
            add_standby_exempt(win.config, app_id)
            win.open_settings('standby')
            section = sections.section('standby')
            if section is not None:
                section._render_exempt(win)
        elif page == 'screen-time limit':
            win.open_settings('screentime')
            section = sections.section('screentime')
            if section is not None:
                section._select(win, app_id)
        else:
            return False
    except Exception:
        return False
    return True


class AppItemActions:
    """Builds and shows item action popovers; mutations go through config."""

    def __init__(self, config: ShellConfig,
                 on_changed: Callable[[], None],
                 on_status: Callable[[str], None],
                 focus_state: object | None = None,
                 nope_state: object | None = None,
                 on_popover_changed: Callable[[bool], None] | None = None,
                 on_keyboard: Callable[[bool], None] | None = None,
                 real_name_fn: Callable[[str], str | None] | None = None,
                 on_launch: Callable[[], None] | None = None,
                 on_open_page=None,
                 on_muted: Callable[[str], None] | None = None,
                 on_uninstall: Callable[[object, AppEntry], None] | None = None,
                 ) -> None:
        self._config = config
        self._on_changed = on_changed
        self._on_status = on_status
        self._focus = focus_state
        self._nope = nope_state
        self._on_popover_changed = on_popover_changed
        self._on_keyboard = on_keyboard
        self._real_name_fn = real_name_fn
        self._on_launch = on_launch
        self._on_open_page = on_open_page
        self._on_muted = on_muted
        self._on_uninstall = on_uninstall

    def _real_name(self, app_id: str) -> str | None:
        """The app's current desktop Name, for blank-rename resets."""
        if not callable(self._real_name_fn):
            return None
        try:
            return self._real_name_fn(app_id)
        except Exception:
            return None

    @staticmethod
    def desktop_action_items(entry: AppEntry) -> list[tuple[str, str]]:
        """(localized label, action id) for the row's Desktop Actions
        (the Linux counterpart of Android's pinned shortcuts)."""
        try:
            actions = entry.desktop_app.list_actions() or ()
        except Exception:
            return []
        items = []
        for action in actions:
            try:
                name = entry.desktop_app.get_action_name(action)
            except Exception:
                name = None
            items.append((name or str(action), str(action)))
        return items

    @staticmethod
    def launch_desktop_action(entry: AppEntry, action_id: str) -> bool:
        """Launch one Desktop Action; False when the backend refuses."""
        try:
            entry.desktop_app.launch_action(action_id, None)
        except Exception:
            return False
        return True

    # -- popover plumbing --------------------------------------------------

    def _show_popover(self, anchor: Gtk.Widget, content: Gtk.Widget):
        """Show ``content`` for ``anchor``: an in-window sheet when the
        anchor's window hosts one (the shell), else a plain popover
        (windows without a SheetHost)."""
        from action_sheet import host_for, in_settings

        host = host_for(anchor)
        if host is not None:
            sheet = host.present(content, settings_scope=in_settings(anchor))

            def _on_sheet_closed(_sheet) -> None:
                if self._on_popover_changed is not None:
                    self._on_popover_changed(False)

            sheet.connect('closed', _on_sheet_closed)
            if self._on_popover_changed is not None:
                self._on_popover_changed(True)
            return sheet
        popover = Gtk.Popover()
        popover.add_css_class('action-menu')
        popover.set_has_arrow(False)
        popover.set_child(content)
        popover.set_parent(anchor)
        popover.set_position(Gtk.PositionType.BOTTOM)

        def _on_closed(p: Gtk.Popover) -> None:
            p.unparent()
            if self._on_popover_changed is not None:
                self._on_popover_changed(False)

        popover.connect('closed', _on_closed)
        popover.popup()
        if self._on_popover_changed is not None:
            self._on_popover_changed(True)
        return popover

    @staticmethod
    def _cap_title(label: Gtk.Label) -> None:
        # Two lines before the ellipsis: large text in a phone-width
        # sheet would otherwise cut "Swipe Down Action" mid-word.
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(_MENU_TEXT_WIDTH)
        if hasattr(label, 'set_lines'):
            label.set_wrap(True)
            label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.set_lines(2)

    @staticmethod
    def _cap_item_label(label: Gtk.Label) -> None:
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(_MENU_TEXT_WIDTH)

    def _action_list(self, actions: list[tuple[str, Callable[[], None]]],
                     title: str | None = None,
                     subtitle: str | None = None,
                     checked: int | None = None) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.add_css_class('xx-dialog')
        if title:
            heading = Gtk.Label(label=title, xalign=0)
            heading.add_css_class('xx-dialog-title')
            self._cap_title(heading)
            box.append(heading)
        if subtitle:
            sub = Gtk.Label(label=subtitle, xalign=0)
            sub.add_css_class('xx-secondary')
            self._cap_item_label(sub)
            box.append(sub)
        self._popover_ref: Gtk.Popover | None = None

        def _run(callback: Callable[[], None]) -> None:
            if self._popover_ref is not None:
                self._popover_ref.popdown()
            callback()

        rows = box
        if len(actions) > _MENU_SCROLL_AFTER:
            # A long choice list scrolls inside the menu instead of
            # running off the bottom of a phone screen.
            rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.NEVER,
                                Gtk.PolicyType.AUTOMATIC)
            scroller.set_propagate_natural_height(True)
            scroller.set_max_content_height(_MENU_MAX_HEIGHT)
            scroller.set_child(rows)
            box.append(scroller)
        for index, (label, callback) in enumerate(actions):
            if index == checked:
                btn = self._checked_row(label)
            else:
                btn = Gtk.Button(label=label)
                btn.add_css_class('xx-menu-row')
                child = btn.get_child()
                child.set_xalign(0)
                self._cap_item_label(child)
            btn.connect('clicked', lambda _b, cb=callback: _run(cb))
            rows.append(btn)
        return box

    def _checked_row(self, label: str) -> Gtk.Button:
        """A choice row marked as the current value (the Android list
        preference's checked radio), the check trailing."""
        btn = Gtk.Button()
        btn.add_css_class('xx-menu-row')
        btn.add_css_class('checked')
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        text = Gtk.Label(label=label, xalign=0)
        text.set_hexpand(True)
        self._cap_item_label(text)
        mark = Gtk.Label(label='✓')
        mark.add_css_class('xx-menu-check')
        line.append(text)
        line.append(mark)
        btn.set_child(line)
        return btn

    def _show_action_menu(self, anchor: Gtk.Widget, title: str | None,
                          actions: list[tuple[str, Callable[[], None]]],
                          subtitle: str | None = None,
                          checked: int | None = None) -> None:
        content = self._action_list(actions, title, subtitle, checked)
        self._popover_ref = self._show_popover(anchor, content)

    def _entry_dialog_content(self, title: str, initial: str,
                                commit_label: str,
                                on_commit: Callable[[str], None],
                                password: bool | None = None,
                                on_popover_commit=None,
                                allow_empty: bool = False):
        """Rename/confirm/entry dialog body: title, entry, primary commit.

        Returns (box, entry, commit). The popover wrapper commits first so
        the same body renders inline (screenshots) and in a popover.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class('xx-dialog')
        heading = Gtk.Label(label=title, xalign=0)
        heading.add_css_class('xx-dialog-title')
        self._cap_title(heading)
        entry = Gtk.Entry()
        entry.add_css_class('xx-entry')
        entry.set_text(initial)
        entry.set_max_width_chars(_MENU_TEXT_WIDTH)
        if password is None:
            is_password = self._title_is_password(title)
        else:
            is_password = password
        if is_password:
            entry.set_visibility(False)
            entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        else:
            entry.select_region(0, -1)
        commit_btn = Gtk.Button(label=commit_label)
        commit_btn.add_css_class('xx-btn')
        commit_btn.add_css_class('xx-btn-primary')
        self._cap_item_label(commit_btn.get_child())
        box.append(heading)
        box.append(entry)
        box.append(commit_btn)

        def _commit(*_args: object) -> None:
            raw = entry.get_text()
            text = raw if is_password else raw.strip()
            if on_popover_commit is not None:
                on_popover_commit()
            if text or allow_empty:
                on_commit(text)

        entry.connect('activate', _commit)
        commit_btn.connect('clicked', _commit)
        if callable(self._on_keyboard):
            from osk import attach
            attach(entry, on_show=lambda: self._on_keyboard(True))
        return box, entry, _commit

    def _show_entry_dialog(self, anchor: Gtk.Widget, title: str, initial: str,
                           commit_label: str, on_commit: Callable[[str], None],
                           password: bool | None = None,
                           allow_empty: bool = False) -> None:
        shown: dict = {}

        def _pop_shown() -> None:
            popover = shown.get('popover')
            if popover is not None:
                shown['popover'] = None
                popover.popdown()

        box, entry, _commit = self._entry_dialog_content(
            title, initial, commit_label, on_commit, password,
            on_popover_commit=_pop_shown, allow_empty=allow_empty)
        popover = self._show_popover(anchor, box)
        shown['popover'] = popover

        def _closed(_p: Gtk.Popover) -> None:
            if callable(self._on_keyboard):
                self._on_keyboard(False)

        if hasattr(popover, 'connect'):
            popover.connect('closed', _closed)
        entry.grab_focus()
        if callable(self._on_keyboard):
            self._on_keyboard(True)

    @staticmethod
    def _title_is_password(title: str) -> bool:
        return 'password' in title.casefold()

    # -- drawer app rows ---------------------------------------------------

    def _build_app_menu_actions(
            self, anchor: Gtk.Widget, entry: AppEntry,
            hidden_view: bool = False) -> list[tuple[str, Callable[[], None]]]:
        config = self._config
        actions: list[tuple[str, Callable[[], None]]] = []
        actions.append(('App info', lambda: self._show_app_info(anchor, entry)))
        for display, action_id in self.desktop_action_items(entry):
            actions.append((display, lambda aid=action_id, lb=display:
                            self._run_desktop_action(entry, aid, lb)))
        actions.append(('Rename', lambda: self._show_rename(anchor, entry)))
        actions.append(('Add to folder', lambda: self._show_add_to_folder(anchor, entry)))
        actions.append(('Add to Home', lambda: self._add_to_home(
            entry.app_id, config.label_for(entry.app_id, entry.name))))
        if entry.app_id in config.hidden_apps or hidden_view:
            actions.append(('Show', lambda: self._set_hidden(entry, False)))
        else:
            actions.append(('Hide', lambda: self._set_hidden(entry, True)))
        actions.append(('Disable for…', lambda: self._show_disable_for(anchor, entry.app_id, entry.name)))
        if self._focus is not None:
            if self._focus.is_focus_app(entry.app_id):
                actions.append(('Focus: unpause this app',
                                lambda: self._set_focus_app(entry, False)))
            else:
                actions.append(('Focus: pause this app',
                                lambda: self._set_focus_app(entry, True)))
        if entry.app_id in config.pinned:
            actions.append(('Unpin', lambda: self._set_pinned(entry, False)))
            actions.append(('Move up', lambda: self._move_pinned(entry, -1)))
            actions.append(('Move down', lambda: self._move_pinned(entry, +1)))
            if len(config.pinned) >= 2:
                actions.append(('Rearrange pinned…',
                                lambda: self._show_pinned_reorder(anchor)))
        else:
            actions.append(('Pin', lambda: self._set_pinned(entry, True)))
        if self._on_uninstall is not None:
            actions.append(('Uninstall',
                            lambda: self._ask_uninstall(anchor, entry)))
        return actions

    def show_app_menu(self, anchor: Gtk.Widget, entry: AppEntry,
                      hidden_view: bool = False) -> None:
        display = self._config.label_for(entry.app_id, entry.name)
        self._show_action_menu(
            anchor, display,
            self._build_app_menu_actions(anchor, entry, hidden_view),
            subtitle=muted_menu_subtitle(self._config, entry.app_id))

    def info_page_items(self, entry: AppEntry) -> list[tuple[str, str]]:
        """(button label, page key) for the App info popover: the existing
        per-app pages and actions."""
        items = [('Permissions', 'permissions'),
                 ('Network access', 'network access'),
                 ('Standby exemption', 'standby exemption'),
                 ('Screen-time limit', 'screen-time limit')]
        if self._focus is not None:
            if self._focus.is_focus_app(entry.app_id):
                items.append(('Focus: unpause this app', 'focus unpause'))
            else:
                items.append(('Focus: pause this app', 'focus pause'))
        items.append(('Disable for…', 'disable'))
        return items

    def _open_info_page(self, anchor: Gtk.Widget, page: str,
                        entry: AppEntry) -> None:
        name = self._config.label_for(entry.app_id, entry.name)
        if page == 'disable':
            self._show_disable_for(anchor, entry.app_id, name)
        elif page == 'focus pause':
            self._set_focus_app(entry, True)
        elif page == 'focus unpause':
            self._set_focus_app(entry, False)
        elif self._on_open_page is not None:
            self._on_open_page(page, entry)
        else:
            self._on_status('No settings page is wired for %s.' % page)

    def _ask_uninstall(self, anchor: Gtk.Widget, entry: AppEntry) -> None:
        if callable(self._on_uninstall):
            self._on_uninstall(anchor, entry)

    def _show_app_info(self, anchor: Gtk.Widget, entry: AppEntry) -> None:
        lines = [entry.name]
        if entry.description:
            lines.append(entry.description)
        lines.append(entry.app_id)
        if entry.executable:
            lines.append(entry.executable)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for idx, line in enumerate(lines):
            lbl = Gtk.Label(label=line, xalign=0, wrap=True)
            lbl.add_css_class('xx-dialog-title' if idx == 0 else 'xx-secondary')
            if idx == 0:
                lbl.set_wrap(False)
                self._cap_title(lbl)
            else:
                self._cap_item_label(lbl)
            box.append(lbl)
        for label, page in self.info_page_items(entry):
            btn = Gtk.Button(label=label)
            btn.add_css_class('xx-menu-row')
            child = btn.get_child()
            child.set_xalign(0)
            self._cap_item_label(child)
            btn.connect('clicked',
                        lambda _b, p=page: self._open_info_page(anchor, p, entry))
            box.append(btn)
        self._show_popover(anchor, box)

    def _run_desktop_action(self, entry: AppEntry, action_id: str,
                            display: str) -> None:
        try:
            nope = getattr(self, '_nope', None)
            blocked = bool(nope is not None and nope.is_blocked(entry.app_id))
        except Exception:
            blocked = False
        if blocked:
            try:
                self._on_status(nope.notice_text(display or entry.name))
            except Exception:
                pass
            return
        if self.launch_desktop_action(entry, action_id):
            try:
                self._config.record_launch(entry.app_id)
            except Exception:
                pass
            if self._on_launch is not None:
                self._on_launch()
        else:
            self._on_status('Could not open %s.' % display)

    def _show_rename(self, anchor: Gtk.Widget, entry: AppEntry) -> None:
        current = self._config.label_for(entry.app_id, entry.name)

        def _commit(text: str) -> None:
            if not text:
                self._config.set_app_label(entry.app_id, None)
                self._rename_in_slots(entry.app_id, entry.name)
                self._on_changed()
                self._on_status(f'Renamed to {entry.name}.')
                return
            self._config.set_app_label(entry.app_id, None if text == entry.name else text)
            self._rename_in_slots(entry.app_id, text)
            self._on_changed()
            self._on_status(f'Renamed to {text}.')

        self._show_entry_dialog(anchor, 'Rename', current, 'Rename', _commit,
                                allow_empty=True)

    def _add_to_home(self, app_id: str, display: str) -> None:
        """Put an app in the next free home slot, up to the 8-slot cap."""
        slots = self._config.home_slots
        for slot in slots:
            if slot.get('type') == 'app' and slot.get('app_id') == app_id:
                self._on_status('Already on home.')
                return
            for member in slot.get('folder') or []:
                if member.get('app_id') == app_id:
                    self._on_status('Already on home.')
                    return
        if len(slots) >= 8:
            self._on_status('Home is full — remove a slot first.')
            return
        slots.append({'type': 'app', 'label': display, 'app_id': app_id,
                      'cmd': None, 'folder': None})
        self._config.set_home_slots(slots)
        self._on_changed()
        self._on_status(f'{display} added to home.')

    def _rename_in_slots(self, app_id: str, label: str) -> None:
        slots = self._config.home_slots
        changed = False
        for slot in slots:
            if slot.get('type') == 'app' and slot.get('app_id') == app_id:
                slot['label'] = label
                changed = True
            for member in slot.get('folder') or []:
                if member.get('app_id') == app_id:
                    member['label'] = label
                    changed = True
        if changed:
            self._config.set_home_slots(slots)

    def _show_add_to_folder(self, anchor: Gtk.Widget, entry: AppEntry) -> None:
        slots = self._config.home_slots
        display = self._config.label_for(entry.app_id, entry.name)
        actions: list[tuple[str, Callable[[], None]]] = []
        for idx, slot in enumerate(slots):
            if slot.get('type') != 'folder':
                continue
            actions.append((slot.get('label', 'Folder'),
                            lambda i=idx: self._add_member(i, entry, display)))
        actions.append(('New folder…', lambda: self._show_new_folder(anchor, entry, display)))
        self._show_action_menu(anchor, 'Add to folder', actions)

    def _add_member(self, slot_index: int, entry: AppEntry, display: str) -> None:
        slots = self._config.home_slots
        if not (0 <= slot_index < len(slots)):
            return
        members = slots[slot_index].setdefault('folder', [])
        if any(m.get('app_id') == entry.app_id for m in members):
            self._on_status('Already in that folder.')
            return
        members.append({'label': display, 'app_id': entry.app_id})
        self._config.set_home_slots(slots)
        self._on_changed()
        self._on_status(f'Added to {slots[slot_index].get("label", "folder")}.')

    def _show_new_folder(self, anchor: Gtk.Widget, entry: AppEntry, display: str) -> None:
        def _commit(name: str) -> None:
            slots = self._config.home_slots
            error = validate_folder_name(slots, name)
            if error is not None:
                self._on_status(error)
                return
            if len(slots) >= 8:
                self._on_status('Home is full — remove a slot first.')
                return
            slots.append({'type': 'folder', 'label': name, 'app_id': None, 'cmd': None,
                          'folder': [{'label': display, 'app_id': entry.app_id}]})
            self._config.set_home_slots(slots)
            self._on_changed()
            self._on_status(f'Created folder {name}.')

        self._show_entry_dialog(anchor, 'New folder', '', 'Create', _commit)

    def _set_hidden(self, entry: AppEntry, hide: bool) -> None:
        self._set_hidden_id(entry.app_id, hide)

    def _set_hidden_id(self, app_id: str, hide: bool) -> None:
        hidden = [h for h in self._config.hidden_apps if h != app_id]
        if hide:
            hidden.append(app_id)
        self._config.set_hidden_apps(hidden)
        self._on_changed()
        self._on_status('App hidden — still reachable via search.' if hide else 'App shown in drawer.')

    def _set_pinned(self, entry: AppEntry, pin: bool) -> None:
        pinned = [p for p in self._config.pinned if p != entry.app_id]
        if pin:
            pinned.append(entry.app_id)
        self._config.set_pinned(pinned)
        self._on_changed()
        self._on_status('Pinned.' if pin else 'Unpinned.')

    def _move_pinned(self, entry: AppEntry, delta: int) -> None:
        pinned = self._config.pinned
        if entry.app_id not in pinned:
            return
        idx = pinned.index(entry.app_id)
        new_idx = idx + delta
        if not (0 <= new_idx < len(pinned)):
            self._on_status('Already at the top.' if delta < 0 else 'Already at the bottom.')
            return
        pinned[idx], pinned[new_idx] = pinned[new_idx], pinned[idx]
        self._config.set_pinned(pinned)
        self._on_changed()

    def _set_focus_app(self, entry: AppEntry, focused: bool) -> None:
        self._focus.set_app_focused(entry.app_id, focused)
        self._on_changed()
        self._on_status(
            f'{entry.name} pauses while Focus is on.' if focused
            else f'{entry.name} removed from Focus.')

    def _mute_app(self, app_id: str, name: str, hours: int, label: str) -> None:
        """Mute an app, then drop its live notifications through on_muted."""
        self._config.set_app_muted(app_id, time.time() + hours * 3600)
        self._on_status(f'{name} notifications off for {label}.')
        if self._on_muted is not None:
            self._on_muted(app_id)

    def _show_disable_for(self, anchor: Gtk.Widget, app_id: str, name: str) -> None:
        def _mute(hours: int, label: str) -> None:
            self._mute_app(app_id, name, hours, label)

        actions = [(label, lambda h=hours, lb=label: _mute(h, lb))
                   for label, hours in _DISABLE_DURATIONS]
        self._show_action_menu(anchor, 'Disable for…', actions)

    # -- folder members (home and drawer) ----------------------------------

    def show_member_menu(self, anchor: Gtk.Widget, slot_index: int, member_index: int) -> None:
        slots = self._config.home_slots
        if not (0 <= slot_index < len(slots)):
            return
        members = slots[slot_index].get('folder') or []
        if not (0 <= member_index < len(members)):
            return
        member = members[member_index]
        label = member.get('label', '')
        app_id = member.get('app_id')

        actions: list[tuple[str, Callable[[], None]]] = []
        actions.append(('App info', lambda: self._show_member_info(anchor, member)))
        actions.append(('Rename', lambda: self._show_member_rename(anchor, slot_index, member_index)))
        if app_id:
            hidden = app_id in self._config.hidden_apps
            actions.append(('Show' if hidden else 'Hide',
                            lambda: self._set_hidden_id(app_id, not hidden)))
            actions.append(('Add to Home',
                            lambda: self._add_to_home(app_id, label)))
            actions.append(('Disable for…', lambda: self._show_disable_for(anchor, app_id, label)))
        actions.append(('Move up', lambda: self._move_member(slot_index, member_index, -1)))
        actions.append(('Move down', lambda: self._move_member(slot_index, member_index, +1)))
        actions.append(('Sort A–Z', lambda: self.sort_folder_members(slot_index)))
        actions.append(('Rearrange…', lambda: self._show_folder_reorder(anchor, slot_index)))
        actions.append(('Remove from folder', lambda: self._remove_member(slot_index, member_index)))
        self._show_action_menu(
            anchor, label, actions,
            subtitle=muted_menu_subtitle(self._config, app_id))

    def _show_member_info(self, anchor: Gtk.Widget, member: dict) -> None:
        lines = [member.get('label', '')]
        if member.get('app_id'):
            lines.append(str(member['app_id']))
        if member.get('cmd'):
            lines.append(' '.join(member['cmd']))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for idx, line in enumerate(lines):
            lbl = Gtk.Label(label=line, xalign=0, wrap=True)
            lbl.add_css_class('xx-dialog-title' if idx == 0 else 'xx-secondary')
            if idx == 0:
                lbl.set_wrap(False)
                self._cap_title(lbl)
            else:
                self._cap_item_label(lbl)
            box.append(lbl)
        self._show_popover(anchor, box)

    def _show_member_rename(self, anchor: Gtk.Widget, slot_index: int, member_index: int) -> None:
        slots = self._config.home_slots
        member = (slots[slot_index].get('folder') or [])[member_index]

        def _commit(text: str) -> None:
            if text:
                slots[slot_index]['folder'][member_index]['label'] = text
                if member.get('app_id'):
                    self._config.set_app_label(str(member['app_id']), text)
                self._on_status(f'Renamed to {text}.')
            elif member.get('app_id'):
                # Blank clears the override and restores the real name at
                # once, like the drawer rename.
                self._config.set_app_label(str(member['app_id']), None)
                real = self._real_name(str(member['app_id']))
                if real:
                    slots[slot_index]['folder'][member_index]['label'] = real
                self._on_status('Name reset.')
            self._config.set_home_slots(slots)
            self._on_changed()

        self._show_entry_dialog(anchor, 'Rename', member.get('label', ''), 'Rename', _commit,
                                allow_empty=True)

    def _remove_member(self, slot_index: int, member_index: int) -> None:
        slots = self._config.home_slots
        if not (0 <= slot_index < len(slots)):
            return
        members = slots[slot_index].get('folder') or []
        if not (0 <= member_index < len(members)):
            return
        removed = members.pop(member_index)
        if members:
            slots[slot_index]['folder'] = members
            self._on_status(f'{removed.get("label", "Item")} removed from folder.')
        else:
            label = slots[slot_index].get('label', 'Folder')
            slots.pop(slot_index)
            self._on_status(f'{label} deleted — its apps are back in the drawer.')
        self._config.set_home_slots(slots)
        self._on_changed()

    def _move_member(self, slot_index: int, member_index: int, delta: int) -> None:
        slots = self._config.home_slots
        members = slots[slot_index].get('folder') or []
        new_idx = member_index + delta
        if not (0 <= new_idx < len(members)):
            self._on_status('Already at the top.' if delta < 0 else 'Already at the bottom.')
            return
        members[member_index], members[new_idx] = members[new_idx], members[member_index]
        slots[slot_index]['folder'] = members
        self._config.set_home_slots(slots)
        self._on_changed()

    # -- drawer folder rows --------------------------------------------------

    def _folder_slot(self, slot_index: int) -> dict | None:
        slots = self._config.home_slots
        if not (0 <= slot_index < len(slots)):
            return None
        slot = slots[slot_index]
        return slot if slot.get('type') == 'folder' else None

    def show_folder_menu(self, anchor: Gtk.Widget, slot_index: int) -> None:
        slot = self._folder_slot(slot_index)
        if slot is None:
            return
        actions: list[tuple[str, Callable[[], None]]] = [
            ('Rename', lambda: self._show_folder_rename(anchor, slot_index)),
            ('Move up', lambda: self.move_folder(slot_index, -1)),
            ('Move down', lambda: self.move_folder(slot_index, +1)),
            ('Sort A–Z', lambda: self.sort_folder_members(slot_index)),
            ('Rearrange…', lambda: self._show_folder_reorder(anchor, slot_index)),
            ('Delete', lambda: self.confirm_delete_folder(anchor, slot_index)),
        ]
        self._show_action_menu(anchor, slot.get('label', 'Folder'), actions)

    def _show_folder_rename(self, anchor: Gtk.Widget, slot_index: int) -> None:
        slot = self._folder_slot(slot_index)
        if slot is None:
            return

        def _commit(text: str) -> None:
            self.rename_folder(slot_index, text)

        self._show_entry_dialog(
            anchor, 'Rename folder', slot.get('label', ''), 'Rename', _commit)

    def rename_folder(self, slot_index: int, name: str) -> bool:
        slots = self._config.home_slots
        if self._folder_slot(slot_index) is None:
            return False
        error = validate_folder_name(slots, name, exclude=slot_index)
        if error is not None:
            self._on_status(error)
            return False
        slots[slot_index]['label'] = name.strip()
        self._config.set_home_slots(slots)
        self._on_changed()
        self._on_status(f'Renamed to {name.strip()}.')
        return True

    def move_folder(self, slot_index: int, delta: int) -> bool:
        slots = self._config.home_slots
        if self._folder_slot(slot_index) is None:
            return False
        new_idx = slot_index + delta
        if not (0 <= new_idx < len(slots)):
            self._on_status('Already at the top.' if delta < 0 else 'Already at the bottom.')
            return False
        slots[slot_index], slots[new_idx] = slots[new_idx], slots[slot_index]
        self._config.set_home_slots(slots)
        self._on_changed()
        return True

    def sort_folder_members(self, slot_index: int, notify: bool = True) -> bool:
        slot = self._folder_slot(slot_index)
        if slot is None:
            return False
        members = slot.get('folder') or []
        members.sort(key=lambda m: str(m.get('label', '')).casefold())
        slot['folder'] = members
        self._config.set_home_slots(self._config.home_slots)
        # The open reorder sheet owns its anchor: sorting from its Sort row
        # persists silently and Done refreshes, so the popover never loses
        # the row it is anchored on mid-drag.
        if notify:
            self._on_changed()
        self._on_status(f'{slot.get("label", "Folder")} sorted A–Z.')
        return True

    def confirm_delete_folder(self, anchor: Gtk.Widget, slot_index: int) -> None:
        slot = self._folder_slot(slot_index)
        if slot is None:
            return
        name = slot.get('label', 'Folder')
        self._show_action_menu(
            anchor, f'Delete folder "{name}"? Its apps return to the drawer.',
            [('Delete', lambda: self.delete_folder(slot_index)),
             ('Cancel', lambda: None)])

    def delete_folder(self, slot_index: int) -> None:
        slots = self._config.home_slots
        if self._folder_slot(slot_index) is None:
            return
        removed = slots.pop(slot_index)
        self._config.set_home_slots(slots)
        self._on_changed()
        self._on_status(
            f'{removed.get("label", "Folder")} deleted — its apps are back in the drawer.')

    # -- drag-to-reorder sheets -----------------------------------------------

    def show_reorder(self, anchor: Gtk.Widget, title: str,
                     items: list[tuple[str, str]],
                     on_order_changed: Callable[[list[str]], None],
                     on_sort: Callable[[object], None] | None = None) -> None:
        """One shared reorder sheet; drops persist silently and Done refreshes."""
        import reorder_sheet

        shown: dict = {}

        def _done() -> None:
            popover = shown.get('popover')
            if popover is not None:
                shown['popover'] = None
                popover.popdown()
            self._on_changed()

        box, view = reorder_sheet.build_reorder_box(
            title, items, on_order_changed,
            on_sort=(lambda: on_sort(view)) if on_sort is not None else None,
            on_done=_done)
        shown['popover'] = self._show_popover(anchor, box)

    def _show_folder_reorder(self, anchor: Gtk.Widget, slot_index: int) -> None:
        slot = self._folder_slot(slot_index)
        if slot is None:
            return
        members = slot.get('folder') or []
        if len(members) < 2:
            self._on_status('Needs at least two apps to rearrange.')
            return
        name = slot.get('label', 'Folder')
        items = [(str(i), str(m.get('label', '')))
                 for i, m in enumerate(members)]

        def _on_sort(view) -> None:
            self.sort_folder_members(slot_index, notify=False)
            ordered = (self._folder_slot(slot_index) or {}).get('folder') or []
            view.replace([(str(i), str(m.get('label', '')))
                          for i, m in enumerate(ordered)])

        self.show_reorder(
            anchor, f'Rearrange {name}', items,
            on_order_changed=lambda ids: self.apply_member_order(slot_index, ids),
            on_sort=_on_sort)

    def _show_pinned_reorder(self, anchor: Gtk.Widget) -> None:
        pinned = list(self._config.pinned)
        if len(pinned) < 2:
            self._on_status('Needs at least two apps to rearrange.')
            return
        items = [(app_id, self._config.label_for(app_id, app_id))
                 for app_id in pinned]
        self.show_reorder(
            anchor, 'Rearrange pinned apps', items,
            on_order_changed=self.apply_pinned_order)

    def show_home_reorder(self, anchor: Gtk.Widget) -> None:
        slots = self._config.home_slots
        if len(slots) < 2:
            self._on_status('Needs at least two apps to rearrange.')
            return
        items = [(str(i), str(s.get('label', '')))
                 for i, s in enumerate(slots)]
        self.show_reorder(
            anchor, 'Rearrange Home Screen', items,
            on_order_changed=self.apply_slot_order)

    def apply_member_order(self, slot_index: int, ids: list[str]) -> bool:
        """Persist a whole member permutation silently; Done refreshes."""
        slot = self._folder_slot(slot_index)
        if slot is None:
            return False
        members = slot.get('folder') or []
        try:
            order = [int(i) for i in ids]
        except (TypeError, ValueError):
            return False
        if sorted(order) != list(range(len(members))):
            return False
        slot['folder'] = [members[i] for i in order]
        self._config.set_home_slots(self._config.home_slots)
        return True

    def apply_slot_order(self, ids: list[str]) -> bool:
        """Persist a whole home-slot permutation silently; Done refreshes."""
        slots = self._config.home_slots
        try:
            order = [int(i) for i in ids]
        except (TypeError, ValueError):
            return False
        if sorted(order) != list(range(len(slots))):
            return False
        self._config.set_home_slots([slots[i] for i in order])
        return True

    def apply_pinned_order(self, ids: list[str]) -> bool:
        """Persist a whole pinned permutation silently; Done refreshes."""
        pinned = list(self._config.pinned)
        if sorted(ids) != sorted(pinned):
            return False
        self._config.set_pinned(list(ids))
        return True
