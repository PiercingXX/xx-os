from __future__ import annotations

import json
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path


CONTACTS_BUS = 'io.piercingxx.Contacts'
CONTACTS_PATH = '/io/piercingxx/Contacts'
CONTACTS_IFACE = 'io.piercingxx.Contacts1'
CONTACTS_LIST_REPLY = '(tasas)'
CONTACTS_GET_REPLY = '(s)'
CONTACTS_LOOKUP_REPLY = '(ssas)'

_CALL_TIMEOUT_MS = 7000


def _gi():
    try:
        from gi.repository import Gio, GLib
    except Exception:
        return None, None
    return Gio, GLib


def _digits(number: str) -> str:
    return re.sub(r'[^\d]', '', number or '')


@dataclass
class Contact:
    name: str
    numbers: list[str] = field(default_factory=list)
    uid: str = ''
    starred: bool = False

    def primary_number(self) -> str:
        return self.numbers[0] if self.numbers else ''


def _unescape_vcard_text(value: str) -> str:
    return (value.replace('\\\\', '\x00').replace('\\n', '\n')
            .replace('\\N', '\n').replace('\\;', ';').replace('\\,', ',')
            .replace('\x00', '\\'))


def contact_from_service_vcard(uid: str, vcard: str) -> Contact | None:
    lines: list[str] = []
    for raw in (vcard or '').splitlines():
        if raw[:1] in (' ', '\t') and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    name = ''
    numbers: list[str] = []
    starred = False
    for line in lines:
        upper = line.upper()
        if upper.startswith('FN:'):
            name = _unescape_vcard_text(line[3:].strip())
        elif upper.startswith('TEL') and ':' in line:
            number = line.split(':', 1)[1].strip()
            if number:
                numbers.append(number)
        elif upper.startswith('X-STARRED:'):
            starred = line.split(':', 1)[1].strip().lower() in (
                '1', 'true', 'yes')
    if not numbers:
        return None
    return Contact(name=name, numbers=numbers, uid=uid, starred=starred)


def merge_starred_numbers(existing: list[str],
                          starred: list[str]) -> list[str]:
    merged = {_digits(n) for n in existing}
    merged.discard('')
    for number in starred:
        digits = _digits(number)
        if digits:
            merged.add(digits)
    return sorted(merged)


_VCF_SEARCH_PATHS = [
    Path.home() / '.local/share/gnome-contacts/system.vcf',
    Path.home() / '.local/share/contacts/contacts.vcf',
    Path('/var/lib/gnome-contacts/contacts.vcf'),
]

_LOCAL_JSON = Path.home() / '.local/share/xx-wm/contacts.json'


