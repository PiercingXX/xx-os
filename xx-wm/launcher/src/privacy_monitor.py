"""Privacy indicators (Android/GrapheneOS parity): live mic/camera/location use.

Mic/camera come from the PipeWire graph: one long-lived `pw-dump --monitor`
child on a worker thread, its JSON stream parsed incrementally and restarted
with backoff when it dies. When pw-dump is absent the monitor degrades to
polling single-shot `pw-dump` every few seconds, and only while the screen
is on. Location users come from the shell's GeoClue2 agent
(quick_actions.LocationState) through a provider callback, so this module
never re-implements the agent; they are only asked for while the screen is
on, and the query never starts geoclue.

The monitor child is tied to the monitor thread (PR_SET_PDEATHSIG) and
terminated on stop, so it never outlives the shell.

Snapshots are `{kind: set(display names)}` with kinds `mic`, `camera` and
`location`. `on_change` fires only when the snapshot actually changes, off
the GTK main thread — handlers must hop back with GLib.idle_add.
"""
from __future__ import annotations

import ctypes
import json
import os
import select
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from shell_log import log_exc

MIC = 'mic'
CAMERA = 'camera'
LOCATION = 'location'

KINDS = (MIC, CAMERA, LOCATION)

# Recently-used window for the settings log (Android's 24 h recency).
RECENT_USE_WINDOW_S = 24 * 3600
_MAX_USAGE_ENTRIES = 512
_USAGE_SAVE_INTERVAL_S = 60.0


def default_usage_path() -> Path:
    """Usage-log file (queryable storage for the settings "recently used"
    list). Under XDG_DATA_HOME like the other shell state."""
    base = os.environ.get('XDG_DATA_HOME') or str(
        Path.home() / '.local' / 'share')
    return Path(base) / 'xx-wm' / 'privacy-usage.json'


def indicators_enabled(config) -> bool:
    """Privacy indicators master switch (docs/config.md): on by default,
    corrupt or missing values fail open to shown."""
    try:
        value = getattr(config, 'privacy_indicators', True)
    except Exception:
        return True
    return value if isinstance(value, bool) else True


