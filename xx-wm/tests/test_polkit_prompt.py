"""The password dialog does not repeat PAM's generic "Password:" prompt
under a field that already says Device password (seen on the FLX1)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))



def _load_meaningful_prompt():
    """The pure helper from polkit_agent.py without importing the module
    (it registers a GObject Listener subclass; re-importing segfaults)."""
    import ast
    import re
    src = (Path(__file__).parent.parent / 'launcher' / 'src'
           / 'polkit_agent.py').read_text()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)
              and n.name == 'meaningful_prompt')
    ns = {'re': re}
    exec(compile(ast.Module([fn], []), 'polkit_agent.py', 'exec'), ns)
    return ns['meaningful_prompt']


meaningful_prompt = _load_meaningful_prompt()


def test_generic_password_prompts_are_hidden():
    for text in ('Password:', 'password', ' Password: ', '[sudo] password for furios:',
                 'Password for furios:', None, ''):
        assert meaningful_prompt(text) == ''


def test_other_prompts_are_kept():
    assert meaningful_prompt('Verification code:') == 'Verification code:'
    assert meaningful_prompt('Place your finger on the reader') == \
        'Place your finger on the reader'


def test_automatic_wifi_listing_never_rescans():
    # Opening Settings ran a bare `nmcli dev wifi list`, which rescans and
    # asked for the device password (seen on the FLX1)
    import system_settings
    calls = []

    class Done:
        returncode, stdout = 0, ''

    def run(args, timeout=0):
        calls.append(args)
        return Done()

    system_settings.list_wifi(run)
    system_settings.list_wifi(run, rescan=True)
    assert calls[0][-2:] == ['--rescan', 'no']
    assert calls[1][-2:] == ['--rescan', 'yes']
