"""xx-files: native Linux file manager (Android xx-files port).

Volumes + tree/list/grid browsing, breadcrumbs, sort, search,
multi-select, copy/move/rename/delete (freedesktop Trash), new folder,
open-with, share via xx-share, properties, progress with cancel.

Headless-safe import: Gtk/Adw load lazily so pure tests never need a
display. Workers own every directory read and file operation; results
post back with GLib.idle_add.
"""
from __future__ import annotations

import os

from .prefs import Settings, load as load_settings, save as save_settings

APP_CSS = """
.xx-files-title { font-size: 20px; font-weight: 400; }
.xx-files-crumb { min-height: 44px; padding: 0 10px; }
.xx-files-row { min-height: 64px; padding: 6px 0; border-radius: 0; }
.xx-files-row-title { font-size: 16px; font-weight: 500; }
.xx-files-row-sub { font-size: 14px; }
.xx-files-grid-cell { min-width: 72px; padding: 8px 2px; }
.xx-files-view button { min-width: 0; padding-left: 10px; padding-right: 10px; }
.xx-files-grid-name { font-size: 13px; }
.xx-files-sheet { border-top: 1px solid @borders; padding: 12px 20px 20px; }
.xx-files-sheet-title { font-size: 18px; font-weight: 500; }
.xx-files-action { min-height: 48px; }
.xx-files-status { padding: 4px 20px 8px; }
"""


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

        self.win = Adw.ApplicationWindow(application=app)
        self.win.set_title("xx-files")
        self.win.set_default_size(360, 720)
        self._apply_theme()
        self._load_app_css()
        try:
            self._theme_unsub = xx_ui.on_change(lambda _t: self._apply_theme())
        except Exception:
            self._theme_unsub = None

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.add_css_class("xx-page-body")
        self.win.set_content(root)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        top.set_margin_start(12)
        top.set_margin_end(12)
        top.set_margin_top(12)
        self.back_btn = Gtk.Button.new_from_icon_name(
            "go-previous-symbolic")
        self.back_btn.add_css_class("xx-btn")
        self.back_btn.add_css_class("xx-btn-icon")
        self.back_btn.set_tooltip_text("Back")
        self.back_btn.connect("clicked", lambda _b: self.go_back())
        top.append(self.back_btn)
        title = Gtk.Label(label="xx-files")
        title.add_css_class("xx-files-title")
        title.add_css_class("xx-display")
        title.set_xalign(0.0)
        title.set_hexpand(True)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_max_width_chars(12)
        top.append(title)
        self.search_btn = Gtk.Button.new_from_icon_name(
            "system-search-symbolic")
        self.search_btn.add_css_class("xx-btn")
        self.search_btn.add_css_class("xx-btn-icon")
        self.search_btn.set_tooltip_text("Search")
        self.search_btn.connect("clicked",
                                lambda _b: self._toggle_search())
        top.append(self.search_btn)
        self.overflow_btn = Gtk.MenuButton()
        self.overflow_btn.add_css_class("xx-btn")
        self.overflow_btn.add_css_class("xx-btn-icon")
        self.overflow_btn.set_icon_name("view-more-symbolic")
        self.overflow_btn.set_tooltip_text("More actions")
        top.append(self.overflow_btn)
        root.append(top)

        self.crumb_scroll = Gtk.ScrolledWindow()
        self.crumb_scroll.set_policy(Gtk.PolicyType.AUTOMATIC,
                                     Gtk.PolicyType.NEVER)
        self.crumb_scroll.set_margin_start(12)
        self.crumb_scroll.set_margin_end(12)
        self.crumb_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                 spacing=2)
        self.crumb_scroll.set_child(self.crumb_box)
        root.append(self.crumb_scroll)

        self.search_revealer = Gtk.Revealer()
        self.search_revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search this folder")
        self.search_entry.set_margin_start(20)
        self.search_entry.set_margin_end(20)
        self.search_entry.set_margin_top(4)
        self.search_entry.connect("search-changed",
                                  self._on_search_changed)
        _tap_to_focus(self.search_entry)
        self.search_revealer.set_child(self.search_entry)
        root.append(self.search_revealer)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.set_margin_start(20)
        toolbar.set_margin_end(20)
        toolbar.set_margin_top(4)
        self.view_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                spacing=0)
        self.view_box.add_css_class("linked")
        self.view_box.add_css_class("xx-files-view")
        self.view_btns: dict[str, object] = {}
        for key, tip in (("list", "List"), ("tree", "Tree"),
                         ("grid", "Grid")):
            btn = Gtk.ToggleButton(label=tip)
            btn.set_tooltip_text(tip)
            btn.connect("toggled", self._on_view_toggled, key)
            self.view_box.append(btn)
            self.view_btns[key] = btn
        toolbar.append(self.view_box)
        root.append(toolbar)

        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER,
                               Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_vexpand(True)
        self.scroll.set_margin_top(4)
        self.list_box = Gtk.ListBox()
        self.list_box.add_css_class("xx-list")
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list_box.connect("row-activated", self._on_row_activated)
        self.scroll.set_child(self.list_box)
        root.append(self.scroll)

        self.grid_scroll = Gtk.ScrolledWindow()
        self.grid_scroll.set_policy(Gtk.PolicyType.NEVER,
                                    Gtk.PolicyType.AUTOMATIC)
        self.grid_scroll.set_vexpand(True)
        self.flow = Gtk.FlowBox()
        self.flow.set_valign(Gtk.Align.START)
        self.flow.set_max_children_per_line(3)
        self.flow.set_min_children_per_line(2)
        self.flow.set_column_spacing(6)
        self.flow.set_row_spacing(6)
        self.flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.flow.connect("child-activated", self._on_grid_activated)
        self.grid_scroll.set_child(self.flow)
        self.grid_scroll.set_visible(False)
        root.append(self.grid_scroll)

        self.status = Gtk.Label(label="")
        self.status.add_css_class("xx-caption")
        self.status.set_xalign(0.0)
        self.status.set_ellipsize(Pango.EllipsizeMode.NONE)
        self.status.set_max_width_chars(32)
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        status_box.add_css_class("xx-files-status")
        status_box.append(self.status)
        root.append(status_box)

        self.sel_revealer = Gtk.Revealer()
        self.sel_revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_UP)
        sel_bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        sel_bar.set_margin_start(20)
        sel_bar.set_margin_end(20)
        sel_bar.set_margin_bottom(12)
        sel_top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.sel_label = Gtk.Label(label="")
        self.sel_label.add_css_class("xx-secondary")
        self.sel_label.set_xalign(0.0)
        self.sel_label.set_hexpand(True)
        self.sel_label.set_ellipsize(Pango.EllipsizeMode.NONE)
        self.sel_label.set_max_width_chars(20)
        sel_top.append(self.sel_label)
        cancel = Gtk.Button.new_with_label("Done")
        cancel.connect("clicked", lambda _b: self._clear_selection())
        sel_top.append(cancel)
        sel_bar.append(sel_top)
        sel_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                              spacing=4)
        sel_actions.set_halign(Gtk.Align.CENTER)
        sel_actions.set_homogeneous(True)
        for name, tip, cb in (
                ("edit-copy-symbolic", "Copy",
                 lambda: self._stage_selection(False)),
                ("edit-cut-symbolic", "Cut",
                 lambda: self._stage_selection(True)),
                ("user-trash-symbolic", "Delete",
                 lambda: self._ask_delete(sorted(self.selection))),
                ("send-to-symbolic", "Share",
                 lambda: self._share_paths(sorted(self.selection)))):
            btn = Gtk.Button.new_from_icon_name(name)
            btn.add_css_class("xx-btn")
            btn.add_css_class("xx-btn-icon")
            btn.set_tooltip_text(tip)
            btn.connect("clicked", lambda _b, f=cb: f())
            sel_actions.append(btn)
        sel_bar.append(sel_actions)
        self.sel_revealer.set_child(sel_bar)
        root.append(self.sel_revealer)

        self.sheet_revealer = Gtk.Revealer()
        self.sheet_revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_UP)
        self.sheet_scroll = Gtk.ScrolledWindow()
        self.sheet_scroll.set_policy(Gtk.PolicyType.NEVER,
                                     Gtk.PolicyType.AUTOMATIC)
        self.sheet_scroll.set_propagate_natural_height(True)
        try:
            self.sheet_scroll.set_max_content_height(320)
        except Exception:
            pass
        self.sheet_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                 spacing=8)
        self.sheet_box.add_css_class("xx-files-sheet")
        self.sheet_scroll.set_child(self.sheet_box)
        self.sheet_revealer.set_child(self.sheet_scroll)
        root.append(self.sheet_revealer)

        self._build_menus()
        self._refresh_roots()
        self._watch_volumes()
        self._render_chrome()
        self.refresh()

    # -- menus ------------------------------------------------------
    def _build_menus(self) -> None:
        Gio = self._Gio
        menu = Gio.Menu()
        menu.append("New folder", "app.new-folder")
        menu.append("Paste", "app.paste")
        menu.append("Trash", "app.trash")
        menu.append("Volumes", "app.volumes")
        self.overflow_btn.set_menu_model(menu)
        app = self.win.get_application()
        for name, cb in (("new-folder", lambda *_: self._ask_new_folder()),
                         ("paste", lambda *_: self._paste()),
                         ("trash", lambda *_: self.show_trash()),
                         ("volumes", lambda *_: self.show_volumes())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", cb)
            app.add_action(action)
        show = Gio.SimpleAction.new_stateful(
            "show-hidden", None,
            self._GLib.Variant.new_boolean(self.settings.show_hidden))
        show.connect("activate", self._on_show_hidden)
        app.add_action(show)
        menu.append_item(Gio.MenuItem.new("Show hidden", "app.show-hidden"))
        sort_menu = Gio.Menu()
        for key, label in (("name", "Name"), ("size", "Size"),
                           ("date", "Date")):
            sort_menu.append_item(Gio.MenuItem.new(label, f"app.sort::{key}"))
        sort_action = Gio.SimpleAction.new_stateful(
            "sort", self._GLib.VariantType.new("s"),
            self._GLib.Variant.new_string(self.settings.sort))
        sort_action.connect("activate", self._on_sort)
        app.add_action(sort_action)
        menu.append_submenu("Sort", sort_menu)

    def _on_show_hidden(self, action, _param) -> None:
        value = not action.get_state().get_boolean()
        action.set_state(self._GLib.Variant.new_boolean(value))
        self.settings.show_hidden = value
        save_settings(self.settings)
        self.refresh()

    def _on_sort(self, action, param) -> None:
        action.set_state(param)
        self.settings.sort = param.get_string()
        save_settings(self.settings)
        self.refresh()

    def _on_view_toggled(self, btn, key: str) -> None:
        if not btn.get_active():
            return
        for other_key, other in self.view_btns.items():
            if other_key != key and other.get_active():
                other.set_active(False)
        if self.settings.view != key:
            self.settings.view = key
            save_settings(self.settings)
            self._render_chrome()
            self._render_rows()

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
                    self._Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except Exception:
            pass

    # -- navigation -------------------------------------------------
    def _refresh_roots(self) -> None:
        from .ops import discover_roots, removable_mounts

        home = os.environ.get("XX_FILES_HOME") or os.path.expanduser("~")
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
        self._render_chrome()
        self._render_rows()

    def show_trash(self) -> None:
        self.mode = "trash"
        self._clear_selection()
        self._render_chrome()
        self.refresh_trash()

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
        self._set_status("Loading…")
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

        self._set_status("Loading…")
        files_dir, info_dir = trash_dirs()

        def on_done(entries: list) -> None:
            if not self._alive:
                return
            self.trash_rows = entries
            self._render_rows()

        def on_error(_exc: Exception) -> None:
            if not self._alive:
                return
            self.trash_rows = []
            self._render_rows()

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
        for index, label in enumerate(crumbs):
            if index:
                sep = Gtk.Image.new_from_icon_name("go-next-symbolic")
                sep.set_pixel_size(16)
                sep.set_valign(Gtk.Align.CENTER)
                self.crumb_box.append(sep)
            btn = Gtk.Button.new_with_label(label)
            btn.add_css_class("xx-files-crumb")
            btn.connect("clicked", self._on_crumb, index)
            child = btn.get_child()
            if child is not None:
                try:
                    _cond_ellipsize(child, self._Pango, label, 10)
                except Exception:
                    pass
            self.crumb_box.append(btn)
        for key, btn in self.view_btns.items():
            if btn.get_active() != (self.settings.view == key):
                btn.set_active(self.settings.view == key)
        browsing = self.mode == "browse"
        grid = browsing and self.settings.view == "grid"
        self.scroll.set_visible(not grid)
        self.grid_scroll.set_visible(grid)
        self.view_box.set_visible(browsing)
        try:
            adj = self.crumb_scroll.get_hadjustment()
            adj.set_value(adj.get_upper())
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

        self.open_path(navigate_to(self.cwd, self._current_root(), index))

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
        self._set_status("Loading…")
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
            self._render_volume_rows()
        elif self.mode == "trash":
            self._render_trash_rows()
        elif self.settings.view == "grid":
            for item in self._visible_rows():
                self.flow.append(self._grid_cell(item))
            self._update_status_grid()
        elif self.settings.view == "tree":
            nodes = self._tree_nodes()
            if not nodes and self.query:
                self.list_box.append(self._message_row("No matches."))
            for item in nodes:
                self.list_box.append(self._browse_row(item))
            self._update_status_list(len(nodes))
        else:
            items = self._visible_rows()
            if self._at_volume_root():
                self.list_box.append(self._volumes_row())
            if not items and self._list_error.get("status") == "ok":
                self.list_box.append(self._message_row(
                    "No matches." if self.query else "Empty."))
            elif self._list_error.get("status") == "blocked":
                from .directory_contents import BLOCKED_COPY

                self.list_box.append(self._message_row(BLOCKED_COPY))
            elif self._list_error.get("status") == "failed":
                self.list_box.append(self._message_row(
                    self._list_error.get("message", "Could not open")))
            for item in items:
                self.list_box.append(self._browse_row(item))
            self._update_status_list(len(items))
        self._update_selection_bar()

    def _at_volume_root(self) -> bool:
        from .volumes import is_volume_root

        return is_volume_root(self.cwd, [r.root_path for r in self.roots])

    def _message_row(self, text: str):
        Gtk, Pango = self._Gtk, self._Pango
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        label = Gtk.Label(label=text)
        label.add_css_class("xx-secondary")
        label.set_xalign(0.0)
        label.set_margin_start(20)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(28)
        row.set_child(label)
        return row

    def _volumes_row(self):
        Gtk = self._Gtk
        row = Gtk.ListBoxRow()
        row.set_name("↑ Volumes")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_start(20)
        box.set_margin_end(20)
        label = Gtk.Label(label="↑ Volumes")
        label.add_css_class("xx-files-row-title")
        label.set_xalign(0.0)
        label.set_hexpand(True)
        label.set_ellipsize(self._Pango.EllipsizeMode.NONE)
        label.set_max_width_chars(20)
        box.append(label)
        row.set_child(box)
        return row

    def _render_volume_rows(self) -> None:
        for vol in self.roots:
            self.list_box.append(self._volume_row(vol))
        self._set_status(f"{len(self.roots)} locations")

    def _volume_row(self, vol) -> object:
        Gtk, Pango = self._Gtk, self._Pango
        row = Gtk.ListBoxRow()
        row.set_name("vol:" + vol.root_path)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_start(20)
        box.set_margin_end(20)
        icon = ("drive-harddisk-symbolic" if vol.is_primary
                else "drive-removable-media-symbolic")
        img = Gtk.Image.new_from_icon_name(icon)
        img.set_pixel_size(24)
        box.append(img)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        texts.set_hexpand(True)
        title = Gtk.Label(label=vol.label)
        title.add_css_class("xx-files-row-title")
        title.set_xalign(0.0)
        _cond_ellipsize(title, Pango, vol.label, 20)
        texts.append(title)
        sub = Gtk.Label(label=vol.root_path)
        sub.add_css_class("xx-secondary")
        sub.add_css_class("xx-files-row-sub")
        sub.set_xalign(0.0)
        _cond_ellipsize(sub, Pango, vol.root_path, 24)
        texts.append(sub)
        box.append(texts)
        row.set_child(box)
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
        Gtk, Pango = self._Gtk, self._Pango
        from .row import icon_for_kind

        entry = item["entry"]
        row = Gtk.ListBoxRow()
        row.set_name("file:" + entry.path)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_start(20)
        box.set_margin_end(20)
        if item["depth"]:
            pad = Gtk.Label(label=" " * (item["depth"] * 2))
            pad.add_css_class("xx-secondary")
            box.append(pad)
        if self.selecting:
            check = Gtk.CheckButton()
            check.set_active(entry.path in self.selection)
            check.set_can_focus(False)
            box.append(check)
        img = Gtk.Image.new_from_icon_name(
            icon_for_kind(item["kind"],
                          bool(item.get("expanded"))))
        img.set_pixel_size(24)
        box.append(img)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        texts.set_hexpand(True)
        shown = item["guide"] + entry.name
        title = Gtk.Label(label=shown)
        title.add_css_class("xx-files-row-title")
        title.set_xalign(0.0)
        _cond_ellipsize(title, Pango, shown, 20)
        texts.append(title)
        second = self._second_line(item)
        sub = Gtk.Label(label=second)
        sub.add_css_class("xx-secondary")
        sub.add_css_class("xx-files-row-sub")
        sub.set_xalign(0.0)
        _cond_ellipsize(sub, Pango, second, 24)
        texts.append(sub)
        box.append(texts)
        if item.get("expanded") is not None:
            exp = Gtk.Image.new_from_icon_name(
                "go-down-symbolic" if item["expanded"]
                else "go-next-symbolic")
            exp.set_pixel_size(20)
            box.append(exp)
        row.set_child(box)
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
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("xx-files-grid-cell")
        box.set_size_request(100, -1)
        img = Gtk.Image.new_from_icon_name(icon_for_kind(item["kind"]))
        img.set_pixel_size(44)
        img.set_halign(Gtk.Align.CENTER)
        box.append(img)
        label = Gtk.Label(label=entry.name)
        label.add_css_class("xx-files-grid-name")
        label.set_xalign(0.5)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_ellipsize(Pango.EllipsizeMode.NONE)
        label.set_max_width_chars(6)
        box.append(label)
        cell.set_child(box)
        long_press = Gtk.GestureLongPress.new()
        long_press.connect("pressed", self._on_long_press, entry.path,
                           entry.is_dir)
        cell.add_controller(long_press)
        return cell

    def _render_trash_rows(self) -> None:
        for entry in self.trash_rows:
            self.list_box.append(self._trash_row(entry))
        if not self.trash_rows:
            self.list_box.append(self._message_row(
                "Empty. Deleted files stay here until you remove them."))
            self._set_status("Trash is empty")
        else:
            self._set_status(f"{len(self.trash_rows)} in Trash")

    def _trash_row(self, entry) -> object:
        Gtk, Pango = self._Gtk, self._Pango
        row = Gtk.ListBoxRow()
        row.set_name("trash:" + entry.trash_path)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_start(20)
        box.set_margin_end(20)
        img = Gtk.Image.new_from_icon_name("user-trash-symbolic")
        img.set_pixel_size(24)
        box.append(img)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        texts.set_hexpand(True)
        title = Gtk.Label(label=entry.display_name)
        title.add_css_class("xx-files-row-title")
        title.set_xalign(0.0)
        _cond_ellipsize(title, Pango, entry.display_name, 20)
        texts.append(title)
        origin = entry.original_path or "Original unknown"
        sub = Gtk.Label(label=origin)
        sub.add_css_class("xx-secondary")
        sub.add_css_class("xx-files-row-sub")
        sub.set_xalign(0.0)
        _cond_ellipsize(sub, Pango, origin, 24)
        texts.append(sub)
        box.append(texts)
        row.set_child(box)
        long_press = Gtk.GestureLongPress.new()
        long_press.connect("pressed", self._on_trash_long_press,
                           entry.trash_path)
        row.add_controller(long_press)
        return row

    def _update_status_list(self, count: int) -> None:
        if self._list_error.get("status") != "ok":
            self._set_status("Could not read this folder")
            return
        noun = "item" if count == 1 else "items"
        hidden = " · hidden shown" if self.settings.show_hidden else ""
        self._set_status(f"{count} {noun}{hidden}")

    def _update_status_grid(self) -> None:
        self._update_status_list(len(self._visible_rows()))

    # -- activation -------------------------------------------------
    def _row_target(self, row) -> tuple[str, bool] | None:
        name = row.get_name() or ""
        if name == "↑ Volumes":
            self.show_volumes()
            return None
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

    def _toggle_selected(self, path: str) -> None:
        if path in self.selection:
            self.selection.discard(path)
        else:
            self.selection.add(path)
        if not self.selection:
            self.selecting = False
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
            n = len(self.selection)
            text = f"{n} selected"
            self.sel_label.set_text(text)
            _cond_ellipsize(self.sel_label, self._Pango, text, 20)

    def _set_status(self, text: str) -> None:
        self.status.set_text(text)
        _cond_ellipsize(self.status, self._Pango, text, 32)

    def _stage_selection(self, is_cut: bool) -> None:
        self.clip = [(p, is_cut) for p in sorted(self.selection)]
        verb = "Cut" if is_cut else "Copied"
        n = len(self.clip)
        self._clear_selection()
        self._render_rows()
        self._set_status(f"{verb} {n} for paste")

    # -- bottom sheets ----------------------------------------------
    def _open_sheet(self, title: str) -> object:
        Gtk = self._Gtk
        while (child := self.sheet_box.get_first_child()) is not None:
            self.sheet_box.remove(child)
        head = Gtk.Label(label=title)
        head.add_css_class("xx-files-sheet-title")
        head.set_xalign(0.0)
        _cond_ellipsize(head, self._Pango, title, 24)
        self.sheet_box.append(head)
        self.sheet_revealer.set_reveal_child(True)
        return self.sheet_box

    def _close_sheet(self) -> None:
        self.sheet_revealer.set_reveal_child(False)

    def _sheet_buttons(self, box, labels: list[tuple[str, object]]) -> None:
        Gtk = self._Gtk
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_halign(Gtk.Align.END)
        for label, cb in labels:
            btn = Gtk.Button.new_with_label(label)
            btn.add_css_class("xx-btn")
            btn.connect("clicked", lambda _b, f=cb: f())
            try:
                child = btn.get_child()
                if child is not None:
                    child.set_ellipsize(self._Pango.EllipsizeMode.NONE)
                    child.set_max_width_chars(16)
            except Exception:
                pass
            row.append(btn)
        box.append(row)

    def _sheet_entry(self, box, initial: str = ""):
        Gtk = self._Gtk
        entry = Gtk.Entry()
        entry.set_text(initial)
        entry.set_hexpand(True)
        box.append(entry)
        return entry

    def _show_note(self, title: str, body: str) -> None:
        box = self._open_sheet(title)
        label = self._Gtk.Label(label=body)
        label.add_css_class("xx-secondary")
        label.set_xalign(0.0)
        label.set_wrap(True)
        label.set_wrap_mode(self._Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(28)
        box.append(label)
        self._sheet_buttons(box, [("OK", self._close_sheet)])

    def _ask_new_folder(self) -> None:
        if self.mode != "browse":
            self._show_note("New folder", "Open a folder first.")
            return
        box = self._open_sheet("New folder")
        entry = self._sheet_entry(box)

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
            self._set_status("Creating…")
            jobs.submit(do_mkdir, target,
                        on_done=lambda _p: self.refresh(),
                        on_error=lambda e: self._op_failed(e))

        self._sheet_buttons(box, [("Cancel", self._close_sheet),
                                  ("Create", save)])
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
            self._run_op(do_rename, path, target,
                         done_text="Renamed")

        self._sheet_buttons(box, [("Cancel", self._close_sheet),
                                  ("Rename", save)])
        entry.grab_focus()

    def _confirm_overwrite(self, source: str, target: str) -> None:
        box = self._open_sheet("Replace file?")
        label = self._Gtk.Label(
            label=f"{os.path.basename(target)} already exists. "
                  "Replace it?")
        label.add_css_class("xx-secondary")
        label.set_xalign(0.0)
        label.set_wrap(True)
        label.set_wrap_mode(self._Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(28)
        box.append(label)

        def replace() -> None:
            from . import jobs
            from .ops import _remove, do_rename

            self._close_sheet()
            jobs.submit(lambda job: (_remove(target),
                                     do_rename(job, source, target))[1],
                        on_done=lambda _p: self.refresh(),
                        on_error=lambda e: self._op_failed(e))

        self._sheet_buttons(box, [("Cancel", self._close_sheet),
                                  ("Replace", replace)])

    def _ask_delete(self, paths: list[str]) -> None:
        n = len(paths)
        box = self._open_sheet("Move to Trash?")
        label = self._Gtk.Label(
            label=f"{n} item{'s' if n != 1 else ''} will go to Trash.")
        label.add_css_class("xx-secondary")
        label.set_xalign(0.0)
        label.set_wrap(True)
        label.set_wrap_mode(self._Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(28)
        box.append(label)

        def delete() -> None:
            from . import jobs
            from .ops import do_trash

            self._close_sheet()
            self._clear_selection()
            self._set_status("Moving to Trash…")
            jobs.submit(do_trash, paths,
                        on_done=lambda _r: self.refresh(),
                        on_error=lambda e: self._op_failed(e))

        self._sheet_buttons(box, [("Cancel", self._close_sheet),
                                  ("Move to Trash", delete)])

    def _show_properties(self, path: str) -> None:
        from .mime import from_extension
        from .row import human_size, iso_date

        box = self._open_sheet(os.path.basename(path) or path)
        lines: list[str] = []
        try:
            stat = os.stat(path)
            lines.append(f"Size  {human_size(stat.st_size)}")
            lines.append(f"Modified  {iso_date(int(stat.st_mtime * 1000))}")
        except OSError:
            pass
        if not os.path.isdir(path):
            lines.append(f"Type  {from_extension(path)}")
        else:
            count, _hidden = self._count_now(path)
            lines.append(f"Contents  {count} items")
        path_label = self._Gtk.Label(label=path)
        path_label.add_css_class("xx-secondary")
        path_label.set_xalign(0.0)
        path_label.set_wrap(True)
        path_label.set_wrap_mode(self._Pango.WrapMode.WORD_CHAR)
        path_label.set_max_width_chars(28)
        box.append(path_label)
        for line in lines:
            row = self._Gtk.Label(label=line)
            row.add_css_class("xx-secondary")
            row.set_xalign(0.0)
            _cond_ellipsize(row, self._Pango, line, 28)
            box.append(row)
        self._sheet_buttons(box, [("Close", self._close_sheet)])

    def _count_now(self, path: str) -> tuple[int, int]:
        from .ops import count_visible

        return count_visible(path, self.settings.show_hidden)

    def _show_file_actions(self, path: str, is_dir: bool) -> None:
        from .menu_actions import for_type

        box = self._open_sheet(os.path.basename(path))
        for action in for_type(is_dir):
            btn = self._Gtk.Button.new_with_label(action.value.capitalize())
            btn.add_css_class("xx-files-action")
            btn.connect("clicked", self._on_file_action, action, path)
            box.append(btn)
        self._sheet_buttons(box, [("Cancel", self._close_sheet),
                                  ("Select", lambda: (
                                      self._close_sheet(),
                                      self._on_long_press(None, 0, 0,
                                                          path, is_dir)))])

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
            self._set_status("Copied 1 for paste")
        elif action is Action.CUT:
            self.clip = [(path, True)]
            self._set_status("Cut 1 for paste")
        elif action is Action.RENAME:
            self._ask_rename(path)
        elif action is Action.DELETE:
            self._ask_delete([path])
        elif action is Action.PROPERTIES:
            self._show_properties(path)

    def _show_trash_actions(self, trash_path: str) -> None:
        box = self._open_sheet(os.path.basename(trash_path))
        restore = self._Gtk.Button.new_with_label("Restore")
        restore.add_css_class("xx-files-action")
        restore.connect("clicked", lambda _b: (
            self._close_sheet(), self._restore_entry(trash_path)))
        box.append(restore)
        forever = self._Gtk.Button.new_with_label("Delete forever")
        forever.add_css_class("xx-files-action")
        forever.connect("clicked", lambda _b: (
            self._close_sheet(), self._delete_forever([trash_path])))
        box.append(forever)
        self._sheet_buttons(box, [("Cancel", self._close_sheet),
                                  ("Empty Trash",
                                   lambda: (self._close_sheet(),
                                            self._empty_trash()))])

    # -- operations -------------------------------------------------
    def _run_op(self, func, *args, done_text: str = "Done") -> None:
        from . import jobs

        if self._op_job is not None:
            self._op_job.cancel()
        self._set_status("Working…")

        def on_done(_result) -> None:
            if not self._alive:
                return
            self._op_job = None
            self._close_sheet()
            self._set_status(done_text)
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

        if self.mode != "browse":
            self._show_note("Paste", "Open a folder first.")
            return
        if not can_paste(self.clip[0] if self.clip else None,
                         os.access(self.cwd, os.W_OK)):
            if not self.clip:
                self._show_note("Paste", "Nothing copied yet.")
            else:
                self._show_note("Paste", "This folder is read-only.")
            return
        items = list(self.clip)
        cwd = self.cwd
        self.clip = []
        self._show_progress("Pasting…")
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
                self._set_status("Cancelled")
                self.refresh()
            else:
                self._op_failed(exc)

        self._op_job = jobs.submit(
            lambda job: do_paste(job, items, cwd,
                                 on_progress=progress),
            on_done=on_done, on_error=on_error)

    def _show_progress(self, title: str) -> None:
        Gtk = self._Gtk
        box = self._open_sheet(title)
        self._progress_bar = Gtk.ProgressBar()
        self._progress_bar.set_show_text(True)
        box.append(self._progress_bar)
        self._progress_label = Gtk.Label(label="Starting…")
        self._progress_label.add_css_class("xx-secondary")
        self._progress_label.set_xalign(0.0)
        self._progress_label.set_ellipsize(self._Pango.EllipsizeMode.NONE)
        self._progress_label.set_max_width_chars(28)
        box.append(self._progress_label)
        cancel = Gtk.Button.new_with_label("Cancel")
        cancel.connect("clicked", lambda _b: self._cancel_op())
        cancel_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        cancel_box.set_halign(Gtk.Align.END)
        cancel_box.append(cancel)
        box.append(cancel_box)

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

        self._set_status("Restoring…")
        self._run_op(lambda job: do_restore(job, trash_path, find_info()),
                     done_text="Restored")

    def _delete_forever(self, trash_paths: list[str]) -> None:
        from .ops import do_delete_forever, trash_dirs

        _files, info_dir = trash_dirs()
        victims = list(trash_paths)
        for path in trash_paths:
            sidecar = os.path.join(info_dir,
                                   os.path.basename(path) + ".trashinfo")
            if os.path.isfile(sidecar):
                victims.append(sidecar)
        self._run_op(do_delete_forever, victims, done_text="Deleted")

    def _empty_trash(self) -> None:
        from .ops import do_empty_trash

        self._set_status("Emptying Trash…")
        self._run_op(lambda job: do_empty_trash(job),
                     done_text="Trash is empty")

    def _share_paths(self, paths: list[str]) -> None:
        from .ops import share_files

        error = share_files(paths)
        if error:
            self._show_note("Share", error)

    def present(self) -> None:
        self.win.present()

    # -- demo seeding (snaps) ---------------------------------------
    def seed_demo(self, scenario: str) -> None:
        home = os.environ.get("XX_FILES_HOME") or os.path.expanduser("~")
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
        if scenario in ("trash", "trash_empty", "progress"):
            if scenario == "trash_empty":
                self.trash_rows = []
                self.mode = "trash"
                self._render_chrome()
                self._render_rows()
            elif scenario == "progress":
                self.open_root_home()
                self._show_progress("Pasting…")
                self._progress_bar.set_fraction(0.45)
                self._progress_label.set_text("45.0 MB of 100.0 MB")
            else:
                self.show_trash()
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
        elif scenario == "search":
            self.search_revealer.set_reveal_child(True)
            self.search_entry.set_text("pho")
            self.query = "pho"
            self._render_rows()
        elif scenario == "multiselect":
            photos = [os.path.join(home, "Pictures", n)
                      for n in sorted(os.listdir(os.path.join(
                          home, "Pictures")))]
            self.selecting = True
            self.selection = set(photos[:2])
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
        elif scenario == "sort_menu":
            self._open_sort_sheet()

    def open_root_home(self) -> None:
        home = os.environ.get("XX_FILES_HOME") or os.path.expanduser("~")
        self.root_label = "Home"
        self.cwd = home
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
                                  ("Create", self._close_sheet)])

    def _open_rename_sheet(self, path: str) -> None:
        box = self._open_sheet("Rename")
        self._sheet_entry(box, os.path.basename(path))
        self._sheet_buttons(box, [("Cancel", self._close_sheet),
                                  ("Rename", self._close_sheet)])

    def _open_sort_sheet(self) -> None:
        Gtk = self._Gtk
        box = self._open_sheet("Sort by")
        current = self.settings.sort
        for key, label in (("name", "Name"), ("size", "Size"),
                           ("date", "Date")):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            mark = Gtk.CheckButton()
            mark.set_active(key == current)
            mark.set_sensitive(False)
            mark.set_can_focus(False)
            row.append(mark)
            text = Gtk.Label(label=label)
            text.set_xalign(0.0)
            text.set_hexpand(True)
            row.append(text)
            box.append(row)
        self._sheet_buttons(box, [("Close", self._close_sheet)])


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
