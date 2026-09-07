import pytest
from PIL import Image

import thumbnail
from formats import LONG, SHORT


# -- which formats get one --------------------------------------------------


def test_long_form_builds_a_thumbnail():
    assert LONG.build_thumbnail is True


def test_shorts_do_not():
    # Shorts are chosen from a vertical feed, and a 16:9 still misrepresents them.
    assert SHORT.build_thumbnail is False


# -- title wrapping ---------------------------------------------------------


def test_wrap_title_upper_cases_and_breaks_lines():
    assert thumbnail.wrap_title("the mysterious glow of the deep ocean", 18, 3) == [
        "THE MYSTERIOUS",
        "GLOW OF THE DEEP",
        "OCEAN",
    ]


def test_wrap_title_respects_the_line_budget():
    lines = thumbnail.wrap_title("one two three four five six seven eight nine", 10, 2)
    assert len(lines) == 2
    assert all(len(line) <= 10 for line in lines)


def test_wrap_title_keeps_a_long_word_on_its_own_line():
    # Splitting a word mid-way is worse than a line that overruns slightly.
    lines = thumbnail.wrap_title("bioluminescence explained", 12, 3)
    assert "BIOLUMINESCENCE" in lines


def test_wrap_title_handles_nothing():
    assert thumbnail.wrap_title("") == []
    assert thumbnail.wrap_title(None) == []


# -- frame scoring ----------------------------------------------------------


def _solid(colour, size=(64, 36)):
    return Image.new("RGB", size, colour)


def test_a_black_frame_scores_zero():
    # Common at cuts and fades, and useless behind text.
    assert thumbnail.score_frame(_solid((2, 2, 2))) == 0.0


def test_a_blown_out_frame_scores_zero():
    assert thumbnail.score_frame(_solid((252, 252, 252))) == 0.0


def test_a_flat_mid_grey_scores_low():
    assert thumbnail.score_frame(_solid((120, 120, 120))) < 1.0


def test_a_contrasty_frame_beats_a_flat_one():
    flat = _solid((120, 120, 120))
    contrasty = Image.new("RGB", (64, 36))
    for x in range(64):
        for y in range(36):
            contrasty.putpixel((x, y), (30, 30, 30) if x % 2 else (210, 210, 210))
    assert thumbnail.score_frame(contrasty) > thumbnail.score_frame(flat)


# -- frame choice -----------------------------------------------------------


def test_pick_best_frame_returns_none_without_candidates():
    assert thumbnail.pick_best_frame([]) is None


def test_pick_best_frame_skips_unreadable_files(tmp_path):
    broken = tmp_path / "broken.png"
    broken.write_text("not an image")
    good = tmp_path / "good.png"
    _solid((120, 60, 30), (64, 36)).save(good)
    assert thumbnail.pick_best_frame([broken, good]) == good


def test_pick_best_frame_falls_back_when_everything_scores_zero(tmp_path):
    # Better a dark thumbnail than none: YouTube would pick a random frame.
    black = tmp_path / "black.png"
    _solid((0, 0, 0)).save(black)
    assert thumbnail.pick_best_frame([black]) == black


# -- composition ------------------------------------------------------------


def test_compose_writes_a_youtube_sized_jpeg(tmp_path):
    frame = tmp_path / "frame.png"
    _solid((90, 110, 130), (1920, 1080)).save(frame)
    out = tmp_path / "thumb.jpg"

    thumbnail.compose(frame, ["HELLO", "WORLD"], str(out))

    with Image.open(out) as image:
        assert image.size == (thumbnail.THUMBNAIL_WIDTH, thumbnail.THUMBNAIL_HEIGHT)
        assert image.format == "JPEG"


def test_compose_crops_a_vertical_frame_to_sixteen_by_nine(tmp_path):
    frame = tmp_path / "tall.png"
    _solid((90, 110, 130), (1080, 1920)).save(frame)
    out = tmp_path / "thumb.jpg"

    thumbnail.compose(frame, ["X"], str(out))

    with Image.open(out) as image:
        assert image.size == (thumbnail.THUMBNAIL_WIDTH, thumbnail.THUMBNAIL_HEIGHT)


def test_compose_works_without_any_text(tmp_path):
    frame = tmp_path / "frame.png"
    _solid((90, 110, 130), (1920, 1080)).save(frame)
    out = tmp_path / "thumb.jpg"
    assert thumbnail.compose(frame, [], str(out)) == str(out)


# -- orchestration ----------------------------------------------------------


def test_build_thumbnail_returns_none_when_no_frame_can_be_read(monkeypatch, tmp_path):
    monkeypatch.setattr(thumbnail, "extract_candidates", lambda *a, **k: [])
    assert thumbnail.build_thumbnail(
        "video.mp4", "title", str(tmp_path / "t.jpg"), 100.0, tmp_path
    ) is None
