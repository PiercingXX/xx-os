"""Ringtone and notification sounds (sound.py, the main.py hookup).

- The ringtone loop and notification sounds use separate players: a
  notification while the phone rings never stops the ringtone.
- No notification sound plays over a ringing call.
- Each loop carries a generation token: stop() then a quick play() never
  leaves the old loop respawning beside the new one.
"""
import sys
import threading
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import sound  # noqa: E402


class FakeProc:
    def __init__(self, name):
        self.name = name
        self.terminated = False
        self.released = threading.Event()

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        # Held until the test releases wait(): the old loop wakes only
        # after the new one has started, the window the race needs.
        self.terminated = True

    def wait(self):
        self.released.wait(5)
        return 0


class Rig:
    def __init__(self, monkeypatch, tmp_path):
        for name in ('ringtone.ogg', 'notify.ogg'):
            (tmp_path / name).write_bytes(b'')
        self.spawned = []
        self.threads = []
        self.cond = threading.Condition()
        monkeypatch.setattr(sound, 'sound_dir', lambda: tmp_path)
        monkeypatch.setattr(sound, '_player', lambda: 'paplay')
        monkeypatch.setattr(sound.SoundPlayer, '_spawn',
                            staticmethod(self._spawn))
        rig = self

        class Thread(threading.Thread):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                rig.threads.append(self)

        monkeypatch.setattr(sound.threading, 'Thread', Thread)
        for attr in ('_ring_player', '_notify_player', '_default_player'):
            if hasattr(sound, attr):
                monkeypatch.setattr(sound, attr, sound.SoundPlayer())

    def _spawn(self, _player, path):
        proc = FakeProc(Path(path).name)
        with self.cond:
            self.spawned.append(proc)
            self.cond.notify_all()
        return proc

    def wait_spawned(self, count):
        with self.cond:
            assert self.cond.wait_for(lambda: len(self.spawned) >= count, 5)

    def release_all(self):
        for proc in self.spawned:
            proc.released.set()
        for thread in self.threads:
            thread.join(5)


def test_notification_does_not_stop_the_ringtone(monkeypatch, tmp_path):
    rig = Rig(monkeypatch, tmp_path)
    try:
        sound.play('ringtone.ogg', loop=True)
        rig.wait_spawned(1)
        ring = rig.spawned[0]
        sound.play('notify.ogg')
        assert ring.terminated is False
    finally:
        sound.stop()
        rig.release_all()


def test_no_notification_sound_while_ringing(monkeypatch, tmp_path):
    rig = Rig(monkeypatch, tmp_path)
    try:
        sound.play('ringtone.ogg', loop=True)
        rig.wait_spawned(1)
        sound.notify('notify.ogg')
        assert [p.name for p in rig.spawned] == ['ringtone.ogg']
        sound.stop()
        sound.notify('notify.ogg')
        assert [p.name for p in rig.spawned] == ['ringtone.ogg', 'notify.ogg']
    finally:
        sound.stop()
        rig.release_all()


def test_stop_then_play_leaves_one_loop(monkeypatch, tmp_path):
    rig = Rig(monkeypatch, tmp_path)
    try:
        sound.play('ringtone.ogg', loop=True)
        rig.wait_spawned(1)
        first = rig.spawned[0]
        sound.stop()
        sound.play('ringtone.ogg', loop=True)
        rig.wait_spawned(2)
        old_worker = rig.threads[0]
        first.released.set()            # the old loop wakes up now
        old_worker.join(5)
        assert not old_worker.is_alive()
        assert len(rig.spawned) == 2    # it did not respawn
        assert sound.ringing() is True
    finally:
        sound.stop()
        rig.release_all()
    assert sound.ringing() is False


# -- main.py: no notify.ogg over a ringing call --------------------------------


def _app_with_ringer(ringing):
    import main
    app = main.XXWMApplication()
    app._maybe_banner = lambda *args, **kwargs: None
    app._usage_tracker = None
    config = types.SimpleNamespace(
        is_app_muted=lambda app_id: False, sound_notifications=True)
    shade = types.SimpleNamespace(add_notification=lambda *args: None)
    app._shell = types.SimpleNamespace(
        config=config, focus_state=None, dnd_state=None, nope_state=None,
        _ensure_shade=lambda: shade,
        _call_ui=types.SimpleNamespace(
            _ringer=types.SimpleNamespace(ringing=ringing)))
    return app


def test_notification_quiet_while_call_rings(monkeypatch):
    played = []
    monkeypatch.setattr(sound, 'play', lambda *a, **k: played.append(a))
    monkeypatch.setattr(sound, 'notify', lambda *a, **k: played.append(a),
                        raising=False)
    monkeypatch.setattr(sound, 'ringing', lambda: False, raising=False)
    _app_with_ringer(True)._on_notification(
        7, 'App', 'Hi', 'body', 'org.a.App', {})
    assert played == []
    _app_with_ringer(False)._on_notification(
        8, 'App', 'Hi', 'body', 'org.a.App', {})
    assert played == [('notify.ogg',)]
