"""Medical ID editor settings section.

Edits the config Medical ID fields (name, blood type, allergies,
medications, conditions) in a sheet, the emergency contacts and the
"Show on lock screen" switch; every change saves at once. Emergency contacts are picked through the xx-contacts
session service ``io.piercingxx.Contacts1`` Search (async, never
call_sync on the main loop); the shapes below mirror its introspection
XML (bus io.piercingxx.Contacts, path /io/piercingxx/Contacts,
`Search(s query, u limit) -> a(sss)` = uid, display name, primary
number — a static test pins them to ref/xx-contacts).
"""
from __future__ import annotations

import settings_page
import settings_ui as ui

CONTACTS_BUS = 'io.piercingxx.Contacts'
CONTACTS_PATH = '/io/piercingxx/Contacts'
CONTACTS_IFACE = 'io.piercingxx.Contacts1'
CONTACTS_SEARCH_REPLY = '(a(sss))'

_SEARCH_TIMEOUT_MS = 8_000
_SEARCH_LIMIT = 8

BLOOD_TYPES = ('', 'A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-')

MEDICAL_FIELDS: tuple = (
    ('name', 'Name'),
    ('blood_type', 'Blood type'),
    ('allergies', 'Allergies'),
    ('medications', 'Medications'),
    ('conditions', 'Conditions'),
)


def parse_search_results(value) -> list:
    """a(sss) rows into [(uid, name, number)]; bad shapes give []."""
    rows = []
    if not isinstance(value, (list, tuple)):
        return rows
    for row in value:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            continue
        uid, name, number = (str(part) for part in row)
        if name or number:
            rows.append((uid, name, number))
    return rows


def search_contacts(query: str, on_done, limit: int = _SEARCH_LIMIT) -> None:
    """Async xx-contacts Search: on_done(True, [(uid, name, number)]) or
    (False, error). Missing service degrades to failure, never raises."""
    try:
        from gi.repository import Gio, GLib
    except ImportError as err:
        on_done(False, f'D-Bus unavailable: {err}')
        return

    def _fail(message: str) -> None:
        on_done(False, message)

    def _on_bus(source, result) -> None:
        try:
            bus = Gio.bus_get_finish(result)
        except Exception as err:
            _fail(str(err))
            return

        def _on_reply(conn, reply_result) -> None:
            try:
                reply = conn.call_finish(reply_result)
            except Exception as err:
                _fail(str(err))
                return
            try:
                rows = reply.unpack()[0]
            except Exception as err:
                _fail(str(err))
                return
            on_done(True, parse_search_results(rows))

        try:
            bus.call(
                CONTACTS_BUS, CONTACTS_PATH, CONTACTS_IFACE, 'Search',
                GLib.Variant('(su)', (str(query or ''), int(limit))),
                GLib.VariantType.new(CONTACTS_SEARCH_REPLY),
                Gio.DBusCallFlags.NONE, _SEARCH_TIMEOUT_MS, None, _on_reply,
            )
        except Exception as err:
            _fail(str(err))

    try:
        Gio.bus_get(Gio.BusType.SESSION, None, _on_bus)
    except Exception as err:
        _fail(str(err))


def fields_summary(medical) -> str:
    """Medical ID row value: how many fields are filled, never the
    values themselves (the page can be seen over a shoulder)."""
    if not isinstance(medical, dict):
        return 'Not set'
    filled = sum(1 for field, _title in MEDICAL_FIELDS if medical.get(field))
    if not filled:
        return 'Not set'
    return '%d of %d fields' % (filled, len(MEDICAL_FIELDS))


