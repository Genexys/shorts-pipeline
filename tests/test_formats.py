import dataclasses

import pytest

import formats
import video
from formats import LONG, SHORT, resolve_format


# -- resolution -------------------------------------------------------------


@pytest.mark.parametrize("name", [None, "", "   ", "nonsense", "vertical"])
def test_resolve_format_falls_back_to_short(name):
    # Anything unrecognised must keep producing today's output, so a payload
    # written before formats existed is unaffected.
    assert resolve_format(name) is SHORT


@pytest.mark.parametrize("name", ["long", "LONG", " Long "])
def test_resolve_format_is_case_and_space_insensitive(name):
    assert resolve_format(name) is LONG


def test_resolve_format_returns_short_by_name():
    assert resolve_format("short") is SHORT


# -- derived properties -----------------------------------------------------


def test_short_is_nine_by_sixteen():
    assert SHORT.aspect_ratio == pytest.approx(0.5625)


def test_long_is_sixteen_by_nine():
    assert LONG.aspect_ratio == pytest.approx(16 / 9)


def test_stock_video_count_multiplies_terms_by_clips():
    assert SHORT.stock_video_count == 5
    assert LONG.stock_video_count == 20


# -- preset sanity ----------------------------------------------------------


def test_presets_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        SHORT.width = 720


def test_formats_are_registered_under_their_own_name():
    assert all(name == fmt.name for name, fmt in formats.FORMATS.items())


def test_short_preset_matches_what_the_pipeline_does_today():
    # This preset exists to change nothing. If any of these drift from the
    # values video.py and pipeline.py use, wiring the format in would silently
    # alter every Short.
    assert (SHORT.width, SHORT.height) == (video.VIDEO_WIDTH, video.VIDEO_HEIGHT)
    assert SHORT.subtitle_font_size == video.SUBTITLE_FONT_SIZE
    assert SHORT.aspect_ratio == pytest.approx(0.5625)
    assert SHORT.burn_subtitles is True


def test_long_form_does_not_burn_subtitles():
    # It ships an .srt caption track instead, which YouTube can translate.
    assert LONG.burn_subtitles is False


def test_long_form_uses_longer_clips_and_more_of_them():
    # Repetition is the quality risk over several minutes, not render cost.
    assert LONG.max_clip_duration > SHORT.max_clip_duration
    assert LONG.stock_video_count > SHORT.stock_video_count


def test_long_form_subtitle_lines_are_readable_not_word_by_word():
    assert LONG.subtitle_max_chars > SHORT.subtitle_max_chars


def test_long_form_targets_a_three_to_four_minute_script():
    # ~150 spoken words per minute.
    assert 3.0 <= LONG.target_words / 150 <= 4.0
