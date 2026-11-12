"""Notification pipeline hardening: real GLib Variants where the fakes
used to be more forgiving than the library.

Modules are loaded fresh from launcher/src under private names, so a
sibling file that imported them against fake gi seams cannot leak in.
"""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def _glib():
    # Resolved at test time: sibling files stub gi during collection and
    # conftest puts the real modules back before the first test runs.
    from gi.repository import GLib
    return GLib


def Variant(*args):
    return _glib().Variant(*args)

SRC = Path(__file__).parent.parent / 'launcher' / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f'_hardening_{name}', SRC / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _QueueGLib:
    """Module-level GLib seam: idle/timeout callbacks queue until drained."""

    Error = Exception

    def __init__(self) -> None:
        self.idle: list = []
        self.timeouts: dict = {}
        self._next = 100

    def idle_add(self, callback, *args):
        self.idle.append((callback, args))
        return len(self.idle)

    def run_idle(self) -> None:
        queued, self.idle = self.idle, []
        for callback, args in queued:
            callback(*args)

    def timeout_add(self, ms, callback, *args):
        self._next += 1
        self.timeouts[self._next] = (ms, callback, args)
        return self._next

    def source_remove(self, tid):
        self.timeouts.pop(tid, None)
        return True

    @staticmethod
    def Variant(*args):
        return _glib().Variant(*args)


class _Bus:
    def __init__(self) -> None:
        self.signals: list = []

    def emit_signal(self, _dest, _path, _iface, name, params):
        self.signals.append((name, params.unpack()))


def _daemon(on_notify=None, on_close=None):
    mod = _load('notif_daemon')
    glib = _QueueGLib()
    mod.GLib = glib
    daemon = object.__new__(mod.NotificationDaemon)
    daemon._on_notify = on_notify or (lambda *a: None)
    daemon._on_close = on_close or (lambda _i: None)
    daemon._bus = _Bus()
    daemon._reg_id = 0
    daemon._name_id = 0
    daemon._next_id = 1
    daemon._owns_name = True
    daemon._expire_timers = {}
    daemon._notif_hints = {}
    daemon._issued_ids = {}
    daemon._takes_actions = None
    return mod, daemon, glib


def _notify(summary='S', body='B', hints=None, expire=-1, replaces=0,
            actions=None):
    return Variant('(susssasa{sv}i)', (
        'App', replaces, '', summary, body, actions or [],
        hints or {}, expire))


class TestImageDataStaysPacked:
    def test_image_data_never_unpacked(self, monkeypatch):
        seen: list = []
        cls = _glib().Variant
        real_unpack = cls.unpack

        def recording_unpack(self):
            seen.append(self.get_type_string())
            return real_unpack(self)

        received: list = []
        _mod, daemon, glib = _daemon(
            on_notify=lambda *args: received.append(args))
        pixels = bytes(256 * 256 * 4)
        params = _notify(hints={
            'image-data': Variant(
                '(iiibiiay)', (256, 256, 1024, True, 8, 4, pixels)),
            'icon_data': Variant(
                '(iiibiiay)', (1, 1, 4, True, 8, 4, bytes(4))),
            'category': Variant('s', 'im'),
            'urgency': Variant('y', 1),
        })
        monkeypatch.setattr(cls, 'unpack', recording_unpack)
        daemon._handle_notify(params)
        monkeypatch.setattr(cls, 'unpack', real_unpack)
        glib.run_idle()
        assert '(iiibiiay)' not in seen
        assert 'ay' not in seen
        hints = received[0][5]
        assert hints == {'category': 'im', 'urgency': 1}
        assert received[0][2:4] == ('S', 'B')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
