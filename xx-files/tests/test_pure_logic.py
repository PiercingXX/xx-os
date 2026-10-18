"""Pure-logic ports of the Android JVM tests.

Covers Rename, Clip, IoError, SaveHere, Transfer, MenuActions, Mime,
Listing, Breadcrumbs, FileKind, Hidden, DirectoryContents, Tree, Row,
Volumes, VolumeLabels, BackNav, ShowHiddenStore and trash restore
naming. Deltas from the JVM suite (all in PORT_SPEC.md):

- Breadcrumbs/Volumes labels use Home (Linux primary label), not Internal.
- Hidden has no .xx-trash name: the freedesktop Trash leaves no marker
  in browsed folders, so dotfiles are the whole rule.
- DirectoryContents.BLOCKED_COPY is the Linux wording.
- The all-files gate (GateUi), theme sync, log upload and suite backup
  are not ported (no permission model / no home server on Linux).
- Trash expiry/purge is the desktop's job under freedesktop Trash.
"""
import os

import pytest

from xx_files import breadcrumbs, clip, filekind, hidden, io_error, listing
from xx_files import menu_actions, mime, rename, row, save_here, tree
from xx_files import volumes
from xx_files.directory_contents import Blocked, Empty, Entries, Failed
from xx_files.directory_contents import BLOCKED_COPY
from xx_files.directory_contents import classify, read_list
from xx_files.menu_actions import Action
from xx_files.transfer import copy_file, copy_tree, move_path
from xx_files.trash_info import parse_trashinfo, restore_destination
from xx_files.volumes import Dest, Volume


def _entry(tmp_path, name, is_dir=False):
    path = tmp_path / name
    if is_dir:
        path.mkdir()
    else:
        path.write_text("x")
    return listing.Entry(name, str(path), is_dir)


# -- Rename ----------------------------------------------------------

def test_rename_empty_is_invalid():
    assert rename.validate("") is not None


def test_rename_slash_is_invalid():
    assert rename.validate("a/b") is not None


def test_rename_valid_accepted():
    assert rename.validate("ok.txt") is None


def test_rename_same_name_is_not_overwrite():
    assert rename.would_overwrite("foo.txt", "foo.txt",
                                  ["foo.txt", "bar.txt"]) is False


def test_rename_taken_is_overwrite():
    assert rename.would_overwrite("foo.txt", "bar.txt",
                                  ["foo.txt", "bar.txt"]) is True


def test_rename_free_is_not_overwrite():
    assert rename.would_overwrite("foo.txt", "baz.txt",
                                  ["foo.txt", "bar.txt"]) is False


def test_rename_free_name_returns_plain():
    assert rename.next_available_name("foo.txt", ["bar.txt"]) == "foo.txt"


def test_rename_taken_returns_counter():
    assert rename.next_available_name("foo.txt",
                                      ["foo.txt", "bar.txt"]) == "foo (2).txt"


def test_rename_doubly_taken_returns_three():
    siblings = ["foo.txt", "foo (2).txt", "bar.txt"]
    assert rename.next_available_name("foo.txt", siblings) == "foo (3).txt"


def test_rename_resolve_target_free():
    assert rename.resolve_target("/tmp/foo.txt", "foo.txt",
                                  ["bar.txt"]) == "/tmp/foo.txt"


def test_rename_resolve_target_taken():
    assert rename.resolve_target("/tmp/foo.txt", "foo.txt",
                                  ["foo.txt",
                                   "bar.txt"]) == "/tmp/foo (2).txt"


def test_rename_resolve_target_never_collides():
    siblings = ["foo.txt", "foo (2).txt", "foo (3).txt"]
    target = rename.resolve_target("/tmp/foo.txt", "foo.txt", siblings)
    assert os.path.basename(target) not in set(siblings)


# -- Clip ------------------------------------------------------------

def test_clip_free_name_plain(tmp_path):
    assert clip.unique_name([], "foo.txt") == "foo.txt"


def test_clip_taken_name_counters(tmp_path):
    (tmp_path / "foo.txt").write_text("x")
    existing = [p.name for p in tmp_path.iterdir()]
    assert clip.unique_name(existing, "foo.txt") == "foo (2).txt"


