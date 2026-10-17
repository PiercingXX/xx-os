"""Session-bus contacts service: ``io.piercingxx.Contacts1`` (Revision 2 §3).

Headless entry: ``python3 -m xx_contacts --service`` owns the bus name,
serves ``/io/piercingxx/Contacts`` from the SQLite store with an
in-memory lookup index, emits ``Changed`` on writes, and exits after
60 s without a method call. ``Pick`` validates the kind, launches the
``xx-contacts --pick`` GUI (which owns all GTK), and replies once the
GUI reports back over ``PickReply`` — with the pick, or with
``("", "")`` on cancel. The service itself never touches GTK.

Method dispatch takes the exact Gio shapes (tuple ``GLib.Variant``
parameters, ``invocation.return_value`` / ``invocation.return_dbus_error``)
so the same code runs against the real bus and the pytest fakes.
"""

from __future__ import annotations

import os
import time

BUS_NAME = "io.piercingxx.Contacts"
OBJECT_PATH = "/io/piercingxx/Contacts"
INTERFACE = "io.piercingxx.Contacts1"

IDLE_EXIT_SECONDS = 60

#: Environment override for the idle exit, so tests can shorten the wait.
IDLE_SECONDS_ENV = "XX_CONTACTS_IDLE_SECONDS"


def idle_exit_seconds() -> float:
    """Configured idle exit: env override, else :data:`IDLE_EXIT_SECONDS`."""
    try:
        wanted = float(os.environ.get(IDLE_SECONDS_ENV, "") or 0.0)
    except ValueError:
        return float(IDLE_EXIT_SECONDS)
    if wanted <= 0.0:
        return float(IDLE_EXIT_SECONDS)
    return max(1.0, wanted)

ERROR_NOT_FOUND = INTERFACE + ".NotFound"
ERROR_INVALID_ARGUMENT = INTERFACE + ".InvalidArgument"
ERROR_NOT_IMPLEMENTED = INTERFACE + ".NotImplemented"


def default_photos_dir(db_path: str) -> str | None:
    """Sibling ``photos/`` dir for a file store; None for ``:memory:``."""
    if not db_path or db_path == ":memory:":
        return None
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "photos")


def _is_within_directory(path: str, directory: str) -> bool:
    try:
        common = os.path.commonpath(
            (os.path.abspath(path), os.path.abspath(directory)))
    except (ValueError, OSError):
        return False
    return common == os.path.abspath(directory)

INTROSPECTION_XML = """\
<node>
  <interface name="io.piercingxx.Contacts1">
    <method name="LookupNumber">
      <arg direction="in" type="s" name="number"/>
      <arg direction="out" type="s" name="uid"/>
      <arg direction="out" type="s" name="display_name"/>
      <arg direction="out" type="as" name="tags"/>
    </method>
    <method name="Search">
      <arg direction="in" type="s" name="query"/>
      <arg direction="in" type="u" name="limit"/>
      <arg direction="out" type="a(sss)" name="results"/>
    </method>
    <method name="LookupEmail">
      <arg direction="in" type="s" name="email"/>
      <arg direction="out" type="s" name="uid"/>
      <arg direction="out" type="s" name="display_name"/>
    </method>
    <method name="Get">
      <arg direction="in" type="s" name="uid"/>
      <arg direction="out" type="s" name="vcard"/>
    </method>
    <method name="List">
      <arg direction="in" type="t" name="since_revision"/>
      <arg direction="out" type="t" name="revision"/>
      <arg direction="out" type="as" name="changed_uids"/>
      <arg direction="out" type="as" name="deleted_uids"/>
    </method>
    <method name="Create">
      <arg direction="in" type="s" name="vcard"/>
      <arg direction="out" type="s" name="uid"/>
    </method>
    <method name="Update">
      <arg direction="in" type="s" name="uid"/>
      <arg direction="in" type="s" name="vcard"/>
    </method>
    <method name="Delete">
      <arg direction="in" type="s" name="uid"/>
    </method>
    <method name="SetTags">
      <arg direction="in" type="s" name="uid"/>
      <arg direction="in" type="as" name="tags"/>
    </method>
    <method name="UpdateTags">
      <arg direction="in" type="s" name="uid"/>
      <arg direction="in" type="as" name="add"/>
      <arg direction="in" type="as" name="remove"/>
      <arg direction="out" type="t" name="revision"/>
    </method>
    <method name="Import">
      <arg direction="in" type="s" name="vcards"/>
      <arg direction="out" type="u" name="created"/>
      <arg direction="out" type="u" name="updated"/>
      <arg direction="out" type="u" name="skipped"/>
    </method>
    <method name="Pick">
      <arg direction="in" type="s" name="kind"/>
      <arg direction="out" type="s" name="uid"/>
      <arg direction="out" type="s" name="value"/>
    </method>
    <method name="PickReply">
      <arg direction="in" type="s" name="token"/>
      <arg direction="in" type="s" name="uid"/>
      <arg direction="in" type="s" name="value"/>
    </method>
    <signal name="Changed">
      <arg type="t" name="revision"/>
      <arg type="as" name="uids"/>
    </signal>
  </interface>
</node>
"""


