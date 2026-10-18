"""xx-share GTK4 UI in the XX design language.

Three tabs (Share, History, Settings) built only from
``xx_ui.widgets`` builders: PageHeader titles, ListRows with avatars
and hairlines, SettingRows with the inverted switch, one inverted
primary Send button. No titlebar chrome, no app-owned theme CSS.
Minimal: an empty list shows nothing (no empty-state lines, no section
label over nothing), Send is enabled only with a device and files, the
receiver-PIN field appears only when a receiver asks for one, and each
action has one entry point (the star on a nearby row favorites it;
Settings lists the favorites under the auto-accept switch they feed).
Network work runs on worker threads with results posted back via
GLib.idle_add; the main loop never blocks.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

import xx_ui
from xx_ui import widgets

from config import VISIBILITY_ALWAYS, ShareConfig
from favorites import (
    auto_accept_enabled,
    favorites_list,
    is_favorite,
    set_auto_accept,
    set_favorite,
)
from notify import ACTION_ACCEPT, ACTION_DECLINE, format_size
from service import PIN_NEEDED


def _initials(name: str) -> str:
    parts = [piece for piece in str(name).split() if piece[:1].isalnum()]
    if not parts:
        return "?"
    return "".join(piece[0].upper() for piece in parts[:2])


def _peer_sub(peer: dict) -> str:
    kind = str(peer.get("device_type", "") or "").title() or "Device"
    addr = str(peer.get("addr", ""))
    return f"{kind}\u00a0· {addr}" if addr else kind


def _history_date(entry: dict, now: float | None = None) -> str:
    """Today's entries show the time, older ones the day ("Oct 17")."""
    try:
        moment = time.localtime(float(entry.get("received_at", 0)))
    except (TypeError, ValueError):
        return ""
    today = time.localtime(time.time() if now is None else now)
    if moment[:3] == today[:3]:
        return time.strftime("%H:%M", moment)
    return f"{time.strftime('%b', moment)} {moment.tm_mday}"


TABS = (("share", "Share"), ("history", "History"),
        ("settings", "Settings"))


def count_caption(count: int, noun: str) -> str:
    """Header caption for a list: "6 FILES", nothing when empty."""
    if count <= 0:
        return ""
    return f"{count} {noun}{'s' if count != 1 else ''}".upper()


def visibility_caption(visible: bool, always: bool) -> str:
    if not visible:
        return "Hidden"
    return "Visible" if always else "Visible · 10 min"


def keep_visible_sub(always: bool) -> str:
    return "Until turned off" if always else "Hides after 10 min"


def send_ready(has_peer: bool, file_count: int) -> bool:
    return bool(has_peer) and file_count > 0


def incoming_summary(count: int, total: int) -> str:
    noun = "file" if count == 1 else "files"
    return f"Wants to send {count} {noun} · {format_size(total)}"


def _fit_row(root: object, file_name: bool = False,
             wrap_sub: bool = False) -> object:
    """Fit a row's text to phone widths and large text without widening
    the window. A file name stays on one line, ellipsized in the middle
    so its extension stays; any other title wraps between words onto a
    second line instead of ellipsizing, and so does the subtitle with
    ``wrap_sub`` (a setting's one-line description)."""
    from gi.repository import Gtk, Pango

    stack = [root]
    while stack:
        widget = stack.pop()
        if isinstance(widget, Gtk.Label):
            title = widget.has_css_class("xx-title")
            if title and file_name:
                widget.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            elif title or (wrap_sub and widget.has_css_class("xx-secondary")):
                widget.set_wrap(True)
                widget.set_wrap_mode(Pango.WrapMode.WORD)
                widget.set_lines(2)
                widget.set_ellipsize(Pango.EllipsizeMode.END)
        child = widget.get_last_child()  # type: ignore[union-attr]
        while child is not None:
            stack.append(child)
            child = child.get_prev_sibling()
    return root


def _set_caption(caption: object, text: str) -> None:
    caption.set_text(text)  # type: ignore[union-attr]
    caption.set_visible(bool(text))  # type: ignore[union-attr]


