"""GUI-side contact writes through the contacts service (single writer).

The service owns ``io.piercingxx.Contacts`` and is the only process
that writes the SQLite store or emits ``Changed``: D-Bus signals
carry the sender's unique name, and dialer/TxxT/shell subscribers
filter on the service name, so a GUI writing SQLite directly (or
re-emitting ``Changed`` from its own connection) never reaches them.
GUIs keep READING SQLite directly for speed; every write here goes
over the session bus with auto-activation, so a cold (idle-exited)
service starts on demand.

Two flavors: ``*_sync`` for worker threads and tests (blocking off
the main loop is fine), ``*_async`` for main-thread use (never blocks
the GTK loop). D-Bus errors map back onto the store exceptions the UI
already handles, so callers keep their ``except NotFoundError`` paths.
"""

from __future__ import annotations

from xx_contacts.service import (
    BUS_NAME,
    ERROR_INVALID_ARGUMENT,
    ERROR_NOT_FOUND,
    INTERFACE,
    OBJECT_PATH,
)
from xx_contacts.store import NotFoundError

#: D-Bus round-trip budget for local writes (photos included).
CALL_TIMEOUT_MS = 5000

#: D-Bus wait budget for ``Pick``: the call stays open until the user
#: picks or cancels, so this must cover human time, not machine time.
PICK_TIMEOUT_MS = 300000


def _remote_error_name(exc: Exception) -> str | None:
    """D-Bus error name for a failed call, or None for transport errors."""
    try:
        from gi.repository import Gio

        name = Gio.DBusError.get_remote_error(exc)
    except Exception:
        name = None
    if name:
        return str(name)
    text = str(exc)
    for known in (ERROR_NOT_FOUND, ERROR_INVALID_ARGUMENT,
                  INTERFACE + ".NotImplemented"):
        if known in text:
            return known
    return None


def _remote_message(exc: Exception) -> str:
    try:
        from gi.repository import Gio

        stripped = Gio.DBusError.strip_remote_error(getattr(exc, "message",
                                                            None) or str(exc))
        if stripped:
            return str(stripped)
    except Exception:
        pass
    return str(exc)


def raise_for_call_error(exc: Exception) -> Exception:
    """Map a failed D-Bus write onto the exception the UI already handles."""
    name = _remote_error_name(exc)
    message = _remote_message(exc)
    if name == ERROR_NOT_FOUND:
        return NotFoundError(message or "unknown contact")
    if name == ERROR_INVALID_ARGUMENT:
        return ValueError(message or "invalid argument")
    return RuntimeError(f"contacts service is not reachable: {message}")


def gui_connection():
    """Session-bus connection for a worker thread; raises RuntimeError."""
    from gi.repository import Gio

    try:
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except Exception as exc:
        raise RuntimeError(
            f"contacts service is not reachable: {exc}") from exc


def _reply_type(signature: str | None):
    if signature is None:
        return None
    from gi.repository import GLib

    return GLib.VariantType.new(signature)


def _call_sync(connection, method: str, parameters,
               reply_signature: str | None, timeout_ms: int = CALL_TIMEOUT_MS):
    from gi.repository import Gio

    try:
        reply = connection.call_sync(
            BUS_NAME, OBJECT_PATH, INTERFACE, method, parameters,
            _reply_type(reply_signature), Gio.DBusCallFlags.NONE,
            timeout_ms, None)
    except Exception as exc:
        raise raise_for_call_error(exc) from exc
    if reply is None:
        return ()
    return reply.unpack()


def _params(signature: str, *args):
    from gi.repository import GLib

    return GLib.Variant(signature, args)


def create_sync(connection, vcard: str) -> str:
    """Create a contact through the service; return the new uid."""
    (uid,) = _call_sync(connection, "Create", _params("(s)", vcard), "(s)")
    return str(uid)


def update_sync(connection, uid: str, vcard: str) -> None:
    """Replace ``uid`` through the service (photo rides in PHOTO)."""
    _call_sync(connection, "Update", _params("(ss)", uid, vcard), None)


def delete_sync(connection, uid: str) -> None:
    """Delete ``uid`` through the service."""
    _call_sync(connection, "Delete", _params("(s)", uid), None)


def set_tags_sync(connection, uid: str, tags) -> None:
    """Replace all tags on ``uid`` through the service."""
    _call_sync(connection, "SetTags",
               _params("(sas)", uid, [str(t) for t in tags]), None)


def update_tags_sync(connection, uid: str, add=(),
                     remove=()) -> int:
    """Atomically add/remove tags through the service; return revision."""
    (revision,) = _call_sync(
        connection, "UpdateTags",
        _params("(sasas)", uid, [str(t) for t in add],
                [str(t) for t in remove]), "(t)")
    return int(revision)


def import_sync(connection, vcards: str) -> tuple[int, int, int]:
    """Bulk import through the service; return ``(created, updated,
    skipped)`` from one transaction with one ``Changed`` signal."""
    created, updated, skipped = _call_sync(
        connection, "Import", _params("(s)", vcards), "(uuu)")
    return int(created), int(updated), int(skipped)


