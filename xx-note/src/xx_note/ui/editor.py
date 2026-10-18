"""Note editor page: live-styled TextView + read view, autosave, undo.

The buffer restyles itself on every change (headings, bold/italic,
strike, code, links); checkbox lines toggle on tap. Saves debounce at
750 ms onto a worker thread; ``EditHistory`` backs undo/redo. Save
applies the checklist sort-to-bottom rewrite only for `type: checklist`
bodies, never otherwise.
"""

from __future__ import annotations

import threading
from pathlib import Path

from xx_note import checklist as _checklist
from xx_note import edit_history as _history
from xx_note import frontmatter as _fm
from xx_note import markdown_format as _format
from xx_note.ui import markdown_tags as _tags

AUTOSAVE_MS = 750

ICON_DIR = Path(__file__).with_name("icons")

# (name, kind, face, command): kind "icon" names a symbolic icon, "text"
# a Pango-markup face; None draws a hairline group break.
TOOLS = (
    ("Undo", "icon", "edit-undo-symbolic", "undo"),
    ("Redo", "icon", "edit-redo-symbolic", "redo"),
    None,
    ("Heading 1", "text", "H1", _format.Command.H1),
    ("Heading 2", "text", "H2", _format.Command.H2),
    ("Heading 3", "text", "H3", _format.Command.H3),
    None,
    ("Bold", "icon", "format-text-bold-symbolic", _format.Command.BOLD),
    ("Italic", "icon", "format-text-italic-symbolic", _format.Command.ITALIC),
    ("Strikethrough", "icon", "format-text-strikethrough-symbolic",
     _format.Command.STRIKE),
    ("Highlight", "hl", "ab", _format.Command.HIGHLIGHT),
    ("Inline code", "text", "&lt;&gt;", _format.Command.CODE),
    None,
    ("Bulleted list", "icon", "view-list-bullet-symbolic",
     _format.Command.BULLET),
    ("Numbered list", "icon", "view-list-ordered-symbolic",
     _format.Command.NUMBER),
    ("Checkbox", "icon", "checkbox-checked-symbolic", _format.Command.TASK),
    ("Quote", "text", "&gt;", _format.Command.QUOTE),
    None,
    ("Link", "icon", "insert-link-symbolic", _format.Command.LINK),
)


def _archive_icon():  # type: ignore[no-untyped-def]
    """The archive glyph (Android draws its own; Adwaita has none)."""
    from gi.repository import Gio

    path = ICON_DIR / "xx-note-archive-symbolic.svg"
    return Gio.FileIcon.new(Gio.File.new_for_path(str(path)))


def _icon_button(Gtk, icon_name: str, gicon=None):  # type: ignore[no-untyped-def]
    button = Gtk.Button()
    button.add_css_class("xx-btn")
    button.add_css_class("xx-btn-icon")
    button.set_can_focus(False)
    button.set_valign(Gtk.Align.CENTER)
    if gicon is not None:
        image = Gtk.Image.new_from_gicon(gicon)
    else:
        image = Gtk.Image.new_from_icon_name(icon_name)
    image.set_pixel_size(20)
    button.set_child(image)
    return button


def _tool_button(Gtk, kind: str, face: str):  # type: ignore[no-untyped-def]
    button = Gtk.Button()
    button.add_css_class("xx-note-tool")
    button.set_can_focus(False)
    if kind == "icon":
        image = Gtk.Image.new_from_icon_name(face)
        image.set_pixel_size(20)
        button.set_child(image)
    else:
        label = Gtk.Label()
        label.set_markup(face)
        if kind == "hl":
            label.add_css_class("xx-note-hl")
            label.set_valign(Gtk.Align.CENTER)
        button.set_child(label)
    return button


