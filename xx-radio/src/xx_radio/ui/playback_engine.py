"""Settings › Playback engine, and the first-run "Set up playback engine".

Both drive ``xx_radio.engine_status.PlaybackEngine`` (and the token
helper set-up in ``xx_radio.setup``) through ``EngineControl``, always on
a worker thread; progress steps come back on the main loop. The screen
names states and steps in plain words: Ready, Token helper not set up,
Updating, Update failed. A failure never changes the working engine,
and the screen says so; the raw error is only in Copy details.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from gi.repository import GLib, Gtk

from xx_music.engine import ComponentState
from xx_music.diagnostics import redact_text
from xx_music.ui import common, css, fmt, minimal
from xx_music.ui.settings import nav_row, section
from xx_radio import engine_status as es
from xx_ui import widgets

STEP_TEXT = {
    es.STEP_UV: "Get the installer",
    es.STEP_VENV: "Make a private engine folder",
    es.STEP_INSTALL: "Download the music libraries",
    es.STEP_SELF_CHECK: "Check the new engine",
    es.STEP_ONLINE_CHECK: "Try one search",
    es.STEP_PROMOTE: "Switch to the new engine",
}
TOKEN_STEP = "token-helper"
STATE_TITLE = {
    es.READY: "Ready",
    es.TOKEN_MISSING: "Token helper not set up",
    es.UPDATING: "Updating",
    es.UPDATE_FAILED: "Update failed",
}
STATE_LINE = {
    es.READY: "Search and playback work. Nothing updates by itself.",
    es.TOKEN_MISSING: "Search works and downloaded songs play. If YouTube "
                      "stops offering audio, set up the token helper.",
    es.UPDATING: "Keep the app open. Music keeps playing.",
    es.UPDATE_FAILED: "Nothing changed: the engine you had still runs.",
}
PACKAGE_TEXT = {
    "ytmusicapi": "Catalog",
    "yt-dlp": "Stream resolver",
    "yt-dlp-ejs": "Challenge scripts",
    "bgutil-ytdlp-pot-provider": "Token plugin",
}


class EngineControl:
    """The engine screen's backend: snapshot and actions, worker only."""

    def __init__(self, engine_dir: Path | str,
                 playback: es.PlaybackEngine | None = None,
                 token_setup: Callable[[], object] | None = None) -> None:
        self.engine_dir = Path(engine_dir)
        self.playback = playback or es.PlaybackEngine(self.engine_dir)
        self._token_setup = token_setup

    def snapshot(self) -> es.Snapshot:
        return self.playback.snapshot()

    def update(self, on_progress: Callable[[str], None]) -> None:
        self.playback.update(on_progress=on_progress)

    def undo(self) -> None:
        self.playback.undo()

    def drop(self) -> None:
        self.playback.drop_to_system()

    def setup_token(self) -> None:
        if self._token_setup is not None:
            self._token_setup()
            return
        from xx_radio import setup

        setup.setup_token_helper(self.engine_dir)


def _run_progress(ui, work: Callable[[Callable[[str], None]], None],
                  on_step: Callable[[str], None], on_done, on_error) -> None:
    """``work(progress)`` on the worker; each step and the outcome come
    back on the main loop."""
    def progress(step: str) -> None:
        GLib.idle_add(lambda: (on_step(step), False)[1])

    ui.io(lambda: work(progress), on_done, on_error)


