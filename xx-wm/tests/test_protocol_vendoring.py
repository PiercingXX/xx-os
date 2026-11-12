"""Tests for runtime-generated Wayland protocol bindings.

The shell no longer vendors generated pywayland modules: the installed
pywayland's own scanner generates them on first use into a version-keyed
cache (see launcher/src/wayland_bindings.py). These tests cover:

- the protocol XML inputs still shipped under launcher/data/ (the scanner's
  source of truth);
- the ``interface="wl_object"`` strip applied to wayland.xml;
- the version-keyed cache path;
- binding loading from BOTH scanner output layouts (single-file modules and
  package-per-protocol), using fake generated trees in tmp_path;
- degrade-on-failure: any failure yields None and never raises.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import wayland_bindings

LAUNCHER_DATA = Path(__file__).parent.parent / 'launcher' / 'data'

WLR_XML = LAUNCHER_DATA / 'wlr-foreign-toplevel-management-unstable-v1.xml'
WLR_SCREENCOPY_XML = LAUNCHER_DATA / 'wlr-screencopy-unstable-v1.xml'
WAYLAND_XML = LAUNCHER_DATA / 'wayland.xml'

# All core Wayland interfaces the wayland.xml scanner input must declare.
CORE_INTERFACES = [
    'wl_display',
    'wl_registry',
    'wl_callback',
    'wl_compositor',
    'wl_shm_pool',
    'wl_shm',
    'wl_buffer',
    'wl_data_offer',
    'wl_data_source',
    'wl_data_device',
    'wl_data_device_manager',
    'wl_shell',
    'wl_shell_surface',
    'wl_surface',
    'wl_seat',
    'wl_pointer',
    'wl_keyboard',
    'wl_touch',
    'wl_output',
    'wl_region',
    'wl_subcompositor',
    'wl_subsurface',
]

NEEDED = ('WlSeat', 'ZwlrForeignToplevelManagerV1', 'ZwlrForeignToplevelHandleV1')


@pytest.fixture
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point XDG_CACHE_HOME at tmp and clear one-shot log state."""
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path))
    wayland_bindings._logged_once.clear()
    return tmp_path


@pytest.fixture
def isolated_imports():
    """Snapshot sys.modules/sys.path so fake-tree imports leave no trace."""
    saved_modules = dict(sys.modules)
    saved_path = list(sys.path)
    # Start as a fresh process would: bindings an earlier test generated and
    # loaded for real (a regular package, which outranks a fake namespace
    # tree on sys.path) are neither imported nor importable here.
    name = wayland_bindings.PROTO_PACKAGE_NAME
    for mod in [n for n in sys.modules if n == name or n.startswith(name + '.')]:
        sys.modules.pop(mod)
    sys.path[:] = [p for p in sys.path if not (Path(p) / name).is_dir()]
    try:
        yield
    finally:
        for name in [n for n in sys.modules if n not in saved_modules]:
            sys.modules.pop(name, None)
        sys.modules.update(saved_modules)
        sys.path[:] = saved_path


def _write_single_file_tree(package_dir: Path) -> None:
    """Fake 0.4.19-style scanner output: one module per protocol."""
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / 'wayland.py').write_text(
        "class WlSeat:\n    name = 'wl_seat'\n", encoding='utf-8',
    )
    (package_dir / 'wlr_foreign_toplevel_management_unstable_v1.py').write_text(
        "class ZwlrForeignToplevelManagerV1:\n"
        "    name = 'zwlr_foreign_toplevel_manager_v1'\n"
        "\n"
        "\n"
        "class ZwlrForeignToplevelHandleV1:\n"
        "    name = 'zwlr_foreign_toplevel_handle_v1'\n",
        encoding='utf-8',
    )


