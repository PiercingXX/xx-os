"""xx-files: native Linux file manager (Android xx-files port).

Volumes + tree/list/grid browsing, breadcrumbs, sort, search,
multi-select, copy/move/rename/delete (freedesktop Trash), new folder,
open-with, share via xx-share, properties, progress with cancel.

Look (Android tree list in the PiercingXX brand): the path IS the
header (muted ancestors, current folder in text, a faint slash between),
one overflow, a hairline, then text-first rows. No title, no status
line, no empty-state copy: an empty folder shows nothing. Row actions,
dialogs and the View sheet slide up from the bottom over a scrim.

Headless-safe import: Gtk/Adw load lazily so pure tests never need a
display. Workers own every directory read and file operation; results
post back with GLib.idle_add.
"""
from __future__ import annotations

import os

from .prefs import Settings, load as load_settings, save as save_settings

# Sizes that must follow the user's text scale come from the shared
# xx_ui classes (xx-title, xx-secondary, xx-btn, ...); this sheet only
# adds geometry and the theme's named colors, so every preset (and a
# live switch) restyles it.
APP_CSS = """
.xx-files-top { min-height: 56px; padding: 4px 4px 4px 4px; }
.xx-files-rule { min-height: 1px; background-color: @borders; }
button.xx-files-crumb { min-height: 44px; min-width: 0; padding: 0 4px;
  border-radius: 0; color: @insensitive_fg_color; }
button.xx-files-crumb.current { color: @window_fg_color; }
.xx-files-crumb-sep { color: @borders; }
entry.xx-files-search { background-color: @card_bg_color; border: none;
  box-shadow: none; border-radius: 12px; min-height: 44px;
  padding: 0 12px; margin: 8px 20px 4px; }
row.xx-files-row { padding: 8px 20px; border-radius: 0; }
row.xx-files-row:active { background-color: @card_bg_color; }
row.xx-files-message { padding: 16px 20px; }
.xx-files-guide { color: @insensitive_fg_color; }
.xx-files-row check { min-width: 18px; min-height: 18px;
  border: 1px solid @insensitive_fg_color; border-radius: 3px;
  background: none; box-shadow: none; }
.xx-files-row check:checked { background-color: @window_fg_color;
  color: @window_bg_color; border-color: @window_fg_color; }
.xx-files-grid-cell { padding: 12px 2px; }
.xx-files-grid-name { color: @window_fg_color; }
.xx-files-selbar { border-top: 1px solid @borders; padding: 4px 4px 8px 20px; }
.xx-files-scrim { background-color: alpha(@window_bg_color, 0.72); }
.xx-files-sheet { background-color: @card_bg_color;
  border-top: 1px solid @borders; border-radius: 16px 16px 0 0;
  padding: 8px 0 12px; }
.xx-files-sheet-head { padding: 12px 20px 8px; }
button.xx-files-action { min-height: 48px; padding: 0 20px; border-radius: 0; }
button.xx-files-action:active { background-color: @insensitive_bg_color; }
.xx-files-dialog { padding: 0 20px; }
.xx-files-buttons { padding: 12px 12px 0 20px; }
.xx-files-prop { padding-top: 6px; padding-bottom: 6px; }
.xx-files-sheet switch { border: 1px solid @borders; }
.xx-files-sheet entry.xx-entry { background-color: @window_bg_color;
  border: 1px solid @borders; }
.xx-files-sheet entry.xx-entry:focus { border-color: @window_fg_color; }
.xx-files-sheet .xx-segmented { margin-top: 8px; }
.xx-files-sheet progressbar trough { min-height: 4px; border-radius: 2px; }
.xx-files-sheet progressbar progress { min-height: 4px; border-radius: 2px; }
"""

VIEWS = (("list", "List"), ("tree", "Tree"), ("grid", "Grid"))
SORTS = (("name", "Name"), ("size", "Size"), ("date", "Date"))


def _require_gtk():
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

    return Adw, Gdk, Gio, GLib, Gtk, Pango


def _cond_ellipsize(label, Pango, text: str, chars: int) -> None:
    """Ellipsize only text that cannot fit ``chars``.

    An unconditional ellipsis misfires on fractional rounding and
    swaps a fitting character for "…" (xx_ui.widgets documents the
    same trap); a wrapped label must never ellipsize at all.
    """
    label.set_max_width_chars(chars)
    if len(text) > chars:
        label.set_ellipsize(Pango.EllipsizeMode.END)
    else:
        label.set_ellipsize(Pango.EllipsizeMode.NONE)


def _tap_to_focus(entry) -> None:
    """Entries take focus only from an explicit tap (no auto keyboard)."""
    from gi.repository import Gtk

    entry.set_focusable(False)
    entry.set_can_focus(False)
    tap = Gtk.GestureClick.new()
    tap.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    tap.connect("pressed", lambda *_: (
        entry.set_focusable(True), entry.set_can_focus(True),
        entry.grab_focus()))
    entry.add_controller(tap)
    focus = Gtk.EventControllerFocus.new()
    focus.connect("leave", lambda *_: (
        entry.set_focusable(False), entry.set_can_focus(False)))
    entry.add_controller(focus)


def pretty_path(path: str, home: str) -> str:
    """``~`` for the home prefix, the path otherwise."""
    home = home.rstrip("/")
    if home and (path == home or path.startswith(home + "/")):
        return "~" + path[len(home):]
    return path


