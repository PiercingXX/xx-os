"""Static guard against GTK3-isms and invented API (each one below crashed a
real session while the headless suite stayed green): every signal the shell
connects to must exist on some GTK4/Gdk/Adw/Gio type, and a few known
removed/never-existing methods must not be called."""
import re
import warnings
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / 'launcher' / 'src'

_CONNECT_RE = re.compile(r"""\.connect\(\s*['"]([a-z0-9_-]+)(?:::[a-z0-9_-]+)?['"]""")

# Methods that do not exist on the GTK4/PyGObject versions we target.
_FORBIDDEN = {
    r'dlg\.get_filename\(|dialog\.get_filename\(': 'GTK4 file choosers return Gio.File (get_file)',
    r'\bdialog\.run\(': 'GTK4 dialogs have no run()',
    r'Stack\(.*\)\.set_homogeneous|stack\.set_homogeneous\(': 'Gtk.Stack has no set_homogeneous',
    r'GLib\.unix_fd_add\(': 'PyGObject has no GLib.unix_fd_add',
    r"'size-allocate'": 'GTK4 has no size-allocate signal',
}


def _gi_signal_names() -> set[str]:
    gi = pytest.importorskip('gi')
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import Adw, Gdk, Gio, GObject, Gtk
    modules = [Gtk, Gdk, Adw, Gio]
    try:
        gi.require_version('PolkitAgent', '1.0')
        from gi.repository import PolkitAgent
        modules.append(PolkitAgent)
    except (ImportError, ValueError):
        pass
    try:
        gi.require_version('Gtk4SessionLock', '1.0')
        from gi.repository import Gtk4SessionLock
        modules.append(Gtk4SessionLock)
    except (ImportError, ValueError):
        pass
    names: set[str] = set()
    warnings.simplefilter('ignore')
    for module in modules:
        for attr in dir(module):
            obj = getattr(module, attr, None)
            gtype = getattr(obj, '__gtype__', None)
            if gtype is None:
                continue
            try:
                # Signals register at class init; force it for lazy classes.
                GObject.type_class_ref(gtype)
                names.update(GObject.signal_list_names(gtype))
            except Exception:
                continue
    return names | {'notify'}


def test_every_connected_signal_exists_in_gtk4():
    known = _gi_signal_names()
    unknown = []
    for path in sorted(SRC.glob('*.py')):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            for name in _CONNECT_RE.findall(line):
                if name not in known:
                    unknown.append(f'{path.name}:{lineno}: {name}')
    assert unknown == []


@pytest.mark.parametrize('pattern,why', sorted(_FORBIDDEN.items()))
def test_no_removed_or_invented_api(pattern, why):
    hits = [f'{p.name}:{n}' for p in sorted(SRC.glob('*.py'))
            for n, line in enumerate(p.read_text().splitlines(), 1)
            if re.search(pattern, line) and not line.lstrip().startswith('#')]
    assert hits == [], why