class ShareWindow:
    def __init__(self, app: object, service: object, initial_files: list[str]) -> None:
        from gi.repository import Gtk

        self.service = service
        self.config: ShareConfig = service.config  # type: ignore[union-attr]
        self.files: list[str] = [item for item in initial_files
                                 if Path(item).is_file()]
        self._peers: list[dict] = []
        self._history: list[dict] = []
        self.selected_fp = ""
        self.current_transfer: str | None = None
        self._tab = "share"
        self._incoming_dialog: object | None = None

        xx_ui.apply(app)

        self.window = Gtk.ApplicationWindow(application=app, title="xx-share")
        self.window.set_decorated(False)
        self.window.set_default_size(360, 720)

        self.share_header = widgets.page_header(
            "Share", self._visibility_caption(),
            actions=[("Add", self._on_add_files)])
        self.share_caption = self.share_header.get_last_child()
        share_body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self.device_label = widgets.section_label("Nearby devices")
        share_body.append(self.device_label)
        self.device_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        share_body.append(self.device_box)
        self.file_label = widgets.section_label("Files")
        share_body.append(self.file_label)
        self.file_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        share_body.append(self.file_box)
        self.send_pin = widgets.deferred_entry("Receiver PIN")
        self.send_pin.set_visible(False)
        send_row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        self.send_button = widgets.primary_button("Send", self._on_send)
        self.send_button.set_hexpand(True)
        self.cancel_button = widgets.outline_button("Cancel", self._on_cancel)
        self.cancel_button.set_hexpand(True)
        self.cancel_button.set_visible(False)
        send_row.append(self.send_button)
        send_row.append(self.cancel_button)
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_visible(False)
        self.status_label = widgets.wrap_label("", "xx-secondary")
        self.status_label.set_visible(False)
        share_actions = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
        share_actions.add_css_class("xx-page-body")
        share_actions.set_margin_top(8)
        share_actions.set_margin_bottom(12)
        share_actions.append(self.progress_bar)
        share_actions.append(self.status_label)
        share_actions.append(self.send_pin)
        share_actions.append(send_row)
        share_page = widgets.page(self.share_header, share_body,
                                  share_actions)

        self.history_header = widgets.page_header(
            "History", " ", actions=[("Clear", self._on_clear_history)])
        self.history_caption = self.history_header.get_last_child()
        self.history_clear = \
            self.history_header.get_first_child().get_last_child()
        self.history_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        history_page = widgets.page(self.history_header, self.history_box)

        settings_header = widgets.page_header("Settings")
        settings_body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        settings_body.append(widgets.section_label("Device name"))
        self.alias_entry = widgets.deferred_entry("Device name")
        self.alias_entry.set_text(str(self.config.data.get("alias", "")))
        self.alias_entry.connect("changed", self._on_alias_changed)
        settings_body.append(self.alias_entry)
        settings_body.append(widgets.section_label("Receive PIN"))
        self.pin_entry = widgets.deferred_entry("None")
        self.pin_entry.set_text(str(self.config.data.get("require_pin", "")))
        self.pin_entry.connect("changed", self._on_pin_changed)
        settings_body.append(self.pin_entry)
        settings_body.append(widgets.section_label("Visibility"))
        self.visible_switch = widgets.make_switch(
            bool(self.config.data.get("visible")),
            self._on_visible_switched)
        settings_body.append(_fit_row(wrap_sub=True, root=widgets.setting_row(
            "Visible to nearby devices", "", self.visible_switch)))
        self.always_switch = widgets.make_switch(
            self.config.data.get("visibility_mode") == VISIBILITY_ALWAYS,
            self._on_always_switched)
        keep_row = _fit_row(wrap_sub=True, root=widgets.setting_row(
            "Keep visible",
            keep_visible_sub(self.config.data.get("visibility_mode")
                             == VISIBILITY_ALWAYS),
            self.always_switch, last=True))
        self.keep_sub = keep_row.get_first_child().get_last_child()
        settings_body.append(keep_row)
        settings_body.append(widgets.section_label("Receiving"))
        self.auto_accept_switch = widgets.make_switch(
            auto_accept_enabled(self.config), self._on_auto_accept_switched)
        settings_body.append(_fit_row(wrap_sub=True, root=widgets.setting_row(
            "Auto-accept favorites", "Favorites skip the prompt",
            self.auto_accept_switch, last=True)))
        # Favorites live beside the switch they feed; a nearby row's star
        # adds one, the cross here removes one (also when not nearby).
        self.favorites_label = widgets.section_label("Favorites")
        settings_body.append(self.favorites_label)
        self.favorites_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        settings_body.append(self.favorites_box)
        settings_page = widgets.page(settings_header, settings_body)

        self.stack = Gtk.Stack()
        self.stack.add_named(share_page, "share")
        self.stack.add_named(history_page, "history")
        self.stack.add_named(settings_page, "settings")
        self.stack.set_visible_child_name("share")

        root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        root.append(self.stack)
        self.tabs_slot = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        root.append(self.tabs_slot)
        self._render_tabs()
        self.window.set_child(root)
        previous = getattr(service, "on_signal", None)
        service.on_signal = self._chain_signal(previous)  # type: ignore[union-attr]
        self.refresh_files()
        self.refresh_devices()
        self.refresh_history()
        self.refresh_favorites()
        self.send_button.grab_focus()

    def _visibility_caption(self) -> str:
        return visibility_caption(
            bool(self.config.data.get("visible")),
            self.config.data.get("visibility_mode") == VISIBILITY_ALWAYS)

    def _refresh_chrome(self) -> None:
        self.share_caption.set_text(self._visibility_caption().upper())  # type: ignore[union-attr]
        _set_caption(self.history_caption,
                     count_caption(len(self._history), "file"))
        self.history_clear.set_visible(bool(self._history))  # type: ignore[union-attr]
        self.keep_sub.set_text(keep_visible_sub(  # type: ignore[union-attr]
            self.config.data.get("visibility_mode") == VISIBILITY_ALWAYS))

    def _tabs(self) -> list:
        return [("", title, self._tab == name,
                 lambda _name=name: self._show_tab(_name))
                for name, title in TABS]

    def _render_tabs(self) -> None:
        while True:
            child = self.tabs_slot.get_first_child()  # type: ignore[union-attr]
            if child is None:
                break
            self.tabs_slot.remove(child)  # type: ignore[union-attr]
        self.tabs_slot.append(widgets.bottom_tabs(self._tabs()))  # type: ignore[union-attr]

    def _show_tab(self, name: str) -> None:
        self._tab = name
        self.stack.set_visible_child_name(name)  # type: ignore[union-attr]
        self._render_tabs()

    def _chain_signal(self, previous: object) -> object:
        def chained(name: str, args: tuple) -> None:
            try:
                if callable(previous):
                    previous(name, args)  # type: ignore[operator]
            except Exception as error:
                logger.warning("chained signal failed: %s", error)
            self._on_bus_signal(name, args)

        return chained

    def present(self) -> None:
        self.window.present()  # type: ignore[union-attr]

    def set_backend(self, service: object) -> None:
        previous = getattr(service, "on_signal", None)
        service.on_signal = self._chain_signal(previous)  # type: ignore[union-attr]
        self.service = service
        self.config = service.config  # type: ignore[union-attr]
        self.refresh_devices()
        self.refresh_history()
        self.refresh_favorites()

    @staticmethod
    def _clear_box(box: object) -> None:
        while True:
            row = box.get_first_child()  # type: ignore[union-attr]
            if row is None:
                break
            box.remove(row)  # type: ignore[union-attr]

    def refresh_files(self) -> None:
        self._clear_box(self.file_box)
        self.file_label.set_visible(bool(self.files))  # type: ignore[union-attr]
        for index, path in enumerate(self.files):
            try:
                size = Path(path).stat().st_size
            except OSError:
                continue
            remove = widgets.icon_button(
                "window-close-symbolic",
                lambda _path=path: self._remove_file(_path))
            self.file_box.append(_fit_row(file_name=True, root=widgets.list_row(
                Path(path).name, format_size(size), trailing=remove,
                last=index == len(self.files) - 1)))
        self._update_send_state()

    def _remove_file(self, path: str) -> None:
        if path in self.files:
            self.files.remove(path)
        self.refresh_files()

    def _peer_dicts(self) -> list[dict]:
        peers = self.service.list_peers()  # type: ignore[union-attr]
        return [dict(item) for item in peers]

    def refresh_devices(self) -> None:
        list_async = getattr(self.service, "list_peers_async", None)
        if callable(list_async):
            list_async(self._got_peers)
            return
        try:
            self._peers = self._peer_dicts()
        except Exception as error:
            logger.warning("refresh devices failed: %s", error)
            self._peers = []
        self._render_peers()
        self.refresh_favorites()

    def _got_peers(self, ok: bool, peers: object) -> None:
        from gi.repository import GLib

        def render() -> bool:
            try:
                self._peers = [dict(item) for item in peers] if ok else []  # type: ignore[union-attr]
            except Exception as error:
                logger.warning("render peers failed: %s", error)
                self._peers = []
            self._render_peers()
            self.refresh_favorites()
            return False

        GLib.idle_add(render)

    def _render_peers(self) -> None:
        from gi.repository import Gtk

        self._clear_box(self.device_box)
        self.device_label.set_visible(bool(self._peers))  # type: ignore[union-attr]
        for index, peer in enumerate(self._peers):
            fingerprint = str(peer.get("fingerprint", ""))
            trailing = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
            if fingerprint and fingerprint == self.selected_fp:
                trailing.append(widgets.icon_image("object-select-symbolic"))
            if fingerprint:
                trailing.append(widgets.icon_button(
                    "starred-symbolic" if is_favorite(self.config, fingerprint)
                    else "non-starred-symbolic",
                    lambda _fp=fingerprint: self._toggle_favorite(_fp)))
            self.device_box.append(_fit_row(wrap_sub=True, root=widgets.list_row(
                str(peer.get("alias", "")), _peer_sub(peer),
                leading=widgets.avatar(_initials(str(peer.get("alias", ""))), 40),
                trailing=trailing,
                on_activate=lambda _fp=fingerprint: self._select_peer(_fp),
                last=index == len(self._peers) - 1)))
        self._update_send_state()

    def _select_peer(self, fingerprint: str) -> None:
        self.selected_fp = fingerprint
        self._render_peers()

    def refresh_history(self) -> None:
        try:
            self._history = list(self.service.history.list())  # type: ignore[union-attr]
        except Exception as error:
            logger.warning("refresh history failed: %s", error)
            self._history = []
        self._clear_box(self.history_box)
        for index, entry in enumerate(self._history):
            sender = str(entry.get("sender_alias", ""))
            try:
                size = format_size(int(entry.get("size", 0))).replace(" ", "\u00a0")
            except (TypeError, ValueError):
                size = ""
            self.history_box.append(_fit_row(
                file_name=True, wrap_sub=True, root=widgets.list_row(
                str(entry.get("file_name", "file")),
                f"{sender}\u00a0· {size}" if sender else size,
                meta=_history_date(entry),
                leading=widgets.avatar(_initials(sender or "?"), 40),
                last=index == len(self._history) - 1)))
        self._refresh_chrome()

    def refresh_favorites(self) -> None:
        self._clear_box(self.favorites_box)
        favorites = favorites_list(self.config)
        self.favorites_label.set_visible(bool(favorites))  # type: ignore[union-attr]
        nearby = {str(peer.get("fingerprint", "")).lower(): peer
                  for peer in self._peers}
        for index, fingerprint in enumerate(favorites):
            peer = nearby.get(fingerprint.lower(), {})
            alias = str(peer.get("alias", "")) or "Unknown device"
            addr = str(peer.get("addr", "")) or "Not nearby"
            remove = widgets.icon_button(
                "window-close-symbolic",
                lambda _fp=fingerprint: self._unfavorite(_fp))
            self.favorites_box.append(_fit_row(wrap_sub=True, root=widgets.list_row(
                alias, f"{fingerprint[:8]} · {addr}",
                leading=widgets.avatar(_initials(alias), 40),
                trailing=remove,
                last=index == len(favorites) - 1)))
        self._refresh_chrome()

    def _unfavorite(self, fingerprint: str) -> None:
        set_favorite(self.config, fingerprint, False)
        self.refresh_favorites()
        self._render_peers()

    def _selected_peer(self) -> dict | None:
        for peer in self._peers:
            if str(peer.get("fingerprint", "")) == self.selected_fp:
                return peer
        return None

    def _on_alias_changed(self, entry: object) -> None:
        text = entry.get_text()  # type: ignore[union-attr]
        self.config.data["alias"] = text.strip()
        self.config.save()

    def _on_pin_changed(self, entry: object) -> None:
        text = entry.get_text()  # type: ignore[union-attr]
        self.config.data["require_pin"] = text.strip()
        self.config.save()

    def _on_visible_switched(self, active: bool) -> None:
        from gi.repository import GLib

        def work() -> None:
            from netif import allowed_bind_addrs

            addrs = allowed_bind_addrs()
            GLib.idle_add(self._apply_visibility, active, addrs)

        threading.Thread(target=work, daemon=True).start()

    def _apply_visibility(self, active: bool, addrs: list[str]) -> bool:
        self.service.set_visible(active, addrs)  # type: ignore[union-attr]
        self._refresh_chrome()
        self.refresh_devices()
        return False

    def _on_always_switched(self, active: bool) -> None:
        from gi.repository import GLib

        self.config.data["visibility_mode"] = (
            VISIBILITY_ALWAYS if active else "auto-10min")
        self.config.save()
        GLib.idle_add(lambda: self._refresh_chrome() or False)

    def _on_auto_accept_switched(self, active: bool) -> None:
        set_auto_accept(self.config, active)

    def _toggle_favorite(self, fingerprint: str) -> None:
        set_favorite(self.config, fingerprint,
                     not is_favorite(self.config, fingerprint))
        self.refresh_favorites()
        self._render_peers()

    def _on_clear_history(self) -> None:
        try:
            self.service.history.clear()  # type: ignore[union-attr]
        except Exception as error:
            logger.warning("clear history failed: %s", error)
        self.refresh_history()

    def show_incoming(self, session_id: str, alias: str,
                      names: list[str], total: int) -> None:
        from gi.repository import Gtk

        from gi.repository import Pango

        self._close_incoming()
        content = widgets.dialog_box(alias or "Nearby device")
        content.append(widgets.wrap_label(incoming_summary(len(names), total)))
        listing = Gtk.Box.new(Gtk.Orientation.VERTICAL, 4)
        for name in names[:3]:
            line = widgets.label(name, "xx-body")
            line.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            listing.append(line)
        if len(names) > 3:
            listing.append(widgets.label(f"+{len(names) - 3} more",
                                         "xx-secondary"))
        content.append(listing)
        buttons = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        decline = widgets.outline_button(
            "Decline",
            lambda: self._resolve_incoming(session_id, ACTION_DECLINE))
        decline.set_hexpand(True)
        accept = widgets.primary_button(
            "Accept",
            lambda: self._resolve_incoming(session_id, ACTION_ACCEPT))
        accept.set_hexpand(True)
        buttons.append(decline)
        buttons.append(accept)
        content.append(buttons)
        dialog = Gtk.Window(transient_for=self.window, modal=True)
        dialog.set_decorated(False)
        dialog.set_default_size(
            self.window.get_width() or 360,  # type: ignore[union-attr]
            self.window.get_height() or 720)  # type: ignore[union-attr]
        outer = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        outer.add_css_class("xx-page-body")
        top_gap = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        top_gap.set_vexpand(True)
        outer.append(top_gap)
        outer.append(content)
        bottom_gap = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        bottom_gap.set_vexpand(True)
        outer.append(bottom_gap)
        dialog.set_child(outer)
        dialog.present()
        self._incoming_dialog = dialog

    def _close_incoming(self) -> None:
        dialog, self._incoming_dialog = self._incoming_dialog, None
        if dialog is not None:
            try:
                dialog.close()  # type: ignore[union-attr]
            except Exception as error:
                logger.warning("incoming close failed: %s", error)

    def _resolve_incoming(self, session_id: str, action: str) -> None:
        self._close_incoming()
        resolve = getattr(self.service, "_resolve", None)
        if callable(resolve):
            try:
                resolve(session_id, action)
            except Exception as error:
                logger.warning("incoming resolve failed: %s", error)

    def _on_bus_signal(self, name: str, args: tuple) -> None:
        from gi.repository import GLib

        if name == "TransferProgress":
            transfer_id = str(args[0]) if args else ""
            sent = int(args[2]) if len(args) > 2 else 0
            total = int(args[3]) if len(args) > 3 else 0
            GLib.idle_add(self._idle_bus_progress, transfer_id, sent, total)
        elif name == "TransferDone":
            transfer_id = str(args[0]) if args else ""
            ok = bool(args[1]) if len(args) > 1 else False
            message = str(args[2]) if len(args) > 2 else ""
            if not self.current_transfer or transfer_id == self.current_transfer:
                GLib.idle_add(self._idle_done, message or "Send finished", ok)
        elif name == "IncomingRequest":
            session_id = str(args[0]) if args else ""
            alias = str(args[1]) if len(args) > 1 else ""
            names = [str(item) for item in args[3]] if len(args) > 3 else []
            total = int(args[4]) if len(args) > 4 else 0
            GLib.idle_add(self._idle_incoming, session_id, alias, names, total)

    def _idle_incoming(self, session_id: str, alias: str,
                       names: list[str], total: int) -> bool:
        self.show_incoming(session_id, alias, names, total)
        self.refresh_history()
        return False

    def _idle_bus_progress(self, transfer_id: str, sent: int, total: int) -> bool:
        if self.current_transfer and transfer_id != self.current_transfer:
            return False
        self._set_sending(True)
        if total > 0:
            self.progress_bar.set_fraction(  # type: ignore[union-attr]
                max(0.0, min(1.0, sent / total)))
        self._set_status(f"{format_size(sent)} / {format_size(total)}")
        return False

    def _set_status(self, text: str) -> None:
        self.status_label.set_text(text)  # type: ignore[union-attr]
        self.status_label.set_visible(bool(text))  # type: ignore[union-attr]

    def _set_sending(self, sending: bool) -> None:
        """While sending, the one button is Cancel and the bar shows."""
        self.progress_bar.set_visible(sending)  # type: ignore[union-attr]
        self.cancel_button.set_visible(sending)  # type: ignore[union-attr]
        self.send_button.set_visible(not sending)  # type: ignore[union-attr]

    def _update_send_state(self) -> None:
        self.send_button.set_sensitive(  # type: ignore[union-attr]
            send_ready(self._selected_peer() is not None, len(self.files)))

    def _on_add_files(self) -> None:
        from gi.repository import Gtk

        dialog = Gtk.FileDialog(title="Choose files to send")
        dialog.open_multiple(self.window, None, self._on_files_chosen)

    def _on_files_chosen(self, dialog: object, result: object) -> None:
        from gi.repository import GLib

        try:
            chosen = dialog.open_multiple_finish(result)  # type: ignore[union-attr]
        except GLib.Error:
            return
        paths = [item.get_path() for item in chosen]
        self.files.extend(item for item in paths if item)
        GLib.idle_add(self._idle_refresh_files)

    def _idle_refresh_files(self) -> bool:
        self.refresh_files()
        return False

    def _on_send(self) -> None:
        from gi.repository import GLib

        peer = self._selected_peer()
        if peer is None or not self.files:
            return
        files = list(self.files)
        pin_text = self.send_pin.get_text().strip()
        self._set_status(f"Sending to {peer.get('alias', '')}")
        self.progress_bar.set_fraction(0.0)  # type: ignore[union-attr]
        self._set_sending(True)

        def on_done(ok: bool, message: str) -> None:
            GLib.idle_add(self._idle_done, message, ok)

        start_async = getattr(self.service, "start_send_async", None)
        if callable(start_async):
            def on_started(ok: bool, result: str) -> None:
                if ok:
                    self.current_transfer = result
                else:
                    GLib.idle_add(self._idle_done, result, False)

            try:
                start_async(str(peer.get("fingerprint", "")), files,  # type: ignore[operator]
                            pin_text, on_started, on_done)
            except Exception as error:
                logger.warning("send failed: %s", error)
                GLib.idle_add(self._idle_done, "Send failed", False)
            return
        try:
            self.current_transfer = self.service.start_send(  # type: ignore[union-attr]
                str(peer.get("fingerprint", "")), files, pin_text, on_done)
        except ValueError as error:
            GLib.idle_add(self._idle_done, str(error), False)
        except Exception as error:
            logger.warning("send failed: %s", error)
            GLib.idle_add(self._idle_done, "Send failed", False)

    def _on_cancel(self) -> None:
        transfer_id = self.current_transfer
        if not transfer_id:
            return
        try:
            self.service.cancel_send(transfer_id)  # type: ignore[union-attr]
        except Exception as error:
            logger.warning("cancel send failed: %s", error)

    def _idle_done(self, message: str, _ok: bool) -> bool:
        self._set_status(message)
        self._set_sending(False)
        if message == PIN_NEEDED:
            self.send_pin.set_visible(True)  # type: ignore[union-attr]
        self.current_transfer = None
        return False
