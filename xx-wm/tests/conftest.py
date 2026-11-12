"""Shared test setup: launcher sources importable, HOME never the real one
for tests that opt into the isolated_home fixture."""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Nothing a test runs may write into the developer's real ~/.cache (the
# Wayland bindings cache used to land in ~/.cache/xx-wm/wayland_proto).
_TEST_CACHE = tempfile.mkdtemp(prefix='xx-wm-test-cache-')
os.environ['XDG_CACHE_HOME'] = _TEST_CACHE
atexit.register(shutil.rmtree, _TEST_CACHE, True)
# Nor the host's /sys/class backlight and LEDs (a workstation's
# input*::scrolllock): brightness and torch code see an empty tree; a test
# that needs a panel builds one under its own root.
os.environ['XX_WM_SYSFS_CLASS'] = os.path.join(_TEST_CACHE, 'no-sysfs')
# Nor the host's services: no polkit agent on the real system bus, and no
# portal requests from GTK or libadwaita, which would activate the host's
# xdg-desktop-portal (and its compositor backend) even on a private bus.
# Children (dbus-run-session, Broadway runs) inherit all of it.
os.environ['XX_WM_NO_POLKIT_AGENT'] = '1'
os.environ['ADW_DISABLE_PORTAL'] = '1'
os.environ['GTK_USE_PORTAL'] = '0'
if 'no-portals' not in os.environ.get('GDK_DEBUG', '').split(','):
    os.environ['GDK_DEBUG'] = ','.join(
        f for f in (os.environ.get('GDK_DEBUG', ''), 'no-portals') if f)

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))
# The shell themes through the shared design system: common/xx_ui must be
# importable too (installed as xx_ui on the phone; repo path in tests).
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'common'))

import pytest

try:
    import gi

    try:
        gi.require_version('Gtk', '4.0')
        from gi.repository import GLib, Gtk  # noqa: F401
    except (ImportError, ValueError):
        pass
finally:
    _REAL_GI_MODULES = {
        name: mod for name, mod in sys.modules.items()
        if name == 'gi' or name.startswith('gi.')
    }


@pytest.fixture(scope='session', autouse=True)
def _real_gi_modules():
    """Undo collection-time gi stubs before the first test runs.

    Some test modules install stub ``gi`` packages at import time (i.e.
    during collection, before any test runs) and never restore them.
    PyGObject resolves override methods such as
    ``Gtk.CssProvider.load_from_data`` through the live
    ``sys.modules['gi.repository.*']`` entries, so once a stub shadows the
    namespace there, previously imported real-GTK code (hud.py) crashes
    mid-suite even though it imported cleanly. Re-install the captured
    real modules once up front; fakes installed later by fixtures or test
    bodies (which manage their own save/restore) are left untouched.
    """
    saved = {
        name: sys.modules.pop(name) for name in list(sys.modules)
        if name == 'gi' or name.startswith('gi.')
    }
    sys.modules.update(_REAL_GI_MODULES)
    yield
    for name in list(sys.modules):
        if name == 'gi' or name.startswith('gi.'):
            del sys.modules[name]
    sys.modules.update(saved)


@pytest.fixture(autouse=True)
def _no_host_tailscale(monkeypatch):
    """Tests never see the host's Tailscale: the shade tile and anything
    that probes the CLI behave as on a machine without it. test_tailscale
    puts the real probe back for its fake-CLI tests."""
    import tailscale
    monkeypatch.setattr(tailscale, 'available', lambda: False)


@pytest.fixture(autouse=True)
def _config_saves_write_at_once():
    """The running shell coalesces config saves (main.do_activate turns
    that on); a test that activated the app must not leave later tests,
    which read config.json right after a setter, with deferred saves."""
    yield
    config = sys.modules.get('config')
    if config is not None and hasattr(config, 'enable_deferred_saves'):
        config.flush_saves()
        config.enable_deferred_saves(False)


@pytest.fixture(autouse=True)
def _not_in_a_session(monkeypatch):
    """Tests never run as a live XX-WM session: desktop_theme (gsettings,
    ~/.config/gtk-*/gtk.css) stays off the developer's real desktop."""
    monkeypatch.delenv('XX_WM_SESSION', raising=False)
