"""xx-auto custom actions: Like/Dislike from the AppSpec player_actions."""

from __future__ import annotations

from xx_music.appspec import AppSpec
from xx_music.engine import Engine, Track

ACTIONS_IFACE = "io.piercingxx.XXAuto.Actions1"
ACTIONS_PATH = "/org/mpris/MediaPlayer2"

ACTIONS_XML = """<node>
  <interface name="io.piercingxx.XXAuto.Actions1">
    <method name="ListActions"><arg direction="out" type="a(sssb)"/></method>
    <method name="ActivateAction"><arg direction="in" type="s"/></method>
  </interface>
</node>"""


def action_rows(spec: AppSpec, engine: Engine,
                track: Track | None) -> list[tuple[str, str, str, bool]]:
    return [(action.id, action.label, action.icon_name,
             bool(action.enabled(engine, track)))
            for action in spec.player_actions]