class ContactBook:
    """Contact names for shell surfaces.

    Primary source is the xx-contacts session service
    (``io.piercingxx.Contacts1``): List/Get fill an in-memory cache that
    search and lookup read synchronously, and Changed signals refresh it.
    Every bus call is async, so __init__ and the sync lookups below never
    block the GTK main loop. When the service is missing the local JSON
    file and GNOME Contacts VCF below keep working as the fallback.
    """

    def __init__(self, config=None) -> None:
        self._contacts: list[Contact] = []
        self._reload()
        self._lock = threading.Lock()
        self._dbus_by_uid: dict[str, Contact] = {}
        self._dbus_revision: int = 0
        self._dbus_ready = False
        self._config = config
        self._bus = None
        self._signal_id = None
        self._start_async_refresh()

    def _reload(self) -> None:
        self._contacts = []
        self._load_local_json()
        self._load_vcf()

    def _load_local_json(self) -> None:
        if not _LOCAL_JSON.exists():
            return
        try:
            raw = json.loads(_LOCAL_JSON.read_text(encoding='utf-8'))
            for entry in raw:
                name = entry.get('name', '').strip()
                nums = [str(n).strip() for n in entry.get('numbers', []) if n]
                if name and nums:
                    self._contacts.append(Contact(name=name, numbers=nums))
        except Exception:
            pass

    def _load_vcf(self) -> None:
        for path in _VCF_SEARCH_PATHS:
            if path.exists():
                try:
                    self._parse_vcf(path.read_text(encoding='utf-8', errors='replace'))
                except Exception:
                    pass
                break

    def _parse_vcf(self, text: str) -> None:
        existing_names = {c.name for c in self._contacts}
        cards = re.split(r'(?im)^BEGIN:VCARD', text)
        for card in cards:
            name = ''
            numbers: list[str] = []
            for line in card.splitlines():
                up = line.upper()
                if up.startswith('FN:'):
                    name = line[3:].strip()
                elif re.match(r'TEL', line, re.IGNORECASE):
                    # TEL;TYPE=CELL:+1234567890  or  TEL:+1234567890
                    parts = line.split(':', 1)
                    if len(parts) == 2:
                        num = re.sub(r'[^\d+]', '', parts[1].strip())
                        if num:
                            numbers.append(num)
            if name and numbers and name not in existing_names:
                self._contacts.append(Contact(name=name, numbers=numbers))
                existing_names.add(name)

    # ------------------------------------------------------------------ public

    def _visible_contacts(self) -> list[Contact]:
        """D-Bus cache first, then fallback entries with no D-Bus twin."""
        with self._lock:
            dbus_contacts = list(self._dbus_by_uid.values())
        seen = {c.name.casefold() for c in dbus_contacts if c.name}
        merged = list(dbus_contacts)
        for local in self._contacts:
            if local.name and local.name.casefold() in seen:
                continue
            merged.append(local)
        return merged

    def search(self, query: str, limit: int = 5) -> list[Contact]:
        """Return contacts matching a name prefix or number prefix/suffix."""
        if not query:
            return []
        q = query.casefold()
        stripped = re.sub(r'[^\d]', '', query)

        number_hits: list[Contact] = []
        name_prefix_hits: list[Contact] = []
        name_sub_hits: list[Contact] = []

        for c in self._visible_contacts():
            matched_num = False
            if stripped:
                for n in c.numbers:
                    ns = re.sub(r'[^\d]', '', n)
                    if ns.startswith(stripped) or ns.endswith(stripped):
                        matched_num = True
                        break
            if matched_num:
                number_hits.append(c)
                continue
            name_cf = c.name.casefold()
            if name_cf.startswith(q):
                name_prefix_hits.append(c)
            elif q in name_cf:
                name_sub_hits.append(c)

        return (number_hits + name_prefix_hits + name_sub_hits)[:limit]

    def lookup_number(self, number: str) -> str | None:
        """Return the contact name for a phone number, or None."""
        stripped = re.sub(r'[^\d]', '', number)
        if not stripped:
            return None
        for c in self._visible_contacts():
            for n in c.numbers:
                ns = re.sub(r'[^\d]', '', n)
                if not ns:
                    continue
                tail = min(7, len(stripped), len(ns))
                if stripped[-tail:] == ns[-tail:]:
                    return c.name or None
        return None

    def lookup_number_async(self, number: str, on_done,
                            on_error=None) -> None:
        """Caller-ID lookup through the service; on_done(name or None).

        Async LookupNumber with fallback to the in-memory cache (which is
        the local file when the service is missing), so this never raises
        and never blocks the main loop.
        """
        Gio, GLib = _gi()
        if Gio is None or GLib is None:
            on_done(self.lookup_number(number))
            return

        def _fail(_err) -> None:
            try:
                on_done(self.lookup_number(number))
            except Exception as exc:
                if on_error is not None:
                    on_error(exc)

        def _on_reply(conn, result) -> None:
            try:
                reply = conn.call_finish(result)
            except Exception:
                _fail(None)
                return
            try:
                _uid, name, _tags = reply.unpack()
            except Exception:
                _fail(None)
                return
            if name:
                on_done(str(name))
            else:
                _fail(None)

        def _on_bus(_source, bus_result) -> None:
            try:
                conn = Gio.bus_get_finish(bus_result)
            except Exception as exc:
                if on_error is not None:
                    try:
                        on_error(exc)
                    except Exception:
                        pass
                _fail(exc)
                return
            try:
                conn.call(
                    CONTACTS_BUS, CONTACTS_PATH, CONTACTS_IFACE,
                    'LookupNumber', GLib.Variant('(s)', (str(number),)),
                    GLib.VariantType.new(CONTACTS_LOOKUP_REPLY),
                    Gio.DBusCallFlags.NONE, _CALL_TIMEOUT_MS, None,
                    _on_reply)
            except Exception:
                _fail(None)

        try:
            Gio.bus_get(Gio.BusType.SESSION, None, _on_bus)
        except Exception:
            _fail(None)

    @property
    def all(self) -> list[Contact]:
        return self._visible_contacts()

    @property
    def dbus_ready(self) -> bool:
        """True once the service cache has synced at least once."""
        with self._lock:
            return self._dbus_ready

    def starred_numbers(self) -> list[str]:
        """Sorted digit-strings of starred service contacts (DnD bridge)."""
        with self._lock:
            cached = list(self._dbus_by_uid.values())
        numbers: list[str] = []
        for contact in cached:
            if not contact.starred:
                continue
            for phone in contact.numbers:
                digits = _digits(phone)
                if digits and digits not in numbers:
                    numbers.append(digits)
        return sorted(numbers)

    def sync_starred_to_dnd(self, config=None) -> list[str]:
        """Merge starred numbers into dnd_starred_numbers, without dupes.

        Additive only: user entries never drop out. Returns the merged
        list. `config` defaults to the handle passed to __init__ and may
        be a ShellConfig (with .data/.save) or a DndState (with _config).
        """
        target = config if config is not None else self._config
        data = getattr(target, 'data', None)
        if not isinstance(data, dict):
            inner = getattr(target, '_config', None)
            data = getattr(inner, 'data', None)
            target = inner
        if not isinstance(data, dict):
            return self.starred_numbers()
        merged = merge_starred_numbers(
            data.get('dnd_starred_numbers', []), self.starred_numbers())
        data['dnd_starred_numbers'] = merged
        save = getattr(target, 'save', None)
        if callable(save):
            try:
                save()
            except Exception:
                pass
        return merged

    def refresh(self) -> None:
        """Re-sync the service cache off the main loop (Changed path)."""
        self._start_async_refresh()

    def close(self) -> None:
        """Drop the Changed subscription; the fallback cache stays."""
        Gio, _GLib = _gi()
        bus, sub_id = self._bus, self._signal_id
        self._bus = None
        self._signal_id = None
        if Gio is None or bus is None or sub_id is None:
            return
        try:
            bus.signal_unsubscribe(sub_id)
        except Exception:
            pass

    # ------------------------------------------------- service cache (async)

    def _start_async_refresh(self) -> None:
        Gio, _GLib = _gi()
        if Gio is None:
            return

        def _on_bus(_source, bus_result) -> None:
            try:
                conn = Gio.bus_get_finish(bus_result)
            except Exception:
                return
            self._bus = conn
            if self._signal_id is None:
                try:
                    self._signal_id = conn.signal_subscribe(
                        CONTACTS_BUS, CONTACTS_IFACE, 'Changed',
                        CONTACTS_PATH, None, Gio.DBusSignalFlags.NONE,
                        self._on_changed)
                except Exception:
                    pass
            self._list_async(conn, self._dbus_revision)

        try:
            Gio.bus_get(Gio.BusType.SESSION, None, _on_bus)
        except Exception:
            pass

    def _list_async(self, conn, since_revision: int) -> None:
        _Gio, GLib = _gi()
        if GLib is None:
            return

        def _on_reply(_conn, result) -> None:
            try:
                reply = _conn.call_finish(result)
            except Exception:
                return
            try:
                revision, changed, deleted = reply.unpack()
            except Exception:
                return
            self._apply_list_result(_conn, int(revision), list(changed),
                                    list(deleted))

        try:
            conn.call(
                CONTACTS_BUS, CONTACTS_PATH, CONTACTS_IFACE, 'List',
                GLib.Variant('(t)', (int(since_revision),)),
                GLib.VariantType.new(CONTACTS_LIST_REPLY),
                _Gio.DBusCallFlags.NONE,
                _CALL_TIMEOUT_MS, None, _on_reply)
        except Exception:
            pass

    def _apply_list_result(self, conn, revision: int, changed: list,
                           deleted: list) -> None:
        with self._lock:
            for uid in deleted:
                self._dbus_by_uid.pop(str(uid), None)
            pending = [str(uid) for uid in changed]
        if not pending and not deleted:
            self._finish_refresh(revision)
            return
        if not pending:
            self._finish_refresh(revision)
            return
        state = {'left': len(pending)}

        def _one_done() -> None:
            state['left'] -= 1
            if state['left'] <= 0:
                self._finish_refresh(revision)

        for uid in pending:
            self._get_async(conn, uid, _one_done)

    def _get_async(self, conn, uid: str, on_settled) -> None:
        _Gio, GLib = _gi()
        if GLib is None:
            on_settled()
            return

        def _on_reply(_conn, result) -> None:
            try:
                reply = _conn.call_finish(result)
            except Exception:
                with self._lock:
                    self._dbus_by_uid.pop(uid, None)
                on_settled()
                return
            try:
                (vcard,) = reply.unpack()
            except Exception:
                on_settled()
                return
            contact = contact_from_service_vcard(uid, str(vcard))
            with self._lock:
                if contact is None:
                    self._dbus_by_uid.pop(uid, None)
                else:
                    self._dbus_by_uid[uid] = contact
            on_settled()

        try:
            conn.call(
                CONTACTS_BUS, CONTACTS_PATH, CONTACTS_IFACE, 'Get',
                GLib.Variant('(s)', (str(uid),)),
                GLib.VariantType.new(CONTACTS_GET_REPLY),
                _Gio.DBusCallFlags.NONE, _CALL_TIMEOUT_MS, None, _on_reply)
        except Exception:
            on_settled()

    def _finish_refresh(self, revision: int) -> None:
        with self._lock:
            self._dbus_revision = max(self._dbus_revision, revision)
            self._dbus_ready = True
        if self._config is not None:
            try:
                self.sync_starred_to_dnd()
            except Exception:
                pass

    def _on_changed(self, _conn, _sender, _path, _iface, _signal,
                    params) -> None:
        try:
            revision, _uids = params.unpack()
        except Exception:
            return
        try:
            if int(revision) <= self._dbus_revision:
                return
        except Exception:
            pass
        bus = self._bus
        if bus is None:
            self._start_async_refresh()
            return
        try:
            self._list_async(bus, self._dbus_revision)
        except Exception:
            pass


