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
    assert SHORT.stock_video_count == SHORT.search_term_count * SHORT.clips_per_term
    assert LONG.stock_video_count == LONG.search_term_count * LONG.clips_per_term


def test_every_format_has_enough_footage_to_avoid_repeating_itself():
    # Footage repeating inside one video is what makes an automated upload look
    # mass-produced. Each format must be able to cover its own runtime from
    # distinct clips: spoken words at ~150 wpm, capped shots.
    for fmt in (SHORT, LONG):
        runtime_seconds = fmt.target_words / 150 * 60
        coverage = fmt.stock_video_count * fmt.max_clip_duration
        assert coverage >= runtime_seconds


# -- preset sanity ----------------------------------------------------------


def test_presets_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        SHORT.width = 720


def test_formats_are_registered_under_their_own_name():
    assert all(name == fmt.name for name, fmt in formats.FORMATS.items())


def test_short_preset_still_describes_a_vertical_burned_in_short():
    # This preset exists to change nothing about today's output.
    assert (SHORT.width, SHORT.height) == (1080, 1920)
    assert SHORT.aspect_ratio == pytest.approx(0.5625)
    assert SHORT.subtitle_font_size == 112
    assert SHORT.burn_subtitles is True


def test_video_defaults_to_the_short_format():
    # Every format-aware entry point must fall back to SHORT, or an untouched
    # caller would change shape.
    assert "PlayResY: 1920" in video.patch_ass_script(
        "[Script Info]\n\n[Events]\n", "center,center", "#FFFF00"
    )


def test_long_form_does_not_burn_subtitles():
    # It ships an .srt caption track instead, which YouTube can translate.
    assert LONG.burn_subtitles is False


def test_long_form_uses_longer_clips_and_more_of_them():
    # Repetition is the quality risk over several minutes, not render cost.
    assert LONG.max_clip_duration > SHORT.max_clip_duration
    assert LONG.stock_video_count > SHORT.stock_video_count


def test_long_form_subtitle_lines_are_readable_not_word_by_word():
    assert LONG.subtitle_max_chars > SHORT.subtitle_max_chars


def test_long_form_is_minutes_and_shorts_are_seconds():
    # The exact length is a judgement call that moves; the gap between the two
    # formats is what has to hold, or "long" stops meaning anything.
    assert LONG.target_words >= SHORT.target_words * 4


# -- voice ------------------------------------------------------------------


def test_each_format_names_a_voice_the_tts_actually_has():
    # A typo here would only surface as a failed job mid-render.
    import tiktokvoice

    for fmt in (SHORT, LONG):
        assert fmt.voice in tiktokvoice.VOICES


def test_short_falls_back_to_a_narration_voice():
    # Only reached when ElevenLabs is unavailable, so it still has to sound
    # like the channel rather than like every automated upload on the platform.
    assert SHORT.voice == "en_male_narration"


def test_the_two_formats_do_not_share_a_narrator():
    # Same subject, same voice, same look across both formats is exactly the
    # "impression of mass production" the monetization policy describes.
    assert SHORT.voice != LONG.voice


def test_shorts_stay_inside_the_shorts_limit():
    # At roughly 150 spoken words a minute. Past 60 s it is no longer a Short.
    assert SHORT.target_words / 150 * 60 <= 60


def test_no_format_uses_the_ubiquitous_tiktok_voice():
    # en_us_001 is the most recognisable synthetic voice on the internet. It
    # reads as "content farm" before a word of the script lands, which is the
    # impression the whole pipeline is trying not to give.
    for fmt in (SHORT, LONG):
        assert fmt.voice != "en_us_001"


def test_both_formats_narrate_through_elevenlabs():
    assert SHORT.elevenlabs_voice_id
    assert LONG.elevenlabs_voice_id
    # Different voices: the two formats should read as two strands of one
    # channel, not one output run twice.
    assert SHORT.elevenlabs_voice_id != LONG.elevenlabs_voice_id


def test_the_fallback_voices_differ_too():
    assert SHORT.voice != LONG.voice
