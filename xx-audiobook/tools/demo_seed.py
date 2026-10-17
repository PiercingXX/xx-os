#!/usr/bin/env python3
"""Seed a HOME with a realistic xx-audiobook library via the real stores.

Usage: python3 xx-audiobook/tools/demo_seed.py OUT_HOME

Writes the item cache, cached details, progress, a finished download
row, bookmarks and the last-played pointer through M1's repositories,
fed by DemoApi: an in-process stand-in for the server (no sockets) that
the snap app also uses for home shelves, browse views, collections and
generated covers. Idempotent.
"""

from __future__ import annotations

import os
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
LANE_ROOT = os.path.dirname(os.path.dirname(TOOLS_DIR))
sys.path.insert(0, os.path.join(LANE_ROOT, "common"))
sys.path.insert(0, os.path.join(LANE_ROOT, "xx-audiobook", "src"))

from xx_audiobook import api as api_module  # noqa: E402

NOW = 1792000000.0
H = 3600.0

# id, title, author, narrator, series (name, id, seq) | None, hours,
# progress fraction (None = not started, 1.0 = finished), cover colour
# (None = no cover art), rating
CATALOG = (
    ("lw", "The Long Way to a Small, Angry Planet", "Becky Chambers",
     "Rachel Dulude", ("Wayfarers", 7, "1"), 14.9, 1.0, "#2f5d62", 4.6),
    ("cc", "A Closed and Common Orbit", "Becky Chambers",
     "Patricia Rodriguez", ("Wayfarers", 7, "2"), 12.3, 0.34, "#8a4b2a", 4.4),
    ("rs", "Record of a Spaceborn Few", "Becky Chambers", "Rachel Dulude",
     ("Wayfarers", 7, "3"), 12.9, None, "#5b5f97", 4.3),
    ("ph", "Project Hail Mary", "Andy Weir", "Ray Porter", None, 16.2,
     0.42, "#c9a227", 4.8),
    ("tm", "The Martian", "Andy Weir", "R.C. Bray", None, 10.9, 1.0,
     "#a33b20", 4.7),
    ("pi", "Piranesi", "Susanna Clarke", "Chiwetel Ejiofor", None, 6.2,
     0.08, None, 4.5),
    ("du", "Dune", "Frank Herbert", "Scott Brick", ("Dune", 3, "1"), 21.0,
     None, "#b5651d", 4.6),
    ("ct", "Children of Time", "Adrian Tchaikovsky", "Mel Hudson",
     ("Children of Time", 4, "1"), 16.5, None, "#3f6e48", 4.5),
    ("lh", "The Left Hand of Darkness", "Ursula K. Le Guin",
     "George Guidall", None, 10.4, 1.0, "#6d7f8c", 4.2),
    ("se", "Station Eleven", "Emily St. John Mandel", "Kirsten Potter",
     None, 10.1, None, None, 4.1),
    ("hy", "Hyperion", "Dan Simmons", "Victor Bevine",
     ("Hyperion Cantos", 5, "1"), 20.9, None, "#4b3f72", 4.4),
    ("nm", "Neuromancer", "William Gibson", "Robertson Dean", None, 10.5,
     None, "#1f4e5f", 4.0),
    ("mb", "All Systems Red", "Martha Wells", "Kevin R. Free",
     ("The Murderbot Diaries", 6, "1"), 3.3, 1.0, "#8c2f39", 4.7),
    ("ac", "Artificial Condition", "Martha Wells", "Kevin R. Free",
     ("The Murderbot Diaries", 6, "2"), 3.7, None, "#355c7d", 4.6),
)
DOWNLOADED = ("ph", "pi")
# The Downloads queue beside the finished ones: (id, state, fraction,
# error). Sizes follow the book length at ~64 kbit/s.
QUEUE = (("du", "Running", 0.42, None),
         ("ct", "Queued", 0.0, None),
         ("nm", "Failed", 0.61, "Checksum mismatch on file 2."))
BYTES_PER_HOUR = 28_800_000
DAMAGED = ("hy",)
# Signed-in demo account (no password anywhere; the token is a dummy).
ME = {"user_id": "u-demo", "username": "sam", "display_name": "Sam Reader",
      "is_admin": True}
ORIGIN = "https://estate.example"
LAST = ("ph", 0.42)
AUTHORS = {name: 100 + i for i, name in enumerate(sorted(
    {row[2] for row in CATALOG}))}
