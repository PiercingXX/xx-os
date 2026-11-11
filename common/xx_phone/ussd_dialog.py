"""Text-first sheet for USSD replies and the ``*#06#`` IMEI.

Shared by the XX-WM shell dialer and the xx-dialer app so a code typed in
either behaves the same: the network's reply as plain text, a reply field
only while the network is waiting for one (USSD State user-response), a
Cancel that ends the network session, and errors as plain sentences. The
session logic lives in :mod:`xx_phone.mmi`; this module only draws it with
the shared ``xx_ui`` widgets (themed dialog, no stock chrome).

GTK is imported lazily so ``import xx_phone`` stays toolkit-free.
"""
from __future__ import annotations

from types import SimpleNamespace

from xx_phone import mmi


def _system_bus_async(on_bus) -> None:
    """``on_bus(bus_or_None)`` once the system bus connection is ready,
    without blocking the main loop."""
    from gi.repository import Gio

    def _done(_src, result) -> None:
        try:
            bus = Gio.bus_get_finish(result)
        except Exception:
            bus = None
        on_bus(bus)

    try:
        Gio.bus_get(Gio.BusType.SYSTEM, None, _done)
    except Exception:
        on_bus(None)


def _window(title: str, parent=None):
    from gi.repository import Gtk
    from xx_ui import widgets

    window = Gtk.Window(title=title)
    window.set_decorated(False)
    window.add_css_class('xx-dialog')
    if parent is not None:
        try:
            window.set_transient_for(parent)
            window.set_modal(True)
        except Exception:
            pass
    body = widgets.dialog_box(title)
    # Phone width floor (same as the shell's polkit dialog): content-sized
    # dialogs otherwise shrink until their own buttons ellipsize.
    body.set_size_request(312, -1)
    window.set_child(body)
    return window, body


def open_ussd_dialog(code: str, parent=None, bus=None, attach_entry=None,
                     modem_path: str | None = None):
    """Send ``code`` and show the conversation. Returns a namespace with
    the ``session`` and ``window`` (tests and callers may close it).
    ``attach_entry(entry)`` lets the shell hook its on-screen keyboard."""
    from gi.repository import GLib, Gio, Gtk
    from xx_ui import widgets

    window, body = _window(mmi.normalize_code(code) or 'USSD', parent)
    message = widgets.wrap_label('Sending…', cls='xx-body')
    message.set_selectable(True)
    body.append(message)
    status = widgets.caption('')
    status.set_visible(False)
    body.append(status)

    entry = widgets.deferred_entry('Reply')
    entry.set_input_purpose(Gtk.InputPurpose.PHONE)
    entry.set_visible(False)
    if attach_entry is not None:
        try:
            attach_entry(entry)
        except Exception:
            pass
    body.append(entry)

    buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    body.append(buttons)
    state = SimpleNamespace(session=None, window=window, closing=False)

    def _close(*_args) -> None:
        if state.closing:
            return
        state.closing = True
        if state.session is not None:
            state.session.cancel()
        try:
            window.close()
        except Exception:
            pass

    def _send(*_args) -> None:
        session = state.session
        text = entry.get_text()
        if session is None or not session.awaiting_reply or not text.strip():
            return
        entry.set_text('')
        session.respond(text)

    send_btn = widgets.primary_button('Send', _send)
    send_btn.set_hexpand(True)
    send_btn.set_visible(False)
    buttons.append(send_btn)
    close_btn = widgets.outline_button('Cancel', _close)
    close_btn.set_hexpand(True)
    buttons.append(close_btn)
    entry.connect('activate', _send)

    def _render(session: mmi.UssdSession) -> None:
        if state.closing:
            return
        if session.error:
            text = session.error
        elif session.messages:
            text = session.messages[-1]
        else:
            text = 'Sending…'
        message.set_text(text)
        line = mmi.state_text(session)
        status.set_text(line)
        status.set_visible(bool(line))
        asking = session.awaiting_reply
        entry.set_visible(asking)
        send_btn.set_visible(asking)
        open_session = session.busy or session.state in (
            mmi.STATE_ACTIVE, mmi.STATE_USER_RESPONSE)
        widgets.set_button_text(
            close_btn, 'Cancel' if open_session and not session.error else 'Close')

    def _on_close_request(_win) -> bool:
        _close()
        return False

    window.connect('close-request', _on_close_request)

    def _start(conn) -> None:
        if state.closing:
            return
        session = mmi.UssdSession(conn, GLib, Gio, modem_path=modem_path,
                                  on_change=_render)
        state.session = session
        session.start(code)

    if bus is not None:
        _start(bus)
    else:
        _system_bus_async(_start)
    window.present()
    return state


def open_imei_dialog(parent=None, bus=None):
    """``*#06#``: show every modem's IMEI, read locally."""
    from gi.repository import Gio, Gtk
    from xx_ui import widgets

    window, body = _window('IMEI', parent)
    message = widgets.wrap_label('Reading…', cls='xx-body')
    message.set_selectable(True)
    body.append(message)
    buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    close_btn = widgets.outline_button('Close', lambda *_a: window.close())
    close_btn.set_hexpand(True)
    buttons.append(close_btn)
    body.append(buttons)

    def _done(imeis, error) -> None:
        message.set_text(mmi.imei_text(imeis) if imeis else error)

    def _with_bus(conn) -> None:
        mmi.fetch_imei(conn, _done, Gio)

    if bus is not None:
        _with_bus(bus)
    else:
        _system_bus_async(_with_bus)
    window.present()
    return SimpleNamespace(window=window, message=message)
