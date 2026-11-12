"""Emergency calling + Medical ID from the lock screen (Android parity).

Covers the pure matcher (built-in union modem list, dial-string
formatting), the lock-screen refusal of non-emergency numbers, medical_id
config validation, backup export/restore round-trip, and the lock screen
showing Emergency in every state (resting, keypad revealed, lockout).

Headless idiom (see test_lock_screen.py): real GTK widgets are never
constructed under pytest — LockScreen decision methods run unbound
against a fake-widget harness, and Gtk construction in the contact-row
path runs against a recording FakeGtk.
"""
import copy
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from config import ShellConfig  # noqa: E402  (pure python, no gi)


@pytest.fixture(scope='module', autouse=True)
def _fresh_real_modules():
    """Re-import gi-bound modules against the real bindings for this file.

    Sibling test modules install stub ``gi`` packages at collection time
    and import dialer/lock_screen/modem_monitor against them; a plain
    ``import`` here would inherit those stubs (no get_monotonic_time,
    no Pango.EllipsizeMode, no Gio.DBusCallFlags). Re-import fresh once
    conftest has restored the real bindings, and put the collection
    versions back afterwards so sibling tests keep their expected fakes.
    """
    names = ('dialer', 'lock_screen', 'modem_monitor')
    saved = {}
    for name in names:
        if name in sys.modules:
            saved[name] = sys.modules.pop(name)
    try:
        import dialer  # noqa: F401
        import lock_screen  # noqa: F401
        import modem_monitor  # noqa: F401
        yield
    finally:
        for name in names:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def _dialer():
    import dialer
    return dialer


def _lock_screen():
    import lock_screen
    return lock_screen


def _modem_monitor():
    import modem_monitor
    return modem_monitor


def _backup():
    import backup
    return backup


BUILTIN = ('112', '911')

#: Emergency numbers in some countries and ordinary (some billable)
#: numbers in others: never dialable from the lock screen on their own.
REGIONAL = ('999', '000', '08', '110', '118', '119', '100', '101', '102',
            '108')


@pytest.fixture(autouse=True)
def _no_host_modem(monkeypatch):
    """No test here reaches the host's system bus: the modem list reads
    empty unless a test hands get_modem_emergency_numbers its own bus."""
    dialer = _dialer()
    real = dialer.get_modem_emergency_numbers

    def _modem_numbers(bus=None, *args, **kwargs):
        return real(bus, *args, **kwargs) if bus is not None else []

    monkeypatch.setattr(dialer, 'get_modem_emergency_numbers', _modem_numbers)


class TestNormalize:
    def test_plain_numbers_unchanged(self):
        dialer = _dialer()
        for number in BUILTIN:
            assert dialer.normalize_emergency_number(number) == number

    def test_formatting_stripped(self):
        dialer = _dialer()
        assert dialer.normalize_emergency_number('1 1 2') == '112'
        assert dialer.normalize_emergency_number('9-1-1') == '911'
        assert dialer.normalize_emergency_number('+112') == '112'
        assert dialer.normalize_emergency_number('(911)') == '911'
        assert dialer.normalize_emergency_number('+1 (911)') == '1911'

    def test_non_string_gives_empty(self):
        dialer = _dialer()
        assert dialer.normalize_emergency_number(None) == ''
        assert dialer.normalize_emergency_number(112) == ''
        assert dialer.normalize_emergency_number('') == ''


class TestIsEmergencyNumber:
    def test_every_builtin_matches(self):
        dialer = _dialer()
        for number in BUILTIN:
            assert dialer.is_emergency_number(number) is True

    def test_formatted_builtins_match(self):
        dialer = _dialer()
        assert dialer.is_emergency_number('1 1 2') is True
        assert dialer.is_emergency_number('9-1-1') is True
        assert dialer.is_emergency_number('+112') is True

    def test_regional_numbers_need_the_modem_list(self):
        dialer = _dialer()
        for number in REGIONAL:
            assert dialer.is_emergency_number(number) is False, number
        assert dialer.is_emergency_number('999', extra=['999']) is True
        assert dialer.is_emergency_number('110', extra=['999']) is False

    def test_non_emergency_refused(self):
        dialer = _dialer()
        assert dialer.is_emergency_number('5551234') is False
        assert dialer.is_emergency_number('9115551234') is False
        assert dialer.is_emergency_number('') is False
        assert dialer.is_emergency_number('+15550102222') is False

    def test_modem_list_unions_with_builtin(self):
        dialer = _dialer()
        assert dialer.is_emergency_number('9999') is False
        assert dialer.is_emergency_number('9999', extra=['9999']) is True
        assert dialer.is_emergency_number('99 99', extra=['9999']) is True
        assert dialer.is_emergency_number('112', extra=['9999']) is True
        assert dialer.is_emergency_number('5551234', extra=['9999']) is False

    def test_garbage_extra_ignored(self):
        dialer = _dialer()
        assert dialer.is_emergency_number('112', extra='112') is True
        assert dialer.is_emergency_number('9999', extra='9999') is False
        assert dialer.is_emergency_number('9999', extra=[None, 9999]) is False
        assert dialer.is_emergency_number('9999', extra=None) is False


