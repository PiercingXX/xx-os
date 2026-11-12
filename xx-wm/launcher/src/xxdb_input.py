"""xxdb input injection: a uinput virtual touchscreen + keyboard.

Prefers python-libevdev when importable, else raw /dev/uinput ioctls via
struct/fcntl. Needs the session user in the `input` group or uinput
permission — probe() reports clearly when injection is unavailable instead
of failing mid-command.
"""
from __future__ import annotations

import os
import struct

UINPUT_PATH = '/dev/uinput'

EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03
SYN_REPORT = 0
ABS_X = 0x00
ABS_Y = 0x01
BTN_TOUCH = 0x14a

_KEY_CODES = {
    'ENTER': 28, 'ESC': 1, 'ESCAPE': 1, 'BACKSPACE': 14, 'TAB': 15,
    'SPACE': 57, 'DELETE': 111, 'HOME': 102, 'END': 107,
    'UP': 103, 'DOWN': 108, 'LEFT': 105, 'RIGHT': 106,
    'PAGEUP': 104, 'PAGEDOWN': 109, 'VOLUMEUP': 115, 'VOLUMEDOWN': 114,
    'POWER': 116, 'BACK': 158, 'MENU': 139,
}
for _i, _ch in enumerate('1234567890'):
    _KEY_CODES[_ch] = (2 + _i) if _ch != '0' else 11
# linux/input-event-codes.h: each letter row starts at its own code, with
# punctuation keys between the rows (KEY_SEMICOLON..KEY_BACKSLASH = 39..43).
for _row, _base in (('QWERTYUIOP', 16), ('ASDFGHJKL', 30), ('ZXCVBNM', 44)):
    for _i, _ch in enumerate(_row):
        _KEY_CODES[_ch] = _base + _i
del _row, _base
_KEY_CODES['SHIFT'] = 42
del _i, _ch

_SHIFTED = {'!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6',
            '&': '7', '*': '8', '(': '9', ')': '0', '_': '-', '+': '=',
            '{': '[', '}': ']', '|': '\\', ':': ';', '"': "'", '<': ',',
            '>': '.', '?': '/', '~': '`'}
_PLAIN = {'-': 12, '=': 13, '[': 26, ']': 27, ';': 39, "'": 40, '`': 41,
          '\\': 43, ',': 51, '.': 52, '/': 53}


def _libevdev_backend():
    try:
        import libevdev
    except ImportError:
        return None
    return libevdev


def probe(uinput_path: str = UINPUT_PATH) -> tuple[bool, str]:
    if _libevdev_backend() is None and not os.path.exists(uinput_path):
        return False, f'{uinput_path} missing and python-libevdev not installed'
    if not os.access(uinput_path, os.W_OK):
        user = os.environ.get('USER', 'session user')
        return False, (f'no write access to {uinput_path}: add the {user} '
                       'to the `input` group or fix uinput permissions')
    return True, 'uinput writable'


