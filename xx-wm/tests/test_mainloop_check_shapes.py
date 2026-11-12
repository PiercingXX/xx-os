"""scripts/check_mainloop_blocks.py sees every blocking call shape."""
import importlib.util
import textwrap
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parent.parent / 'scripts'
          / 'check_mainloop_blocks.py')


@pytest.fixture(scope='module')
def checker():
    spec = importlib.util.spec_from_file_location('check_mainloop_blocks',
                                                  SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _kinds(checker, tmp_path, source):
    path = tmp_path / 'sample.py'
    path.write_text(textwrap.dedent(source), encoding='utf-8')
    return {(scope, kind) for _line, kind, scope, _text
            in checker._iter_sites(path)}


@pytest.mark.parametrize('body, kind', [
    ('subprocess.run(["true"], check=False)', 'subprocess.run'),
    ('subprocess.call(["true"])', 'subprocess.call'),
    ('subprocess.check_call(["true"])', 'subprocess.check_call'),
    ('subprocess.check_output(["true"])', 'check_output'),
    ('Gio.bus_get_sync(Gio.BusType.SYSTEM, None)', 'bus_get_sync'),
    ('bus.call_sync(*args)', 'call_sync'),
    ('time.sleep(1)', 'time.sleep'),
    ('urllib.request.urlopen(url, timeout=5)', 'urlopen'),
    ('sock.recv(4096)', 'socket.recv'),
    ('proc.communicate()', 'communicate'),
    ('proc.wait()', 'Popen.wait'),
])
def test_direct_shapes(checker, tmp_path, body, kind):
    found = _kinds(checker, tmp_path, f'''
        def handler(url, sock, bus, proc, args):
            {body}
    ''')
    assert found == {('handler', kind)}


def test_aliased_runner_is_flagged(checker, tmp_path):
    found = _kinds(checker, tmp_path, '''
        import subprocess

        def check(run=None):
            runner = run or subprocess.run
            return runner(['fc-cache', '-f'], check=False)
    ''')
    assert ('check', 'subprocess runner runner') in found


def test_runner_default_parameter_is_flagged(checker, tmp_path):
    found = _kinds(checker, tmp_path, '''
        import subprocess

        def probe(run=subprocess.run):
            return run(['ip', 'addr'], check=False)
    ''')
    assert found == {('probe', 'subprocess runner run')}


def test_imported_sleep_and_urlopen_are_flagged(checker, tmp_path):
    found = _kinds(checker, tmp_path, '''
        from time import sleep
        from urllib.request import urlopen

        def tick():
            sleep(0.1)
            urlopen('https://example.invalid')
    ''')
    assert found == {('tick', 'time.sleep'), ('tick', 'urlopen')}


def test_async_and_fire_and_forget_pass(checker, tmp_path):
    found = _kinds(checker, tmp_path, '''
        import subprocess
        import threading

        def fine(bus, event):
            subprocess.Popen(['notify-send', 'x'])
            bus.call('a', 'b', 'c', 'd', None, None, 0, -1, None, None)
            Gio.bus_get(Gio.BusType.SYSTEM, None, None)
            event.wait(1)
            threading.Event().wait(0)
    ''')
    assert found == set()
