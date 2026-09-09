import pytest

import elevenlabs_voice
import speech
from formats import LONG, SHORT


# -- provider choice --------------------------------------------------------


def test_short_narrates_through_elevenlabs():
    # It used to stay on the free service to save credits. The saving was not
    # worth it: the default TikTok voice is the most recognisable synthetic
    # voice on the internet and reads as an automated upload immediately.
    assert SHORT.elevenlabs_voice_id == "Gfpl8Yo74Is0W6cPUWWT"


def test_long_form_uses_elevenlabs():
    assert LONG.elevenlabs_voice_id


def test_choose_provider_needs_both_a_voice_and_a_key(monkeypatch):
    monkeypatch.setattr(elevenlabs_voice, "is_configured", lambda: True)
    assert speech.choose_provider("voice-id") == speech.ELEVENLABS
    assert speech.choose_provider(None) == speech.TIKTOK


def test_choose_provider_falls_back_without_a_key(monkeypatch):
    # A format asking for ElevenLabs on a deployment with no key must still run.
    monkeypatch.setattr(elevenlabs_voice, "is_configured", lambda: False)
    assert speech.choose_provider("voice-id") == speech.TIKTOK


# -- narration --------------------------------------------------------------


@pytest.fixture
def paths(tmp_path):
    counter = {"n": 0}

    def make_path():
        counter["n"] += 1
        return str(tmp_path / f"{counter['n']}.mp3")

    return make_path


def test_narrates_every_sentence_with_tiktok(monkeypatch, paths):
    spoken = []
    monkeypatch.setattr(
        speech.tiktokvoice, "tts",
        lambda text, voice, filename: spoken.append((text, voice)),
    )
    result, provider = speech.synthesize_sentences(["one", "two"], paths, "en_us_001")

    assert provider == speech.TIKTOK
    assert [text for text, _ in spoken] == ["one", "two"]
    assert len(result) == 2


def test_narrates_with_elevenlabs_when_configured(monkeypatch, paths):
    spoken = []
    monkeypatch.setattr(elevenlabs_voice, "is_configured", lambda: True)
    monkeypatch.setattr(
        elevenlabs_voice, "tts",
        lambda text, voice_id, filename, model, **kwargs: spoken.append((text, voice_id)),
    )
    result, provider = speech.synthesize_sentences(
        ["one", "two"], paths, "en_us_001", "george"
    )

    assert provider == speech.ELEVENLABS
    assert [voice for _, voice in spoken] == ["george", "george"]
    assert len(result) == 2


def test_a_failure_redoes_the_whole_video_not_one_sentence(monkeypatch, paths):
    tiktok_calls = []
    monkeypatch.setattr(elevenlabs_voice, "is_configured", lambda: True)

    def flaky(text, voice_id, filename):
        # Quota runs out mid-video; this is not a rare path.
        if text == "three":
            raise RuntimeError("quota exceeded")

    monkeypatch.setattr(elevenlabs_voice, "tts", flaky)
    monkeypatch.setattr(
        speech.tiktokvoice, "tts",
        lambda text, voice, filename: tiktok_calls.append(text),
    )

    result, provider = speech.synthesize_sentences(
        ["one", "two", "three", "four"], paths, "en_us_001", "george"
    )

    # Splicing two narrators into one video is worse than the cheaper voice
    # throughout, so every sentence is redone.
    assert provider == speech.TIKTOK
    assert tiktok_calls == ["one", "two", "three", "four"]
    assert len(result) == 4


def test_the_fallback_is_reported(monkeypatch, paths):
    messages = []
    monkeypatch.setattr(elevenlabs_voice, "is_configured", lambda: True)

    def boom(text, voice_id, filename):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(elevenlabs_voice, "tts", boom)
    monkeypatch.setattr(speech.tiktokvoice, "tts", lambda text, voice, filename: None)

    speech.synthesize_sentences(
        ["one"], paths, "en_us_001", "george",
        on_log=lambda message, level: messages.append((message, level)),
    )

    assert any("ElevenLabs unavailable" in m and level == "warning"
               for m, level in messages)


def test_paths_are_distinct_per_sentence(monkeypatch, paths):
    monkeypatch.setattr(speech.tiktokvoice, "tts", lambda text, voice, filename: None)
    result, _ = speech.synthesize_sentences(["a", "b", "c"], paths, "en_us_001")
    assert len(set(result)) == 3


# -- key handling -----------------------------------------------------------


def test_key_is_scrubbed_from_messages():
    assert "sk-secret" not in elevenlabs_voice.scrub(
        "failed with sk-secret in it", "sk-secret"
    )


def test_scrub_is_a_no_op_without_a_key():
    assert elevenlabs_voice.scrub("plain text", "") == "plain text"