def test_clip_doubly_taken_name(tmp_path):
    (tmp_path / "foo.txt").write_text("x")
    (tmp_path / "foo (2).txt").write_text("x")
    existing = [p.name for p in tmp_path.iterdir()]
    assert clip.unique_name(existing, "foo.txt") == "foo (3).txt"


def test_clip_no_extension_counters(tmp_path):
    (tmp_path / "report").write_text("x")
    assert clip.unique_name(["report"], "report") == "report (2)"


def test_clip_paste_disabled_without_item(tmp_path):
    assert clip.can_paste(None, True) is False
    assert clip.can_paste(None, False) is False


def test_clip_paste_disabled_in_readonly():
    assert clip.can_paste(clip.Item("/tmp/src.txt", False), False) is False


def test_clip_paste_enabled_with_item_and_writable():
    assert clip.can_paste(clip.Item("/tmp/src.txt", False), True) is True
    assert clip.can_paste(clip.Item("/tmp/src.txt", True), True) is True


# -- IoError ---------------------------------------------------------

def test_io_error_useful_bit():
    assert io_error.message(Exception("Permission denied")) == \
        "Permission denied"


def test_io_error_none_falls_back():
    assert io_error.message(None) == io_error.FALLBACK


def test_io_error_blank_falls_back():
    assert io_error.message(Exception("   ")) == io_error.FALLBACK


# -- SaveHere --------------------------------------------------------

def test_save_here_display_name_used():
    assert save_here.file_name("photo.jpg", "image/jpeg") == "photo.jpg"


def test_save_here_path_stripped():
    assert save_here.file_name("/tmp/share/doc.pdf") == "doc.pdf"


def test_save_here_blank_falls_back_to_mime():
    assert save_here.file_name("  ", "application/pdf") == "shared.pdf"


def test_save_here_unknown_mime_is_shared_bin():
    assert save_here.file_name(None, "application/octet-stream") == \
        save_here.DEFAULT_NAME


def test_save_here_destination_uniquifies(tmp_path):
    (tmp_path / "photo.jpg").write_text("x")
    existing = [p.name for p in tmp_path.iterdir()]
    assert save_here.destination(str(tmp_path), existing,
                                 "photo.jpg") == str(tmp_path / "photo (2).jpg")


def test_save_here_destination_free(tmp_path):
    assert save_here.destination(str(tmp_path), [], "photo.jpg") == \
        str(tmp_path / "photo.jpg")


# -- Transfer --------------------------------------------------------

def test_transfer_copy_writes_sibling(tmp_path):
    source = tmp_path / "note.txt"
    source.write_text("hello")
    target = str(tmp_path / "sub" / "note.txt")
    os.makedirs(os.path.dirname(target))
    copy_file(str(source), target)
    assert open(target).read() == "hello"
    assert source.exists()


def test_transfer_move_renames(tmp_path):
    source = tmp_path / "a.txt"
    source.write_text("x")
    target = str(tmp_path / "b.txt")
    move_path(str(source), target)
    assert open(target).read() == "x"
    assert not source.exists()


def test_transfer_copy_tree(tmp_path):
    album = tmp_path / "album"
    album.mkdir()
    (album / "shot.jpg").write_text("img")
    target = str(tmp_path / "copy" / "album")
    os.makedirs(os.path.dirname(target))
    copy_tree(str(album), target)
    assert open(os.path.join(target, "shot.jpg")).read() == "img"
    assert album.exists()


def test_transfer_failed_copy_leaves_no_target(tmp_path):
    with pytest.raises(Exception):
        copy_tree(str(tmp_path / "missing"), str(tmp_path / "out"))
    assert not (tmp_path / "out").exists()


def test_transfer_copy_cancelled(tmp_path):
    from xx_files.jobs import Cancelled

    source = tmp_path / "note.txt"
    source.write_text("hello")
    with pytest.raises(Cancelled):
        copy_tree(str(source), str(tmp_path / "out.txt"),
                  cancelled=lambda: True)
    assert not (tmp_path / "out.txt").exists()


# -- MenuActions -----------------------------------------------------

def test_menu_file_actions_include_share():
    assert menu_actions.for_type(False) == [
        Action.OPEN, Action.SHARE, Action.COPY, Action.CUT,
        Action.RENAME, Action.DELETE, Action.PROPERTIES]


def test_menu_folder_actions_minus_share():
    assert menu_actions.for_type(True) == [
        Action.OPEN, Action.COPY, Action.CUT,
        Action.RENAME, Action.DELETE, Action.PROPERTIES]