class StepList:
    """Numbered steps: pending, now (blinking square), done, failed."""

    def __init__(self, steps: list[str]) -> None:
        self.steps = steps
        self.widget = common.vbox(0)
        self.rows: dict[str, tuple] = {}
        for index, step in enumerate(steps):
            row = common.hbox(14)
            row.add_css_class("xx-mu-step")
            num = Gtk.Label(label=str(index + 1))
            num.add_css_class("xx-mu-stepnum")
            num.add_css_class("xx-mu-num")
            if minimal.active():
                num.add_css_class("xx-mm-stepnum")
            num.set_size_request(28, 28)
            num.set_valign(Gtk.Align.CENTER)
            row.append(num)
            text = common.label(STEP_TEXT.get(step, step), "xx-body",
                                lines=2, chars=24)
            text.set_hexpand(True)
            text.set_valign(Gtk.Align.CENTER)
            row.append(text)
            mark = common.hbox(0)
            mark.set_valign(Gtk.Align.CENTER)
            row.append(mark)
            self.rows[step] = (num, text, mark)
            self.widget.append(row)
        self.reset()

    def reset(self) -> None:
        for step in self.steps:
            self._set(step, "pending")

    def _set(self, step: str, state: str) -> None:
        num, text, mark = self.rows[step]
        for cls in ("done", "now", "bad"):
            num.remove_css_class(cls)
        common.clear(mark)
        text.remove_css_class("xx-mu-faint")
        index = self.steps.index(step) + 1
        num.set_text(str(index))
        if state == "done":
            num.add_css_class("done")
            num.set_text("✓")
        elif state == "now":
            num.add_css_class("now")
            mark.append(minimal.text(minimal.RESOLVING, minimal.Tone.STRONG,
                                     15, chars=2)
                        if num.has_css_class("xx-mm-stepnum")
                        else common.busy_square())
        elif state == "bad":
            num.add_css_class("bad")
            num.set_text("×")
        else:
            text.add_css_class("xx-mu-faint")
        num.update_property([Gtk.AccessibleProperty.LABEL],
                            [f"Step {index}, {state}"])

    def at(self, step: str) -> None:
        """Mark ``step`` running and everything before it done."""
        if step not in self.rows:
            return
        reached = False
        for name in self.steps:
            if name == step:
                self._set(name, "now")
                reached = True
            elif not reached:
                self._set(name, "done")
            else:
                self._set(name, "pending")

    def finish(self) -> None:
        for name in self.steps:
            self._set(name, "done")

    def fail(self, step: str) -> None:
        if step in self.rows:
            self._set(step, "bad")


def _state_block(snap: es.Snapshot) -> Gtk.Widget:
    if minimal.active():
        box = common.vbox(4)
        box.append(common.label(STATE_TITLE.get(snap.state, snap.state),
                                "xx-mu-h2", lines=2, chars=18))
        box.append(minimal.text(STATE_LINE.get(snap.state, ""),
                                minimal.Tone.QUIET, 14, wrap=True,
                                chars=30))
        return box
    box = common.vbox(6)
    box.add_css_class("xx-mu-block")
    top = common.hbox(10)
    dot = Gtk.Box()
    dot.add_css_class("xx-mu-state-dot")
    dot.set_size_request(10, 10)
    dot.set_valign(Gtk.Align.CENTER)
    if snap.state == es.READY:
        dot.add_css_class("ok")
    elif snap.state == es.UPDATE_FAILED:
        dot.add_css_class("bad")
    elif snap.state == es.TOKEN_MISSING:
        dot.add_css_class("hollow")
    top.append(dot)
    top.append(common.label(STATE_TITLE.get(snap.state, snap.state),
                            "xx-mu-h2", lines=2, chars=18))
    box.append(top)
    box.append(common.para(STATE_LINE.get(snap.state, ""), "xx-secondary"))
    return box


