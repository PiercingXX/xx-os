"""Wayland protocol bindings generated at runtime from the installed pywayland.

Distros ship different pywayland versions whose scanners emit incompatible
bindings (0.4.19 generates generic ``Global[...]``/``Resource[...]`` modules
that older runtimes cannot even import; 0.4.18 emits a package-per-protocol
layout instead of single files), so committing one generation breaks the
switcher on any other version. Instead the shell runs the INSTALLED
pywayland's own scanner on first use and caches the output keyed by
``pywayland.__version__``, guaranteeing the generated code always matches
the runtime importing it.

Every entry point here is safe to call on the GTK main loop except
``ensure_bindings`` and ``ensure_protocol_bindings``, which block in the
scanner subprocess and must run in a worker thread. All failures degrade
to None with a single log line.
"""

from __future__ import annotations

import importlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Sequence
from pathlib import Path

log = logging.getLogger(__name__)

PROTO_PACKAGE_NAME = 'xx_wm_wayland_proto'
WAYLAND_XML_NAME = 'wayland.xml'
WLR_XML_NAME = 'wlr-foreign-toplevel-management-unstable-v1.xml'
OUTPUT_POWER_XML_NAME = 'wlr-output-power-management-unstable-v1.xml'
NEEDED_CLASS_NAMES = (
    'WlSeat',
    'ZwlrForeignToplevelManagerV1',
    'ZwlrForeignToplevelHandleV1',
)
OUTPUT_POWER_XML_NAMES = (WAYLAND_XML_NAME, OUTPUT_POWER_XML_NAME)
OUTPUT_POWER_CLASS_NAMES = (
    'WlOutput',
    'ZwlrOutputPowerManagerV1',
    'ZwlrOutputPowerV1',
)
VIRTUAL_POINTER_XML_NAME = 'virtual-pointer-unstable-v1.xml'
VIRTUAL_POINTER_XML_NAMES = (WAYLAND_XML_NAME, VIRTUAL_POINTER_XML_NAME)
VIRTUAL_POINTER_CLASS_NAMES = (
    'WlSeat',
    'WlOutput',
    'ZwlrVirtualPointerManagerV1',
    'ZwlrVirtualPointerV1',
)
SCREENCAST_XML_NAME = 'wlr-screencopy-unstable-v1.xml'
SCREENCAST_XML_NAMES = (WAYLAND_XML_NAME, SCREENCAST_XML_NAME)
SCREENCAST_CLASS_NAMES = (
    'WlOutput',
    'WlShm',
    'ZwlrScreencopyManagerV1',
    'ZwlrScreencopyFrameV1',
)
# phosh_private (verbatim from phoc, MIT-style licence): XX-WM uses only
# set_shell_state (since v6) to lower phoc's -S shield. The XML references
# interfaces from protocols we do not vendor (layer-shell, screencopy,
# foreign-toplevel); strip_unknown_interfaces() drops those attributes on
# a scanner copy so generation works — request opcodes are positional,
# so the requests we send keep their wire numbers.
PHOSH_PRIVATE_XML_NAME = 'phosh-private.xml'
PHOSH_PRIVATE_XML_NAMES = (WAYLAND_XML_NAME, PHOSH_PRIVATE_XML_NAME)
PHOSH_PRIVATE_CLASS_NAMES = ('PhoshPrivate',)
PHOSH_PRIVATE_MIN_VERSION = 6
PHOSH_PRIVATE_MAX_VERSION = 8
SHELL_STATE_UP = 1
# Safety net for the no-PIN path only: if neither the shell nor the
# wizard reported ready by then (a bug, or a ~25 s a11y stall twice
# over), lower the shield anyway rather than leave a dead black screen.
# With a PIN configured the shield never drops before the lock maps.
SHELL_STATE_WATCHDOG_SECONDS = 90
_WL_OBJECT_ATTR = ' interface="wl_object"'
_SCANNER_TIMEOUT_SECONDS = 180

# Generation is serialized (threads in-process, flock across processes) and
# built in a temp dir that is swapped into place: two workers generating at
# once used to rmtree the dir under each other's scanner, which failed with
# "No such file ... wayland.py" on the Librem (pywayland 0.4.19).
_GENERATE_LOCK = threading.Lock()
_COMPLETE_MARKER = '.complete'