def test_menu_folder_is_file_minus_share():
    assert [a for a in menu_actions.for_type(False)
            if a is not Action.SHARE] == menu_actions.for_type(True)


def test_menu_overflow_never_row_actions():
    for actions in (menu_actions.for_type(False),
                    menu_actions.for_type(True)):
        assert Action.NEW_FOLDER not in actions
        assert Action.PASTE not in actions
        assert Action.SHOW_HIDDEN not in actions
        assert Action.TRASH not in actions
        assert Action.RESTORE not in actions
        assert Action.DELETE_FOREVER not in actions


def test_menu_trash_actions():
    assert menu_actions.for_trash() == [Action.RESTORE,
                                        Action.DELETE_FOREVER]


def test_overflow_browse_paste_only_when_staged():
    labels = [t for t, _k in menu_actions.overflow("browse")]
    assert labels == ["New folder", "View", "Trash", "Volumes"]
    labels = [t for t, _k in menu_actions.overflow("browse", has_clip=True)]
    assert labels == ["New folder", "Paste", "View", "Trash", "Volumes"]


def test_overflow_trash_has_no_volumes_entry():
    # Back leads from the Trash to the Volumes: one way to get there.
    assert menu_actions.overflow("trash", trash_count=2) == [
        ("Empty Trash", "empty-trash")]
    assert menu_actions.overflow("trash", trash_count=0) == []


def test_overflow_volumes_lists_trash():
    assert menu_actions.overflow("volumes") == [("Trash", "trash")]


# -- Mime ------------------------------------------------------------

def test_mime_known_types():
    assert mime.from_extension("notes.txt") == "text/plain"
    assert mime.from_extension("photo.jpg") == "image/jpeg"
    assert mime.from_extension("photo.jpeg") == "image/jpeg"
    assert mime.from_extension("doc.pdf") == "application/pdf"
    assert mime.from_extension("clip.mp4") == "video/mp4"
    assert mime.from_extension("archive.zip") == "application/zip"


def test_mime_case_insensitive():
    assert mime.from_extension("IMG_0001.HEIC") == "image/heic"
    assert mime.from_extension("show.mkv") == "video/x-matroska"
    assert mime.from_extension("track.flac") == "audio/flac"
    assert mime.from_extension("app.apk") == \
        "application/vnd.android.package-archive"


def test_mime_fallback():
    assert mime.from_extension("blob.xyz") == "application/octet-stream"
    assert mime.from_extension("README") == "application/octet-stream"


# -- Listing ---------------------------------------------------------

def test_listing_dirs_before_files(tmp_path):
    rows = [_entry(tmp_path, "d", True), _entry(tmp_path, "b"),
            _entry(tmp_path, "c", True), _entry(tmp_path, "a")]
    assert [e.name for e in listing.sort(rows)] == ["c", "d", "a", "b"]


def test_listing_case_insensitive(tmp_path):
    rows = [_entry(tmp_path, n) for n in ("cherry", "Banana", "apple")]
    assert [e.name for e in listing.sort(rows)] == ["apple", "Banana",
                                                    "cherry"]


def test_listing_dirs_case_insensitive(tmp_path):
    rows = [_entry(tmp_path, "Beta", True), _entry(tmp_path, "alpha", True)]
    assert [e.name for e in listing.sort(rows)] == ["alpha", "Beta"]


def test_listing_sort_modes_stay_dirs_first(tmp_path):
    sub = _entry(tmp_path, "sub", True)
    big = listing.Entry("big.bin", str(tmp_path / "big.bin"), False,
                        size=9999, modified=10.0)
    small = listing.Entry("small.bin", str(tmp_path / "small.bin"), False,
                          size=1, modified=99.0)
    by_size = listing.sort_by([small, big, sub], "size")
    assert [e.name for e in by_size] == ["sub", "big.bin", "small.bin"]
    by_date = listing.sort_by([small, big, sub], "date")
    assert [e.name for e in by_date] == ["sub", "small.bin", "big.bin"]


# -- Breadcrumbs -----------------------------------------------------

def test_crumbs_root_only():
    assert breadcrumbs.split("/home/u", "/home/u") == ["Home"]


