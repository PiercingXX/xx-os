from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app_index import AppEntry

from app_index import fold_for_search

_WEB_SEARCH_URL = 'https://duckduckgo.com/?q='

# Drawer sheet geometry (design.md "App drawer"): the top fraction stays
# an open transparent gap so the drawer reads as a sheet; tapping it
# closes the drawer.
SHEET_GAP_FRACTION = 0.12

# The Android launcher's sort modes (config.DRAWER_SORTS), in its order;
# the drawer's sort toggle cycles them with these short labels.
_DRAWER_SORT_MODES = ('default', 'alphabetical', 'alphabetical_no_priority',
                      'install_date', 'apk_size')
_DRAWER_SORT_LABELS = {
    'default': 'Default',
    'alphabetical': 'A-Z',
    'alphabetical_no_priority': 'A-Z, no pins',
    'install_date': 'Install date',
    'apk_size': 'Size',
}
_LEGACY_SORTS = {'az': 'alphabetical_no_priority', 'install': 'install_date'}

_SETTINGS_ROW_LABEL = 'launcher settings'

# An install counts as new for the row marker while under an hour old.
_NEW_APP_SECONDS = 3600.0


def next_drawer_sort(mode: str) -> str:
    """Next sort mode for the toggle button, in the Android order."""
    mode = _LEGACY_SORTS.get(mode, mode)
    if mode not in _DRAWER_SORT_MODES:
        return 'default'
    return _DRAWER_SORT_MODES[
        (_DRAWER_SORT_MODES.index(mode) + 1) % len(_DRAWER_SORT_MODES)]


def drawer_sort_label(mode: str) -> str:
    """Toggle button label for a sort mode."""
    mode = _LEGACY_SORTS.get(mode, mode)
    return _DRAWER_SORT_LABELS.get(mode, _DRAWER_SORT_LABELS['default'])


def drawer_sort_of(win) -> str:
    """Effective drawer sort mode: the persisted config, with the legacy
    in-memory `_sort_mode` (bare test doubles) as the fallback."""
    mode = getattr(win, '_sort_mode', None)
    try:
        mode = win.config.drawer_sort
    except Exception:
        pass
    mode = _LEGACY_SORTS.get(mode, mode)
    return mode if mode in _DRAWER_SORT_MODES else 'default'


def sort_browse_list(entries: list, mode: str, pinned: list,
                     install_time=None, size_of=None) -> list:
    """The Android drawer sort (AppDrawerActivity.sortApps), pure.

    The base order is A–Z (as the index returns it), newest install
    first, or largest first; pinned apps then float to the top — in
    their manual order for 'default', in the base order for the
    others — except for 'alphabetical_no_priority'."""
    mode = _LEGACY_SORTS.get(mode, mode)
    base = list(entries)
    if mode == 'install_date' and install_time is not None:
        base = sorted(base, key=install_time, reverse=True)
    elif mode == 'apk_size' and size_of is not None:
        base = sorted(base, key=size_of, reverse=True)
    if mode == 'alphabetical_no_priority':
        return base
    order = {app_id: i for i, app_id in enumerate(pinned)}
    pinned_rows = [e for e in base if e.app_id in order]
    if mode == 'default':
        pinned_rows.sort(key=lambda e: order[e.app_id])
    return pinned_rows + [e for e in base if e.app_id not in order]


def settings_row_visible(query: str) -> bool:
    """The Launcher Settings row matches like Android: the settings label
    contains the query (fold-insensitive), or there is no query."""
    trimmed = (query or '').strip().casefold()
    if not trimmed:
        return True
    folded = fold_for_search(trimmed)
    return (trimmed in _SETTINGS_ROW_LABEL
            or bool(folded) and folded in fold_for_search(_SETTINGS_ROW_LABEL))


def web_search_row_query(raw_query: str, result_count: int) -> str | None:
    """Web-search row query, or None when no row shows: a `!` query always
    shows it (past the bare `!`), otherwise only with zero app matches."""
    text = raw_query or ''
    if text.startswith('!'):
        return text[1:].strip() or None
    if result_count == 0 and text.strip():
        return text.strip()
    return None