class UsageLog:
    """Last-24-hours record of mic/camera/location use for settings.

    note_snapshot() folds one monitor snapshot into (kind, app) pairs
    stamped with the last-seen epoch; recent() reads back newest-first
    entries inside the window. Persisted best-effort as JSON (the log
    directory is created mode 0700 on first use); corrupt files read
    as empty, never raise.
    """

    def __init__(self, path=None, clock=None) -> None:
        self._path = Path(path) if path is not None else default_usage_path()
        self._clock = clock or time.time
        self._entries: dict[tuple[str, str], float] = {}
        self._last_save: float | None = None
        self._dirty = False
        self.load()

    def note_snapshot(self, snapshot: dict | None,
                      now: float | None = None) -> bool:
        """Record every (kind, app) currently in use. Returns True when
        the file was rewritten. Timestamps always move forward in memory;
        the file is rewritten immediately only for a new (kind, app) key,
        otherwise at most once per 60 s (flash coalescing for eMMC/UFS)."""
        try:
            at = float(now) if now is not None else float(self._clock())
        except (TypeError, ValueError):
            return False
        if not isinstance(snapshot, dict):
            return False
        touched = False
        new_key = False
        for kind in KINDS:
            try:
                apps = snapshot.get(kind) or ()
            except Exception:
                continue
            for app in apps:
                name = str(app).strip() if app is not None else ''
                if not name:
                    continue
                key = (kind, name)
                old = self._entries.get(key)
                if old is None:
                    self._entries[key] = at
                    touched = True
                    new_key = True
                elif old != at:
                    self._entries[key] = at
                    touched = True
        if not touched:
            return False
        self._prune(at)
        if new_key:
            self.save()
            self._last_save = at
            self._dirty = False
            return True
        if self._last_save is None or (
                at - self._last_save) >= _USAGE_SAVE_INTERVAL_S:
            self.save()
            self._last_save = at
            self._dirty = False
            return True
        self._dirty = True
        return False

    def flush(self, now: float | None = None) -> bool:
        """Write pending timestamp-only updates (call on shutdown)."""
        if not self._dirty:
            return False
        try:
            at = float(now) if now is not None else float(self._clock())
        except (TypeError, ValueError):
            return False
        self._prune(at)
        self.save()
        self._last_save = at
        self._dirty = False
        return True

    def shutdown(self) -> None:
        """Coalesced-log shutdown hook: persist any pending updates."""
        try:
            self.flush()
        except Exception:
            pass

    def recent(self, now: float | None = None,
               window_s: float = RECENT_USE_WINDOW_S) -> list[dict]:
        """[{kind, app, at}] inside the window, newest first."""
        try:
            at = float(now) if now is not None else float(self._clock())
        except (TypeError, ValueError):
            return []
        try:
            window = float(window_s)
        except (TypeError, ValueError):
            window = float(RECENT_USE_WINDOW_S)
        cutoff = at - window
        items = [{'kind': kind, 'app': app, 'at': seen}
                 for (kind, app), seen in self._entries.items()
                 if seen >= cutoff]
        items.sort(key=lambda entry: entry['at'], reverse=True)
        return items

    def _prune(self, now: float) -> None:
        cutoff = now - RECENT_USE_WINDOW_S
        self._entries = {key: seen for key, seen in self._entries.items()
                         if seen >= cutoff}
        if len(self._entries) > _MAX_USAGE_ENTRIES:
            ordered = sorted(self._entries.items(),
                             key=lambda item: item[1], reverse=True)
            self._entries = dict(ordered[:_MAX_USAGE_ENTRIES])

    def to_dict(self) -> dict:
        return {'entries': [{'kind': kind, 'app': app, 'at': seen}
                            for (kind, app), seen in self._entries.items()]}

    def from_dict(self, raw: object) -> None:
        entries: dict[tuple[str, str], float] = {}
        items = raw.get('entries') if isinstance(raw, dict) else None
        if not isinstance(items, list):
            self._entries = entries
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            kind, app = item.get('kind'), item.get('app')
            if kind not in KINDS or not isinstance(app, str) or not app:
                continue
            try:
                seen = float(item.get('at'))
            except (TypeError, ValueError):
                continue
            entries[(str(kind), app)] = seen
        self._entries = entries

    def load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self._entries = {}
            return
        try:
            self.from_dict(raw)
        except Exception:
            self._entries = {}

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError:
            return
        try:
            self._path.write_text(json.dumps(self.to_dict()) + '\n',
                                  encoding='utf-8')
            os.chmod(self._path, 0o600)
        except OSError:
            pass

_SHORT_LABELS = {MIC: 'mic', CAMERA: 'cam', LOCATION: 'loc'}
_KIND_ORDER = (MIC, CAMERA, LOCATION)

_MIC_CLASSES = frozenset({'Stream/Input/Audio'})
_CAMERA_CLASSES = frozenset({'Stream/Input/Video'})
_NODE_TYPE = 'PipeWire:Interface:Node'

POLL_INTERVAL_SECONDS = 3.0
# Screen off: nothing shows the indicators, so the location re-query
# (and the fallback pw-dump poll) wait this long between passes; waking
# the screen catches up at once.
DARK_POLL_SECONDS = 60.0
_MONITOR_ARGV = ('pw-dump', '--monitor')
_BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)
_READ_CHUNK_BYTES = 65536


def display_name_for(props: dict) -> str | None:
    """Best human label for a PipeWire stream's owner.

    Human name first, then the portal Flatpak id, then the desktop id, then
    the node name; the pid label is the last resort. Returns None when the
    props carry nothing attributable at all.
    """
    if not isinstance(props, dict):
        return None
    for key in ('application.name', 'pipewire.access.portal.app_id',
                'application.id', 'node.name'):
        value = props.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    pid = props.get('application.process.id')
    try:
        return 'pid %d' % int(pid)
    except (TypeError, ValueError):
        return None


