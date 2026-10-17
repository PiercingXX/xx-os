"""
Home screen launcher — slot list rendered from config.home_slots.

Folders drop open inline: tapping a folder slot inserts its members directly
under that row (same typography, no title or close chrome); the centered list
grows around them and the widget block stays visible. Tapping the folder
again, launching a member, or a re-render collapses it.
Android apps launch via waydroid when available.
"""
from __future__ import annotations

import subprocess
from typing import Callable

import os as _os

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gtk

from config import ThemePreset


def _pt(base: float, scale: float) -> str:
    return '%gpt' % round(base * scale, 1)


def theme_css(preset: ThemePreset, scale: float = 1.0) -> str:
    """Home sheet. Sizes scale with the launcher text size (hot-reloaded
    through window._apply_theme like the shared sheet's ${...} tokens)."""
    return f"""
.home-item-btn {{
    background: transparent;
    border: none;
    border-radius: 0;
    outline: none;
    padding: 20px 0;
}}
/* Slots never hold a highlight: hover, press and keyboard focus all stay
   on the plain background, so no grey band sticks to the focused slot. */
.home-item-btn:hover, .home-item-btn:active, .home-item-btn:focus {{
    background: transparent;
}}
.home-item-label {{
    font-size: {_pt(27, scale)};
    font-weight: 400;
    color: {preset.foreground};
}}
/* Empty slot "· · ·": the Android 40% alpha. */
.home-item-btn.empty-slot .home-item-label {{
    opacity: 0.4;
}}
.home-folder-indicator {{
    font-size: {_pt(14, scale)};
    font-weight: 400;
    color: {preset.muted};
    margin-left: 6px;
}}
.home-edit-label {{
    font-size: {_pt(18, scale)};
    font-weight: 300;
}}
.home-edit-ctl {{
    font-size: {_pt(13, scale)};
    font-weight: 300;
    padding: 4px 12px;
    min-width: 44px;
    min-height: 44px;
    background: transparent;
    border: none;
    color: {preset.muted};
}}
.home-edit-action {{
    font-size: {_pt(13, scale)};
    font-weight: 300;
    padding: 10px 0;
    min-height: 44px;
    background: transparent;
    border: none;
    color: {preset.muted};
}}
"""


_WAYDROID_ENV = {
    **_os.environ,
    # Honor the session we were launched into; wayland-0 is only the fallback.
    'WAYLAND_DISPLAY': _os.environ.get('WAYLAND_DISPLAY') or 'wayland-0',
    'XDG_RUNTIME_DIR': f'/run/user/{_os.getuid()}',
}


def _waydroid_session_running() -> bool:
    try:
        out = subprocess.check_output(['waydroid', 'status'], text=True, timeout=2,
                                      env=_WAYDROID_ENV, stderr=subprocess.DEVNULL)
        return 'Session:\tRUNNING' in out
    except Exception:
        return False