def should_auto_launch(raw_text: str, result_count: int,
                       settings_visible: bool, enabled: bool) -> bool:
    """Single-result auto-launch gate, mirroring Android: a leading space
    suppresses it, a matching Launcher Settings row suppresses it, and a
    `!` web query never fires it."""
    if not enabled or result_count != 1 or settings_visible:
        return False
    text = raw_text or ''
    if text.startswith(' '):
        return False
    stripped = text.strip()
    return bool(stripped) and not stripped.startswith('!')


def app_is_new(entry: AppEntry, now: float | None = None) -> bool:
    """True when the .desktop install mtime is under an hour old."""
    if now is None:
        import time
        now = time.time()
    try:
        filename = entry.desktop_app.get_filename()
        mtime = Path(filename).stat().st_mtime if filename else 0.0
    except OSError:
        return False
    return (now - mtime) < _NEW_APP_SECONDS


def row_title(name: str, pinned: bool, is_new: bool,
              paused: bool = False, limited: bool = False) -> str:
    """Drawer row text: `⊙` marks pinned apps, `•` newly installed ones."""
    title = name
    if pinned:
        title += '  ⊙'
    if is_new:
        title += '  •'
    if paused:
        title += ' · paused'
    if limited:
        title += ' · time limit'
    return title


def accessible_label(name: str, pinned: bool = False, is_new: bool = False,
                     paused: bool = False, limited: bool = False) -> str:
    """Spoken row label carrying the same facts as the markers."""
    parts = [name]
    if pinned:
        parts.append('pinned')
    if is_new:
        parts.append('new app')
    if paused:
        parts.append('paused')
    if limited:
        parts.append('time limit')
    return ', '.join(parts)


def set_row_accessible_label(row, text: str) -> None:
    """Set a list row's accessible label; never fails the row build."""
    try:
        from gi.repository import GObject, Gtk
        value = GObject.Value()
        value.init(GObject.TYPE_STRING)
        value.set_string(text)
        row.update_property([Gtk.AccessibleProperty.LABEL], [value])
    except Exception:
        pass


def decide_drawer_swipe_down(top_edge_origin: bool,
                             scrolled_to_top: bool) -> str:
    """Drawer swipe-down destination: the top edge always keeps the shade;
    otherwise a list parked at the top closes the drawer (goes home)."""
    if top_edge_origin or not scrolled_to_top:
        return 'shade'
    return 'home'


def drawer_list_at_top(win) -> bool:
    """True when the drawer list is scrolled to the top."""
    try:
        return float(
            win.apps_scroller.get_vadjustment().get_value()) <= 0.0
    except Exception:
        return False


def gap_tap_closes(y: float, height: float,
                   fraction: float = SHEET_GAP_FRACTION) -> bool:
    """True when a tap landing at height `y` is inside the transparent gap
    above the drawer sheet."""
    try:
        return float(height) > 0 and float(y) < float(height) * fraction
    except (TypeError, ValueError):
        return False


def drawer_folder_title(label: str, member_count: int) -> str:
    """Android-style folder row text: the name plus its member count."""
    return f'{label} ({member_count})'


def drawer_xalign(win) -> float:
    """Row text alignment from the home alignment pref (design.md
    "Alignment"): drawer, folder and member rows follow home."""
    try:
        alignment = win.config.home_alignment
    except AttributeError:
        try:
            alignment = win.config.data.get('home_alignment', 'center')
        except AttributeError:
            return 0.5
    return {'left': 0.0, 'right': 1.0}.get(alignment, 0.5)


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


