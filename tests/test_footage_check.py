"""Stock clips are looked at before they are used.

On 2026-10-03 the tomato Short opened on an AI-drawn pelican and the
Neanderthal Short on a cartoon dog, both Pixabay "animation" clips that won the
opening on contrast. The same videos showed a tennis court for "supreme court
building" and a Halloween skull for "Neanderthal skull".
"""

import base64
import io
import json
import subprocess
import types

from PIL import Image

import footage_judge
import video
from footage_judge import CARTOON, FOOTAGE, RENDER, Frame, Verdict
from formats import SHORT

SUBJECT = "In 1893 the Supreme Court ruled tomatoes are vegetables"
NARRATION = "The Supreme Court ruled unanimously that tomatoes are vegetables, not fruit."


def _response(verdicts, stop_reason="end_turn"):
    block = types.SimpleNamespace(type="text", text=json.dumps({"clips": verdicts}))
    return types.SimpleNamespace(content=[block], stop_reason=stop_reason)


def _fake_client(monkeypatch, replies, calls):
    """Each create() returns (or raises) the next reply."""
    replies = list(replies)

    def create(**kwargs):
        calls.append(kwargs)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    client = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
    monkeypatch.setattr(footage_judge, "_client", lambda: client)


def _configured(monkeypatch, model=None):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    if model is None:
        monkeypatch.delenv("FOOTAGE_MODEL", raising=False)
    else:
        monkeypatch.setenv("FOOTAGE_MODEL", model)


def _frames(*terms):
    return [Frame(f"{index}.mp4", b"\xff\xd8jpeg", term) for index, term in enumerate(terms, 1)]


# -- the verdict ----------------------------------------------------------------------


def test_a_cartoon_is_never_kept_even_when_it_suits_the_topic():
    assert not Verdict(CARTOON, True, "").keep
    assert Verdict(RENDER, True, "").keep
    assert Verdict(FOOTAGE, True, "").keep
    assert not Verdict(FOOTAGE, False, "a tennis court").keep


# -- configuration --------------------------------------------------------------------


def test_the_model_defaults_to_sonnet(monkeypatch):
    monkeypatch.delenv("FOOTAGE_MODEL", raising=False)
    assert footage_judge.model_name() == "claude-sonnet-5-5"


