"""Main window: notes grid, labels, archive, trash, settings, sync, editor.

One window, one ``Gtk.Stack`` of pages, four caps bottom tabs (Notes,
Labels, Archive, Trash) under an xx_ui page header whose icons open
search, Sync and Settings; Settings, Sync and the editor push on top with
their own back bar. Notes is the Android grid: a two-column staggered
card grid with Pinned/Others, and a capture bar instead of a FAB (type a
title and press Enter, or tap the checklist box). Long-press selects;
the header turns into the selection bar. Escape (or a back key) steps
back: selection, then search, then a pushed page. Empty lists show
nothing. The search and capture entries stay unfocusable until tapped so
the on-screen keyboard never pops on launch. All disk reads ride a worker thread.
"""

from __future__ import annotations

import threading

from xx_ui import widgets as xw

from xx_note import labels as _labels
from xx_note import note_colors as _colors
from xx_note import preview as _preview
from xx_note import store as _store
from xx_note import trash as _trash
from xx_note.ui import editor as _editor
from xx_note.ui import settings_view as _settings_view

TABS = ("notes", "labels", "archive", "trash")
TAB_TITLES = {"notes": "Notes", "labels": "Labels", "archive": "Archive",
              "trash": "Trash"}
CARD_CHIPS = 2


def _plural(count: int, word: str) -> str:
    if count <= 0:
        return ""
    return f"{count} {word}{'' if count == 1 else 's'}"