def emit_changed(connection, revision: int,
                 uids: list[str]) -> bool:
    """Emit the ``Changed`` signal on a live connection; fail open.

    Only the service calls this, on its own bus connection: the signal
    carries the sender's unique name, and clients filter on the
    well-known ``io.piercingxx.Contacts`` owner, so a GUI re-emit from
    its own unique name would be dropped. ``None`` connections (tests,
    offline) report False instead of raising.
    """
    if connection is None:
        return False
    try:
        from gi.repository import GLib

        connection.emit_signal(
            None, OBJECT_PATH, INTERFACE, "Changed",
            GLib.Variant("(tas)", (int(revision), list(uids))))
    except Exception:
        return False
    return True


class ContactsService:
    """D-Bus method dispatch over a ContactStore + LookupIndex."""

    def __init__(self, store, index, default_region: str = "US",
                 photos_dir: str | None = None) -> None:
        self._store = store
        self._index = index
        self._default_region = default_region or "US"
        self._connection = None
        self._pending_picks: dict[str, object] = {}
        #: Display hook for ``Pick``: ``(kind, rows, on_done) -> bool``.
        #: None (default) launches the pick-mode GUI; tests inject a fake.
        self.pick_presenter = None
        #: GUI-launch hook for ``Pick``: ``(kind, token) -> bool``.
        #: None (default) spawns ``xx-contacts --pick``; tests inject
        #: a fake that settles the pick through :meth:`reply_pick`.
        self.pick_launcher = None
        self.last_activity = time.monotonic()
        if photos_dir is None:
            photos_dir = default_photos_dir(getattr(store, "path", ""))
        self._photos_dir = photos_dir
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        from xx_contacts.store import ContactRecord  # noqa: F401 (typing aid)

        rows = []
        for record in self._store.all_records():
            rows.append((record.uid, record.display_name,
                         [(p.number_raw, p.number_e164, p.number_digits)
                          for p in record.phones],
                         self._store.get_tags(record.uid)))
        self._index.rebuild(rows)

    def _reindex_record(self, uid: str) -> None:
        try:
            record = self._store.get(uid)
        except Exception:
            self._index.remove(uid)
            return
        self._index.upsert(
            uid, record.display_name,
            [(p.number_raw, p.number_e164, p.number_digits)
             for p in record.phones],
            self._store.get_tags(uid))

    def _touch(self) -> None:
        self.last_activity = time.monotonic()

    def pick_pending(self) -> bool:
        """True while a ``Pick`` invocation waits for the user to choose."""
        return bool(self._pending_picks)

    def reply_pick(self, token: str, uid: str, value: str) -> bool:
        """Settle the held ``Pick`` for ``token``; False when unknown.

        The GUI calls this over ``PickReply`` after the user picks or
        cancels; a cancel reports ``("", "")``. ``Pick`` never writes,
        so settling emits no ``Changed``.
        """
        from gi.repository import GLib

        pending = self._pending_picks.pop(str(token or ""), None)
        if pending is None:
            return False
        pending.return_value(
            GLib.Variant("(ss)", (str(uid), str(value))))
        self._touch()
        return True

    def _launch_pick_gui(self, kind: str, token: str) -> bool:
        """Spawn the pick-mode GUI; False when it cannot start.

        Never blocks: the child owns all GTK and reports back over
        ``PickReply``. The reply token travels only in the child
        command line, never in a log line.
        """
        import subprocess
        import sys

        db_path = getattr(getattr(self, "_store", None), "path", "") or ""
        argv = [sys.executable, "-m", "xx_contacts",
                "--pick", kind, "--reply-to", token]
        if db_path and db_path != ":memory:":
            argv += ["--db", db_path]
        try:
            subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL,
                             close_fds=True)
        except Exception:
            return False
        return True

    def request_pick(self, kind: str, invocation,
                     presenter=None) -> None:
        """Serve ``Pick``: validate the kind, launch the GUI, hold the call.

        The method invocation stays open until the pick-mode GUI
        reports back over ``PickReply``: a tap replies ``(uid, value)``,
        a cancel or a closed window replies ``("", "")``. ``Pick``
        never writes, so it emits no ``Changed``. A second ``Pick``
        while one is open fails, and a GUI that cannot start fails,
        instead of leaving the caller hanging. The caller-visible
        contract is unchanged: ``Pick(s kind) -> (s uid, s value)``.
        """
        import secrets

        from xx_contacts import picker as picker_module
        import xx_phone  # noqa: E402

        try:
            normalized = picker_module.normalize_kind(kind)
        except ValueError as exc:
            invocation.return_dbus_error(ERROR_INVALID_ARGUMENT, str(exc))
            return
        if self._pending_picks:
            invocation.return_dbus_error(
                ERROR_INVALID_ARGUMENT,
                "a contact pick is already in progress")
            return
        show = presenter
        if show is None:
            show = self.pick_presenter
        token = secrets.token_urlsafe(16)
        if show is not None:
            rows = picker_module.build_pick_rows(
                normalized, self._store.all_records(),
                xx_phone.display_country(self._default_region))

            def _on_settled(uid: str, value: str,
                            _token: str = token) -> None:
                self.reply_pick(_token, uid, value)

            self._pending_picks[token] = invocation
            self._touch()
            try:
                shown = bool(show(normalized, rows, _on_settled))
            except Exception:
                shown = False
            if not shown:
                self._pending_picks.pop(token, None)
                invocation.return_dbus_error(
                    ERROR_NOT_IMPLEMENTED,
                    "contact picker needs a display")
            return
        launch = self.pick_launcher
        if launch is None:
            launch = self._launch_pick_gui
        self._pending_picks[token] = invocation
        self._touch()
        try:
            started = bool(launch(normalized, token))
        except Exception:
            started = False
        if not started:
            self._pending_picks.pop(token, None)
            invocation.return_dbus_error(
                ERROR_NOT_IMPLEMENTED,
                "contact picker needs a display")

    def _emit_changed(self, revision: int, uids: list[str]) -> None:
        emit_changed(self._connection, revision, uids)

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_activity

    # ------------------------------------------------------------ operations

    def lookup_number(self, number: str) -> tuple[str, str, list[str]]:
        hit = self._index.lookup(number)
        if hit is None:
            return "", "", []
        return hit.uid, hit.display_name, list(hit.tags)

    def search(self, query: str, limit: int) -> list[tuple[str, str, str]]:
        capped = max(0, min(int(limit), 500))
        return self._store.search(query, capped)

    def lookup_email(self, email: str) -> tuple[str, str]:
        """Exact email owner: ``(uid, display_name)``, else ``("", "")``.

        Never an error on a miss (like ``LookupNumber``); sender and
        autocomplete callers fall back to the raw address.
        """
        hit = self._store.lookup_email(email)
        if hit is None:
            return "", ""
        return hit[0], hit[1]

    def get(self, uid: str) -> str:
        from xx_contacts import vcard as vcard_module
        from xx_contacts.store import NotFoundError

        try:
            record = self._store.get(uid)
        except NotFoundError as exc:
            raise LookupError(str(exc)) from exc
        return vcard_module.contact_to_vcard(record)

    def list_changes(self, since_revision: int
                     ) -> tuple[int, list[str], list[str]]:
        return self._store.list_changes(int(since_revision))

    def _sanitize_photo_path(self, data) -> None:
        """Drop a URI photo path that escapes the photo dir.

        An imported ``PHOTO;VALUE=URI:/elsewhere`` must never reach the
        database: export/share would otherwise embed that file's bytes.
        Embedded payloads (``data.extra``) are unaffected; they are
        staged through the photo file below.
        """
        path = (getattr(data, "photo_path", "") or "")
        if not path or not self._photos_dir:
            return
        if not _is_within_directory(path, self._photos_dir):
            data.photo_path = ""

    def _stage_photo(self, data, provisional_uid: str
                     ) -> tuple[str, str]:
        """Stage an embedded vCard PHOTO to a temp file.

        Returns ``(tmp, final)``; the caller replaces the final path
        only after the row commits, so a failed write never destroys
        the photo already stored there. Undecodable payloads stay in
        ``extra`` and round-trip, exactly as before.
        """
        if not self._photos_dir:
            return "", ""
        from xx_contacts import photos as photos_module

        try:
            return photos_module.stage_photo_from_extra(
                data, self._photos_dir, provisional_uid)
        except Exception:
            return "", ""

    def _commit_staged_photo(self, tmp_path: str, final_path: str) -> None:
        if not tmp_path:
            return
        from xx_contacts import photos as photos_module

        try:
            photos_module.commit_staged_photo(tmp_path, final_path)
        except Exception:
            photos_module.discard_staged_photo(tmp_path)

    def _discard_staged_photo(self, tmp_path: str) -> None:
        if not tmp_path:
            return
        from xx_contacts import photos as photos_module

        photos_module.discard_staged_photo(tmp_path)

    def _drop_replaced_photo(self, old_path: str, new_path: str) -> None:
        """Delete a photo file the write just orphaned; guarded to stay
        inside the photo dir, since imported URI values may point
        anywhere and must never be unlinked by a contact edit."""
        if not old_path or old_path == (new_path or ""):
            return
        if not self._photos_dir or not _is_within_directory(
                old_path, self._photos_dir):
            return
        try:
            os.unlink(old_path)
        except OSError:
            pass

    def create(self, vcard_text: str) -> str:
        from xx_contacts import vcard as vcard_module
        from xx_contacts.store import (
            NotFoundError,
            StoreError,
            is_valid_uid,
            new_uid,
        )

        try:
            data, hint = vcard_module.contact_from_vcard(
                vcard_text, self._default_region)
        except ValueError as exc:
            raise ValueError(f"no vCard block found: {exc}") from exc
        chosen = hint if is_valid_uid(hint) else new_uid()
        try:
            self._store.get(chosen)
        except NotFoundError:
            pass
        else:
            raise StoreError(f"contact already exists: {chosen}")
        self._sanitize_photo_path(data)
        tmp_path, final_path = self._stage_photo(data, chosen)
        try:
            uid = self._store.create(data, chosen)
        except StoreError:
            self._discard_staged_photo(tmp_path)
            raise
        self._commit_staged_photo(tmp_path, final_path)
        self._reindex_record(uid)
        revision = self._store.get_revision()
        self._emit_changed(revision, [uid])
        return uid

    def update(self, uid: str, vcard_text: str) -> None:
        from xx_contacts import vcard as vcard_module
        from xx_contacts.store import NotFoundError

        try:
            data, _hint = vcard_module.contact_from_vcard(
                vcard_text, self._default_region)
        except ValueError as exc:
            raise ValueError(f"no vCard block found: {exc}") from exc
        try:
            old = self._store.get(uid)
        except NotFoundError as exc:
            raise LookupError(str(exc)) from exc
        self._sanitize_photo_path(data)
        tmp_path, final_path = self._stage_photo(data, uid)
        try:
            revision = self._store.update(uid, data)
        except NotFoundError as exc:
            self._discard_staged_photo(tmp_path)
            raise LookupError(str(exc)) from exc
        self._commit_staged_photo(tmp_path, final_path)
        self._drop_replaced_photo(old.photo_path or "", data.photo_path or "")
        self._reindex_record(uid)
        self._emit_changed(revision, [uid])

    def delete(self, uid: str) -> None:
        from xx_contacts.store import NotFoundError

        try:
            revision = self._store.delete(uid)
        except NotFoundError as exc:
            raise LookupError(str(exc)) from exc
        self._index.remove(uid)
        self._emit_changed(revision, [uid])

    def set_tags(self, uid: str, tags: list[str]) -> None:
        from xx_contacts.store import NotFoundError, VALID_TAGS

        unknown = [t for t in tags if t not in VALID_TAGS]
        if unknown:
            raise ValueError(f"unknown tags: {', '.join(sorted(unknown))}")
        try:
            revision = self._store.set_tags(uid, tags)
        except NotFoundError as exc:
            raise LookupError(str(exc)) from exc
        self._reindex_record(uid)
        self._emit_changed(revision, [uid])

    def update_tags(self, uid: str, add: list[str],
                    remove: list[str]) -> int:
        """Atomically add/remove tags; return the new revision.

        One store transaction, so two clients toggling different tags
        cannot lose each other's change (unlike Get + SetTags). Unknown
        tags are rejected, exactly as in ``set_tags``.
        """
        from xx_contacts.store import NotFoundError, VALID_TAGS

        unknown = [t for t in list(add or ()) + list(remove or ())
                   if t not in VALID_TAGS]
        if unknown:
            raise ValueError(f"unknown tags: {', '.join(sorted(set(unknown)))}")
        try:
            revision = self._store.update_tags(uid, add or (), remove or ())
        except NotFoundError as exc:
            raise LookupError(str(exc)) from exc
        self._reindex_record(uid)
        self._emit_changed(revision, [uid])
        return revision

    def import_vcards(self, vcards_text: str) -> tuple[int, int, int]:
        """Bulk import in one transaction with one ``Changed`` signal.

        The same duplicate rule as the UI's dry-run plan (same UID, or
        same normalized name + phone): matches update, the rest create,
        empty cards skip. One store revision covers the whole batch, so
        a 2,000-contact file costs one transaction, one revision bump
        and one signal instead of 2,000 of each through ``Create``.
        """
        from xx_contacts import import_export as import_export_module
        from xx_contacts.store import StoreError

        incoming = import_export_module.parse_text(
            vcards_text or "", self._default_region)
        if not incoming:
            raise ValueError("no vCard block found")
        plan = import_export_module.plan_import(
            self._store.all_records(), incoming, self._default_region)
        staged: list[tuple[str, str, str]] = []
        for item in plan.creates:
            self._sanitize_photo_path(item.data)
            tmp_path, final_path = self._stage_photo(item.data, item.uid)
            if tmp_path:
                staged.append((tmp_path, final_path, ""))
        for item in plan.updates:
            try:
                old = self._store.get(item.uid)
            except Exception:
                old = None
            self._sanitize_photo_path(item.data)
            tmp_path, final_path = self._stage_photo(item.data, item.uid)
            if tmp_path:
                staged.append(
                    (tmp_path, final_path,
                     old.photo_path if old is not None else ""))
        try:
            revision, created, updated = self._store.apply_import(
                [(item.uid, item.data) for item in plan.creates],
                [(item.uid, item.data) for item in plan.updates])
        except (StoreError, LookupError):
            for tmp_path, _final, _old in staged:
                self._discard_staged_photo(tmp_path)
            raise
        for uid in created + updated:
            self._reindex_record(uid)
        if revision:
            for tmp_path, final_path, old_path in staged:
                self._commit_staged_photo(tmp_path, final_path)
                if old_path:
                    self._drop_replaced_photo(old_path, final_path)
            self._emit_changed(revision, created + updated)
        return len(created), len(updated), plan.skipped

    # ------------------------------------------------------- Gio dispatch

    def handle_method_call(self, connection, sender, object_path,
                           interface_name, method_name, parameters,
                           invocation) -> None:
        """Gio method-call closure: unpack args, dispatch, reply in tuples."""
        from gi.repository import GLib

        from xx_contacts.store import StoreError

        self._touch()
        try:
            args = parameters.unpack() if parameters is not None else ()
        except Exception as exc:
            invocation.return_dbus_error(
                ERROR_INVALID_ARGUMENT, f"cannot unpack args: {exc}")
            return
        try:
            if method_name == "LookupNumber":
                uid, name, tags = self.lookup_number(*args)
                invocation.return_value(
                    GLib.Variant("(ssas)", (uid, name, tags)))
            elif method_name == "Search":
                results = self.search(*args)
                invocation.return_value(
                    GLib.Variant("(a(sss))", (results,)))
            elif method_name == "LookupEmail":
                uid, name = self.lookup_email(*args)
                invocation.return_value(
                    GLib.Variant("(ss)", (uid, name)))
            elif method_name == "Get":
                invocation.return_value(
                    GLib.Variant("(s)", (self.get(*args),)))
            elif method_name == "List":
                revision, changed, deleted = self.list_changes(*args)
                invocation.return_value(
                    GLib.Variant("(tasas)", (revision, changed, deleted)))
            elif method_name == "Create":
                invocation.return_value(
                    GLib.Variant("(s)", (self.create(*args),)))
            elif method_name == "Update":
                self.update(*args)
                invocation.return_value(GLib.Variant("()", ()))
            elif method_name == "Delete":
                self.delete(*args)
                invocation.return_value(GLib.Variant("()", ()))
            elif method_name == "SetTags":
                self.set_tags(*args)
                invocation.return_value(GLib.Variant("()", ()))
            elif method_name == "UpdateTags":
                revision = self.update_tags(*args)
                invocation.return_value(
                    GLib.Variant("(t)", (revision,)))
            elif method_name == "Import":
                created, updated, skipped = self.import_vcards(*args)
                invocation.return_value(
                    GLib.Variant("(uuu)", (created, updated, skipped)))
            elif method_name == "Pick":
                if len(tuple(args)) != 1:
                    invocation.return_dbus_error(
                        ERROR_INVALID_ARGUMENT,
                        "Pick takes exactly one argument: kind")
                else:
                    self.request_pick(args[0], invocation)
            elif method_name == "PickReply":
                if len(tuple(args)) != 3:
                    invocation.return_dbus_error(
                        ERROR_INVALID_ARGUMENT,
                        "PickReply takes token, uid and value")
                elif not self.reply_pick(args[0], args[1], args[2]):
                    invocation.return_dbus_error(
                        ERROR_INVALID_ARGUMENT,
                        "unknown pick token")
                else:
                    invocation.return_value(GLib.Variant("()", ()))
            else:
                invocation.return_dbus_error(
                    ERROR_INVALID_ARGUMENT,
                    f"unknown method: {method_name}")
        except LookupError as exc:
            invocation.return_dbus_error(ERROR_NOT_FOUND, str(exc))
        except ValueError as exc:
            invocation.return_dbus_error(ERROR_INVALID_ARGUMENT, str(exc))
        except StoreError as exc:
            invocation.return_dbus_error(ERROR_INVALID_ARGUMENT, str(exc))
        except TypeError as exc:
            invocation.return_dbus_error(
                ERROR_INVALID_ARGUMENT, f"bad arguments: {exc}")

    def register_on(self, connection) -> int:
        """Register the object on a live Gio.DBusConnection; keep it."""
        from gi.repository import Gio

        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        iface = node.interfaces[0]
        registration_id = connection.register_object(
            OBJECT_PATH, iface, self.handle_method_call)
        self._connection = connection
        return registration_id


