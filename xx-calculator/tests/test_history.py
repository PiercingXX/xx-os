"""Pure-logic port of HistoryStoreTest.kt from ref/xx-calculator.

Newest-first ordering, the 100-entry cap, corrupt-payload resilience and
field survival across a save/load round-trip — over the same in-memory
KV seam, no Android needed.
"""
from xx_calculator.history import HistoryEntry, HistoryStore


class InMemoryHistoryKv:
    """In-memory HistoryKeyValueStore, mirroring the JVM test fake."""

    def __init__(self):
        self.strings = {}

    def get_string(self, key):
        return self.strings.get(key)

    def put_string(self, key, value):
        if value is None:
            self.strings.pop(key, None)
        else:
            self.strings[key] = value


# ------------------------------------------------------------- round-trip


def test_entries_come_back_newest_first():
    store = HistoryStore(InMemoryHistoryKv())
    store.add("1+2", "3", 1_000)
    store.add("3×4", "12", 2_000)
    store.add("5!", "120", 3_000)
    assert store.list() == [
        HistoryEntry("5!", "120", 3_000),
        HistoryEntry("3×4", "12", 2_000),
        HistoryEntry("1+2", "3", 1_000),
    ]


def test_every_entry_field_survives_the_round_trip():
    store = HistoryStore(InMemoryHistoryKv())
    entry = HistoryEntry("sin(30)", "0.5", 1_756_123_456_789)
    store.add(entry.expression, entry.result, entry.timestamp)
    assert store.list() == [entry]


# ------------------------------------------------------------------- cap


def test_the_one_hundred_entry_cap_drops_the_oldest():
    store = HistoryStore(InMemoryHistoryKv())
    for i in range(HistoryStore.MAX_ENTRIES + 25):
        store.add(f"e{i}", f"{i}", i)
    entries = store.list()
    assert len(entries) == HistoryStore.MAX_ENTRIES
    assert entries[0] == HistoryEntry("e124", "124", 124)
    assert entries[-1] == HistoryEntry("e25", "25", 25)


# -------------------------------------------------------- corrupt payloads


def test_garbage_payload_yields_an_empty_list_instead_of_crashing():
    for garbage in ("not json", "{broken"):
        kv = InMemoryHistoryKv()
        kv.strings[HistoryStore.KEY_ENTRIES] = garbage
        assert HistoryStore(kv).list() == []


def test_empty_json_array_payload_yields_an_empty_list():
    kv = InMemoryHistoryKv()
    kv.strings[HistoryStore.KEY_ENTRIES] = "[]"
    assert HistoryStore(kv).list() == []


def test_missing_or_empty_payload_yields_an_empty_list():
    assert HistoryStore(InMemoryHistoryKv()).list() == []
    kv = InMemoryHistoryKv()
    kv.strings[HistoryStore.KEY_ENTRIES] = ""
    assert HistoryStore(kv).list() == []


# ------------------------------------------------------------------ clear


def test_clear_empties_the_list():
    kv = InMemoryHistoryKv()
    store = HistoryStore(kv)
    store.add("1+1", "2", 42)
    store.clear()
    assert store.list() == []


# ------------------------------------------------------- file persistence


def test_file_store_round_trips_across_instances(tmp_path, monkeypatch):
    from xx_calculator.history import FileHistoryKv

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    first = HistoryStore(FileHistoryKv())
    first.add("2+2", "4", 7)
    second = HistoryStore(FileHistoryKv())
    assert second.list() == [HistoryEntry("2+2", "4", 7)]


def test_file_store_creates_its_directory_from_scratch(tmp_path, monkeypatch):
    from xx_calculator.history import FileHistoryKv

    missing = tmp_path / "nope" / "nothing"
    monkeypatch.setenv("XDG_DATA_HOME", str(missing))
    store = HistoryStore(FileHistoryKv())
    store.add("1+1", "2", 1)
    assert store.list() == [HistoryEntry("1+1", "2", 1)]