_SHARED: "ContactBook | None" = None


def shared_book(config=None) -> "ContactBook":
    """The shell's one ContactBook. Its xx-contacts cache fills
    asynchronously and stays current from Changed signals, so caller ID
    must reuse it: a fresh book per lookup sees an empty cache (and would
    leave one signal subscription behind per call). The first caller that
    passes the shell config turns on the starred -> DnD bridge."""
    global _SHARED
    if _SHARED is None:
        _SHARED = ContactBook(config)
    elif config is not None and _SHARED._config is None:
        _SHARED._config = config
    return _SHARED


def display_number(number: str) -> str:
    """`number` as it should read on shell surfaces (call UI, banners,
    lock-screen notifications): the shared xx_phone rule for the locale
    country. Explicit empty operator strings keep the country lookup off
    D-Bus, so this never blocks the GTK main thread. Saved names still
    win: resolve those first and only format the bare number."""
    try:
        import xx_phone
        return xx_phone.display(number, xx_phone.user_country("", ""))
    except Exception:
        return number


# ------------------------------------------------------------------ mmcli SMS

def _resolve_mmcli_modem() -> str | None:
    """D-Bus-resolved modem path for the mmcli fallback, if reachable."""
    try:
        from modem_monitor import resolve_modem_path
        return resolve_modem_path(
            'org.freedesktop.ModemManager1.Modem.Messaging')
    except Exception:
        return None


