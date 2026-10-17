"""The shell starts with the portal opted out (a stale portal cost 90 s of
black screen at session start on the FLX1) but apps it launches keep the
portal, which carries the dark color-scheme."""
import ast
import os
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parent.parent / 'launcher'


def _fn(name):
    tree = ast.parse((LAUNCHER / 'src' / 'main.py').read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == name)
    ns = {'os': os}
    exec(compile(ast.Module([fn], []), 'main.py', 'exec'), ns)
    return ns[name]


def test_opt_out_dropped_for_children():
    env = {'ADW_DISABLE_PORTAL': '1', 'GDK_DEBUG': 'no-portals', 'HOME': '/h'}
    _fn('drop_portal_opt_out')(env)
    assert env == {'HOME': '/h'}


def test_other_gdk_debug_flags_survive():
    env = {'GDK_DEBUG': 'gl-disable,no-portals'}
    _fn('drop_portal_opt_out')(env)
    assert env == {'GDK_DEBUG': 'gl-disable'}


def test_dropped_at_activation():
    tree = ast.parse((LAUNCHER / 'src' / 'main.py').read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
               and n.name == 'XXWMApplication')
    activate = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                    and n.name == 'do_activate')
    first = activate.body[0]
    assert isinstance(first, ast.Expr)
    assert first.value.func.id == 'drop_portal_opt_out'


def test_wrapper_opts_shell_out_and_restarts_portals():
    text = (LAUNCHER / 'data' / 'xx-wm.in').read_text()
    assert 'ADW_DISABLE_PORTAL=1 GDK_DEBUG=' in text
    assert 'no-portals' in text
    assert 'try-restart' in text and 'xdg-desktop-portal.service' in text
