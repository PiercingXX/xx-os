"""
Sound playback for shell events (ringtone, notifications).

Plays via `paplay` (PipeWire's pactl frontend, present on every target
distro), falling back to `pw-play`. Loop mode respawns the player from a
daemon thread until stopped. Every failure is a silent no-op — sound must
never take the shell down.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path

_SOUND_DIR_CANDIDATES = [
    Path('/usr/share/xx-wm/sounds'),
    Path('/usr/local/share/xx-wm/sounds'),
    Path(__file__).resolve().parent.parent / 'data' / 'sounds',
]


def sound_dir() -> Path | None:
    for candidate in _SOUND_DIR_CANDIDATES:
        if candidate.is_dir():
            return candidate
    return None


def _player() -> str | None:
    for cmd in ('paplay', 'pw-play'):
        if shutil.which(cmd):
            return cmd
    return None


class SoundPlayer:
    """One player per sound role. Every play() and stop() bumps a
    generation token: a loop worker exits as soon as its token is stale,
    so a stop() followed by a quick play() never leaves two loops
    running, and a one-shot spawned after a stop() is killed at once."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._gen = 0
        self._loop_gen: int | None = None

    @property
    def looping(self) -> bool:
        with self._lock:
            return self._loop_gen is not None and self._loop_gen == self._gen

    def play(self, name: str, loop: bool = False) -> None:
        directory = sound_dir()
        player = _player()
        if directory is None or player is None:
            return
        path = directory / name
        if not path.is_file():
            return
        with self._lock:
            self._gen += 1
            gen = self._gen
            self._loop_gen = gen if loop else None
            old, self._proc = self._proc, None
        _terminate(old)
        if loop:
            threading.Thread(target=self._loop_worker,
                             args=(player, path, gen), daemon=True).start()
            return
        proc = self._spawn(player, path)
        with self._lock:
            if self._gen == gen:
                self._proc = proc
                return
        _terminate(proc)

    def _loop_worker(self, player: str, path: Path, gen: int) -> None:
        while True:
            with self._lock:
                if self._gen != gen:
                    return
                proc = self._spawn(player, path)
                self._proc = proc
                if proc is None:
                    self._loop_gen = None
                    return
            proc.wait()

    @staticmethod
    def _spawn(player: str, path: Path) -> subprocess.Popen | None:
        try:
            return subprocess.Popen(
                [player, str(path)], close_fds=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError:
            return None

    def stop(self) -> None:
        with self._lock:
            self._gen += 1
            self._loop_gen = None
            proc, self._proc = self._proc, None
        _terminate(proc)


def _terminate(proc: subprocess.Popen | None) -> None:
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except OSError:
            pass


# Ringtone and notification sounds never share a player: a notification
# arriving while the phone rings must not stop the ringtone.
_ring_player = SoundPlayer()
_notify_player = SoundPlayer()


def play(name: str, loop: bool = False) -> None:
    """Loops are the ringtone; one-shots go to the notification player."""
    if loop:
        _ring_player.play(name, loop=True)
    else:
        notify(name)


def notify(name: str) -> None:
    """One notification sound; skipped while the ringtone loops."""
    if _ring_player.looping:
        return
    _notify_player.play(name)


def ringing() -> bool:
    return _ring_player.looping


def stop() -> None:
    """Stop the ringtone (every ringer terminal path calls this)."""
    _ring_player.stop()