def _write_packaged_tree(package_dir: Path) -> None:
    """Fake 0.4.18-style scanner output: a package per protocol."""
    wayland_pkg = package_dir / 'wayland'
    wlr_pkg = package_dir / 'wlr_foreign_toplevel_management_unstable_v1'
    wayland_pkg.mkdir(parents=True, exist_ok=True)
    wlr_pkg.mkdir(parents=True, exist_ok=True)
    (package_dir / '__init__.py').write_text('', encoding='utf-8')
    (wayland_pkg / '__init__.py').write_text(
        'from .wl_seat import WlSeat\n', encoding='utf-8',
    )
    (wayland_pkg / 'wl_seat.py').write_text(
        "class WlSeat:\n    name = 'wl_seat'\n", encoding='utf-8',
    )
    (wlr_pkg / '__init__.py').write_text(
        'from .manager import ZwlrForeignToplevelManagerV1\n'
        'from .handle import ZwlrForeignToplevelHandleV1\n',
        encoding='utf-8',
    )
    (wlr_pkg / 'manager.py').write_text(
        "class ZwlrForeignToplevelManagerV1:\n"
        "    name = 'zwlr_foreign_toplevel_manager_v1'\n",
        encoding='utf-8',
    )
    (wlr_pkg / 'handle.py').write_text(
        "class ZwlrForeignToplevelHandleV1:\n"
        "    name = 'zwlr_foreign_toplevel_handle_v1'\n",
        encoding='utf-8',
    )


def _interface_names(path: Path) -> set[str]:
    root = ET.parse(path).getroot()
    return {iface.get('name') for iface in root.findall('interface')}


@pytest.mark.parametrize('path', [WLR_XML, WLR_SCREENCOPY_XML, WAYLAND_XML])
def test_xml_file_exists(path: Path) -> None:
    assert path.is_file(), f'protocol XML missing: {path}'


@pytest.mark.parametrize('path', [WLR_XML, WLR_SCREENCOPY_XML, WAYLAND_XML])
def test_xml_is_well_formed(path: Path) -> None:
    # Raises ET.ParseError if the file is not well-formed XML.
    ET.parse(path)


def test_wlr_xml_declares_both_toplevel_interfaces() -> None:
    names = _interface_names(WLR_XML)
    assert 'zwlr_foreign_toplevel_manager_v1' in names
    assert 'zwlr_foreign_toplevel_handle_v1' in names


def test_screencopy_xml_declares_manager_and_frame() -> None:
    names = _interface_names(WLR_SCREENCOPY_XML)
    assert 'zwlr_screencopy_manager_v1' in names
    assert 'zwlr_screencopy_frame_v1' in names


def test_wayland_xml_declares_all_core_interfaces() -> None:
    names = _interface_names(WAYLAND_XML)
    missing = [name for name in CORE_INTERFACES if name not in names]
    assert not missing, f'wayland.xml missing core interfaces: {missing}'


def test_protocol_xmls_resolve_from_checkout() -> None:
    """The scanner inputs resolve relative to the module in a source checkout."""
    assert wayland_bindings.protocol_xml_path('wayland.xml') == WAYLAND_XML
    assert wayland_bindings.protocol_xml_path(
        'wlr-foreign-toplevel-management-unstable-v1.xml') == WLR_XML


def test_strip_wl_object_removes_stale_attribute() -> None:
    raw = WAYLAND_XML.read_text(encoding='utf-8')
    assert 'interface="wl_object"' in raw
    stripped = wayland_bindings.strip_wl_object(raw)
    assert 'interface="wl_object"' not in stripped
    ET.fromstring(stripped)


def test_cache_dir_keyed_by_pywayland_version(isolated_cache: Path) -> None:
    cache_418 = wayland_bindings.cache_dir_for('0.4.18')
    cache_419 = wayland_bindings.cache_dir_for('0.4.19')
    root = isolated_cache / 'xx-wm' / 'wayland_proto'
    assert cache_418.parent == root and cache_418.name.startswith('0.4.18-')
    assert cache_419.name.startswith('0.4.19-')
    assert cache_418 != cache_419


def test_cache_dir_changes_when_protocol_xml_changes(
        isolated_cache: Path, monkeypatch) -> None:
    before = wayland_bindings.cache_dir_for('0.4.18')
    monkeypatch.setattr(wayland_bindings, '_xml_digest', lambda: 'deadbeef0000')
    assert wayland_bindings.cache_dir_for('0.4.18') != before


