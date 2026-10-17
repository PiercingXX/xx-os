"""Long-press menus for songs and for albums, artists and playlists.

Entries follow the engine's capabilities: a missing capability hides
its entry (never greyed out).
"""

from __future__ import annotations

from xx_music.engine import (RADIO, STREAM, Item, ItemKind, Rating,
                             Track)
from xx_music.ui import common


def download_payload(kind: str, item_id: str, title: str = "",
                     track: Track | None = None) -> dict:
    payload = {"kind": kind, "id": item_id, "title": title}
    if track is not None:
        payload["track"] = track.to_dict()
    return payload


def track_menu(ui, track: Track, queue_index: int | None = None):
    """Play next, Add to queue, Start radio, Go to artist/album,
    Download / Remove download, Like, Dislike (and, from the queue,
    Remove and Move up/down)."""
    player = ui.player
    shell = ui.shell
    entries: list = []
    if queue_index is None:
        entries += [
            ("media-skip-forward-symbolic", "Play next",
             lambda: (player.enqueue([track], True),
                      common.toast(ui, "Plays next"))),
            ("list-add-symbolic", "Add to queue",
             lambda: (player.enqueue([track], False),
                      common.toast(ui, "Added to the queue"))),
        ]
    else:
        entries += [
            ("media-skip-forward-symbolic", "Play next",
             lambda: player.move(queue_index, max(player.index + 1, 0))),
            ("go-up-symbolic", "Move up",
             lambda: player.move(queue_index, max(queue_index - 1, 0))),
            ("go-down-symbolic", "Move down",
             lambda: player.move(queue_index, queue_index + 1)),
            ("list-remove-symbolic", "Remove from queue",
             lambda: player.remove(queue_index)),
        ]
    if ui.supports(RADIO):
        entries.append(("media-playlist-shuffle-symbolic", "Start radio",
                        lambda: player.start_radio(track)))
    artist = next((a for a in track.artists if a.id), None)
    album = track.album if track.album and track.album.id else None
    if artist is not None or album is not None:
        entries.append(None)
    if artist is not None:
        entries.append(("avatar-default-symbolic", "Go to artist",
                        lambda: shell.open_artist(artist.id, artist.name)))
    if album is not None:
        entries.append(("media-optical-symbolic", "Go to album",
                        lambda: shell.open_album(album.id, album.title)))
    if ui.supports(STREAM):
        entries.append(None)
        if track.id in ui.downloaded:
            entries.append(("user-trash-symbolic", "Remove download",
                            lambda: (player.remove_download(track.id),
                                     common.toast(ui, "Download removed"))))
        else:
            entries.append(("folder-download-symbolic", "Download",
                            lambda: (player.download([download_payload(
                                "track", track.id, track.title, track)]),
                                common.toast(ui, "Downloading"))))
    liked = player.rating(track.id) == Rating.LIKE
    disliked = player.rating(track.id) == Rating.DISLIKE
    entries.append(None)
    entries.append(("xx-music-thumb-up-symbolic",
                    "Remove thumbs up" if liked else "Thumbs up",
                    lambda: ui.rate(track, Rating.LIKE)))
    entries.append(("xx-music-thumb-down-symbolic",
                    "Remove thumbs down" if disliked else "Thumbs down",
                    lambda: ui.rate(track, Rating.DISLIKE)))
    from xx_music.ui.kill import request_kill, targets_for_track

    kills = targets_for_track(track)
    if kills:
        entries.append(None)
        for aid, name in kills:
            entries.append(("user-trash-symbolic", f"Kill {name}"[:32],
                            lambda aid=aid, name=name: request_kill(
                                ui, aid, name)))
    art = common.art(ui, track.art_url, track.title, 48, "r4")
    dialog, _box = common.menu_sheet(ui, track.title, track.artist_line,
                                     art, entries)
    return common.present(dialog, shell.window)


def item_menu(ui, item: Item):
    """Albums and playlists: Play, Shuffle, Add to queue, Download;
    artists: Open, Start radio."""
    from xx_music.ui import pages

    shell = ui.shell
    entries: list = []
    if item.kind in (ItemKind.ALBUM, ItemKind.PLAYLIST):
        entries += [
            ("media-playback-start-symbolic", "Play",
             lambda: pages.play_collection(ui, item, shuffle=False)),
            ("media-playlist-shuffle-symbolic", "Shuffle",
             lambda: pages.play_collection(ui, item, shuffle=True)),
            ("list-add-symbolic", "Add to queue",
             lambda: pages.play_collection(ui, item, enqueue=True)),
        ]
        if ui.supports(STREAM):
            entries.append(("folder-download-symbolic", "Download",
                            lambda: (ui.player.download([download_payload(
                                item.kind.value, item.id, item.title)]),
                                common.toast(ui, "Downloading"))))
    elif item.kind == ItemKind.ARTIST:
        entries.append(("avatar-default-symbolic", "Open artist",
                        lambda: shell.open_item(item)))
        if ui.supports(RADIO):
            entries.append(("media-playlist-shuffle-symbolic", "Start radio",
                            lambda: pages.artist_radio(ui, item.id)))
        from xx_music.ui.kill import request_kill

        entries.append(None)
        entries.append(("user-trash-symbolic", f"Kill {item.title}"[:32],
                        lambda: request_kill(ui, item.id, item.title)))
    if not entries:
        return None
    art = common.item_art(ui, item, 48)
    dialog, _box = common.menu_sheet(ui, item.title, item.subtitle, art,
                                     entries)
    return common.present(dialog, shell.window)
