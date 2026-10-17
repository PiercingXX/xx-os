"""
Default home layout seeding on first boot.

Seeds five slots in order — Notes, Audio, Comms, Calendar, Tools — resolving
each member against installed apps. Every seeded label is also written as a
per-app rename (config.app_labels) so the drawer, search, and folders show the
same names as home. Also seeds gesture defaults (swipe left -> Skippy when an
app by that name exists, swipe right -> camera) and hides stock clutter.

Unresolved members are skipped, empty folders are not created, and slots
compact (no gaps). Never overwrites a non-empty home_slots; the
default_layout_applied flag is set even when nothing resolved.
"""
from __future__ import annotations

import shutil
from typing import Callable


def _gio() -> object:
    """Import Gio lazily so the module is importable for pure-logic tests.

    Gio (PyGObject) is only needed by the real resolver lookups below; the
    seeding logic itself is injected-fake testable without it.
    """
    import gi

    gi.require_version('Gio', '2.0')

    from gi.repository import Gio

    return Gio

# Shell-redundant or noise apps hidden on first boot. Hidden apps stay
# searchable in the drawer; this only removes them from the browse list.
# org.gnome.Calls/Chatty are NOT here: on devices with a real telephony stack
# (FuriOS, pmOS) they ARE the Phone/Text apps and the Comms folder binds them.
STOCK_HIDDEN_APPS = [
    'org.gnome.Tour',
    'org.gnome.Software',
    'org.gnome.Settings',
    'mobi.phosh.MobileSettings',
]

_NOTES_CANDIDATES = ['io.piercingxx.XXNote', 'org.gnome.Notes', 'net.cozic.joplin_desktop',
                     'md.obsidian.Obsidian', 'org.gnome.TextEditor']
# Android-container apps: FuriOS prefixes desktop ids with 'android.',
# Waydroid with 'waydroid.' — carry both variants for every package
_AUDIOBOOK_CANDIDATES = ['com.audiobookshelf.app', 'android.com.audiobookshelf.app',
                         'waydroid.com.audiobookshelf.app']
_MUSIC_CANDIDATES = [
    'android.com.google.android.apps.youtube.music',
    'waydroid.com.google.android.apps.youtube.music',
    'com.github.neithern.g4music',
    'org.gnome.Rhythmbox3',
    'org.gnome.Lollypop',
]
# Phone/Text prefer the device's real telephony apps (GNOME Calls / Chatty —
# what FuriOS and postmarketOS ship); the shell's built-in dialer is the
# fallback so the pair always correlates with whatever stack actually works.
_PHONE_CANDIDATES = ['io.piercingxx.XXDialer', 'org.gnome.Calls', 'sm.puri.Calls']
_TEXT_CANDIDATES = ['io.piercingxx.TxxT', 'sm.puri.Chatty', 'org.gnome.Chats']
_EMAIL_CANDIDATES = ['io.piercingxx.XXEmail', 'org.gnome.Geary', 'org.gnome.Evolution']
_CHAT_CANDIDATES = ['android.com.synology.dschat', 'waydroid.com.synology.dschat',
                    'com.synology.dschat', 'com.synology.chat']
_SOFTPHONE_CANDIDATES = [
    'android.cz.acrobits.softphone.cloudphone',
    'waydroid.cz.acrobits.softphone.cloudphone',
    'cz.acrobits.softphone.cloudphone',
]
_CALENDAR_CANDIDATES = ['io.piercingxx.XXCalendar', 'org.gnome.Calendar', 'gnome-calendar',
                        'android.com.google.android.calendar',
                        'waydroid.com.google.android.calendar']
_CALCULATOR_CANDIDATES = ['io.piercingxx.XXCalculator', 'org.gnome.Calculator',
                          'gnome-calculator']
_CAMERA_CANDIDATES = ['furios-camera', 'org.postmarketos.Megapixels',
                      'org.gnome.Snapshot', 'megapixels']