_logged_once: set[str] = set()


def _log_once(level: int, key: str, message: str, *args: object) -> None:
    if key in _logged_once:
        return
    _logged_once.add(key)
    log.log(level, message, *args)


def cache_root() -> Path:
    """Base cache dir: ``$XDG_CACHE_HOME/xx-wm/wayland_proto``."""
    base = os.environ.get('XDG_CACHE_HOME') or str(Path.home() / '.cache')
    return Path(base) / 'xx-wm' / 'wayland_proto'


def _xml_digest(xml_names: Sequence[str] | None = None) -> str:
    """Short hash of the protocol XMLs, so editing them invalidates the cache
    (a stale generation would mis-decode new events and kill the connection)."""
    import hashlib
    digest = hashlib.sha256()
    for name in (WAYLAND_XML_NAME, WLR_XML_NAME) if xml_names is None else xml_names:
        path = protocol_xml_path(name)
        if path is not None:
            try:
                digest.update(path.read_bytes())
            except OSError:
                pass
    return digest.hexdigest()[:12]


def cache_dir_for(version: str, xml_names: Sequence[str] | None = None) -> Path:
    """Cache dir keyed by pywayland version AND protocol XML content, so a
    pywayland upgrade or an XML update never reads stale output."""
    if xml_names is None:
        digest = _xml_digest()
    else:
        digest = _xml_digest(tuple(xml_names))
    return cache_root() / f'{version}-{digest}'


def package_dir_for(version: str, xml_names: Sequence[str] | None = None) -> Path:
    """Directory the scanner writes into (imported as PROTO_PACKAGE_NAME)."""
    return cache_dir_for(version, xml_names) / PROTO_PACKAGE_NAME


def installed_pywayland_version() -> str | None:
    """Installed pywayland version, or None when pywayland is not importable."""
    try:
        import pywayland
    except Exception:
        return None
    version = getattr(pywayland, '__version__', None)
    return str(version) if version else 'unknown'


def protocol_xml_path(name: str) -> Path | None:
    """Locate a protocol XML next to this module (installed tree) or under
    the repo's data dir (running from the source checkout)."""
    here = Path(__file__).resolve().parent
    for candidate in (here / name, here.parent / 'data' / name):
        if candidate.is_file():
            return candidate
    return None


def strip_wl_object(xml_text: str) -> str:
    """Drop the stale ``interface="wl_object"`` attribute the vendored
    wayland.xml carries. The scanner requires every referenced interface to
    be declared; ``wl_object`` is not one, and upstream wayland.xml removed
    the attribute (its generated module types that argument as plain
    object). Wire format is unchanged."""
    return xml_text.replace(_WL_OBJECT_ATTR, '')


