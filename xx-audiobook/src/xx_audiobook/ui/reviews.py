"""The Book page's Ratings & reviews block (M5, ui/detail/ReviewsBlock.kt).

The summary (average + stars + count), "Finished. Rate it?" when
offered, Your review (tap a star or Write a review; Edit / Delete once
posted, delete asks first), then everyone else newest first: five, then
"All reviews (N)". Before the book is started or finished the block
explains why reviews are closed and offers Start listening / Mark
finished. Writes are optimistic: the post shows at once and rolls back
if the server refuses. Admins can delete anyone's review (asks first).

State lives in reviews.ReviewsState (built on a worker: it loads the
first page); its listener repaints through GLib.idle_add, so the
optimistic state paints before the write answers.
"""

from __future__ import annotations

from gi.repository import GLib, Gtk

from xx_audiobook import fmt
from xx_audiobook import reviews as reviews_module
from xx_audiobook.ui import common, style
from xx_ui import widgets

COLLAPSED = 5
GATE_LINE = ("Reviews open once you've started the book or marked it "
             "finished.")


def star_picker(stars: int, on_pick) -> Gtk.Box:
    """1-5 as five 48 px targets, each read as 'n of 5 stars'."""
    row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    for n in range(1, 6):
        button = Gtk.Button()
        button.add_css_class("xx-ab-star")
        if n <= stars:
            button.add_css_class("on")
        button.set_child(widgets.icon_image(
            "starred-symbolic" if n <= stars else "non-starred-symbolic",
            22))
        common.name(button, f"{n} of 5 stars"
                    + (", selected" if n == stars else ""))
        button.connect("clicked", lambda _b, n=n: on_pick(n))
        row.append(button)
    return row


def others_total(ui) -> int:
    page = ui.page
    count = page.count if page is not None else 0
    return max(count - (1 if ui.mine is not None else 0), len(ui.others))


def when_text(review) -> str:
    return fmt.day_text(review.updated_at or review.created_at)