class _FakeReply:
    # Mirrors GLib: every reply is a tuple of out-args.
    def __init__(self, value):
        self._value = value

    def unpack(self):
        if isinstance(self._value, dict):
            return (self._value,)
        return self._value


class _FakeBus:
    def __init__(self, replies):
        self.calls = []
        self._replies = list(replies)

    def call_sync(self, *args):
        self.calls.append(args)
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


MODEM = '/org/freedesktop/ModemManager1/Modem/0'
SIM = '/org/freedesktop/ModemManager1/SIM/0'
_MM = 'org.freedesktop.ModemManager1'
_PROPS = 'org.freedesktop.DBus.Properties'
_3GPP = 'org.freedesktop.ModemManager1.Modem.Modem3gpp'


class TestModemEmergencyNumbers:
    def test_modem3gpp_and_sim_union(self):
        dialer = _dialer()
        bus = _FakeBus([
            _FakeReply({'EmergencyNumbers': ['911', ' 118 ']}),
            _FakeReply((SIM,)),
            _FakeReply({'EmergencyNumbers': ['9999']}),
        ])
        assert dialer.get_modem_emergency_numbers(bus=bus, modem=MODEM) == \
            ['911', '118', '9999']
        assert bus.calls[0][:4] == (_MM, MODEM, _PROPS, 'GetAll')
        assert bus.calls[1][3] == 'Get'
        assert bus.calls[2][1] == SIM and bus.calls[2][3] == 'GetAll'

    def test_missing_sim_path_skips_sim(self):
        dialer = _dialer()
        bus = _FakeBus([
            _FakeReply({'EmergencyNumbers': ['112']}),
            _FakeReply(('/',)),
        ])
        assert dialer.get_modem_emergency_numbers(bus=bus, modem=MODEM) == ['112']
        assert len(bus.calls) == 2

    def test_non_list_properties_ignored(self):
        dialer = _dialer()
        bus = _FakeBus([
            _FakeReply({'EmergencyNumbers': '911'}),
            _FakeReply((SIM,)),
            _FakeReply({}),
        ])
        assert dialer.get_modem_emergency_numbers(bus=bus, modem=MODEM) == []

    def test_transport_failure_gives_empty(self):
        dialer = _dialer()
        bus = _FakeBus([Exception('modem vanished')])
        assert dialer.get_modem_emergency_numbers(bus=bus, modem=MODEM) == []

    def test_no_bus_gives_empty(self, monkeypatch):
        dialer = _dialer()
        monkeypatch.setattr(dialer.Gio, 'bus_get_sync', lambda *a, **k: None)
        assert dialer.get_modem_emergency_numbers() == []

    def test_unresolvable_modem_gives_empty(self, monkeypatch):
        dialer = _dialer()
        monitor = _modem_monitor()
        monkeypatch.setattr(monitor, 'resolve_modem_path', lambda *a, **k: None)
        bus = _FakeBus([])
        assert dialer.get_modem_emergency_numbers(bus=bus) == []
        assert bus.calls == []


