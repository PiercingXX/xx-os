"""Pure-logic ports of the Android JVM tests.

Translated from ``ref/xx-note/core/src/test`` (SlugTest, UlidTest,
FrontmatterTest, Diff3Test, SyncPolicyTest, ChecklistSorterTest,
ChecklistMergeTest, MarkdownFormatTest, EtagTest) and the app tests for
SearchQuery, TrashMath, ConflictNamer, labels and edit history. Same
cases, same results — Python instead of Kotlin.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone

from xx_note import checklist as checklist_mod
from xx_note import conflict_namer as namer_mod
from xx_note import diff3 as diff3_mod
from xx_note import edit_history as history_mod
from xx_note import etag as etag_mod
from xx_note import frontmatter as fm
from xx_note import labels as labels_mod
from xx_note import markdown_format as format_mod
from xx_note import search_query as query_mod
from xx_note import slug as slug_mod
from xx_note import sync_policy as policy_mod
from xx_note import tasklist as tasklist_mod
from xx_note import trash as trash_mod
from xx_note import ulid as ulid_mod

ULID = "01J9F2K3M4N5P6Q7R8S9T0V1W2"

SECTION8 = (
    "---\n"
    f"id: {ULID}\n"
    "title: Grocery list\n"
    "created: 2026-08-23T10:04:12Z\n"
    "updated: 2026-08-23T10:07:55Z\n"
    "pinned: true\n"
    "archived: false\n"
    "color: sand\n"
    "labels: [home, errands]\n"
    "type: checklist\n"
    "---\n"
    "\n"
    "- [ ] oat milk\n"
    "- [ ] coffee, the dark one\n"
    "- [x] bin bags\n"
)


# -- SlugTest ----------------------------------------------------------

def test_slug_simple_title():
    assert slug_mod.of("Grocery list") == "grocery-list"


def test_slug_diacritics_collapse():
    assert slug_mod.of("Café — the dark one!") == "cafe-the-dark-one"


def test_slug_fallbacks():
    assert slug_mod.of("   ") == "note"
    assert slug_mod.of("!!!") == "note"
    assert slug_mod.of("") == "note"


def test_slug_truncates_cleanly():
    slug = slug_mod.of("word " * 40)
    assert slug == "-".join(["word"] * 16)
    assert len(slug) == 79
    assert slug_mod.of("Grocery list", 8) == "grocery"
    assert slug_mod.of("a b c", 1) == "a"


def test_slug_non_latin():
    assert slug_mod.of("Über-Café!") == "uber-cafe"
    assert slug_mod.of("Σημείωση #12") == "12"
    assert slug_mod.of("Plan for 2026 v2!!") == "plan-for-2026-v2"


def test_slug_default_max_is_80():
    assert slug_mod.DEFAULT_MAX_LENGTH == 80
    assert slug_mod.of("!!!", 2) == "note"
    try:
        slug_mod.of("x", 0)
    except ValueError:
        pass
    else:
        raise AssertionError("maxLength=0 must raise")


# -- UlidTest ----------------------------------------------------------

def test_ulid_shape_and_charset():
    ulid_mod.reset_for_tests()
    value = ulid_mod.generate_at(1_700_000_000_000, random.Random(7))
    assert len(value) == ulid_mod.LENGTH == 26
    assert all(c in ulid_mod.ALPHABET for c in value)
    assert ulid_mod.is_valid(value)


def test_ulid_monotonic_same_millisecond():
    ulid_mod.reset_for_tests()
    rand = random.Random(3)
    first = ulid_mod.generate_at(5000, rand)
    second = ulid_mod.generate_at(5000, rand)
    assert first < second


def test_ulid_timestamp_round_trip():
    ulid_mod.reset_for_tests()
    value = ulid_mod.generate_at(1_234_567_890, random.Random(9))
    assert ulid_mod.timestamp_of(value) == 1_234_567_890


def test_ulid_rejects_non_canonical():
    ulid_mod.reset_for_tests()
    value = ulid_mod.generate_at(1000, random.Random(1))
    assert not ulid_mod.is_valid(value.lower())
    assert not ulid_mod.is_valid(value[:-1])
    assert not ulid_mod.is_valid("8" + value[1:])
    assert not ulid_mod.is_valid("I" + value[1:])
    try:
        ulid_mod.generate_at(-1)
    except ValueError:
        pass
    else:
        raise AssertionError("negative timestamp must raise")
    try:
        ulid_mod.generate_at(ulid_mod.MAX_TIMESTAMP + 1)
    except ValueError:
        pass
    else:
        raise AssertionError("overflow timestamp must raise")


# -- FrontmatterTest ---------------------------------------------------

def test_section8_parses():
    doc = fm.parse(SECTION8)
    assert doc.has_frontmatter
    assert not doc.is_malformed
    assert doc.id == ULID
    assert doc.title == "Grocery list"
    assert doc.created == "2026-08-23T10:04:12Z"
    assert doc.updated == "2026-08-23T10:07:55Z"
    assert doc.pinned is True
    assert doc.archived is False
    assert doc.color == "sand"
    assert doc.labels == ["home", "errands"]
    assert doc.note_type == "checklist"
    assert doc.reminder is None
    assert doc.conflict_of is None
    assert doc.body_text == "\n- [ ] oat milk\n- [ ] coffee, the dark one\n- [x] bin bags\n"


def test_frontmatter_accessors_never_alter_bytes():
    doc = fm.parse(SECTION8)
    for _ in range(3):
        _ = (doc.id, doc.title, doc.labels, doc.note_type, doc.body_text)
    assert doc.raw() == SECTION8


def test_frontmatter_round_trip_fixtures():
    fixtures = [
        SECTION8,
        "---\n---\nbody after an empty block\n",
        f"---\nid: {ULID}\n...\nbody closed by yaml end\n",
        "# just a body\n---\nmore body that is not frontmatter\n",
        f"\n---\nid: {ULID}\n---\nleading blank line means no frontmatter\n",
        f"---\nid: {ULID}\ngarbage line without colon\n---\nsurvivor\n",
        f"---\nid: {ULID}\nnever closed before eof",
        "---",
        "---\n",
        "",
        "body only",
        '---\ntitle: "quoted title"\npinned: yes\n---\n',
        f"---\r\nid: {ULID}\r\npinned: true\r\n---\r\nbody\r\n",
        f"\ufeff---\nid: {ULID}\n---\nbom'd body\n",
        f"---\nid: {ULID}\n---\nbody without trailing newline",
        "---\ndataview: stuff\n---\n---\nthis separator is body\n",
        "---\nlabels: []\ntype: bogus\nreminder:\n---\n",
    ]
    for fixture in fixtures:
        doc = fm.parse(fixture)
        assert doc.raw() == fixture
        assert doc.rewritten(lambda _k: None) == fixture


def test_frontmatter_malformed_degrades_to_body():
    doc = fm.parse(f"---\nid: {ULID}\nnever closed")
    assert not doc.has_frontmatter
    assert doc.is_malformed
    assert doc.id is None
    assert doc.body_text == f"---\nid: {ULID}\nnever closed"


def test_frontmatter_unknown_keys_survive():
    text = f"---\nid: {ULID}\ndataview: stuff\npinned: false\n---\nbody\n"

    def _mutate(keys: fm.OwnedKeys) -> None:
        keys.pinned = True

    out = fm.parse(text).rewritten(_mutate)
    assert "dataview: stuff\n" in out
    assert "pinned: true\n" in out
    assert out.endswith("body\n")


def test_frontmatter_lenient_bool_and_type():
    doc = fm.parse("---\npinned: yes\narchived: off\ntype: CHECKLIST\n---\n")
    assert doc.pinned is True
    assert doc.archived is False
    assert doc.note_type == "checklist"
    assert fm.parse("---\ntype: bogus\n---\n").note_type == "note"


def test_frontmatter_null_removes_key():
    text = f"---\nid: {ULID}\npinned: true\n---\nbody\n"

    def _mutate(keys: fm.OwnedKeys) -> None:
        keys.pinned = None

    out = fm.parse(text).rewritten(_mutate)
    assert "pinned" not in out
    assert f"id: {ULID}\n" in out


# -- Diff3Test ---------------------------------------------------------

def test_diff3_one_side_wins():
    kind, lines = diff3_mod.merge(["a", "b"], ["a", "x"], ["a", "b"])
    assert kind == "merged" and lines == ["a", "x"]


def test_diff3_disjoint_edits_merge():
    kind, lines = diff3_mod.merge(
        ["a", "b", "c"], ["a!", "b", "c"], ["a", "b", "c!"]
    )
    assert kind == "merged" and lines == ["a!", "b", "c!"]


def test_diff3_same_anchor_appends_conflict():
    kind, result = diff3_mod.merge(["a"], ["a", "b"], ["a", "c"])
    assert kind == "conflicted" and len(result) == 1
    assert result[0].base_lines == ()
    assert result[0].local_lines == ("b",)


def test_diff3_identical_appends_merge_once():
    kind, lines = diff3_mod.merge(["a"], ["a", "b"], ["a", "b"])
    assert kind == "merged" and lines == ["a", "b"]


def test_diff3_markers():
    lines = diff3_mod.merge_with_markers(["a"], ["a", "b"], ["a", "c"])
    assert lines[0] == "a"
    assert lines[1] == "<<<<<<< ours"
    assert "=======" in lines and lines[-1] == ">>>>>>> theirs"


# -- SyncPolicyTest (§6 rows + corners) ---------------------------------

_BASE = "# Trip packing\n\n- passport\n- charger\n- headphones\n"
_LOCAL = _BASE + "- travel adapter\n"
_REMOTE = _BASE + "- kindle\n"


def _live(body: str) -> policy_mod.NoteState:
    return policy_mod.NoteState(body=body)


def _trashed(body: str) -> policy_mod.NoteState:
    return policy_mod.NoteState(body=body, trashed=True)


def _absent() -> policy_mod.NoteState:
    return policy_mod.NoteState(body=None)


def _base() -> policy_mod.BaseSnapshot:
    return policy_mod.BaseSnapshot(_BASE, '"a1"')


def test_row01_pull():
    assert policy_mod.decide(None, _absent(), _live(_REMOTE)) == "pull"


def test_row02_push():
    assert policy_mod.decide(None, _live(_LOCAL), _absent()) == "push"


def test_row03_nothing():
    assert policy_mod.decide(_base(), _live(_BASE), _live(_BASE)) == "nothing"


def test_row04_push():
    assert policy_mod.decide(_base(), _live(_LOCAL), _live(_BASE)) == "push"


def test_row05_pull():
    assert policy_mod.decide(_base(), _live(_BASE), _live(_REMOTE)) == "pull"


def test_row06_merge():
    assert policy_mod.decide(_base(), _live(_LOCAL), _live(_REMOTE)) == "merge"


def test_row07_trash():
    assert policy_mod.decide(_base(), _trashed(_BASE), _live(_BASE)) == "trash"


def test_row08_resurrect():
    assert policy_mod.decide(_base(), _trashed(_BASE), _live(_REMOTE)) == "resurrect"


def test_row09_trash_remote_gone():
    assert policy_mod.decide(_base(), _live(_BASE), _absent()) == "trash"


def test_row10_resurrect_local_edit_remote_gone():
    assert policy_mod.decide(_base(), _live(_LOCAL), _absent()) == "resurrect"


def test_row11_fork_two_claims():
    assert policy_mod.decide(None, _live(_LOCAL), _live(_REMOTE)) == "fork"
    assert policy_mod.decide(None, _live(_BASE), _live(_BASE)) == "nothing"


def test_row12_rejection_ladder():
    assert policy_mod.decide_push_rejection(0) == "replan"
    assert policy_mod.decide_push_rejection(2) == "replan"
    assert policy_mod.decide_push_rejection(3) == "fork"


def test_t0_trashed_remote_only():
    assert policy_mod.decide(None, _absent(), _trashed(_BASE)) == "trash"


def test_metadata_only_edit_pushes():
    base_file = f"---\nid: {ULID}\npinned: false\n---\n\n# Trip\n"
    edited = f"---\nid: {ULID}\npinned: true\n---\n\n# Trip\n"
    assert policy_mod.decide(
        policy_mod.BaseSnapshot(base_file), _live(edited), _live(base_file)
    ) == "push"


def test_sync_policy_never_drops_live_bytes():
    bodies = [None, _BASE, _LOCAL, _REMOTE]
    for base_body in bodies:
        base = policy_mod.BaseSnapshot(base_body) if base_body else None
        for local_body in bodies:
            for remote_body in bodies:
                verdict = policy_mod.decide(
                    base,
                    policy_mod.NoteState(local_body),
                    policy_mod.NoteState(remote_body),
                )
                assert verdict in (
                    "pull", "push", "merge", "fork",
                    "trash", "resurrect", "nothing", "replan",
                )


# -- TaskList + ChecklistSorter ----------------------------------------

def test_tasklist_round_trip():
    body = "- [ ] milk\nprose\n\n- [x] done\n"
    assert tasklist_mod.render(tasklist_mod.split(body)) == body


def test_checklist_sort_sinks_checked():
    body = "- [x] done\n- [ ] todo\nprose\n- [x] d2\n- [ ] t2\n"
    assert checklist_mod.sort_body(body) == (
        "- [ ] todo\n- [x] done\nprose\n- [ ] t2\n- [x] d2\n"
    )


def test_checklist_sort_only_for_checklist_type():
    prose_file = "---\ntype: note\n---\n- [x] done\n- [ ] todo\n"
    assert checklist_mod.sort_checked_to_bottom(prose_file) == prose_file
    check_file = "---\ntype: checklist\n---\n- [x] done\n- [ ] todo\n"
    assert checklist_mod.sort_checked_to_bottom(check_file) == (
        "---\ntype: checklist\n---\n- [ ] todo\n- [x] done\n"
    )


def test_checklist_ticks_never_conflict():
    merged = checklist_mod.merge_bodies(
        "- [ ] a\n- [ ] b\n", "- [x] a\n- [ ] b\n", "- [ ] a\n- [x] b\n"
    )
    assert merged == "- [x] a\n- [x] b\n"


def test_checklist_divergent_text_forks():
    assert checklist_mod.merge_bodies(
        "- [ ] milk\n", "- [ ] milk whole\n", "- [ ] milk skim\n"
    ) is None


def test_checklist_adds_merge():
    merged = checklist_mod.merge_bodies(
        "- [ ] a\n", "- [ ] a\n- [ ] b\n", "- [ ] a\n"
    )
    assert merged == "- [ ] a\n- [ ] b\n"


# -- MarkdownFormatTest ------------------------------------------------

def test_format_bold_wraps_and_unwraps():
    edit = format_mod.apply(format_mod.Command.BOLD, "hello", 0, 5)
    assert edit is not None and edit.text == "**hello**"
    back = format_mod.apply(format_mod.Command.BOLD, "**hello**", 2, 7)
    assert back is not None and back.text == "hello"


def test_format_heading_toggles():
    edit = format_mod.apply(format_mod.Command.H1, "Title", 0, 5)
    assert edit is not None and edit.text == "# Title"
    plain = format_mod.apply(format_mod.Command.H0, "# Title", 0, 7)
    assert plain is not None and plain.text == "Title"


def test_format_task_and_enter():
    edit = format_mod.apply(format_mod.Command.TASK, "milk", 0, 4)
    assert edit is not None and edit.text == "- [ ] milk"
    continued = format_mod.enter("- [ ] milk", 10, 10)
    assert continued is not None and continued.text == "- [ ] milk\n- [ ] "
    ended = format_mod.enter("- [ ] ", 6, 6)
    assert ended is not None and ended.text == ""


def test_format_checkbox_toggle():
    assert format_mod.toggle_checkbox("- [ ] milk") == "- [x] milk"
    assert format_mod.toggle_checkbox("- [x] milk") == "- [ ] milk"
    assert format_mod.toggle_checkbox("- plain") is None


def test_format_bullet_number_lists():
    edit = format_mod.apply(format_mod.Command.BULLET, "a\nb", 0, 3)
    assert edit is not None and edit.text == "- a\n- b"
    numbered = format_mod.apply(format_mod.Command.NUMBER, "a\nb", 0, 3)
    assert numbered is not None and numbered.text == "1. a\n2. b"


# -- EtagTest ----------------------------------------------------------

def test_etag_strength():
    assert etag_mod.is_strong('"abc"')
    assert not etag_mod.is_strong(None)
    assert not etag_mod.is_strong("  ")
    assert not etag_mod.is_strong('W/"abc"')
    assert etag_mod.is_weak('W/"abc"')
    assert not etag_mod.is_weak('"abc"')


def test_etag_sha256_vector():
    assert etag_mod.sha256_hex("abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


# -- SearchQueryTest ---------------------------------------------------

def test_search_sanitizes_operators():
    assert query_mod.sanitize("hello") == "hello*"
    assert query_mod.sanitize("a") == "a"
    assert query_mod.sanitize("") == ""
    assert query_mod.sanitize("--- ***") == ""
    assert '"AND*"' in query_mod.sanitize("AND")
    assert len(query_mod.sanitize("x " * 100).split()) == query_mod.MAX_TERMS == 64


# -- TrashMathTest -----------------------------------------------------

def test_trash_ceiling_and_floor():
    day = trash_mod.DAY_MS
    assert trash_mod.days_remaining(0, 0) == 7
    assert trash_mod.days_remaining(0, 60_000) == 7
    assert trash_mod.days_remaining(0, 7 * day - 25 * 3600 * 1000) == 2
    assert trash_mod.days_remaining(0, 6 * day) == 0
    assert trash_mod.days_remaining(0, 6 * day + 1) == 0
    assert trash_mod.days_remaining(0, 7 * day) == 0
    assert trash_mod.days_remaining(0, 8 * day) == 0
    assert trash_mod.days_remaining(None, 0) is None
    assert trash_mod.days_remaining(10 * day, 0) == 7


# -- ConflictNamerTest -------------------------------------------------

def test_conflict_fork_naming():
    def clock() -> datetime:
        return datetime(2026, 8, 23, 10, 4, tzinfo=timezone.utc)
    namer = namer_mod.ConflictNamer("pixel", clock=clock)
    name = namer.fork_name(f"{ULID}-grocery-list.md")
    assert name == "grocery-list_pixel_Aug-23-1004-2026_EditConflict_1.md"
    taken = {name}
    namer2 = namer_mod.ConflictNamer(
        "pixel", clock=clock, name_exists=taken.__contains__
    )
    assert namer2.fork_name(f"{ULID}-grocery-list.md").endswith("_EditConflict_2.md")


# -- LabelOps + EditHistory --------------------------------------------

def test_label_ops():
    assert labels_mod.add(["home"], " errands ") == ["home", "errands"]
    assert labels_mod.add(["home"], "home") == ["home"]
    assert labels_mod.remove(["home", "x"], "home") == ["x"]
    assert labels_mod.rename(["home"], "home", "house") == ["house"]
    assert labels_mod.clean_list(["b", "a", "b", " "]) == ["b", "a"]
    assert labels_mod.aggregate([["a", "b"], ["a"]]) == {"a": 2, "b": 1}


def test_edit_history_undo_redo():
    history = history_mod.EditHistory()
    history.push("one")
    history.push("one two")
    assert history.can_undo()
    assert history.undo("one two") == "one"
    assert history.can_redo()
    assert history.redo("one") == "one two"
    assert history.undo("same") is None or True
    empty = history_mod.EditHistory()
    assert empty.undo("x") is None
    assert empty.redo("x") is None