NARRATORS = {name: 200 + i for i, name in enumerate(sorted(
    {row[3] for row in CATALOG}))}
SHELVES = (("continue_series", "Continue the series", ("cc", "ac")),
           ("listen_again", "Listen again", ("tm", "lh", "mb")),
           ("discover", "Discover", ("ct", "nm", "du", "se")))
COLLECTIONS = (("col-hugo", "Hugo winners", "Best Novel, read aloud.",
                ("lh", "du", "hy", "ct", "mb")),
               ("col-staff", "Staff picks", "", ("ph", "pi", "lw", "se")))
PLAYLISTS = (("pl-road", "Road trip", "Long drives.", ("ph", "du", "tm")),)
CHAPTER_NAMES = {
    "ph": ["Astrophage", "The Beetles", "Tau Ceti", "Rocky", "Xenonite",
           "The Hail Mary", "Amaterasu", "Taumoeba", "Erid", "Home"],
    "lw": ["Wayfarer", "Port Coriol", "The Tunnel", "Hedra Ka", "Lucky"],
}
DESCRIPTIONS = {
    # Demo copy only: placeholder text, not publisher blurbs.
    "ph": ("[Book description] A long first paragraph so the About block "
           "wraps and clamps on a 360 px screen.\nA second paragraph, "
           "shown when the block is expanded."),
    "hy": ("[Book description] One short paragraph."),
}


def _rows():
    for (item_id, title, author, narrator, series, hours, frac, colour,
         rating) in CATALOG:
        yield dict(id=item_id, title=title, author=author,
                   narrator=narrator, series=series, hours=hours,
                   frac=frac, colour=colour, rating=rating)


def _progress(row: dict) -> dict | None:
    if row["frac"] is None:
        return None
    duration = row["hours"] * H
    finished = row["frac"] >= 1.0
    stamp = NOW - (0 if row["id"] == LAST[0] else 86400 * (
        3 + CATALOG.index(next(c for c in CATALOG if c[0] == row["id"]))))
    return {"item_id": row["id"],
            "position_s": 0.0 if finished else duration * row["frac"],
            "duration_s": duration, "speed": 1.0, "is_finished": finished,
            "started_at": NOW - 864000, "finished_at": stamp if finished
            else None, "last_played_at": stamp, "updated_at": stamp}


def summary_doc(row: dict) -> dict:
    series = ([{"id": row["series"][1], "name": row["series"][0],
                "sequence": row["series"][2]}] if row["series"] else [])
    cover = (f"api/items/{row['id']}/cover?size=thumb"
             if row["colour"] else None)
    return {
        "id": row["id"], "title": row["title"], "subtitle": "",
        "authors": [row["author"]], "narrators": [row["narrator"]],
        "series": series, "duration_s": row["hours"] * H, "status": "ok",
        "added_at": NOW - 86400 * len(row["id"]) * 7,
        "cover_url": cover, "thumb_url": cover,
        "progress": _progress(row),
        "rating": {"average": row["rating"], "count": 12, "mine": None},
        "author_refs": [{"id": AUTHORS[row["author"]],
                         "name": row["author"]}],
        "narrator_refs": [{"id": NARRATORS[row["narrator"]],
                           "name": row["narrator"]}],
    }


def detail_doc(row: dict) -> dict:
    duration = row["hours"] * H
    names = CHAPTER_NAMES.get(row["id"])
    count = len(names) * 3 if names else max(int(row["hours"] * 1.8), 4)
    step = duration / count
    chapters = []
    for index in range(count):
        if names:
            title = f"{index + 1}. {names[index % len(names)]}"
        else:
            title = f"Chapter {index + 1}"
        chapters.append({"index": index, "title": title,
                         "start_s": index * step,
                         "end_s": (index + 1) * step})
    files = [{"file_id": f"{row['id']}-f{i}", "index": i,
              "url": f"api/items/{row['id']}/files/{row['id']}-f{i}",
              "start_offset_s": i * duration / 2,
              "duration_s": duration / 2, "mime": "audio/mp4",
              "size": 1000, "sha256": ""} for i in range(2)]
    return dict(summary_doc(row), **{
        "description": DESCRIPTIONS.get(
            row["id"], f"{row['title']}, read by {row['narrator']}."),
        "publisher": "", "published_year": "2014", "language": "en",
        "genres": ["sci-fi"], "asin": "", "isbn": "", "explicit": False,
        "abridged": False, "size_bytes": 2000, "file_count": 2,
        "updated_at": NOW, "chapters": chapters, "files": files,
        "playable": row["id"] not in DAMAGED,
        "issues": ["missing_file"] if row["id"] in DAMAGED else []})


