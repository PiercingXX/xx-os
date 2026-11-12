"""Tailscale for the shade tile: status, up/down and the sign-in URL.

Talks to the `tailscale` CLI. Every call blocks (subprocess, up to a few
seconds), so callers run them on worker threads. Up/down need no root once
the installer ran `tailscale set --operator=$USER` (see scripts/apps.sh).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from typing import NamedTuple

RUNNING, STOPPED, NEEDS_LOGIN, UNAVAILABLE = (
    'running', 'stopped', 'needs_login', 'unavailable')

SIGN_IN = 'Sign in'
NO_PERMISSION = 'No permission'


class Status(NamedTuple):
    state: str
    detail: str = ''        # tile second line: device name, 'Sign in', …
    auth_url: str = ''


def available() -> bool:
    return shutil.which('tailscale') is not None


def _run(*args: str, timeout: float = 5.0) -> subprocess.CompletedProcess:
    return subprocess.run(['tailscale', *args], capture_output=True,
                          text=True, timeout=timeout, check=False)


def parse_status(raw: str) -> Status:
    """`tailscale status --json` → Status (pure, for tests)."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return Status(UNAVAILABLE)
    if not isinstance(data, dict):
        return Status(UNAVAILABLE)
    backend = data.get('BackendState')
    auth_url = str(data.get('AuthURL') or '')
    if backend == 'Running':
        me = data.get('Self') or {}
        name = str(me.get('HostName') or '') if isinstance(me, dict) else ''
        return Status(RUNNING, name or 'Connected')
    if backend == 'NeedsLogin' or auth_url:
        return Status(NEEDS_LOGIN, SIGN_IN, auth_url)
    if backend in ('Stopped', 'Starting', 'NoState', 'NeedsMachineAuth'):
        return Status(STOPPED)
    return Status(UNAVAILABLE)


def status() -> Status:
    if not available():
        return Status(UNAVAILABLE)
    try:
        proc = _run('status', '--json')
    except (OSError, subprocess.TimeoutExpired):
        return Status(UNAVAILABLE)
    # `status` exits non-zero while stopped or logged out but still prints
    # the JSON; only an empty answer means tailscaled is not reachable.
    return parse_status(proc.stdout) if proc.stdout.strip() else Status(UNAVAILABLE)


def _denied(proc: subprocess.CompletedProcess) -> bool:
    return 'access denied' in (proc.stderr or '').lower()


def down() -> Status:
    try:
        proc = _run('down')
    except (OSError, subprocess.TimeoutExpired):
        return status()
    if _denied(proc):
        return Status(STOPPED, NO_PERMISSION)
    return status()


def up(wait_s: float = 10.0) -> Status:
    """Connect. When the device needs a login, `tailscale up` blocks until
    the browser sign-in finishes, so it is left running detached and this
    returns as soon as tailscaled reports the AuthURL to open."""
    try:
        proc = subprocess.Popen(['tailscale', 'up'], stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True,
                                start_new_session=True)
    except OSError:
        return Status(UNAVAILABLE)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            if 'access denied' in (proc.stderr.read() or '').lower():
                return Status(STOPPED, NO_PERMISSION)
            return status()
        current = status()
        if current.state == RUNNING or current.auth_url:
            return current
        time.sleep(0.4)
    return status()