class TestPlaceEmergencyCall:
    def test_non_emergency_refused_without_touching_modem(self, monkeypatch):
        dialer = _dialer()
        monitor = _modem_monitor()
        calls = []
        monkeypatch.setattr(monitor, 'voice_create_and_start',
                            lambda *a, **k: calls.append(a) or (True, '/c/1'))
        ok, detail = dialer.place_emergency_call('5551234')
        assert (ok, detail) == (False, dialer.EMERGENCY_REFUSAL)
        assert calls == []

    def test_emergency_goes_through_voice_create_and_start(self, monkeypatch):
        """Same transport the dialer uses: the created MM call is what the
        modem monitor adopts, feeding the existing CallUI-above-lock path."""
        dialer = _dialer()
        monitor = _modem_monitor()
        calls = []
        monkeypatch.setattr(
            monitor, 'voice_create_and_start',
            lambda number, *a, **k: calls.append(number) or (True, '/c/1'))
        assert dialer.place_emergency_call('9-1-1') == (True, '/c/1')
        assert calls == ['911']

    def test_modem_extra_accepted_end_to_end(self, monkeypatch):
        dialer = _dialer()
        monitor = _modem_monitor()
        monkeypatch.setattr(monitor, 'voice_create_and_start',
                            lambda number, *a, **k: (True, '/c/2'))
        assert dialer.place_emergency_call('9999', extra=['9999']) == (True, '/c/2')

    def test_regional_number_refused_without_modem_list(self, monkeypatch):
        dialer = _dialer()
        monitor = _modem_monitor()
        calls = []
        monkeypatch.setattr(monitor, 'voice_create_and_start',
                            lambda number, *a, **k: calls.append(number)
                            or (True, '/c/3'))
        assert dialer.place_emergency_call('100') == (
            False, dialer.EMERGENCY_REFUSAL)
        assert calls == []

    def test_stale_list_rechecked_with_the_modem_at_dial_time(
            self, monkeypatch):
        """The list the caller holds may be empty or old (fetched before
        the modem registered): the modem is asked again before refusing."""
        dialer = _dialer()
        monitor = _modem_monitor()
        calls = []
        monkeypatch.setattr(dialer, 'get_modem_emergency_numbers',
                            lambda *a, **k: ['999'])
        monkeypatch.setattr(monitor, 'voice_create_and_start',
                            lambda number, *a, **k: calls.append(number)
                            or (True, '/c/4'))
        assert dialer.place_emergency_call('999', extra=[]) == (True, '/c/4')
        assert calls == ['999']


class TestMedicalConfig:
    @pytest.fixture(autouse=True)
    def _isolated_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))

    def test_defaults(self):
        config = ShellConfig()
        assert config.medical_id == {}
        assert config.lock_screen_medical_id is True

    def test_valid_roundtrip(self):
        config = ShellConfig()
        medical = {
            'name': 'Sam Lee', 'date_of_birth': '1990-05-01',
            'blood_type': 'O-', 'allergies': 'penicillin',
            'medications': 'none', 'conditions': 'asthma',
            'organ_donor': True, 'notes': 'ICE: Sam',
            'emergency_contacts': [
                {'name': 'Jo', 'number': '+15550102222', 'relation': 'Sibling'},
            ],
        }
        config.set_medical_id(medical)
        config.set_lock_screen_medical_id(False)
        assert config.medical_id == medical
        assert config.lock_screen_medical_id is False
        fresh = ShellConfig()
        fresh.load()
        assert fresh.medical_id == medical
        assert fresh.lock_screen_medical_id is False

    def test_bad_types_ignored_never_crash(self):
        config = ShellConfig()
        config.set_medical_id({
            'name': 123, 'blood_type': None, 'organ_donor': 'yes',
            'emergency_contacts': [
                {'name': 1, 'number': '+15550102222'},
                'not a dict',
                {'relation': 'Sibling'},
                {},
            ],
            'unknown_key': 'dropped',
        })
        assert config.medical_id == {
            'emergency_contacts': [{'number': '+15550102222'}],
        }

    def test_non_dict_medical_id_gives_empty(self, tmp_path):
        import json
        config = ShellConfig()
        config.config_dir.mkdir(parents=True, exist_ok=True)
        config.config_path.write_text(json.dumps({'medical_id': ['x']}),
                                       encoding='utf-8')
        config.load()
        assert config.medical_id == {}

    def test_corrupt_flag_fails_open_to_visible(self, tmp_path):
        import json
        config = ShellConfig()
        config.config_dir.mkdir(parents=True, exist_ok=True)
        config.config_path.write_text(
            json.dumps({'lock_screen_medical_id': 'no'}), encoding='utf-8')
        config.load()
        assert config.lock_screen_medical_id is True