def classify_node(obj: dict) -> tuple[str, str] | None:
    """(kind, display name) for one pw-dump object, or None when not a live
    mic/camera stream. Only `running` input streams count; devices, sinks
    and suspended nodes never do."""
    if not isinstance(obj, dict):
        return None
    if obj.get('type', _NODE_TYPE) != _NODE_TYPE:
        return None
    info = obj.get('info')
    if not isinstance(info, dict):
        return None
    if str(info.get('state', '')).lower() != 'running':
        return None
    props = info.get('props')
    if not isinstance(props, dict):
        return None
    media_class = str(props.get('media.class', ''))
    if media_class in _MIC_CLASSES:
        kind = MIC
    elif media_class in _CAMERA_CLASSES:
        kind = CAMERA
    else:
        return None
    name = display_name_for(props)
    if not name:
        return None
    return (kind, name)


def format_indicator(snapshot: dict | None) -> str:
    """Compact `● mic · cam · loc` line with only the active kinds, in
    fixed mic/camera/location order. Empty when nothing is in use; the
    accent color is applied by the widget's CSS class, not here."""
    active = [kind for kind in _KIND_ORDER
              if snapshot and snapshot.get(kind)]
    if not active:
        return ''
    return '● ' + ' · '.join(_SHORT_LABELS[kind] for kind in active)


def detail_text(snapshot: dict | None) -> str:
    """One `mic: Foo, Bar` line per active kind for the shade tap-through."""
    lines = []
    for kind in _KIND_ORDER:
        apps = sorted((snapshot or {}).get(kind) or ())
        if apps:
            lines.append('%s in use by: %s'
                         % (_SHORT_LABELS[kind], ', '.join(apps)))
    return '\n'.join(lines)


class PwDumpParser:
    """Incremental JSON extraction for `pw-dump --monitor` stdout.

    The stream is a run of JSON values (the initial full-dump array, then
    one object per graph change) arriving in arbitrary chunks. feed()
    buffers bytes and returns every value that completed since the last
    call; whitespace and stray commas between values are skipped.
    """

    def __init__(self) -> None:
        self._buffer = ''
        self._decoder = json.JSONDecoder()

    def feed(self, chunk: str | bytes) -> list:
        """Append a chunk, return the newly completed JSON values."""
        if isinstance(chunk, bytes):
            try:
                chunk = chunk.decode('utf-8', 'replace')
            except Exception:
                return []
        self._buffer += chunk
        values = []
        text = self._buffer
        pos = 0
        end = len(text)
        while True:
            while pos < end and (text[pos].isspace() or text[pos] == ','):
                pos += 1
            if pos >= end:
                break
            try:
                value, next_pos = self._decoder.raw_decode(text, pos)
            except json.JSONDecodeError:
                break
            values.append(value)
            pos = next_pos
        self._buffer = text[pos:]
        return values


def _resolve_prctl():
    try:
        return ctypes.CDLL(None, use_errno=True).prctl
    except (OSError, AttributeError):
        return None


_prctl = _resolve_prctl()
_PR_SET_PDEATHSIG = 1


def _die_with_thread() -> None:
    """preexec_fn: SIGTERM the child when the spawning (monitor) thread
    ends, including when the shell dies without calling stop()."""
    if _prctl is not None:
        _prctl(_PR_SET_PDEATHSIG, signal.SIGTERM)


def _end_child(proc) -> None:
    try:
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=1)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=1)
        except Exception:
            log_exc('privacy_monitor._end_child: proc.kill')


