"""Footage is chosen line by line and cut to the narration.

Until 2026-10-13 a Short's clips came from four or five terms for the whole
video and played in search order, so the picture had nothing to do with the
words being said over it.
"""

import json

import pytest

import shot_list
from footage_judge import Frame
import footage_judge
from shot_list import Beat

SCRIPT = (
    "Your knuckles crack because of a gas bubble. "
    "Scientists filmed it inside an MRI.\n\n"
    "The joint fluid holds dissolved gas; pulling the joint apart drops the pressure. "
    "A cavity snaps open."
)


def _beats_reply(*beats):
    return json.dumps(
        {"beats": [{"first": f, "last": l, "term": t, "backup": b} for f, l, t, b in beats]}
    )


# -- the plan --------------------------------------------------------------------------


def test_sentences_are_read_as_the_narrator_reads_them():
    # The semicolon becomes a full stop in the narration, and in the captions.
    assert shot_list.narrated_sentences(SCRIPT) == [
        "Your knuckles crack because of a gas bubble.",
        "Scientists filmed it inside an MRI.",
        "The joint fluid holds dissolved gas.",
        "Pulling the joint apart drops the pressure.",
        "A cavity snaps open.",
    ]


def test_a_plan_that_covers_every_sentence_in_order_is_used():
    sentences = shot_list.narrated_sentences(SCRIPT)
    reply = _beats_reply(
        (1, 1, "cracking knuckles", "hands closeup"),
        (2, 2, "MRI scanner", "hospital scanner"),
        (3, 5, "joint x-ray", ""),
    )
    beats = shot_list.parse_beats(reply, sentences)
    assert [(b.first, b.last, b.term) for b in beats] == [
        (0, 0, "cracking knuckles"),
        (1, 1, "MRI scanner"),
        (2, 4, "joint x-ray"),
    ]
    assert beats[2].text == " ".join(sentences[2:])
    assert beats[0].backup == "hands closeup"


@pytest.mark.parametrize(
    "beats",
    [
        [(1, 1, "a", ""), (3, 5, "b", "")],  # sentence 2 left out
        [(1, 2, "a", ""), (2, 5, "b", "")],  # sentence 2 twice
        [(1, 2, "a", ""), (3, 4, "b", "")],  # stops short
        [(1, 6, "a", "")],  # past the end
        [(1, 5, "", "")],  # no term
    ],
)
def test_a_plan_that_does_not_cover_the_script_in_order_is_not_used(beats):
    sentences = shot_list.narrated_sentences(SCRIPT)
    assert shot_list.parse_beats(_beats_reply(*beats), sentences) == []


def test_a_reply_that_is_not_json_is_not_used():
    assert shot_list.parse_beats("Sure! Here are the beats.", ["One."]) == []


def test_a_backup_that_repeats_the_term_is_dropped():
    beats = shot_list.parse_beats(_beats_reply((1, 1, "Onion", "onion")), ["One."])
    assert beats[0].backup == ""


def test_no_model_means_no_beats_and_the_old_footage():
    def down(prompt, model):
        raise RuntimeError("overloaded")

    assert shot_list.plan_beats("knuckles", SCRIPT, down) == []


def test_the_prompt_numbers_the_sentences_and_asks_for_a_sensible_count():
    prompts = []
    reply = _beats_reply((1, 5, "cracking knuckles", ""))
    beats = shot_list.plan_beats("knuckles", SCRIPT, lambda p, m: prompts.append(p) or reply)
    assert len(beats) == 1
    assert "2. Scientists filmed it inside an MRI." in prompts[0]
    assert "Aim for 3 to 4 beats" in prompts[0]


def test_beat_count_follows_the_length():
    assert shot_list.beat_range(80, 9) == (6, 9)
    assert shot_list.beat_range(80, 20) == (6, 10)
    # Never more beats than sentences.
    assert shot_list.beat_range(30, 2) == (2, 2)


def test_a_long_line_gets_enough_clips_that_no_shot_outruns_the_cap():
    beat = Beat(0, 0, " ".join(["word"] * 23), "t", "")
    # 23 words at 2.26 a second is about 10 s: two five-second shots, plus one.
    assert shot_list.clips_wanted(beat, 2.26, 5.0) == 3
    assert shot_list.clips_wanted(Beat(0, 0, "Short.", "t", ""), 2.26, 5.0) == 1