class NoteWindow:
    def __init__(self, app, vault, settings, secrets, Adw, Gtk, Pango, GLib):  # type: ignore[no-untyped-def]
        self._Gtk = Gtk
        self._Pango = Pango
        self._GLib = GLib
        self._vault = vault
        self._settings = settings
        self._secrets = secrets
        self._notes: list[_store.Note] = []
        self._trash_notes: list[_store.Note] = []
        self._selected: set[str] = set()
        self._selecting = False
        self._label_filter: str | None = None
        self._query = ""
        self._search_open = False
        self._page = "notes"
        self._tab = "notes"

        self.win = Adw.ApplicationWindow(application=app)
        self.win.set_title("xx-note")
        self.win.set_default_size(360, 720)
        # Adw windows floor at 360 px wide by default: a 320 px
        # screen would cut the app off.
        self.win.set_size_request(320, 200)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.add_css_class("xx-note-root")
        root.add_css_class("xx-page")
        self.win.set_content(root)

        # Header: page title + count caption + icons, or the selection bar.
        self.header = Gtk.Stack()
        self.header.set_transition_type(Gtk.StackTransitionType.NONE)
        self.header.set_hhomogeneous(True)
        self.header.set_vhomogeneous(False)
        root.append(self.header)

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        head.add_css_class("xx-page-body")
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        top.add_css_class("xx-page-top")
        self.title_label = xw.label("Notes", "xx-display")
        self.title_label.set_max_width_chars(8)
        self.title_label.set_hexpand(True)
        self.title_label.set_valign(Gtk.Align.CENTER)
        top.append(self.title_label)
        self.search_button = self._icon("system-search-symbolic",
                                        self.toggle_search, "Search")
        top.append(self.search_button)
        self.sync_button = self._icon("view-refresh-symbolic",
                                      lambda: self.show_page("sync"), "Sync")
        top.append(self.sync_button)
        self.gear_button = self._icon("emblem-system-symbolic",
                                      lambda: self.show_page("settings"),
                                      "Settings")
        top.append(self.gear_button)
        head.append(top)
        self.caption = xw.caption("")
        head.append(self.caption)
        self.header.add_named(head, "title")

        # Android's SelectionBar: a tight 4 px edge and flush icon targets,
        # so "N selected" keeps its words at 320 px and at large text.
        select = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        select.add_css_class("xx-note-select")
        select.add_css_class("xx-page-top")
        select.append(self._icon("window-close-symbolic",
                                 lambda: self._bulk("clear"), "Done"))
        self.select_label = xw.label("", "xx-title")
        self.select_label.set_hexpand(True)
        self.select_label.set_margin_start(4)
        self.select_label.set_valign(Gtk.Align.CENTER)
        select.append(self.select_label)
        select.append(self._icon("view-pin-symbolic",
                                 lambda: self._bulk("pin"), "Pin"))
        select.append(self._icon("", lambda: self._bulk("archive"), "Archive",
                                 gicon=_editor._archive_icon()))
        select.append(self._icon("user-trash-symbolic",
                                 lambda: self._bulk("trash"), "Trash"))
        self.header.add_named(select, "select")

        # Search (opened from the header icon) and the label filter chip.
        self.search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.search_box.add_css_class("xx-note-search")
        self.search_box.add_css_class("xx-searchbar-boxed")
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Search notes")
        self.search.set_hexpand(True)
        self.search.set_can_focus(False)
        self.search.set_max_width_chars(16)
        self.search.connect("search-changed", self._on_search)
        press = Gtk.GestureClick.new()
        press.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        press.connect("pressed", self._on_search_tapped)
        self.search.add_controller(press)
        self.search_box.append(self.search)
        self.search_box.set_visible(False)
        root.append(self.search_box)

        self.filter_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.filter_box.add_css_class("xx-note-filter")
        self.filter_chip = Gtk.Button()
        self.filter_chip.add_css_class("xx-chip")
        self.filter_chip.add_css_class("selected")
        self.filter_chip.add_css_class("xx-note-filter-chip")
        self.filter_chip.set_can_focus(False)
        chip_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.filter_text = Gtk.Label(label="")
        chip_row.append(self.filter_text)
        chip_row.append(xw.icon_image("window-close-symbolic", 16))
        self.filter_chip.set_child(chip_row)
        self.filter_chip.connect("clicked", lambda _b: self._filter_label(None))
        self.filter_box.append(self.filter_chip)
        self.filter_box.set_visible(False)
        root.append(self.filter_box)

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.set_hexpand(True)
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        root.append(self.stack)

        self.notes_list = self._add_list_page("notes")
        self.labels_list = self._add_list_page("labels")
        self.archive_list = self._add_list_page("archive")
        self.trash_list = self._add_list_page("trash")

        self.settings_page = _settings_view.SettingsPage(
            Gtk, Pango, GLib, settings,
            on_settings_changed=self._on_settings_changed,
        )
        self.settings_page.back_button.connect("clicked", lambda _b: self.go_back())
        self.stack.add_named(self.settings_page.box, "settings")

        self.sync_page = _settings_view.SyncPage(
            Gtk, Pango, GLib, settings, secrets, lambda: self._vault
        )
        self.sync_page.back_button.connect("clicked", lambda _b: self.go_back())
        self.stack.add_named(self.sync_page.box, "sync")

        self.editor_page = _editor.EditorPage(
            Gtk, Pango, GLib, on_saved=self._on_editor_saved,
            on_trashed=self._after_bulk,
        )
        self.editor_page.bind(vault)
        self.editor_page.back_button.connect("clicked", lambda _b: self.go_back())
        self.editor_page.trash_button.connect("clicked", lambda _b: self.go_back())
        self.stack.add_named(self.editor_page.box, "editor")

        # Capture bar: Android's answer to a FAB. Enter makes the note
        # (typed text becomes its title); the box makes a checklist.
        self.capture_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.capture_bar.add_css_class("xx-note-capture")
        self.capture = xw.deferred_entry("Take a note...")
        self.capture.set_hexpand(True)
        self.capture.set_valign(Gtk.Align.CENTER)
        self.capture.connect("activate", self._on_capture)
        self.capture_bar.append(self.capture)
        self.capture_bar.append(self._icon(
            "checkbox-checked-symbolic", self._on_capture_checklist,
            "New checklist"))
        root.append(self.capture_bar)

        self.tabs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.tabs.add_css_class("xx-tabs")
        self.tabs.set_homogeneous(True)
        self._tab_buttons: dict[str, object] = {}
        for key in TABS:
            button = Gtk.Button.new_with_label(TAB_TITLES[key].upper())
            button.add_css_class("xx-tab")
            button.set_can_focus(False)
            child = button.get_child()
            if isinstance(child, Gtk.Label):
                child.set_max_width_chars(8)
            button.connect("clicked", lambda _b, k=key: self.show_page(k))
            self.tabs.append(button)
            self._tab_buttons[key] = button
        root.append(self.tabs)
        keys = Gtk.EventControllerKey.new()
        keys.connect("key-pressed", self._on_key)
        self.win.add_controller(keys)
        self.show_page("notes")

    # ---- building blocks -------------------------------------------------

    def _icon(self, name: str, callback, tooltip: str = "", gicon=None):  # type: ignore[no-untyped-def]
        button = _editor._icon_button(self._Gtk, name, gicon=gicon)
        if tooltip:
            button.set_tooltip_text(tooltip)
        button.connect("clicked", lambda _b: callback())
        return button

    def _add_list_page(self, name: str):  # type: ignore[no-untyped-def]
        Gtk = self._Gtk
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.add_css_class("xx-page-body")
        body.set_margin_bottom(16)
        scroll.set_child(body)
        self.stack.add_named(scroll, name)
        return body

    def _wrap_label(self, text: str, cls: str, lines: int, chars: int = 10):  # type: ignore[no-untyped-def]
        label = self._Gtk.Label(label=text)
        label.add_css_class(cls)
        label.set_xalign(0.0)
        label.set_wrap(True)
        label.set_wrap_mode(self._Pango.WrapMode.WORD_CHAR)
        label.set_lines(lines)
        label.set_ellipsize(self._Pango.EllipsizeMode.END)
        label.set_max_width_chars(chars)
        return label

    # ---- navigation ------------------------------------------------------

    def show_page(self, name: str) -> None:
        if self._page == "editor" and name != "editor":
            self.editor_page.flush()
        if name in TABS:
            self._tab = name
        self._page = name
        tab_page = name in TABS
        notes = name == "notes"
        self.header.set_visible(tab_page)
        self.tabs.set_visible(tab_page)
        self.capture_bar.set_visible(notes and not self._selecting)
        self.search_box.set_visible(notes and self._search_open)
        self.filter_box.set_visible(notes and self._label_filter is not None)
        self.header.set_visible_child_name(
            "select" if notes and self._selecting else "title"
        )
        self.search_button.set_visible(notes)
        self.stack.set_visible_child_name(name)
        if name == "sync":
            self.sync_page.check_saved()
        if tab_page:
            self.title_label.set_text(TAB_TITLES[name])
            self._update_caption()
            for key, button in self._tab_buttons.items():
                if key == name:
                    button.add_css_class("active")
                else:
                    button.remove_css_class("active")

    def go_back(self) -> None:
        self.show_page(self._tab)

    def back(self) -> bool:
        """One step back, as Escape (or a phone's back key) expects:
        selection, then search, then a pushed page. False at a tab root."""
        if self._page == "notes" and self._selecting:
            self._bulk("clear")
            return True
        if self._page == "notes" and self._search_open:
            self.toggle_search()
            return True
        if self._page not in TABS:
            self.go_back()
            return True
        return False

    def _on_key(self, _ctrl, keyval: int, _code: int, _state) -> bool:  # type: ignore[no-untyped-def]
        from gi.repository import Gdk

        if keyval in (Gdk.KEY_Escape, Gdk.KEY_Back):
            return self.back()
        return False

    def open_sheet(self, name: str) -> None:
        self.show_page(name)

    def toggle_search(self) -> None:
        self._search_open = not self._search_open
        if self._search_open:
            self.search_box.set_visible(True)
            self.search.set_can_focus(True)
            self.search.grab_focus()
        else:
            self.search.set_text("")
            self.search.set_can_focus(False)
            self.search_box.set_visible(False)
            if self._query:
                self._query = ""
                self._render(self._notes, self._trash_notes)

    def _update_caption(self) -> None:
        live = [n for n in self._notes if not n.archived]
        text = {
            "notes": _plural(len(live), "note"),
            "labels": _plural(len(_labels.aggregate([n.labels for n in live])), "label"),
            "archive": _plural(len([n for n in self._notes if n.archived]), "note"),
            "trash": _plural(len(self._trash_notes), "note"),
        }.get(self._tab, "")
        self.caption.set_text(text.upper())
        self.caption.set_visible(bool(text))

    # ---- data ------------------------------------------------------------

    def seed_demo(self, scenario: str) -> None:
        vault = self._vault
        vault.ensure()
        if scenario == "empty":
            return
        if vault.scan():
            return
        from xx_note import frontmatter as fm

        def _set(note, **values) -> None:  # type: ignore[no-untyped-def]
            text = vault.read(note.path)
            if text is None:
                return

            def _mutate(keys: fm.OwnedKeys) -> None:
                for key, value in values.items():
                    setattr(keys, key, value)

            vault.write(note.path, fm.parse(text).rewritten(_mutate))

        vault.create("Grocery list", "- [ ] oat milk\n- [ ] coffee, the dark one\n"
                     "- [x] bin bags\n", note_type="checklist",
                     labels=["home", "errands"])
        trip = vault.create("Trip ideas", "# Lisbon\n\nTrams, custards, "
                            "**miradouros** at dusk. Ask about the ferry.\n",
                            labels=["travel"])
        _set(trip, pinned=True)
        bike = vault.create("Bike service", "Rear brake rubbing at full lock. "
                            "Mention the headset creak too.\n",
                            labels=["errands"])
        _set(bike, pinned=True, color="banana")
        vault.create("Meeting notes", "## Standup\n\n- ship the editor\n"
                     "- fix sync\n- ask about staging DNS\n", labels=["work"])
        reading = vault.create("Reading", "- [ ] Le Guin, The Dispossessed\n"
                               "- [x] Solaris\n", note_type="checklist")
        _set(reading, color="sage")
        vault.create("Loft", "2410 x 1870, joist at 400 centres.\n")
        archived = vault.create("Archived thought", "Old but kept.", labels=[])
        _set(archived, archived=True)
        doomed = vault.create("To delete", "This one is trashed.")
        vault.trash_note(doomed.path)

    def refresh(self) -> None:
        def work() -> None:
            try:
                notes = self._vault.scan()
                trash_notes = self._vault.scan_trash()
            except OSError:
                notes, trash_notes = [], []
            self._GLib.idle_add(self._render, notes, trash_notes)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()

    def open_first_for_snap(self) -> None:
        try:
            notes = [n for n in self._vault.scan() if not n.archived]
        except OSError:
            return
        notes = self._sorted(notes)
        if notes:
            self._notes = notes
            pick = next((n for n in notes if n.note_type == "checklist"), notes[0])
            self.open_note(pick.id)

    def refresh_sync(self) -> None:
        self._render(self._notes, self._trash_notes)

    def _render(self, notes: list, trash_notes: list) -> bool:  # type: ignore[no-untyped-def]
        self._notes = notes
        self._trash_notes = trash_notes
        live = [n for n in notes if not n.archived]
        self._selected &= {n.id for n in live}
        if not self._selected:
            self._selecting = False
        self._render_notes(live)
        self._render_labels(live)
        self._render_archive([n for n in notes if n.archived])
        self._render_trash(trash_notes)
        self.select_label.set_text(f"{len(self._selected)} selected")
        if self._page in TABS:
            self.show_page(self._page)
        return False

    def _sorted(self, notes: list) -> list:  # type: ignore[no-untyped-def]
        order = self._settings.sort_order
        if order == "title":
            return sorted(notes, key=lambda n: n.title.casefold())
        if order == "created":
            return sorted(notes, key=lambda n: n.created or "", reverse=True)
        return sorted(notes, key=lambda n: n.updated or n.created or "", reverse=True)

    def _visible_notes(self, notes: list) -> list:  # type: ignore[no-untyped-def]
        if self._label_filter:
            notes = [n for n in notes if self._label_filter in n.labels]
        if self._query.strip():
            import re

            terms = [t for t in re.split(r"\s+", self._query.strip()) if t]
            notes = [
                n for n in notes
                if all(t.casefold() in f"{n.title}\n{n.body}".casefold() for t in terms)
            ]
        return self._sorted(notes)

    # ---- notes grid ------------------------------------------------------

    def _render_notes(self, notes: list) -> None:  # type: ignore[no-untyped-def]
        self._clear(self.notes_list)
        if self._label_filter:
            xw.fit_text(self.filter_text, self._label_filter.upper(), 16)
        shown = self._visible_notes(notes)
        pinned = [n for n in shown if n.pinned]
        others = [n for n in shown if not n.pinned]
        first = True
        if pinned:
            self.notes_list.append(self._section("Pinned", first))
            self.notes_list.append(self._grid(pinned))
            first = False
        if others:
            if pinned:
                self.notes_list.append(self._section("Others", first))
            else:
                spacer = self._Gtk.Box()
                spacer.set_size_request(-1, 8)
                self.notes_list.append(spacer)
            self.notes_list.append(self._grid(others))

    def _section(self, text: str, first: bool):  # type: ignore[no-untyped-def]
        label = xw.section_label(text)
        if first:
            label.add_css_class("xx-note-first")
        return label

    def _grid(self, notes: list):  # type: ignore[no-untyped-def]
        """Staggered two columns: each card drops into the shorter one."""
        Gtk = self._Gtk
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_homogeneous(True)
        columns = []
        for _ in range(2):
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            column.set_valign(Gtk.Align.START)
            row.append(column)
            columns.append(column)
        heights = [0, 0]
        for note in notes:
            index = 0 if heights[0] <= heights[1] else 1
            columns[index].append(self._card(note))
            heights[index] += self._card_weight(note)
        return row

    def _card_weight(self, note) -> int:  # type: ignore[no-untyped-def]
        lines = len(_preview.preview_lines(note.body))
        extra = 1 if note.labels or _preview.checklist_progress(note.body)[1] else 0
        return 3 + lines + extra

    def _card(self, note):  # type: ignore[no-untyped-def]
        Gtk = self._Gtk
        card = Gtk.Button()
        card.add_css_class("xx-note-card")
        card.set_can_focus(False)
        tone = _colors.card_class(note.color, self._settings.color_mode)
        if tone:
            card.add_css_class(tone)
        if note.id in self._selected:
            card.add_css_class("selected")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        title = note.title or "Untitled"
        title_label = self._wrap_label(title, "xx-note-card-title", 2)
        title_label.add_css_class("xx-display-face")
        box.append(title_label)
        markup = _preview.preview_markup(note.body, title=title)
        if markup:
            body = self._wrap_label("", "xx-note-card-preview",
                                    _preview.PREVIEW_LINES)
            body.set_markup(markup)
            box.append(body)
        # Android order: checklist progress, then the label chips (they
        # wrap rather than widen a narrow column).
        done, total = _preview.checklist_progress(note.body)
        if total:
            progress = xw.label(f"{done} / {total}", "xx-note-card-progress")
            progress.add_css_class("xx-display-face")
            box.append(progress)
        if note.labels:
            chips = Gtk.FlowBox()
            chips.add_css_class("xx-note-chips")
            chips.set_selection_mode(Gtk.SelectionMode.NONE)
            chips.set_can_focus(False)
            chips.set_column_spacing(4)
            chips.set_row_spacing(4)
            chips.set_max_children_per_line(6)
            chips.set_margin_top(2)
            texts = note.labels[:CARD_CHIPS]
            if len(note.labels) > CARD_CHIPS:
                texts = texts + [f"+{len(note.labels) - CARD_CHIPS}"]
            for text in texts:
                chip = Gtk.Label()
                chip.add_css_class("xx-note-chip")
                xw.fit_text(chip, text.upper(), 10)
                chip.set_halign(Gtk.Align.START)
                chips.append(chip)
                chips.get_last_child().set_can_focus(False)
            box.append(chips)
        card.set_child(box)
        card.connect("clicked", lambda _b: self._card_tapped(note.id))
        long_press = Gtk.GestureLongPress.new()
        long_press.connect("pressed", self._on_long_press, note.id)
        card.add_controller(long_press)
        return card

    def _card_tapped(self, note_id: str) -> None:
        if self._selecting:
            if note_id in self._selected:
                self._selected.discard(note_id)
            else:
                self._selected.add(note_id)
            if not self._selected:
                self._selecting = False
            self._render(self._notes, self._trash_notes)
            return
        self.open_note(note_id)

    # ---- labels, archive, trash -----------------------------------------

    def _row_box(self, last: bool, on_tap=None):  # type: ignore[no-untyped-def]
        Gtk = self._Gtk
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.add_css_class("xx-row")
        if last:
            row.add_css_class("last")
        if on_tap is not None:
            tap = Gtk.GestureClick.new()
            tap.connect("released", lambda *_a: on_tap())
            row.add_controller(tap)
        return row

    def _row_text(self, primary: str, secondary: str = ""):  # type: ignore[no-untyped-def]
        middle = self._Gtk.Box(orientation=self._Gtk.Orientation.VERTICAL, spacing=2)
        middle.set_hexpand(True)
        middle.set_valign(self._Gtk.Align.CENTER)
        title = xw.label(primary, "xx-title")
        title.set_max_width_chars(12)
        middle.append(title)
        if secondary:
            sub = xw.label(secondary, "xx-secondary")
            sub.set_max_width_chars(12)
            middle.append(sub)
        return middle

    def _action(self, text: str, callback):  # type: ignore[no-untyped-def]
        button = self._Gtk.Button.new_with_label(text.upper())
        button.add_css_class("xx-note-action")
        button.set_can_focus(False)
        button.set_valign(self._Gtk.Align.CENTER)
        button.connect("clicked", lambda _b: callback())
        return button

    def _render_labels(self, notes: list) -> None:  # type: ignore[no-untyped-def]
        self._clear(self.labels_list)
        counts = _labels.aggregate([n.labels for n in notes])
        names = sorted(counts, key=str.casefold)
        for index, name in enumerate(names):
            row = self._row_box(index == len(names) - 1,
                                lambda n=name: self._filter_label(n))
            row.append(self._row_text(name))
            count = xw.label(str(counts[name]), "xx-note-meta", xalign=1.0)
            count.set_valign(self._Gtk.Align.CENTER)
            row.append(count)
            self.labels_list.append(row)

    def _render_archive(self, notes: list) -> None:  # type: ignore[no-untyped-def]
        self._clear(self.archive_list)
        ordered = self._sorted(notes)
        for index, note in enumerate(ordered):
            lines = _preview.preview_lines(note.body, 1)
            row = self._row_box(index == len(ordered) - 1,
                                lambda i=note.id: self.open_note(i))
            row.append(self._row_text(note.title or "Untitled",
                                      lines[0] if lines else ""))
            row.append(self._action(
                "Unarchive", lambda p=note.path: self._set_flag(p, "archived", False)))
            self.archive_list.append(row)

    def _render_trash(self, notes: list) -> None:  # type: ignore[no-untyped-def]
        import time

        self._clear(self.trash_list)
        now_ms = int(time.time() * 1000)
        for index, note in enumerate(notes):
            stamp = _store._parse_iso_ms(note.trashed_at)
            days = _trash.days_remaining(stamp, now_ms)
            chip = "Today" if days == 0 else (f"{days}d left" if days else "")
            row = self._row_box(index == len(notes) - 1)
            row.append(self._row_text(note.title or "Untitled", chip))
            row.append(self._action("Restore", lambda p=note.path: self._restore(p)))
            self.trash_list.append(row)

    def _clear(self, box) -> None:  # type: ignore[no-untyped-def]
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt

    # ---- input -----------------------------------------------------------

    def _on_search_tapped(self, gesture, _n: int, _x: float, _y: float) -> None:  # type: ignore[no-untyped-def]
        self.search.set_can_focus(True)
        self.search.grab_focus()

    def _on_search(self, _entry: object) -> None:
        self._query = self.search.get_text()
        self._render(self._notes, self._trash_notes)

    def _on_capture(self, entry: object) -> None:
        text = entry.get_text().strip()
        entry.set_text("")
        self._create(text or "Untitled", "", "note")

    def _on_capture_checklist(self) -> None:
        text = self.capture.get_text().strip()
        self.capture.set_text("")
        self._create(text or "Untitled", "- [ ] ", "checklist")

    def _create(self, title: str, body: str, note_type: str) -> None:
        def work() -> None:
            note = self._vault.create(title, body, note_type=note_type)
            self._GLib.idle_add(self._open_after_create, note)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()

    def _open_after_create(self, note) -> bool:  # type: ignore[no-untyped-def]
        if not any(n.id == note.id for n in self._notes):
            self._notes = [*self._notes, note]
        self.refresh()
        self.open_note(note.id)
        return False

    def _on_long_press(self, _gesture: object, _x: float, _y: float, note_id: str) -> None:
        self._selecting = True
        self._selected.add(note_id)
        self._render(self._notes, self._trash_notes)

    def _bulk(self, action: str) -> None:
        if action == "clear":
            self._selected.clear()
            self._selecting = False
            self._render(self._notes, self._trash_notes)
            return
        selected = set(self._selected)
        chosen = [n for n in self._notes if n.id in selected]
        # Android's pin target: pin unless every selected note is pinned.
        pin_target = not all(n.pinned for n in chosen)
        self._selected.clear()
        self._selecting = False

        def work() -> None:
            from xx_note import frontmatter as fm

            for note in chosen:
                if action == "trash":
                    self._vault.trash_note(note.path)
                    continue
                text = self._vault.read(note.path)
                if text is None:
                    continue
                doc = fm.parse(text)

                def _mutate(keys: fm.OwnedKeys, a: str = action) -> None:
                    if a == "pin":
                        keys.pinned = pin_target
                    elif a == "archive":
                        keys.archived = True

                try:
                    self._vault.write(note.path, doc.rewritten(_mutate))
                except ValueError:
                    continue
            self._GLib.idle_add(self._after_bulk)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()

    def _set_flag(self, path: str, key: str, value: bool) -> None:
        def work() -> None:
            from xx_note import frontmatter as fm

            text = self._vault.read(path)
            if text is None:
                return

            def _mutate(keys: fm.OwnedKeys) -> None:
                setattr(keys, key, value)

            try:
                self._vault.write(path, fm.parse(text).rewritten(_mutate))
            except (OSError, ValueError):
                return
            self._GLib.idle_add(self._after_bulk)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()

    def _after_bulk(self) -> bool:
        self.refresh()
        return False

    def _filter_label(self, name: str | None) -> None:
        self._label_filter = name
        self._render(self._notes, self._trash_notes)
        self.show_page("notes")

    def _restore(self, path: str) -> None:
        def work() -> None:
            self._vault.restore(path)
            self._GLib.idle_add(self._after_bulk)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()

    def _on_settings_changed(self) -> None:
        from xx_note import store as store_mod

        self._vault = store_mod.Vault(self._settings.resolved_notes_dir())
        self._vault.ensure()
        self.editor_page.bind(self._vault)
        self.refresh()

    def _on_editor_saved(self) -> bool:
        self.refresh()
        return False

    def new_note(self) -> None:
        self._create("Untitled", "", "note")

    def open_note(self, note_id: str) -> None:
        note = next((n for n in self._notes if n.id == note_id), None)
        if note is None:
            return
        text = self._vault.read(note.path)
        if text is None:
            return
        from xx_note import frontmatter as fm

        doc = fm.parse(text)
        self.editor_page.load_note(note.id, note.path, doc.title or note.title,
                                   doc.body_text, pinned=note.pinned,
                                   archived=note.archived)
        self.show_page("editor")