class EnginePage:
    """The pushed Playback engine page."""

    def __init__(self, ui, control: EngineControl, on_change=None) -> None:
        self.ui = ui
        self.control = control
        self.on_change = on_change
        self.snap: es.Snapshot | None = None
        self.running = ""
        self.error = ""
        self.loading = common.LoadingLine()
        self.body = common.vbox(0)
        self.body.add_css_class("xx-page-body")
        self.body.set_margin_bottom(32)
        root = common.vbox(0)
        root.add_css_class("xx-mu")
        root.append(common.header(ui.shell.pop, "Playback engine"))
        root.append(self.loading)
        root.append(common.scroller(self.body))
        self.widget = root
        self.steps = StepList(list(es.STEPS))
        self.body.append(common.muted("Checking the engine…"))
        self.refresh()

    def refresh(self) -> None:
        self.loading.set_active(True)
        self.ui.io(self.control.snapshot, self._got, self._snap_failed)

    def _snap_failed(self, error) -> None:
        self.loading.set_active(False)
        self.error = str(error)

    def _got(self, snap: es.Snapshot) -> None:
        self.loading.set_active(bool(self.running))
        self.snap = snap
        if self.on_change is not None:
            self.on_change(snap)
        self.paint()

    def paint(self) -> None:
        snap = self.snap
        body = self.body
        common.clear(body)
        if snap is None:
            return
        shown = snap
        if self.running == "update":
            shown = es.Snapshot(es.UPDATING, snap.components, snap.packages,
                                snap.venv_active, snap.has_previous)
        top = _state_block(shown)
        top.set_margin_top(8)
        body.append(top)
        if self.running == "update" or snap.state == es.UPDATE_FAILED:
            body.append(section("Steps"))
            body.append(self.steps.widget)
        if snap.state == es.UPDATE_FAILED and not self.running:
            body.append(self._failure(snap.last_error))
        body.append(self._actions(snap))
        body.append(section("Parts"))
        parts = list(snap.components)
        for index, part in enumerate(parts):
            row = widgets.setting_row(
                part.name, fmt.joined(part.state_text, part.version),
                self._part_mark(part.state), last=index == len(parts) - 1)
            body.append(row)
        if snap.packages:
            body.append(section("Versions"))
            source = "Private engine" if snap.venv_active \
                else "System packages"
            body.append(common.para(
                f"{source} in use."
                + (" The previous one is kept for Undo."
                   if snap.has_previous else ""), "xx-mu-meta"))
            rows = common.vbox(0)
            rows.set_margin_top(6)
            packages = list(snap.packages)
            for index, package in enumerate(packages):
                detail = []
                if package.engine:
                    detail.append(f"private {package.engine}")
                if package.system:
                    detail.append(f"system {package.system}")
                rows.append(widgets.setting_row(
                    PACKAGE_TEXT.get(package.name, package.name),
                    " · ".join(detail) or "Not installed",
                    common.label(package.active or "—",
                                 "xx-time xx-mu-num", xalign=1.0, chars=12),
                    last=index == len(packages) - 1))
            body.append(rows)
        if self.running == "update":
            self.steps.widget.set_visible(True)

    @staticmethod
    def _part_mark(state) -> Gtk.Widget:
        if minimal.active():
            if state == ComponentState.OK:
                return minimal.text("ok", minimal.Tone.QUIET, 13, chars=8)
            if state in (ComponentState.MISSING, ComponentState.BROKEN):
                return minimal.text("fix", minimal.Tone.STRONG, 13, chars=8)
            return minimal.text("—", minimal.Tone.QUIET, 13, chars=8)
        dot = Gtk.Box()
        dot.add_css_class("xx-mu-state-dot")
        dot.set_size_request(8, 8)
        if state == ComponentState.OK:
            dot.add_css_class("ok")
        elif state in (ComponentState.MISSING, ComponentState.BROKEN):
            dot.add_css_class("bad")
        else:
            dot.add_css_class("hollow")
        return dot

    def _failure(self, error: str) -> Gtk.Widget:
        box = common.vbox(8)
        box.set_margin_top(12)
        box.append(common.para("The update stopped.", "xx-secondary"))
        copy = minimal.outline("Copy details", lambda: self._copy(
            error, copy))
        common.fit(copy, 14)
        copy.set_halign(Gtk.Align.START)
        box.append(copy)
        return box

    def _copy(self, error: str, button: Gtk.Widget) -> None:
        self.widget.get_clipboard().set(redact_text(error))
        common.copied(button)

    def _actions(self, snap: es.Snapshot) -> Gtk.Widget:
        box = common.vbox(10)
        box.set_margin_top(18)
        busy = bool(self.running)
        if busy:
            line = common.hbox(10)
            line.append(common.busy_square())
            line.append(common.label(
                {"update": "Updating the engine…",
                 "token": "Setting up the token helper…"}.get(
                     self.running, "Working…"), "xx-mu-meta", chars=30))
            box.append(line)
            return box
        update = minimal.primary(
            "Updating…" if self.running == "update" else (
                "Try again" if snap.state == es.UPDATE_FAILED
                else "Update engine"), self.update)
        common.fit(update, 18)
        update.set_sensitive(not busy)
        box.append(update)
        if snap.state == es.TOKEN_MISSING or self.running == "token":
            token = minimal.outline(
                "Setting up…" if self.running == "token"
                else "Set up token helper", self.setup_token)
            common.fit(token, 20)
            token.set_sensitive(not busy)
            box.append(token)
        row = Gtk.Box.new(Gtk.Orientation.VERTICAL if css.large_text()
                          else Gtk.Orientation.HORIZONTAL, 10)
        row.set_homogeneous(True)
        if snap.has_previous:
            undo = minimal.outline("Undo update", self.undo)
            common.fit(undo, 12)
            undo.set_sensitive(not busy)
            row.append(undo)
        if snap.venv_active:
            system = minimal.outline("Use system", self.drop)
            common.fit(system, 12)
            system.set_sensitive(not busy)
            row.append(system)
        if row.get_first_child() is not None:
            box.append(row)
        return box  # the steps list explains an update as it runs

    # -- actions -----------------------------------------------------------------
    def update(self) -> None:
        if self.running:
            return
        self.running = "update"
        self.steps.reset()
        self.steps.at(es.STEPS[0])
        self.loading.set_active(True)
        self.paint()
        self._failed_step = ""

        def step(name: str) -> None:
            self._failed_step = name
            self.steps.at(name)

        _run_progress(self.ui, self.control.update, step,
                      lambda _r: self._finished(True),
                      lambda e: self._finished(False, e))

    def _finished(self, ok: bool, error=None) -> None:
        if ok:
            self.steps.finish()  # the steps and the new versions say so
        else:
            self.steps.fail(getattr(self, "_failed_step", "") or es.STEPS[0])
        self.running = ""
        self.refresh()

    def setup_token(self) -> None:
        if self.running:
            return
        self.running = "token"
        self.loading.set_active(True)
        self.paint()

        def done(ok: bool, error=None) -> None:
            self.running = ""
            if not ok:
                common.toast(self.ui, "The token helper didn't set up. "
                             "Nothing changed.")
            self.refresh()

        self.ui.io(self.control.setup_token, lambda _r: done(True),
                   lambda e: done(False, e))

    def undo(self) -> None:
        self._simple(self.control.undo)

    def drop(self) -> None:
        self._simple(self.control.drop)

    def _simple(self, fn) -> None:
        self.running = "other"
        self.paint()

        def done(ok: bool) -> None:
            self.running = ""
            if not ok:
                common.toast(self.ui, "That didn't work. Nothing changed.")
            self.refresh()

        self.ui.io(fn, lambda _r: done(True), lambda _e: done(False))


