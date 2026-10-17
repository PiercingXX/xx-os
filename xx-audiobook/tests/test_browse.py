"""Home shelves, browse views and collections (browse.py),
against the loopback fake server and offline."""

from xx_audiobook import api as api_module
from xx_audiobook import browse
from xx_audiobook import repos
from xx_audiobook import store


def _repos(base):
    db = store.Database.in_memory()
    api = api_module.AudiobookApi(base_url=base, token="tok-1")
    return db, api, repos.LibraryRepository(api, db)


def test_home_keeps_known_non_empty_shelves_in_order(fake_server):
    _db, api, library = _repos(fake_server.api_base)
    home = browse.HomeRepository(api, library)
    assert home.refresh() is None
    shelves = home.shelves()
    assert [s.id for s in shelves] == ["continue_series", "discover"]
    assert shelves[0].label == "Continue the series"
    assert [i.id for i in shelves[0].items] == ["it-2"]


def test_home_offline_reports_and_keeps_nothing():
    _db, api, library = _repos(None)
    home = browse.HomeRepository(api, library)
    assert isinstance(home.refresh(), api_module.NotSignedIn)
    assert home.shelves() == []


def test_series_by_id_in_sequence_order_with_live_progress(fake_server):
    _db, api, library = _repos(fake_server.api_base)
    result = browse.BrowseRepository(api, library).load(
        browse.SERIES, "Wayfarers", 7)
    assert result.error is None
    assert [(b.item.id, b.sequence) for b in result.books] == [("it-1", "1")]
    assert result.books[0].item.position_s > 0


def test_author_offline_falls_back_to_cache_by_name(fake_server):
    db, api, library = _repos(fake_server.api_base)
    library.refresh(store.LibraryQuery())
    offline = repos.LibraryRepository(api_module.AudiobookApi(), db)
    result = browse.BrowseRepository(api_module.AudiobookApi(),
                                     offline).load(
        browse.AUTHOR, "Andy Weir", 13)
    assert isinstance(result.error, api_module.NotSignedIn)
    assert [b.item.id for b in result.books] == ["it-2"]


def test_select_local_orders_series_by_sequence():
    items = [repos.LibraryItem("b", "Two", series="Saga #2"),
             repos.LibraryItem("a", "One", series="Saga #1"),
             repos.LibraryItem("x", "Other", series="Sagas #1"),
             repos.LibraryItem("c", "Ten", series="Saga #10")]
    picked = browse.select_local(browse.SERIES, "saga", items)
    assert [b.item.id for b in picked] == ["a", "b", "c"]
    assert picked[2].sequence == "10"


def test_select_local_matches_one_of_many_narrators():
    items = [repos.LibraryItem("a", "A", narrators="Ann, Bob"),
             repos.LibraryItem("b", "B", narrators="Bobby")]
    picked = browse.select_local(browse.NARRATOR, "Bob", items)
    assert [b.item.id for b in picked] == ["a"]


def test_collections_list_detail_and_offline_memory(fake_server):
    _db, api, library = _repos(fake_server.api_base)
    lists = browse.CollectionsRepository(api, library)
    collections, _playlists, error = lists.lists()
    assert error is None
    assert collections[0].name == "Staff picks"
    books, error = lists.detail("collection", "col-1")
    assert error is None
    assert [b.id for b in books] == ["it-1", "it-2"]
    lists._api = api_module.AudiobookApi()
    again, _p, error = lists.lists()
    assert isinstance(error, api_module.NotSignedIn)
    assert [c.id for c in again] == ["col-1"]
