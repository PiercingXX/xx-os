"""Installer/packaging fix-ups from the first real postmarketOS install (W3).

pmOS v26.06 Phosh on generic-x86_64 (VM) showed: a meson build-output
collision shipping the OpenRC script as /usr/bin/xx-wm; a forced
gtk4-layer-shell compile-time dep; a wrong apk name (py3-pywayland);
init detection via the package manager on a systemd pmOS; no greetd
support; no unattended mode; squeekboard layouts only linked on one path;
wrong apk font names with no fallback; and no VM phoc fragment.

Shell functions are exercised by extracting them from install.sh into a
temp file and sourcing that file in `sh -c` with stubbed commands on
PATH — the whole script is never executed, so no TUI, network, or
privilege escalation can trigger.
"""
import configparser
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
INSTALL_SH = REPO / 'scripts' / 'install.sh'
MESON_BUILD = REPO / 'launcher' / 'meson.build'
PHOC_DIR = REPO / 'launcher' / 'data' / 'phoc'


@pytest.fixture(scope='module')
def install_script():
    assert INSTALL_SH.is_file(), f'missing {INSTALL_SH}'
    return INSTALL_SH.read_text()


@pytest.fixture(scope='module')
def meson_text():
    assert MESON_BUILD.is_file(), f'missing {MESON_BUILD}'
    return MESON_BUILD.read_text()


def _function_body(script, name):
    m = re.search(rf'^{name}\(\) \{{(.*?)^\}}', script, re.M | re.S)
    return m.group(1) if m else ''


def _extract(names, dest):
    """Copy named shell functions from install.sh into dest for sourcing."""
    script = INSTALL_SH.read_text()
    chunks = []
    for name in names:
        m = re.search(rf'^{name}\(\) \{{(.*?)^\}}', script, re.M | re.S)
        assert m, f'shell function {name} not found in install.sh'
        chunks.append('%s() {%s}\n' % (name, m.group(1)))
    dest.write_text(''.join(chunks))


def _run_funcs(names, driver, tmp_path, env=None):
    """Source extracted functions in `sh -c` and run driver; return CompletedProcess."""
    funcs = tmp_path / 'funcs.sh'
    _extract(names, funcs)
    full_env = dict(os.environ)
    full_env.update(env or {})
    return subprocess.run(
        ['sh', '-c', '. "%s"; %s' % (funcs, driver)],
        capture_output=True, text=True, env=full_env, cwd=str(tmp_path))


# --- 1. meson build-output collision ------------------------------------------

def test_configure_file_outputs_unique(meson_text):
    outputs = re.findall(r"output:\s*'([^']+)'", meson_text)
    assert outputs, 'no configure_file outputs found'
    assert len(outputs) == len(set(outputs)), (
        f'duplicate configure_file outputs: {sorted(outputs)}')
    assert outputs.count('xx-wm') == 1


def test_openrc_build_output_distinct_and_renamed(meson_text):
    assert "output: 'xx-wm.openrc'" in meson_text
    assert "rename: 'xx-wm'" in meson_text
    assert "'init.d'" in meson_text


@pytest.mark.skipif(
    not (shutil.which('meson') and shutil.which('ninja')),
    reason='needs meson and ninja',
)
def test_staged_install_wrapper_and_openrc(tmp_path):
    build_dir = tmp_path / 'build'
    destdir = tmp_path / 'destdir'
    # launcher/meson.build requires importable xx_ui (the shell themes
    # through the installed package); point setup at the repo copy.
    common = str(REPO.parent / 'common')
    setup_env = dict(os.environ)
    setup_env['PYTHONPATH'] = common + (
        os.pathsep + setup_env['PYTHONPATH'] if setup_env.get('PYTHONPATH') else '')
    subprocess.run(
        ['meson', 'setup', str(build_dir), str(REPO / 'launcher'), '--prefix=/usr'],
        check=True, capture_output=True, text=True, env=setup_env)
    env = dict(os.environ, DESTDIR=str(destdir))
    subprocess.run(
        ['meson', 'install', '-C', str(build_dir)],
        check=True, capture_output=True, text=True, env=env)
    wrapper = destdir / 'usr' / 'bin' / 'xx-wm'
    init = destdir / 'etc' / 'init.d' / 'xx-wm'
    wrapper_text = wrapper.read_text()
    assert wrapper_text.startswith('#!/bin/sh'), wrapper_text.splitlines()[0]
    assert 'main.py' in wrapper_text
    init_text = init.read_text()
    assert init_text.startswith('#!/sbin/openrc-run'), init_text.splitlines()[0]
    assert (destdir / 'usr' / 'share' / 'xx-wm' / 'phoc' / 'qemu.ini').is_file()