def test_crumbs_children():
    assert breadcrumbs.split("/home/u/Download/Reports",
                              "/home/u") == ["Home", "Download", "Reports"]


def test_crumbs_removable_label():
    assert breadcrumbs.split("/media/USB/Music", "/media/USB",
                              root_label="USB") == ["USB", "Music"]


def test_crumbs_segment_path():
    assert breadcrumbs.segment_path("/home/u/a/b", "/home/u", 0) == "/home/u"
    assert breadcrumbs.segment_path("/home/u/a/b", "/home/u",
                                     1) == "/home/u/a"
    assert breadcrumbs.segment_path("/home/u/a/b", "/home/u",
                                     2) == "/home/u/a/b"


def test_crumbs_navigate_out_of_range_stays():
    assert breadcrumbs.navigate_to("/home/u/a", "/home/u", 9) == "/home/u/a"
    assert breadcrumbs.navigate_to("/home/u/a", "/home/u", -1) == "/home/u/a"
    assert breadcrumbs.navigate_to("/home/u/a/b", "/home/u",
                                    1) == "/home/u/a"


# -- FileKind --------------------------------------------------------

def test_kind_thumbnails_only_images_video():
    assert filekind.from_name("IMG_0001.JPG").has_thumbnail
    assert filekind.from_name("shot.heic").has_thumbnail
    assert filekind.from_name("clip.mp4").has_thumbnail
    assert filekind.from_name("show.mkv").has_thumbnail
    assert not filekind.from_name("song.mp3").has_thumbnail
    assert not filekind.from_name("notes.txt").has_thumbnail
    assert not filekind.FileKind.FOLDER.has_thumbnail


def test_kind_mapping():
    assert filekind.from_name("photo.PNG") is filekind.FileKind.IMAGE
    assert filekind.from_name("movie.webm") is filekind.FileKind.VIDEO
    assert filekind.from_name("voice.opus") is filekind.FileKind.AUDIO
    assert filekind.from_name("book.pdf") is filekind.FileKind.DOCUMENT
    assert filekind.from_name("README.md") is filekind.FileKind.TEXT
    assert filekind.from_name("backup.tar.gz") is filekind.FileKind.ARCHIVE
    assert filekind.from_name("Main.kt") is filekind.FileKind.CODE
    assert filekind.from_name("xx-files.apk") is filekind.FileKind.PACKAGE


def test_kind_other():
    assert filekind.from_name("LICENSE") is filekind.FileKind.OTHER
    assert filekind.from_name("blob.xyz") is filekind.FileKind.OTHER
    assert filekind.from_name("trailing.") is filekind.FileKind.OTHER


def test_kind_folder_wins():
    assert filekind.of("photos.jpg", True) is filekind.FileKind.FOLDER
    assert filekind.of("photos.jpg", False) is filekind.FileKind.IMAGE


# -- Hidden ----------------------------------------------------------

def test_hidden_dotfile_off():
    assert hidden.should_show(".nomedia", False) is False


def test_hidden_dotfile_on():
    assert hidden.should_show(".nomedia", True) is True


def test_hidden_normal_shown():
    assert hidden.should_show("DCIM", False) is True


def test_hidden_trash_marker_is_dotfile():
    assert hidden.should_show(".xx-trash", False) is False


# -- DirectoryContents -----------------------------------------------

def test_contents_none_is_blocked():
    assert isinstance(classify(None), Blocked)


def test_contents_empty_is_empty():
    assert isinstance(classify([]), Empty)


def test_contents_names_are_entries():
    result = classify(["DCIM", "Download"])
    assert isinstance(result, Entries)
    assert result.names == ("DCIM", "Download")


def test_contents_error_is_failed():
    result = classify(None, Exception("Permission denied"))
    assert isinstance(result, Failed)
    assert result.message == "Permission denied"