def _strip_label(line: str, label: str) -> str | None:
    """Extract value after 'label:' in mmcli tabular output."""
    m = re.search(rf'\|\s+{re.escape(label)}:\s+(.+)', line)
    return m.group(1).strip() if m else None


def load_sms_history(
    modem_idx: int = 0, timeout: int = 8, modem: str | None = None,
) -> list[dict]:
    """
    Fetch SMS history from ModemManager.
    Returns list of dicts: {number, text, outgoing, timestamp_iso}.
    Prefers Messaging.List + GetAll over D-Bus (exact text, including
    multi-line bodies that mmcli's human-readable tables truncate);
    falls back to mmcli scraping against the D-Bus-resolved modem path,
    then the legacy numeric index. Empty list when nothing is reachable.
    """
    try:
        from modem_monitor import messaging_list, resolve_modem_path
        resolved = modem or resolve_modem_path(
            'org.freedesktop.ModemManager1.Modem.Messaging')
        messages = messaging_list(
            modem=resolved, timeout_ms=timeout * 1000)
        if messages is not None:
            return messages
    except Exception:
        pass
    selector = modem or _resolve_mmcli_modem() or str(modem_idx)
    try:
        result = subprocess.run(
            ['mmcli', '-m', selector, '--messaging-list-sms'],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    paths = re.findall(r'(/org/freedesktop/ModemManager1/SMS/\d+)', result.stdout)
    if not paths:
        return []

    messages: list[dict] = []
    for path in paths:
        try:
            r = subprocess.run(
                ['mmcli', '-s', path],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            continue

        number = ''
        text = ''
        timestamp = ''
        pdu_type = ''

        for line in r.stdout.splitlines():
            v = _strip_label(line, 'number')
            if v:
                number = v
            v = _strip_label(line, 'text')
            if v:
                text = v
            v = _strip_label(line, 'timestamp')
            if v:
                timestamp = v
            v = _strip_label(line, 'pdu type')
            if v:
                pdu_type = v

        if not number or not text:
            continue

        outgoing = pdu_type.lower() in ('submit', 'submit-report')
        messages.append({
            'number': number,
            'text': text,
            'outgoing': outgoing,
            'timestamp_iso': timestamp,
        })

    return messages