def build_service(db_path: str, default_region: str = "US") -> ContactsService:
    from xx_contacts.phones import LookupIndex
    from xx_contacts.store import ContactStore

    return ContactsService(ContactStore(db_path), LookupIndex(default_region),
                           default_region)


def run_service(db_path: str, default_region: str = "US") -> int:
    """Own the bus name and serve until idle. Headless: no GTK.

    The idle exit defaults to 60 s; :envvar:`XX_CONTACTS_IDLE_SECONDS`
    overrides it (tests shorten the wait). The shipped D-Bus
    activation file re-starts the service on the next method call.
    """
    from gi.repository import Gio, GLib

    idle_limit = idle_exit_seconds()
    service = build_service(db_path, default_region)
    loop = GLib.MainLoop()
    state: dict[str, object] = {}

    def on_bus_acquired(connection: Gio.DBusConnection, _name: str) -> None:
        service.register_on(connection)

    def on_name_acquired(_connection: Gio.DBusConnection, _name: str) -> None:
        state["owned"] = True

    def on_name_lost(_connection: Gio.DBusConnection | None,
                     _name: str) -> None:
        loop.quit()

    def on_idle_check() -> bool:
        if service.pick_pending():
            service._touch()
            return True
        if service.idle_seconds() >= idle_limit:
            loop.quit()
            return False
        return True

    Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME,
                     Gio.BusNameOwnerFlags.NONE,
                     on_bus_acquired, on_name_acquired, on_name_lost)
    GLib.timeout_add_seconds(5, on_idle_check)
    loop.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="xx-contacts --service")
    parser.add_argument("--service", action="store_true")
    parser.add_argument("--db", default=None)
    parser.add_argument("--default-region", default="US")
    args = parser.parse_args(argv if argv is not None else [])
    home = os.path.expanduser("~")
    from xx_contacts.store import default_db_path

    db_path = args.db or default_db_path(home)
    return run_service(db_path, args.default_region)