def test_wlr_xml_declares_every_v3_message_in_upstream_order() -> None:
    # Opcodes come from declaration order: a missing or reordered message
    # makes libwayland reject the compositor's events and drop the link
    # (phoc 0.56 sends `parent`, event 7, at version 3).
    import xml.etree.ElementTree as ET
    root = ET.parse(wayland_bindings.protocol_xml_path(
        wayland_bindings.WLR_XML_NAME)).getroot()
    handle = next(i for i in root.iter('interface')
                  if i.get('name') == 'zwlr_foreign_toplevel_handle_v1')
    events = [e.get('name') for e in handle.iter('event')]
    requests = [r.get('name') for r in handle.iter('request')]
    assert events == ['title', 'app_id', 'output_enter', 'output_leave',
                      'state', 'done', 'closed', 'parent']
    assert requests == ['set_maximized', 'unset_maximized', 'set_minimized',
                        'unset_minimized', 'activate', 'close', 'set_rectangle',
                        'destroy', 'set_fullscreen', 'unset_fullscreen']
    assert handle.get('version') == '3'


def test_package_dir_lives_under_versioned_cache(isolated_cache: Path) -> None:
    package_dir = wayland_bindings.package_dir_for('0.4.18')
    assert package_dir.parent == wayland_bindings.cache_dir_for('0.4.18')
    assert package_dir.name == wayland_bindings.PROTO_PACKAGE_NAME


def test_load_single_file_layout(tmp_path: Path, isolated_imports) -> None:
    package_dir = tmp_path / wayland_bindings.PROTO_PACKAGE_NAME
    _write_single_file_tree(package_dir)
    classes = wayland_bindings.load_classes_from(package_dir)
    assert classes is not None
    assert [c.__name__ for c in
            (classes['WlSeat'], classes['ZwlrForeignToplevelManagerV1'],
             classes['ZwlrForeignToplevelHandleV1'])] == list(NEEDED)


def test_load_packaged_layout(tmp_path: Path, isolated_imports) -> None:
    package_dir = tmp_path / wayland_bindings.PROTO_PACKAGE_NAME
    _write_packaged_tree(package_dir)
    classes = wayland_bindings.load_classes_from(package_dir)
    assert classes is not None
    assert [c.__name__ for c in
            (classes['WlSeat'], classes['ZwlrForeignToplevelManagerV1'],
             classes['ZwlrForeignToplevelHandleV1'])] == list(NEEDED)


def test_load_tolerates_broken_sibling_module(tmp_path: Path, isolated_imports) -> None:
    package_dir = tmp_path / wayland_bindings.PROTO_PACKAGE_NAME
    _write_single_file_tree(package_dir)
    (package_dir / 'aaa_broken.py').write_text(
        'raise RuntimeError("boom")\n', encoding='utf-8',
    )
    classes = wayland_bindings.load_classes_from(package_dir)
    assert classes is not None
    assert set(classes) == set(NEEDED)


def test_load_missing_dir_returns_none(tmp_path: Path, isolated_imports) -> None:
    assert wayland_bindings.load_classes_from(tmp_path / 'no-such-dir') is None


def test_load_incomplete_tree_returns_none(tmp_path: Path, isolated_imports) -> None:
    package_dir = tmp_path / wayland_bindings.PROTO_PACKAGE_NAME
    package_dir.mkdir(parents=True)
    (package_dir / 'wayland.py').write_text(
        "class WlSeat:\n    name = 'wl_seat'\n", encoding='utf-8',
    )
    assert wayland_bindings.load_classes_from(package_dir) is None


def test_screencopy_resolves_against_installed_tree() -> None:
    """The screencopy XML ships in data/ and declares the manager + frame
    interfaces the generated bindings (and the old vendored module)
    exposed."""
    import xml.etree.ElementTree as ET

    path = wayland_bindings.protocol_xml_path(
        wayland_bindings.SCREENCAST_XML_NAME)
    assert path is not None
    names = {i.get('name') for i in ET.parse(str(path)).getroot().iter('interface')}
    assert 'zwlr_screencopy_manager_v1' in names
    assert 'zwlr_screencopy_frame_v1' in names