class TestBackupMedical:
    @pytest.fixture(autouse=True)
    def _isolated_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))

    def _populated(self):
        config = ShellConfig()
        config.set_medical_id({
            'name': 'Sam Lee', 'blood_type': 'O-', 'organ_donor': False,
            'emergency_contacts': [
                {'name': 'Jo', 'number': '+15550102222', 'relation': 'Sibling'},
            ],
        })
        config.set_lock_screen_medical_id(False)
        return config

    def test_export_carries_medical_id(self):
        backup = _backup()
        exported = backup.export_backup(self._populated())
        assert exported['medical_id']['blood_type'] == 'O-'
        assert exported['medical_id']['emergency_contacts'] == [
            {'name': 'Jo', 'number': '+15550102222', 'relation': 'Sibling'}]
        assert exported['lock_screen_medical_id'] is False

    def test_round_trip(self, tmp_path, monkeypatch):
        backup = _backup()
        exported = backup.export_backup(self._populated())
        monkeypatch.setenv('HOME', str(tmp_path / 'home2'))
        fresh = ShellConfig()
        assert backup.restore_backup(fresh, copy.deepcopy(exported))
        assert fresh.medical_id == self._populated().medical_id
        assert fresh.lock_screen_medical_id is False
        assert backup.export_backup(fresh) == exported

    @pytest.mark.parametrize('mutate', [
        lambda p: p.update(medical_id='not a dict'),
        lambda p: p.update(medical_id={'organ_donor': 'yes'}),
        lambda p: p.update(medical_id={'name': 42}),
        lambda p: p.update(medical_id={'emergency_contacts': 'Jo'}),
        lambda p: p.update(medical_id={'emergency_contacts': ['Jo']}),
        lambda p: p.update(medical_id={'emergency_contacts': [{'number': 7}]}),
        lambda p: p.update(lock_screen_medical_id='yes'),
    ])
    def test_invalid_medical_payload_writes_nothing(self, mutate):
        backup = _backup()
        fresh = ShellConfig()
        payload = {'version': 1, 'home_slots': [], 'theme': 'amoled',
                   'font': 'jetbrains-mono-nerd'}
        mutate(payload)
        before = copy.deepcopy(fresh.data)
        ok, error = backup.validate_backup(payload)
        assert not ok and error
        assert not backup.restore_backup(fresh, payload)
        assert fresh.data == before


class TestFormatMedicalInfo:
    def test_full_record(self):
        text = _lock_screen().format_medical_info({
            'name': 'Sam Lee', 'date_of_birth': '1990-05-01',
            'blood_type': 'O-', 'allergies': 'penicillin',
            'medications': 'none', 'conditions': 'asthma',
            'organ_donor': True, 'notes': 'ICE: Sam',
            'emergency_contacts': [
                {'name': 'Jo', 'number': '+15550102222', 'relation': 'Sibling'},
                {'number': '+15550103333'},
            ],
        })
        assert text.splitlines() == [
            'Name: Sam Lee', 'Born: 1990-05-01', 'Blood type: O-',
            'Allergies: penicillin', 'Medications: none', 'Conditions: asthma',
            'Organ donor: Yes', 'Notes: ICE: Sam', 'Emergency contacts:',
            'Jo — +15550102222 (Sibling)', '+15550103333',
        ]

    def test_organ_donor_no(self):
        text = _lock_screen().format_medical_info({'organ_donor': False})
        assert text == 'Organ donor: No'

    def test_empty_and_garbage(self):
        lock_screen = _lock_screen()
        assert lock_screen.format_medical_info({}) == 'No medical info set'
        assert lock_screen.format_medical_info('nope') == 'No medical info set'
        assert lock_screen.format_medical_info(
            {'name': '', 'organ_donor': 'yes'}) == 'No medical info set'


class _FakeLabel:
    def __init__(self):
        self.text = ''
        self.css = []

    def set_text(self, text):
        self.text = text

    def add_css_class(self, name):
        if name not in self.css:
            self.css.append(name)

    def remove_css_class(self, name):
        if name in self.css:
            self.css.remove(name)


class _FakeVisibility:
    def __init__(self):
        self.visible = True

    def set_visible(self, value):
        self.visible = bool(value)


class _FakeRevealer:
    def __init__(self):
        self.revealed = False

    def set_reveal_child(self, value):
        self.revealed = bool(value)