class ReviewsBlock:
    def __init__(self, ctx, item_id: str, on_start_listening,
                 on_finished, window_getter) -> None:
        self.ctx = ctx
        self.item_id = item_id
        self._start_listening = on_start_listening
        self._on_finished = on_finished
        self._window = window_getter
        self.state: reviews_module.ReviewsState | None = None
        self.all = False
        self.comment = False
        self._queued = False
        self._eligible: bool | None = None
        self.widget = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self.text = Gtk.TextView()
        self.text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.text.set_accepts_tab(False)
        self.text.set_left_margin(12)
        self.text.set_right_margin(12)
        self.text.set_top_margin(10)
        self.text.set_bottom_margin(10)
        self.text.update_property([Gtk.AccessibleProperty.LABEL],
                                  ["Comment (optional)"])
        # A bounded box: the comment grows to ~6 lines, then scrolls.
        self.text_box = Gtk.ScrolledWindow()
        self.text_box.add_css_class("xx-ab-text")
        self.text_box.set_policy(Gtk.PolicyType.NEVER,
                                 Gtk.PolicyType.AUTOMATIC)
        self.text_box.set_min_content_height(96)
        self.text_box.set_max_content_height(180)
        self.text_box.set_propagate_natural_height(True)
        self.text_box.set_child(self.text)
        self._render()
        self._build_state()

    # -- state --------------------------------------------------------------

    def _build_state(self) -> None:
        ctx, item_id = self.ctx, self.item_id

        def _finish():
            ctx.progress.set_finished(item_id, True)
            ctx.progress.flush()

        def _make():
            return reviews_module.ReviewsState(
                item_id, ctx.reviews, me_id=ctx.me_id,
                is_admin=ctx.is_admin, mark_finished=_finish,
                listener=self._changed)

        def _done(state, error) -> None:
            if error is None and state is not None:
                self.state = state
                if self._eligible is not None:
                    state.set_eligibility(self._eligible)
            self._render()

        ctx.run(_make, _done)

    def _changed(self) -> None:
        """Any thread: repaint once on the GTK thread."""
        if self._queued:
            return
        self._queued = True

        def _paint() -> bool:
            self._queued = False
            self._render()
            return False

        GLib.idle_add(_paint)

    def set_eligibility(self, started_or_finished: bool) -> None:
        self._eligible = started_or_finished
        if self.state is not None:
            self.state.set_eligibility(started_or_finished)

    def resolve_me(self) -> None:
        if self.state is not None:
            self.state.resolve_me(self.ctx.me_id, self.ctx.is_admin)
            self._render()

    def _work(self, call, after=None) -> None:
        """A state call that touches the network: on a worker."""
        def _done(_result, _error) -> None:
            if after is not None:
                after()
            self._render()

        self.ctx.run(call, _done)

    # -- render -------------------------------------------------------------

    def _render(self) -> None:
        box = self.widget
        common.clear(box)
        box.append(common.section("Ratings & reviews"))
        state = self.state
        if state is None:
            box.append(common.muted("Loading reviews…"))
            return
        ui = state.ui()
        page = ui.page
        if page is not None and page.average is not None and page.count > 0:
            box.append(self._summary(page.average, page.count))
        elif page is not None:
            box.append(common.para("No ratings yet", "xx-secondary"))
        if ui.error:
            error = common.para(ui.error, "xx-secondary")
            error.add_css_class("xx-danger-text")
            error.set_margin_top(6)
            box.append(error)
        if ui.show_finished_prompt:
            box.append(self._finished_prompt(ui))
        box.append(self._yours(ui))
        others = ui.others
        shown = others if self.all else others[:COLLAPSED]
        for index, review in enumerate(shown):
            box.append(self._review(review, ui.admin,
                                    last=index == len(shown) - 1))
        total = others_total(ui)
        if not self.all and total > COLLAPSED:
            more = widgets.text_button(f"All reviews ({total})",
                                       self._show_all)
            common.fit(more, 20)
            more.set_halign(Gtk.Align.START)
            box.append(more)
        elif self.all and ui.can_load_more:
            more = widgets.text_button(
                "Loading…" if ui.loading_more else "More reviews",
                lambda: self._work(state.load_more))
            more.set_sensitive(not ui.loading_more)
            common.fit(more, 16)
            more.set_halign(Gtk.Align.START)
            box.append(more)
        if page is not None and not others and ui.mine is None:
            line = common.para("No reviews yet. Be the first.",
                               "xx-secondary")
            line.set_margin_top(12)
            box.append(line)

    def _summary(self, average: float, count: int) -> Gtk.Box:
        row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 14)
        row.set_margin_top(2)
        row.append(common.label(f"{average:.1f}", "xx-ab-figure xx-ab-num",
                                chars=4))
        side = Gtk.Box.new(Gtk.Orientation.VERTICAL, 4)
        side.set_valign(Gtk.Align.CENTER)
        side.append(common.stars(average, 14))
        side.append(common.label(fmt.ratings_count(count), "xx-ab-meta",
                                 chars=14))
        row.append(side)
        row.update_property([Gtk.AccessibleProperty.LABEL], [
            f"Rated {average:.1f} of 5 from {fmt.ratings_count(count)}"])
        return row

    def _buttons(self, items: list, stacked: bool = False) -> Gtk.Box:
        """Side by side at 1x, stacked at large text sizes (or always)."""
        row = Gtk.Box.new(Gtk.Orientation.VERTICAL
                          if stacked or style.large_text()
                          else Gtk.Orientation.HORIZONTAL, 10)
        row.set_homogeneous(True)
        row.set_margin_top(12)
        for widget in items:
            row.append(widget)
        return row

    def _yours(self, ui) -> Gtk.Box:
        state = self.state
        mine = ui.mine
        card = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        card.add_css_class("xx-ab-card")
        card.set_margin_top(14)
        card.append(common.caps(
            "Edit your review" if ui.editing and mine is not None
            else "Your review"))
        if ui.editing:
            if ui.not_started:
                card.append(self._gate_text())
                card.append(self._buttons([
                    common.fit(widgets.primary_button(
                        "Start listening", self._start), 20),
                    common.fit(widgets.outline_button(
                        "Mark finished",
                        lambda: self._work(state.mark_finished_and_save,
                                           self._on_finished)), 20)],
                    stacked=True))
            picker = star_picker(ui.draft_stars, state.set_stars)
            picker.set_margin_top(6)
            card.append(picker)
            head = common.caps("Comment (optional)")
            head.set_margin_top(8)
            head.set_margin_bottom(6)
            card.append(head)
            parent = self.text_box.get_parent()
            if parent is not None:
                parent.remove(self.text_box)
            card.append(self.text_box)
            post = widgets.primary_button(
                "Saving…" if ui.saving else
                "Save changes" if mine is not None else "Post review",
                self._save)
            post.set_sensitive(not ui.saving and 1 <= ui.draft_stars <= 5)
            cancel = widgets.outline_button("Cancel", state.cancel_editing)
            card.append(self._buttons([common.fit(post, 14),
                                       common.fit(cancel, 14)]))
        elif mine is not None:
            row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 10)
            row.set_margin_top(8)
            row.append(common.badge("You"))
            row.append(common.stars(mine.stars, 14))
            row.append(common.label("Saving…" if ui.saving
                                    else when_text(mine), "xx-ab-meta",
                                    chars=12))
            card.append(row)
            for text in fmt.description_paragraphs(mine.body):
                para = common.para(text, "xx-body")
                para.set_margin_top(8)
                card.append(para)
            edit = widgets.outline_button("Edit review",
                                          self._start_editing)
            common.name(edit, "Edit your review")
            delete = widgets.outline_button("Delete", self.ask_delete_mine)
            delete.get_child().add_css_class("xx-danger-text")
            card.append(self._buttons([common.fit(edit, 16),
                                       common.fit(delete, 10)]))
        elif ui.gated:
            card.append(self._gate_text())
            if ui.draft_stars > 0 or ui.draft_body.strip():
                card.append(common.para("Your draft is kept.", "xx-ab-meta"))
            card.append(self._buttons([
                common.fit(widgets.primary_button(
                    "Start listening", self._start), 20),
                common.fit(widgets.outline_button(
                    "Mark finished",
                    lambda: self._work(state.mark_finished_and_edit,
                                       self._on_finished)), 20)],
                stacked=True))
        else:
            hint = common.para("Tap a star to rate it.", "xx-secondary")
            hint.set_margin_top(6)
            card.append(hint)
            picker = star_picker(ui.draft_stars, self._start_with_stars)
            picker.set_margin_top(4)
            card.append(picker)
            write = widgets.outline_button("Write review",
                                           self._start_editing)
            common.name(write, "Write a review")
            common.fit(write, 18)
            write.set_margin_top(8)
            card.append(write)
        return card

    def _gate_text(self) -> Gtk.Label:
        line = common.para(GATE_LINE, "xx-body")
        line.set_margin_top(8)
        return line

    def _finished_prompt(self, ui) -> Gtk.Box:
        state = self.state
        card = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        card.add_css_class("xx-ab-card")
        card.add_css_class("live")
        card.set_margin_top(14)
        head = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        title = common.label("Finished. Rate it?", "xx-title", chars=20)
        title.set_hexpand(True)
        head.append(title)
        head.append(common.icon_button("window-close-symbolic", "Not now",
                                       state.dismiss_finished_prompt))
        card.append(head)
        card.append(star_picker(ui.draft_stars, state.set_stars))
        if ui.not_started:
            card.append(self._gate_text())
        post = widgets.primary_button(
            "Post rating", lambda: self._work(
                lambda: state.post_from_prompt(
                    state.ui().draft_stars, self._draft())))
        post.set_sensitive(1 <= ui.draft_stars <= 5 and not ui.saving)
        card.append(self._buttons([common.fit(post, 14)]))
        return card

    def _review(self, review, admin: bool, last: bool) -> Gtk.Box:
        row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        row.add_css_class("xx-ab-review")
        if last:
            row.add_css_class("last")
        text = Gtk.Box.new(Gtk.Orientation.VERTICAL, 6)
        text.set_hexpand(True)
        head = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 10)
        name = review.user.display_name or "Someone"
        who = common.label(name, "xx-title", chars=16)
        who.set_hexpand(True)
        head.append(who)
        head.append(common.label(when_text(review), "xx-ab-meta",
                                 xalign=1.0, chars=10))
        text.append(head)
        stars = common.stars(review.stars, 12)
        stars.set_halign(Gtk.Align.START)
        text.append(stars)
        for paragraph in fmt.description_paragraphs(review.body):
            text.append(common.para(paragraph, "xx-secondary"))
        row.append(text)
        if admin:
            delete = common.icon_button(
                "user-trash-symbolic", f"Delete review by {name}",
                lambda: self.ask_delete(review))
            delete.set_valign(Gtk.Align.START)
            row.append(delete)
        row.update_property([Gtk.AccessibleProperty.LABEL], [
            f"{name}, {fmt.stars_text(review.stars)}"])
        return row

    # -- actions ------------------------------------------------------------

    def _draft(self) -> str:
        buffer = self.text.get_buffer()
        return buffer.get_text(buffer.get_start_iter(),
                               buffer.get_end_iter(), False)

    def _start(self) -> None:
        if self.state is not None:
            self.state.dismiss_not_started()
        self._start_listening()

    def _start_editing(self) -> None:
        self.state.start_editing()
        self.text.get_buffer().set_text(self.state.ui().draft_body)

    def _start_with_stars(self, stars: int) -> None:
        self.state.start_with_stars(stars)
        self.text.get_buffer().set_text(self.state.ui().draft_body)

    def _save(self) -> None:
        state = self.state
        state.set_body(self._draft())
        self._work(state.save)

    def _show_all(self) -> None:
        self.all = True
        if self.state is not None and self.state.ui().can_load_more:
            self._work(self.state.load_more)
        self._render()

    def ask_delete_mine(self):
        state = self.state
        state.request_delete_mine()
        stars = state.ui().mine.stars if state.ui().mine else 0

        def _answer(response: str) -> None:
            if response == "delete":
                self._work(state.delete_mine)
            else:
                state.cancel_delete_mine()

        return common.confirm(
            self._window(), "Delete your review?",
            f"Your {stars}-star review will be removed for everyone.",
            [("keep", "Keep", "outline"), ("delete", "Delete", "danger")],
            _answer)

    def ask_delete(self, review):
        state = self.state
        state.request_delete(review)
        name = review.user.display_name or "Someone"

        def _answer(response: str) -> None:
            if response == "delete":
                self._work(state.confirm_delete)
            else:
                state.cancel_delete()

        return common.confirm(
            self._window(), "Delete this review?",
            f"{name}'s {review.stars}-star review will be removed for "
            "everyone.",
            [("keep", "Keep", "outline"), ("delete", "Delete", "danger")],
            _answer)
