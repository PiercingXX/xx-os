"""Kill artist: confirm sheet and the menu entries that open it."""

from __future__ import annotations

from xx_music.blocked import confirm_text
from xx_music.engine import Item, ItemKind, Track
from xx_music.ui import common


def targets_for_track(track: Track) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for artist in track.artists or ():
        name = " ".join(str(getattr(artist, "name", "") or "").split())
        aid = str(getattr(artist, "id", "") or "")
        if not name:
            continue
        key = (aid, name.casefold())
        if key not in seen:
            seen.add(key)
            out.append((aid, name))
    return out


def target_for_artist_item(item: Item) -> tuple[str, str] | None:
    if item.kind != ItemKind.ARTIST:
        return None
    name = " ".join(str(item.title or "").split())
    if not name and not item.id:
        return None
    return (str(item.id or ""), name)


def request_kill(ui, artist_id: str = "", artist_name: str = "") -> None:
    aid, name = str(artist_id or ""), str(artist_name or "")
    if not aid and not " ".join(name.split()):
        return
    label = name or aid
    heading, body = confirm_text(label)
    parent = ui.shell.window if ui.shell is not None else None

    def answered(response: str) -> None:
        if response == "kill":
            ui.kill_artist(aid, name)

    if parent is None:
        ui.kill_artist(aid, name)
        return
    common.confirm(parent, heading, body,
                   [("cancel", "Cancel", "outline"),
                    ("kill", f"Kill {label}"[:24], "danger")], answered)