class _RawUinput:
    """Minimal uinput device via raw ioctls (no libevdev needed)."""

    UI_SET_EVBIT = 0x40045564
    UI_SET_KEYBIT = 0x40045565
    UI_SET_ABSBIT = 0x40045566
    UI_DEV_SETUP = 0x405c5503
    UI_ABS_SETUP = 0x405c5504
    UI_DEV_CREATE = 0x5501
    UI_DEV_DESTROY = 0x5502

    def __init__(self, width: int, height: int,
                 path: str = UINPUT_PATH) -> None:
        import fcntl
        self._fcntl = fcntl
        self._fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        try:
            self._setup(width, height)
        except BaseException:
            os.close(self._fd)
            raise

    def _ioctl(self, fd: int, req: int, arg=0):
        return self._fcntl.ioctl(fd, req, arg)

    def _setup(self, width: int, height: int) -> None:
        fcntl = self._fcntl
        fcntl.ioctl(self._fd, self.UI_SET_EVBIT, EV_SYN)
        fcntl.ioctl(self._fd, self.UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(self._fd, self.UI_SET_EVBIT, EV_ABS)
        fcntl.ioctl(self._fd, self.UI_SET_KEYBIT, BTN_TOUCH)
        for code in set(_KEY_CODES.values()):
            fcntl.ioctl(self._fd, self.UI_SET_KEYBIT, code)
        fcntl.ioctl(self._fd, self.UI_SET_ABSBIT, ABS_X)
        fcntl.ioctl(self._fd, self.UI_SET_ABSBIT, ABS_Y)
        name = b'xxdb-input' + b'\x00' * (80 - len('xxdb-input'))
        setup = struct.pack('80sHHHHi128sHi', name, 0x3, 0x1, 0x1, 0x0,
                            b'\x00' * 128, 0, 0)
        fcntl.ioctl(self._fd, self.UI_DEV_SETUP, setup)
        for axis, maximum in ((ABS_X, max(1, width - 1)),
                              (ABS_Y, max(1, height - 1))):
            abs_setup = struct.pack('Ii6i', axis,
                                    0, 0, maximum, 0, 0, 0, 0)
            fcntl.ioctl(self._fd, self.UI_ABS_SETUP, abs_setup)
        fcntl.ioctl(self._fd, self.UI_DEV_CREATE)

    def _event(self, kind: int, code: int, value: int) -> None:
        packed = struct.pack('qqHHi', 0, 0, kind, code, value)
        os.write(self._fd, packed)

    def _sync(self) -> None:
        self._event(EV_SYN, SYN_REPORT, 0)

    def tap(self, x: int, y: int) -> None:
        self._event(EV_ABS, ABS_X, x)
        self._event(EV_ABS, ABS_Y, y)
        self._event(EV_KEY, BTN_TOUCH, 1)
        self._sync()
        self._event(EV_KEY, BTN_TOUCH, 0)
        self._sync()

    def key(self, code: int) -> None:
        self._event(EV_KEY, code, 1)
        self._sync()
        self._event(EV_KEY, code, 0)
        self._sync()

    def close(self) -> None:
        try:
            self._fcntl.ioctl(self._fd, self.UI_DEV_DESTROY)
        except OSError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass


def _open_device(width: int, height: int, path: str = UINPUT_PATH):
    libevdev = _libevdev_backend()
    if libevdev is not None:
        device = libevdev.Device()
        device.name = 'xxdb-input'
        device.enable(libevdev.EV_ABS.ABS_X,
                      libevdev.InputAbsInfo(minimum=0, maximum=width - 1))
        device.enable(libevdev.EV_ABS.ABS_Y,
                      libevdev.InputAbsInfo(minimum=0, maximum=height - 1))
        device.enable(libevdev.EV_KEY.BTN_TOUCH)
        for code in set(_KEY_CODES.values()):
            device.enable(libevdev.EV_KEY(code))
        return device.create_uinput_device()
    return _RawUinput(width, height, path)


def _char_events(emit, char: str) -> bool:
    if char == ' ':
        emit(_KEY_CODES['SPACE'], False)
        return True
    if char == '\n':
        emit(_KEY_CODES['ENTER'], False)
        return True
    upper = char.upper()
    if upper in _KEY_CODES and (char.isalnum() or upper in ('ENTER',)):
        emit(_KEY_CODES[upper], char.isupper() and char.isalpha())
        return True
    if char in _PLAIN:
        emit(_PLAIN[char], False)
        return True
    if char in _SHIFTED and _SHIFTED[char] in _PLAIN:
        emit(_PLAIN[_SHIFTED[char]], True)
        return True
    return False


def inject(action: str, params: dict | None = None,
           uinput_path: str = UINPUT_PATH) -> tuple[bool, str]:
    """Run one input action. Returns (ok, message)."""
    params = dict(params or {})
    ok, reason = probe(uinput_path)
    if not ok:
        return False, reason
    width = max(1, int(params.get('width', 360)))
    height = max(1, int(params.get('height', 720)))
    try:
        device = _open_device(width, height, uinput_path)
    except (OSError, PermissionError) as error:
        return False, f'cannot create uinput device: {error}'
    try:
        return _drive(device, action, params, width, height)
    finally:
        try:
            close = getattr(device, 'close', None)
            if callable(close):
                close()
        except OSError:
            pass


def _drive(device, action: str, params: dict,
           width: int, height: int) -> tuple[bool, str]:
    libevdev = _libevdev_backend()
    if libevdev is not None and not isinstance(device, _RawUinput):
        return _drive_libevdev(device, libevdev, action, params,
                               width, height)
    return _drive_raw(device, action, params, width, height)


def _drive_libevdev(device, libevdev, action: str, params: dict,
                    width: int, height: int) -> tuple[bool, str]:
    def emit(code: int, shift: bool) -> None:
        events = []
        if shift:
            events.append(libevdev.InputEvent(libevdev.EV_KEY.KEY_LEFTSHIFT, 1))
        events.append(libevdev.InputEvent(libevdev.EV_KEY(code), 1))
        events.append(libevdev.InputEvent(libevdev.EV_SYN.SYN_REPORT, 0))
        events.append(libevdev.InputEvent(libevdev.EV_KEY(code), 0))
        if shift:
            events.append(libevdev.InputEvent(libevdev.EV_KEY.KEY_LEFTSHIFT, 0))
        events.append(libevdev.InputEvent(libevdev.EV_SYN.SYN_REPORT, 0))
        device.send_events(events)

    def touch(x: int, y: int, down: bool) -> None:
        device.send_events([
            libevdev.InputEvent(libevdev.EV_ABS.ABS_X, x),
            libevdev.InputEvent(libevdev.EV_ABS.ABS_Y, y),
            libevdev.InputEvent(libevdev.EV_KEY.BTN_TOUCH, 1 if down else 0),
            libevdev.InputEvent(libevdev.EV_SYN.SYN_REPORT, 0),
        ])

    return _run_gesture(action, params, width, height, touch, emit,
                       lambda c, s: _char_events(emit, c))


def _drive_raw(device: _RawUinput, action: str, params: dict,
               width: int, height: int) -> tuple[bool, str]:
    import time

    def emit(code: int, shift: bool) -> None:
        if shift:
            device._event(EV_KEY, _KEY_CODES['SHIFT'], 1)
        device.key(code)
        if shift:
            device._event(EV_KEY, _KEY_CODES['SHIFT'], 0)
            device._sync()

    def touch(x: int, y: int, down: bool) -> None:
        device._event(EV_ABS, ABS_X, x)
        device._event(EV_ABS, ABS_Y, y)
        device._event(EV_KEY, BTN_TOUCH, 1 if down else 0)
        device._sync()

    def nap(ms: int) -> None:
        time.sleep(max(0, ms) / 1000.0)

    return _run_gesture(action, params, width, height, touch, emit,
                       lambda c, s: _char_events(emit, c), nap=nap)


def _run_gesture(action, params, width, height, touch, emit, type_char,
                 nap=None) -> tuple[bool, str]:
    if action == 'tap':
        try:
            x = int(params['x'])
            y = int(params['y'])
        except (KeyError, TypeError, ValueError):
            return False, 'tap needs integer x and y'
        if not (0 <= x < width and 0 <= y < height):
            return False, f'tap ({x}, {y}) outside {width}x{height}'
        touch(x, y, True)
        touch(x, y, False)
        return True, f'tapped {x},{y}'
    if action == 'swipe':
        try:
            x1, y1 = int(params['x1']), int(params['y1'])
            x2, y2 = int(params['x2']), int(params['y2'])
            ms = max(0, int(params.get('ms', 300)))
        except (KeyError, TypeError, ValueError):
            return False, 'swipe needs integer x1 y1 x2 y2 and ms'
        for point in ((x1, y1), (x2, y2)):
            if not (0 <= point[0] < width and 0 <= point[1] < height):
                return False, f'swipe point {point} outside {width}x{height}'
        steps = max(1, min(50, ms // 10))
        touch(x1, y1, True)
        for step in range(1, steps + 1):
            x = x1 + (x2 - x1) * step // steps
            y = y1 + (y2 - y1) * step // steps
            touch(x, y, True)
            if nap is not None and step < steps:
                nap(ms // steps)
        touch(x2, y2, False)
        return True, f'swiped {x1},{y1} -> {x2},{y2} in {ms}ms'
    if action == 'key':
        name = str(params.get('name', '')).upper()
        if name not in _KEY_CODES:
            return False, f'unknown key {name!r}'
        emit(_KEY_CODES[name], False)
        return True, f'pressed {name}'
    if action == 'text':
        text = str(params.get('string', ''))
        if len(text) > 4096:
            return False, 'text too long (max 4096 chars)'
        skipped = [c for c in text if not type_char(c, False)]
        if skipped:
            return False, f'unsupported characters: {"".join(skipped[:8])!r}'
        return True, f'typed {len(text)} chars'
    return False, f'unknown input action {action!r}'