BY_ID = {row["id"]: row for row in _rows()}


class DemoApi:
    """Duck-typed AudiobookApi answering from CATALOG (no network)."""

    def __init__(self) -> None:
        self._covers: dict = {}
        self.marks = [api_module.Bookmark(
            "bm-1", "ph", 4120.0, "Rocky's first words", NOW - 4000,
            NOW - 4000), api_module.Bookmark(
            "bm-2", "ph", 12650.0, "", NOW - 2000, NOW - 2000)]

    def _summaries(self, ids) -> list:
        return [api_module.ItemSummary.from_dict(summary_doc(BY_ID[i]))
                for i in ids]

    def items(self, query=None):
        rows = [summary_doc(r) for r in BY_ID.values()]
        if query is not None and query.q:
            needle = query.q.lower()
            rows = [r for r in rows if needle in (
                r["title"] + " ".join(r["authors"])).lower()]
        page = query.page if query else 1
        limit = query.limit if query else 50
        chunk = rows[(page - 1) * limit:page * limit]
        return api_module.ItemsPage.from_dict(
            {"total": len(rows), "page": page, "limit": limit,
             "results": chunk})

    def item(self, item_id: str):
        if item_id not in BY_ID:
            raise api_module.Http(404, None, "no such book")
        return api_module.ItemDetail.from_dict(detail_doc(BY_ID[item_id]))

    def continue_listening(self) -> list:
        return self._summaries(
            [r["id"] for r in BY_ID.values()
             if r["frac"] is not None and r["frac"] < 1.0])

    def home(self):
        return api_module.Home.from_dict({"shelves": [
            {"id": sid, "label": label, "kind": "items",
             "results": [summary_doc(BY_ID[i]) for i in ids]}
            for sid, label, ids in SHELVES]})

    def series(self, series_id: int):
        rows = [r for r in BY_ID.values()
                if r["series"] and r["series"][1] == series_id]
        if not rows:
            raise api_module.Http(404, None, "no such series")
        return api_module.SeriesDetail.from_dict({
            "id": series_id, "name": rows[0]["series"][0],
            "book_count": len(rows), "duration_s": 0.0,
            "books": [dict(summary_doc(r), sequence=r["series"][2])
                      for r in rows]})

    def _person(self, field: str, refs: dict, person_id: int) -> dict:
        name = next(n for n, i in refs.items() if i == person_id)
        rows = [r for r in BY_ID.values() if r[field] == name]
        series = {}
        for row in rows:
            if row["series"]:
                series.setdefault(row["series"][1], [row["series"][0], 0])
                series[row["series"][1]][1] += 1
        return {"id": person_id, "name": name, "book_count": len(rows),
                "asin": "", "description": "", "image_url": None,
                "books": [summary_doc(r) for r in rows],
                "series": [{"id": sid, "name": n, "book_count": c}
                           for sid, (n, c) in series.items()]}

    def author(self, author_id: int):
        return api_module.AuthorDetail.from_dict(
            self._person("author", AUTHORS, author_id))

    def narrator(self, narrator_id: int):
        return api_module.NarratorDetail.from_dict(
            self._person("narrator", NARRATORS, narrator_id))

    def _card(self, entry) -> dict:
        list_id, name, description, ids = entry
        return {"id": list_id, "name": name, "description": description,
                "book_count": len(ids), "updated_at": NOW,
                "thumb_urls": [f"api/items/{i}/cover?size=thumb"
                               for i in ids if BY_ID[i]["colour"]][:4]}

    def collections(self) -> list:
        return [api_module.ListCard.from_dict(self._card(c))
                for c in COLLECTIONS]

    def playlists(self) -> list:
        return [api_module.ListCard.from_dict(self._card(p))
                for p in PLAYLISTS]

    def _list(self, entries, list_id: str):
        for entry in entries:
            if entry[0] == list_id:
                doc = self._card(entry)
                doc["books"] = [summary_doc(BY_ID[i]) for i in entry[3]]
                return api_module.ListDetail.from_dict(doc)
        raise api_module.Http(404, None, "no such list")

    def collection(self, list_id: str):
        return self._list(COLLECTIONS, list_id)

    def playlist(self, list_id: str):
        return self._list(PLAYLISTS, list_id)

    def bookmarks(self, item_id=None) -> list:
        return [m for m in self.marks if item_id in (None, m.item_id)]

    def add_bookmark(self, body):
        mark = api_module.Bookmark(f"bm-{len(self.marks) + 1}",
                                   body.item_id, body.position_s, body.note,
                                   NOW, NOW)
        self.marks.append(mark)
        return mark

    def edit_bookmark(self, bookmark_id, body):
        for mark in self.marks:
            if mark.id == bookmark_id:
                mark.note = body.note if body.note is not None else mark.note
                return mark
        raise api_module.Http(404, None, "gone")

    def delete_bookmark(self, bookmark_id) -> None:
        self.marks = [m for m in self.marks if m.id != bookmark_id]

    def progress(self, item_id: str):
        doc = _progress(BY_ID[item_id]) if item_id in BY_ID else None
        return api_module.Progress.from_dict(doc) if doc else None

    def all_progress(self) -> list:
        return [p for p in (self.progress(i) for i in BY_ID) if p]

    def finish(self, item_id):
        return api_module.Progress(item_id=item_id, is_finished=True)

    def unfinish(self, item_id):
        return api_module.Progress(item_id=item_id)

    def put_progress(self, item_id, body):
        return api_module.Progress(item_id=item_id,
                                   position_s=body.position_s)

    def resolve(self, url: str):
        return url

    def cover_url(self, item_id: str, size: str = "thumb"):
        return f"demo://{item_id}/{size}"

    def cover_bytes(self, item_id: str, size: str = "thumb") -> bytes:
        row = BY_ID.get(item_id)
        if row is None or not row["colour"]:
            raise api_module.Http(404, None, "no cover")
        if item_id not in self._covers:
            self._covers[item_id] = _cover_png(row)
        return self._covers[item_id]

    # -- M4/M5: account, settings, downloads, stats, reviews, zones ----------

    def me(self):
        return api_module.Me.from_dict(ME)

    def status(self):
        return api_module.Status.from_dict({
            "version": "1.6.0", "service": "xx-audiobook-server",
            "items": 1204, "items_missing": 3, "pending_matches": 0,
            "media_roots": [{"path": "/srv/audiobooks", "present": True,
                             "item_count": 1204}],
            "subsystems": {
                "db": {"state": "ok", "detail": "", "hint": ""},
                "ha": {"state": "ok", "detail": "2 locations", "hint": ""},
                "stream": {"state": "not_configured",
                           "detail": "No signing key for speakers.",
                           "hint": "Set it in the studio."}}})

    def settings(self):
        return api_module.UserSettings(skip_back_s=30, skip_forward_s=30,
                                       default_speed=1.0, auto_rewind_s=2,
                                       updated_at=NOW, applied=True)

    def put_settings(self, body):
        return api_module.UserSettings(
            skip_back_s=body.skip_back_s, skip_forward_s=body.skip_forward_s,
            default_speed=body.default_speed,
            auto_rewind_s=body.auto_rewind_s, updated_at=body.updated_at,
            applied=True)

    def manifest(self, item_id: str):
        size = int(BY_ID[item_id]["hours"] * BYTES_PER_HOUR / 2)
        return api_module.Manifest.from_dict({
            "item_id": item_id, "total_size": size * 2,
            "files": [{"file_id": f"{item_id}-f{i}", "index": i,
                       "size": size, "sha256": "", "mime": "audio/mp4"}
                      for i in range(2)]})

    # Stats: a steady listener since 2024, numbers from a fixed seed.

    def stats(self, year=None):
        import datetime as dt

        today = dt.date.today()
        year = year or today.year
        first = 2024
        if year < first:
            return api_module.Stats.from_dict({"year": year,
                                               "first_year": first})
        scale = {today.year: 1.0, today.year - 1: 0.8}.get(year, 0.45)
        rng = _Rng(year)
        by_day = []
        last = today if year == today.year else dt.date(year, 12, 31)
        for back in range(0, 60):
            day = last.fromordinal(last.toordinal() - back)
            if day.year != year:
                break
            off = back > 5 and rng.next() < 0.28
            minutes = 0 if off else int(15 + rng.next() * 140 * scale)
            if minutes:
                by_day.append({"date": day.isoformat(),
                               "listened_s": minutes * 60.0})
        months = last.month
        by_month = [{"month": m, "listened_s": (18 + rng.next() * 46)
                     * 3600 * scale, "sessions": int(20 + rng.next() * 30)}
                    for m in range(1, months + 1)]
        total = sum(m["listened_s"] for m in by_month)
        tops = (("ph", 0.24), ("cc", 0.17), ("lw", 0.14), ("mb", 0.09),
                ("tm", 0.07))
        return api_module.Stats.from_dict({
            "year": year, "total_listened_s": total,
            "sessions": sum(m["sessions"] for m in by_month),
            "items_played": 11, "items_finished": int(7 * scale) or 1,
            "days_listened": int(48 * months / 3 * scale),
            "by_month": by_month, "first_year": first, "by_day": by_day,
            "streak": {"current_days": 6 if year == today.year else 0,
                       "longest_days": int(23 * scale) or 3},
            "top_items": [{"item_id": i, "title": BY_ID[i]["title"],
                           "listened_s": total * share,
                           "thumb_url": f"api/items/{i}/cover?size=thumb"}
                          for i, share in tops],
            "top_authors": [
                {"id": AUTHORS[n], "name": n, "listened_s": total * share}
                for n, share in (("Becky Chambers", 0.33),
                                 ("Andy Weir", 0.31),
                                 ("Martha Wells", 0.12))]})

    def _sessions(self) -> list:
        import time as _time

        if getattr(self, "_session_rows", None) is None:
            rng = _Rng(7)
            now = _time.time()
            order = ("ph", "ph", "cc", "ph", "mb", "lw", "cc", "ph", "tm")
            rows = []
            start = now - 1800
            for index in range(46):
                item_id = order[index % len(order)]
                listened = (12 + rng.next() * 70) * 60
                position = BY_ID[item_id]["hours"] * H * (
                    0.1 + 0.8 * rng.next())
                rows.append({
                    "session_id": f"s-{index}", "item_id": item_id,
                    "title": BY_ID[item_id]["title"],
                    "device": "Phone" if index % 3 else "Web player",
                    "start_position_s": max(position - listened, 0.0),
                    "position_s": position, "listened_s": listened,
                    "speed": 1.25, "started_at": start,
                    "updated_at": start + listened,
                    "closed_at": start + listened})
                start -= (8 + rng.next() * 30) * 3600
            self._session_rows = rows
        return self._session_rows

    def sessions(self, page: int = 1, limit: int = 50):
        rows = self._sessions()
        chunk = rows[(page - 1) * limit:page * limit]
        return api_module.ListeningSessionPage.from_dict(
            {"total": len(rows), "page": page, "limit": limit,
             "results": chunk})

    def delete_session(self, session_id: str) -> None:
        self._session_rows = [r for r in self._sessions()
                              if r["session_id"] != session_id]

    # Reviews: placeholder people and placeholder text only.

    def _reviews(self, item_id: str) -> list:
        store = self.__dict__.setdefault("_review_rows", {})
        if item_id not in store:
            rng = _Rng(sum(map(ord, item_id)))
            rows = []
            count = 7 if item_id == "ph" else (3 if item_id in BY_ID else 0)
            for index in range(count):
                stars = 5 if rng.next() > 0.35 else 4
                if index == 4:
                    stars = 3
                body = ("[Review text] A placeholder review, written for "
                        "the demo. It runs two lines on a phone."
                        if index % 2 == 0 else "")
                when = NOW - 86400 * (2 + index * 9)
                rows.append({"id": f"rv-{item_id}-{index}",
                             "item_id": item_id,
                             "user": {"id": f"u-{index}",
                                      "display_name": f"Listener {chr(65 + index)}"},
                             "stars": stars, "body": body,
                             "created_at": when, "updated_at": when})
            if item_id == "lw":
                rows.insert(0, {"id": "rv-mine", "item_id": "lw",
                                "user": {"id": ME["user_id"],
                                         "display_name": ME["display_name"]},
                                "stars": 5,
                                "body": "[Your review] Placeholder text for "
                                        "the demo.",
                                "created_at": NOW - 86400 * 20,
                                "updated_at": NOW - 86400 * 20})
            store[item_id] = rows
        return store[item_id]

    def reviews(self, item_id: str, page: int = 1):
        rows = self._reviews(item_id)
        limit = 20
        chunk = rows[(page - 1) * limit:page * limit]
        average = (sum(r["stars"] for r in rows) / len(rows)
                   if rows else None)
        return api_module.ReviewsPage.from_dict(
            {"average": average, "count": len(rows), "page": page,
             "limit": limit, "results": chunk})

    def put_review(self, item_id: str, body):
        row = BY_ID.get(item_id)
        if row is not None and row["frac"] is None:
            raise api_module.Http(403, "not_started", "Start it first.")
        rows = [r for r in self._reviews(item_id)
                if r["user"]["id"] != ME["user_id"]]
        mine = {"id": f"rv-mine-{item_id}", "item_id": item_id,
                "user": {"id": ME["user_id"],
                         "display_name": ME["display_name"]},
                "stars": body.stars, "body": body.body,
                "created_at": NOW, "updated_at": NOW}
        self._review_rows[item_id] = [mine] + rows
        return api_module.Review.from_dict(mine)

    def delete_my_review(self, item_id: str) -> None:
        self._review_rows[item_id] = [
            r for r in self._reviews(item_id)
            if r["user"]["id"] != ME["user_id"]]

    def admin_delete_review(self, review_id: str) -> None:
        for item_id, rows in self.__dict__.get("_review_rows", {}).items():
            self._review_rows[item_id] = [r for r in rows
                                          if r["id"] != review_id]

    # Zones: two locations of Home Assistant players.

    def _zone_rows(self) -> dict:
        if getattr(self, "_targets", None) is None:
            self._targets = {
                "loc-home": [
                    {"entity_id": "media_player.kitchen",
                     "name": "Kitchen", "state": "idle",
                     "volume_level": 0.35},
                    {"entity_id": "media_player.living_room",
                     "name": "Living room", "state": "playing",
                     "volume_level": 0.55,
                     "media_title": "[Other audio]",
                     "media_position_s": 734.0},
                    {"entity_id": "media_player.bedroom",
                     "name": "Bedroom", "state": "off"}],
                "loc-cabin": [
                    {"entity_id": "media_player.porch", "name": "Porch",
                     "state": "idle", "volume_level": 0.5}]}
        return self._targets

    def locations(self) -> list:
        return [api_module.Location("loc-home", "Home"),
                api_module.Location("loc-cabin", "Cabin")]

    def cast_targets(self, location_id: str) -> list:
        return [api_module.CastTarget.from_dict(t)
                for t in self._zone_rows().get(location_id, [])]

    def _target(self, entity_id: str) -> dict | None:
        for rows in self._zone_rows().values():
            for target in rows:
                if target["entity_id"] == entity_id:
                    return target
        return None

    def cast_play(self, body):
        target = self._target(body.entity_id)
        if target is not None:
            target.update(state="playing",
                          media_title=BY_ID[body.item_id]["title"],
                          media_position_s=1840.0)
        return api_module.CastPlayResult(
            location_id=body.location_id or "", entity_id=body.entity_id,
            item_id=body.item_id, position_s=body.position_s or 0.0)

    def cast_transport(self, body):
        target = self._target(body.entity_id)
        if target is not None:
            if body.action in ("play", "pause"):
                target["state"] = "playing" if body.action == "play" \
                    else "paused"
            elif body.action == "stop":
                target.update(state="idle", media_title=None,
                              media_position_s=None)
            elif body.action == "volume":
                target["volume_level"] = body.value
            elif body.action == "seek" and body.value is not None:
                target["media_position_s"] = body.value
        return api_module.CastTransportResult(ok=True, action=body.action,
                                              entity_id=body.entity_id)