class _FakeNotifBox:
    def __init__(self):
        self.visible = True

    def get_first_child(self):
        return None

    def remove(self, _child):
        pass

    def append(self, _child):
        pass

    def set_visible(self, value):
        self.visible = bool(value)


class _FakeGridPane:
    def __init__(self):
        self.homogeneous = True
        self.vexpand = True

    def set_row_homogeneous(self, value):
        self.homogeneous = bool(value)

    def set_vexpand(self, value):
        self.vexpand = bool(value)


class _FakeButton:
    """Recording stand-in for the emergency contact-row Gtk.Button."""

    def __init__(self, label=''):
        self.label = label
        self.css = []
        self.visible = True
        self.sensitive = True
        self.connected = []
        self._next = None

    def add_css_class(self, name):
        self.css.append(name)

    def connect(self, signal, handler, *args):
        self.connected.append((signal, handler, args))
        return 1

    def get_child(self):
        return _FakeInner()

    def get_next_sibling(self):
        return self._next

    def set_visible(self, value):
        self.visible = bool(value)

    def set_sensitive(self, value):
        self.sensitive = bool(value)


class _FakeInner:
    def __init__(self):
        self.ellipsized = None

    def set_ellipsize(self, mode):
        self.ellipsized = mode


class _FakeContactsBox:
    def __init__(self):
        self.children = []

    def get_first_child(self):
        return self.children[0] if self.children else None

    def remove(self, child):
        self.children.remove(child)

    def append(self, child):
        if self.children:
            self.children[-1]._next = child
        self.children.append(child)


class _SyncThread:
    """threading.Thread that runs its target inline: worker-thread logic
    stays off the GTK loop in production but runs deterministically here."""

    def __init__(self, target=None, args=(), kwargs=None, **_ignored):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


class _EmgHarness:
    """Headless stand-in for the LockScreen widget surface (repo idiom:
    GTK widgets are never constructed under pytest). The real LockScreen
    decision methods run against this namespace self."""

    def __init__(self, config):
        self._config = config
        self._pin = ''
        self._fail_count = 0
        self._lockout_src = None
        self._open_shade_after_unlock = False
        self._pin_revealer = _FakeRevealer()
        self._swipe_hint = _FakeVisibility()
        self.dots_label = _FakeLabel()
        self.error_label = _FakeLabel()
        self.clock_label = _FakeLabel()
        self.date_label = _FakeVisibility()
        self._notif_box = _FakeNotifBox()
        self._lock_grid = _FakeGridPane()
        self._top_pane = _FakeGridPane()
        self._key_size = 0
        self._key_widgets = []
        self._keypad_btns = [_FakeButton('1'), _FakeButton('Unlock')]
        self._get_notifications = lambda: []
        self._dnd_active = lambda: False
        self.visible = True
        self.unlocked = 0
        self._emg_digits = ''
        self._emg_display = _FakeLabel()
        self._emg_status = _FakeLabel()
        self._emergency_revealer = _FakeRevealer()
        self._medical_revealer = _FakeRevealer()
        self._emergency_btn = _FakeButton('Emergency')
        self._emg_medical_btn = _FakeButton('Medical info')
        self._emg_contacts_box = _FakeContactsBox()
        self._medical_label = _FakeLabel()
        self._modem_emergency_extra = []
        self._modem_emg_fetching = False
        self.lockout_label = _FakeLabel()

    def get_width(self):
        return 360

    def get_height(self):
        return 720

    def _on_unlock(self):
        self.unlocked += 1

    def set_visible(self, value):
        self.visible = bool(value)

    # Delegating wrappers (test_lock_screen.py idiom): the real
    # LockScreen methods below call each other via self, so the harness
    # re-exposes them bound to this namespace.

    def _refresh_dots(self):
        _lock_screen().LockScreen._refresh_dots(self)

    def _refresh_notifications(self):
        _lock_screen().LockScreen._refresh_notifications(self)

    def _set_clock_compact(self, compact):
        _lock_screen().LockScreen._set_clock_compact(self, compact)

    def _apply_keypad_size(self, size):
        _lock_screen().LockScreen._apply_keypad_size(self, size)

    def _fit_keypad_to_screen(self):
        _lock_screen().LockScreen._fit_keypad_to_screen(self)

    def _refresh_emg_display(self):
        _lock_screen().LockScreen._refresh_emg_display(self)

    def _refresh_emg_contacts(self):
        _lock_screen().LockScreen._refresh_emg_contacts(self)

    def _refresh_medical_visibility(self):
        _lock_screen().LockScreen._refresh_medical_visibility(self)

    def _emg_call(self, number, contact=False):
        _lock_screen().LockScreen._emg_call(self, number, contact=contact)

    def _emg_call_thread(self, number, contact=False):
        _lock_screen().LockScreen._emg_call_thread(self, number, contact)

    def _emg_call_done(self, number, ok, detail):
        return _lock_screen().LockScreen._emg_call_done(self, number, ok, detail)

    def _modem_emg_thread(self):
        _lock_screen().LockScreen._modem_emg_thread(self)

    def _modem_emg_done(self, extra):
        return _lock_screen().LockScreen._modem_emg_done(self, extra)

    def _set_keypad_sensitive(self, sensitive):
        _lock_screen().LockScreen._set_keypad_sensitive(self, sensitive)

    def _tick_lockout(self):
        return _lock_screen().LockScreen._tick_lockout(self)

    def _on_emg_contact(self, btn, number):
        _lock_screen().LockScreen._on_emg_contact(self, btn, number)