def test_a_rejected_clip_is_replaced_from_the_backup_term_first():
    term = {"term": "hemlock plant", "search_pass": "beat", "urls": ["t1", "t2"]}
    backup = {"term": "wild herbs", "search_pass": shot_list.BACKUP_PASS, "urls": ["b1"]}
    assert shot_list.backup_first([term, backup]) == [backup, term]
    # With no backup searched, the order is untouched.
    assert shot_list.backup_first([term]) == [term]


# -- the timing ------------------------------------------------------------------------


def _beats(*ranges):
    return [Beat(f, l, "", f"term{f}", "") for f, l in ranges]


def test_each_line_starts_with_the_caption_holding_its_first_word():
    sentences = ["An onion fights back.", "It fires a gas.", "You cry."]
    cues = [
        (0.0, 0.9, "AN ONION"), (0.9, 1.9, "FIGHTS BACK."),
        (2.3, 3.0, "IT FIRES"), (3.0, 3.7, "A GAS."), (4.1, 4.8, "YOU CRY."),
    ]
    windows = shot_list.beat_windows(_beats((0, 0), (1, 2)), sentences, cues, 5.3)
    assert windows == [(0.0, 2.3), (2.3, 5.3)]


def test_without_captions_the_time_is_shared_by_letters():
    windows = shot_list.beat_windows(_beats((0, 0), (1, 1)), ["Aaaa.", "Bbbbbbbbbbbb."], [], 8.0)
    assert windows == [(0.0, 2.0), (2.0, 8.0)]


def test_each_line_shows_its_own_clips_for_its_own_time():
    segments = shot_list.plan_segments(
        [(0.0, 3.0), (3.0, 11.0)],
        [[("knuckles.mp4", 12.0)], [("mri.mp4", 12.0), ("xray.mp4", 12.0)]],
        5.0,
    )
    assert segments == [("knuckles.mp4", 3.0), ("mri.mp4", 4.0), ("xray.mp4", 4.0)]


def test_a_lone_clip_holds_the_whole_line_rather_than_restarting():
    segments = shot_list.plan_segments([(0.0, 6.5)], [[("one.mp4", 12.0)]], 5.0)
    assert segments == [("one.mp4", 6.5)]


def test_a_line_with_no_clips_gives_its_time_to_the_line_before():
    segments = shot_list.plan_segments(
        [(0.0, 3.0), (3.0, 5.0), (5.0, 8.0)],
        [[("a.mp4", 12.0)], [], [("c.mp4", 12.0)]],
        5.0,
    )
    assert segments == [("a.mp4", 5.0), ("c.mp4", 3.0)]


def test_a_line_too_short_for_a_shot_is_not_cut_to():
    segments = shot_list.plan_segments(
        [(0.0, 3.0), (3.0, 3.8), (3.8, 7.0)],
        [[("a.mp4", 12.0)], [("b.mp4", 12.0)], [("c.mp4", 12.0)]],
        5.0,
    )
    assert [path for path, _ in segments] == ["a.mp4", "c.mp4"]
    assert sum(seconds for _, seconds in segments) == pytest.approx(7.0)


def test_an_empty_opening_line_gives_its_time_to_the_next():
    segments = shot_list.plan_segments(
        [(0.0, 2.0), (2.0, 5.0)], [[], [("b.mp4", 12.0)]], 5.0
    )
    assert segments == [("b.mp4", 5.0)]


def test_no_clips_at_all_is_an_error():
    with pytest.raises(ValueError):
        shot_list.plan_segments([(0.0, 5.0)], [[]], 5.0)


def test_paths_are_grouped_by_their_line_in_order():
    beats = _beats((0, 0), (1, 1))
    index = {"a": 1, "b": 0, "c": 1, "stray": None}
    assert shot_list.beat_of_each(beats, ["a", "b", "c", "stray"], index.get) == [["b"], ["a", "c"]]


# -- the footage check -----------------------------------------------------------------


def test_the_footage_check_is_told_the_line_a_clip_plays_under(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("FOOTAGE_MODEL", raising=False)
    calls = []

    class Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            raise RuntimeError("stop here")

    monkeypatch.setattr(
        footage_judge, "_client", lambda: type("C", (), {"messages": Messages()})()
    )
    frames = [Frame("1.mp4", b"\xff\xd8", "MRI scanner", "Scientists filmed it inside an MRI.")]
    footage_judge.judge(frames, "knuckles", SCRIPT)
    text = calls[0]["messages"][0]["content"][0]["text"]
    assert 'found by "MRI scanner", plays while the narrator says: "Scientists filmed it' in text