def _launch_android(pkg: str) -> None:
    """Start the session check off the GTK thread: `waydroid status` can
    block for the full 2 s timeout on every home tap while the container
    is down. The actual launches stay fire-and-forget Popen (never waited)."""
    import threading

    def _work() -> None:
        try:
            running = _waydroid_session_running()
        except Exception:
            return
        from gi.repository import GLib
        if not running:
            try:
                # Start the session in background; app comes up once ready
                subprocess.Popen(
                    ['waydroid', 'session', 'start'],
                    env=_WAYDROID_ENV, close_fds=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                return
            # Give the session a moment before launching the app
            GLib.timeout_add(5000, lambda p=pkg: _do_launch_android(p) or False)
            return
        GLib.idle_add(lambda p=pkg: _do_launch_android(p) or False)

    threading.Thread(target=_work, daemon=True, name='waydroid-launch').start()


def _do_launch_android(pkg: str) -> None:
    try:
        subprocess.Popen(
            ['waydroid', 'app', 'launch', pkg],
            env=_WAYDROID_ENV, close_fds=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass


def _launch_cmd(cmd: list[str]) -> None:
    try:
        subprocess.Popen(cmd, close_fds=True, env=_WAYDROID_ENV)
    except FileNotFoundError:
        pass


def folder_row_markup(label: str) -> str:
    """Folder slot text: the escaped name plus a smaller, dimmed chevron."""
    from gi.repository import GLib
    return (GLib.markup_escape_text(label)
            + ' <span size="52%" alpha="60%">›</span>')


_HOME_ACCESSIBLE_LABEL = 'Home'
_HOME_ACCESSIBLE_DESCRIPTION = 'Open app drawer. Open launcher settings.'


# An empty home slot (Android home_slot_empty / its a11y string).
EMPTY_SLOT_TEXT = '· · ·'
EMPTY_SLOT_ACCESSIBLE = 'Empty home slot %d, tap to choose an app'


def slot_accessible_label(label: str, index: int) -> str:
    """Screen-reader label for an app slot: `Phone, home slot 3`."""
    return f'{label}, home slot {index + 1}'


def folder_accessible_label(label: str, count: int) -> str:
    """Screen-reader label for a folder slot: `Folder Comms, 3 apps`."""
    try:
        total = int(count)
    except (TypeError, ValueError):
        total = 0
    noun = 'app' if total == 1 else 'apps'
    return f'Folder {label}, {total} {noun}'


def _set_accessible(widget: Gtk.Widget, label: str | None = None,
                    description: str | None = None) -> None:
    """GTK4 screen-reader text via Gtk.Accessible.update_property.
    Never raises: accessibility must not break the visible UI."""
    try:
        props, values = [], []
        if label is not None:
            props.append(Gtk.AccessibleProperty.LABEL)
            values.append(label)
        if description is not None:
            props.append(Gtk.AccessibleProperty.DESCRIPTION)
            values.append(description)
        if props:
            Gtk.Accessible.update_property(widget, props, values)
    except Exception:
        pass


class HomeLauncher(Gtk.Box):
    """Vertical list of home slots; folders drop open inline under their slot."""

    def __init__(self, open_dialer_fn: Callable[[], None] | None = None,
                 get_slots_fn: Callable[[], list[dict]] | None = None,
                 on_launch_slot: Callable[[dict], None] | None = None,
                 on_member_long_press: Callable[[Gtk.Widget, int, int], None] | None = None,
                 on_slot_move: Callable[[int, int], None] | None = None,
                 on_slot_remove: Callable[[int, Gtk.Widget], None] | None = None,
                 on_slot_rename: Callable[[Gtk.Widget, int], None] | None = None,
                 on_edit_action: Callable[[str, Gtk.Widget], None] | None = None,
                 is_paused_fn: Callable[[str], bool] | None = None,
                 get_alignment_fn: Callable[[], str] | None = None,
                 on_status_fn: Callable[[str], None] | None = None,
                 get_empty_count_fn: Callable[[], int] | None = None,
                 on_empty_tap: Callable[[], None] | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_vexpand(True)
        self._open_dialer = open_dialer_fn
        self._get_slots = get_slots_fn or (lambda: [])
        self._on_launch_slot = on_launch_slot or (lambda slot: None)
        self._on_member_long_press = on_member_long_press
        self._on_slot_move = on_slot_move or (lambda idx, delta: None)
        self._on_slot_remove = on_slot_remove or (lambda idx, _w: None)
        self._on_slot_rename = on_slot_rename or (lambda widget, idx: None)
        self._on_edit_action = on_edit_action or (lambda action, widget: None)
        self._is_paused = is_paused_fn or (lambda app_id: False)
        self._get_alignment = get_alignment_fn or (lambda: 'center')
        self._on_status = on_status_fn or (lambda message: None)
        self._get_empty_count = get_empty_count_fn or (lambda: 0)
        self._on_empty_tap = on_empty_tap or (lambda: None)
        self._open_folder_index: int | None = None
        self.edit_mode = False
        self._build()
        # Swipe gestures have no screen-reader equivalent; the home list
        # itself carries what opens the drawer and the launcher settings.
        _set_accessible(self, label=_HOME_ACCESSIBLE_LABEL,
                        description=_HOME_ACCESSIBLE_DESCRIPTION)

    def set_edit_mode(self, enabled: bool) -> None:
        if self.edit_mode == enabled:
            return
        self.edit_mode = enabled
        self._open_folder_index = None
        self._build()

    @property
    def folder_open(self) -> bool:
        return self._open_folder_index is not None

    def close_folder(self) -> bool:
        """Collapse the open folder drop-down. Returns True if one was open."""
        if self._open_folder_index is None:
            return False
        self._open_folder_index = None
        self._build()
        return True

    def refresh(self, preserve_folder: bool = False) -> None:
        if not preserve_folder:
            self._open_folder_index = None
        self._build()

    def _build(self) -> None:
        child = self.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.remove(child)
            child = nxt

        slots = self._get_slots()
        if self.edit_mode:
            self._build_edit_rows(slots)
            return

        open_idx = self._open_folder_index
        if open_idx is not None and not (
            0 <= open_idx < len(slots) and slots[open_idx].get('type') == 'folder'
        ):
            self._open_folder_index = open_idx = None

        for idx, slot in enumerate(slots):
            slot_type = slot.get('type')
            if slot_type == 'app':
                self.append(self._make_row(
                    slot.get('label', ''),
                    lambda s=slot: self._on_launch_slot(s),
                    paused=self._is_paused(str(slot.get('app_id') or '')),
                    accessible_label=slot_accessible_label(
                        slot.get('label', ''), idx)))
            elif slot_type == 'folder':
                self.append(self._make_folder_row(slot, idx))
                if idx == open_idx:
                    self.append(self._make_member_dropdown(slot))
        # Android "Home Slot Count" past the filled slots: dim "· · ·"
        # rows; a tap picks an app for the slot.
        try:
            empty = max(0, int(self._get_empty_count()))
        except Exception:
            empty = 0
        for offset in range(empty):
            row = self._make_row(
                EMPTY_SLOT_TEXT, lambda: self._on_empty_tap(),
                accessible_label=EMPTY_SLOT_ACCESSIBLE % (
                    len(slots) + offset + 1))
            row.add_css_class('empty-slot')
            self.append(row)

    def _build_edit_rows(self, slots: list[dict]) -> None:
        for idx, slot in enumerate(slots):
            self.append(self._make_edit_row(slot, idx, len(slots)))
        for action, label in self._edit_footer_actions(len(slots)):
            btn = Gtk.Button(label=label)
            btn.add_css_class('home-edit-action')
            btn.set_hexpand(True)
            btn.connect('clicked', lambda _b, a=action, w=btn: self._on_edit_action(a, w))
            self.append(btn)

    @staticmethod
    def _edit_footer_actions(slot_count: int) -> list[tuple[str, str]]:
        actions: list[tuple[str, str]] = []
        if slot_count < 8:
            actions.append(('add_app', '+ Add app'))
            actions.append(('new_folder', '+ New folder'))
        if slot_count >= 2:
            actions.append(('rearrange', 'Rearrange…'))
        actions.append(('settings', 'Settings'))
        actions.append(('done', 'Done'))
        return actions

    def _make_edit_row(self, slot: dict, idx: int, total: int) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        row.set_halign(Gtk.Align.CENTER)

        label_text = slot.get('label', '')
        if slot.get('type') == 'folder':
            label_text += ' ›'
        from gi.repository import Pango
        name_btn = Gtk.Button(label=label_text)
        name_btn.add_css_class('home-edit-ctl')
        name_btn.add_css_class('home-edit-label')
        child = name_btn.get_child()
        if isinstance(child, Gtk.Label):
            child.set_ellipsize(Pango.EllipsizeMode.END)
            child.set_max_width_chars(16)
        name_btn.connect('clicked', lambda _b, w=name_btn, i=idx: self._on_slot_rename(w, i))
        row.append(name_btn)

        for symbol, delta in (('↑', -1), ('↓', +1)):
            ctl = Gtk.Button(label=symbol)
            ctl.add_css_class('home-edit-ctl')
            ctl.set_sensitive(0 <= idx + delta < total)
            ctl.connect('clicked', lambda _b, i=idx, d=delta: self._on_slot_move(i, d))
            row.append(ctl)

        remove = Gtk.Button(label='✕')
        remove.add_css_class('home-edit-ctl')
        remove.connect('clicked', lambda _b, i=idx, w=remove: self._on_slot_remove(i, w))
        row.append(remove)
        return row

    def _make_member_dropdown(self, slot: dict) -> Gtk.Widget:
        slot_index = self._open_folder_index
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        for m_idx, member in enumerate(slot.get('folder', [])):
            row = self._make_row(member.get('label', ''),
                                 lambda m=member: self._tap_member(m),
                                 paused=self._is_paused(str(member.get('app_id') or '')))
            if self._on_member_long_press is not None:
                def _on_long_press(gesture: Gtk.GestureLongPress, _x: float, _y: float,
                                   w: Gtk.Widget = row, s: int = slot_index, m: int = m_idx) -> None:
                    gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                    self._on_member_long_press(w, s, m)

                long_press = Gtk.GestureLongPress.new()
                long_press.set_touch_only(False)
                long_press.connect('pressed', _on_long_press)
                row.add_controller(long_press)
            box.append(row)
        revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=120,
        )
        revealer.set_child(box)
        GLib.idle_add(self._reveal_once, revealer)
        return revealer

    @staticmethod
    def _reveal_once(revealer: Gtk.Revealer) -> bool:
        revealer.set_reveal_child(True)
        return GLib.SOURCE_REMOVE

    def _row_halign(self) -> Gtk.Align:
        return {'left': Gtk.Align.START, 'right': Gtk.Align.END}.get(
            self._get_alignment(), Gtk.Align.CENTER)

    def _row_xalign(self) -> float:
        return {'left': 0.0, 'right': 1.0}.get(self._get_alignment(), 0.5)

    def _make_row(self, label: str, on_tap: Callable[[], None],
                  paused: bool = False,
                  accessible_label: str | None = None) -> Gtk.Button:
        from gi.repository import Pango
        lbl = Gtk.Label(label=label + (' · paused' if paused else ''))
        # Fill the row and align the text, rather than centering a box sized
        # to the label's natural width: at fractional scale that width rounds
        # a pixel short and the label ellipsizes ("Calenda…") on a wide row.
        lbl.set_halign(Gtk.Align.FILL)
        lbl.set_xalign(self._row_xalign())
        lbl.set_hexpand(True)
        # 27pt names like "Mozilla Firefox" would otherwise force the page
        # wider than the phone screen.
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        lbl.add_css_class('home-item-label')

        btn = Gtk.Button()
        btn.add_css_class('home-item-btn')
        if paused:
            btn.add_css_class('paused')
        btn.set_hexpand(True)
        btn.set_child(lbl)
        btn.connect('clicked', lambda _b: on_tap())
        if accessible_label is not None:
            _set_accessible(btn, label=accessible_label)
        return btn

    def _make_folder_row(self, slot: dict, idx: int) -> Gtk.Button:
        from gi.repository import Pango
        # One label (name + dimmed chevron as markup) filling the row, for
        # the same rounding reason as _make_row.
        row = Gtk.Label()
        row.set_markup(folder_row_markup(slot.get('label', '')))
        row.add_css_class('home-item-label')
        row.set_halign(Gtk.Align.FILL)
        row.set_xalign(self._row_xalign())
        row.set_hexpand(True)
        row.set_ellipsize(Pango.EllipsizeMode.END)

        btn = Gtk.Button()
        btn.add_css_class('home-item-btn')
        btn.set_hexpand(True)
        btn.set_child(row)
        btn.connect('clicked', lambda _b, i=idx, s=slot: self._toggle_folder(i, s))
        _set_accessible(btn, label=folder_accessible_label(
            slot.get('label', ''), len(slot.get('folder') or [])))
        return btn

    def _toggle_folder(self, idx: int, slot: dict) -> None:
        if self._open_folder_index == idx:
            self._open_folder_index = None
        elif slot.get('folder'):
            self._open_folder_index = idx
        else:
            self._on_status('Folder is empty')
            return
        self._build()

    def _tap_member(self, member: dict) -> None:
        self.close_folder()
        self._on_launch_slot({'type': 'app', **member})