def test_ensure_screencopy_loads_from_generated_tree(
    tmp_path: Path, isolated_imports, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ensure_screencopy_bindings goes through the same cache/generate/load
    path as the toplevel bindings, keyed on the screencopy XML names."""
    package_dir = tmp_path / wayland_bindings.PROTO_PACKAGE_NAME
    package_dir.mkdir(parents=True)
    for filename, class_names in (
        ('wayland.py', ('WlOutput', 'WlShm')),
        ('wlr_screencopy_unstable_v1.py',
         ('ZwlrScreencopyManagerV1', 'ZwlrScreencopyFrameV1')),
    ):
        body = ''.join(f"class {name}:\n    name = '{name}'\n"
                        for name in class_names)
        (package_dir / filename).write_text(body, encoding='utf-8')
    monkeypatch.setattr(wayland_bindings, 'installed_pywayland_version',
                        lambda: '0.4.18-test')
    monkeypatch.setattr(wayland_bindings, 'package_dir_for',
                        lambda *args: package_dir)
    found = wayland_bindings.ensure_screencopy_bindings()
    assert found is not None
    assert [c.__name__ for c in
            (found['WlOutput'], found['WlShm'],
             found['ZwlrScreencopyManagerV1'],
             found['ZwlrScreencopyFrameV1'])] == list(
                 wayland_bindings.SCREENCAST_CLASS_NAMES)


def test_ensure_degrades_without_pywayland(
    isolated_cache: Path, isolated_imports, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, 'pywayland', None)
    assert wayland_bindings.ensure_bindings() is None


def test_ensure_degrades_when_xmls_missing(
    isolated_cache: Path, isolated_imports, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wayland_bindings, 'installed_pywayland_version',
                        lambda: '0.4.18-test')
    monkeypatch.setattr(wayland_bindings, 'protocol_xml_path', lambda _name: None)
    assert wayland_bindings.ensure_bindings() is None


def test_ensure_degrades_when_scanner_fails(
    isolated_cache: Path, isolated_imports, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(wayland_bindings, 'installed_pywayland_version',
                        lambda: '0.4.18-test')

    def _fail(*args, **kwargs):
        raise RuntimeError('no scanner here')

    monkeypatch.setattr(wayland_bindings.subprocess, 'run', _fail)
    with caplog.at_level('INFO', logger='wayland_bindings'):
        assert wayland_bindings.ensure_bindings() is None
        assert wayland_bindings.ensure_bindings() is None
    failures = [r for r in caplog.records
                if 'failed to generate wayland protocol bindings' in r.message]
    assert len(failures) == 1


def test_generate_raises_on_scanner_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess as stdlib_subprocess

    def _fail(*args, **kwargs):
        return stdlib_subprocess.CompletedProcess(
            args=args, returncode=1, stdout='', stderr='scanner boom',
        )

    monkeypatch.setattr(wayland_bindings.subprocess, 'run', _fail)
    with pytest.raises(RuntimeError, match='scanner boom'):
        wayland_bindings.generate_bindings(
            tmp_path / 'pkg', WAYLAND_XML, WLR_XML)


def test_ensure_generates_then_loads(
    isolated_cache: Path, isolated_imports, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full pipeline with a stubbed scanner: miss -> generate -> usable load."""
    monkeypatch.setattr(wayland_bindings, 'installed_pywayland_version',
                        lambda: '0.4.18-test')

    def _fake_generate(package_dir: Path, _wayland_xml: Path, _wlr_xml: Path) -> None:
        _write_single_file_tree(Path(package_dir))

    monkeypatch.setattr(wayland_bindings, 'generate_bindings', _fake_generate)
    classes = wayland_bindings.ensure_bindings()
    assert classes is not None
    assert set(classes) == set(NEEDED)
    assert wayland_bindings.package_dir_for('0.4.18-test').is_dir()
    # Second call hits the warm cache without regenerating.
    monkeypatch.setattr(
        wayland_bindings, 'generate_bindings',
        lambda *args: (_ for _ in ()).throw(AssertionError('must not regenerate')),
    )
    assert wayland_bindings.ensure_bindings() is not None


def test_ensure_degrades_when_generated_tree_unusable(
    isolated_cache: Path, isolated_imports, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wayland_bindings, 'installed_pywayland_version',
                        lambda: '0.4.18-test')
    monkeypatch.setattr(wayland_bindings, 'generate_bindings',
                        lambda package_dir, *_args: Path(package_dir).mkdir(
                            parents=True, exist_ok=True))
    assert wayland_bindings.ensure_bindings() is None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