@pytest.fixture
def emg_lock(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    lock_screen = _lock_screen()
    monkeypatch.setattr(lock_screen.GLib, 'timeout_add', lambda *a, **k: 1)
    monkeypatch.setattr(lock_screen.GLib, 'idle_add',
                        lambda cb, *a, **k: (cb(*a), 1)[1])
    monkeypatch.setattr(lock_screen.threading, 'Thread', _SyncThread)
    config = lock_screen.ShellConfig()
    return types.SimpleNamespace(
        module=lock_screen, config=config, lock=_EmgHarness(config))


class TestEmergencyButtonVisible:
    """The Emergency text button is always visible: resting, keypad
    revealed, and lockout — and lockout never desensitizes it."""

    def test_resting_state(self, emg_lock):
        lock_screen = emg_lock.module
        lock = emg_lock.lock
        lock._emergency_btn.set_visible(False)
        lock_screen.LockScreen.prepare(lock)
        assert lock._emergency_btn.visible is True
        assert lock._emergency_btn.sensitive is True
        assert lock._emergency_revealer.revealed is False
        assert lock._medical_revealer.revealed is False

    def test_keypad_revealed(self, emg_lock):
        lock_screen = emg_lock.module
        lock = emg_lock.lock
        emg_lock.config.set_pin('123456')
        lock_screen.LockScreen.prepare(lock)
        lock_screen.LockScreen._swipe_up(lock)
        assert lock._pin_revealer.revealed is True
        assert lock._emergency_btn.visible is True
        assert lock._emergency_btn.sensitive is True

    def test_lockout_keeps_emergency(self, emg_lock):
        lock_screen = emg_lock.module
        lock = emg_lock.lock
        emg_lock.config.set_pin('123456')
        lock_screen.LockScreen._start_lockout(lock, 60)
        assert lock.lockout_label.text == 'Try again in 60s'
        for btn in lock._keypad_btns:
            assert btn.sensitive is False
        assert lock._emergency_btn.visible is True
        assert lock._emergency_btn.sensitive is True
        assert lock._emergency_btn not in lock._keypad_btns

    def test_prepare_resets_surface_but_keeps_button(self, emg_lock):
        lock_screen = emg_lock.module
        lock = emg_lock.lock
        lock._emergency_revealer.revealed = True
        lock._medical_revealer.revealed = True
        lock._emg_digits = '911'
        lock_screen.LockScreen.prepare(lock)
        assert lock._emergency_revealer.revealed is False
        assert lock._medical_revealer.revealed is False
        assert lock._emg_digits == ''
        assert lock._emg_display.text == ''
        assert lock._emergency_btn.visible is True


class TestEmergencySurface:
    def test_open_reveals_and_refetches_modem_list(self, emg_lock, monkeypatch):
        """The modem list is fetched on every open (it changes with the
        network and SIM), and an empty answer never wipes a good one."""
        lock_screen = emg_lock.module
        dialer = _dialer()
        answers = [['9999'], [], ['999']]
        fetched = []
        monkeypatch.setattr(dialer, 'get_modem_emergency_numbers',
                            lambda *a, **k: fetched.append(1) or answers.pop(0))
        lock = emg_lock.lock
        lock_screen.LockScreen.open_emergency(lock)
        assert lock._emergency_revealer.revealed is True
        assert lock._medical_revealer.revealed is False
        assert lock._modem_emergency_extra == ['9999']
        lock_screen.LockScreen.open_emergency(lock)
        assert lock._modem_emergency_extra == ['9999']
        lock_screen.LockScreen.open_emergency(lock)
        assert lock._modem_emergency_extra == ['999']
        assert fetched == [1, 1, 1]
        assert lock._modem_emg_fetching is False

    def test_empty_first_fetch_does_not_latch(self, emg_lock, monkeypatch):
        lock_screen = emg_lock.module
        dialer = _dialer()
        answers = [[], ['999']]
        monkeypatch.setattr(dialer, 'get_modem_emergency_numbers',
                            lambda *a, **k: answers.pop(0))
        lock = emg_lock.lock
        lock_screen.LockScreen.open_emergency(lock)
        assert lock._modem_emergency_extra == []
        lock_screen.LockScreen.open_emergency(lock)
        assert lock._modem_emergency_extra == ['999']

    def test_number_on_the_modem_list_only_dials(self, emg_lock, monkeypatch):
        """999 is not built in; the cached list is still empty (fetched
        before the modem registered), and the modem has it now."""
        monitor = _modem_monitor()
        dialer = _dialer()
        lock_screen = emg_lock.module
        lock = emg_lock.lock
        placed = []
        monkeypatch.setattr(dialer, 'get_modem_emergency_numbers',
                            lambda *a, **k: ['999'])
        monkeypatch.setattr(monitor, 'voice_create_and_start',
                            lambda number, *a, **k: placed.append(number)
                            or (True, '/c/5'))
        lock._emg_digits = '999'
        lock_screen.LockScreen._on_emg_call(lock, None)
        assert placed == ['999']
        assert lock._emg_status.text == 'Dialing…'

    def test_regional_number_refused_from_the_lock(self, emg_lock, monkeypatch):
        monitor = _modem_monitor()
        dialer = _dialer()
        lock_screen = emg_lock.module
        lock = emg_lock.lock
        placed = []
        monkeypatch.setattr(monitor, 'voice_create_and_start',
                            lambda number, *a, **k: placed.append(number)
                            or (True, '/c/6'))
        lock._emg_digits = '118'
        lock_screen.LockScreen._on_emg_call(lock, None)
        assert placed == []
        assert lock._emg_status.text == dialer.EMERGENCY_REFUSAL

    def test_digit_entry_and_delete(self, emg_lock):
        lock_screen = emg_lock.module
        lock = emg_lock.lock
        lock_screen.LockScreen._on_emg_digit(lock, None, '9')
        lock_screen.LockScreen._on_emg_digit(lock, None, '1')
        lock_screen.LockScreen._on_emg_digit(lock, None, '1')
        assert lock._emg_digits == '911'
        assert lock._emg_display.text == '911'
        lock_screen.LockScreen._on_emg_delete(lock, None)
        assert lock._emg_digits == '91'

    def test_non_emergency_refused(self, emg_lock, monkeypatch):
        lock_screen = emg_lock.module
        dialer = _dialer()
        lock = emg_lock.lock
        lock._emg_digits = '5551234'
        started = []
        monkeypatch.setattr(lock_screen.threading, 'Thread',
                            lambda **k: started.append(k) or _SyncThread(**k))
        lock_screen.LockScreen._on_emg_call(lock, None)
        assert lock._emg_status.text == dialer.EMERGENCY_REFUSAL

    def test_emergency_dials_through_modem(self, emg_lock, monkeypatch):
        monitor = _modem_monitor()
        lock_screen = emg_lock.module
        lock = emg_lock.lock
        placed = []
        monkeypatch.setattr(monitor, 'voice_create_and_start',
                            lambda number, *a, **k: placed.append(number) or (True, '/c/1'))
        lock._emg_digits = '9-1-1'
        lock_screen.LockScreen._on_emg_call(lock, None)
        assert placed == ['911']
        assert lock._emg_status.text == 'Dialing…'

    def test_failed_call_reports_detail(self, emg_lock, monkeypatch):
        monitor = _modem_monitor()
        lock_screen = emg_lock.module
        lock = emg_lock.lock
        monkeypatch.setattr(monitor, 'voice_create_and_start',
                            lambda number, *a, **k: (False, 'no service'))
        lock._emg_digits = '112'
        lock_screen.LockScreen._on_emg_call(lock, None)
        assert lock._emg_status.text == 'Call failed: no service'

    def test_contact_tap_calls_configured_number(self, emg_lock, monkeypatch):
        monitor = _modem_monitor()
        lock_screen = emg_lock.module
        lock = emg_lock.lock
        emg_lock.config.set_medical_id({
            'emergency_contacts': [
                {'name': 'Jo', 'number': '+15550102222', 'relation': 'Sibling'},
                {'number': '+15550103333'},
                {'name': 'No number'},
                'junk',
            ],
        })
        fake_gtk = types.SimpleNamespace(Button=_FakeButton)
        monkeypatch.setattr(lock_screen, 'Gtk', fake_gtk)
        lock._modem_emg_fetching = True
        lock_screen.LockScreen.open_emergency(lock)
        assert [b.label for b in lock._emg_contacts_box.children] == [
            'Jo — +15550102222', '+15550103333']
        placed = []
        monkeypatch.setattr(monitor, 'voice_create_and_start',
                            lambda number, *a, **k: placed.append(number) or (True, '/c/9'))
        first = lock._emg_contacts_box.children[0]
        signal, handler, args = first.connected[0]
        assert signal == 'clicked'
        handler(first, *args)
        # Configured contacts are explicitly allowed from the lock screen:
        # no emergency-number gate in front of them.
        assert placed == ['+15550102222']
        assert lock._emg_status.text == 'Dialing…'

    def test_medical_info_read_only_view(self, emg_lock):
        lock_screen = emg_lock.module
        lock = emg_lock.lock
        emg_lock.config.set_medical_id({'name': 'Sam Lee', 'blood_type': 'O-'})
        lock_screen.LockScreen._on_emg_medical(lock, None)
        assert lock._medical_label.text == 'Name: Sam Lee\nBlood type: O-'
        assert lock._medical_revealer.revealed is True
        assert lock._emergency_revealer.revealed is False
        lock_screen.LockScreen._on_medical_back(lock, None)
        assert lock._medical_revealer.revealed is False
        assert lock._emergency_revealer.revealed is True

    def test_medical_hidden_by_flag(self, emg_lock):
        lock_screen = emg_lock.module
        lock = emg_lock.lock
        assert lock._emg_medical_btn.visible is True
        emg_lock.config.set_lock_screen_medical_id(False)
        lock_screen.LockScreen.open_emergency(lock)
        assert lock._emg_medical_btn.visible is False
        assert lock._emergency_revealer.revealed is True
        emg_lock.config.set_lock_screen_medical_id(True)
        lock_screen.LockScreen.open_emergency(lock)
        assert lock._emg_medical_btn.visible is True

    def test_contacts_hidden_and_not_dialable_with_medical_off(
            self, emg_lock, monkeypatch):
        """Medical ID off: emergency contacts are Medical ID data, so no
        names, no numbers and no PIN-less calls to them; 112 still dials."""
        monitor = _modem_monitor()
        dialer = _dialer()
        lock_screen = emg_lock.module
        lock = emg_lock.lock
        emg_lock.config.set_medical_id({'emergency_contacts': [
            {'name': 'Jo', 'number': '+15550102222'}]})
        monkeypatch.setattr(lock_screen, 'Gtk',
                            types.SimpleNamespace(Button=_FakeButton))
        placed = []
        monkeypatch.setattr(monitor, 'voice_create_and_start',
                            lambda number, *a, **k: placed.append(number)
                            or (True, '/c/9'))
        lock._modem_emg_fetching = True
        lock_screen.LockScreen.open_emergency(lock)
        jo = lock._emg_contacts_box.children[0]
        emg_lock.config.set_lock_screen_medical_id(False)
        assert lock_screen.lock_emergency_contacts(emg_lock.config) == []
        lock_screen.LockScreen.open_emergency(lock)
        assert lock._emg_contacts_box.children == []
        # A row drawn before the switch flipped dials nothing either.
        signal, handler, args = jo.connected[0]
        handler(jo, *args)
        assert placed == []
        assert lock._emg_status.text == dialer.EMERGENCY_REFUSAL
        lock._emg_digits = '112'
        lock_screen.LockScreen._on_emg_call(lock, None)
        assert placed == ['112']
