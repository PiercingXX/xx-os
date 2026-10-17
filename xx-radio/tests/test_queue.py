"""Queue model: order, shuffle, repeat, persistence."""

import pytest

from xx_music import engine as e
from xx_music.queue import Queue


def _track(track_id: str, title: str = "") -> e.Track:
    return e.Track(id=track_id, title=title or track_id,
                   artists=(e.ArtistRef("Singer", "ar-1"),))


def _queue(n: int = 4) -> Queue:
    queue = Queue()
    queue.replace([_track(f"tr-{i:03d}") for i in range(1, n + 1)])
    return queue


def test_replace_sets_start_and_resets_shuffle():
    queue = _queue()
    queue.set_shuffle(True)
    assert queue.shuffle
    queue.replace([_track("a"), _track("b")], start=1,
                  origin={"type": "album"})
    assert [t.id for t in queue.tracks] == ["a", "b"]
    assert queue.index == 1
    assert queue.shuffle is False
    assert queue.origin == {"type": "album"}
    assert queue.current().id == "b"


def test_replace_empty_stops():
    queue = Queue()
    queue.replace([])
    assert queue.empty and queue.index == -1 and queue.current() is None


def test_enqueue_dedups_and_play_next():
    queue = _queue(2)
    added = queue.enqueue([_track("tr-001"), _track("tr-003")])
    assert added == 1
    assert [t.id for t in queue.tracks] == ["tr-001", "tr-002", "tr-003"]
    queue.play_at(0)
    queue.enqueue([_track("tr-009")], play_next=True)
    assert [t.id for t in queue.tracks][1] == "tr-009"
    assert queue.current().id == "tr-001"


def test_enqueue_into_empty_starts():
    queue = Queue()
    assert queue.enqueue([_track("a")]) == 1
    assert queue.index == 0


def test_play_at_bounds():
    queue = _queue()
    queue.play_at(2)
    assert queue.current().id == "tr-003"
    with pytest.raises(IndexError):
        queue.play_at(9)


def test_remove_fixes_index():
    queue = _queue(3)
    queue.play_at(1)
    queue.remove(0)
    assert queue.current().id == "tr-002"
    assert queue.index == 0
    queue.remove(0)
    assert queue.current().id == "tr-003"
    queue.remove(0)
    assert queue.empty and queue.index == -1


def test_remove_id_drops_disliked_copies():
    queue = Queue()
    queue.replace([_track("a"), _track("b"), _track("a")], start=1)
    assert queue.remove_id("a") == 2
    assert [t.id for t in queue.tracks] == ["b"]
    assert queue.current().id == "b"
    assert queue.remove_id("missing") == 0


def test_move_keeps_current():
    queue = _queue(3)
    queue.play_at(0)
    queue.move(0, 2)
    assert [t.id for t in queue.tracks] == ["tr-002", "tr-003", "tr-001"]
    assert queue.current().id == "tr-001"
    assert queue.index == 2


def test_shuffle_anchors_current():
    queue = _queue(6)
    queue.play_at(2)
    current = queue.current()
    queue.set_shuffle(True)
    assert queue.shuffle
    assert queue.current() is current
    assert {t.id for t in queue.tracks} == \
        {f"tr-{i:03d}" for i in range(1, 7)}
    queue.set_shuffle(False)
    assert queue.shuffle is False
    assert queue.current() is current


def test_repeat_validation_and_sequencing():
    queue = _queue(2)
    with pytest.raises(ValueError):
        queue.set_repeat("everything")
    queue.play_at(1)
    assert queue.next_index() is None
    queue.set_repeat("all")
    assert queue.next_index() == 0
    queue.set_repeat("one")
    assert queue.next_index() == 1
    queue.set_repeat("none")
    assert queue.next_index() is None


def test_previous_restarts_past_three_seconds():
    queue = _queue(3)
    queue.play_at(1)
    assert queue.previous_index(0.5) == 0
    assert queue.previous_index(10.0) == 1
    queue.play_at(0)
    assert queue.previous_index(0.0) == 0


def test_persistence_round_trip(tmp_path):
    queue = _queue(3)
    queue.play_at(1)
    queue.set_repeat("all")
    path = tmp_path / "queue.json"
    queue.save(path)
    loaded = Queue.load(path)
    assert [t.id for t in loaded.tracks] == [t.id for t in queue.tracks]
    assert loaded.index == 1
    assert loaded.repeat == "all"
    assert loaded.tracks[0].artists[0].name == "Singer"


def test_corrupt_queue_loads_empty(tmp_path):
    path = tmp_path / "queue.json"
    path.write_text("{nope", encoding="utf-8")
    assert Queue.load(path).empty
    assert Queue.load(tmp_path / "absent.json").empty