def test_without_a_key_nothing_is_checked(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert footage_judge.judge(_frames("ripe tomatoes"), SUBJECT, NARRATION) == {}


def test_the_check_can_be_turned_off(monkeypatch):
    _configured(monkeypatch, "off")
    assert footage_judge.is_enabled() is False
    assert footage_judge.judge(_frames("ripe tomatoes"), SUBJECT, NARRATION) == {}


# -- the request ----------------------------------------------------------------------


def test_every_frame_goes_with_its_search_term_and_the_story(monkeypatch):
    _configured(monkeypatch)
    calls = []
    _fake_client(monkeypatch, [_response([])], calls)

    footage_judge.judge(
        _frames("vegetable market stall", "supreme court building"), SUBJECT, NARRATION
    )

    request = calls[0]
    assert request["model"] == "claude-sonnet-5-5"
    assert request["output_config"]["effort"] == "low"
    assert request["output_config"]["format"]["type"] == "json_schema"
    content = request["messages"][0]["content"]
    images = [block for block in content if block["type"] == "image"]
    assert len(images) == 2
    assert base64.standard_b64decode(images[0]["source"]["data"]) == b"\xff\xd8jpeg"
    assert images[0]["source"]["media_type"] == "image/jpeg"
    prompt = content[0]["text"]
    assert SUBJECT in prompt and NARRATION in prompt
    assert 'Clip 2: found by "supreme court building"' in prompt


def test_verdicts_are_filed_under_their_clips(monkeypatch):
    _configured(monkeypatch)
    _fake_client(
        monkeypatch,
        [
            _response(
                [
                    {"clip": 1, "kind": "cartoon", "fits": True, "problem": "an AI pelican"},
                    {"clip": 2, "kind": "footage", "fits": False, "problem": " a tennis court "},
                    {"clip": 7, "kind": "footage", "fits": True, "problem": ""},
                ]
            )
        ],
        [],
    )
    verdicts = footage_judge.judge(
        _frames("vegetable market stall", "supreme court building"), SUBJECT, NARRATION
    )
    assert verdicts == {
        "1.mp4": Verdict(CARTOON, True, "an AI pelican"),
        "2.mp4": Verdict(FOOTAGE, False, "a tennis court"),
    }


def test_a_failed_batch_leaves_only_its_own_clips_unchecked(monkeypatch):
    _configured(monkeypatch)
    monkeypatch.setattr(footage_judge, "BATCH_SIZE", 2)
    _fake_client(
        monkeypatch,
        [
            RuntimeError("529 overloaded"),
            _response([{"clip": 1, "kind": "cartoon", "fits": False, "problem": "a dog"}]),
        ],
        [],
    )
    verdicts = footage_judge.judge(_frames("a", "b", "c"), SUBJECT, NARRATION)
    assert verdicts == {"3.mp4": Verdict(CARTOON, False, "a dog")}


def test_a_refusal_checks_nothing(monkeypatch):
    _configured(monkeypatch)
    _fake_client(
        monkeypatch,
        [_response([{"clip": 1, "kind": "cartoon", "fits": False, "problem": ""}], "refusal")],
        [],
    )
    assert footage_judge.judge(_frames("a"), SUBJECT, NARRATION) == {}


def test_an_unreadable_reply_checks_nothing(monkeypatch):
    _configured(monkeypatch)
    block = types.SimpleNamespace(type="text", text="not json")
    _fake_client(monkeypatch, [types.SimpleNamespace(content=[block], stop_reason="end_turn")], [])
    assert footage_judge.judge(_frames("a"), SUBJECT, NARRATION) == {}


# -- replacing what is rejected -------------------------------------------------------


def _check_from(verdicts, seen=None):
    def check(paths):
        if seen is not None:
            seen.append(list(paths))
        return {path: verdicts[path] for path in paths if path in verdicts}

    return check


GOOD = Verdict(FOOTAGE, True, "")
RENDERED = Verdict(RENDER, True, "")
PELICAN = Verdict(CARTOON, True, "an AI pelican selling fish")
COURT = Verdict(FOOTAGE, False, "a tennis court")


def test_rejected_clips_are_replaced_by_the_next_results():
    footage = {"a": (0.0, 9.0), "pelican": (0.0, 9.0), "court": (0.2, 9.0)}
    asked = []

    def replace(count):
        asked.append(count)
        return {"b": (0.0, 9.0), "c": (0.0, 9.0)}

    kept, openers = footage_judge.vet_footage(
        footage,
        _check_from({"a": GOOD, "pelican": PELICAN, "court": COURT, "b": GOOD, "c": RENDERED}),
        replace,
    )
    assert asked == [2]
    assert list(kept) == ["a", "b", "c"]
    assert kept["a"] == (0.0, 9.0)
    # A realistic render illustrates; only real footage opens.
    assert openers == {"a", "b"}


def test_replacements_are_checked_too_but_only_so_many_times():
    asked = []

    def replace(count):
        asked.append(count)
        return {f"cartoon{len(asked)}": (0.0, 9.0)}

    verdicts = {"a": GOOD, "pelican": PELICAN}
    verdicts.update({f"cartoon{n}": PELICAN for n in range(1, 5)})
    kept, _ = footage_judge.vet_footage(
        {"a": (0.0, 9.0), "pelican": (0.0, 9.0)}, _check_from(verdicts), replace, rounds=2
    )
    assert asked == [1, 1]
    assert list(kept) == ["a"]


def test_a_clip_nobody_judged_is_used_and_may_open():
    # A check that could not run changes nothing.
    kept, openers = footage_judge.vet_footage(
        {"a": (0.0, 9.0), "b": (0.0, 9.0)}, lambda paths: {}, lambda count: {}
    )
    assert list(kept) == ["a", "b"]
    assert openers == {"a", "b"}


def test_nothing_left_to_replace_with_keeps_what_passed():
    kept, _ = footage_judge.vet_footage(
        {"a": (0.0, 9.0), "court": (0.0, 9.0)},
        _check_from({"a": GOOD, "court": COURT}),
        lambda count: {},
    )
    assert list(kept) == ["a"]


def test_if_everything_is_rejected_everything_is_used_after_all():
    # Poor footage beats no video.
    kept, openers = footage_judge.vet_footage(
        {"pelican": (0.0, 9.0), "court": (0.5, 9.0)},
        _check_from({"pelican": PELICAN, "court": COURT}),
        lambda count: {},
    )
    assert kept == {"pelican": (0.0, 9.0), "court": (0.5, 9.0)}
    assert openers == {"pelican", "court"}


def test_only_the_new_clips_are_judged_in_a_later_round():
    seen = []
    footage_judge.vet_footage(
        {"a": (0.0, 9.0), "court": (0.0, 9.0)},
        _check_from({"a": GOOD, "court": COURT, "b": GOOD}, seen),
        lambda count: {"b": (0.0, 9.0)},
    )
    assert seen == [["a", "court"], ["b"]]


# -- the opening ----------------------------------------------------------------------


def test_only_an_eligible_clip_may_open(monkeypatch, tmp_path):
    sampled = []

    def run(command, **kwargs):
        sampled.append(command[command.index("-i") + 1])
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(video.subprocess, "run", run)
    video.promote_strongest_opening(
        ["pelican.mp4", "street.mp4", "dog.mp4"], tmp_path, eligible={"street.mp4"}
    )
    assert sampled == ["street.mp4"]


def test_with_no_eligible_clip_in_the_list_any_may_open(monkeypatch, tmp_path):
    sampled = []

    def run(command, **kwargs):
        sampled.append(command[command.index("-i") + 1])
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(video.subprocess, "run", run)
    video.promote_strongest_opening(["a.mp4", "b.mp4"], tmp_path, eligible={"gone.mp4"})
    assert sampled == ["a.mp4", "b.mp4"]


def _busy_opening(path, black_lead_in):
    """A cartoon-bright clip whose shot only starts after a black lead-in."""
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"color=c=black:s=320x180:d={black_lead_in}:r=15",
            "-f", "lavfi", "-i", "testsrc=s=320x180:d=2:r=15",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True, capture_output=True,
    )
    return str(path)