_PHOTOS_CANDIDATES = [
    'android.com.synology.projectkailash',
    'waydroid.com.synology.projectkailash',
    'com.synology.projectkailash',
    'io.furios.Gallery',
    'org.gnome.Loupe',
]
_WEATHER_CANDIDATES = ['io.piercingxx.XXWeather', 'org.gnome.Weather']
_FILES_CANDIDATES = ['io.piercingxx.XXFiles']


def _find_desktop_app(app_id: str) -> str | None:
    """Resolve a desktop id, tolerating a missing .desktop suffix."""
    Gio = _gio()
    for candidate in (app_id, f'{app_id}.desktop'):
        try:
            if Gio.DesktopAppInfo.new(candidate) is not None:
                return candidate
        except Exception:
            continue
    return None


def _find_cmd(cmd: list[str]) -> bool:
    if not cmd:
        return False
    return shutil.which(cmd[0]) is not None


def _find_browser() -> tuple[str, str] | None:
    """Default HTTP handler -> (desktop id, display name)."""
    Gio = _gio()
    try:
        info = Gio.AppInfo.get_default_for_type('x-scheme-handler/http', True)
        if info:
            return (info.get_id() or '', info.get_display_name() or 'Internet')
    except Exception:
        pass
    return None


def _find_by_name(name: str) -> str | None:
    """First installed app whose display name matches case-insensitively.

    Skippy installs as a PWA so its desktop id varies; the label is stable.
    """
    Gio = _gio()
    wanted = name.strip().casefold()
    try:
        for info in Gio.AppInfo.get_all():
            if not isinstance(info, Gio.DesktopAppInfo):
                continue
            display = (info.get_display_name() or info.get_name() or '').strip()
            if display.casefold() == wanted:
                return info.get_id()
    except Exception:
        pass
    return None


class DefaultLayoutResolver:
    """Resolves the default layout against installed apps.

    All lookups are injectable so the seeding logic is unit-testable.
    """

    def __init__(
        self,
        find_desktop: Callable[[str], str | None] | None = None,
        find_cmd: Callable[[list[str]], bool] | None = None,
        find_browser: Callable[[], tuple[str, str] | None] | None = None,
        find_by_name: Callable[[str], str | None] | None = None,
    ) -> None:
        self._find_desktop = find_desktop or _find_desktop_app
        self._find_cmd = find_cmd or _find_cmd
        self._find_browser = find_browser or _find_browser
        self._find_by_name = find_by_name or _find_by_name
        self.labels: dict[str, str] = {}

    def _first(self, candidates: list[str]) -> str | None:
        for cid in candidates:
            resolved = self._find_desktop(cid)
            if resolved:
                return resolved
        return None

    def _app_member(self, label: str, candidates: list[str]) -> dict | None:
        app_id = self._first(candidates)
        if not app_id:
            return None
        self.labels[app_id] = label
        return {'label': label, 'app_id': app_id}

    def resolve_notes(self) -> dict | None:
        if self._find_cmd(['piercing-note']):
            return {'type': 'app', 'label': 'Notes', 'cmd': ['piercing-note']}
        member = self._app_member('Notes', _NOTES_CANDIDATES)
        if member:
            return {'type': 'app', **member}
        return None

    def resolve_audio_folder(self) -> dict | None:
        children = []
        for label, candidates in (('Audiobook', _AUDIOBOOK_CANDIDATES),
                                  ('Music', _MUSIC_CANDIDATES)):
            member = self._app_member(label, candidates)
            if member:
                children.append(member)
        if not children:
            return None
        return {'type': 'folder', 'label': 'Audio', 'folder': children}

    def resolve_comms_folder(self) -> dict | None:
        phone = self._app_member('Phone', _PHONE_CANDIDATES)
        if phone is None:
            phone = {'label': 'Phone', 'app_id': None, 'cmd': None}  # built-in dialer
        children = [phone]
        text = self._app_member('Text', _TEXT_CANDIDATES)
        if text:
            children.append(text)
        elif self._find_cmd(['chatty']):
            children.append({'label': 'Text', 'cmd': ['chatty']})
        for label, candidates in (('Email', _EMAIL_CANDIDATES),
                                  ('Chat', _CHAT_CANDIDATES),
                                  ('SoftPhone', _SOFTPHONE_CANDIDATES)):
            member = self._app_member(label, candidates)
            if member:
                children.append(member)
        return {'type': 'folder', 'label': 'Comms', 'folder': children}

    def resolve_calendar(self) -> dict | None:
        member = self._app_member('Calendar', _CALENDAR_CANDIDATES)
        if member:
            return {'type': 'app', **member}
        return None

    def resolve_tools_folder(self) -> dict | None:
        children = []
        browser = self._find_browser()
        if browser and browser[0]:
            app_id, name = browser
            self.labels[app_id] = name
            children.append({'label': name, 'app_id': app_id})
        for label, candidates in (('Calculator', _CALCULATOR_CANDIDATES),
                                  ('Camera', _CAMERA_CANDIDATES),
                                  ('Photos', _PHOTOS_CANDIDATES),
                                  ('Weather', _WEATHER_CANDIDATES),
                                  ('Files', _FILES_CANDIDATES)):
            member = self._app_member(label, candidates)
            if member:
                children.append(member)
        if not children:
            return None
        return {'type': 'folder', 'label': 'Tools', 'folder': children}

    def resolve_slots(self) -> list[dict]:
        slots = []
        for resolve in (self.resolve_notes, self.resolve_audio_folder,
                        self.resolve_comms_folder, self.resolve_calendar,
                        self.resolve_tools_folder):
            slot = resolve()
            if slot:
                slots.append(slot)
        return slots

    def resolve_skippy(self) -> str | None:
        return self._find_by_name('Skippy')


