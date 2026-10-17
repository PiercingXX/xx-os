"""Main window: notes grid, labels, archive, trash, settings, sync, editor.

One window, one ``Gtk.Stack`` of pages, four bottom tabs (Notes, Labels,
Archive, Trash) with Settings/Sync/Editor pushed on top. Header is plain
text (no HeaderBar chrome); the search entry stays unfocusable until
tapped so the on-screen keyboard never pops on launch. Every list row is
64 px with ellipsized title + wrapped preview; the body scrolls while
header and tabs stay fixed. All disk reads ride a worker thread.
"""

from __future__ import annotations

import threading

from xx_note import labels as _labels
from xx_note import store as _store
from xx_note import trash as _trash
from xx_note.ui import editor as _editor
from xx_note.ui import settings_view as _settings_view

KEEP_COLORS = (
    "default", "red", "orange", "yellow", "green", "teal", "blue",
    "purple", "pink", "brown", "gray", "dark",
)

TABS = ("notes", "labels", "archive", "trash")


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

        self.win = Adw.ApplicationWindow(application=app)
        self.win.set_title("xx-note")
        self.win.set_default_size(360, 720)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.win.set_content(root)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_margin_start(20)
        header.set_margin_end(20)
        header.set_margin_top(12)
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title_box.set_hexpand(True)
        self.title_label = Gtk.Label(label="xx-note")
        self.title_label.add_css_class("xx-note-title")
        self.title_label.set_xalign(0.0)
        self.title_label.set_max_width_chars(12)
        self.title_label.set_ellipsize(Pango.EllipsizeMode.END)
        title_box.append(self.title_label)
        self.caption = Gtk.Label(label="")
        self.caption.add_css_class("xx-note-caption")
        self.caption.set_xalign(0.0)
        self.caption.set_max_width_chars(24)
        self.caption.set_ellipsize(Pango.EllipsizeMode.END)
        title_box.append(self.caption)
        header.append(title_box)
        self.sync_button = Gtk.Button.new_with_label("Sync")
        self.sync_button.set_can_focus(False)
        self.sync_button.connect("clicked", lambda _b: self.show_page("sync"))
        header.append(self.sync_button)
        self.gear_button = Gtk.Button.new_with_label("Settings")
        self.gear_button.set_can_focus(False)
        self.gear_button.connect("clicked", lambda _b: self.show_page("settings"))
        header.append(self.gear_button)
        root.append(header)

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Search notes")
        self.search.set_margin_start(20)
        self.search.set_margin_end(20)
        self.search.set_margin_top(8)
        self.search.set_can_focus(False)
        self.search.set_max_width_chars(24)
        self.search.connect("search-changed", self._on_search)
        press = Gtk.GestureClick.new()
        press.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        press.connect("pressed", self._on_search_tapped)
        self.search.add_controller(press)
        root.append(self.search)

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.set_hexpand(True)
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        root.append(self.stack)

        self.notes_scroll = self._make_scroll()
        self.notes_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.notes_scroll.set_child(self.notes_list)
        self.stack.add_named(self.notes_scroll, "notes")

        labels_scroll = self._make_scroll()
        self.labels_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        labels_scroll.set_child(self.labels_list)
        self.stack.add_named(labels_scroll, "labels")

        archive_scroll = self._make_scroll()
        self.archive_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        archive_scroll.set_child(self.archive_list)
        self.stack.add_named(archive_scroll, "archive")

        trash_scroll = self._make_scroll()
        self.trash_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        trash_scroll.set_child(self.trash_list)
        self.stack.add_named(trash_scroll, "trash")

        settings_scroll = self._make_scroll()
        self.settings_page = _settings_view.SettingsPage(
            Gtk, Pango, GLib, settings,
            on_settings_changed=self._on_settings_changed,
        )
        settings_scroll.set_child(self.settings_page.box)
        self.stack.add_named(settings_scroll, "settings")

        sync_scroll = self._make_scroll()
        self.sync_page = _settings_view.SyncPage(
            Gtk, Pango, GLib, settings, secrets, lambda: self._vault
        )
        sync_scroll.set_child(self.sync_page.box)
        self.stack.add_named(sync_scroll, "sync")

        self.editor_page = _editor.EditorPage(
            Gtk, Pango, GLib, on_saved=self._on_editor_saved
        )
        self.editor_page.bind(vault)
        self.editor_page.back_button.connect(
            "clicked", lambda _b: self.show_page("notes")
        )
        self.stack.add_named(self.editor_page.box, "editor")

        overlay = Gtk.Overlay()
        root.remove(self.stack)
        overlay.set_child(self.stack)
        self.fab = Gtk.Button.new_with_label("New")
        self.fab.add_css_class("xx-note-fab")
        self.fab.set_can_focus(False)
        self.fab.set_halign(Gtk.Align.END)
        self.fab.set_valign(Gtk.Align.END)
        self.fab.set_margin_end(16)
        self.fab.set_margin_bottom(76)
        self.fab.connect("clicked", lambda _b: self.new_note())
        overlay.add_overlay(self.fab)
        root.append(overlay)

        tabs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        tabs.set_homogeneous(True)
        self._tab_buttons: dict[str, object] = {}
        for key in TABS:
            button = Gtk.Button.new_with_label(key.capitalize())
            button.add_css_class("xx-note-tab")
            button.set_can_focus(False)
            button.connect("clicked", lambda _b, k=key: self.show_page(k))
            tabs.append(button)
            self._tab_buttons[key] = button
        root.append(tabs)

    def _make_scroll(self):  # type: ignore[no-untyped-def]
        scroll = self._Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        scroll.set_policy(self._Gtk.PolicyType.NEVER, self._Gtk.PolicyType.AUTOMATIC)
        return scroll

    def show_page(self, name: str) -> None:
        if name in ("settings", "sync", "editor"):
            self.fab.set_visible(False)
        else:
            self.fab.set_visible(True)
        self.stack.set_visible_child_name(name)
        if name in self._tab_buttons:
            for key, button in self._tab_buttons.items():
                if key == name:
                    button.add_css_class("suggested-action")
                else:
                    button.remove_css_class("suggested-action")

    def open_sheet(self, name: str) -> None:
        self.show_page(name)

    def seed_demo(self, scenario: str) -> None:
        vault = self._vault
        vault.ensure()
        if scenario == "empty":
            return
        if vault.scan():
            return
        vault.create("Grocery list", "- [ ] oat milk\n- [x] bin bags\n",
                     note_type="checklist", labels=["home", "errands"])
        note = vault.create("Trip ideas", "# Lisbon\n\nTrams, custards, miradouros.\n",
                            labels=["travel"])
        vault.create("Meeting notes", "## Standup\n\n- ship the editor\n- fix sync\n",
                     labels=["work"])
        text = vault.read(note.path)
        if text is not None:
            from xx_note import frontmatter as fm

            doc = fm.parse(text)

            def _pin(keys: fm.OwnedKeys) -> None:
                keys.pinned = True

            vault.write(note.path, doc.rewritten(_pin))
        vault.create("Archived thought", "Old but kept.", labels=[])
        for item in vault.scan():
            if item.title == "Archived thought":
                text = vault.read(item.path)
                if text is not None:
                    from xx_note import frontmatter as fm2

                    doc = fm2.parse(text)

                    def _arch(keys: fm2.OwnedKeys) -> None:
                        keys.archived = True

                    vault.write(item.path, doc.rewritten(_arch))
        vault.create("To delete", "This one is trashed.")
        for item in vault.scan():
            if item.title == "To delete":
                vault.trash_note(item.path)

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
        if notes:
            self._notes = notes
            self.open_note(notes[0].id)

    def refresh_sync(self) -> None:
        notes = self._notes
        trash_notes = self._trash_notes
        self._render(notes, trash_notes)

    def _render(self, notes: list, trash_notes: list) -> bool:  # type: ignore[no-untyped-def]
        self._notes = notes
        self._trash_notes = trash_notes
        live = [n for n in notes if not n.archived]
        count = len(live)
        self.caption.set_text(f"{count} note{'s' if count != 1 else ''}")
        self._render_notes(live)
        self._render_labels(live)
        self._render_archive([n for n in notes if n.archived])
        self._render_trash(trash_notes)
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

    def _render_notes(self, notes: list) -> None:  # type: ignore[no-untyped-def]
        self._clear(self.notes_list)
        if self._label_filter:
            chip = self._chip_row(f"Label: {self._label_filter} (tap to clear)")
            self.notes_list.append(chip)
        capture = self._Gtk.Entry()
        capture.set_placeholder_text("Take a note...")
        capture.set_margin_start(20)
        capture.set_margin_end(20)
        capture.set_margin_top(8)
        capture.set_can_focus(False)
        press = self._Gtk.GestureClick.new()
        press.set_propagation_phase(self._Gtk.PropagationPhase.CAPTURE)
        press.connect("pressed", self._on_capture_tapped, capture)
        capture.add_controller(press)
        capture.connect("activate", self._on_capture)
        self.notes_list.append(capture)
        if self._selecting and self._selected:
            bar = self._selection_bar()
            self.notes_list.append(bar)
        shown = self._visible_notes(notes)
        pinned = [n for n in shown if n.pinned]
        others = [n for n in shown if not n.pinned]
        if pinned:
            self.notes_list.append(self._section("Pinned"))
            for note in pinned:
                self.notes_list.append(self._note_row(note))
        if others:
            if pinned:
                self.notes_list.append(self._section("Others"))
            for note in others:
                self.notes_list.append(self._note_row(note))
        if not shown:
            self.notes_list.append(
                self._empty("No notes yet. Tap New to start one.")
            )

    def _render_labels(self, notes: list) -> None:  # type: ignore[no-untyped-def]
        self._clear(self.labels_list)
        counts = _labels.aggregate([n.labels for n in notes])
        if not counts:
            self.labels_list.append(self._empty("No labels yet."))
            return
        for name in sorted(counts, key=str.casefold):
            row = self._Gtk.Button()
            row.add_css_class("xx-note-row")
            row.set_can_focus(False)
            inner = self._Gtk.Box(orientation=self._Gtk.Orientation.HORIZONTAL, spacing=8)
            inner.set_margin_start(20)
            inner.set_margin_end(20)
            title = self._Gtk.Label(label=f"{name} ({counts[name]})")
            title.add_css_class("xx-note-row-title")
            title.set_xalign(0.0)
            title.set_hexpand(True)
            title.set_max_width_chars(22)
            title.set_ellipsize(self._Pango.EllipsizeMode.END)
            inner.append(title)
            row.set_child(inner)
            row.connect("clicked", lambda _b, n=name: self._filter_label(n))
            self.labels_list.append(row)

    def _render_archive(self, notes: list) -> None:  # type: ignore[no-untyped-def]
        self._clear(self.archive_list)
        if not notes:
            self.archive_list.append(self._empty("Nothing archived."))
            return
        self.archive_list.append(self._section("Archive"))
        for note in self._sorted(notes):
            self.archive_list.append(self._note_row(note))

    def _render_trash(self, notes: list) -> None:  # type: ignore[no-untyped-def]
        import time

        self._clear(self.trash_list)
        now_ms = int(time.time() * 1000)
        if not notes:
            self.trash_list.append(self._empty("Trash is empty."))
            return
        for note in notes:
            stamp = _store._parse_iso_ms(note.trashed_at)
            days = _trash.days_remaining(stamp, now_ms)
            chip = "today" if days == 0 else (f"{days}d left" if days else "")
            self.trash_list.append(self._trash_row(note, chip))

    def _note_row(self, note) -> object:  # type: ignore[no-untyped-def]
        Gtk = self._Gtk
        row = Gtk.Button()
        row.add_css_class("xx-note-row")
        row.set_can_focus(False)
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        inner.set_margin_start(20)
        inner.set_margin_end(20)
        dot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        dot.add_css_class("xx-note-tone")
        dot.set_valign(Gtk.Align.CENTER)
        try:
            dot.set_opacity(0.25 + 0.75 * (
                (KEEP_COLORS.index(note.color) + 1) / len(KEEP_COLORS)
                if note.color in KEEP_COLORS else 0.5
            ))
        except ValueError:
            dot.set_opacity(0.5)
        inner.append(dot)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_hexpand(True)
        title = Gtk.Label(label=note.title or "Untitled")
        title.add_css_class("xx-note-row-title")
        title.set_xalign(0.0)
        title.set_max_width_chars(22)
        title.set_ellipsize(self._Pango.EllipsizeMode.END)
        text.append(title)
        preview = _store._preview(note.body, 2).replace("\n", " ")
        if preview:
            sub = Gtk.Label(label=preview[:160])
            sub.add_css_class("xx-note-preview")
            sub.set_xalign(0.0)
            sub.set_max_width_chars(28)
            sub.set_ellipsize(self._Pango.EllipsizeMode.END)
            text.append(sub)
        if note.labels:
            lab = Gtk.Label(label=", ".join(note.labels[:4]))
            lab.add_css_class("xx-note-chip")
            lab.set_xalign(0.0)
            lab.set_max_width_chars(28)
            lab.set_ellipsize(self._Pango.EllipsizeMode.END)
            text.append(lab)
        inner.append(text)
        row.set_child(inner)
        row.connect("clicked", lambda _b: self.open_note(note.id))
        long_press = Gtk.GestureLongPress.new()
        long_press.connect("pressed", self._on_long_press, note.id)
        row.add_controller(long_press)
        return row

    def _trash_row(self, note, chip: str) -> object:  # type: ignore[no-untyped-def]
        Gtk = self._Gtk
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.set_margin_top(8)
        title = Gtk.Label(label=note.title or "Untitled")
        title.add_css_class("xx-note-row-title")
        title.set_xalign(0.0)
        title.set_hexpand(True)
        title.set_max_width_chars(16)
        title.set_ellipsize(self._Pango.EllipsizeMode.END)
        box.append(title)
        if chip:
            meta = Gtk.Label(label=chip)
            meta.add_css_class("xx-note-chip")
            meta.set_max_width_chars(8)
            box.append(meta)
        restore = Gtk.Button.new_with_label("Restore")
        restore.set_can_focus(False)
        restore.connect("clicked", lambda _b: self._restore(note.path))
        box.append(restore)
        return box

    def _selection_bar(self):  # type: ignore[no-untyped-def]
        Gtk = self._Gtk
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.set_margin_start(20)
        bar.set_margin_top(8)
        for label, action in (
            ("Pin", "pin"), ("Archive", "archive"), ("Trash", "trash"),
            ("Clear", "clear"),
        ):
            button = Gtk.Button.new_with_label(label)
            button.set_can_focus(False)
            button.connect("clicked", lambda _b, a=action: self._bulk(a))
            bar.append(button)
        return bar

    def _section(self, text: str):  # type: ignore[no-untyped-def]
        label = self._Gtk.Label(label=text.upper())
        label.add_css_class("xx-note-caption")
        label.set_xalign(0.0)
        label.set_margin_start(20)
        label.set_margin_top(12)
        label.set_max_width_chars(24)
        return label

    def _chip_row(self, text: str):  # type: ignore[no-untyped-def]
        button = self._Gtk.Button.new_with_label(text)
        button.set_margin_start(20)
        button.set_margin_end(20)
        button.set_margin_top(8)
        button.set_can_focus(False)
        button.connect("clicked", lambda _b: self._filter_label(None))
        return button

    def _empty(self, text: str):  # type: ignore[no-untyped-def]
        label = self._Gtk.Label(label=text)
        label.add_css_class("xx-note-preview")
        label.set_xalign(0.0)
        label.set_margin_start(20)
        label.set_margin_top(24)
        label.set_wrap(True)
        label.set_wrap_mode(self._Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(30)
        return label

    def _clear(self, box) -> None:  # type: ignore[no-untyped-def]
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt

    def _on_search_tapped(self, gesture, _n: int, _x: float, _y: float) -> None:  # type: ignore[no-untyped-def]
        self.search.set_can_focus(True)
        self.search.grab_focus()

    def _on_search(self, _entry: object) -> None:
        self._query = self.search.get_text()
        self._render(self._notes, self._trash_notes)

    def _on_capture_tapped(self, gesture, _n: int, _x: float, _y: float, entry) -> None:  # type: ignore[no-untyped-def]
        entry.set_can_focus(True)
        entry.grab_focus()

    def _on_capture(self, entry: object) -> None:
        text = entry.get_text().strip()
        if not text:
            return
        entry.set_text("")

        def work() -> None:
            note = self._vault.create(text, "")
            self._GLib.idle_add(self._open_after_create, note.id)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()

    def _open_after_create(self, note_id: str) -> bool:
        self.refresh()
        self.open_note(note_id)
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
        self._selected.clear()
        self._selecting = False

        def work() -> None:
            from xx_note import frontmatter as fm

            for note_id in selected:
                note = next((n for n in self._notes if n.id == note_id), None)
                if note is None:
                    continue
                if action == "trash":
                    self._vault.trash_note(note.path)
                    continue
                text = self._vault.read(note.path)
                if text is None:
                    continue
                doc = fm.parse(text)

                def _mutate(keys: fm.OwnedKeys, a: str = action) -> None:
                    if a == "pin":
                        keys.pinned = True
                    elif a == "archive":
                        keys.archived = True

                try:
                    self._vault.write(note.path, doc.rewritten(_mutate))
                except ValueError:
                    continue
            self._GLib.idle_add(self._after_bulk)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()

    def _after_bulk(self) -> bool:
        self.refresh()
        return False

    def _filter_label(self, name: str | None) -> None:
        self._label_filter = name
        self.show_page("notes")
        self._render(self._notes, self._trash_notes)

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
        def work() -> None:
            note = self._vault.create("Untitled", "")
            self._GLib.idle_add(self._open_after_create, note.id)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()

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
                                   doc.body_text)
        self.show_page("editor")
