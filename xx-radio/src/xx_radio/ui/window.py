"""The XX-Radio window: the xx_music shell plus this app's own screens.

Adds, through ``xx_music.ui.context.Hooks``: Settings › Account (browser
headers, OAuth, sign out), Settings › Playback engine (state, parts,
versions, Update / Undo / Use system, token helper), the first-run "Set
up playback engine" screen, the banner actions, the About disclaimer and
the license table.
"""

from __future__ import annotations

from xx_music.ui import downloads as downloads_mod
from xx_music.ui.context import FirstRun, Hooks, SettingsSection, Welcome
from xx_radio import __version__, licenses

#: About › the short, honest version of the README's disclaimer.
DISCLAIMER = (
    "Unofficial. xx-radio is not made or approved by YouTube or Google. "
    "It reads YouTube Music through the interfaces of YouTube's own apps "
    "and plays without ads, which YouTube's terms don't allow. A change "
    "on their side can stop search or playback until the engine is "
    "updated. Signing in could get that account limited, so a spare one "
    "is the careful choice. Downloads are for your own listening. Use it "
    "at your own risk.")
SOURCE_NAME = "YouTube"


def make_hooks(spec, engine, manager=None, control=None,
               first_run=None, downloads=None, welcome: bool = True
               ) -> Hooks:
    """Hooks for the shell. ``manager`` is an AccountManager-shaped
    object (default: the engine's own), ``control`` an EngineControl and
    ``first_run`` a ``FirstRunState`` (default: on for the YouTube
    engine only)."""
    from xx_radio import firstrun
    from xx_radio.ui import account, playback_engine

    paths = spec.paths()
    if manager is None:
        manager = getattr(engine, "account", None)
        if not hasattr(manager, "sign_in_with_headers"):
            manager = None
    if manager is None and engine.key == "ytm":
        from xx_radio.account import AccountManager
        from xx_radio.youtube import auth

        manager = AccountManager(auth.LibsecretStore(), paths.runtime_dir())
    if control is None:
        control = playback_engine.EngineControl(paths.engine_dir())
    if first_run is None and engine.key == "ytm":
        first_run = firstrun.FirstRunState(paths.engine_dir())

    sections: dict[str, object] = {}

    def build_account(ui):
        sections["account"] = account.AccountSection(ui, manager)
        return sections["account"].widget

    def build_engine(ui):
        sections["engine"] = playback_engine.EngineSection(ui, control)
        return sections["engine"].widget

    def open_engine(ui) -> None:
        ui.shell.show("settings")
        page = playback_engine.EnginePage(ui, control)
        ui.shell.push(page.widget, "Playback engine")

    def open_sign_in(ui) -> None:
        ui.shell.show("settings")
        section = sections.get("account")
        on_done = section.show if section is not None else (
            lambda state: ui.set_account(state.signed_in,
                                         state.account_name))
        page = account.SignInPage(ui, manager, on_done)
        ui.shell.push(page.widget, "Sign in")

    def needed(ui) -> bool:
        if ui.store.prefs().get("first_run_done"):
            return False
        return first_run.snapshot().state != firstrun.READY

    def welcome_needed(ui) -> bool:
        if ui.store.prefs().get("welcome_done"):
            return False
        try:
            return not manager.state().signed_in
        except Exception:  # noqa: BLE001 - no keyring: still offer it
            return True

    def build_first_run(ui, done):
        return playback_engine.SetupScreen(ui, first_run, done).widget

    def engine_lines(_ui) -> list[str]:
        snap = control.snapshot()
        lines = [f"state {snap.state}"]
        lines += [f"{c.name}: {c.state_text} {c.version}".rstrip()
                  for c in snap.components]
        lines += [f"{p.name}: system {p.system or '-'}, private "
                  f"{p.engine or '-'}" for p in snap.packages]
        return lines

    settings = [SettingsSection("engine", build_engine)]
    if manager is not None:
        settings.insert(0, SettingsSection("account", build_account))
    actions = {"update-engine": open_engine, "setup-helper": open_engine}
    if manager is not None:
        actions["sign-in"] = open_sign_in
    hooks = Hooks(
        source_name=SOURCE_NAME,
        settings=tuple(settings),
        version=__version__,
        disclaimer=DISCLAIMER,
        licenses=lambda: licenses.entries(spec.name),
        first_run=(FirstRun(needed, build_first_run)
                   if first_run is not None else None),
        actions=actions,
        welcome=(Welcome(welcome_needed,
                         lambda ui: account.show_welcome(ui, open_sign_in))
                 if welcome and manager is not None else None),
        downloads=downloads or downloads_mod.default_rows,
        engine_lines=engine_lines,
    )
    hooks.engine_control = control
    hooks.open_sign_in = open_sign_in
    return hooks


def build_window(app, spec, engine=None, **hook_args):
    """The window entry point ``RadioApplication.do_activate`` calls."""
    from xx_music.ui.shell import build_window as core_window

    engine = engine if engine is not None else spec.make_engine()
    player = hook_args.pop("player", None)
    ui_factory = hook_args.pop("ui_factory", None)
    hooks = make_hooks(spec, engine, **hook_args)
    return core_window(app, spec, engine, hooks=hooks, player=player,
                       ui_factory=(lambda s, e: ui_factory(s, e, hooks))
                       if ui_factory else None)
