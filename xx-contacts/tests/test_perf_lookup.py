"""LookupNumber answers from the in-memory index in < 5 ms per call."""

import time

from xx_contacts.phones import LookupIndex


def _big_index(count=5000):
    index = LookupIndex()
    rows = []
    for seq in range(count):
        digits = f"1555{seq:07d}"
        rows.append((f"uid-{seq:05d}", f"Contact {seq}",
                     [(f"+1 555 {seq:07d}", "+" + digits, digits)], ()))
    index.rebuild(rows)
    return index


def test_lookup_over_5000_contacts_under_5ms():
    index = _big_index()
    assert index.contact_count == 5000
    queries = [f"555{seq:07d}" for seq in range(0, 5000, 25)]
    assert len(queries) == 200
    worst = 0.0
    started = time.perf_counter()
    for query in queries:
        tick = time.perf_counter()
        hit = index.lookup(query)
        worst = max(worst, time.perf_counter() - tick)
        assert hit is not None
    elapsed = time.perf_counter() - started
    assert elapsed / len(queries) < 0.005
    assert worst < 0.005
