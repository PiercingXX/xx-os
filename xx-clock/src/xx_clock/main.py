"""xx-clock entry point: Adw.Application window or `--daemon` service.

`xx-clock --daemon` runs the schedule-owning user service (no display).
`xx-clock --tab=alarms` opens that tab (mirrors Actions.EXTRA_TAB).
`XX_CLOCK_LOCAL=1` forces the in-process fallback (tests, screenshots).
"""

from __future__ import annotations

import os
import sys

VALID_TABS = ("clock", "alarms", "timers", "setup")


def tab_from_args(argv: list[str]) -> str | None:
    for arg in argv:
        if arg.startswith("--tab="):
            wanted = arg[len("--tab="):]
            if wanted in VALID_TABS:
                return wanted
    return None


def _local_hooks_for_ui(shell_ref: dict):
    from .coordinator import Hooks

    def _ring_start(entry_id: int, is_alarm: bool) -> bool:
        from . import ringer as ringer_module
        from . import ring_candidates
        shell = shell_ref.get("shell")
        service = shell.service if shell is not None else None
        store = service._local._store if service is not None and service._local else None
        label, candidates = "", []
        if store is not None:
            if is_alarm:
                alarm = store.get_alarm(entry_id)
                if alarm is None:
                    return False
                label, candidates = alarm.label, ring_candidates.ring_candidates(
                    alarm.sound_uri, None, None)
            else:
                timer = store.get_timer(entry_id)
                if timer is None:
                    return False
                label = timer.label
        ring = ringer_module.Ringer()
        started = ring.start(candidates, gradual=False)
        shell_ref["ringer"] = ring
        if shell is not None:
            shell.on_ringing(entry_id, True, "alarm" if is_alarm else "timer", label)
        return started

    def _ring_stop() -> None:
        ring = shell_ref.pop("ringer", None)
        if ring is not None:
            try:
                ring.stop()
            except Exception:
                pass

    def _show_alert(kind: str, entry_id: int) -> None:
        shell = shell_ref.get("shell")
        if shell is None:
            return
        store = shell.service._local._store if shell.service._local else None
        label = ""
        if store is not None:
            item = store.get_alarm(entry_id) if kind == "alarm" else store.get_timer(entry_id)
            label = item.label if item is not None else ""
        shell.on_ringing(entry_id, True, kind, label)

    def _missed(entry_id: int, title: str) -> None:
        from . import notify as notify_module
        try:
            notify_module.Notifier().missed(title)
        except Exception:
            pass

    return Hooks(ring_start=_ring_start, ring_stop=_ring_stop,
                 show_alert=_show_alert, missed=_missed)


class ClockApp:
    def __init__(self, start_tab: str | None = None) -> None:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gio
        from . import __app_id__
        self._Adw, self._Gio = Adw, Gio
        self._start_tab = start_tab if start_tab in VALID_TABS else None
        self._app = Adw.Application(application_id=__app_id__,
                                    flags=Gio.ApplicationFlags.FLAGS_NONE)
        self._app.connect("activate", self._on_activate)
        self.window = None

    def _on_activate(self, app) -> None:
        from . import paths, prefs
        from .service_client import ClockService
        from .ui import shell as shell_module
        paths.ensure_app_dirs()
        settings = prefs.load()
        if self._start_tab:
            settings.last_tab = self._start_tab
        shell_ref: dict = {}
        force_local = os.environ.get("XX_CLOCK_LOCAL") == "1"
        service = ClockService(
            on_state=lambda: self.window.refresh() if self.window else None,
            on_ringing=lambda eid, ringing, kind, label: (
                self.window.on_ringing(eid, ringing, kind, label) if self.window else None),
            local_hooks=_local_hooks_for_ui(shell_ref),
            force_local=force_local,
            defer_daemon=not force_local)
        self.window = shell_module.ClockWindow(app, service, settings)
        app._clock_window = self.window
        shell_ref["shell"] = self.window
        if not force_local:
            service.upgrade_to_daemon()
        if os.environ.get("XX_CLOCK_SEED") == "1":
            self.window.seed_demo()
        ring = os.environ.get("XX_CLOCK_RING", "")
        if ring in ("alarm", "timer"):
            self.window.seed_demo()
            state = {"id": 99, "kind": ring, "label": "Work" if ring == "alarm" else "Pasta"}
            self.window.on_ringing(99, True, ring, state["label"])
        tab = os.environ.get("XX_CLOCK_TAB", "")
        if tab in VALID_TABS:
            self.window.show_tab(tab)
        self.window.present()

    def run(self, argv=None) -> int:
        return self._app.run(argv if argv is not None else [])


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--daemon" in args:
        from .daemon import ClockDaemon
        from . import paths
        paths.ensure_app_dirs()
        return ClockDaemon().run()
    start_tab = tab_from_args(args)
    app_args = [a for a in args if not a.startswith("--tab=")]
    return ClockApp(start_tab=start_tab).run([sys.argv[0], *app_args])


if __name__ == "__main__":
    raise SystemExit(main())