def test_contents_regular_file_is_blocked(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("x")
    assert isinstance(read_list(str(target)), Blocked)


def test_contents_empty_dir(tmp_path):
    assert isinstance(read_list(str(tmp_path)), Empty)


def test_contents_dir_with_children(tmp_path):
    (tmp_path / "DCIM").mkdir()
    result = read_list(str(tmp_path))
    assert isinstance(result, Entries)
    assert result.names == ("DCIM",)


def test_contents_blocked_copy():
    assert BLOCKED_COPY == "Could not open this folder."


# -- Tree ------------------------------------------------------------

def _scan(path):
    out = []
    for name in sorted(os.listdir(path)):
        full = os.path.join(path, name)
        out.append(listing.Entry(name, full, os.path.isdir(full)))
    return out


def test_tree_collapsed_is_one_level(tmp_path):
    (tmp_path / "DCIM").mkdir()
    (tmp_path / "note.txt").write_text("x")
    nodes = tree.flatten(_scan(str(tmp_path)), set(), False)
    assert [n.entry.name for n in nodes] == ["DCIM", "note.txt"]
    assert all(n.depth == 0 for n in nodes)
    assert nodes[0].is_dir and not nodes[0].expanded


def test_tree_expand_inserts_children(tmp_path):
    dcim = tmp_path / "DCIM"
    dcim.mkdir()
    (dcim / "Camera").mkdir()
    (tmp_path / "note.txt").write_text("x")

    def read(entry):
        return _scan(entry.path) if entry.is_dir else []

    nodes = tree.flatten(_scan(str(tmp_path)), {str(dcim)}, False,
                         read_children=read)
    assert [n.entry.name for n in nodes] == ["DCIM", "Camera", "note.txt"]
    assert [n.depth for n in nodes] == [0, 1, 0]
    assert nodes[0].expanded


def test_tree_guide_elbows():
    node = tree.TreeNode(listing.Entry("leaf", "/tmp/leaf", False),
                         depth=2, is_last=True,
                         ancestor_continues=[True], expanded=False)
    assert tree.guide(node) == "│  └─ "


def test_tree_guide_root_empty():
    node = tree.TreeNode(listing.Entry("root", "/tmp/root", True),
                         depth=0, is_last=False,
                         ancestor_continues=[], expanded=False)
    assert tree.guide(node) == ""


# -- Row -------------------------------------------------------------

def test_row_file_second_line(tmp_path):
    target = tmp_path / "photo.jpg"
    target.write_bytes(b"x" * 12700)
    modified_ms = int(target.stat().st_mtime * 1000)
    assert row.second_line_file(12700, modified_ms) == \
        f"12.4 KB · {row.iso_date(modified_ms)}"


def test_row_file_without_date():
    assert row.second_line_file(512, 0) == "512 B"


def test_row_folder_counts():
    assert row.item_count(0) == "Empty"
    assert row.item_count(1) == "1 item"
    assert row.item_count(2) == "2 items"
    assert row.item_count(0, hidden=1) == "Empty · 1 hidden"
    assert row.item_count(3, hidden=2) == "3 items · 2 hidden"


def test_row_icon_follows_kind():
    assert row.icon_for_kind("folder") == "folder-symbolic"
    assert row.icon_for_kind("folder", expanded=True) == \
        "folder-open-symbolic"
    assert row.icon_for_kind("image") == "image-x-generic-symbolic"
    assert row.icon_for_kind("audio") == "audio-x-generic-symbolic"
    assert row.icon_for_kind("other") == "text-x-generic-symbolic"


def test_row_human_sizes():
    assert row.human_size(512) == "512 B"
    assert row.human_size(12700) == "12.4 KB"
    assert row.human_size(5 * 1024 * 1024) == "5.0 MB"


# -- Volumes ---------------------------------------------------------

def _home_volumes():
    primary = Volume("Home", "/home/u", True, False)
    sd = Volume("SD card", "/media/SD", False, True)
    usb = Volume("USB drive", "/media/USB", False, True)
    return primary, sd, usb


def test_volumes_root_match():
    assert volumes.is_volume_root("/home/u", ["/home/u"]) is True
    assert volumes.is_volume_root("/home/u/", ["/home/u"]) is True
    assert volumes.is_volume_root("/home/u/Download",
                                   ["/home/u"]) is False
    assert volumes.is_volume_root("/other", ["/home/u"]) is False


def test_volumes_mounted_keeps_all_with_roots():
    primary, sd, usb = _home_volumes()
    assert volumes.mounted([primary, sd, usb]) == [primary, sd, usb]


def test_volumes_mounted_drops_empty_root():
    primary, _sd, _usb = _home_volumes()
    ghost = Volume("SD card", "", False, True)
    assert volumes.mounted([primary, ghost]) == [primary]


def test_volumes_containing():
    primary, sd, _usb = _home_volumes()
    assert volumes.containing("/home/u/DCIM/Camera",
                              [primary, sd]) == primary
    assert volumes.containing("/media/SD/Music", [primary, sd]) == sd
    assert volumes.containing("/media/SD", [primary, sd]) == sd
    assert volumes.containing("/nonexistent/child",
                              [primary, sd]) is None


def test_volume_primary_labelled_home():
    assert volumes.label("anything", True) == "Home"
    assert volumes.label("SD card", False) == "SD card"
    assert volumes.label("USB drive", False) == "USB drive"


def test_back_volumes_mode_finishes():
    primary, sd, _usb = _home_volumes()
    assert volumes.back_dest(True, False, "/home/u",
                             [primary, sd]) is Dest.FINISH


def test_back_root_goes_to_volume_list():
    primary, sd, _usb = _home_volumes()
    assert volumes.back_dest(False, False, "/home/u",
                             [primary, sd]) is Dest.VOLUME_LIST
    assert volumes.back_dest(False, False, "/media/SD",
                             [primary, sd]) is Dest.VOLUME_LIST


def test_back_child_goes_to_parent():
    primary, sd, _usb = _home_volumes()
    assert volumes.back_dest(False, False, "/media/SD/Music",
                             [primary, sd]) is Dest.PARENT
    assert volumes.back_dest(False, False, "/home/u/DCIM",
                             [primary, sd]) is Dest.PARENT


def test_back_trash_goes_to_volume_list():
    primary, sd, _usb = _home_volumes()
    assert volumes.back_dest(False, True, "/home/u",
                             [primary, sd]) is Dest.VOLUME_LIST


# -- Trash restore naming --------------------------------------------

def test_trash_restore_free_name():
    assert restore_destination("/tmp/a/foo.txt",
                               ["bar.txt"]) == "/tmp/a/foo.txt"


def test_trash_restore_taken_uniquifies():
    assert restore_destination("/tmp/a/foo.txt",
                               ["foo.txt",
                                "bar.txt"]) == "/tmp/a/foo (2).txt"
    assert restore_destination("/tmp/a/foo.txt",
                               ["foo.txt", "foo (2).txt"]) == \
        "/tmp/a/foo (3).txt"


def test_trash_restore_keeps_bytes(tmp_path):
    from xx_files import jobs
    from xx_files.ops import do_restore

    folder = tmp_path / "docs"
    folder.mkdir()
    source = folder / "note.txt"
    source.write_text("old")
    trash_files = tmp_path / "shred" / "files"
    trash_info_dir = tmp_path / "shred" / "info"
    trash_files.mkdir(parents=True)
    trash_info_dir.mkdir(parents=True)
    payload = trash_files / "note.txt"
    source.rename(payload)
    info = trash_info_dir / "note.txt.trashinfo"
    info.write_text("[Trash Info]\n"
                    f"Path={source}\n"
                    "DeletionDate=2026-10-01T10:00:00\n")
    (folder / "note.txt").write_text("taken")
    restored = jobs.run_sync(do_restore, str(payload), str(info))
    assert restored == str(folder / "note (2).txt")
    assert open(restored).read() == "old"
    assert open(folder / "note.txt").read() == "taken"
    assert not payload.exists()
    assert not info.exists()


def test_trashinfo_parse():
    path, date = parse_trashinfo("[Trash Info]\n"
                                 "Path=/home/u/old%20name.txt\n"
                                 "DeletionDate=2026-10-01T10:00:00\n")
    assert path == "/home/u/old name.txt"
    assert date == "2026-10-01T10:00:00"


def test_trashinfo_broken_is_none():
    assert parse_trashinfo("junk") == (None, None)
    assert parse_trashinfo("[Trash Info]\nPath=\n") == (None, None)


# -- Settings store --------------------------------------------------

def test_settings_round_trip(tmp_path):
    from xx_files.prefs import Settings, load, save

    path = str(tmp_path / "settings.json")
    assert load(path) == Settings()
    save(Settings(show_hidden=True, sort="date", view="grid"), path)
    assert load(path) == Settings(show_hidden=True, sort="date",
                                  view="grid")


def test_settings_bad_file_falls_back(tmp_path):
    from xx_files.prefs import Settings, load

    path = tmp_path / "settings.json"
    path.write_text("{oops")
    assert load(str(path)) == Settings()
