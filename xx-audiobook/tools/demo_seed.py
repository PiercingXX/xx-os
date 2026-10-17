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
DAMAGED = ("hy",)
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
    for item_id in DOWNLOADED:
        db.downloads().upsert(store_module.Download(
            item_id, BY_ID[item_id]["title"],
            store_module.DownloadState.DONE.value, 1000, 1000, None,
            int(NOW * 1000), int(NOW * 1000)))
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
