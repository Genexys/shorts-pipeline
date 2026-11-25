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


# -- severity settled against what can be checked -------------------------------------


NARRATION_XRAY = "By January 1896, physicians were already using it to see the skeletons of living patients."


def test_unrelated_footage_never_holds_the_video():
    # 2026-11-16..21: four Shorts held on openings the review itself called
    # "only a minor mismatch" or "acceptable but weak".
    settled = video_review.settle(
        [Problem(1, "unrelated", MAJOR, "back of a head in a cinema; acceptable but weak", 2.0),
         Problem(5, "unrelated", MAJOR, "a rat while the narration says 'steel bar'", 10.0)],
        "In the 1920s, two psychologists struck a steel bar.",
        shots=[(0.0, 4.8), (4.8, 4.4), (9.2, 4.4)],
    )
    assert [p.severity for p in settled] == [MINOR, MINOR]


def test_a_stand_in_in_the_opening_holds_however_brief():
    # 2026-11-03: a sloth under "An opossum playing dead", rated minor.
    settled = video_review.settle(
        [Problem(1, "stand_in", MINOR, "a sloth, not an opossum", 2.0)],
        "An opossum playing dead is not pretending.",
        shots=[(0.0, 2.5), (2.5, 4.0)],
    )
    assert settled[0].severity == MAJOR


def test_a_stand_in_holds_once_its_shot_plays_long_enough():
    # 2026-11-24: a modern lab-coat chemist for Constantin Fahlberg, 7.8 s.
    settled = video_review.settle(
        [Problem(4, "stand_in", MINOR, "a stock scientist for Fahlberg", 14.0)],
        "Its discoverer, Constantin Fahlberg, was working at Johns Hopkins.",
        shots=[(0.0, 5.0), (5.0, 4.0), (9.0, 4.2), (13.2, 7.8)],
    )
    assert settled[0].severity == MAJOR


def test_a_stand_in_that_flashes_by_is_noted_not_held():
    # 2026-11-22: one 2.4 s shot of a woman drinking tea near a named physician,
    # reported on two frames of the same shot.
    settled = video_review.settle(
        [Problem(8, "stand_in", MAJOR, "a woman for Melissa Schumacher", 25.5),
         Problem(9, "stand_in", MAJOR, "the same woman", 27.0)],
        "Physician Melissa Schumacher says it clears up as you warm up.",
        shots=[(0.0, 10.0), (10.0, 15.1), (25.1, 2.4), (27.5, 5.0)],
    )
    assert [p.severity for p in settled] == [MINOR, MINOR]


def test_brief_stand_ins_add_up_across_shots():
    settled = video_review.settle(
        [Problem(6, "stand_in", MINOR, "a woman for Ffirth", 23.0),
         Problem(7, "stand_in", MINOR, "the same woman", 26.0)],
        "Stubbins Ffirth drank it. He never got sick.",
        shots=[(0.0, 21.9), (21.9, 1.6), (23.5, 2.0), (25.5, 1.6), (27.1, 6.0)],
    )
    # Two shots of 1.6 s, each too brief alone, 3.2 s on screen together.
    assert [p.severity for p in settled] == [MAJOR, MAJOR]


def test_a_stand_in_holds_when_its_screen_time_is_unknown():
    problem = Problem(6, "stand_in", MINOR, "a stock actor for a named doctor", 20.0)
    assert video_review.settle([problem], "n")[0].severity == MAJOR
    # A frame past the end of the timeline cannot be placed either.
    beyond = video_review.settle([problem], "n", shots=[(0.0, 10.0)])
    assert beyond[0].severity == MAJOR


def test_other_kinds_keep_the_models_severity():
    settled = video_review.settle(
        [Problem(2, "black", MAJOR, "black frame", 7.0),
         Problem(3, "watermark", MINOR, "a faint logo", 10.0)],
        "n",
        shots=[(0.0, 30.0)],
    )
    assert [p.severity for p in settled] == [MAJOR, MINOR]


def test_the_summary_lists_the_minor_problems():
    review = video_review.Review(checked=True, problems=[
        Problem(2, "black", MAJOR, "black", 7.0),
        Problem(4, "unrelated", MINOR, "a rat", 10.0),
    ])
    summary = review.summary()
    assert len(summary["problems"]) == 2
    assert summary["minor"] == ["unrelated at 10s: a rat"]


def test_review_decides_stand_ins_with_the_timeline(monkeypatch, tmp_path, clip):
    _enable(monkeypatch)
    monkeypatch.setattr(video_review, "OPENING_SECONDS", 0.0)

    def ask(frames, narration):
        return [Problem(6, "stand_in", MAJOR, "a stock actor", frames[5][0])]

    brief = video_review.review(str(clip), "n", 4.0, tmp_path / "a", ask=ask,
                                shots=[(0.0, 1.0), (1.0, 1.0), (2.0, 2.0)])
    assert brief.major == [] and len(brief.minor) == 1
    unknown = video_review.review(str(clip), "n", 4.0, tmp_path / "b", ask=ask)
    assert len(unknown.major) == 1


def test_a_misspelling_the_script_does_not_have_is_not_held():
    # 2026-11-08: held for "SKELETONS", spelt as the script spells it.
    note = 'Caption reads "SKELETONS" but is misspelt as "SKELETONS" with an extra letter'
    settled = video_review.settle([Problem(11, "caption", MAJOR, note, 33.0)], NARRATION_XRAY)
    assert settled[0].severity == MINOR
    real = video_review.settle([Problem(11, "caption", MAJOR, 'reads "SKELTONS"', 33.0)], NARRATION_XRAY)
    assert real[0].severity == MAJOR


def test_the_prompt_separates_stand_ins_from_unrelated_footage():
    prompt = " ".join(video_review.PROMPT.split())
    assert '"stand_in"' in prompt and '"unrelated"' in prompt
    assert "a related species" in prompt
    assert "a stock actor while the narration names a real person" in prompt
    assert "A stand-in is major" in prompt
