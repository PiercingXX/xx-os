"""Reviews: optimistic writes with rollback, the not-started gate."""

from xx_audiobook import api as api_module
from xx_audiobook import reviews as reviews_module
from xx_audiobook.api import AudiobookApi
from xx_audiobook.reviews import (
    ReviewRepository,
    ReviewsState,
    WriteFailed,
    WriteNotStarted,
    WriteOk,
)


def _repo(server, transport=None, changed=None):
    kwargs = {} if transport is None else {"transport": transport}
    return ReviewRepository(
        AudiobookApi(server.api_base, "tok", **kwargs),
        on_changed=changed)


def _dead_transport(method, url, headers, body, timeout):
    raise api_module.ApiError.Unreachable("off")


def test_refresh_caches_and_survives_offline(fake_server):
    changed = []
    repo = _repo(fake_server, changed=changed.append)
    page, error = repo.refresh("it-1")
    assert error is None
    assert page.count == 2
    assert repo.cached("it-1").count == 2

    offline = ReviewRepository(
        AudiobookApi(fake_server.api_base, "tok",
                     transport=_dead_transport))
    page, error = offline.refresh("it-1")
    assert page is None
    assert isinstance(error, api_module.ApiError.Unreachable)
    assert offline.cached("it-1") is None


def test_put_ok_refreshes_and_notifies(fake_server):
    changed = []
    repo = _repo(fake_server, changed=changed.append)
    result = repo.put("it-1", 5, "  Loved it  ")
    assert isinstance(result, WriteOk)
    assert result.review.stars == 5
    assert result.review.body == "Loved it"
    assert changed == ["it-1"]
    assert repo.cached("it-1") is not None


def test_put_not_started_gate(fake_server):
    repo = _repo(fake_server)
    fake_server.queue("PUT", "/audiobook/api/items/it-1/review", 403,
                      '{"reason": "not_started",'
                      ' "detail": "Start listening first"}')
    result = repo.put("it-1", 5, "Too soon")
    assert isinstance(result, WriteNotStarted)


def test_put_failure_reports_message(fake_server):
    repo = _repo(fake_server)
    fake_server.queue("PUT", "/audiobook/api/items/it-1/review", 500,
                      '{"detail": "boom"}')
    result = repo.put("it-1", 4, "x")
    assert isinstance(result, WriteFailed)
    assert result.message == "boom"


def test_delete_mine_and_admin_delete(fake_server):
    changed = []
    repo = _repo(fake_server, changed=changed.append)
    _result, error = repo.delete_mine("it-1")
    assert error is None
    _result, error = repo.admin_delete("it-1", "rv-2")
    assert error is None
    assert changed == ["it-1", "it-1"]


def _state(server, **kwargs):
    kwargs.setdefault("me_id", "u-9")
    return ReviewsState("it-1", _repo(server), **kwargs)


def _queue_page_with_mine(fake_server, stars=5, body="Loved it",
                          times=2):
    """The fake never persists PUTs: script the GETs after a save."""
    payload = ('{"average": 5.0, "count": 3, "page": 1, "limit": 20,'
               ' "results": [%s, %s,'
               ' {"id": "rv-9", "item_id": "it-1",'
               ' "user": {"id": "u-9", "display_name": "Me"},'
               ' "stars": %d, "body": "%s",'
               ' "created_at": 1790000000.0,'
               ' "updated_at": 1790000000.0}]}'
               % (_review_row("rv-1", "u-1", 5),
                  _review_row("rv-2", "u-2", 4), stars, body))
    for _ in range(times):
        fake_server.queue(
            "GET", "/audiobook/api/items/it-1/reviews", 200, payload)


def test_save_shows_review_at_once(fake_server):
    state = _state(fake_server)
    assert state.ui().mine is None
    _queue_page_with_mine(fake_server)
    state.set_stars(5)
    state.set_body("Loved it")
    state.start_editing()
    state.save()
    ui = state.ui()
    assert ui.saving is False
    assert ui.mine is not None
    assert ui.mine.stars == 5
    assert ui.eligible is True


def test_save_rolls_back_on_not_started(fake_server):
    state = _state(fake_server)
    fake_server.queue("PUT", "/audiobook/api/items/it-1/review", 403,
                      '{"reason": "not_started",'
                      ' "detail": "Start listening first"}')
    state.set_stars(5)
    state.set_body("Too soon")
    state.start_editing()
    state.save()
    ui = state.ui()
    assert ui.not_started is True
    assert ui.editing is True
    assert ui.mine is None
    assert ui.draft_body == "Too soon"