# --- 2. gtk4-layer-shell: installer check, not meson dep ----------------------

def test_no_gtk_layer_shell_meson_dep(meson_text):
    assert not re.search(
        r"dependency\(\s*['\"]gtk4-layer-shell", meson_text)


def test_installer_checks_layer_shell_typelib(install_script):
    body = _function_body(install_script, 'check_layer_shell')
    assert body, 'check_layer_shell not defined'
    assert "gi.require_version('Gtk4LayerShell', '1.0')" in body
    assert 'check_layer_shell' in _function_body(install_script, 'build_install')


# --- 3. package names ----------------------------------------------------------

def test_apk_pywayland_name(install_script):
    body = _function_body(install_script, 'install_deps')
    assert 'py3-wayland' in body
    assert 'py3-pywayland' not in install_script


def test_valid_apk_names_pinned(install_script):
    body = _function_body(install_script, 'install_deps')
    # squeekboard is intentionally gone: xx-keyboard is installed instead
    # (install_xx_keyboard) and squeekboard stays a fallback only.
    for pkg in ('gtk4.0', 'libadwaita', 'gtk4-layer-shell', 'lisgd',
                'brightnessctl'):
        assert pkg in body, f'apk deps miss {pkg}'


# --- 4. init detection: runtime marker, never the package manager -------------

def test_init_detect_uses_run_systemd(install_script):
    body = _function_body(install_script, 'is_systemd')
    assert body, 'is_systemd not defined'
    assert '/run/systemd/system' in body
    enable_body = _function_body(install_script, 'enable_service')
    assert 'is_systemd' in enable_body
    assert '$PKG' not in enable_body
    assert 'ps -p 1' not in install_script


def test_openrc_needs_binary_and_no_systemd(install_script):
    body = _function_body(install_script, 'has_openrc')
    assert body, 'has_openrc not defined'
    assert '/sbin/openrc-run' in body
    assert 'is_systemd' in body
    enable_body = _function_body(install_script, 'enable_service')
    assert 'has_openrc' in enable_body
    assert 'rc-update add xx-wm default' in enable_body


def test_is_systemd_probes_live_marker(tmp_path):
    missing = tmp_path / 'no-systemd-here'
    proc = _run_funcs(['is_systemd'], 'is_systemd',
                      tmp_path, {'XX_WM_SYSTEMD_RUN_DIR': str(missing)})
    assert proc.returncode != 0
    present = tmp_path / 'systemd'
    present.mkdir()
    proc = _run_funcs(['is_systemd'], 'is_systemd',
                      tmp_path, {'XX_WM_SYSTEMD_RUN_DIR': str(present)})
    assert proc.returncode == 0


