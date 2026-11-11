"""Battery saver & app standby settings (UI over app_standby).

Saver toggle, auto-on threshold, what the saver does, the freeze timeout
(5/15/30 min or never), per-app never-freeze exemptions picked from the
drawer, the built-in exemptions (read-only), and the live frozen count.
Then Automatic suspend (``suspend_after``: Off / 1 / 5 / 15 min after the
screen goes off, auto_suspend.py) and the Caffeine tile's auto-off
(``caffeine_minutes``).
"""
from __future__ import annotations

import settings_page
import settings_ui as ui

TIMEOUT_CHOICES = ((5, '5 min'), (15, '15 min'), (30, '30 min'),
                   (0, 'Never'))
THRESHOLD_CHOICES = (10, 15, 20, 25)
# (seconds, label) — mirrors auto_suspend.SUSPEND_AFTER_CHOICES.
SUSPEND_CHOICES = ((0, 'Off'), (60, '1 min'), (300, '5 min'),
                   (900, '15 min'))
CAFFEINE_CHOICES = (30, 60, 120, 0)
SUSPEND_NOTE = ('Never during a call, playing media, Caffeine, or an app '
                'keeping the screen on. The phone wakes a minute before '
                'an alarm; where it cannot schedule that wake, an armed '
                'alarm keeps the phone awake.')


def suspend_after(win) -> int:
    try:
        from auto_suspend import suspend_after_secs
        return suspend_after_secs(getattr(win, 'config', None))
    except Exception:
        return 0


def caffeine_minutes(win) -> int:
    try:
        from idle_inhibit import caffeine_minutes as minutes
        return minutes(getattr(win, 'config', None))
    except Exception:
        return 60


def caffeine_label(minutes: int) -> str:
    if minutes <= 0:
        return 'When turned off'
    if minutes % 60 == 0:
        hours = minutes // 60
        return 'After %d hour%s' % (hours, '' if hours == 1 else 's')
    return 'After %d min' % minutes


def _cfg(win, name: str, default):
    try:
        value = getattr(getattr(win, 'config', None), name, default)
    except Exception:
        return default
    return default if value is None else value


def _controller(win):
    ctl = getattr(win, 'standby', None)
    if ctl is not None:
        return ctl
    try:
        from app_standby import get_standby
        return get_standby()
    except Exception:
        return None


def saver_is_on(win) -> bool:
    ctl = _controller(win)
    if ctl is not None:
        try:
            return bool(ctl.saver_effective)
        except Exception:
            pass
    return bool(_cfg(win, 'battery_saver', False))


def frozen_count(win) -> int | None:
    ctl = _controller(win)
    if ctl is None:
        return None
    try:
        return len(ctl.frozen_apps)
    except Exception:
        return None


def exempt_ids(win) -> list:
    try:
        return list(getattr(win.config, 'standby_exempt', None) or ())
    except Exception:
        return []


def display_name(win, app_id: str) -> str:
    try:
        entries = getattr(getattr(win, 'app_index', None), 'entries', None) or ()
        for entry in entries:
            if getattr(entry, 'app_id', None) == app_id:
                try:
                    return win.config.label_for(entry.app_id, entry.name)
                except Exception:
                    return entry.name
    except Exception:
        pass
    return str(app_id)


def _frozen_text(win) -> str:
    count = frozen_count(win)
    if count is None:
        return 'Frozen apps: checking…'
    return '%d app%s frozen now' % (count, '' if count == 1 else 's')


