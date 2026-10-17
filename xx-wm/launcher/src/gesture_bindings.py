"""Generate the lisgd bindings for system-level gestures.

docs/config.md documents the system-level gesture slots as configurable
via gestures.json; this module makes that real. At session start the xx-wm
wrapper (data/xx-wm.in) runs this module and feeds each output line to
lisgd as one -g value, so lisgd startup stays in exactly one place and no
extra daemon exists.

Valid values for the system-level slots are the IPC verbs main.py actually
dispatches (_on_ipc_command; the ipc.py protocol line):

    gesture.back  gesture.home  gesture.shade
    gesture.keyboard  gesture.switcher

gestures.json action names map through ACTION_TO_VERB (home→gesture.home,
app_switcher→gesture.switcher, notification_shade→gesture.shade,
back→gesture.back, search→gesture.keyboard).

A valid action with no verb mapping (`camera`, `launch:<app_id>`,
`none`) silently keeps that slot's legacy default verb; a truly invalid
value is ignored at load (see gesture_config), so the default action
mapping applies instead (config-compat invariant: a bad gestures.json
never changes device behavior).
Rebinding restarts lisgd immediately from Settings. If that restart
fails, the wrapper's session-start bindings still apply at next login.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

from gesture_config import IPC_VERBS, GestureConfig

# slot -> lisgd geometry prefixes, in canonical order. Fields:
# nfingers,swipe,startregion,endregion,distance,mode — R fires on release.
# swipe_left_edge owns both edge directions, matching the pair the xx-wm
# wrapper hardcoded before bindings became generated.
_LISGD_GEOMETRY: dict[str, tuple[str, ...]] = {
    'swipe_up_short': ('1,DU,B,S,R',),
    'swipe_up_long': ('1,DU,B,L,R',),
    'swipe_down_top': ('1,UD,T,*,R',),
    'swipe_left_edge': ('1,LR,L,*,R', '1,RL,R,*,R'),
}

# slot -> verb fired when gestures.json says nothing valid. Invalid values
# (launch:, camera, none, garbage) keep these legacy verbs; pinned by
# tests/test_gesture_bindings.py LEGACY_BINDINGS.
DEFAULT_VERBS: dict[str, str] = {
    'swipe_up_short': 'gesture.keyboard',
    'swipe_up_long': 'gesture.home',
    'swipe_down_top': 'gesture.shade',
    'swipe_left_edge': 'gesture.back',
}

# gestures.json action name -> IPC verb lisgd fires. Config stores actions
# (home, app_switcher, ...); lisgd can only exec xx-wm-ipc <verb>.
# Map the names the settings UI writes; leave explicit IPC verbs and
# launch:<app> (in-shell, not lisgd) alone.
ACTION_TO_VERB: dict[str, str] = {
    'home': 'gesture.home',
    'app_switcher': 'gesture.switcher',
    'notification_shade': 'gesture.shade',
    'back': 'gesture.back',
    'search': 'gesture.keyboard',
}


def resolve_verb(gc: GestureConfig, slot: str) -> str:
    """Configured IPC verb for a system-level slot, or its default."""
    value = gc.get(slot)
    if value in IPC_VERBS:
        return value
    mapped = ACTION_TO_VERB.get(value)
    if mapped is not None:
        return mapped
    return DEFAULT_VERBS[slot]


def detect_touch_device(sys_input: Path | None = None) -> str | None:
    """First evdev node with INPUT_PROP_DIRECT, matching the session wrapper."""
    override = os.environ.get('PIERCING_TOUCH_DEV')
    if override:
        return override
    root = sys_input or Path('/sys/class/input')
    try:
        nodes = sorted(root.glob('event*'))
    except OSError:
        return None
    for syspath in nodes:
        try:
            raw = (syspath / 'device' / 'properties').read_text().strip()
        except OSError:
            continue
        if not raw:
            continue
        try:
            value = int(raw, 16)
        except ValueError:
            continue
        if value & 2:
            return f'/dev/input/{syspath.name}'
    return None


def _lisgd_pids(proc: Path = Path('/proc'), uid: int | None = None) -> list[int]:
    """PIDs of this user's running lisgd, read from /proc (no subprocess)."""
    uid = os.getuid() if uid is None else uid
    pids: list[int] = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if (entry / 'comm').read_text().strip() != 'lisgd':
                continue
            if entry.stat().st_uid != uid:
                continue
        except OSError:
            continue
        pids.append(int(entry.name))
    return pids


def _kill_lisgd(pids=None, kill=None) -> None:
    # Only the lisgd instances that exist *now*. A fire-and-forget
    # `pkill -x lisgd` raced the replacement spawned right after it and
    # killed that too: every shell start left the session with no
    # gestures (owner stuck inside an app on the FLX1).
    import signal
    kill = kill or os.kill
    for pid in (_lisgd_pids() if pids is None else pids):
        try:
            kill(pid, signal.SIGTERM)
        except OSError:
            pass


def restart_lisgd(
        bindir: str,
        gc: GestureConfig | None = None,
        *,
        touch_dev: str | None = None,
        spawn=None,
        kill_fn=None,
        which=None) -> bool:
    """Replace the session lisgd with bindings from the current gestures.json."""
    touch = detect_touch_device() if touch_dev is None else touch_dev
    if not touch:
        return False
    lisgd = (which or shutil.which)('lisgd')
    if not lisgd:
        return False
    bindings = generate_bindings(bindir, gc)
    if not bindings:
        return False
    argv = [lisgd, '-d', touch]
    for binding in bindings:
        argv.extend(['-g', binding])
    try:
        (kill_fn or _kill_lisgd)()
        (spawn or subprocess.Popen)(
            argv, close_fds=True, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    return True


def generate_bindings(bindir: str, gc: GestureConfig | None = None) -> list[str]:
    """One -g value per binding, in canonical order."""
    gc = gc if gc is not None else GestureConfig()
    ipc = f'{bindir}/xx-wm-ipc'
    lines: list[str] = []
    for slot, geometries in _LISGD_GEOMETRY.items():
        verb = resolve_verb(gc, slot)
        for geometry in geometries:
            lines.append(f'{geometry},{ipc} {verb}')
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--bindir', required=True,
        help='directory holding the installed xx-wm-ipc client')
    args = parser.parse_args(argv)
    for line in generate_bindings(args.bindir):
        print(line)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