class DrawerPage:
    """App drawer builder and list behavior for ShellWindow.

    Holds only a reference to the window; all state (list, search,
    sort mode, folder expansion) stays on the window so bare-instance
    headless tests keep working. Method names mirror the ShellWindow
    delegators in window.py.
    """

    def __init__(self, win) -> None:
        self._win = win

    def _build_apps_page(self):
        from gi.repository import Gtk

        win = self._win
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_vexpand(True)
        # The list sits on the plain background, like the PiercingXX
        # Android launcher — never an inset grey panel.
        outer.add_css_class('drawer-sheet')

        win._drawer_open_hidden = False

        # No 'All apps' header or count — the drawer is just the list, like
        # the PiercingXX Android launcher; the sort toggle rides alone, dim
        win._sort_btn = Gtk.Button(label=drawer_sort_label(drawer_sort_of(win)))
        win._sort_btn.add_css_class('flat')
        win._sort_btn.add_css_class('dim-label')
        win._sort_btn.set_halign(Gtk.Align.END)
        win._sort_btn.connect('clicked', win._toggle_sort_mode)

        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_row.set_margin_end(20)
        header_row.set_margin_top(8)
        header_row.append(Gtk.Box(hexpand=True))
        header_row.append(win._sort_btn)

        win.apps_search.set_margin_start(20)
        win.apps_search.set_margin_end(20)
        win.apps_search.set_margin_bottom(12)

        win.apps_scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        win.apps_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        win.apps_scroller.set_child(win.apps_list)

        # A-Z jump strip on the right edge: every letter a real ≥ 44 px
        # touch target in its own narrow scroller, so 27 letters never push
        # the search field off the phone screen.
        win._alpha_letter_rows = {}
        alpha_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        alpha_box.set_valign(Gtk.Align.CENTER)
        alpha_box.set_margin_end(4)

        for ch in '#ABCDEFGHIJKLMNOPQRSTUVWXYZ':
            btn = Gtk.Button(label=ch)
            btn.add_css_class('flat')
            btn.add_css_class('dim-label')
            btn.add_css_class('alpha-jump')
            btn.connect('clicked', win._on_alpha_jump, ch)
            alpha_box.append(btn)

        win._alpha_scroller = Gtk.ScrolledWindow()
        win._alpha_scroller.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # Fills the list height and scrolls the 44 px targets inside it:
        # minimum height stays the search field's, never 27 tall buttons.
        win._alpha_scroller.set_vexpand(True)
        win._alpha_scroller.set_valign(Gtk.Align.FILL)
        win._alpha_scroller.set_child(alpha_box)

        list_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        list_row.append(win.apps_scroller)
        list_row.append(win._alpha_scroller)

        # Search lives at the bottom where the thumb is; keyboard only on tap.
        # The list row expands and shrinks; header and search keep their
        # minimum so search is never pushed off-screen.
        list_row.set_vexpand(True)
        outer.append(header_row)
        outer.append(list_row)
        outer.append(win.apps_search)

        # The drawer reads as a sheet: the top fraction stays an open
        # transparent gap (tapping it closes the drawer). The layout manager
        # keeps the page minimum at the content's own (a fixed spacer
        # widget would bake reserved space into it and push search off).
        class _SheetTopGapLayout(Gtk.LayoutManager):
            _GAP_FRACTION = SHEET_GAP_FRACTION

            def do_measure(self, widget, orientation, for_size):
                child = widget.get_first_child()
                if child is None:
                    return (0, 0, -1, -1)
                return child.measure(orientation, for_size)

            def do_allocate(self, widget, width, height, baseline):
                from gi.repository import Gsk, Graphene
                child = widget.get_first_child()
                if child is None:
                    return
                gap = int(height * self._GAP_FRACTION)
                point = Graphene.Point()
                point.x = 0.0
                point.y = float(gap)
                child.allocate(width, max(0, height - gap), baseline,
                               Gsk.Transform.new().translate(point))

        sheet = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sheet.set_vexpand(True)
        sheet.set_layout_manager(_SheetTopGapLayout())
        sheet.append(outer)
        win._drawer_sheet = sheet
        gap_tap = Gtk.GestureClick.new()
        gap_tap.set_touch_only(False)
        gap_tap.connect('pressed', self._on_drawer_gap_tap)
        sheet.add_controller(gap_tap)
        return sheet

    def _on_drawer_gap_tap(self, _gesture, _n_press: int,
                           _x: float, y: float) -> None:
        """A tap inside the transparent gap above the sheet closes the
        drawer; taps on the sheet itself carry a larger y and fall through."""
        win = self._win
        sheet = getattr(win, '_drawer_sheet', None)
        try:
            height = float(sheet.get_height())
        except Exception:
            return
        if gap_tap_closes(y, height):
            win.go_home()

    def _toggle_sort_mode(self, _btn) -> None:
        win = self._win
        nxt = next_drawer_sort(drawer_sort_of(win))
        try:
            win.config.set_drawer_sort(nxt)
        except Exception:
            win._sort_mode = nxt
        try:
            win._sort_btn.set_label(drawer_sort_label(nxt))
        except Exception:
            pass
        win._populate_apps(win.apps_search.get_text())

    def _home_slot_app_ids(self) -> set[str]:
        win = self._win
        ids: set[str] = set()
        # Apps on slots past the Home Slot Count are not on screen, so
        # the drawer lists them (Android hides visible slots only).
        visible = getattr(win.config, 'visible_home_slots', None)
        slots = visible() if callable(visible) else win.config.home_slots
        for slot in slots:
            if slot.get('app_id'):
                ids.add(str(slot['app_id']))
            for member in slot.get('folder') or []:
                if member.get('app_id'):
                    ids.add(str(member['app_id']))
        return ids

    def _drawer_folder_slots(self) -> list[tuple[int, dict, list[tuple[int, dict]]]]:
        """Folder slots the drawer shows, in home-slot order (folders first).

        Members keep their original folder-list indices so the shared member
        menu edits the right entry; members whose app is not installed are
        skipped at render, and empty folders never appear.
        """
        win = self._win
        installed = {entry.app_id for entry in win.app_index.entries}
        out: list[tuple[int, dict, list[tuple[int, dict]]]] = []
        for idx, slot in enumerate(win.config.home_slots):
            if slot.get('type') != 'folder':
                continue
            members = [
                (m_idx, m) for m_idx, m in enumerate(slot.get('folder') or [])
                if m.get('app_id') and str(m['app_id']) in installed
            ]
            if members:
                out.append((idx, slot, members))
        return out

    def _size_lookup(self):
        """Installed-size key for the "APK size" sort. Sizes are looked
        up off the main loop; the list re-sorts when they arrive."""
        win = self._win
        cache = getattr(win, '_app_sizes', None)
        if cache is None:
            from app_size import SizeCache
            cache = win._app_sizes = SizeCache()
        try:
            entries = list(win.app_index.entries)
        except Exception:
            entries = []
        cache.ensure(entries, lambda: win._populate_apps(
            win.apps_search.get_text()))
        return lambda entry: cache.size(entry.app_id)

    @staticmethod
    def _install_time(entry: AppEntry) -> float:
        try:
            filename = entry.desktop_app.get_filename()
            return Path(filename).stat().st_mtime if filename else 0.0
        except OSError:
            return 0.0

    def _on_alpha_jump(self, _btn, letter: str) -> None:
        win = self._win
        idx = win._alpha_letter_rows.get(letter)
        if idx is None and letter == '#':
            idx = 0
        if idx is None:
            return
        row = win.apps_list.get_row_at_index(idx)
        if row is None:
            return
        alloc = row.get_allocation()
        adj = win.apps_scroller.get_vadjustment()
        adj.set_value(max(0.0, alloc.y))

    def _populate_apps(self, query: str = '') -> None:
        from gi.repository import GLib, Gtk

        win = self._win
        raw = query or ''
        trimmed = raw.strip().casefold()
        folded = fold_for_search(trimmed)
        labels = win.config.app_labels
        bang = trimmed.startswith('!')
        sort_mode = drawer_sort_of(win)

        if bang:
            # `!query` skips app matching entirely and offers only the web row
            results = []
        else:
            results = win.app_index.search(raw)

        if trimmed and not bang:
            # Search surfaces everything: hidden apps, home-slot apps, folder
            # members — plus fold-insensitive matches against renamed labels.
            matched = {e.app_id for e in results}
            renamed_hits = [
                e for e in win.app_index.entries
                if e.app_id not in matched and (
                    trimmed in labels.get(e.app_id, '').casefold()
                    or bool(folded) and folded in fold_for_search(
                        labels.get(e.app_id, '')))
            ]
            results = results + renamed_hits
        elif not trimmed:
            # The browse list hides hidden apps and everything already on
            # home — except pinned apps, which stay listed (Android rule).
            pinned_ids = set(win.config.pinned or ())
            home_ids = set(win._home_slot_app_ids()) - pinned_ids
            browse_hidden = set(win.config.hidden_apps) | home_ids
            results = [e for e in results if e.app_id not in browse_hidden]
            results = sort_browse_list(
                results, sort_mode, list(win.config.pinned or ()),
                install_time=win._install_time,
                size_of=(self._size_lookup() if sort_mode == 'apk_size'
                         else None))

        win._last_search_results = results if trimmed else []

        # Home folders stay on home — the drawer only lists apps that are
        # not already a slot or folder member (they remain searchable)
        folder_slots = win._drawer_folder_slots()
        if (win._drawer_open_folder is not None
                and win._drawer_open_folder not in {i for i, _s, _m in folder_slots}):
            win._drawer_open_folder = None

        # Hidden apps get their own expandable group in the browse list so
        # they stay reachable without typing (search still surfaces them).
        hidden_entries: list = []
        if not trimmed:
            hidden_ids = set(win.config.hidden_apps)
            hidden_entries = [e for e in win.app_index.entries
                              if e.app_id in hidden_ids]
        hidden_open = bool(hidden_entries) and bool(
            getattr(win, '_drawer_open_hidden', False))

        expanded_members = 0
        for i, _slot, members in folder_slots:
            if i == win._drawer_open_folder:
                expanded_members = len(members)
        row_offset = (len(folder_slots) + expanded_members
                      + (1 if hidden_entries else 0)
                      + (len(hidden_entries) if hidden_open else 0))

        # Build letter→first-row-index map for A-Z jump strip
        win._alpha_letter_rows = {}
        for idx, entry in enumerate(results):
            display = labels.get(entry.app_id, entry.name)
            first = display[0].upper() if display else '#'
            letter = first if first.isalpha() else '#'
            if letter not in win._alpha_letter_rows:
                win._alpha_letter_rows[letter] = idx + row_offset

        win._replace_rows(win.apps_list, results, 'No apps matched this search.', win._make_app_row)

        # Folder drop-downs sit above the app rows (design.md "App drawer")
        win._drawer_folder_header = None
        insert_at = 0
        for idx, slot, members in folder_slots:
            header = win._make_drawer_folder_row(slot, idx)
            if idx == win._drawer_open_folder:
                win._drawer_folder_header = header
            win.apps_list.insert(header, insert_at)
            insert_at += 1
            if idx == win._drawer_open_folder:
                for m_idx, member in members:
                    win.apps_list.insert(
                        win._make_drawer_member_row(idx, m_idx, member), insert_at)
                    insert_at += 1
        if hidden_entries:
            win.apps_list.insert(win._make_hidden_header(len(hidden_entries)), insert_at)
            insert_at += 1
            if hidden_open:
                for entry in hidden_entries:
                    win.apps_list.insert(win._make_app_row(entry), insert_at)
                    insert_at += 1

        # Synthetic "Launcher Settings" entry always sits at the very end
        settings_visible = settings_row_visible(raw) and not bang
        if settings_visible:
            win.apps_list.append(win._make_settings_row())

        # A visible "Web: <query>" row ends the results when nothing
        # matches, or for a `!` web query; tapping it web-searches like Enter
        web_query = web_search_row_query(raw, len(results))
        if web_query:
            win.apps_list.append(win._make_web_row(web_query))

        # Search results anchor at the bottom, above the search field, within
        # thumb reach; a set that overflows the view still reads from the top.
        win.apps_list.set_valign(Gtk.Align.END if trimmed else Gtk.Align.FILL)
        if trimmed:
            GLib.idle_add(win._scroll_apps_to_top)

        if trimmed:
            win.app_count_label.set_text(f'{len(results)} matches')
        else:
            win.app_count_label.set_text(f'{len(results)} apps indexed')

    def _scroll_apps_to_top(self) -> bool:
        from gi.repository import GLib

        win = self._win
        win.apps_scroller.get_vadjustment().set_value(0.0)
        return GLib.SOURCE_REMOVE

    def _make_drawer_folder_row(self, slot: dict, slot_index: int):
        from gi.repository import Gtk, Pango

        win = self._win
        members = next((m for i, _s, m in win._drawer_folder_slots()
                        if i == slot_index), [])
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        # Home-alignment rows; long folder names ellipsize
        # instead of forcing the page wider than the phone screen.
        title = Gtk.Label(
            label=drawer_folder_title(slot.get('label', ''), len(members)),
            xalign=drawer_xalign(win))
        title.add_css_class('app-name')
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_hexpand(True)
        indicator = Gtk.Label(label='⌄' if slot_index == win._drawer_open_folder else '›')
        indicator.add_css_class('dim-label')
        inner.append(title)
        inner.append(indicator)
        inner.set_hexpand(True)

        btn = Gtk.Button()
        btn.add_css_class('flat')
        btn.add_css_class('app-entry')
        btn.set_child(inner)
        btn.connect('clicked', lambda _b, i=slot_index: win._toggle_drawer_folder(i))
        self._add_long_press(
            btn, lambda w: win.item_actions.show_folder_menu(w, slot_index))

        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.set_child(btn)
        return row

    def _make_drawer_member_row(self, slot_index: int, member_index: int, member: dict):
        from gi.repository import Gtk, Pango

        win = self._win
        label = member.get('label', '')
        paused = win.focus_state.is_paused_app(str(member.get('app_id') or ''))
        from settings_screentime import is_limited_today
        limited = is_limited_today(win, str(member.get('app_id') or ''))
        if paused:
            suffix = ' · paused'
        elif limited:
            suffix = ' · time limit'
        else:
            suffix = ''
        # Home-alignment rows like folder and app rows; the indented
        # button frame (folder-member margin) is what sets members apart.
        title = Gtk.Label(label=label + suffix, xalign=drawer_xalign(win))
        title.add_css_class('app-name')
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_hexpand(True)

        btn = Gtk.Button()
        btn.add_css_class('flat')
        btn.add_css_class('app-entry')
        btn.add_css_class('folder-member')
        if paused or limited:
            btn.add_css_class('paused')
        btn.set_child(title)

        def _launch(_b) -> None:
            win._drawer_open_folder = None
            win._populate_apps(win.apps_search.get_text())
            win._launch_slot({'type': 'app', **member})

        btn.connect('clicked', _launch)
        win._add_long_press(
            btn, lambda w: win.item_actions.show_member_menu(w, slot_index, member_index))

        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.set_child(btn)
        return row

    def _make_hidden_header(self, count: int):
        from gi.repository import Gtk, Pango

        win = self._win
        open_now = bool(getattr(win, '_drawer_open_hidden', False))
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title = Gtk.Label(label=f'Hidden ({count})', xalign=drawer_xalign(win))
        title.add_css_class('app-name')
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_hexpand(True)
        indicator = Gtk.Label(label='⌄' if open_now else '›')
        indicator.add_css_class('dim-label')
        inner.append(title)
        inner.append(indicator)
        inner.set_hexpand(True)

        btn = Gtk.Button()
        btn.add_css_class('flat')
        btn.add_css_class('app-entry')
        btn.set_child(inner)
        btn.connect('clicked', lambda _b: win._toggle_hidden())

        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.set_child(btn)
        return row

    def _toggle_hidden(self) -> None:
        win = self._win
        win._drawer_open_hidden = not getattr(win, '_drawer_open_hidden', False)
        win._populate_apps(win.apps_search.get_text())

    def _toggle_drawer_folder(self, slot_index: int) -> None:
        from gi.repository import GLib

        win = self._win
        opening = win._drawer_open_folder != slot_index
        win._drawer_open_folder = slot_index if opening else None
        win._populate_apps(win.apps_search.get_text())
        if opening:
            GLib.idle_add(win._center_drawer_folder)

    def _center_drawer_folder(self) -> bool:
        from gi.repository import GLib

        win = self._win
        # Scroll so the folder row plus its drop-down sit vertically centered
        header = win._drawer_folder_header
        if header is None or win._drawer_open_folder is None:
            return GLib.SOURCE_REMOVE
        row_h = header.get_height()
        if row_h <= 0:
            return GLib.SOURCE_CONTINUE
        # Size from rendered members only: uninstalled members are skipped
        # at render, so counting the raw list would overshoot the block.
        members = next((m for i, _s, m in win._drawer_folder_slots()
                        if i == win._drawer_open_folder), [])
        block_h = row_h * (1 + len(members))
        view_h = win.apps_scroller.get_height()
        alloc = header.get_allocation()
        block_h = min(block_h, view_h)
        target = alloc.y - max(0, (view_h - block_h) // 2)
        adj = win.apps_scroller.get_vadjustment()
        adj.set_value(max(0.0, float(target)))
        return GLib.SOURCE_REMOVE

    def _add_long_press(self, widget, callback) -> None:
        from gi.repository import Gtk

        def _on_long_press(gesture, _x: float, _y: float) -> None:
            # Claim the sequence so releasing doesn't also fire the row's click
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            callback(widget)

        long_press = Gtk.GestureLongPress.new()
        long_press.set_touch_only(False)
        long_press.connect('pressed', _on_long_press)
        widget.add_controller(long_press)
        right_click = Gtk.GestureClick.new()
        right_click.set_button(3)
        right_click.connect('pressed', lambda _g, _n, _x, _y, w=widget: callback(w))
        widget.add_controller(right_click)

    def _make_settings_row(self):
        from gi.repository import Gtk, Pango

        win = self._win
        title = Gtk.Label(label='Launcher Settings', xalign=drawer_xalign(win))
        title.add_css_class('app-name')
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_hexpand(True)

        btn = Gtk.Button()
        btn.add_css_class('flat')
        btn.add_css_class('app-entry')
        btn.set_child(title)
        btn.connect('clicked', lambda _b: _open_settings(win))

        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.set_child(btn)
        return row

    def _replace_rows(
        self,
        list_box,
        entries: list[AppEntry],
        empty_message: str,
        row_maker: object = None,
    ) -> None:
        from gi.repository import Gtk

        win = self._win
        child = list_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            list_box.remove(child)
            child = next_child

        if not entries:
            label = Gtk.Label(label=empty_message, xalign=drawer_xalign(win))
            label.add_css_class('dim-label')
            row = Gtk.ListBoxRow(selectable=False, activatable=False)
            row.set_child(label)
            list_box.append(row)
            return

        maker = row_maker if callable(row_maker) else win._make_app_row
        for entry in entries:
            list_box.append(maker(entry))

    def _make_app_row(self, entry: AppEntry):
        from gi.repository import Gtk, Pango

        win = self._win
        base_name = win.config.label_for(entry.app_id, entry.name)
        pinned = entry.app_id in (win.config.pinned or ())
        new = app_is_new(entry)
        paused = win.focus_state.is_paused_app(entry.app_id)
        from settings_screentime import is_limited_today
        limited = is_limited_today(win, entry.app_id)
        # Single-line rows following the home alignment, name only — matches the PiercingXX
        # Android launcher's drawer; descriptions were noise at phone size
        title = Gtk.Label(
            label=row_title(base_name, pinned, new,
                            paused=paused, limited=limited),
            xalign=drawer_xalign(win))
        title.add_css_class('app-name')
        title.set_ellipsize(Pango.EllipsizeMode.END)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(title)
        content.set_hexpand(True)

        launch_btn = Gtk.Button()
        launch_btn.add_css_class('flat')
        launch_btn.add_css_class('app-entry')
        if paused or limited:
            launch_btn.add_css_class('paused')
        launch_btn.set_child(content)
        launch_btn.connect('clicked', lambda _b, e=entry: win._launch_entry(e))
        win._add_long_press(
            launch_btn, lambda w, e=entry: win.item_actions.show_app_menu(w, e))

        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.set_child(launch_btn)
        set_row_accessible_label(
            row, accessible_label(base_name, pinned=pinned, is_new=new,
                                  paused=paused, limited=limited))
        return row

    def _make_web_row(self, query: str):
        from gi.repository import Gtk, Pango

        win = self._win
        title = Gtk.Label(label='Web: ' + query, xalign=0.5)
        title.add_css_class('app-name')
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_hexpand(True)

        btn = Gtk.Button()
        btn.add_css_class('flat')
        btn.add_css_class('app-entry')
        btn.set_child(title)
        btn.connect('clicked', lambda _b: win._open_web_search(query))

        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.set_child(btn)
        set_row_accessible_label(row, 'Web search for ' + query)
        return row

    def _launch_entry(self, entry: AppEntry) -> None:
        from settings_page import _GESTURE_TITLES

        win = self._win
        if win._pick_slot_mode:
            win._pick_slot_mode = False
            target = getattr(win, '_pick_slot_target', None)
            win._pick_slot_target = None
            if target is not None:
                win._replace_app_slot(target, entry)
            else:
                win._add_app_slot(entry)
            return
        if win._pick_gesture_key:
            key = win._pick_gesture_key
            win._pick_gesture_key = None
            win.gesture_config.set(key, f'launch:{entry.app_id}')
            win._refresh_gesture_labels()
            _open_settings(win)
            name = win.config.label_for(entry.app_id, entry.name)
            title = _GESTURE_TITLES.get(key, key.replace('_', ' '))
            win._show_status(f'{name} bound to {title}.')
            return
        if getattr(win, '_pick_widget_tap', None):
            key = win._pick_widget_tap
            win._pick_widget_tap = None
            win.config.set_widget_tap(key, {'app': entry.app_id})
            _open_settings(win)
            name = win.config.label_for(entry.app_id, entry.name)
            win._show_status(f'{name} opens on {key} tap.')
            return
        if getattr(win, '_pick_exempt_mode', False):
            win._pick_exempt_mode = False
            from app_item_actions import add_standby_exempt
            add_standby_exempt(win.config, entry.app_id)
            _open_settings(win)
            win._show_status(
                f'{win.config.label_for(entry.app_id, entry.name)} '
                'will never freeze.')
            return
        if win.focus_state.is_paused_app(entry.app_id):
            win._show_focus_notice(win.config.label_for(entry.app_id, entry.name))
            return
        ok, error = win.app_index.launch(
            entry, tracker=getattr(win, 'standby', None))
        if ok:
            win.config.record_launch(entry.app_id)
            # The app surfacing above the shell is the feedback; a toast
            # per launch was noise over every drawer tap.
            win.drop_to_background()
        else:
            win._show_status(f'Failed to launch {entry.name}: {error}')

    def _on_apps_search_changed(self, entry) -> None:
        win = self._win
        text = entry.get_text()
        win._populate_apps(text)
        # Auto-launch the single result while typing, when enabled: a
        # leading space suppresses it, a matching settings row keeps the
        # list on screen, and a `!` web query never fires it.
        if should_auto_launch(text, len(win._last_search_results),
                               settings_row_visible(text),
                               bool(win.config.search_auto_launch)):
            entry.set_text('')
            win._launch_entry(win._last_search_results[0])

    def _on_apps_search_activate(self, entry) -> None:
        win = self._win
        query = entry.get_text().strip()
        if not query:
            return
        # `!query` → web search; a query with zero app matches falls back too
        if query.startswith('!'):
            win._open_web_search(query[1:].strip())
            entry.set_text('')
            return
        results = win._last_search_results
        if results:
            entry.set_text('')
            win._launch_entry(results[0])
        else:
            win._open_web_search(query)
            entry.set_text('')

    def _open_web_search(self, query: str) -> None:
        from gi.repository import Gio, GLib
        from urllib.parse import quote_plus

        win = self._win
        if not query:
            return
        try:
            Gio.AppInfo.launch_default_for_uri(_WEB_SEARCH_URL + quote_plus(query), None)
            win._show_status(f'Searching the web for {query}...')
        except GLib.Error as error:
            win._show_status(f'No browser available: {error.message}')