class StandbySection(settings_page._Section):
    key = 'standby'
    title = 'Battery saver'

    def build(self, win) -> list:
        saver = ui.switch_row('Battery saver', _frozen_text(win),
                              saver_is_on(win))
        saver.switch.connect(
            'notify::active',
            lambda sw, _p: self._on_saver_flipped(win, sw))
        win._saver_switch = saver.switch
        win._standby_frozen_label = saver.subtitle_label
        auto = ui.switch_row('Automatic', '',
                             bool(_cfg(win, 'battery_saver_auto', True)))
        auto.switch.connect(
            'notify::active',
            lambda sw, _p: self._on_auto_flipped(win, sw))
        win._saver_auto_switch = auto.switch
        self._auto_row = auto
        self._threshold_box = ui.nav_row(
            'Turn on at', '', lambda r: self._pick_threshold(win, r))
        self._render_threshold(win)
        saver_group = ui.group(
            self.title, [saver, auto, self._threshold_box],
            note='Freezes apps sooner and pauses weather and update checks.')

        self._timeout_box = ui.nav_row(
            'Freeze unused apps', '',
            lambda r: self._pick_timeout(win, r))
        self._render_timeout(win)
        self._exempt_add = ui.nav_row(
            'Never freeze an app…', 'Pick it from the app drawer',
            lambda _r: self._on_add_exempt(win))
        try:
            from app_standby import builtin_exemption_labels
            labels = builtin_exemption_labels()
        except Exception:
            labels = ()
        standby_group = ui.group(
            'App standby', note='Always kept awake: %s.' % ', '.join(labels)
            if labels else None)
        win._standby_exempt_box = standby_group[1]
        self._render_exempt(win)
        return saver_group + standby_group + self._build_suspend(win)

    # -- automatic suspend + caffeine ------------------------------------

    def _build_suspend(self, win) -> list:
        self._suspend_box = ui.nav_row(
            'Suspend when screen is off', '',
            lambda r: self._pick_suspend(win, r))
        self._caffeine_box = ui.nav_row(
            'Caffeine turns off', '',
            lambda r: self._pick_caffeine(win, r))
        self._render_suspend(win)
        return ui.group(
            'Automatic suspend', [self._suspend_box, self._caffeine_box],
            note=SUSPEND_NOTE)

    def _render_suspend(self, win) -> None:
        secs = suspend_after(win)
        try:
            from auto_suspend import suspend_after_label
            label = suspend_after_label(secs)
        except Exception:
            label = 'Off' if secs <= 0 else '%d s' % secs
        ui.set_subtitle(getattr(self, '_suspend_box', None),
                        label if secs <= 0 else 'After %s' % label)
        ui.set_subtitle(getattr(self, '_caffeine_box', None),
                        caffeine_label(caffeine_minutes(win)))

    def _pick_suspend(self, win, anchor) -> None:
        actions = getattr(win, 'item_actions', None)
        if actions is None:
            return
        actions._show_action_menu(
            anchor, 'Suspend after',
            [(label, lambda secs=secs: self._set_suspend(win, secs))
             for secs, label in SUSPEND_CHOICES])

    def _set_suspend(self, win, secs: int) -> None:
        try:
            win.config.set('suspend_after', int(secs))
        except Exception:
            pass
        self._render_suspend(win)

    def _pick_caffeine(self, win, anchor) -> None:
        actions = getattr(win, 'item_actions', None)
        if actions is None:
            return
        actions._show_action_menu(
            anchor, 'Caffeine turns off',
            [(caffeine_label(minutes),
              lambda minutes=minutes: self._set_caffeine(win, minutes))
             for minutes in CAFFEINE_CHOICES])

    def _set_caffeine(self, win, minutes: int) -> None:
        try:
            win.config.set('caffeine_minutes', int(minutes))
        except Exception:
            pass
        self._render_suspend(win)

    def refresh(self, win) -> None:
        self._sync_switches(win)
        self._render_suspend(win)
        self._render_threshold(win)
        self._render_timeout(win)
        self._render_exempt(win)
        self._render_frozen(win)

    def _sync_switches(self, win) -> None:
        win._standby_updating = True
        try:
            switch = getattr(win, '_saver_switch', None)
            if switch is not None:
                switch.set_active(saver_is_on(win))
            auto = getattr(win, '_saver_auto_switch', None)
            if auto is not None:
                auto.set_active(bool(_cfg(win, 'battery_saver_auto', True)))
        finally:
            win._standby_updating = False

    def _on_saver_flipped(self, win, switch) -> None:
        if getattr(win, '_standby_updating', False):
            return
        on = bool(switch.get_active())
        ctl = _controller(win)
        if ctl is not None:
            try:
                ctl.set_saver(on)
            except Exception:
                pass
        try:
            win.config.set_battery_saver(on)
        except Exception:
            pass
        self._render_frozen(win)

    def _on_auto_flipped(self, win, switch) -> None:
        if getattr(win, '_standby_updating', False):
            return
        try:
            win.config.set_battery_saver_auto(bool(switch.get_active()))
        except Exception:
            pass

    def _render_threshold(self, win) -> None:
        pct = _threshold_pct(win)
        ui.set_subtitle(getattr(self, '_threshold_box', None),
                        '%d%% battery' % pct)
        ui.set_subtitle(getattr(self, '_auto_row', None),
                        'Turns on at low battery')

    def _pick_threshold(self, win, anchor) -> None:
        win.item_actions._show_action_menu(
            anchor, 'Turn on at',
            [('%d%%' % pct, lambda pct=pct: self._set_threshold(win, pct))
             for pct in THRESHOLD_CHOICES])

    def _set_threshold(self, win, pct: int) -> None:
        try:
            win.config.set_battery_saver_auto_pct(pct)
        except Exception:
            pass
        self._render_threshold(win)

    def _timeout_label(self, win) -> str:
        minutes = _standby_minutes(win)
        for value, label in TIMEOUT_CHOICES:
            if value == minutes:
                return label
        return '%d min' % minutes

    def _render_timeout(self, win) -> None:
        # The standby delay is a value row opening the choices, always
        # showing the current delay, including the stock 10 min default
        # the menu omits.
        label = self._timeout_label(win)
        ui.set_subtitle(getattr(self, '_timeout_box', None),
                        label if label == 'Never' else 'After %s' % label)

    def _pick_timeout(self, win, anchor) -> None:
        actions = getattr(win, 'item_actions', None)
        if actions is None:
            return
        choices = [(label, lambda minutes=minutes: self._set_timeout(win, minutes))
                   for minutes, label in TIMEOUT_CHOICES]
        actions._show_action_menu(anchor, 'Freeze after', choices)

    def _set_timeout(self, win, minutes: int) -> None:
        try:
            win.config.set_app_standby_minutes(minutes)
        except Exception:
            pass
        self._render_timeout(win)

    def _render_exempt(self, win) -> None:
        box = getattr(win, '_standby_exempt_box', None)
        if box is None:
            return
        rows = [getattr(self, '_timeout_box', None)]
        for app_id in exempt_ids(win):
            rows.append(ui.row(
                display_name(win, app_id), 'Never frozen',
                on_activate=lambda r, aid=app_id: win.item_actions
                ._show_action_menu(
                    r, display_name(win, aid),
                    [('Allow freezing',
                      lambda: self._on_remove_exempt(win, aid)),
                     ('Cancel', lambda: None)])))
        rows.append(getattr(self, '_exempt_add', None))
        ui.fill(box, [row for row in rows if row is not None])

    def _on_add_exempt(self, win) -> None:
        try:
            win._pick_exempt_mode = True
            win.stack.set_visible_child_name('apps')
            win._show_status('Tap an app to never freeze.')
        except Exception:
            pass

    def _on_remove_exempt(self, win, app_id: str) -> None:
        try:
            from app_standby import normalize_app_id
        except ImportError:
            def normalize_app_id(value: str) -> str:
                text = str(value or '').strip()
                if text.lower().endswith('.desktop'):
                    text = text[:-8]
                return text.casefold()
        try:
            wanted = normalize_app_id(app_id)
            kept = [entry for entry in exempt_ids(win)
                    if normalize_app_id(entry) != wanted]
            win.config.set_standby_exempt(kept)
        except Exception:
            pass
        self._render_exempt(win)

    def _render_frozen(self, win) -> None:
        label = getattr(win, '_standby_frozen_label', None)
        if label is not None:
            try:
                label.set_text(_frozen_text(win))
            except Exception:
                pass


def _threshold_pct(win) -> int:
    try:
        from app_standby import saver_auto_threshold
        return saver_auto_threshold(getattr(win, 'config', None))
    except Exception:
        pass
    return int(_cfg(win, 'battery_saver_auto_pct', 15) or 15)


def _standby_minutes(win) -> int:
    try:
        return max(0, int(getattr(win.config, 'app_standby_minutes', 10)))
    except (AttributeError, TypeError, ValueError):
        return 10