class MedicalSection(settings_page._Section):
    """Settings section: Medical ID (fields sheet), emergency contacts
    and the lock-screen switch. Every change saves at once."""

    key = 'medical'
    title = 'Medical ID'

    def __init__(self) -> None:
        self._row = None
        self._contacts_box = None
        self._results_box = None
        self._show_switch = None
        self._note = None

    def build(self, win) -> list:
        self._row = ui.nav_row(self.title, '',
                               lambda r: self._open_fields(win, r))
        show = ui.switch_row(
            'Show on lock screen', 'First responders see it without '
            'unlocking', bool(getattr(win.config, 'lock_screen_medical_id',
                                      True)))
        self._show_switch = show.switch
        self._show_switch.connect('notify::active',
                                  lambda sw, _p: self._on_show(win, sw))
        medical = ui.group(self.title, [self._row, show], note='')
        self._note = medical[2]
        contacts = ui.group('Emergency contacts')
        self._contacts_box = contacts[1]
        self._render(win)
        return medical + contacts

    def refresh(self, win) -> None:
        self._render(win)

    # ------------------------------------------------------------ behavior

    def _medical(self, win) -> dict:
        medical = getattr(win.config, 'medical_id', {})
        return dict(medical) if isinstance(medical, dict) else {}

    def _contacts(self, win) -> list:
        return [dict(entry) for entry in
                self._medical(win).get('emergency_contacts', [])
                if isinstance(entry, dict)]

    def _render(self, win) -> None:
        ui.set_subtitle(self._row, fields_summary(self._medical(win)))
        if self._contacts_box is None:
            return
        rows = []
        for index, entry in enumerate(self._contacts(win)):
            rows.append(ui.row(
                entry.get('name', '') or 'Contact', entry.get('number', ''),
                on_activate=lambda r, i=index: self._ask_remove(win, r, i)))
        rows.append(ui.nav_row('Add emergency contact…', 'From Contacts',
                               lambda r: self._open_search(win, r)))
        ui.fill(self._contacts_box, rows)

    def _save(self, win, medical: dict) -> bool:
        try:
            win.config.set_medical_id(medical)
        except Exception as err:
            self._say(f'Could not save: {err}')
            return False
        self._say('')
        self._render(win)
        return True

    def _open_fields(self, win, anchor):
        medical = self._medical(win)
        return ui.form_sheet(
            win, anchor, self.title,
            [(field, title, medical.get(field, ''), 'text')
             for field, title in MEDICAL_FIELDS],
            'Save', lambda values: self._on_save(win, values))

    def _on_save(self, win, values: dict) -> str | None:
        medical = self._medical(win)
        for field, _title in MEDICAL_FIELDS:
            value = str(values.get(field, '')).strip()
            if value:
                medical[field] = value
            else:
                medical.pop(field, None)
        medical['emergency_contacts'] = self._contacts(win)
        if not self._save(win, medical):
            return 'Could not save.'
        self._say('Saved.')
        return None

    def _on_show(self, win, switch) -> None:
        try:
            win.config.set_lock_screen_medical_id(bool(switch.get_active()))
        except Exception as err:
            self._say(f'Could not save: {err}')

    def _ask_remove(self, win, anchor, index: int) -> None:
        win.item_actions._show_action_menu(
            anchor, 'Emergency contact',
            [('Remove', lambda: self._remove_contact(win, index)),
             ('Cancel', lambda: None)])

    def _remove_contact(self, win, index: int) -> None:
        medical = self._medical(win)
        contacts = self._contacts(win)
        try:
            del contacts[index]
        except IndexError:
            return
        medical['emergency_contacts'] = contacts
        self._save(win, medical)

    def _add_contact(self, win, name: str, number: str) -> None:
        medical = self._medical(win)
        medical['emergency_contacts'] = self._contacts(win) + [
            {'name': name, 'number': number}]
        self._save(win, medical)

    def _open_search(self, win, anchor):
        self._results_box = ui.card()
        self._results_box.set_visible(False)
        box, entries = ui.form_sheet(
            win, anchor, 'Add emergency contact',
            [('query', 'Search contacts', '', 'text')], 'Done',
            lambda _values: None, extra=self._results_box)
        self._sheet = box
        entries['query'].connect(
            'changed', lambda entry: self._on_search(win, entry.get_text()))
        return box, entries

    def _on_search(self, win, query: str) -> None:
        if not query.strip():
            self._render_results(win, [])
            return
        search_contacts(query, lambda ok, results: self._on_search_done(
            win, ok, results))

    def _on_search_done(self, win, ok: bool, results) -> None:
        if not ok:
            self._render_results(win, [], note='Contacts is not available.')
            return
        self._render_results(win, results if isinstance(results, list)
                             else [])

    def _render_results(self, win, rows: list, note: str = '') -> None:
        if self._results_box is None:
            return
        rendered = [ui.row(name or uid, number,
                           on_activate=lambda _r, n=name, p=number:
                           self._add_contact(win, n, p))
                    for uid, name, number in rows[:_SEARCH_LIMIT]]
        if not rendered and note:
            rendered = [ui.note_row(note)]
        ui.fill(self._results_box, rendered, hide_empty=True)

    def _say(self, text: str) -> None:
        ui.say(self._note, text)