def _default_pw_dump_once() -> str:
    try:
        out = subprocess.run(['pw-dump'], capture_output=True, text=True,
                             timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return ''
    return out.stdout if out.returncode == 0 else ''


class PrivacyMonitor:
    """Live mic/camera/location snapshot for the shell indicators."""

    def __init__(self, on_change=None, location_provider=None,
                 popen_factory=None, pw_dump_runner=None,
                 poll_interval: float = POLL_INTERVAL_SECONDS,
                 backoff: tuple = _BACKOFF_SECONDS,
                 usage_log=None) -> None:
        self._on_change = on_change
        self._usage_log = usage_log
        self._location_provider = location_provider
        self._popen = popen_factory or subprocess.Popen
        self._pw_dump_once = pw_dump_runner or _default_pw_dump_once
        self._poll_interval = max(1.0, float(poll_interval or 1.0))
        self._backoff = tuple(backoff) or (5.0,)
        self._screen_on = True
        self._fallback_mode = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._nodes: dict[int, tuple[str, str]] = {}
        self._location_apps: set[str] = set()
        self._snapshot: dict[str, set[str]] = {k: set() for k in KINDS}
        self._emitted: dict[str, set[str]] | None = None
        self._dump_seen = False
        self._feed_parser = PwDumpParser()

    @property
    def snapshot(self) -> dict[str, set[str]]:
        with self._lock:
            return {kind: set(apps)
                    for kind, apps in self._snapshot.items()}

    def set_screen_on(self, on: bool) -> None:
        """Gate the location re-query and the pw-dump-absent fallback
        poll: none while off; turning on catches up immediately."""
        on = bool(on)
        was_on, self._screen_on = self._screen_on, on
        thread = self._thread
        if on and not was_on and thread is not None and thread.is_alive():
            try:
                threading.Thread(target=self._catch_up, daemon=True,
                                 name='xx-wm-privacy-wake').start()
            except RuntimeError:
                pass

    def _catch_up(self) -> None:
        if self._fallback_mode:
            self.poll_once()
        self.refresh_location()

    def _interval(self) -> float:
        if self._screen_on:
            return self._poll_interval
        return max(self._poll_interval, DARK_POLL_SECONDS)

    def note_screen_blank(self, blanked: bool) -> None:
        self.set_screen_on(not blanked)

    def feed_data(self, chunk: str | bytes) -> None:
        """Ingest raw pw-dump output; the parser persists across calls so
        values split over many chunks still complete."""
        for value in self._feed_parser.feed(chunk):
            self.update_from_value(value)

    def update_from_value(self, value: object) -> None:
        """Fold one parsed JSON value (dump array or change object) into
        the node table. Objects without `info` mark a removed node."""
        with self._lock:
            if isinstance(value, list):
                if not self._dump_seen:
                    self._nodes.clear()
                    self._dump_seen = True
                for obj in value:
                    self._upsert_locked(obj)
            elif isinstance(value, dict):
                self._upsert_locked(value)
            self._recompute_locked()
        self._maybe_emit()

    def _upsert_locked(self, obj: object) -> None:
        if not isinstance(obj, dict):
            return
        try:
            node_id = int(obj.get('id'))
        except (TypeError, ValueError):
            return
        if obj.get('type', _NODE_TYPE) != _NODE_TYPE:
            return
        info = obj.get('info')
        if not isinstance(info, dict):
            self._nodes.pop(node_id, None)
            return
        classified = classify_node(obj)
        if classified is None:
            self._nodes.pop(node_id, None)
        else:
            self._nodes[node_id] = classified

    def _recompute_locked(self) -> None:
        fresh: dict[str, set[str]] = {k: set() for k in KINDS}
        for kind, name in self._nodes.values():
            fresh[kind].add(name)
        fresh[LOCATION] = set(self._location_apps)
        self._snapshot = fresh

    def refresh_location(self) -> None:
        """Re-query the location provider (GeoClue active clients); never
        while the screen is off, where nobody can see the indicator."""
        if not self._screen_on:
            return
        try:
            apps = self._location_provider() if self._location_provider else ()
        except Exception:
            return
        try:
            names = {str(a).strip() for a in apps or () if str(a).strip()}
        except Exception:
            return
        with self._lock:
            if names == self._location_apps:
                return
            self._location_apps = names
            self._recompute_locked()
        self._maybe_emit()

    def poll_once(self) -> None:
        """Single-shot `pw-dump` refresh (the absent-binary fallback path)."""
        try:
            payload = self._pw_dump_once()
        except Exception:
            return
        if not payload:
            return
        try:
            values = json.loads(payload)
        except (TypeError, ValueError):
            return
        if isinstance(values, dict):
            values = [values]
        if not isinstance(values, list):
            return
        with self._lock:
            for obj in values:
                self._upsert_locked(obj)
            self._recompute_locked()
        self._maybe_emit()
        self.refresh_location()

    def _maybe_emit(self) -> None:
        with self._lock:
            current = {k: set(v) for k, v in self._snapshot.items()}
            changed = self._emitted is None or current != self._emitted
            if changed:
                self._emitted = {k: set(v) for k, v in current.items()}
        if self._usage_log is not None:
            try:
                self._usage_log.note_snapshot(current)
            except Exception:
                log_exc('_maybe_emit: _usage_log.note_snapshot')
        if not changed or self._on_change is None:
            return
        try:
            self._on_change({k: set(v) for k, v in current.items()})
        except Exception:
            log_exc('privacy_monitor._maybe_emit: _on_change')

    def start(self) -> None:
        self.stop()
        self._stop_event.clear()
        try:
            thread = threading.Thread(target=self._run, daemon=True,
                                      name='xx-wm-privacy')
            thread.start()
            self._thread = thread
        except Exception:
            self._thread = None

    def stop(self) -> None:
        self._stop_event.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            try:
                thread.join(timeout=2.0)
            except Exception:
                pass
        try:
            flush = getattr(self._usage_log, 'flush', None)
            if callable(flush):
                flush()
        except Exception:
            log_exc('privacy_monitor.stop: getattr')

    def _run(self) -> None:
        if shutil.which('pw-dump') is None:
            self._fallback_loop()
            return
        try:
            proc = self._spawn_monitor()
        except (OSError, ValueError):
            self._fallback_loop()
            return
        if proc is None or getattr(proc, 'stdout', None) is None:
            try:
                if proc is not None:
                    proc.wait(timeout=1)
            except Exception:
                pass
            self._fallback_loop()
            return
        attempt = 0
        while not self._stop_event.is_set():
            if self._watch(proc):
                attempt = 0
                continue
            attempt += 1
            wait = self._backoff[min(attempt - 1, len(self._backoff) - 1)]
            if self._stop_event.wait(wait):
                break
            try:
                proc = self._spawn_monitor()
            except (OSError, ValueError):
                self._fallback_loop()
                return
            if proc is None or getattr(proc, 'stdout', None) is None:
                self._fallback_loop()
                return

    def _spawn_monitor(self):
        return self._popen(list(_MONITOR_ARGV), stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, close_fds=True,
                           preexec_fn=_die_with_thread)

    def _watch(self, proc) -> bool:
        """Drain one monitor child until it dies; True only on a clean
        shutdown (stop requested), False when it must be restarted."""
        parser = PwDumpParser()
        try:
            fileno = proc.stdout.fileno()
        except Exception:
            fileno = None
        while not self._stop_event.is_set():
            if fileno is not None:
                try:
                    ready, _, _ = select.select(
                        [fileno], [], [], self._interval())
                except (OSError, ValueError):
                    ready = []
            else:
                ready = []
                if self._stop_event.wait(self._interval()):
                    break
            if ready:
                try:
                    import os
                    chunk = os.read(fileno, _READ_CHUNK_BYTES)
                except OSError:
                    break
                if not chunk:
                    break
                for value in parser.feed(chunk):
                    self.update_from_value(value)
            self.refresh_location()
        _end_child(proc)
        return self._stop_event.is_set()

    def _fallback_loop(self) -> None:
        self._fallback_mode = True
        while not self._stop_event.is_set():
            self._fallback_tick()
            if self._stop_event.wait(self._interval()):
                break

    def _fallback_tick(self) -> bool:
        """One fallback pass; False when polling is gated off."""
        if not self._screen_on:
            return False
        self.poll_once()
        return True