class FilesWindow:
    """Main window; built inside an Adw.ApplicationWindow."""

    def __init__(self, app, settings: Settings | None = None) -> None:
        Adw, Gdk, Gio, GLib, Gtk, Pango = _require_gtk()
        self._Adw, self._Gdk, self._Gio = Adw, Gdk, Gio
        self._GLib, self._Gtk, self._Pango = GLib, Gtk, Pango
        self.settings = settings or load_settings()
        try:
            import xx_ui

            self._xx_ui = xx_ui
        except Exception:
            self._xx_ui = None
        self.mode = "volumes"
        self.cwd = os.path.expanduser("~")
        self.root_label = "Home"
        self.roots: list = []
        self.rows: list[dict] = []
        self.trash_rows: list = []
        self.expanded: set[str] = set()
        self.selection: set[str] = set()
        self.selecting = False
        self.clip: list[tuple[str, bool]] = []
        self.query = ""
        self._list_job = None
        self._op_job = None
        self._alive = True
        self._list_error: dict = {"status": "ok"}
        self._kids: dict[str, list[dict]] = {}
        self._sheet_modal = False

        self.win = Adw.ApplicationWindow(application=app)
        self.win.set_title("xx-files")
        self.win.set_default_size(360, 720)
        # Adw windows floor at 360 px wide by default: a 320 px
        # screen would cut the app off.
        self.win.set_size_request(320, 200)
        self._apply_theme()
        self._load_app_css()
        try:
            self._theme_unsub = xx_ui.on_change(lambda _t: self._apply_theme())
        except Exception:
            self._theme_unsub = None

        overlay = Gtk.Overlay()
        self.win.set_content(overlay)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        overlay.set_child(root)

        # Header: back, the path, search, overflow. The path is the title.
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        top.add_css_class("xx-files-top")
        self.back_btn = self._icon_button("go-previous-symbolic", "Back",
                                          self.go_back)
        top.append(self.back_btn)
        self.crumb_scroll = Gtk.ScrolledWindow()
        self.crumb_scroll.set_policy(Gtk.PolicyType.EXTERNAL,
                                     Gtk.PolicyType.NEVER)
        self.crumb_scroll.set_hexpand(True)
        self.crumb_scroll.set_valign(Gtk.Align.CENTER)
        self.crumb_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                 spacing=0)
        self.crumb_scroll.set_child(self.crumb_box)
        try:
            adj = self.crumb_scroll.get_hadjustment()
            adj.connect("changed", self._pin_crumbs_right)
        except Exception:
            pass
        top.append(self.crumb_scroll)
        self.search_btn = self._icon_button("system-search-symbolic",
                                            "Search", self._toggle_search)
        top.append(self.search_btn)
        self.overflow_btn = Gtk.MenuButton()
        self.overflow_btn.add_css_class("xx-btn")
        self.overflow_btn.add_css_class("xx-btn-icon")
        self.overflow_btn.set_icon_name("view-more-symbolic")
        self.overflow_btn.set_tooltip_text("More actions")
        self.overflow_btn.set_valign(Gtk.Align.CENTER)
        top.append(self.overflow_btn)
        root.append(top)
        rule = Gtk.Box()
        rule.add_css_class("xx-files-rule")
        root.append(rule)

        self.search_revealer = Gtk.Revealer()
        self.search_revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.add_css_class("xx-files-search")
        self.search_entry.set_placeholder_text("Search this folder")
        self.search_entry.connect("search-changed",
                                  self._on_search_changed)
        _tap_to_focus(self.search_entry)
        self.search_revealer.set_child(self.search_entry)
        root.append(self.search_revealer)

        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER,
                               Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_vexpand(True)
        self.list_box = Gtk.ListBox()
        self.list_box.add_css_class("xx-list")
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list_box.set_margin_top(4)
        self.list_box.connect("row-activated", self._on_row_activated)
        self.scroll.set_child(self.list_box)
        root.append(self.scroll)

        self.grid_scroll = Gtk.ScrolledWindow()
        self.grid_scroll.set_policy(Gtk.PolicyType.NEVER,
                                    Gtk.PolicyType.AUTOMATIC)
        self.grid_scroll.set_vexpand(True)
        self.flow = Gtk.FlowBox()
        self.flow.set_valign(Gtk.Align.START)
        self.flow.set_homogeneous(True)
        self.flow.set_max_children_per_line(4)
        self.flow.set_min_children_per_line(3)
        self.flow.set_column_spacing(0)
        self.flow.set_row_spacing(4)
        self.flow.set_margin_start(12)
        self.flow.set_margin_end(12)
        self.flow.set_margin_top(8)
        self.flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.flow.connect("child-activated", self._on_grid_activated)
        self.grid_scroll.set_child(self.flow)
        self.grid_scroll.set_visible(False)
        root.append(self.grid_scroll)

        # Selection bar: count + Done, then the four verbs spread out.
        self.sel_revealer = Gtk.Revealer()
        self.sel_revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_UP)
        sel_bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sel_bar.add_css_class("xx-files-selbar")
        sel_top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.sel_label = Gtk.Label(label="")
        self.sel_label.add_css_class("xx-secondary")
        self.sel_label.set_xalign(0.0)
        self.sel_label.set_hexpand(True)
        self.sel_label.set_ellipsize(Pango.EllipsizeMode.NONE)
        self.sel_label.set_max_width_chars(16)
        sel_top.append(self.sel_label)
        done = self._text_button("Done", self._end_selection)
        sel_top.append(done)
        sel_bar.append(sel_top)
        sel_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                              spacing=0)
        sel_actions.set_homogeneous(True)
        sel_actions.set_margin_end(16)
        for name, tip, cb in (
                ("edit-copy-symbolic", "Copy",
                 lambda: self._stage_selection(False)),
                ("edit-cut-symbolic", "Cut",
                 lambda: self._stage_selection(True)),
                ("user-trash-symbolic", "Delete",
                 lambda: self._ask_delete(sorted(self.selection))),
                ("send-to-symbolic", "Share",
                 lambda: self._share_paths(sorted(self.selection)))):
            sel_actions.append(self._icon_button(name, tip, cb))
        sel_bar.append(sel_actions)
        self.sel_revealer.set_child(sel_bar)
        root.append(self.sel_revealer)

        # Bottom sheet over a scrim; tapping the scrim dismisses it.
        self.sheet_layer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                   spacing=0)
        self.sheet_layer.set_visible(False)
        scrim = Gtk.Box()
        scrim.add_css_class("xx-files-scrim")
        scrim.set_vexpand(True)
        tap = Gtk.GestureClick.new()
        tap.connect("released", lambda *_: self._dismiss_sheet())
        scrim.add_controller(tap)
        self.sheet_layer.append(scrim)
        self.sheet_scroll = Gtk.ScrolledWindow()
        self.sheet_scroll.set_policy(Gtk.PolicyType.NEVER,
                                     Gtk.PolicyType.AUTOMATIC)
        self.sheet_scroll.set_propagate_natural_height(True)
        self.sheet_scroll.add_css_class("xx-files-sheet")
        self.sheet_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                 spacing=0)
        self.sheet_scroll.set_child(self.sheet_box)
        self.sheet_layer.append(self.sheet_scroll)
        overlay.add_overlay(self.sheet_layer)

        keys = Gtk.EventControllerKey.new()
        keys.connect("key-pressed", self._on_key)
        self.win.add_controller(keys)

        self._register_actions()
        self._refresh_roots()
        self._watch_volumes()
        self._render_chrome()
        self.refresh()

    # -- small builders ---------------------------------------------
    def _icon_button(self, icon: str, tip: str, callback):
        Gtk = self._Gtk
        btn = Gtk.Button.new_from_icon_name(icon)
        btn.add_css_class("xx-btn")
        btn.add_css_class("xx-btn-icon")
        btn.set_tooltip_text(tip)
        btn.set_valign(Gtk.Align.CENTER)
        btn.connect("clicked", lambda _b: callback())
        return btn

    def _text_button(self, text: str, callback, kind: str = "text"):
        btn = self._Gtk.Button.new_with_label(text)
        btn.add_css_class("xx-btn")
        btn.add_css_class({"text": "xx-btn-text",
                           "primary": "xx-btn-primary",
                           "danger": "xx-btn-danger"}[kind])
        if kind != "text":
            btn.set_margin_start(4)
        child = btn.get_child()
        if child is not None:
            _cond_ellipsize(child, self._Pango, text, 16)
            child.set_margin_start(12)
            child.set_margin_end(12)
        btn.connect("clicked", lambda _b: callback())
        return btn

    def _label(self, text: str, cls: str, chars: int = 28,
               wrap: bool = False):
        label = self._Gtk.Label(label=text)
        label.add_css_class(cls)
        label.set_xalign(0.0)
        if wrap:
            label.set_wrap(True)
            label.set_wrap_mode(self._Pango.WrapMode.WORD_CHAR)
            label.set_ellipsize(self._Pango.EllipsizeMode.NONE)
            label.set_max_width_chars(chars)
        else:
            _cond_ellipsize(label, self._Pango, text, chars)
        return label

    def _segmented(self, items, current: str, on_pick):
        """Joined caps choices (xx_ui segmented look), one selected."""
        Gtk = self._Gtk
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        box.add_css_class("xx-segmented")
        box.set_homogeneous(True)
        last = len(items) - 1
        for index, (key, text) in enumerate(items):
            btn = Gtk.Button.new_with_label(text.upper())
            btn.add_css_class("seg-first" if index == 0 else
                              "seg-last" if index == last else "seg-mid")
            if key == current:
                btn.add_css_class("seg-selected")
            btn.connect("clicked", lambda _b, k=key: on_pick(k))
            box.append(btn)
        return box

    # -- menus ------------------------------------------------------
    def _register_actions(self) -> None:
        Gio = self._Gio
        app = self.win.get_application()
        for name, cb in (("new-folder", self._ask_new_folder),
                         ("paste", self._paste),
                         ("view", self._show_view_sheet),
                         ("trash", self.show_trash),
                         ("volumes", self.show_volumes),
                         ("empty-trash", self._ask_empty_trash)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a, f=cb: f())
            app.add_action(action)

    def _rebuild_menu(self) -> None:
        """Overflow lists only what makes sense here: Paste appears once
        something is staged, Empty Trash only inside Trash."""
        menu = self._Gio.Menu()
        if self.mode == "browse":
            menu.append("New folder", "app.new-folder")
            if self.clip:
                menu.append("Paste", "app.paste")
            menu.append("View", "app.view")
            menu.append("Trash", "app.trash")
            menu.append("Volumes", "app.volumes")
        elif self.mode == "trash":
            if self.trash_rows:
                menu.append("Empty Trash", "app.empty-trash")
            menu.append("Volumes", "app.volumes")
        else:
            menu.append("Trash", "app.trash")
        self.overflow_btn.set_menu_model(menu)

    def _set_sort(self, key: str) -> None:
        if self.settings.sort != key:
            self.settings.sort = key
            save_settings(self.settings)
            self._render_rows()
        self._show_view_sheet()

    def _set_view(self, key: str) -> None:
        if self.settings.view != key:
            self.settings.view = key
            save_settings(self.settings)
            self._render_chrome()
            self._render_rows()
        self._show_view_sheet()

    def _set_show_hidden(self, value: bool) -> None:
        if self.settings.show_hidden != value:
            self.settings.show_hidden = value
            save_settings(self.settings)
            self.refresh()

    def _toggle_search(self) -> None:
        open_ = not self.search_revealer.get_reveal_child()
        self.search_revealer.set_reveal_child(open_)
        if not open_:
            self.search_entry.set_text("")
            self.query = ""
            self._render_rows()

    def _on_search_changed(self, entry) -> None:
        self.query = entry.get_text().strip().casefold()
        self._render_rows()

    def _on_key(self, _ctrl, keyval, _code, _state) -> bool:
        if keyval == self._Gdk.KEY_Escape:
            if self.sheet_layer.get_visible():
                self._dismiss_sheet()
                return True
            if self.selecting:
                self._end_selection()
                return True
        return False

    # -- theme ------------------------------------------------------
    def _apply_theme(self) -> None:
        try:
            self._xx_ui.apply(self.win)
        except Exception:
            pass

    def _load_app_css(self) -> None:
        try:
            provider = self._Gtk.CssProvider()
            provider.load_from_data(APP_CSS.encode("utf-8"))
            display = self._Gdk.Display.get_default()
            if display is not None:
                self._Gtk.StyleContext.add_provider_for_display(
                    display, provider,
                    self._Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        except Exception:
            pass

    # -- navigation -------------------------------------------------
    def _home(self) -> str:
        return os.environ.get("XX_FILES_HOME") or os.path.expanduser("~")

    def _refresh_roots(self) -> None:
        from .ops import discover_roots, removable_mounts

        home = self._home()
        mounts: list = []
        # Seeded demo renders list only the seeded home, never this
        # machine's real media.
        if not os.environ.get("XX_FILES_SCENARIO"):
            try:
                mounts = removable_mounts()
            except Exception:
                mounts = []
        self.roots = discover_roots(home, mounts)
        if not any(r.root_path == self.cwd for r in self.roots):
            self.cwd = home

    def _watch_volumes(self) -> None:
        try:
            monitor = self._Gio.VolumeMonitor.get()
            monitor.connect("mount-added", lambda *_: self._on_media())
            monitor.connect("mount-removed", lambda *_: self._on_media())
        except Exception:
            pass

    def _on_media(self) -> None:
        self._refresh_roots()
        if self.mode == "volumes":
            self._render_rows()

    def show_volumes(self) -> None:
        self.mode = "volumes"
        self._clear_selection()
        self._close_search()
        self._render_chrome()
        self._render_rows()

    def show_trash(self) -> None:
        self.mode = "trash"
        self._clear_selection()
        self._close_search()
        self._render_chrome()
        self.refresh_trash()

    def _close_search(self) -> None:
        if self.search_revealer.get_reveal_child():
            self._toggle_search()

    def open_path(self, path: str) -> None:
        from .volumes import containing

        vol = containing(path, list(self.roots))
        if vol is not None:
            self.root_label = vol.label
        self.cwd = path
        self.mode = "browse"
        self._clear_selection()
        self._render_chrome()
        self.refresh()

    def go_back(self) -> None:
        from .volumes import back_dest
        from .volumes import Dest

        if self.sheet_layer.get_visible():
            self._dismiss_sheet()
            return
        if self.selecting:
            self._end_selection()
            return
        dest = back_dest(self.mode == "volumes", self.mode == "trash",
                         self.cwd, list(self.roots))
        if dest is Dest.FINISH:
            self.win.get_application().quit()
        elif dest is Dest.VOLUME_LIST:
            self.show_volumes()
        else:
            parent = os.path.dirname(self.cwd.rstrip("/")) or "/"
            self.open_path(parent)

    # -- data refresh (workers) -------------------------------------
    def refresh(self) -> None:
        from . import jobs
        from .ops import list_dir_sync

        if self._list_job is not None:
            self._list_job.cancel()
        self._kids = {}
        path, show_hidden = self.cwd, self.settings.show_hidden

        def on_done(result: dict) -> None:
            if not self._alive:
                return
            self._list_job = None
            if result.get("status") == "ok":
                self.rows = result["rows"]
            else:
                self.rows = []
            self._list_error = result
            self._render_rows()
            if (result.get("status") == "ok"
                    and self.mode == "browse"
                    and self.settings.view == "tree"):
                for row in self.rows:
                    if row["is_dir"] and row["path"] in self.expanded \
                            and row["path"] not in self._kids:
                        self._expand_async(row["path"])

        def on_error(exc: Exception) -> None:
            if not self._alive:
                return
            self._list_job = None
            from .io_error import message

            self.rows = []
            self._list_error = {"status": "failed",
                                "message": message(exc)}
            self._render_rows()

        self._list_error = {"status": "ok"}
        self._list_job = jobs.submit(list_dir_sync, path, show_hidden,
                                     on_done=on_done, on_error=on_error)

    def refresh_trash(self) -> None:
        from . import jobs
        from .ops import trash_dirs
        from .trash_info import list_entries

        files_dir, info_dir = trash_dirs()

        def on_done(entries: list) -> None:
            if not self._alive:
                return
            self.trash_rows = entries
            self._render_rows()
            self._rebuild_menu()

        def on_error(_exc: Exception) -> None:
            if not self._alive:
                return
            self.trash_rows = []
            self._render_rows()
            self._rebuild_menu()

        jobs.submit(lambda job: list_entries(files_dir, info_dir),
                    on_done=on_done, on_error=on_error)

    # -- chrome -----------------------------------------------------
    def _render_chrome(self) -> None:
        Gtk = self._Gtk
        while (child := self.crumb_box.get_first_child()) is not None:
            self.crumb_box.remove(child)
        if self.mode == "volumes":
            crumbs = ["Volumes"]
        elif self.mode == "trash":
            crumbs = ["Trash"]
        else:
            from .breadcrumbs import split

            root = self._current_root()
            crumbs = split(self.cwd, root, self.root_label)
        last = len(crumbs) - 1
        for index, label in enumerate(crumbs):
            if index:
                sep = Gtk.Label(label="/")
                sep.add_css_class("xx-files-crumb-sep")
                sep.add_css_class("xx-body")
                sep.set_margin_start(2)
                sep.set_margin_end(2)
                self.crumb_box.append(sep)
            btn = Gtk.Button.new_with_label(label)
            btn.add_css_class("xx-btn")
            btn.add_css_class("xx-files-crumb")
            if index == last:
                btn.add_css_class("current")
            btn.connect("clicked", self._on_crumb, index)
            child = btn.get_child()
            if child is not None:
                try:
                    _cond_ellipsize(child, self._Pango, label,
                                    18 if index == last else 10)
                except Exception:
                    pass
            self.crumb_box.append(btn)
        browsing = self.mode == "browse"
        grid = browsing and self.settings.view == "grid"
        self.scroll.set_visible(not grid)
        self.grid_scroll.set_visible(grid)
        self.search_btn.set_visible(browsing)
        self.back_btn.set_visible(self.mode != "volumes")
        self.crumb_box.set_margin_start(16 if self.mode == "volumes" else 0)
        self._rebuild_menu()

    def _pin_crumbs_right(self, adj) -> None:
        """Keep the current folder in view as the path grows."""
        try:
            adj.set_value(adj.get_upper() - adj.get_page_size())
        except Exception:
            pass

    def _current_root(self) -> str:
        from .volumes import containing

        vol = containing(self.cwd, list(self.roots))
        if vol is not None:
            self.root_label = vol.label
            return vol.root_path
        return os.path.expanduser("~")

    def _on_crumb(self, _btn, index: int) -> None:
        if self.mode == "volumes":
            return
        if self.mode == "trash":
            self.show_volumes()
            return
        from .breadcrumbs import navigate_to

        target = navigate_to(self.cwd, self._current_root(), index)
        if target != self.cwd:
            self.open_path(target)

    # -- rows -------------------------------------------------------
    def _visible_rows(self) -> list[dict]:
        from .filekind import of
        from .listing import Entry, sort_by

        entries = [Entry(r["name"], r["path"], r["is_dir"], r["size"],
                         r["modified"]) for r in self.rows]
        ordered = sort_by(entries, self.settings.sort)
        by_path = {r["path"]: r for r in self.rows}
        out = []
        for entry in ordered:
            if self.query and self.query not in entry.name.casefold():
                continue
            info = by_path[entry.path]
            out.append({"entry": entry, "kind": of(entry.name,
                                                   entry.is_dir).value,
                        "info": info, "depth": 0, "guide": "",
                        "expanded": None})
        return out

    def _tree_nodes(self) -> list[dict]:
        from .filekind import of
        from .listing import Entry
        from .tree import flatten, guide

        children = [Entry(r["name"], r["path"], r["is_dir"], r["size"],
                          r["modified"]) for r in self.rows]
        by_path = {r["path"]: r for r in self.rows}

        def read_children(entry: Entry) -> list[Entry] | None:
            kids = self._kids.get(entry.path) or []
            return [Entry(k["name"], k["path"], k["is_dir"], k["size"],
                          k["modified"]) for k in kids]

        nodes = flatten(children, self.expanded,
                        self.settings.show_hidden,
                        read_children=read_children)
        index = dict(by_path)
        for kids in self._kids.values():
            for kid in kids:
                index.setdefault(kid["path"], kid)
        out = []
        for node in nodes:
            if self.query and self.query not in node.entry.name.casefold():
                continue
            info = index.get(node.entry.path)
            if info is None:
                kids = self._kids.get(node.entry.path, [])
                info = {"is_dir": node.is_dir, "size": node.entry.size,
                        "modified": node.entry.modified,
                        "count": len(kids), "hidden": 0}
            out.append({"entry": node.entry,
                        "kind": of(node.entry.name,
                                   node.is_dir).value,
                        "info": info, "depth": node.depth,
                        "guide": guide(node),
                        "expanded": node.expanded if node.is_dir else None})
        return out

    @staticmethod
    def _read_kids_sync(job, path: str,
                        show_hidden: bool) -> tuple[str, list[dict]]:
        from .hidden import should_show
        from .ops import count_visible

        kids: list[dict] = []
        try:
            with os.scandir(path) as it:
                scanned = list(it)
        except OSError:
            return path, []
        for entry in scanned:
            if job.cancelled:
                from .jobs import Cancelled

                raise Cancelled()
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if not should_show(entry.name, show_hidden):
                continue
            try:
                stat = entry.stat(follow_symlinks=False)
                size, modified = stat.st_size, stat.st_mtime
            except OSError:
                size, modified = 0, 0.0
            count, hidden = (0, 0)
            if is_dir:
                count, hidden = count_visible(entry.path, show_hidden)
            kids.append({"name": entry.name, "path": entry.path,
                         "is_dir": is_dir, "size": size,
                         "modified": modified, "count": count,
                         "hidden": hidden})
        return path, kids

    def _expand_async(self, path: str) -> None:
        from . import jobs

        self.expanded.add(path)
        show_hidden = self.settings.show_hidden

        def on_done(result: tuple[str, list[dict]]) -> None:
            if not self._alive:
                return
            _path, kids = result
            self._kids[_path] = kids
            self._render_rows()

        jobs.submit(self._read_kids_sync, path, show_hidden,
                    on_done=on_done,
                    on_error=lambda _e: self._render_rows())
        self._render_rows()

    def _render_rows(self) -> None:
        while (row := self.list_box.get_row_at_index(0)) is not None:
            self.list_box.remove(row)
        child = self.flow.get_child_at_index(0)
        while child is not None:
            self.flow.remove(child)
            child = self.flow.get_child_at_index(0)
        if self.mode == "volumes":
            for vol in self.roots:
                self.list_box.append(self._volume_row(vol))
        elif self.mode == "trash":
            for entry in self.trash_rows:
                self.list_box.append(self._trash_row(entry))
        elif self._list_error.get("status") != "ok":
            # One quiet line where the rows would be; nothing else.
            if self._list_error.get("status") == "blocked":
                from .directory_contents import BLOCKED_COPY

                text = BLOCKED_COPY
            else:
                text = self._list_error.get("message") or "Could not open"
            self.list_box.append(self._message_row(text))
        elif self.settings.view == "grid":
            for item in self._visible_rows():
                self.flow.append(self._grid_cell(item))
        elif self.settings.view == "tree":
            for item in self._tree_nodes():
                self.list_box.append(self._browse_row(item))
        else:
            for item in self._visible_rows():
                self.list_box.append(self._browse_row(item))
        self._update_selection_bar()

    def _message_row(self, text: str):
        row = self._Gtk.ListBoxRow()
        row.add_css_class("xx-files-message")
        row.set_activatable(False)
        row.set_child(self._label(text, "xx-secondary", 32, wrap=True))
        return row

    def _row_shell(self, name: str):
        Gtk = self._Gtk
        row = Gtk.ListBoxRow()
        row.add_css_class("xx-files-row")
        row.set_name(name)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        row.set_child(box)
        return row, box

    def _row_texts(self, box, title: str, sub: str) -> None:
        Gtk = self._Gtk
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        texts.set_hexpand(True)
        texts.set_valign(Gtk.Align.CENTER)
        texts.append(self._label(title, "xx-title", 22))
        if sub:
            texts.append(self._label(sub, "xx-secondary", 26))
        box.append(texts)

    def _row_icon(self, box, icon: str) -> None:
        img = self._Gtk.Image.new_from_icon_name(icon)
        img.set_pixel_size(22)
        img.set_valign(self._Gtk.Align.CENTER)
        box.append(img)

    def _volume_row(self, vol) -> object:
        row, box = self._row_shell("vol:" + vol.root_path)
        if vol.is_primary:
            icon = "user-home-symbolic"
        elif vol.is_removable:
            icon = "drive-removable-media-symbolic"
        else:
            icon = "folder-symbolic"
        self._row_icon(box, icon)
        self._row_texts(box, vol.label,
                        pretty_path(vol.root_path, self._home()))
        return row

    def _second_line(self, item: dict) -> str:
        from .row import item_count, second_line_file

        entry, info = item["entry"], item["info"]
        if entry.is_dir:
            return item_count(info.get("count") or 0,
                              info.get("hidden") or 0)
        modified_ms = int((entry.modified or info.get("modified") or 0)
                          * 1000)
        return second_line_file(entry.size or info.get("size") or 0,
                                modified_ms)

    def _browse_row(self, item: dict):
        Gtk = self._Gtk
        from .row import icon_for_kind

        entry = item["entry"]
        row, box = self._row_shell("file:" + entry.path)
        if item["guide"]:
            # `tree`-style guide in front of the glyph, like Android.
            guide = self._label(item["guide"].rstrip(), "xx-secondary", 24)
            guide.add_css_class("xx-files-guide")
            guide.set_valign(Gtk.Align.CENTER)
            box.append(guide)
        if self.selecting:
            check = Gtk.CheckButton()
            check.set_active(entry.path in self.selection)
            check.set_can_focus(False)
            check.set_can_target(False)
            check.set_valign(Gtk.Align.CENTER)
            box.append(check)
        self._row_icon(box, icon_for_kind(item["kind"],
                                          bool(item.get("expanded"))))
        self._row_texts(box, entry.name, self._second_line(item))
        long_press = Gtk.GestureLongPress.new()
        long_press.connect("pressed", self._on_long_press, entry.path,
                           entry.is_dir)
        row.add_controller(long_press)
        return row

    def _grid_cell(self, item: dict):
        Gtk, Pango = self._Gtk, self._Pango
        from .row import icon_for_kind

        entry = item["entry"]
        cell = Gtk.FlowBoxChild()
        cell.set_name("file:" + entry.path)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("xx-files-grid-cell")
        img = Gtk.Image.new_from_icon_name(icon_for_kind(item["kind"]))
        img.set_pixel_size(36)
        img.set_halign(Gtk.Align.CENTER)
        box.append(img)
        label = Gtk.Label(label=entry.name)
        label.add_css_class("xx-time")
        label.add_css_class("xx-files-grid-name")
        label.set_justify(Gtk.Justification.CENTER)
        label.set_xalign(0.5)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_lines(2)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(8)
        box.append(label)
        if self.selecting and entry.path in self.selection:
            cell.add_css_class("selected")
        cell.set_child(box)
        long_press = Gtk.GestureLongPress.new()
        long_press.connect("pressed", self._on_long_press, entry.path,
                           entry.is_dir)
        cell.add_controller(long_press)
        return cell

    def _trash_row(self, entry) -> object:
        from .filekind import of
        from .row import icon_for_kind

        row, box = self._row_shell("trash:" + entry.trash_path)
        is_dir = os.path.isdir(entry.trash_path)
        self._row_icon(box, icon_for_kind(of(entry.display_name,
                                             is_dir).value))
        origin = entry.original_path or "Original unknown"
        self._row_texts(box, entry.display_name,
                        pretty_path(origin, self._home()))
        long_press = self._Gtk.GestureLongPress.new()
        long_press.connect("pressed", self._on_trash_long_press,
                           entry.trash_path)
        row.add_controller(long_press)
        return row

    # -- activation -------------------------------------------------
    def _row_target(self, row) -> tuple[str, bool] | None:
        name = row.get_name() or ""
        if name.startswith("vol:"):
            self.open_path(name[4:])
            return None
        if name.startswith("file:"):
            path = name[5:]
            return path, os.path.isdir(path)
        if name.startswith("trash:"):
            return name[6:], False
        return None

    def _on_row_activated(self, _box, row) -> None:
        target = self._row_target(row)
        if target is None:
            return
        path, is_dir = target
        if self.selecting and self.mode == "browse":
            self._toggle_selected(path)
            return
        if self.mode == "trash":
            self._show_trash_actions(path)
            return
        if is_dir:
            if self.settings.view == "tree":
                if path in self.expanded:
                    self.expanded.discard(path)
                    self._render_rows()
                else:
                    self._expand_async(path)
            else:
                self.open_path(path)
        else:
            self._open_file(path)

    def _on_grid_activated(self, _flow, child) -> None:
        name = child.get_name() or ""
        if not name.startswith("file:"):
            return
        path = name[5:]
        if self.selecting:
            self._toggle_selected(path)
            return
        if os.path.isdir(path):
            self.open_path(path)
        else:
            self._open_file(path)

    def _open_file(self, path: str) -> None:
        from .ops import launch_default

        error = launch_default(path)
        if error:
            self._show_note("Cannot open", error)

    def _on_long_press(self, _gesture, _x: float, _y: float,
                       path: str, is_dir: bool) -> None:
        if self.mode == "trash":
            return
        if self.selecting:
            self._toggle_selected(path)
            return
        self._show_file_actions(path, is_dir)

    def _on_trash_long_press(self, _gesture, _x: float, _y: float,
                             trash_path: str) -> None:
        self._show_trash_actions(trash_path)

    def _start_selection(self, path: str) -> None:
        self.selecting = True
        self.selection = {path}
        self._render_rows()

    def _toggle_selected(self, path: str) -> None:
        if path in self.selection:
            self.selection.discard(path)
        else:
            self.selection.add(path)
        if not self.selection:
            self.selecting = False
        self._render_rows()

    def _end_selection(self) -> None:
        self._clear_selection()
        self._render_rows()

    def _clear_selection(self) -> None:
        self.selecting = False
        self.selection = set()
        if hasattr(self, "sel_revealer"):
            self._update_selection_bar()

    def _update_selection_bar(self) -> None:
        show = self.selecting and bool(self.selection)
        self.sel_revealer.set_reveal_child(show)
        if show:
            text = f"{len(self.selection)} selected"
            self.sel_label.set_text(text)
            _cond_ellipsize(self.sel_label, self._Pango, text, 16)

    def _stage_selection(self, is_cut: bool) -> None:
        self.clip = [(p, is_cut) for p in sorted(self.selection)]
        self._end_selection()
        self._rebuild_menu()

    # -- bottom sheets ----------------------------------------------
    def _open_sheet(self, title: str = "", modal: bool = False,
                    head_cls: str = "xx-dialog-title") -> object:
        while (child := self.sheet_box.get_first_child()) is not None:
            self.sheet_box.remove(child)
        if title:
            head = self._label(title, head_cls, 24)
            head.add_css_class("xx-files-sheet-head")
            self.sheet_box.append(head)
        self._sheet_modal = modal
        try:
            height = self.win.get_height() or 720
            self.sheet_scroll.set_max_content_height(int(height * 0.7))
        except Exception:
            pass
        self.sheet_layer.set_visible(True)
        return self.sheet_box

    def _close_sheet(self) -> None:
        self._sheet_modal = False
        self.sheet_layer.set_visible(False)

    def _dismiss_sheet(self) -> None:
        """Scrim tap / Escape: closes anything but a running job."""
        if not self._sheet_modal:
            self._close_sheet()

    def _sheet_body(self, box, text: str) -> None:
        body = self._label(text, "xx-secondary", 30, wrap=True)
        body.add_css_class("xx-files-dialog")
        box.append(body)

    def _sheet_buttons(self, box, buttons: list) -> None:
        """Right-aligned: Cancel as text, the verb filled."""
        Gtk = self._Gtk
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        row.add_css_class("xx-files-buttons")
        row.set_halign(Gtk.Align.END)
        for spec in buttons:
            text, cb = spec[0], spec[1]
            kind = spec[2] if len(spec) > 2 else "text"
            row.append(self._text_button(text, cb, kind))
        box.append(row)

    def _sheet_entry(self, box, initial: str = "", placeholder: str = ""):
        entry = self._Gtk.Entry()
        entry.add_css_class("xx-entry")
        entry.set_text(initial)
        entry.set_placeholder_text(placeholder)
        entry.set_hexpand(True)
        entry.set_max_width_chars(20)
        entry.set_margin_start(20)
        entry.set_margin_end(20)
        entry.set_margin_top(4)
        box.append(entry)
        return entry

    def _sheet_action(self, box, text: str, callback) -> None:
        btn = self._Gtk.Button()
        btn.add_css_class("xx-files-action")
        btn.add_css_class("xx-menu-row")
        label = self._label(text, "xx-body", 24)
        label.set_hexpand(True)
        btn.set_child(label)
        btn.connect("clicked", lambda _b: callback())
        box.append(btn)

    def _show_note(self, title: str, body: str) -> None:
        box = self._open_sheet(title)
        self._sheet_body(box, body)
        self._sheet_buttons(box, [("OK", self._close_sheet)])

    def _ask_new_folder(self) -> None:
        if self.mode != "browse":
            return
        box = self._open_sheet("New folder")
        entry = self._sheet_entry(box, placeholder="Folder name")

        def save() -> None:
            from . import jobs
            from .ops import do_mkdir
            from .rename import validate

            name = entry.get_text().strip()
            error = validate(name)
            if error:
                self._show_note("New folder", error)
                return
            target = os.path.join(self.cwd, name)
            if os.path.exists(target):
                self._show_note("New folder",
                                f"{name} already exists here.")
                return
            self._close_sheet()
            jobs.submit(do_mkdir, target,
                        on_done=lambda _p: self.refresh(),
                        on_error=lambda e: self._op_failed(e))

        entry.connect("activate", lambda _e: save())
        self._sheet_buttons(box, [("Cancel", self._close_sheet),
                                  ("Create", save, "primary")])
        entry.grab_focus()

    def _ask_rename(self, path: str) -> None:
        box = self._open_sheet("Rename")
        entry = self._sheet_entry(box, os.path.basename(path))

        def save() -> None:
            from .ops import do_rename
            from .rename import validate, would_overwrite

            new_name = entry.get_text().strip()
            error = validate(new_name)
            if error:
                self._show_note("Rename", error)
                return
            try:
                siblings = os.listdir(os.path.dirname(path))
            except OSError:
                siblings = []
            target = os.path.join(os.path.dirname(path), new_name)
            if new_name != os.path.basename(path) and \
                    would_overwrite(os.path.basename(path), new_name,
                                    siblings):
                self._confirm_overwrite(path, target)
                return
            self._close_sheet()
            self._run_op(do_rename, path, target)

        entry.connect("activate", lambda _e: save())
        self._sheet_buttons(box, [("Cancel", self._close_sheet),
                                  ("Rename", save, "primary")])
        entry.grab_focus()

    def _confirm_overwrite(self, source: str, target: str) -> None:
        box = self._open_sheet("Replace file?")
        self._sheet_body(box, f"{os.path.basename(target)} already exists.")

        def replace() -> None:
            from . import jobs
            from .ops import _remove, do_rename

            self._close_sheet()
            jobs.submit(lambda job: (_remove(target),
                                     do_rename(job, source, target))[1],
                        on_done=lambda _p: self.refresh(),
                        on_error=lambda e: self._op_failed(e))

        self._sheet_buttons(box, [("Cancel", self._close_sheet),
                                  ("Replace", replace, "danger")])

    def _ask_delete(self, paths: list[str]) -> None:
        n = len(paths)
        box = self._open_sheet("Move to Trash?")
        what = os.path.basename(paths[0]) if n == 1 else f"{n} items"
        self._sheet_body(box, what)

        def delete() -> None:
            from . import jobs
            from .ops import do_trash

            self._close_sheet()
            self._end_selection()
            jobs.submit(do_trash, paths,
                        on_done=lambda _r: self.refresh(),
                        on_error=lambda e: self._op_failed(e))

        self._sheet_buttons(box, [("Cancel", self._close_sheet),
                                  ("Move to Trash", delete, "primary")])

    def _show_properties(self, path: str) -> None:
        from .mime import from_extension
        from .row import human_size, iso_date

        Gtk = self._Gtk
        box = self._open_sheet(os.path.basename(path) or path)
        facts: list[tuple[str, str]] = []
        try:
            stat = os.stat(path)
            facts.append(("Size", human_size(stat.st_size)))
            facts.append(("Modified", iso_date(int(stat.st_mtime * 1000))))
        except OSError:
            pass
        if os.path.isdir(path):
            count, _hidden = self._count_now(path)
            facts.append(("Contents", f"{count} items"))
        else:
            facts.append(("Type", from_extension(path)))
        facts.append(("Where", pretty_path(os.path.dirname(path) or "/",
                                          self._home())))
        for key, value in facts:
            fact = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            fact.add_css_class("xx-files-dialog")
            fact.add_css_class("xx-files-prop")
            fact.append(self._label(key.upper(), "xx-caption", 24))
            fact.append(self._label(value, "xx-body", 30,
                                    wrap=key == "Where"))
            box.append(fact)

    def _count_now(self, path: str) -> tuple[int, int]:
        from .ops import count_visible

        return count_visible(path, self.settings.show_hidden)

    def _show_file_actions(self, path: str, is_dir: bool) -> None:
        from .menu_actions import for_type

        box = self._open_sheet(os.path.basename(path), head_cls="xx-secondary")
        for action in for_type(is_dir):
            self._sheet_action(box, action.value.capitalize(),
                               lambda a=action: self._on_file_action(
                                   None, a, path))
        self._sheet_action(box, "Select", lambda: (
            self._close_sheet(), self._start_selection(path)))

    def _on_file_action(self, _btn, action, path: str) -> None:
        from .menu_actions import Action

        self._close_sheet()
        if action is Action.OPEN:
            if os.path.isdir(path):
                self.open_path(path)
            else:
                self._open_file(path)
        elif action is Action.SHARE:
            self._share_paths([path])
        elif action is Action.COPY:
            self.clip = [(path, False)]
            self._rebuild_menu()
        elif action is Action.CUT:
            self.clip = [(path, True)]
            self._rebuild_menu()
        elif action is Action.RENAME:
            self._ask_rename(path)
        elif action is Action.DELETE:
            self._ask_delete([path])
        elif action is Action.PROPERTIES:
            self._show_properties(path)

    def _show_trash_actions(self, trash_path: str) -> None:
        box = self._open_sheet(os.path.basename(trash_path),
                               head_cls="xx-secondary")
        self._sheet_action(box, "Restore", lambda: (
            self._close_sheet(), self._restore_entry(trash_path)))
        self._sheet_action(box, "Delete forever",
                           lambda: self._ask_delete_forever(trash_path))

    def _ask_delete_forever(self, trash_path: str) -> None:
        box = self._open_sheet("Delete forever?")
        self._sheet_body(box, os.path.basename(trash_path))
        self._sheet_buttons(box, [
            ("Cancel", self._close_sheet),
            ("Delete", lambda: (self._close_sheet(),
                                self._delete_forever([trash_path])),
             "danger")])

    def _ask_empty_trash(self) -> None:
        n = len(self.trash_rows)
        box = self._open_sheet("Empty Trash?")
        self._sheet_body(box, "1 item" if n == 1 else f"{n} items")
        self._sheet_buttons(box, [
            ("Cancel", self._close_sheet),
            ("Empty", lambda: (self._close_sheet(), self._empty_trash()),
             "danger")])

    def _show_view_sheet(self) -> None:
        """List/Tree/Grid, sort and hidden files in one place."""
        Gtk = self._Gtk
        box = self._open_sheet("View")
        for caption, items, current, pick in (
                ("Layout", VIEWS, self.settings.view, self._set_view),
                ("Sort", SORTS, self.settings.sort, self._set_sort)):
            group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            group.add_css_class("xx-files-dialog")
            group.add_css_class("xx-files-prop")
            group.append(self._label(caption.upper(), "xx-caption", 24))
            group.append(self._segmented(items, current, pick))
            box.append(group)
        hidden = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        hidden.add_css_class("xx-files-dialog")
        hidden.add_css_class("xx-files-prop")
        title = self._label("Show hidden", "xx-body", 20)
        title.set_hexpand(True)
        title.set_valign(Gtk.Align.CENTER)
        hidden.append(title)
        switch = Gtk.Switch()
        switch.add_css_class("xx-switch")
        switch.set_active(self.settings.show_hidden)
        switch.set_valign(Gtk.Align.CENTER)
        switch.connect("notify::active",
                       lambda s, _p: self._set_show_hidden(s.get_active()))
        hidden.append(switch)
        box.append(hidden)

    # -- operations -------------------------------------------------
    def _run_op(self, func, *args) -> None:
        from . import jobs

        if self._op_job is not None:
            self._op_job.cancel()

        def on_done(_result) -> None:
            if not self._alive:
                return
            self._op_job = None
            self._close_sheet()
            if self.mode == "trash":
                self.refresh_trash()
            else:
                self.refresh()

        def on_error(exc: Exception) -> None:
            if not self._alive:
                return
            self._op_job = None
            self._op_failed(exc)

        self._op_job = jobs.submit(func, *args, on_done=on_done,
                                   on_error=on_error)

    def _op_failed(self, exc: Exception) -> None:
        from .io_error import message

        self._close_sheet()
        self._show_note("Could not finish", message(exc))
        if self.mode == "trash":
            self.refresh_trash()
        else:
            self.refresh()

    def _paste(self) -> None:
        from . import jobs
        from .clip import can_paste
        from .ops import do_paste

        if self.mode != "browse" or not self.clip:
            return
        if not can_paste(self.clip[0], os.access(self.cwd, os.W_OK)):
            self._show_note("Paste", "This folder is read-only.")
            return
        items = list(self.clip)
        cwd = self.cwd
        self.clip = []
        self._rebuild_menu()
        self._show_progress("Pasting")
        progress = self._progress_cb

        def on_done(_made) -> None:
            if not self._alive:
                return
            self._op_job = None
            self._close_sheet()
            self.refresh()

        def on_error(exc: Exception) -> None:
            if not self._alive:
                return
            self._op_job = None
            if isinstance(exc, jobs.Cancelled):
                self._close_sheet()
                self.refresh()
            else:
                self._op_failed(exc)

        self._op_job = jobs.submit(
            lambda job: do_paste(job, items, cwd,
                                 on_progress=progress),
            on_done=on_done, on_error=on_error)

    def _show_progress(self, title: str) -> None:
        Gtk = self._Gtk
        box = self._open_sheet(title, modal=True)
        self._progress_bar = Gtk.ProgressBar()
        self._progress_bar.add_css_class("xx-files-dialog")
        self._progress_bar.set_margin_top(8)
        box.append(self._progress_bar)
        self._progress_label = self._label("", "xx-secondary", 28)
        self._progress_label.add_css_class("xx-files-dialog")
        self._progress_label.set_margin_top(8)
        box.append(self._progress_label)
        self._sheet_buttons(box, [("Cancel", self._cancel_op)])

    def _progress_cb(self, done: int, total: int) -> None:
        from .row import human_size

        def update() -> bool:
            if not self._alive:
                return False
            try:
                frac = (done / total) if total > 0 else 0.0
                self._progress_bar.set_fraction(min(1.0, max(0.0, frac)))
                text = f"{human_size(done)} of {human_size(total)}"
                self._progress_label.set_text(text)
                _cond_ellipsize(self._progress_label, self._Pango, text, 28)
            except Exception:
                pass
            return False

        try:
            self._GLib.idle_add(update)
        except Exception:
            pass

    def _cancel_op(self) -> None:
        if self._op_job is not None:
            self._op_job.cancel()

    def _restore_entry(self, trash_path: str) -> None:
        from .ops import do_restore

        info = trash_path + ".trashinfo"
        from .ops import trash_dirs

        _files, info_dir = trash_dirs()
        info_path = os.path.join(info_dir,
                                 os.path.basename(trash_path) + ".trashinfo")

        def find_info() -> str | None:
            for candidate in (info, info_path):
                if os.path.isfile(candidate):
                    return candidate
            return None

        self._run_op(lambda job: do_restore(job, trash_path, find_info()))

    def _delete_forever(self, trash_paths: list[str]) -> None:
        from .ops import do_delete_forever, trash_dirs

        _files, info_dir = trash_dirs()
        victims = list(trash_paths)
        for path in trash_paths:
            sidecar = os.path.join(info_dir,
                                   os.path.basename(path) + ".trashinfo")
            if os.path.isfile(sidecar):
                victims.append(sidecar)
        self._run_op(do_delete_forever, victims)

    def _empty_trash(self) -> None:
        from .ops import do_empty_trash

        self._run_op(lambda job: do_empty_trash(job))

    def _share_paths(self, paths: list[str]) -> None:
        from .ops import share_files

        error = share_files(paths)
        if error:
            self._show_note("Share", error)

    def present(self) -> None:
        self.win.present()

    # -- demo seeding (snaps) ---------------------------------------
    def seed_demo(self, scenario: str) -> None:
        home = self._home()
        self._refresh_roots()
        docs = os.path.join(home, "Documents")
        if scenario in ("blocked",):
            self.mode = "browse"
            self.cwd = os.path.join(home, "Documents", "notes.txt")
            self.root_label = "Home"
            self._render_chrome()
            self.refresh()
            return
        if scenario in ("empty_folder",):
            empty = os.path.join(home, "Documents", "Empty folder")
            self.open_path(empty)
            return
        if scenario in ("trash", "trash_empty", "trash_actions",
                        "progress"):
            if scenario == "trash_empty":
                self.trash_rows = []
                self.mode = "trash"
                self._render_chrome()
                self._render_rows()
            elif scenario == "progress":
                self.open_root_home()
                self.clip = [(os.path.join(docs, "report.pdf"), False)]
                self._show_progress("Pasting")
                self._progress_bar.set_fraction(0.45)
                self._progress_label.set_text("45.0 MB of 100.0 MB")
            else:
                self.show_trash()
                if scenario == "trash_actions":
                    from .ops import trash_dirs

                    files_dir, _info = trash_dirs()
                    self._show_trash_actions(
                        os.path.join(files_dir, "draft.txt"))
            return
        self.open_root_home()
        if scenario == "volumes":
            self.show_volumes()
        elif scenario == "tree":
            self.settings.view = "tree"
            for name in ("Documents", "Pictures"):
                candidate = os.path.join(home, name)
                if os.path.isdir(candidate):
                    self.expanded.add(candidate)
            self._render_chrome()
            self._render_rows()
        elif scenario == "grid":
            self.settings.view = "grid"
            self._render_chrome()
            self._render_rows()
        elif scenario == "deep":
            self.open_path(os.path.join(home, "Pictures", "Camera"))
        elif scenario == "search":
            self.open_path(os.path.join(home, "Pictures"))
            self.search_revealer.set_reveal_child(True)
            self.search_entry.set_text("pho")
            self.query = "pho"
            self._render_rows()
        elif scenario == "multiselect":
            self.open_path(os.path.join(home, "Pictures"))
            photos = [os.path.join(home, "Pictures", n)
                      for n in sorted(os.listdir(os.path.join(
                          home, "Pictures")))]
            self.selecting = True
            self.selection = set(photos[1:3])
            self._render_rows()
        elif scenario == "properties":
            self._show_properties(os.path.join(docs, "notes.txt"))
        elif scenario == "new_folder":
            self._open_new_folder_sheet()
        elif scenario == "rename":
            self._open_rename_sheet(os.path.join(docs, "notes.txt"))
        elif scenario == "delete_confirm":
            self._ask_delete([os.path.join(docs, "notes.txt")])
        elif scenario == "actions":
            self._show_file_actions(os.path.join(docs, "notes.txt"), False)
        elif scenario in ("sort_menu", "view"):
            self._show_view_sheet()

    def open_root_home(self) -> None:
        self.root_label = "Home"
        self.cwd = self._home()
        self.mode = "browse"
        self._clear_selection()
        self._render_chrome()
        self.refresh()

    def show_state(self, name: str) -> None:
        self.seed_demo(name)

    def _open_new_folder_sheet(self) -> None:
        box = self._open_sheet("New folder")
        self._sheet_entry(box, "New trip")
        self._sheet_buttons(box, [("Cancel", self._close_sheet),
                                  ("Create", self._close_sheet, "primary")])

    def _open_rename_sheet(self, path: str) -> None:
        box = self._open_sheet("Rename")
        self._sheet_entry(box, os.path.basename(path))
        self._sheet_buttons(box, [("Cancel", self._close_sheet),
                                  ("Rename", self._close_sheet, "primary")])


class FilesApp:
    def __init__(self) -> None:
        Adw, _Gdk, Gio, _GLib, _Gtk, _Pango = _require_gtk()
        from . import __app_id__

        self._app = Adw.Application(
            application_id=__app_id__,
            flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self._app.connect("activate", self._on_activate)
        self._app.connect("open", self._on_open)
        self.window = None

    def _on_activate(self, app) -> None:
        app._files_window = self.window
        if self.window is None:
            settings = load_settings()
            root = os.environ.get("XX_FILES_ROOT")
            self.window = FilesWindow(app, settings)
            app._files_window = self.window
            if root and os.path.isdir(root):
                self.window.open_path(root)
            scenario = os.environ.get("XX_FILES_SCENARIO", "")
            if scenario:
                self.window.seed_demo(scenario)
        self.window.present()

    def _on_open(self, app, files, _hint) -> None:
        self._on_activate(app)
        try:
            path = files[0].get_path()
        except Exception:
            path = None
        if path:
            if os.path.isfile(path):
                self.window._open_file(path)
            else:
                self.window.open_path(path)

    def run(self, argv=None) -> int:
        return self._app.run(argv if argv is not None else [])


def main(argv=None) -> int:
    args = list(argv) if argv is not None else None
    import sys

    if args is None:
        args = sys.argv[1:]
    app = FilesApp()
    return app.run(args)
