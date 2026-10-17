"""Note editor page: live-styled TextView + read view, autosave, undo.

The buffer restyles itself on every change (headings, bold/italic,
strike, code, links); checkbox lines toggle on tap. Saves debounce at
750 ms onto a worker thread; ``EditHistory`` backs undo/redo. Save
applies the checklist sort-to-bottom rewrite only for `type: checklist`
bodies, never otherwise.
"""

from __future__ import annotations

import threading

from xx_note import checklist as _checklist
from xx_note import edit_history as _history
from xx_note import frontmatter as _fm
from xx_note import markdown_format as _format
from xx_note.ui import markdown_tags as _tags

AUTOSAVE_MS = 750


class EditorPage:
    def __init__(self, Gtk, Pango, GLib, on_saved=None) -> None:  # type: ignore[no-untyped-def]
        self._Gtk = Gtk
        self._Pango = Pango
        self._GLib = GLib
        self._on_saved = on_saved
        self._history = _history.EditHistory()
        self._note_id: str | None = None
        self._file_name: str | None = None
        self._vault = None
        self._save_timer = 0
        self._loading = False
        self._read_mode = False

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top.set_margin_start(20)
        top.set_margin_end(20)
        top.set_margin_top(8)
        self.back_button = Gtk.Button.new_with_label("Back")
        self.back_button.add_css_class("xx-note-toolbar-button")
        self.back_button.set_can_focus(False)
        top.append(self.back_button)
        self.mode_button = Gtk.Button.new_with_label("Read")
        self.mode_button.add_css_class("xx-note-toolbar-button")
        self.mode_button.set_can_focus(False)
        self.mode_button.connect("clicked", lambda _b: self.toggle_mode())
        top.append(self.mode_button)
        self.pin_button = Gtk.Button.new_with_label("Pin")
        self.pin_button.add_css_class("xx-note-toolbar-button")
        self.pin_button.set_can_focus(False)
        self.pin_button.connect("clicked", lambda _b: self._toggle_key("pinned"))
        top.append(self.pin_button)
        self.box.append(top)

        self.title_entry = Gtk.Entry()
        self.title_entry.set_placeholder_text("Title")
        self.title_entry.set_can_focus(False)
        _tap = Gtk.GestureClick.new()
        _tap.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        _tap.connect("pressed", self._on_title_tapped)
        self.title_entry.add_controller(_tap)
        self.title_entry.set_margin_start(20)
        self.title_entry.set_margin_end(20)
        self.title_entry.set_max_width_chars(24)
        self.title_entry.connect("changed", lambda _e: self._schedule_save())
        self.box.append(self.title_entry)

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.set_hexpand(True)
        self.box.append(self.stack)

        edit_scroll = Gtk.ScrolledWindow()
        edit_scroll.set_vexpand(True)
        edit_scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        self.textview = Gtk.TextView()
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.textview.set_top_margin(8)
        self.textview.set_left_margin(20)
        self.textview.set_right_margin(20)
        self.textview.set_bottom_margin(120)
        edit_scroll.set_child(self.textview)
        self.stack.add_named(edit_scroll, "edit")

        read_scroll = Gtk.ScrolledWindow()
        read_scroll.set_vexpand(True)
        read_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.read_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.read_box.set_margin_start(20)
        self.read_box.set_margin_end(20)
        self.read_box.set_margin_top(8)
        self.read_box.set_margin_bottom(120)
        read_scroll.set_child(self.read_box)
        self.stack.add_named(read_scroll, "read")

        self.buffer = self.textview.get_buffer()
        self._tags = _tags.ensure_tags(self.buffer)
        self.buffer.connect("changed", self._on_text_changed)
        click = Gtk.GestureClick.new()
        click.connect("pressed", self._on_tap)
        self.textview.add_controller(click)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        toolbar.set_margin_start(8)
        toolbar.set_margin_end(8)
        toolbar.set_margin_top(4)
        toolbar.set_margin_bottom(4)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroll.set_child(toolbar)
        self.box.append(scroll)
        for label, command in (
            ("B", _format.Command.BOLD),
            ("I", _format.Command.ITALIC),
            ("S", _format.Command.STRIKE),
            ("H", _format.Command.HIGHLIGHT),
            ("<>", _format.Command.CODE),
            ("link", _format.Command.LINK),
            ("H1", _format.Command.H1),
            ("H2", _format.Command.H2),
            ("H3", _format.Command.H3),
            ("-", _format.Command.BULLET),
            ("1.", _format.Command.NUMBER),
            ("[ ]", _format.Command.TASK),
            (">", _format.Command.QUOTE),
            ("undo", None),
            ("redo", None),
        ):
            button = Gtk.Button.new_with_label(label)
            button.add_css_class("xx-note-toolbar-button")
            button.set_can_focus(False)
            if command is None:
                if label == "undo":
                    button.connect("clicked", lambda _b: self.undo())
                else:
                    button.connect("clicked", lambda _b: self.redo())
            else:
                button.connect(
                    "clicked", lambda _b, c=command: self.apply_format(c)
                )
            toolbar.append(button)

    def _on_title_tapped(self, _gesture: object, _n: int, _x: float, _y: float) -> None:
        self.title_entry.set_can_focus(True)
        self.title_entry.grab_focus()

    def bind(self, vault) -> None:  # type: ignore[no-untyped-def]
        self._vault = vault

    def load_note(self, note_id: str, file_name: str, title: str, body: str) -> None:
        self._loading = True
        try:
            self._note_id = note_id
            self._file_name = file_name
            self._history.clear()
            self.title_entry.set_text(title)
            self.buffer.set_text(body)
            self._history.push(body)
            _tags.style_buffer(self.buffer, self._tags)
            self._render_read(title, body)
        finally:
            self._loading = False

    def current_body(self) -> str:
        start, end = self.buffer.get_bounds()
        return self.buffer.get_text(start, end, True)

    def toggle_mode(self) -> None:
        self._read_mode = not self._read_mode
        self.mode_button.set_label("Edit" if self._read_mode else "Read")
        self.stack.set_visible_child_name("read" if self._read_mode else "edit")
        if self._read_mode:
            start, end = self.buffer.get_bounds()
            self._render_read(
                self.title_entry.get_text(),
                self.buffer.get_text(start, end, True),
            )

    def apply_format(self, command: _format.Command) -> None:
        text = self.current_body()
        mark = self.buffer.get_insert()
        cursor = self.buffer.get_iter_at_mark(mark).get_offset()
        bounds = self.buffer.get_selection_bounds()
        if bounds:
            s = bounds[0].get_offset()
            e = bounds[1].get_offset()
        else:
            s = e = cursor
        edit = _format.apply(command, text, s, e)
        if edit is None:
            return
        self._replace(edit.text, edit.start, edit.end)

    def insert_enter(self) -> bool:
        text = self.current_body()
        mark = self.buffer.get_insert()
        cursor = self.buffer.get_iter_at_mark(mark).get_offset()
        edit = _format.enter(text, cursor, cursor)
        if edit is None:
            return False
        self._replace(edit.text, edit.start, edit.end)
        return True

    def undo(self) -> None:
        result = self._history.undo(self.current_body())
        if result is not None:
            self._replace(result, len(result), len(result))

    def redo(self) -> None:
        result = self._history.redo(self.current_body())
        if result is not None:
            self._replace(result, len(result), len(result))

    def save_now(self) -> None:
        if self._loading or self._vault is None or self._file_name is None:
            return
        if self._save_timer:
            self._GLib.source_remove(self._save_timer)
            self._save_timer = 0
        self._do_save()

    def _replace(self, text: str, start: int, end: int) -> None:
        self._loading = True
        try:
            self.buffer.set_text(text)
            cursor = self.buffer.get_iter_at_offset(
                max(0, min(start, len(text)))
            )
            self.buffer.place_cursor(cursor)
            _tags.style_buffer(self.buffer, self._tags)
        finally:
            self._loading = False
        self._schedule_save()

    def _on_text_changed(self, _buffer: object) -> None:
        if self._loading:
            return
        _tags.style_buffer(self.buffer, self._tags)
        self._schedule_save()

    def _schedule_save(self) -> None:
        if self._loading:
            return
        if self._save_timer:
            self._GLib.source_remove(self._save_timer)
        self._save_timer = self._GLib.timeout_add(AUTOSAVE_MS, self._autosave_fired)

    def _autosave_fired(self) -> bool:
        self._save_timer = 0
        self._do_save()
        return False

    def _do_save(self) -> None:
        vault = self._vault
        if vault is None or self._file_name is None:
            return
        body = self.current_body()
        title = self.title_entry.get_text()
        self._history.push(body)

        def work() -> None:
            try:
                text = vault.read(self._file_name)
                if text is None:
                    return
                doc = _fm.parse(text)
                if doc.note_type == "checklist":
                    body_sorted = _checklist.sort_body(body)
                else:
                    body_sorted = body

                def _mutate(keys: _fm.OwnedKeys) -> None:
                    keys.title = title

                updated = doc.rewritten(_mutate)
                head = updated[: len(updated) - len(doc.body_text)]
                vault.write(self._file_name, head + body_sorted)
            except OSError:
                return
            if self._on_saved is not None:
                self._GLib.idle_add(self._on_saved)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()

    def _on_tap(self, _gesture: object, _n: int, x: float, y: float) -> None:
        bx, by = self.textview.window_to_buffer_coords(
            self._Gtk.TextWindowType.TEXT, int(x), int(y)
        )
        found, line_iter, _trailing = self.textview.get_iter_at_position(bx, by)
        if not found:
            return
        line_no = line_iter.get_line()
        start = self.buffer.get_iter_at_line(line_no)
        end = self.buffer.get_iter_at_line(line_no + 1) if line_no + 1 < self.buffer.get_line_count() else None
        if end is None:
            start_b, end_b = self.buffer.get_bounds()
            line = self.buffer.get_text(start, end_b, True).split("\n")[0]
        else:
            line = self.buffer.get_text(start, end, True).split("\n")[0]
        toggled = _format.toggle_checkbox(line)
        if toggled is None:
            return
        text = self.current_body()
        lines = text.split("\n")
        if 0 <= line_no < len(lines):
            lines[line_no] = toggled
            new_text = "\n".join(lines)
            offset = sum(len(part) + 1 for part in lines[:line_no]) + len(toggled)
            self._replace(new_text, offset, offset)

    def _toggle_key(self, key: str) -> None:
        if self._vault is None or self._file_name is None:
            return
        file_name = self._file_name

        def work() -> None:
            try:
                text = self._vault.read(file_name)
                if text is None:
                    return
                doc = _fm.parse(text)
                current = bool(doc.pinned) if key == "pinned" else bool(doc.archived)

                def _mutate(keys: _fm.OwnedKeys) -> None:
                    if key == "pinned":
                        keys.pinned = not current
                    else:
                        keys.archived = not current

                self._vault.write(file_name, doc.rewritten(_mutate))
            except OSError:
                return
            if self._on_saved is not None:
                self._GLib.idle_add(self._on_saved)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()

    def _render_read(self, title: str, body: str) -> None:
        Gtk = self._Gtk
        Pango = self._Pango
        child = self.read_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.read_box.remove(child)
            child = nxt
        title_label = Gtk.Label(label=title or "Untitled")
        title_label.add_css_class("xx-note-row-title")
        title_label.set_xalign(0.0)
        title_label.set_wrap(True)
        title_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        title_label.set_max_width_chars(28)
        self.read_box.append(title_label)
        for line in body.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("### "):
                widget = self._read_label(stripped[4:], "xx-note-row-title")
            elif stripped.startswith("## "):
                widget = self._read_label(stripped[3:], "xx-note-row-title")
            elif stripped.startswith("# "):
                widget = self._read_label(stripped[2:], "xx-note-row-title")
            elif stripped.startswith("- [ ] ") or stripped.startswith("- [x] "):
                widget = self._read_label(
                    ("[ ] " if stripped[3:4] == " " else "[x] ") + stripped[6:],
                    "xx-note-preview",
                )
            elif stripped.startswith(("- ", "* ", "+ ")):
                widget = self._read_label(stripped[2:], "xx-note-preview")
            elif stripped.startswith("```"):
                continue
            else:
                widget = self._read_label(_strip_inline(stripped), "xx-note-preview")
            self.read_box.append(widget)

    def _read_label(self, text: str, cls: str):  # type: ignore[no-untyped-def]
        label = self._Gtk.Label(label=text[:400])
        label.add_css_class(cls)
        label.set_xalign(0.0)
        label.set_wrap(True)
        label.set_wrap_mode(self._Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(32)
        return label


def _strip_inline(text: str) -> str:
    import re

    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\(([^)]+)\)", r"\1", text)
    return text