def _write_stub(bindir, name, body):
    path = bindir / name
    path.write_text('#!/bin/sh\n' + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def test_enable_service_openrc_branch(tmp_path):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    log = tmp_path / 'calls.log'
    _write_stub(bindir, 'openrc-run', 'exit 0\n')
    _write_stub(bindir, 'rc-update',
                'echo "rc-update $*" >> "%s"\nexit 0\n' % log)
    env = {'PATH': '%s:%s' % (bindir, os.environ['PATH']),
           'SUDO': '',
           'XX_WM_SYSTEMD_RUN_DIR': str(tmp_path / 'no-systemd-here')}
    proc = _run_funcs(['is_systemd', 'has_openrc', 'enable_service'],
                      'enable_service', tmp_path, env)
    assert proc.returncode == 0
    assert 'rc-update add xx-wm default' in log.read_text()


def test_enable_service_systemd_branch(tmp_path):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    log = tmp_path / 'calls.log'
    _write_stub(bindir, 'systemctl',
                'echo "systemctl $*" >> "%s"\nexit 0\n' % log)
    rundir = tmp_path / 'systemd'
    rundir.mkdir()
    env = {'PATH': '%s:%s' % (bindir, os.environ['PATH']),
           'SUDO': '',
           'XX_WM_SYSTEMD_RUN_DIR': str(rundir)}
    proc = _run_funcs(['is_systemd', 'has_openrc', 'enable_service'],
                      'enable_service', tmp_path, env)
    assert proc.returncode == 0
    assert 'rc-update' not in log.read_text()
    assert 'enable xx-wm' in log.read_text()


def test_has_openrc_false_under_systemd(tmp_path):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    _write_stub(bindir, 'openrc-run', 'exit 0\n')
    rundir = tmp_path / 'systemd'
    rundir.mkdir()
    env = {'PATH': '%s:%s' % (bindir, os.environ['PATH']),
           'XX_WM_SYSTEMD_RUN_DIR': str(rundir)}
    proc = _run_funcs(['is_systemd', 'has_openrc'], 'has_openrc', tmp_path, env)
    assert proc.returncode != 0


def test_enable_service_neither_init(tmp_path):
    env = {'SUDO': '',
           'XX_WM_SYSTEMD_RUN_DIR': str(tmp_path / 'no-systemd-here')}
    proc = _run_funcs(['is_systemd', 'has_openrc', 'enable_service'],
                      'enable_service', tmp_path, env)
    assert proc.returncode == 0
    assert 'no supported init' in proc.stderr


# --- 5. greetd / phrog ----------------------------------------------------------

def test_greeter_session_menu_instruction(install_script):
    body = _function_body(install_script, 'setup_greeter')
    assert body, 'setup_greeter not defined'
    assert 'Session menu' in body
    assert 'greetd' in body
    assert 'setup_greeter' in _function_body(install_script, 'build_install')


def test_autologin_opt_in_only(install_script):
    greeter_body = _function_body(install_script, 'setup_greeter')
    assert 'setup_greetd_autologin' in greeter_body
    assert 'AUTOLOGIN' in greeter_body
    assert 'whiptail' in greeter_body
    total = install_script.count('setup_greetd_autologin')
    in_greeter = greeter_body.count('setup_greetd_autologin')
    assert total == in_greeter + 1, \
        'setup_greetd_autologin must only be called from setup_greeter'


def test_autologin_reads_live_service_config(install_script):
    body = _function_body(install_script, 'greetd_config_path')
    assert body, 'greetd_config_path not defined'
    assert 'systemctl cat greetd' in body
    assert '-c' in body
    assert '/etc/phrog/greetd-config.toml' in body


def test_autologin_backs_up_first(install_script):
    body = _function_body(install_script, 'setup_greetd_autologin')
    assert body, 'setup_greetd_autologin not defined'
    assert '[initial_session]' in body
    assert 'command = ' in body
    assert 'user = ' in body
    assert 'xx-wm-backup' in body
    assert 'xx-wm-session' in body


def test_greetd_config_from_service_flag(tmp_path):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    want = tmp_path / 'greetd-config.toml'
    _write_stub(bindir, 'systemctl',
                'printf "[Service]\\nExecStart=/usr/bin/greetd -c %s\\n"\nexit 0\n' % want)
    env = {'PATH': '%s:%s' % (bindir, os.environ['PATH'])}
    proc = _run_funcs(['greetd_config_path'], 'greetd_config_path', tmp_path, env)
    assert proc.returncode == 0
    assert proc.stdout.strip() == str(want)


def test_greetd_config_from_long_flag(tmp_path):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    want = tmp_path / 'custom.toml'
    _write_stub(bindir, 'systemctl',
                'printf "[Service]\\nExecStart=/usr/bin/greetd --config=%s\\n"\nexit 0\n' % want)
    env = {'PATH': '%s:%s' % (bindir, os.environ['PATH'])}
    proc = _run_funcs(['greetd_config_path'], 'greetd_config_path', tmp_path, env)
    assert proc.returncode == 0
    assert proc.stdout.strip() == str(want)


def test_autologin_writes_block_once(tmp_path):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    cfg = tmp_path / 'greetd-config.toml'
    cfg.write_text('[terminal]\nvt = 1\n')
    _write_stub(bindir, 'systemctl',
                'printf "[Service]\\nExecStart=/usr/bin/greetd -c %s\\n"\nexit 0\n' % cfg)
    env = {'PATH': '%s:%s' % (bindir, os.environ['PATH']), 'SUDO': ''}
    names = ['greetd_config_path', 'setup_greetd_autologin']
    first = _run_funcs(names, 'setup_greetd_autologin', tmp_path, env)
    assert first.returncode == 0
    assert (tmp_path / 'greetd-config.toml.xx-wm-backup').read_text() == \
        '[terminal]\nvt = 1\n'
    text = cfg.read_text()
    assert '[initial_session]' in text
    assert 'command = "/usr/libexec/xx-wm-session"' in text
    me = subprocess.run(
        ['id', '-un'], capture_output=True, text=True).stdout.strip()
    assert ('user = "%s"' % me) in text
    second = _run_funcs(names, 'setup_greetd_autologin', tmp_path, env)
    assert second.returncode == 0
    assert 'already has [initial_session]' in second.stdout
    assert cfg.read_text().count('[initial_session]') == 1


# --- 6. unattended mode ---------------------------------------------------------

def test_parse_args_defaults(tmp_path):
    proc = _run_funcs(['parse_args'],
                      'parse_args; printf "%s|%s|%s|%s" '
                      '"$UNATTENDED" "$DEVICE_ARG" "$AUTOLOGIN" "$NO_FONTS"',
                      tmp_path)
    assert proc.returncode == 0
    assert proc.stdout == '|||'


def test_parse_args_all_flags(tmp_path):
    proc = _run_funcs(['parse_args'],
                      'parse_args --unattended --device qemu --autologin --no-fonts; '
                      'printf "%s|%s|%s|%s" '
                      '"$UNATTENDED" "$DEVICE_ARG" "$AUTOLOGIN" "$NO_FONTS"',
                      tmp_path)
    assert proc.returncode == 0
    assert proc.stdout == '1|qemu|1|1'


def test_parse_args_device_equals_form(tmp_path):
    proc = _run_funcs(['parse_args'],
                      'parse_args --unattended --device=tablet; '
                      'printf "%s|%s" "$UNATTENDED" "$DEVICE_ARG"',
                      tmp_path)
    assert proc.returncode == 0
    assert proc.stdout == '1|tablet'


def test_parse_args_rejects_unknown_and_bare_device(tmp_path):
    proc = _run_funcs(['parse_args'], 'parse_args --bogus', tmp_path)
    assert proc.returncode != 0
    proc = _run_funcs(['parse_args'], 'parse_args --device', tmp_path)
    assert proc.returncode != 0


def test_unattended_dispatch_present(install_script):
    assert 'do_unattended' in install_script
    assert 'if [ "$UNATTENDED" = 1 ]; then' in install_script
    body = _function_body(install_script, 'do_unattended')
    assert 'install summary' in body
    assert 'whiptail' not in body
    assert 'choice=$(menu)' not in body


def _unattended_harness(tmp_path, autologin=True):
    """Fake repo + stub commands for a full do_unattended run."""
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    log = tmp_path / 'calls.log'
    sentinel = tmp_path / 'whiptail-called'
    greetd_cfg = tmp_path / 'greetd-config.toml'
    greetd_cfg.write_text('[terminal]\nvt = 1\n')
    _write_stub(bindir, 'apk', 'echo "apk $*" >> "%s"\nexit 0\n' % log)
    _write_stub(bindir, 'meson',
                'echo "meson $*" >> "%s"\n'
                'if [ "$1" = setup ]; then mkdir -p build; fi\nexit 0\n' % log)
    _write_stub(bindir, 'whiptail',
                'echo CALLED >> "%s"\nexit 1\n' % sentinel)
    _write_stub(bindir, 'systemctl',
                'echo "systemctl $*" >> "%s"\n'
                'if [ "$1" = cat ]; then '
                'printf "[Service]\\nExecStart=/usr/bin/greetd -c %s\\n"; fi\n'
                'exit 1\n' % (log, greetd_cfg))
    _write_stub(bindir, 'greetd', 'exit 0\n')
    _write_stub(bindir, 'usermod', 'echo "usermod $*" >> "%s"\nexit 0\n' % log)
    _write_stub(bindir, 'curl', 'echo "curl $*" >> "%s"\nexit 1\n' % log)
    fakerepo = tmp_path / 'fakerepo'
    (fakerepo / 'launcher').mkdir(parents=True)
    (fakerepo / 'scripts').mkdir()
    # build_install installs common/xx_ui before the launcher (monorepo
    # siblings), so the fake tree mirrors the real layout.
    (tmp_path / 'common').mkdir(exist_ok=True)
    (fakerepo / 'scripts' / 'bootstrap-dots.sh').write_text('#!/bin/sh\nexit 0\n')
    fragdir = fakerepo / 'launcher' / 'data' / 'phoc'
    fragdir.mkdir(parents=True)
    (fragdir / 'qemu.ini').write_text((PHOC_DIR / 'qemu.ini').read_text())
    home = tmp_path / 'home'
    home.mkdir()
    env = {'PATH': '%s:%s' % (bindir, os.environ['PATH']),
           'SUDO': '',
           'PKG': 'apk',
           'HOME': str(home),
           'REPO_DIR': str(fakerepo),
           'UNATTENDED': '1',
           'AUTOLOGIN': '1' if autologin else '0',
           'NO_FONTS': '0',
           'XX_WM_SYSTEMD_RUN_DIR': str(tmp_path / 'no-systemd-here'),
           'XX_WM_PHOC_DEST': str(tmp_path / 'phoc.ini'),
           'XX_WM_FONT_DIR': str(tmp_path / 'fonts')}
    return env, {'log': log, 'sentinel': sentinel,
                 'greetd_cfg': greetd_cfg, 'home': home}


_UNATTENDED_FUNCS = ['msg_box', 'pkg_install', 'install_deps',
                     'install_portals', 'install_media_deps',
                     'download_nerd_font_fallback', 'install_fonts',
                     'install_logind_dropin', 'link_squeekboard_layouts',
                     'check_layer_shell', 'detect_phoc_device', 'select_phoc_scale',
                     '_install_phoc_fragment',
                     'is_systemd', 'has_openrc', 'enable_service',
                     'greetd_detected', 'greetd_config_path',
                     'setup_greetd_autologin', 'setup_greeter',
                     'add_input_group', 'build_install', 'do_unattended']


def test_unattended_full_run_no_whiptail(tmp_path):
    env, paths = _unattended_harness(tmp_path)
    env['DEVICE_ARG'] = 'qemu'
    proc = _run_funcs(_UNATTENDED_FUNCS, 'do_unattended', tmp_path, env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not paths['sentinel'].exists(), 'unattended must not call whiptail'
    assert '--- XX-WM install summary ---' in proc.stdout
    assert 'device: qemu' in proc.stdout
    assert 'Session menu' in proc.stdout
    assert 're-login' in proc.stdout
    log_text = paths['log'].read_text()
    assert 'py3-wayland' in log_text
    assert 'py3-pywayland' not in log_text
    assert 'gst-plugins-good' in log_text
    assert 'py3-ytmusicapi' in log_text
    assert 'usermod -aG input' in log_text
    assert 'Virtual-1' in (tmp_path / 'phoc.ini').read_text()
    link = paths['home'] / '.local' / 'share' / 'squeekboard' / 'keyboards'
    assert link.is_symlink()
    autologin_text = paths['greetd_cfg'].read_text()
    assert '[initial_session]' in autologin_text
    assert (tmp_path / 'greetd-config.toml.xx-wm-backup').is_file()


def test_unattended_unknown_device_fails(tmp_path):
    env, _paths = _unattended_harness(tmp_path)
    env['DEVICE_ARG'] = 'bogus-phone'
    proc = _run_funcs(_UNATTENDED_FUNCS, 'do_unattended', tmp_path, env)
    assert proc.returncode != 0
    assert 'unattended install failed' in proc.stderr


def test_unattended_no_device_no_detection_fails(tmp_path):
    env, _paths = _unattended_harness(tmp_path)
    env['DEVICE_ARG'] = ''
    env['XX_WM_SYSFS_DRM'] = str(tmp_path / 'empty-drm')
    (tmp_path / 'empty-drm').mkdir()
    proc = _run_funcs(_UNATTENDED_FUNCS, 'do_unattended', tmp_path, env)
    assert proc.returncode != 0
    assert 'no --device given' in proc.stderr


# --- 7. input group on every path ----------------------------------------------

def test_input_group_wired_and_announced(install_script):
    body = _function_body(install_script, 'add_input_group')
    assert '$SUDO usermod -aG input' in body
    assert 're-login' in body
    assert 'add_input_group' in _function_body(install_script, 'build_install')
    assert 'do_install' in install_script and 'do_unattended' in install_script


# --- 8. squeekboard layouts + fonts ----------------------------------------------

def test_layouts_linked_on_every_path(install_script):
    body = _function_body(install_script, 'link_squeekboard_layouts')
    assert body, 'link_squeekboard_layouts not defined'
    assert 'squeekboard/keyboards' in body
    build_body = _function_body(install_script, 'build_install')
    assert 'link_squeekboard_layouts' in build_body
    assert 'build_install' in _function_body(install_script, 'do_install')
    assert 'build_install' in _function_body(install_script, 'do_unattended')


def test_fonts_apk_names_and_fallback(install_script):
    body = _function_body(install_script, 'install_fonts')
    assert 'font-jetbrains-mono-nerd' in body
    assert 'font-jetbrains-mono' in body
    assert 'nerd-fonts-jetbrains-mono' not in install_script
    assert 'download_nerd_font_fallback' in body
    fallback = _function_body(install_script, 'download_nerd_font_fallback')
    assert fallback, 'download_nerd_font_fallback not defined'
    assert '.local/share/fonts' in fallback
    assert '.zip' in fallback
    assert 'return 1' not in fallback
    assert 'return 1' not in body


def test_no_fonts_flag_skips(tmp_path):
    env = {'NO_FONTS': '1', 'PKG': 'apk', 'SUDO': 'x'}
    proc = _run_funcs(['install_fonts'], 'install_fonts', tmp_path, env)
    assert proc.returncode == 0
    assert '--no-fonts' in proc.stdout


def test_font_fallback_skips_when_present(tmp_path):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    _write_stub(bindir, 'fc-list', 'echo "JetBrainsMono Nerd Font"\nexit 0\n')
    _write_stub(bindir, 'curl', 'echo CALLED\nexit 0\n')
    env = {'PATH': '%s:%s' % (bindir, os.environ['PATH']),
           'HOME': str(tmp_path)}
    proc = _run_funcs(['download_nerd_font_fallback'],
                      'download_nerd_font_fallback', tmp_path, env)
    assert proc.returncode == 0
    assert proc.stdout == ''


# --- 9. VM/dev device fragment ---------------------------------------------------

def test_qemu_fragment_content():
    frag = PHOC_DIR / 'qemu.ini'
    assert frag.is_file(), 'missing launcher/data/phoc/qemu.ini'
    cfg = configparser.ConfigParser()
    cfg.read(frag)
    assert cfg['core'].getboolean('xwayland') is False
    assert cfg['output:Virtual-1']['scale'] == '1.5'


def test_meson_ships_qemu_fragment(meson_text):
    assert "'data/phoc/qemu.ini'" in meson_text


def test_detect_phoc_device_picks_qemu(tmp_path):
    drm = tmp_path / 'drm' / 'card0-Virtual-1'
    drm.mkdir(parents=True)
    (drm / 'modes').write_text('2560x1600\n')
    env = {'XX_WM_SYSFS_DRM': str(tmp_path / 'drm')}
    proc = _run_funcs(['detect_phoc_device'], 'detect_phoc_device', tmp_path, env)
    assert proc.returncode == 0
    assert proc.stdout.strip() == 'qemu'


def test_ask_device_offers_qemu(install_script):
    body = _function_body(install_script, '_ask_device')
    assert '"qemu"' in body


def test_script_syntax_parses():
    result = subprocess.run(
        ['sh', '-n', str(INSTALL_SH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


if __name__ == '__main__':
    pytest.main([__file__, '-v'])


def test_apt_installs_the_layer_shell_typelib():
    # libgtk4-layer-shell0 alone left the shell without Gtk4LayerShell on
    # FuriOS (panels fell back to plain windows)
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / 'scripts'
           / 'install.sh').read_text()
    assert 'gir1.2-gtk4layershell-1.0' in src