def test_an_ineligible_clip_is_not_promoted_however_striking(tmp_path):
    flat = tmp_path / "flat.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "color=c=gray:s=320x180:d=2:r=15", "-pix_fmt", "yuv420p", str(flat)],
        check=True, capture_output=True,
    )
    busy = _busy_opening(tmp_path / "busy.mp4", 0.1)
    clips = [str(flat), busy]
    assert video.promote_strongest_opening(clips, tmp_path / "w")[0] == busy
    assert video.promote_strongest_opening(
        clips, tmp_path / "w", eligible={str(flat)}
    ) == clips


# -- the frame the judge sees ---------------------------------------------------------


def test_the_frame_is_cropped_to_the_format_and_small(tmp_path):
    clip = _busy_opening(tmp_path / "wide.mp4", 0.1)
    jpeg = video.sample_frame(clip, 0.5, SHORT)
    assert jpeg[:2] == b"\xff\xd8"
    with Image.open(io.BytesIO(jpeg)) as image:
        width, height = image.size
    assert max(width, height) <= video.CHECK_FRAME_LONG_SIDE
    assert abs(width / height - SHORT.aspect_ratio) < 0.02


def test_an_unreadable_clip_gives_no_frame(tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    assert video.sample_frame(str(broken), 0.5, SHORT) is None


def test_a_replacement_batch_that_is_all_black_adds_nothing(monkeypatch):
    monkeypatch.setattr(video, "probe_duration", lambda path: 10.0)
    monkeypatch.setattr(video, "detect_black_spans", lambda path, probed, fmt: [(0.0, 4.0)])
    assert video.find_usable_footage(["a.mp4"], SHORT, keep_all_if_none=False) == {}
    assert video.find_usable_footage(["a.mp4"], SHORT) == {"a.mp4": (0.0, 10.0)}
