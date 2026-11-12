"""Lane bug_shell session findings: 3, 9, 10, 13 and lower-severity L2/L3/L5.

3: the shell's own launcher must not appear in the drawer (NoDisplay +
    filtered id), and the wrapper must not start a second session.
9: OpenRC must run as the installing user with a runtime dir.
10: the wrapper must not respawn the shell after phoc died (incl. 137 backoff).
13: the persisted charge limit / nft blocks must be re-applied at boot.
L2: empty gesture-generator output must not exec lisgd with an empty binding.
L3: lisgd must not survive logout.
L5: deploy must not stage through a fixed world-writable directory.
"""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent


@pytest.fixture(autouse=True)
def _isolate_modules():
    """Leave no imported shell module behind: sibling files import window
    and friends against their own gi seams (test_folders idiom)."""
    import sys
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        sys.modules.pop(name, None)


DATA = REPO / 'launcher' / 'data'
DESKTOP_IN = DATA / 'io.piercingxx.XXWM.desktop.in'
WRAPPER_IN = DATA / 'xx-wm.in'
OPENRC = DATA / 'openrc' / 'xx-wm'
INSTALL_SH = REPO / 'scripts' / 'install.sh'
DEPLOY_SH = REPO / 'scripts' / 'deploy.sh'


def _function_body(script: str, name: str) -> str:
    start = script.find(name + '() {')
    assert start != -1, f'{name} not defined'
    depth = 0
    for i in range(start, len(script)):
        if script[i] == '{':
            depth += 1
        elif script[i] == '}':
            depth -= 1
            if depth == 0 and script[i - 1] == '\n':
                return script[start:i]
    return ''


class TestOwnLauncherHidden:
    def test_desktop_entry_is_no_display(self) -> None:
        assert 'NoDisplay=true' in DESKTOP_IN.read_text()

    def test_shell_id_filtered_from_drawer(self) -> None:
        import sys
        sys.path.insert(0, str(REPO / 'launcher' / 'src'))
        from app_index import _is_shell_or_launcher
        assert _is_shell_or_launcher('io.piercingxx.XXWM')
        assert _is_shell_or_launcher('io.piercingxx.XXWM.desktop')

    def test_wrapper_bails_when_socket_live(self) -> None:
        assert 'xx-wm.sock' in WRAPPER_IN.read_text()


class TestOpenRCUser:
    def test_installer_records_installing_user(self) -> None:
        script = INSTALL_SH.read_text()
        assert '/etc/conf.d/xx-wm' in script
        assert 'XX_WM_USER' in script

    def test_start_pre_creates_runtime_dir(self) -> None:
        text = OPENRC.read_text()
        assert 'XDG_RUNTIME_DIR' in text


class TestWrapperRespawn:
    def test_respawn_checks_compositor_alive(self) -> None:
        src = WRAPPER_IN.read_text()
        assert 'kill -0' in src

    def test_sigkill_backs_off(self) -> None:
        src = WRAPPER_IN.read_text()
        assert '[ "$_st" -eq 138 ] || [ "$_st" -eq 137 ]' not in src
        assert '138' in src
        assert '137' in src


class TestHelperBootEnabled:
    def test_systemd_enables_helper_boot(self) -> None:
        body = _function_body(INSTALL_SH.read_text(), 'enable_service')
        assert 'xx-wm-helper-boot' in body

    def test_openrc_adds_helper_boot(self) -> None:
        body = _function_body(INSTALL_SH.read_text(), 'enable_service')
        assert 'rc-update add xx-wm-helper-boot boot' in body


class TestLisgdLifecycle:
    def test_empty_bindings_do_not_exec_lisgd(self) -> None:
        src = WRAPPER_IN.read_text()
        block = src.split('while IFS= read -r _binding')[1].split(') &')[0]
        assert '_binding' in block
        assert '-z "$_binding"' in block or 'test -n "$_binding"' in block \
            or '[ -n "$_binding" ]' in block

    def test_session_end_kills_lisgd(self) -> None:
        body = _function_body(WRAPPER_IN.read_text(), '_xx_session_end')
        assert 'lisgd' in body


class TestDeployStaging:
    def test_staging_dir_is_private_temp(self) -> None:
        script = DEPLOY_SH.read_text()
        assert 'mktemp -d' in script
        assert 'STAGE=/tmp/xx-wm-deploy' not in script

    def test_wrapper_syntax_parses(self) -> None:
        result = subprocess.run(
            ['sh', '-n', str(WRAPPER_IN)], capture_output=True, text=True,
            check=False)
        assert result.returncode == 0, result.stderr


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
