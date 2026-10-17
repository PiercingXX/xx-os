"""Display text (fmt.py): times, speed, time left, initials."""

from xx_audiobook import fmt


def test_format_ms_switches_to_hours():
    assert fmt.format_ms(0) == "0:00"
    assert fmt.format_ms(65_000) == "1:05"
    assert fmt.format_ms(3_723_000) == "1:02:03"
    assert fmt.format_ms(-5) == "0:00"


def test_format_duration():
    assert fmt.format_duration(9 * 3600 + 12 * 60) == "9 h 12 min"
    assert fmt.format_duration(3600) == "1 h"
    assert fmt.format_duration(42 * 60 + 30) == "42 min"
    assert fmt.format_duration(20) == "< 1 min"


def test_speed_text_drops_trailing_zeros():
    assert fmt.speed_text(1.0) == "1×"
    assert fmt.speed_text(1.25) == "1.25×"
    assert fmt.speed_text(1.5) == "1.5×"
    assert fmt.speed_text(0.75) == "0.75×"


def test_time_left_scales_with_speed_and_names_the_chapter():
    line = fmt.time_left_line(0, 7_200_000, 2.0, chapter_end_ms=600_000)
    assert line == "1:00:00 left · 5:00 in chapter at 2×"
    assert fmt.time_left_line(0, 60_000, 1.0) == "1:00 left"
    assert fmt.time_left_line(0, 0, 1.0) == ""


def test_initials_skip_articles():
    assert fmt.initials("The Long Way") == "LW"
    assert fmt.initials("Piranesi") == "PI"
    assert fmt.initials("") == "XX"
    assert fmt.initials("A Closed and Common Orbit") == "CC"


def test_sleep_label_and_paragraphs():
    assert fmt.sleep_label(0, False) == "Sleep"
    assert fmt.sleep_label(90_000, False) == "1:30"
    assert fmt.description_paragraphs("One.\n\n Two. \r\n") == [
        "One.", "Two."]


def test_rating_and_counts():
    assert fmt.rating_text(4.56, 3) == "★ 4.6"
    assert fmt.rating_text(None, 0) == ""
    assert fmt.books_count(1) == "1 book"
    assert fmt.books_count(3) == "3 books"