class _Rng:
    """A tiny fixed-seed generator: the demo looks the same every run."""

    def __init__(self, seed: int) -> None:
        self._state = (seed * 2654435761) % (2 ** 32) or 1

    def next(self) -> float:
        self._state = (1103515245 * self._state + 12345) % (2 ** 31)
        return self._state / float(2 ** 31)


def _cover_png(row: dict) -> bytes:
    """A stand-in cover: a flat colour field, one geometric form and the
    title, the way real cover art sits in the UI (content, not chrome)."""
    import io

    import cairo

    side = 480
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, side, side)
    cr = cairo.Context(surface)
    colour = row["colour"].lstrip("#")
    r, g, b = (int(colour[i:i + 2], 16) / 255 for i in (0, 2, 4))
    cr.set_source_rgb(r, g, b)
    cr.paint()
    seed = sum(map(ord, row["id"]))
    cr.set_source_rgba(1, 1, 1, 0.16)
    shape = seed % 3
    if shape == 0:
        cr.arc(side * 0.62, side * 0.40, side * 0.26, 0, 6.2832)
        cr.fill()
    elif shape == 1:
        for i in range(7):
            cr.rectangle(0, side * (0.18 + i * 0.07), side, side * 0.025)
        cr.fill()
    else:
        cr.move_to(side * 0.15, side * 0.70)
        cr.line_to(side * 0.55, side * 0.12)
        cr.line_to(side * 0.92, side * 0.70)
        cr.close_path()
        cr.fill()
    cr.set_source_rgba(1, 1, 1, 0.92)
    cr.select_font_face("Serif", cairo.FONT_SLANT_NORMAL,
                        cairo.FONT_WEIGHT_NORMAL)
    words = row["title"].upper().split()
    lines, current = [], ""
    for word in words:
        if len(current) + len(word) > 14 and current:
            lines.append(current)
            current = word
        else:
            current = (current + " " + word).strip()
    lines.append(current)
    cr.set_font_size(side * 0.075)
    y = side * 0.80 - (len(lines) - 1) * side * 0.085
    for text in lines:
        cr.move_to(side * 0.08, y)
        cr.show_text(text)
        y += side * 0.085
    cr.set_font_size(side * 0.045)
    cr.set_source_rgba(1, 1, 1, 0.70)
    cr.move_to(side * 0.08, side * 0.12)
    cr.show_text(row["author"].upper())
    out = io.BytesIO()
    surface.write_to_png(out)
    return out.getvalue()