def generate_protocol_bindings(
    package_dir: Path, xml_paths: Sequence[str | Path],
) -> None:
    """Run the installed pywayland scanner over ``xml_paths`` into ``package_dir``.

    ``wayland.xml`` is the include input (``-i``, with the stale
    ``wl_object`` attribute stripped); every other XML is a scanner target.
    When ``xml_paths`` omits it, the installed ``wayland.xml`` is used as
    the include so cross-protocol references (e.g. ``wl_output``) resolve.

    Raises on any failure; the caller logs and degrades. Blocking (scanner
    subprocess) — never call on the GTK main loop.
    """
    paths = [Path(p) for p in xml_paths]
    base = next((p for p in paths if p.name == WAYLAND_XML_NAME), None)
    if base is None:
        base = protocol_xml_path(WAYLAND_XML_NAME)
        if base is None:
            raise RuntimeError('wayland.xml not found for scanner include')
    targets = [str(p) for p in paths if p != base]
    if not targets:
        raise RuntimeError('no protocol XMLs to generate bindings for')
    package_dir = Path(package_dir)
    package_dir.parent.mkdir(parents=True, exist_ok=True)
    import fcntl
    with _GENERATE_LOCK, open(package_dir.parent / '.generate.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (package_dir / _COMPLETE_MARKER).exists():
            return  # another worker or process generated it meanwhile
        _generate_into(package_dir, base, targets)


def _generate_into(package_dir: Path, base: Path, targets: list) -> None:
    """Scanner into a fresh temp dir, then swap it into ``package_dir``."""
    work = Path(tempfile.mkdtemp(prefix=package_dir.name + '.tmp-',
                                 dir=str(package_dir.parent)))
    try:
        _run_scanner(work, base, targets)
        (work / _COMPLETE_MARKER).write_text('')
        if package_dir.exists():
            shutil.rmtree(package_dir, ignore_errors=True)
        os.replace(work, package_dir)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _run_scanner(package_dir: Path, base: Path, targets: list) -> None:
    stripped = strip_wl_object(base.read_text(encoding='utf-8'))
    fd, stripped_path = tempfile.mkstemp(
        prefix='wayland-stripped-', suffix='.xml',
        dir=str(package_dir.parent), text=True,
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as tmp:
            tmp.write(stripped)
        proc = subprocess.run(
            [sys.executable, '-m', 'pywayland.scanner',
             '-o', str(package_dir),
             '-i', stripped_path, *targets],
            capture_output=True, text=True, timeout=_SCANNER_TIMEOUT_SECONDS,
        )
    finally:
        try:
            os.unlink(stripped_path)
        except OSError:
            pass
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f'pywayland scanner failed: {detail}')
    _ensure_packages_importable(package_dir)


def generate_bindings(package_dir: Path, wayland_xml: Path, wlr_xml: Path) -> None:
    """Run the installed pywayland scanner into ``package_dir``.

    Raises on any failure; the caller logs and degrades. Blocking (scanner
    subprocess) — never call on the GTK main loop.
    """
    generate_protocol_bindings(package_dir, [wayland_xml, wlr_xml])


def _ensure_packages_importable(package_dir: Path) -> None:
    """Touch in missing ``__init__.py`` files so dotted imports work whatever
    layout the scanner emitted (flat files or nested protocol packages)."""
    for dirpath, _dirnames, filenames in os.walk(package_dir):
        if any(name.endswith('.py') for name in filenames):
            init = Path(dirpath) / '__init__.py'
            if not init.exists():
                init.touch()


def _drop_cached_modules(package_name: str) -> None:
    for name in [n for n in sys.modules
                 if n == package_name or n.startswith(package_name + '.')]:
        sys.modules.pop(name, None)


def load_classes_from(
    package_dir: Path, class_names: Sequence[str] | None = None,
) -> dict[str, type] | None:
    """Import generated bindings under their unique package name and collect
    the classes the caller binds.

    Layout-agnostic: walks every ``*.py`` under ``package_dir``, so both the
    single-file-module layout and the package-per-protocol layout resolve.
    Returns None (never raises) when anything is missing or unimportable.
    """
    wanted = tuple(NEEDED_CLASS_NAMES) if class_names is None else tuple(class_names)
    package_dir = Path(package_dir)
    if not package_dir.is_dir():
        return None
    # Every protocol set generates into its own cache dir under the same
    # package name, so another set's package may already be imported (and
    # earlier on sys.path): put this dir first and drop the other one.
    parent = str(package_dir.parent)
    if parent in sys.path:
        sys.path.remove(parent)
    sys.path.insert(0, parent)
    package_name = package_dir.name
    loaded = sys.modules.get(package_name)
    if loaded is not None and [str(Path(p)) for p in
                               getattr(loaded, '__path__', [])] != [str(package_dir)]:
        _drop_cached_modules(package_name)
    try:
        py_files = sorted(package_dir.rglob('*.py'))
    except OSError:
        return None
    found: dict[str, type] = {}
    for path in py_files:
        rel = path.relative_to(package_dir).with_suffix('')
        parts = [package_name, *rel.parts]
        if parts[-1] == '__init__':
            parts = parts[:-1]
        try:
            module = importlib.import_module('.'.join(parts))
        except Exception:
            continue
        for name in wanted:
            if name not in found:
                candidate = getattr(module, name, None)
                if isinstance(candidate, type):
                    found[name] = candidate
        if len(found) == len(wanted):
            break
    if len(found) != len(wanted):
        return None
    return found


def load_cached_bindings(
    class_names: Sequence[str] | None = None,
    xml_names: Sequence[str] | None = None,
) -> dict[str, type] | None:
    """Load previously generated bindings without running the scanner.

    Import-time safe (no subprocess, no blocking): None when pywayland is
    absent or no complete cache exists for its version.
    """
    version = installed_pywayland_version()
    if version is None:
        return None
    try:
        return load_classes_from(package_dir_for(version, xml_names), class_names)
    except Exception:
        return None


def ensure_bindings() -> dict[str, type] | None:
    """Load cached bindings, generating them with the installed scanner when
    absent. Blocking — call only from a worker thread. None on any failure,
    logged once per process; a later call retries generation, so a transient
    failure (e.g. a concurrent install) can still recover.
    """
    version = installed_pywayland_version()
    if version is None:
        _log_once(logging.INFO, 'no-pywayland',
                  'pywayland is not installed; toplevel manager disabled')
        return None
    package_dir = package_dir_for(version)
    try:
        cached = load_classes_from(package_dir)
    except Exception:
        cached = None
    if cached is not None:
        return cached
    wayland_xml = protocol_xml_path(WAYLAND_XML_NAME)
    wlr_xml = protocol_xml_path(WLR_XML_NAME)
    if wayland_xml is None or wlr_xml is None:
        _log_once(logging.INFO, 'xml-missing',
                  'wayland protocol XMLs not found; toplevel manager disabled')
        return None
    try:
        generate_bindings(package_dir, wayland_xml, wlr_xml)
    except Exception as exc:
        _log_once(logging.INFO, 'generate-failed',
                  'failed to generate wayland protocol bindings: %s', exc)
        return None
    _drop_cached_modules(package_dir.name)
    try:
        loaded = load_classes_from(package_dir)
    except Exception:
        loaded = None
    if loaded is None:
        _log_once(logging.INFO, 'load-failed',
                  'generated wayland protocol bindings are unusable; '
                  'toplevel manager disabled')
        return None
    return loaded


def ensure_screencopy_bindings() -> dict[str, type] | None:
    """Load cached wlr-screencopy bindings, generating them with the
    installed scanner when absent. Blocking — call only from a worker
    thread. None on any failure. The XML ships under launcher/data/; no
    vendored generated module is ever imported."""
    return ensure_protocol_bindings(SCREENCAST_XML_NAMES, SCREENCAST_CLASS_NAMES)


def ensure_protocol_bindings(
    xml_names: Sequence[str], class_names: Sequence[str],
) -> dict[str, type] | None:
    """Load cached bindings for ``xml_names``/``class_names``, generating them
    with the installed scanner when absent. Blocking — call only from a
    worker thread. None on any failure, logged once per process; a later
    call retries generation, so a transient failure can still recover.

    The cache dir is keyed by the contents of every named XML, so editing
    any one of them invalidates the generation.
    """
    names = tuple(xml_names)
    wanted = tuple(class_names)
    version = installed_pywayland_version()
    if version is None:
        _log_once(logging.INFO, 'no-pywayland-proto',
                  'pywayland is not installed; protocol bindings disabled')
        return None
    package_dir = package_dir_for(version, names)
    try:
        cached = load_classes_from(package_dir, wanted)
    except Exception:
        cached = None
    if cached is not None:
        return cached
    xml_paths: list[Path] = []
    for name in names:
        path = protocol_xml_path(name)
        if path is None:
            _log_once(logging.INFO, 'xml-missing-proto',
                      'wayland protocol XML %s not found; '
                      'protocol bindings disabled', name)
            return None
        xml_paths.append(path)
    try:
        generate_protocol_bindings(package_dir, xml_paths)
    except Exception as exc:
        _log_once(logging.INFO, 'generate-failed-proto',
                  'failed to generate wayland protocol bindings: %s', exc)
        return None
    _drop_cached_modules(package_dir.name)
    try:
        loaded = load_classes_from(package_dir, wanted)
    except Exception:
        loaded = None
    if loaded is None:
        _log_once(logging.INFO, 'load-failed-proto',
                  'generated wayland protocol bindings are unusable')
        return None
    return loaded


# ---------------------------------------------------------------------------
# phoc shell mode (-S): report the shell up through phosh_private
# ---------------------------------------------------------------------------

def strip_unknown_interfaces(xml_text: str, known: Sequence[str]) -> str:
    """Drop ``interface="X"`` from every arg whose X is neither declared in
    ``xml_text`` nor in ``known``. Unused requests then decode as generic
    objects/new_ids; opcodes (positional) are unchanged."""
    import re
    declared = set(re.findall(r'<interface\s+name="([^"]+)"', xml_text))
    declared.update(known)

    def _sub(match: re.Match) -> str:
        return match.group(0) if match.group(1) in declared else ''

    return re.sub(r'\s+interface="([^"]+)"', _sub, xml_text)


def _declared_interfaces(path: Path) -> list[str]:
    import re
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return []
    return re.findall(r'<interface\s+name="([^"]+)"', text)


def ensure_phosh_private_bindings() -> dict[str, type] | None:
    """Bindings for phosh_private (scanner on a stripped copy). Blocking —
    worker thread only. None on any failure, logged once."""
    version = installed_pywayland_version()
    if version is None:
        _log_once(logging.INFO, 'no-pywayland-phosh',
                  'pywayland is not installed; phoc shell state not reported')
        return None
    names = PHOSH_PRIVATE_XML_NAMES
    package_dir = package_dir_for(version, names)
    try:
        cached = load_classes_from(package_dir, PHOSH_PRIVATE_CLASS_NAMES)
    except Exception:
        cached = None
    if cached is not None:
        return cached
    wayland_xml = protocol_xml_path(WAYLAND_XML_NAME)
    phosh_xml = protocol_xml_path(PHOSH_PRIVATE_XML_NAME)
    if wayland_xml is None or phosh_xml is None:
        _log_once(logging.INFO, 'xml-missing-phosh',
                  'phosh-private.xml not found; phoc shell state not reported')
        return None
    try:
        package_dir.parent.mkdir(parents=True, exist_ok=True)
        stripped = strip_unknown_interfaces(
            phosh_xml.read_text(encoding='utf-8'),
            _declared_interfaces(wayland_xml))
        fd, tmp = tempfile.mkstemp(prefix='phosh-private-', suffix='.xml',
                                   dir=str(package_dir.parent), text=True)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                handle.write(stripped)
            generate_protocol_bindings(package_dir, [wayland_xml, Path(tmp)])
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except Exception as exc:
        _log_once(logging.INFO, 'generate-failed-phosh',
                  'failed to generate phosh_private bindings: %s', exc)
        return None
    _drop_cached_modules(package_dir.name)
    try:
        return load_classes_from(package_dir, PHOSH_PRIVATE_CLASS_NAMES)
    except Exception:
        return None


class ShellStateGate:
    """When to tell phoc the shell is up (pure, GTK-free).

    UP needs the shell (or first-boot wizard) on screen AND, when a PIN
    is configured, the lock surface mapped — phoc then lowers its shield
    straight onto the lock, never onto unlocked apps. Sticky: reported
    once per process. A crash drops the phosh_private connection, phoc
    raises the shield again, and the restarted shell repeats this.
    """

    def __init__(self) -> None:
        self.shell_ready = False
        self.lock_required = False
        self.lock_mapped = False
        self.reported = False

    def ready_to_report(self) -> bool:
        if self.reported or not self.shell_ready:
            return False
        return self.lock_mapped or not self.lock_required


class ShellStateReporter:
    """Binds phosh_private on its own wl_display in a worker thread and
    sends set_shell_state(UP) once the gate opens.

    The connection is held for the whole process: phoc resets the state
    to UNKNOWN (shield up, input blocked) when the binding goes away.
    Every failure — no pywayland, phosh_private not advertised (older
    phoc, other compositors), version < 6, bind refused — degrades to a
    log line and nothing else; phoc without -S never needed the report.
    The note_* methods are called on the GTK main loop and never block.
    """

    def __init__(self, *, bind_fn=None, display_factory=None,
                 start_thread: bool = True, pin_configured=None,
                 watchdog_seconds: float = SHELL_STATE_WATCHDOG_SECONDS,
                 ) -> None:
        self.gate = ShellStateGate()
        self._pin_configured = pin_configured or pin_configured_on_disk
        self._watchdog_seconds = watchdog_seconds
        self._lock = threading.Lock()
        self._up = threading.Event()
        self._bind_fn = bind_fn or ensure_phosh_private_bindings
        self._display_factory = display_factory
        self._display = None
        self._registry = None
        self._phosh = None
        self.sent = False
        self.available: bool | None = None
        self._thread: threading.Thread | None = None
        if start_thread:
            self.start()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run,
                                        name='xx-shell-state', daemon=True)
        self._thread.start()

    # -- main-loop side ---------------------------------------------------

    def _update(self, **flags: bool) -> None:
        with self._lock:
            for name, value in flags.items():
                setattr(self.gate, name, value)
            if self.gate.ready_to_report():
                self.gate.reported = True
                self._up.set()

    def require_lock(self, required: bool = True) -> None:
        self._update(lock_required=bool(required))

    def note_lock_mapped(self) -> None:
        self._update(lock_mapped=True)

    def note_shell_ready(self) -> None:
        self._update(shell_ready=True)

    # -- worker side ------------------------------------------------------

    def _connect(self) -> bool:
        bindings = self._bind_fn()
        if not bindings:
            return False
        phosh_cls = bindings['PhoshPrivate']
        if self._display_factory is not None:
            display = self._display_factory()
        else:
            from pywayland.client import Display
            display = Display()
        display.connect()
        found: dict[str, int] = {}

        def _on_global(registry, name, interface, version) -> None:
            if interface == 'phosh_private':
                found['name'] = int(name)
                found['version'] = int(version)

        # Held for the connection's lifetime (pywayland resolves new_id
        # args through the live registry proxy).
        self._registry = display.get_registry()
        registry = self._registry
        registry.dispatcher['global'] = _on_global
        if display.roundtrip() < 0:
            raise RuntimeError('wayland roundtrip failed')
        if 'name' not in found:
            _log_once(logging.INFO, 'phosh-absent',
                      'compositor does not offer phosh_private; '
                      'shell state not reported')
            display.disconnect()
            return False
        version = min(found['version'], PHOSH_PRIVATE_MAX_VERSION)
        if version < PHOSH_PRIVATE_MIN_VERSION:
            _log_once(logging.INFO, 'phosh-old',
                      'phosh_private v%s lacks set_shell_state', version)
            display.disconnect()
            return False
        self._phosh = registry.bind(found['name'], phosh_cls, version)
        # A refused bind ("only a single client") arrives as a protocol
        # error: libwayland marks the display dead and roundtrip fails.
        if display.roundtrip() < 0:
            raise RuntimeError('phosh_private bind refused')
        self._display = display
        return True

    def _run(self) -> None:
        try:
            ok = self._connect()
        except Exception as exc:  # noqa: BLE001 - degrade, never raise
            _log_once(logging.WARNING, 'phosh-bind-failed',
                      'phosh_private unavailable: %s', exc)
            ok = False
        self.available = ok
        if not ok:
            if os.environ.get('XX_WM_PHOC_SHELL_MODE') == '1':
                log.error('phoc runs in shell mode (-S) but the shell '
                          'cannot report UP; outputs stay shielded')
            return
        while not self._up.wait(self._watchdog_seconds):
            with self._lock:
                gate = self.gate
                if gate.reported or gate.lock_required:
                    continue
            if self._pin_configured():
                continue
            log.warning('shell never reported ready; lowering the phoc '
                        'shield anyway (no PIN configured)')
            self._update(shell_ready=True)
        try:
            self._phosh.set_shell_state(SHELL_STATE_UP)
            self._display.flush()
            self.sent = True
            log.info('reported shell state UP to phoc')
        except Exception as exc:  # noqa: BLE001
            log.warning('set_shell_state failed: %s', exc)


def pin_configured_on_disk(path: Path | None = None) -> bool:
    """Whether config.json carries a PIN (hash or helper marker). Fails
    closed: an unreadable or corrupt file counts as a PIN."""
    import json
    if path is None:
        # Same location config.ShellConfig uses (HOME, not XDG_CONFIG_HOME).
        path = Path.home() / '.config' / 'xx-wm' / 'config.json'
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True
    return not isinstance(data, dict) or data.get('pin_hash') is not None


_reporter: ShellStateReporter | None = None
_reporter_lock = threading.Lock()


def shell_state() -> ShellStateReporter:
    """Process-wide reporter, started on first use. XX_WM_SHELL_STATE=0
    disables it (the gate still records, nothing is sent)."""
    global _reporter
    with _reporter_lock:
        if _reporter is None:
            enabled = os.environ.get('XX_WM_SHELL_STATE', '1') != '0'
            _reporter = ShellStateReporter(start_thread=enabled)
        return _reporter
