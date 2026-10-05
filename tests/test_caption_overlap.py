"""No burned caption starts while the one before it is still up.

ffmpeg's SRT to ASS conversion rounds a cue's start and duration separately, so
one can end a hundredth of a second after the next begins. libass then moves
the newer caption below the older one for its whole time on screen: on
2026-10-04 and 2026-10-05, seven words in four Shorts jumped 120 px down.
"""

import shutil
import subprocess

import pytest

import video


HEADER = (
    "[Script Info]\n"
    "ScriptType: v4.00+\n"
    "\n"
    "[V4+ Styles]\n"
    "Format: Name, Fontname, Fontsize\n"
    "Style: Default,Arial,16\n"
    "\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
)


def events(script: str) -> list:
    return [
        line.split(",", 9)
        for line in script.splitlines()
        if line.startswith("Dialogue:")
    ]


def test_a_caption_ending_after_the_next_begins_ends_where_it_begins():
    script = HEADER + (
        "Dialogue: 0,0:00:26.01,0:00:27.00,Default,,0,0,0,,curtain\n"
        "Dialogue: 0,0:00:26.99,0:00:27.99,Default,,0,0,0,,moves in,\n"
    )
    first, second = events(video.patch_ass_script(script, "center,center", "#FFFF00"))
    assert first[1:3] == ["0:00:26.01", "0:00:26.99"]
    assert second[1:3] == ["0:00:26.99", "0:00:27.99"]


def test_the_text_is_still_upper_cased_and_its_commas_kept():
    script = HEADER + (
        "Dialogue: 0,0:00:01.00,0:00:02.01,Default,,0,0,0,,one, two\n"
        "Dialogue: 0,0:00:02.00,0:00:03.00,Default,,0,0,0,,three\n"
    )
    first, _ = events(video.patch_ass_script(script, "center,center", "#FFFF00"))
    assert first[2] == "0:00:02.00"
    assert first[9] == "ONE, TWO"


def test_captions_that_touch_or_leave_a_gap_are_untouched():
    script = HEADER + (
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,a\n"
        "Dialogue: 0,0:00:02.00,0:00:03.00,Default,,0,0,0,,b\n"
        "Dialogue: 0,0:00:03.50,0:00:04.00,Default,,0,0,0,,c\n"
    )
    ends = [event[2] for event in events(video.patch_ass_script(script, "center,center", "#FFFF00"))]
    assert ends == ["0:00:02.00", "0:00:03.00", "0:00:04.00"]


def test_a_caption_starting_with_the_next_is_not_cut_to_nothing():
    script = HEADER + (
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,a\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,b\n"
    )
    first, _ = events(video.patch_ass_script(script, "center,center", "#FFFF00"))
    assert first[2] == "0:00:02.00"


def test_an_unreadable_time_is_left_alone():
    lines = [
        "Dialogue: 0,soon,0:00:02.00,Default,,0,0,0,,a",
        "Dialogue: 0,0:00:01.50,0:00:03.00,Default,,0,0,0,,b",
    ]
    assert video.end_dialogue_before_next(lines) == lines


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_ffmpegs_own_conversion_no_longer_overlaps(tmp_path):
    # The cue times of the shower-curtain Short's "curtain moves in,".
    srt = tmp_path / "captions.srt"
    srt.write_text(
        "1\n00:00:26,007 --> 00:00:26,994\ncurtain\n\n"
        "2\n00:00:26,994 --> 00:00:27,996\nmoves in,\n",
        encoding="utf-8",
    )
    ass = tmp_path / "captions.ass"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(srt), str(ass)], check=True)
    raw = events(ass.read_text(encoding="utf-8"))
    assert raw[0][2] > raw[1][1], "ffmpeg no longer rounds this way; the test needs new times"

    patched = events(
        video.patch_ass_script(ass.read_text(encoding="utf-8"), "center,center", "#FFFF00")
    )
    assert patched[0][2] == patched[1][1]
