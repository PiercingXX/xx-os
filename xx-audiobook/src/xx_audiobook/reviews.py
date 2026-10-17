"""Book reviews: cached pages, optimistic writes, the not-started gate (M5).

Mirrors data/repo/ReviewRepository.kt (per-item in-memory cache so the
Book screen keeps the last page offline; PUT 403 not_started maps to the
must-start-first gate; posting refreshes the cached detail star line via
on_changed) and ui/detail/ReviewViewModel.kt (compose/edit/delete-own
with optimistic updates rolled back on failure; the up-front gate with
Start listening / Mark finished; "Finished. Rate it?"; admin deletes).
Synchronous; screens use ReviewsState.submit_* from the GTK thread.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from xx_audiobook import api as api_module

NOT_STARTED_MESSAGE = ("Start listening to this book first — "
                       "reviews open once it is started or finished.")
STARS_MESSAGE = "Pick 1 to 5 stars."
SAVE_FAILED_MESSAGE = "Could not save the review."


class WriteOk:
    def __init__(self, review) -> None:
        self.review = review


class WriteNotStarted:
    pass


class WriteFailed:
    def __init__(self, message: str) -> None:
        self.message = message or SAVE_FAILED_MESSAGE


class ReviewRepository:
    """Per-item cached review pages with gated writes."""

    def __init__(self, api, on_changed=None) -> None:
        self._api = api
        self._on_changed = on_changed
        self._lock = threading.Lock()
        self._cache: dict[str, object] = {}

    def cached(self, item_id: str):
        with self._lock:
            return self._cache.get(item_id)

    def refresh(self, item_id: str, page: int = 1) -> tuple:
        try:
            fresh = self._api.reviews(item_id, page)
        except api_module.ApiError as exc:
            return (None, exc)
        with self._lock:
            self._cache[item_id] = fresh
        return (fresh, None)

    def put(self, item_id: str, stars: int, body: str):
        try:
            review = self._api.put_review(
                item_id, api_module.ReviewPut(
                    stars=min(5, max(1, stars)), body=(body or "").strip()))
        except api_module.Http as exc:
            if exc.code == 403 and exc.reason == "not_started":
                return WriteNotStarted()
            return WriteFailed(_message_of(exc))
        except api_module.ApiError as exc:
            return WriteFailed(_message_of(exc))
        self.refresh(item_id)
        self._changed(item_id)
        return WriteOk(review)

    def delete_mine(self, item_id: str) -> tuple:
        try:
            self._api.delete_my_review(item_id)
        except api_module.ApiError as exc:
            return (None, exc)
        self.refresh(item_id)
        self._changed(item_id)
        return (None, None)

    def admin_delete(self, item_id: str, review_id: str) -> tuple:
        try:
            self._api.admin_delete_review(review_id)
        except api_module.ApiError as exc:
            return (None, exc)
        self.refresh(item_id)
        self._changed(item_id)
        return (None, None)

    def _changed(self, item_id: str) -> None:
        if self._on_changed is None:
            return
        try:
            self._on_changed(item_id)
        except Exception:
            pass


def _message_of(error) -> str:
    return str(getattr(error, "message", error) or error)


def with_review(page, review, now_s: float | None = None):
    """Add or replace my review first; recompute the star line locally."""
    stamp = now_s if now_s is not None else time.time()
    mine = api_module.Review(
        id=(review.id or "local"), item_id=review.item_id,
        user=review.user, stars=review.stars, body=review.body,
        created_at=review.created_at,
        updated_at=stamp)
    if page is None:
        page = api_module.ReviewsPage(
            average=float(mine.stars), count=1, page=1, limit=20,
            results=[mine])
        return page
    others = [row for row in page.results
              if row.user.id != mine.user.id]
    grown = len(others) == len(page.results)
    results = [mine] + others
    count = max(len(results), page.count + (1 if grown else 0))
    if count == len(results):
        average = (sum(r.stars for r in results) / len(results)
                   if results else None)
    else:
        average = page.average
    return api_module.ReviewsPage(
        average=average, count=count, page=page.page, limit=page.limit,
        results=results)


def without_review(page, review_id: str):
    if page is None:
        return None
    kept = [row for row in page.results if row.id != review_id]
    count = max(len(kept), page.count + (len(kept) - len(page.results)))
    if count == len(kept):
        average = (sum(r.stars for r in kept) / len(kept) if kept else None)
    else:
        average = page.average
    return api_module.ReviewsPage(
        average=average, count=count, page=page.page, limit=page.limit,
        results=kept)


@dataclass
class ReviewsUi:
    page: object = None
    editing: bool = False
    draft_stars: int = 0
    draft_body: str = ""
    saving: bool = False
    error: str | None = None
    not_started: bool = False
    confirm_delete: object = None
    confirm_delete_mine: bool = False
    me_id: str = ""
    eligible: bool | None = None
    finished_prompt: bool = False
    loading_more: bool = False
    admin: bool = False

    @property
    def mine(self):
        if self.page is None:
            return None
        for row in self.page.results:
            if row.user.id == self.me_id:
                return row
        return None

    @property
    def others(self) -> list:
        if self.page is None:
            return []
        rows = [row for row in self.page.results
                if row.user.id != self.me_id]
        rows.sort(key=lambda r: (
            -(r.updated_at if r.updated_at is not None
              else (r.created_at if r.created_at is not None else 0.0))))
        return rows

    @property
    def gated(self) -> bool:
        return (self.mine is None and not self.editing
                and (self.not_started or self.eligible is False))

    @property
    def show_finished_prompt(self) -> bool:
        return self.finished_prompt and self.mine is None and not self.saving

    @property
    def can_load_more(self) -> bool:
        return self.page is not None and len(self.page.results) < (
            self.page.count or 0)


class ReviewsState:
    """Small state API for the reviews block (mirrors ReviewViewModel)."""

    def __init__(self, item_id: str, repo: ReviewRepository,
                 me_id: str = "", is_admin: bool = False,
                 mark_finished=None, now_s=None, listener=None,
                 load: bool = True) -> None:
        self._item_id = item_id
        self._repo = repo
        self._mark_finished = mark_finished
        self._now_s = now_s if now_s is not None else time.time
        self._lock = threading.Lock()
        # Called (any thread) after every state change, so a screen can
        # paint the optimistic state before the write answers.
        self.listener = listener
        self._ui = ReviewsUi(page=repo.cached(item_id), me_id=me_id,
                             admin=is_admin)
        if load:
            self.refresh()

    @property
    def item_id(self) -> str:
        return self._item_id

    @property
    def is_admin(self) -> bool:
        with self._lock:
            return self._ui.admin

    def ui(self) -> ReviewsUi:
        with self._lock:
            ui = self._ui
            return ReviewsUi(
                page=ui.page, editing=ui.editing,
                draft_stars=ui.draft_stars, draft_body=ui.draft_body,
                saving=ui.saving, error=ui.error,
                not_started=ui.not_started,
                confirm_delete=ui.confirm_delete,
                confirm_delete_mine=ui.confirm_delete_mine,
                me_id=ui.me_id, eligible=ui.eligible,
                finished_prompt=ui.finished_prompt,
                loading_more=ui.loading_more, admin=ui.admin)

    def resolve_me(self, me_id: str, is_admin: bool) -> None:
        with self._lock:
            if me_id:
                self._ui.me_id = me_id
            self._ui.admin = bool(is_admin)

    def _set(self, ui: ReviewsUi) -> None:
        with self._lock:
            self._ui = ui
        listener = self.listener
        if listener is not None:
            try:
                listener()
            except Exception:
                pass

    def _current(self) -> ReviewsUi:
        with self._lock:
            return self._ui

    def refresh(self) -> tuple:
        page, error = self._repo.refresh(self._item_id)
        ui = self._current()
        if error is None:
            self._set(ReviewsUi(
                page=page, editing=ui.editing,
                draft_stars=ui.draft_stars, draft_body=ui.draft_body,
                saving=ui.saving, error=ui.error,
                not_started=ui.not_started,
                confirm_delete=ui.confirm_delete,
                confirm_delete_mine=ui.confirm_delete_mine,
                me_id=ui.me_id, eligible=ui.eligible,
                finished_prompt=ui.finished_prompt,
                loading_more=ui.loading_more, admin=ui.admin))
        elif ui.page is None:
            self._set(ReviewsUi(
                page=None, editing=ui.editing,
                draft_stars=ui.draft_stars, draft_body=ui.draft_body,
                saving=ui.saving, error=_message_of(error),
                not_started=ui.not_started,
                confirm_delete=ui.confirm_delete,
                confirm_delete_mine=ui.confirm_delete_mine,
                me_id=ui.me_id, eligible=ui.eligible,
                finished_prompt=ui.finished_prompt,
                loading_more=ui.loading_more, admin=ui.admin))
        return (page, error)

    def set_eligibility(self, started_or_finished: bool) -> None:
        ui = self._current()
        if ui.eligible == started_or_finished:
            return
        self._set(ReviewsUi(
            page=ui.page, editing=ui.editing,
            draft_stars=ui.draft_stars, draft_body=ui.draft_body,
            saving=ui.saving, error=ui.error,
            not_started=(False if started_or_finished
                         else ui.not_started),
            confirm_delete=ui.confirm_delete,
            confirm_delete_mine=ui.confirm_delete_mine, me_id=ui.me_id,
            eligible=started_or_finished,
            finished_prompt=ui.finished_prompt,
            loading_more=ui.loading_more, admin=ui.admin))

    def start_editing(self) -> None:
        ui = self._current()
        mine = ui.mine
        if mine is None and ui.eligible is False:
            self._set(ReviewsUi(
                page=ui.page, editing=False,
                draft_stars=ui.draft_stars, draft_body=ui.draft_body,
                saving=ui.saving, error=None, not_started=True,
                confirm_delete=ui.confirm_delete,
                confirm_delete_mine=ui.confirm_delete_mine,
                me_id=ui.me_id, eligible=ui.eligible,
                finished_prompt=ui.finished_prompt,
                loading_more=ui.loading_more, admin=ui.admin))
            return
        self._set(ReviewsUi(
            page=ui.page, editing=True,
            draft_stars=(mine.stars if mine else ui.draft_stars),
            draft_body=(mine.body if mine else ui.draft_body),
            saving=ui.saving, error=None, not_started=False,
            confirm_delete=ui.confirm_delete,
            confirm_delete_mine=ui.confirm_delete_mine, me_id=ui.me_id,
            eligible=ui.eligible, finished_prompt=False,
            loading_more=ui.loading_more, admin=ui.admin))

    def start_with_stars(self, stars: int) -> None:
        self.start_editing()
        if self._current().editing:
            self.set_stars(stars)

    def mark_finished_and_edit(self) -> tuple:
        if self._mark_finished is None:
            return (None, api_module.ApiError("Nothing marks finish."))
        try:
            self._mark_finished()
        except Exception as exc:
            ui = self._current()
            self._set(_replace(ui, error=_message_of(exc)))
            return (None, exc)
        ui = self._current()
        self._set(_replace(ui, eligible=True, not_started=False))
        self.start_editing()
        return (None, None)

    def offer_finished_prompt(self) -> None:
        ui = self._current()
        if ui.mine is not None:
            return
        self._set(_replace(ui, finished_prompt=True, eligible=True,
                           not_started=False, error=None))

    def dismiss_finished_prompt(self) -> None:
        self._set(_replace(self._current(), finished_prompt=False))

    def post_from_prompt(self, stars: int, body: str | None = None) -> None:
        ui = self._current()
        self._set(_replace(
            ui, draft_stars=min(5, max(1, stars)),
            draft_body=(body if body is not None else ui.draft_body)))
        self.save()

    def load_more(self) -> tuple:
        ui = self._current()
        if ui.page is None or ui.loading_more or not ui.can_load_more:
            return (None, None)
        limit = ui.page.limit if ui.page.limit > 0 else 1
        nxt = len(ui.page.results) // limit + 1
        self._set(_replace(ui, loading_more=True))
        page, error = self._repo.refresh(self._item_id, nxt)
        ui = self._current()
        if error is not None:
            self._set(_replace(ui, loading_more=False,
                               error=_message_of(error)))
            return (None, error)
        seen = {row.id for row in (ui.page.results if ui.page else [])}
        extra = [row for row in page.results if row.id not in seen]
        base = ui.page if ui.page is not None else page
        merged = api_module.ReviewsPage(
            average=page.average, count=page.count, page=page.page,
            limit=page.limit, results=list(base.results) + extra)
        self._set(_replace(ui, page=merged, loading_more=False))
        return (merged, None)

    def cancel_editing(self) -> None:
        self._set(_replace(self._current(), editing=False, error=None))

    def set_stars(self, stars: int) -> None:
        self._set(_replace(self._current(),
                           draft_stars=min(5, max(1, stars))))

    def set_body(self, body: str) -> None:
        self._set(_replace(self._current(), draft_body=body))

    def save(self) -> None:
        before = self._current()
        if before.saving:
            return
        if before.draft_stars not in (1, 2, 3, 4, 5):
            self._set(_replace(before, error=STARS_MESSAGE))
            return
        existing = before.mine
        optimistic = api_module.Review(
            id=(existing.id if existing else "local"),
            item_id=self._item_id,
            user=api_module.ReviewUser(
                id=before.me_id, display_name="You"),
            stars=before.draft_stars, body=before.draft_body.strip(),
            created_at=(existing.created_at if existing else None),
            updated_at=self._now_s())
        self._set(_replace(
            before, page=with_review(before.page, optimistic,
                                    self._now_s()),
            editing=False, saving=True, error=None, not_started=False))
        result = self._repo.put(self._item_id, optimistic.stars,
                                optimistic.body)
        if isinstance(result, WriteOk):
            page, _error = self._repo.refresh(self._item_id)
            ui = self._current()
            self._set(_replace(
                ui, page=(page if page is not None
                          else with_review(before.page, result.review,
                                           self._now_s())),
                saving=False, finished_prompt=False, eligible=True))
        elif isinstance(result, WriteNotStarted):
            self._set(_replace(before, saving=False, editing=True,
                               not_started=True))
        else:
            self._set(_replace(before, saving=False, editing=True,
                               error=result.message))

    def mark_finished_and_save(self) -> None:
        if self._mark_finished is None:
            self._set(_replace(self._current(),
                               error=SAVE_FAILED_MESSAGE))
            return
        try:
            self._mark_finished()
        except Exception as exc:
            self._set(_replace(self._current(),
                               error=_message_of(exc)))
            return
        ui = self._current()
        self._set(_replace(ui, not_started=False))
        self.save()

    def dismiss_not_started(self) -> None:
        self._set(_replace(self._current(), not_started=False))

    def not_started_message(self) -> str:
        return NOT_STARTED_MESSAGE

    def request_delete_mine(self) -> None:
        if self._current().mine is not None:
            self._set(_replace(self._current(),
                               confirm_delete_mine=True))

    def cancel_delete_mine(self) -> None:
        self._set(_replace(self._current(), confirm_delete_mine=False))

    def delete_mine(self) -> tuple:
        before = _replace(self._current(), confirm_delete_mine=False)
        mine = before.mine
        if mine is None:
            return (None, None)
        self._set(_replace(before, page=without_review(before.page,
                                                      mine.id),
                           editing=False, draft_stars=0, draft_body=""))
        _result, error = self._repo.delete_mine(self._item_id)
        ui = self._current()
        if error is None:
            page, _refresh_error = self._repo.refresh(self._item_id)
            self._set(_replace(
                ui, page=(page if page is not None else ui.page)))
        else:
            self._set(_replace(before, error=_message_of(error)))
        return (None, error)

    def request_delete(self, review) -> None:
        if not self._current().admin:
            return
        self._set(_replace(self._current(), confirm_delete=review))

    def cancel_delete(self) -> None:
        self._set(_replace(self._current(), confirm_delete=None))

    def confirm_delete(self) -> tuple:
        before = self._current()
        target = before.confirm_delete
        if target is None:
            return (None, None)
        self._set(_replace(
            before, page=without_review(before.page, target.id),
            confirm_delete=None))
        _result, error = self._repo.admin_delete(self._item_id, target.id)
        ui = self._current()
        if error is None:
            page, _refresh_error = self._repo.refresh(self._item_id)
            self._set(_replace(
                ui, page=(page if page is not None else ui.page)))
        else:
            self._set(_replace(before, confirm_delete=None,
                               error=_message_of(error)))
        return (None, error)

    def submit_save(self, on_done=None):
        return api_module.submit(self.save, on_done)

    def submit_delete_mine(self, on_done=None):
        return api_module.submit(self.delete_mine, on_done)

    def submit_confirm_delete(self, on_done=None):
        return api_module.submit(self.confirm_delete, on_done)

    def submit_load_more(self, on_done=None):
        return api_module.submit(self.load_more, on_done)


def _replace(ui: ReviewsUi, **changes) -> ReviewsUi:
    values = dict(
        page=ui.page, editing=ui.editing, draft_stars=ui.draft_stars,
        draft_body=ui.draft_body, saving=ui.saving, error=ui.error,
        not_started=ui.not_started, confirm_delete=ui.confirm_delete,
        confirm_delete_mine=ui.confirm_delete_mine, me_id=ui.me_id,
        eligible=ui.eligible, finished_prompt=ui.finished_prompt,
        loading_more=ui.loading_more, admin=ui.admin)
    values.update(changes)
    return ReviewsUi(**values)