def test_save_rolls_back_on_failure(fake_server):
    state = _state(fake_server)
    fake_server.queue("PUT", "/audiobook/api/items/it-1/review", 500,
                      '{"detail": "boom"}')
    state.set_stars(4)
    state.start_editing()
    state.save()
    ui = state.ui()
    assert ui.mine is None
    assert ui.editing is True
    assert ui.error == "boom"


def test_gate_offers_start_and_mark_finished(fake_server):
    finished = []
    state = _state(fake_server,
                   mark_finished=lambda: finished.append(True))
    state.set_eligibility(False)
    state.start_editing()
    ui = state.ui()
    assert ui.editing is False
    assert ui.gated is True
    assert ui.not_started is True
    assert state.not_started_message() != ""

    state.mark_finished_and_edit()
    ui = state.ui()
    assert finished == [True]
    assert ui.editing is True
    assert ui.eligible is True


def test_finished_prompt_flow(fake_server):
    state = _state(fake_server)
    state.offer_finished_prompt()
    assert state.ui().show_finished_prompt is True
    _queue_page_with_mine(fake_server)
    state.post_from_prompt(5)
    ui = state.ui()
    assert ui.mine is not None
    assert ui.show_finished_prompt is False
    state.dismiss_finished_prompt()
    assert state.ui().finished_prompt is False


def test_stars_required(fake_server):
    state = _state(fake_server)
    state.start_editing()
    state.save()
    assert state.ui().error == reviews_module.STARS_MESSAGE


def test_delete_mine_asks_first(fake_server):
    state = _state(fake_server)
    _queue_page_with_mine(fake_server)
    state.set_stars(5)
    state.start_editing()
    state.save()
    assert state.ui().mine is not None
    state.request_delete_mine()
    assert state.ui().confirm_delete_mine is True
    state.cancel_delete_mine()
    assert state.ui().confirm_delete_mine is False
    state.request_delete_mine()
    _result, error = state.delete_mine()
    assert error is None


def test_admin_delete_asks_first(fake_server):
    state = _state(fake_server, is_admin=False)
    target = state.ui().others[0]
    state.request_delete(target)
    assert state.ui().confirm_delete is None

    admin = _state(fake_server, is_admin=True)
    target = admin.ui().others[0]
    admin.request_delete(target)
    assert admin.ui().confirm_delete is not None
    admin.cancel_delete()
    assert admin.ui().confirm_delete is None
    admin.request_delete(target)
    _result, error = admin.confirm_delete()
    assert error is None
    assert admin.ui().confirm_delete is None


def test_others_newest_first(fake_server):
    state = _state(fake_server)
    ui = state.ui()
    assert ui.mine is None
    assert [r.id for r in ui.others] == ["rv-1", "rv-2"]
    assert ui.can_load_more is False
    assert state.load_more() == (None, None)


def test_mine_found_by_server_id(fake_server):
    state = _state(fake_server, me_id="u-1")
    ui = state.ui()
    assert ui.mine is not None and ui.mine.id == "rv-1"
    assert [r.id for r in ui.others] == ["rv-2"]


def _review_row(review_id, user_id, stars):
    return ('{"id": "%s", "item_id": "it-1",'
            ' "user": {"id": "%s", "display_name": "N"},'
            ' "stars": %d, "body": "",'
            ' "created_at": 1790000000.0,'
            ' "updated_at": 1790000000.0}' % (review_id, user_id, stars))


def test_load_more_merges_without_dupes(fake_server):
    state = _state(fake_server)
    fake_server.queue(
        "GET", "/audiobook/api/items/it-1/reviews", 200,
        '{"average": 4.0, "count": 3, "page": 1, "limit": 20,'
        ' "results": [%s, %s]}' % (_review_row("rv-1", "u-1", 5),
                                   _review_row("rv-2", "u-2", 4)))
    state.refresh()
    assert state.ui().can_load_more is True
    fake_server.queue(
        "GET", "/audiobook/api/items/it-1/reviews", 200,
        '{"average": 4.0, "count": 3, "page": 2, "limit": 20,'
        ' "results": [%s, %s]}' % (_review_row("rv-2", "u-2", 4),
                                   _review_row("rv-3", "u-3", 3)))
    merged, error = state.load_more()
    assert error is None
    assert [r.id for r in merged.results] == ["rv-1", "rv-2", "rv-3"]