def seed(home: str) -> None:
    os.environ["HOME"] = home
    from xx_audiobook import prefs as prefs_module
    from xx_audiobook import repos as repos_module
    from xx_audiobook import store as store_module
    from xx_audiobook.bookmarks import BookmarkRepository
    from xx_audiobook.paths import ensure_app_dirs

    ensure_app_dirs()
    db = store_module.Database()
    api = DemoApi()
    library = repos_module.LibraryRepository(api, db,
                                             now_ms=lambda: int(NOW * 1000))
    library.refresh(store_module.LibraryQuery(limit=50))
    for item_id in BY_ID:
        library.refresh_detail(item_id)
    from xx_audiobook.download import BookFiles

    files = BookFiles()
    for index, item_id in enumerate(DOWNLOADED):
        size = int(BY_ID[item_id]["hours"] * BYTES_PER_HOUR)
        db.downloads().upsert(store_module.Download(
            item_id, BY_ID[item_id]["title"],
            store_module.DownloadState.DONE.value, size, size, None,
            int(NOW * 1000) - index * 86_400_000,
            int(NOW * 1000) - index * 86_400_000))
        # Sparse stand-ins: the storage figure reads real sizes, the
        # disk holds nothing.
        files.ensure_dirs(item_id)
        with open(files.track_file(item_id, 0, f"{item_id}-f0"), "wb") as out:
            out.truncate(size)
    for index, (item_id, state, fraction, error) in enumerate(QUEUE):
        size = int(BY_ID[item_id]["hours"] * BYTES_PER_HOUR)
        stamp = int(NOW * 1000) + (len(QUEUE) - index) * 60_000
        db.downloads().upsert(store_module.Download(
            item_id, BY_ID[item_id]["title"], state, int(size * fraction),
            size, error, stamp, stamp))
    BookmarkRepository(api, db).refresh("ph")
    prefs = prefs_module.load()
    prefs.last_item_id = LAST[0]
    prefs.last_position_s = BY_ID[LAST[0]]["hours"] * H * LAST[1]
    prefs_module.save(prefs)
    db.close()


def main(argv: list) -> int:
    if len(argv) < 2:
        print("usage: demo_seed.py OUT_HOME", file=sys.stderr)
        return 2
    seed(os.path.abspath(argv[1]))
    print("seeded", argv[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
