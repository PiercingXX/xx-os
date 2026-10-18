"""Up next, inside Now Playing (the current song sits above it in
Now Playing's compact row). Tapping a row jumps there; More (or
long-press) offers Play next, Move up, Move down and Remove.
"""

from __future__ import annotations

from xx_music.ui import common, menus, minimal


class QueueView:
    def __init__(self, ui) -> None:
        self.ui = ui
        self.body = common.vbox(0)
        self.body.add_css_class("xx-page-body")
        self.body.set_margin_bottom(16)
        self.widget = common.scroller(self.body)
        self._key = None
        ui.player.connect_queue(self.update)
        ui.player.connect(self._maybe)
        minimal.listen(self.update)

    def _maybe(self) -> None:
        key = (self.ui.player.queue_version, self.ui.player.index,
               self.ui.player.playing)
        if key != self._key:
            self.update()

    def update(self) -> None:
        player = self.ui.player
        self._key = (player.queue_version, player.index, player.playing)
        common.clear(self.body)
        upcoming = player.up_next()
        count = len(upcoming)
        if not upcoming:
            return  # nothing queued: no heading, no line
        self.body.append(common.section(f"Up next · {count}", first=True))
        box = common.vbox(0)
        for position, (index, track) in enumerate(upcoming):
            box.append(common.track_row(
                self.ui, track, lambda i=index: player.play_at(i),
                lambda i=index, t=track: menus.track_menu(
                    self.ui, t, queue_index=i),
                offline=track.id in self.ui.downloaded,
                last=position == count - 1, meta=""))
        self.body.append(box)
