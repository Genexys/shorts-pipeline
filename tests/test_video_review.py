import json
import shutil
import subprocess

import pytest

import video_review
from video_review import MAJOR, MINOR, Problem


def test_frame_times_avoid_the_first_and_last_instant():
    times = video_review.frame_times(39.0, 12)
    assert len(times) == 12
    assert times[0] == 3.0 and times[-1] == 36.0


def test_frame_times_for_nothing_is_empty():
    assert video_review.frame_times(0, 12) == []


def test_parse_problems_keeps_valid_items_and_maps_times():
    text = json.dumps({"problems": [
        {"frame": 2, "kind": "black", "severity": "major", "note": "black screen"},
        {"frame": 9, "kind": "black", "severity": "major", "note": "out of range"},
        {"frame": 1, "kind": "nonsense", "severity": "major", "note": "bad kind"},
        {"frame": 1, "kind": "caption", "severity": "minor", "note": "typo"},
    ]})
    problems = video_review.parse_problems(text, [3.0, 6.0])
    assert [(p.frame, p.kind, p.severity, p.seconds) for p in problems] == [
        (2, "black", MAJOR, 6.0), (1, "caption", MINOR, 3.0),
    ]


def _enable(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.delenv("VIDEO_REVIEW_MODEL", raising=False)


def test_disabled_without_a_key(monkeypatch, tmp_path):
    result = video_review.review("x.mp4", "n", 10, tmp_path, ask=lambda f, n: pytest.fail("asked"))
    assert not result.checked


def test_off_switch(monkeypatch, tmp_path):
    _enable(monkeypatch)
    monkeypatch.setenv("VIDEO_REVIEW_MODEL", "off")
    assert not video_review.is_enabled()


@pytest.fixture
def clip(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    path = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
         "-t", "4", "-pix_fmt", "yuv420p", str(path)],
        check=True, timeout=60,
    )
    return path


def test_review_reads_real_frames_and_reports_majors(monkeypatch, tmp_path, clip):
    _enable(monkeypatch)
    seen = {}

    def ask(frames, narration):
        seen["count"] = len(frames)
        seen["jpeg"] = frames[0][1][:2]
        return [Problem(3, "black", MAJOR, "black", frames[2][0]),
                Problem(5, "caption", MINOR, "typo", frames[4][0])]

    result = video_review.review(str(clip), "narration", 4.0, tmp_path / "work", ask=ask)

    assert seen["count"] == video_review.FRAME_COUNT
    assert seen["jpeg"] == b"\xff\xd8"  # JPEG
    assert result.checked
    assert [p.kind for p in result.major] == ["black"]
    assert result.summary()["problems"][0].startswith("black at ")


def test_a_failing_model_publishes_as_before(monkeypatch, tmp_path, clip):
    _enable(monkeypatch)

    def boom(frames, narration):
        raise RuntimeError("overloaded")

    result = video_review.review(str(clip), "n", 4.0, tmp_path / "work", ask=boom)
    assert not result.checked and result.problems == []
