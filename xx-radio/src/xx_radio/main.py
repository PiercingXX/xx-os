"""XX-Radio entry point: the xx_music core with the YouTube Music engine.

Everything app-specific lives in SPEC. The window, pages, player service
and cache come from xx_music and are shared with any other app built on
the same core.
"""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio  # noqa: E402

from xx_music import engine as music  # noqa: E402
from xx_music.appspec import AppSpec, PlayerAction  # noqa: E402
from xx_radio import __app_id__  # noqa: E402
from xx_radio.youtube import engine as youtube  # noqa: E402


def _rate(rating: music.Rating):
    def handler(engine: music.Engine, track: music.Track | None) -> None:
        if track is not None:
            engine.rate(track.id, rating)
    return handler


def _can_rate(engine: music.Engine, track: music.Track | None) -> bool:
    return track is not None and engine.supports(music.RATE)


SPEC = AppSpec(
    app_id=__app_id__,
    name="XX-Radio",
    dir_name="xx-radio",
    mpris_suffix="xx_radio",
    engine_factory=youtube.create,
    engine_env="XX_RADIO_ENGINE",
    engine_choices={
        "fake": "xx_music.fake_engine:create",
        "youtube": "xx_radio.youtube.engine:create",
    },
    # Thumbs on xx-auto's drive screen and in Now Playing. The icons ship
    # with xx-auto; without them the buttons show their labels.
    player_actions=(
        PlayerAction("like", "Like", "xx-auto-thumb-up-symbolic",
                     _rate(music.Rating.LIKE), _can_rate),
        PlayerAction("dislike", "Dislike", "xx-auto-thumb-down-symbolic",
                     _rate(music.Rating.DISLIKE), _can_rate),
    ),
)


class RadioApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=SPEC.app_id,
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is not None:
            window.present()
            return
        from xx_music.ui.shell import build_window

        SPEC.paths().ensure_app_dirs()
        build_window(self, SPEC).present()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--player-service" in args:
        from xx_music.player import service as service_mod

        return service_mod.main(SPEC)
    app = RadioApplication()
    return app.run([sys.argv[0], *args])


if __name__ == "__main__":
    raise SystemExit(main())