def form_to_vcard(uid: str, data, photo_bytes: bytes | None = None) -> str:
    """Serialize editor ``ContactData`` to the vCard ``Create``/``Update``
    carries: tags as ``X-`` lines, groups as ``CATEGORIES``, unseen
    properties verbatim from ``extra``, and picked photo bytes as a
    data-URI ``PHOTO`` line the service materializes into the photo
    file. An empty ``uid`` (new contact) emits a bare ``UID:`` line the
    parser drops, so the service assigns the uid.
    """
    from xx_contacts import vcard as vcard_module
    from xx_contacts.store import ContactRecord

    record = ContactRecord()
    for field_name in data.__dataclass_fields__:
        setattr(record, field_name, getattr(data, field_name))
    record.uid = uid or ""
    return vcard_module.contact_to_vcard(record, photo_bytes=photo_bytes)


def pick_sync(connection, kind: str) -> tuple[str, str]:
    """Pick through the service; ``("", "")`` means the user cancelled.

    Blocking with a human-scale timeout: never call this on the GTK
    main thread, use :func:`pick_async` there.
    """
    uid, value = _call_sync(connection, "Pick", _params("(s)", kind),
                            "(ss)", PICK_TIMEOUT_MS)
    return str(uid), str(value)


def _call_async(method: str, parameters, reply_signature: str | None,
                on_done, on_error=None,
                timeout_ms: int = CALL_TIMEOUT_MS) -> None:
    """Main-thread async write: bus lookup, then an auto-activating call.

    Callbacks take exactly two args (no ``user_data``): ``on_done``
    receives the unpacked out-args tuple, ``on_error`` the mapped
    exception.
    """
    from gi.repository import Gio, GLib

    def _fail(exc: Exception) -> None:
        if on_error is not None:
            try:
                on_error(raise_for_call_error(exc))
            except Exception:
                pass

    def _on_reply(connection, result) -> None:
        try:
            reply = connection.call_finish(result)
        except Exception as exc:
            _fail(exc)
            return
        try:
            on_done(() if reply is None else reply.unpack())
        except Exception as exc:
            _fail(exc)

    def _on_bus(_source, result) -> None:
        try:
            connection = Gio.bus_get_finish(result)
        except GLib.Error as exc:
            _fail(exc)
            return
        try:
            connection.call(
                BUS_NAME, OBJECT_PATH, INTERFACE, method, parameters,
                _reply_type(reply_signature), Gio.DBusCallFlags.NONE,
                timeout_ms, None, _on_reply)
        except Exception as exc:
            _fail(exc)

    Gio.bus_get(Gio.BusType.SESSION, None, _on_bus)


def create_async(vcard: str, on_done, on_error=None) -> None:
    """Async :func:`create_sync`: ``on_done`` receives the new uid."""

    def _done(out) -> None:
        on_done(str(out[0]))

    _call_async("Create", _params("(s)", vcard), "(s)", _done, on_error)


def update_async(uid: str, vcard: str, on_done, on_error=None) -> None:
    """Async :func:`update_sync`: ``on_done`` receives no args."""

    def _done(_out) -> None:
        on_done()

    _call_async("Update", _params("(ss)", uid, vcard), None, _done, on_error)


def delete_async(uid: str, on_done, on_error=None) -> None:
    """Async :func:`delete_sync`: ``on_done`` receives no args."""

    def _done(_out) -> None:
        on_done()

    _call_async("Delete", _params("(s)", uid), None, _done, on_error)


def update_tags_async(uid: str, add, remove, on_done,
                      on_error=None) -> None:
    """Async :func:`update_tags_sync`: ``on_done`` receives the revision."""

    def _done(out) -> None:
        on_done(int(out[0]))

    _call_async("UpdateTags",
                _params("(sasas)", uid, [str(t) for t in add],
                        [str(t) for t in remove]),
                "(t)", _done, on_error)


def import_async(vcards: str, on_done, on_error=None) -> None:
    """Async :func:`import_sync`: ``on_done`` receives the counts tuple."""

    def _done(out) -> None:
        on_done((int(out[0]), int(out[1]), int(out[2])))

    _call_async("Import", _params("(s)", vcards), "(uuu)", _done, on_error)


def pick_async(kind: str, on_done, on_error=None) -> None:
    """Async :func:`pick_sync`: ``on_done`` receives ``(uid, value)``."""

    def _done(out) -> None:
        on_done((str(out[0]), str(out[1])))

    _call_async("Pick", _params("(s)", kind), "(ss)", _done, on_error,
                PICK_TIMEOUT_MS)


def reply_pick_sync(connection, token: str, uid: str,
                    value: str) -> None:
    """Report a pick-mode choice back to the holding ``Pick`` call.

    Called by the ``--pick`` GUI once the user picks or cancels
    (cancel is ``("", "")``); the service settles the caller's held
    ``Pick`` from there. Blocking: the GUI calls this from a worker
    thread, never the GTK main thread.
    """
    _call_sync(connection, "PickReply",
               _params("(sss)", str(token), str(uid), str(value)), None)


def reply_pick_async(token: str, uid: str, value: str, on_done,
                     on_error=None) -> None:
    """Async :func:`reply_pick_sync` for main-thread GUI use."""

    def _done(_out) -> None:
        on_done()

    _call_async("PickReply",
                _params("(sss)", str(token), str(uid), str(value)),
                None, _done, on_error)