class EditorPage:
    def __init__(self, Gtk, Pango, GLib, on_saved=None, on_trashed=None) -> None:  # type: ignore[no-untyped-def]
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

        self._pinned = False
        self._archived = False
        self._lead = ""

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Compact bar: back, then read/edit, pin, archive and trash icons.
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        top.add_css_class("xx-page-body")
        top.add_css_class("xx-compact-header")
        top.add_css_class("xx-note-bar")
        self.back_button = _icon_button(Gtk, "go-previous-symbolic")
        top.append(self.back_button)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        top.append(spacer)
        self.mode_button = _icon_button(Gtk, "view-reveal-symbolic")
        self.mode_button.set_tooltip_text("Read")
        self.mode_button.connect("clicked", lambda _b: self.toggle_mode())
        top.append(self.mode_button)
        self.pin_button = _icon_button(Gtk, "view-pin-symbolic")
        self.pin_button.set_tooltip_text("Pin")
        self.pin_button.connect("clicked", lambda _b: self._toggle_key("pinned"))
        top.append(self.pin_button)
        self.archive_button = _icon_button(Gtk, "", gicon=_archive_icon())
        self.archive_button.set_tooltip_text("Archive")
        self.archive_button.connect(
            "clicked", lambda _b: self._toggle_key("archived")
        )
        top.append(self.archive_button)
        # Android keeps Delete in the editor's overflow; here it is the
        # last icon. A trash move is reversible, so nothing confirms it.
        self.trash_button = _icon_button(Gtk, "user-trash-symbolic")
        self.trash_button.set_tooltip_text("Trash")
        self.trash_button.connect("clicked", lambda _b: self.trash(on_trashed))
        top.append(self.trash_button)
        self.box.append(top)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title_box.add_css_class("xx-note-editor-title")
        self.title_entry = Gtk.Entry()
        self.title_entry.add_css_class("xx-note-title-entry")
        self.title_entry.add_css_class("xx-display-face")
        self.title_entry.set_placeholder_text("Title")
        self.title_entry.set_has_frame(False)
        self.title_entry.set_can_focus(False)
        _tap = Gtk.GestureClick.new()
        _tap.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        _tap.connect("pressed", self._on_title_tapped)
        self.title_entry.add_controller(_tap)
        self.title_entry.set_max_width_chars(12)
        self.title_entry.set_hexpand(True)
        self.title_entry.connect("changed", lambda _e: self._schedule_save())
        title_box.append(self.title_entry)
        self.box.append(title_box)

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
        self.textview.add_css_class("xx-note-body")
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.textview.set_top_margin(12)
        self.textview.set_left_margin(20)
        self.textview.set_right_margin(20)
        self.textview.set_bottom_margin(24)
        self.textview.set_pixels_below_lines(4)
        edit_scroll.set_child(self.textview)
        self.stack.add_named(edit_scroll, "edit")

        read_scroll = Gtk.ScrolledWindow()
        read_scroll.set_vexpand(True)
        read_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.read_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.read_box.set_margin_start(20)
        self.read_box.set_margin_end(20)
        self.read_box.set_margin_top(12)
        self.read_box.set_margin_bottom(24)
        read_scroll.set_child(self.read_box)
        self.stack.add_named(read_scroll, "read")

        self.buffer = self.textview.get_buffer()
        self._tags = _tags.ensure_tags(self.buffer)
        self.buffer.connect("changed", self._on_text_changed)
        click = Gtk.GestureClick.new()
        click.connect("pressed", self._on_tap)
        self.textview.add_controller(click)

        # Format toolbar (Android FormatToolbar order): history, headings,
        # emphasis, lists, link. Equal 44 px tools, hairline group breaks,
        # scrolls sideways where the row is wider than the phone.
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        toolbar.set_valign(Gtk.Align.CENTER)
        # The edge inset scrolls with the tools (Android's padding inside
        # horizontalScroll): padding on the scroller itself cut the row
        # short of the screen edge, a hard clip with no tool peeking.
        toolbar.set_margin_start(12)
        toolbar.set_margin_end(12)
        self.toolbar = Gtk.ScrolledWindow()
        self.toolbar.add_css_class("xx-note-toolbar")
        self.toolbar.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.toolbar.set_child(toolbar)
        self.box.append(self.toolbar)
        for spec in TOOLS:
            if spec is None:
                sep = Gtk.Box()
                sep.add_css_class("xx-note-tool-sep")
                sep.set_valign(Gtk.Align.CENTER)
                toolbar.append(sep)
                continue
            name, kind, face, command = spec
            button = _tool_button(Gtk, kind, face)
            button.set_tooltip_text(name)
            if command == "undo":
                button.connect("clicked", lambda _b: self.undo())
            elif command == "redo":
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

    def load_note(self, note_id: str, file_name: str, title: str, body: str,
                  pinned: bool = False, archived: bool = False) -> None:
        self._loading = True
        try:
            self._pinned = bool(pinned)
            self._archived = bool(archived)
            self._show_flags()
            if self._read_mode:
                self.toggle_mode()
            self._note_id = note_id
            self._file_name = file_name
            # The blank line(s) after the frontmatter are file layout, not
            # text: hide them from the editor, write them back on save.
            stripped = body.lstrip("\n")
            self._lead = body[: len(body) - len(stripped)]
            body = stripped
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
        image = self.mode_button.get_child()
        image.set_from_icon_name(
            "document-edit-symbolic" if self._read_mode else "view-reveal-symbolic"
        )
        self.mode_button.set_tooltip_text("Edit" if self._read_mode else "Read")
        self.toolbar.set_visible(not self._read_mode)
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

    def flush(self) -> None:
        """Write a pending autosave now (leaving the editor)."""
        if self._save_timer:
            self.save_now()

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

    def _pending(self) -> tuple[str, str]:
        """(title, body) as they will be written; the body keeps the file's
        blank lead after the frontmatter."""
        body = self.current_body()
        self._history.push(body)
        return self.title_entry.get_text(), (self._lead + body if body else body)

    @staticmethod
    def _write(vault, file_name: str, title: str, body: str) -> bool:  # type: ignore[no-untyped-def]
        """Worker side: rewrite the note's title and body. False when the
        file is gone."""
        text = vault.read(file_name)
        if text is None:
            return False
        doc = _fm.parse(text)
        if doc.note_type == "checklist":
            body = _checklist.sort_body(body)

        def _mutate(keys: _fm.OwnedKeys) -> None:
            keys.title = title

        updated = doc.rewritten(_mutate)
        head = updated[: len(updated) - len(doc.body_text)]
        vault.write(file_name, head + body)
        return True

    def _do_save(self) -> None:
        vault = self._vault
        file_name = self._file_name
        if vault is None or file_name is None:
            return
        title, body = self._pending()

        def work() -> None:
            try:
                if not self._write(vault, file_name, title, body):
                    return
            except OSError:
                return
            if self._on_saved is not None:
                self._GLib.idle_add(self._on_saved)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()

    def trash(self, on_trashed=None) -> None:  # type: ignore[no-untyped-def]
        """Move the open note to Trash, unsaved edits included, then hand
        back to the shell (``on_trashed`` runs on the main loop)."""
        vault = self._vault
        file_name = self._file_name
        if vault is None or file_name is None:
            return
        pending = bool(self._save_timer)
        if self._save_timer:
            self._GLib.source_remove(self._save_timer)
            self._save_timer = 0
        title, body = self._pending() if pending else ("", "")
        self._file_name = None

        def work() -> None:
            try:
                if pending:
                    self._write(vault, file_name, title, body)
                vault.trash_note(file_name)
            except OSError:
                pass
            if on_trashed is not None:
                self._GLib.idle_add(on_trashed)

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

    def _show_flags(self) -> None:
        for button, on in ((self.pin_button, self._pinned),
                           (self.archive_button, self._archived)):
            if on:
                button.add_css_class("xx-note-on")
            else:
                button.remove_css_class("xx-note-on")

    def _toggle_key(self, key: str) -> None:
        if self._vault is None or self._file_name is None:
            return
        file_name = self._file_name
        if key == "pinned":
            self._pinned = not self._pinned
        else:
            self._archived = not self._archived
        self._show_flags()

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
        """Rendered view: headings in the display face, tasks as boxes,
        list markers dropped. The title stays in the title row above."""
        Gtk = self._Gtk
        child = self.read_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.read_box.remove(child)
            child = nxt
        del title
        for line in body.split("\n"):
            stripped = line.strip()
            if not stripped or stripped.startswith("```"):
                continue
            heading = stripped.lstrip("#")
            if stripped.startswith("#") and heading.startswith(" "):
                widget = self._read_label(heading.strip(), "xx-note-read-heading")
                widget.add_css_class("xx-display-face")
                widget.set_margin_top(8)
            elif stripped[:6] in ("- [ ] ", "- [x] ", "- [X] "):
                done = stripped[3] in "xX"
                widget = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                box = Gtk.Image.new_from_icon_name(
                    "checkbox-checked-symbolic" if done else "checkbox-symbolic"
                )
                box.set_pixel_size(18)
                box.set_valign(Gtk.Align.START)
                widget.append(box)
                text = self._read_label(_strip_inline(stripped[6:]), "xx-note-read")
                text.set_hexpand(True)
                if done:
                    text.add_css_class("xx-note-read-done")
                widget.append(text)
            elif stripped.startswith(("- ", "* ", "+ ")):
                widget = self._read_label(_strip_inline(stripped[2:]), "xx-note-read")
            else:
                widget = self._read_label(_strip_inline(stripped), "xx-note-read")
            self.read_box.append(widget)

    def _read_label(self, text: str, cls: str):  # type: ignore[no-untyped-def]
        label = self._Gtk.Label(label=text[:400])
        label.add_css_class(cls)
        label.set_xalign(0.0)
        label.set_wrap(True)
        label.set_wrap_mode(self._Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(24)
        return label


def _strip_inline(text: str) -> str:
    import re

    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\(([^)]+)\)", r"\1", text)
    return text