class EngineSection:
    """Settings › Playback engine: one row with the state, opening the
    page."""

    def __init__(self, ui, control: EngineControl) -> None:
        self.ui = ui
        self.control = control
        self.widget = common.vbox(0)
        self.widget.append(section("Playback engine"))
        self.slot = common.vbox(0)
        self.widget.append(self.slot)
        self.subtitle = "Checking…"
        self._row(self.subtitle)
        ui.io(control.snapshot, self.show, lambda _e: self._row(
            "Couldn't check the engine"))
        minimal.listen(self._minimal_changed)

    def _minimal_changed(self) -> None:
        self._row(self.subtitle)

    def _row(self, subtitle: str, mark: Gtk.Widget | None = None) -> None:
        self.subtitle = subtitle
        common.clear(self.slot)
        self.slot.append(nav_row("Playback engine", subtitle, self.open,
                                 last=True))

    def show(self, snap: es.Snapshot) -> None:
        parts = [STATE_TITLE.get(snap.state, snap.state)]
        resolver = next((p for p in snap.packages if p.name == "yt-dlp"),
                        None)
        if resolver is not None and resolver.active:
            parts.append(f"yt-dlp {resolver.active}")
        self._row(" · ".join(parts))

    def open(self) -> None:
        page = EnginePage(self.ui, self.control, self.show)
        self.ui.shell.push(page.widget, "Playback engine")


