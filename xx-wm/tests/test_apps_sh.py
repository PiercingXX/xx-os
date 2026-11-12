"""scripts/apps.sh — pacman browser, Tailscale, RAM-gated Waydroid."""
import hashlib
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile

SCRIPT = Path(__file__).parent.parent / 'scripts' / 'apps.sh'


def test_script_syntax_parses():
    result = subprocess.run(
        ['sh', '-n', str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_pacman_browser_is_waterfox_or_firefox_not_esr():
    text = SCRIPT.read_text(encoding='utf-8')
    start = text.find('# --- 17.1 Browser')
    assert start != -1
    chunk = text[start:text.find('# --- 17.2')]
    pacman_at = chunk.find('[ "$PKG" = pacman ]')
    firefox_dt = chunk.find('BROWSER_DESKTOP=firefox.desktop')
    assert pacman_at != -1
    assert firefox_dt != -1
    pacman_arm = chunk[pacman_at:firefox_dt]
    assert 'pacman -Si waterfox' in pacman_arm
    assert 'pacman -Si waterfox-bin' in pacman_arm
    assert 'firefox-esr' not in pacman_arm
    assert re.search(r'pkg_install firefox$', chunk, re.M)
    assert 'BROWSER_DESKTOP=firefox-esr.desktop' in chunk
    assert not re.search(r'^\s*yay\b', chunk, re.M)
    assert 'yay -' not in chunk
    assert 'pkg_install firefox-esr' in chunk


def test_xdg_settings_uses_chosen_desktop():
    text = SCRIPT.read_text(encoding='utf-8')
    assert 'xdg-settings set default-web-browser "$BROWSER_DESKTOP"' in text


def test_tailscale_per_distro_then_static_fallback():
    text = SCRIPT.read_text(encoding='utf-8')
    chunk = text[text.find('# --- 17.3 Tailscale'):text.find('# --- 17.4')]
    # apk picks the service subpackage for the running init (pmOS is systemd)
    assert 'pkg_install tailscale tailscale-systemd' in chunk
    assert 'pkg_install tailscale tailscale-openrc' in chunk
    assert 'pacman) pkg_install tailscale' in chunk
    # Debian does not package tailscale: Tailscale's own apt repo
    assert 'pkgs.tailscale.com/stable/$_ts_dist/$_ts_code' in chunk
    assert '/etc/debian_version' in chunk      # FuriOS: no VERSION_CODENAME
    assert re.search(r'^TS_VERSION=\d+\.\d+\.\d+$', chunk, re.M)
    assert 'systemctl enable --now tailscaled' in chunk
    assert 'rc-service tailscale start' in chunk
    # the shade tile toggles without root
    assert 'tailscale set --operator="$(id -un)"' in chunk
    assert chunk.find('ts_apt_repo ;;') < chunk.find('|| ts_static')


def test_waydroid_skipped_under_3gib():
    text = SCRIPT.read_text(encoding='utf-8')
    assert '3145728' in text
    assert 'MemTotal' in text
    assert 'SKIP: Waydroid' in text


def test_pwa_desktop_escapes_percent_and_quotes_url():
    text = SCRIPT.read_text(encoding='utf-8')
    assert "s/%/%%/g" in text
    assert 'Exec=$_browser --new-window "$_url"' in text
    assert 'pwa_desktop skippy "Skippy"' in text
    assert 'http://${SKIPPY_HOST}:8282/mobile/' in text


def _tailscale_chunk():
    text = SCRIPT.read_text(encoding='utf-8')
    start = text.find('# --- 17.3 Tailscale')
    end = text.find('install_tailscale() {')
    assert start != -1 and end > start
    return text[start:end]


def _ts_version():
    return re.search(r'^TS_VERSION=(\S+)$', _tailscale_chunk(),
                     re.M).group(1)


def _ts_tarball(tmp_path, version, arch):
    top = 'tailscale_%s_%s' % (version, arch)
    files = {'tailscale': b'#!/bin/sh\n', 'tailscaled': b'#!/bin/sh\n',
             'systemd/tailscaled.service':
                 b'[Service]\nExecStart=/usr/sbin/tailscaled\n',
             'systemd/tailscaled.defaults': b'PORT=41641\n'}
    path = tmp_path / ('%s.tgz' % top)
    with tarfile.open(path, 'w:gz') as tf:
        for name, data in files.items():
            info = tarfile.TarInfo('%s/%s' % (top, name))
            info.size = len(data)
            info.mode = 0o755
            tf.addfile(info, io.BytesIO(data))
    return path


def _run_ts_static(tmp_path, tarball, sha_override=None):
    """ts_static from the real script with curl/uname/tar stubbed and a
    logging stand-in for $SUDO, so nothing is installed for real."""
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    log = tmp_path / 'calls.log'
    real_tar = shutil.which('tar')
    stubs = {
        'uname': 'echo aarch64\n',
        'curl': ('echo "curl $*" >> "%s"\n'
                 'while [ $# -gt 0 ]; do case "$1" in\n'
                 '  -o) out="$2"; shift 2 ;;\n'
                 '  *) shift ;;\n'
                 'esac; done\n'
                 'cp "%s" "$out"\n' % (log, tarball)),
        'tar': 'echo "tar $*" >> "%s"\nexec "%s" "$@"\n' % (log, real_tar),
        'fakesudo': 'echo "sudo $*" >> "%s"\ncat >/dev/null\n' % log,
    }
    for name, body in stubs.items():
        (bindir / name).write_text('#!/bin/sh\n' + body)
        (bindir / name).chmod(0o755)
    funcs = tmp_path / 'ts.sh'
    funcs.write_text(_tailscale_chunk())
    driver = '. "%s"; ' % funcs
    if sha_override:
        driver += 'TS_SHA256_ARM64=%s; ' % sha_override
    driver += 'ts_static'
    env = dict(os.environ)
    env.update({'PATH': '%s:%s' % (bindir, os.environ['PATH']),
                'SUDO': 'fakesudo', 'PKG': 'apt'})
    proc = subprocess.run(['sh', '-c', driver], capture_output=True,
                          stdin=subprocess.DEVNULL, text=True, env=env,
                          cwd=str(tmp_path), timeout=60)
    return proc, (log.read_text() if log.exists() else '')


def test_tailscale_static_build_is_pinned_with_sums():
    chunk = _tailscale_chunk()
    assert re.search(r'^TS_VERSION=\d+\.\d+\.\d+$', chunk, re.M)
    for arch in ('ARM64', 'ARM', 'AMD64'):
        assert re.search(r'^TS_SHA256_%s=[0-9a-f]{64}$' % arch, chunk, re.M)
    assert 'TarballsVersion' not in chunk
    assert '?mode=json' not in chunk
    assert not re.search(r'\|\s*tar\b', chunk)


def test_tailscale_tarball_with_wrong_sum_is_not_installed(tmp_path):
    version = _ts_version()
    proc, calls = _run_ts_static(
        tmp_path, _ts_tarball(tmp_path, version, 'arm64'))
    assert proc.returncode != 0
    assert 'not the pinned build' in proc.stderr
    assert 'tailscale_%s_arm64.tgz' % version in calls
    assert 'tar ' not in calls
    assert 'sudo ' not in calls


def test_tailscale_tarball_with_pinned_sum_is_installed(tmp_path):
    tarball = _ts_tarball(tmp_path, _ts_version(), 'arm64')
    sha = hashlib.sha256(tarball.read_bytes()).hexdigest()
    proc, calls = _run_ts_static(tmp_path, tarball, sha)
    assert proc.returncode == 0, proc.stderr
    assert 'tar xzf' in calls
    assert re.search(r'sudo install -m 755 \S+/tailscale \S+/tailscaled '
                     r'/usr/local/bin/', calls)


def test_tailscale_apt_key_is_pinned_and_list_written_locally():
    chunk = _tailscale_chunk()
    assert re.search(r'^TS_KEYRING_SHA256=[0-9a-f]{64}$', chunk, re.M)
    check = chunk.find('sha256_is "$_ts_key" "$TS_KEYRING_SHA256"')
    install = chunk.find('install -m 644 "$_ts_key"')
    assert check != -1 and check < install
    assert 'tailscale-keyring.list' not in chunk
    assert ('deb [signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg]'
            ' https://pkgs.tailscale.com/stable/$_ts_dist $_ts_code main'
            in chunk)