def seed_default_layout(config, resolver: DefaultLayoutResolver | None = None) -> list[dict]:
    """Resolve the default slots. Never overwrites a non-empty home_slots."""
    existing = config.home_slots
    if existing:
        return existing
    resolver = resolver or DefaultLayoutResolver()
    return resolver.resolve_slots()


def apply_default_layout(config, gestures=None, resolver: DefaultLayoutResolver | None = None) -> None:
    """One-shot first-boot seeding: slots, renames, hidden clutter, gestures.

    Sets default_layout_applied even when nothing resolved, and never runs
    once the flag is set or home_slots is non-empty.
    """
    if config.default_layout_applied:
        return
    if config.home_slots:
        config.set_default_layout_applied(True)
        return

    resolver = resolver or DefaultLayoutResolver()
    slots = resolver.resolve_slots()
    if slots:
        config.set_home_slots(slots)
    for app_id, label in resolver.labels.items():
        config.set_app_label(app_id, label)

    hidden = config.hidden_apps
    stock = [app for app in STOCK_HIDDEN_APPS if app not in hidden]
    if stock:
        config.set_hidden_apps(hidden + stock)

    if gestures is not None:
        skippy = resolver.resolve_skippy()
        if skippy:
            gestures.set('swipe_left_home', f'launch:{skippy}')
        # Android's seeder binds swipe right to the camera explicitly
        # (unset, the swipe would open the dialer).
        gestures.set('swipe_right_home', 'camera')

    config.set_default_layout_applied(True)


def remap_superseded_home(config, app_index) -> bool:
    """Point home slots and folder members at the XX app that supersedes a
    stock one (GNOME Contacts -> xx-contacts, Calls -> xx-dialer, Chatty ->
    TxxT). Labels stay. Never drops anything: deleting pins that do not
    resolve at startup once erased the whole Comms folder when its stock
    apps were superseded. Returns True when a write happened."""
    replacement_for = getattr(app_index, 'replacement_for', None)
    if not callable(replacement_for):
        return False

    def remap(item: dict) -> dict:
        new_id = replacement_for(item.get('app_id') or '')
        return dict(item, app_id=new_id) if new_id else item

    current = list(config.home_slots)
    remapped = []
    for slot in current:
        if slot.get('type') == 'folder':
            slot = dict(slot, folder=[remap(m) for m in (slot.get('folder') or [])])
        elif slot.get('type') == 'app':
            slot = remap(slot)
        remapped.append(slot)
    if remapped != current:
        config.set_home_slots(remapped)
        return True
    return False