class SetupScreen:
    """First run: "Set up playback engine", over the whole window.

    Drives ``xx_radio.firstrun.FirstRunState`` (``setup_async`` runs the
    engine set-up on its worker and reports steps on the main loop).
    """

    def __init__(self, ui, state, done) -> None:
        self.ui = ui
        self.first_run = state
        self.done = done
        self.state = "idle"
        self.failed_step = ""
        self.error = ""
        self.loading = common.LoadingLine()
        self.steps = StepList(list(es.STEPS))
        self.body = common.vbox(0)
        self.body.add_css_class("xx-page-body")
        self.body.set_margin_bottom(28)
        root = common.vbox(0)
        root.add_css_class("xx-mu")
        root.add_css_class("xx-mu-firstrun")
        root.append(self.loading)
        root.append(common.scroller(self.body))
        self.actions = common.vbox(10)
        self.actions.add_css_class("xx-page-body")
        self.actions.set_margin_bottom(20)
        self.actions.set_margin_top(10)
        root.append(self.actions)
        self.widget = root
        root.xx_screen = self
        self.paint()

    def paint(self) -> None:
        body = self.body
        common.clear(body)
        mark = common.caps(self.ui.spec.name)
        mark.set_margin_top(28)
        body.append(mark)
        title = {"done": "Ready to play",
                 "failed": "Setup stopped"}.get(
                     self.state, "Set up playback engine")
        head = common.label(title, "xx-mu-h1", lines=3, chars=14)
        head.set_margin_top(10)
        body.append(head)
        text = {
            "idle": "Two open-source libraries, about 40 MB, in a "
                    "private folder. No admin password.",
            "running": "A minute or two. Keep the app open.",
            "done": "The engine works. Search for something to play.",
            "failed": "Nothing on this phone changed. Check the "
                      "connection, then try again.",
        }[self.state]
        line = common.para(text, "xx-secondary")
        line.set_margin_top(12)
        line.set_margin_bottom(18)
        body.append(line)
        body.append(self.steps.widget)
        self.loading.set_active(self.state == "running")
        common.clear(self.actions)
        if self.state == "idle":
            self._button(minimal.primary("Set up", self.start))
            self._button(minimal.quiet("Not now", self.skip))
        elif self.state == "running":
            line = common.hbox(10)
            line.set_margin_top(14)
            line.set_margin_bottom(14)
            line.append(common.busy_square())
            line.append(common.label("Setting up", "xx-mu-meta",
                                     chars=34))
            self.actions.append(line)
        elif self.state == "done":
            self._button(minimal.primary("Start listening",
                                                self.finish))
        else:
            self._button(minimal.primary("Try again", self.start))
            row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 10)
            row.set_homogeneous(True)
            copy = minimal.outline("Copy details",
                                   lambda: self.copy(copy))
            later = minimal.outline("Not now", self.skip)
            for button in (copy, later):
                common.fit(button, 12)
                row.append(button)
            self.actions.append(row)

    def _button(self, button: Gtk.Button) -> None:
        common.fit(button, 20)
        button.set_hexpand(True)
        self.actions.append(button)

    def start(self) -> None:
        self.state = "running"
        self.steps.reset()
        self.steps.at(es.STEPS[0])
        self.failed_step = es.STEPS[0]
        self.paint()

        def step(name: str) -> None:
            self.failed_step = name
            self.steps.at(name)

        self.first_run.setup_async(on_progress=step, on_done=self._ok,
                                   on_error=self._failed)

    def _ok(self, _result) -> None:
        self.state = "done"
        self.steps.finish()
        self.paint()

    def _failed(self, error) -> None:
        self.state = "failed"
        self.error = str(error)
        self.steps.fail(self.failed_step)
        self.paint()

    def copy(self, button: Gtk.Widget | None = None) -> None:
        self.widget.get_clipboard().set(redact_text(self.error))
        if button is not None:
            common.copied(button)

    def skip(self) -> None:
        self.ui.io(lambda: self.ui.store.set_pref("first_run_done", True))
        self.done()

    def finish(self) -> None:
        self.ui.io(lambda: self.ui.store.set_pref("first_run_done", True))
        self.done()


__all__ = ["EngineControl", "EnginePage", "EngineSection", "SetupScreen",
           "StepList"]