def test_tts_refuses_without_a_key(monkeypatch):
    monkeypatch.setattr(elevenlabs_voice, "api_key", lambda: "")
    with pytest.raises(RuntimeError, match="ELEVENLABS_API_KEY"):
        elevenlabs_voice.tts("hello", "voice", "/tmp/x.mp3")


# -- model choice -----------------------------------------------------------


def test_shorts_use_the_cheaper_model():
    # Flash bills half a credit per character. Over three Shorts a day that is
    # the difference between fitting a 60k plan and overrunning it.
    assert SHORT.elevenlabs_model == "eleven_flash_v2_5"


def test_long_form_uses_the_better_model():
    # Same price as v2 multilingual and newer; minutes of narration are where
    # it earns its keep.
    assert LONG.elevenlabs_model == "eleven_v3"


def test_the_model_reaches_the_api(monkeypatch, paths):
    captured = {}
    monkeypatch.setattr(elevenlabs_voice, "is_configured", lambda: True)
    monkeypatch.setattr(
        elevenlabs_voice, "tts",
        lambda text, voice_id, filename, model, **kwargs: captured.update(model=model),
    )
    speech.synthesize_sentences(
        ["one"], paths, "en_male_narration", "voice", "eleven_flash_v2_5"
    )
    assert captured["model"] == "eleven_flash_v2_5"


def test_a_missing_model_falls_back_to_the_module_default(monkeypatch, paths):
    captured = {}
    monkeypatch.setattr(elevenlabs_voice, "is_configured", lambda: True)
    monkeypatch.setattr(
        elevenlabs_voice, "tts",
        lambda text, voice_id, filename, model, **kwargs: captured.update(model=model),
    )
    speech.synthesize_sentences(["one"], paths, "en_male_narration", "voice")
    assert captured["model"] == elevenlabs_voice.ELEVENLABS_MODEL


def test_tts_sends_the_model_it_was_given(monkeypatch):
    captured = {}

    class Response:
        content = b"audio"
        def raise_for_status(self): pass

    monkeypatch.setattr(elevenlabs_voice, "api_key", lambda: "k")
    monkeypatch.setattr(
        elevenlabs_voice.requests, "post",
        lambda url, **kw: captured.update(kw) or Response(),
    )
    elevenlabs_voice.tts("hi", "voice", "/tmp/x.mp3", "eleven_v3")
    assert captured["json"]["model_id"] == "eleven_v3"


# -- narration planning ------------------------------------------------------


SCRIPT = "First section. It has two sentences.\n\nSecond section here.\n\nThird one."


def test_split_sections_finds_the_paragraph_blocks():
    assert speech.split_sections(SCRIPT) == [
        "First section. It has two sentences.",
        "Second section here.",
        "Third one.",
    ]


def test_split_sentences_never_spans_a_paragraph():
    # The old `script.split(". ")` merged the last sentence of one section with
    # the first of the next, so the join was narrated without a break.
    plan = speech.narration_plan(SCRIPT, by_section=False)
    assert plan == [
        ["First section.", "It has two sentences."],
        ["Second section here."],
        ["Third one."],
    ]
    assert all("\n" not in chunk for section in plan for chunk in section)


def test_narration_plan_by_section_keeps_a_paragraph_whole():
    assert speech.narration_plan(SCRIPT, by_section=True) == [
        ["First section. It has two sentences."],
        ["Second section here."],
        ["Third one."],
    ]


def test_split_sentences_handles_questions_and_exclamations():
    assert speech.split_sentences("Why? Because it works! Truly.") == [
        "Why?",
        "Because it works!",
        "Truly.",
    ]


def test_narration_plan_of_an_empty_script_is_empty():
    assert speech.narration_plan("   \n\n  ", by_section=True) == []


def test_elevenlabs_gets_neighbouring_text_for_continuity(monkeypatch, paths):
    calls = []

    def fake_tts(text, voice_id, path, model, previous_text=None, next_text=None):
        calls.append((text, previous_text, next_text))
        open(path, "w").close()

    monkeypatch.setattr(speech.elevenlabs_voice, "tts", fake_tts)
    monkeypatch.setattr(speech.elevenlabs_voice, "is_configured", lambda: True)

    paths, provider = speech.synthesize_sentences(
        ["One.", "Two.", "Three."],
        make_path=paths,
        tiktok_voice="en_us_001",
        elevenlabs_voice_id="v1",
    )

    assert provider == speech.ELEVENLABS
    assert calls[0][1] is None and calls[0][2] == "Two."
    assert calls[1][1] == "One." and calls[1][2] == "Three."
    assert calls[2][1] == "Two." and calls[2][2] is None
