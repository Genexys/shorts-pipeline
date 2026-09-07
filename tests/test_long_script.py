import pytest

import gpt
from formats import LONG


# -- parse_string_array -----------------------------------------------------


def test_parse_string_array_reads_clean_json():
    assert gpt.parse_string_array('["a", "b"]') == ["a", "b"]


def test_parse_string_array_finds_an_array_inside_prose():
    assert gpt.parse_string_array('Sure! ["a", "b"] hope that helps') == ["a", "b"]


def test_parse_string_array_falls_back_to_quoted_strings():
    assert gpt.parse_string_array('"first" then "second"') == ["first", "second"]


def test_parse_string_array_drops_non_strings():
    # A number reaching the caller used to crash the join that logs the result.
    assert gpt.parse_string_array('["a", 1, "b", null]') == ["a", "b"]


def test_parse_string_array_drops_blank_entries():
    assert gpt.parse_string_array('["a", "", "   ", "b"]') == ["a", "b"]


def test_parse_string_array_trims_entries():
    assert gpt.parse_string_array('["  a  "]') == ["a"]


@pytest.mark.parametrize("response", ["", "no arrays here", "{}", "[]"])
def test_parse_string_array_returns_empty_for_garbage(response):
    assert gpt.parse_string_array(response) == []


# -- clean_script_text ------------------------------------------------------


def test_clean_script_text_removes_markdown_emphasis():
    assert gpt.clean_script_text("**bold** and #heading") == "bold and heading"


def test_clean_script_text_removes_bracketed_directions():
    assert gpt.clean_script_text("Hello [pause] there (softly)") == "Hello  there "


def test_clean_script_text_keeps_surrounding_whitespace():
    # generate_script splits on blank lines afterwards; trimming here would
    # change which paragraphs it selects.
    assert gpt.clean_script_text("\n\nbody\n\n") == "\n\nbody\n\n"


# -- generate_outline -------------------------------------------------------


def test_generate_outline_returns_headings(monkeypatch):
    monkeypatch.setattr(gpt, "generate_response", lambda p, m: '["One", "Two", "Three"]')
    assert gpt.generate_outline("subject", 3, "model") == ["One", "Two", "Three"]


def test_generate_outline_caps_at_the_requested_count(monkeypatch):
    monkeypatch.setattr(gpt, "generate_response", lambda p, m: '["a","b","c","d","e"]')
    assert len(gpt.generate_outline("subject", 3, "model")) == 3


def test_generate_outline_returns_empty_when_nothing_parses(monkeypatch):
    monkeypatch.setattr(gpt, "generate_response", lambda p, m: "I cannot do that")
    assert gpt.generate_outline("subject", 6, "model") == []


# -- generate_long_script ---------------------------------------------------


def _model(outline, sections):
    """A fake model: first call returns the outline, then one section each."""
    calls = {"n": 0}

    def generate(prompt, ai_model):
        calls["n"] += 1
        if calls["n"] == 1:
            return outline
        return sections[calls["n"] - 2]

    return generate, calls


def test_generate_long_script_joins_every_section(monkeypatch):
    generate, calls = _model('["A", "B", "C"]', ["first.", "second.", "third."])
    monkeypatch.setattr(gpt, "generate_response", generate)

    script = gpt.generate_long_script("subject", 300, "model", "en_us_001", "")

    assert script == "first.\n\nsecond.\n\nthird."
    # One call to plan, then one per section: the point of the whole function.
    assert calls["n"] == 4


def test_generate_long_script_returns_none_without_an_outline(monkeypatch):
    monkeypatch.setattr(gpt, "generate_response", lambda p, m: "nope")
    assert gpt.generate_long_script("subject", 300, "model", "en_us_001", "") is None


def test_generate_long_script_survives_one_failed_section(monkeypatch):
    calls = {"n": 0}

    def generate(prompt, ai_model):
        calls["n"] += 1
        if calls["n"] == 1:
            return '["A", "B"]'
        if calls["n"] == 2:
            raise RuntimeError("ollama fell over")
        return "second."

    monkeypatch.setattr(gpt, "generate_response", generate)

    # Losing one section is worth it; losing the video is not.
    assert gpt.generate_long_script("subject", 300, "model", "en_us_001", "") == "second."


def test_generate_long_script_returns_none_if_every_section_is_empty(monkeypatch):
    generate, _ = _model('["A", "B"]', ["", "   "])
    monkeypatch.setattr(gpt, "generate_response", generate)
    assert gpt.generate_long_script("subject", 300, "model", "en_us_001", "") is None


def test_generate_long_script_cleans_each_section(monkeypatch):
    generate, _ = _model('["A"]', ["**bold** text [aside]"])
    monkeypatch.setattr(gpt, "generate_response", generate)
    assert gpt.generate_long_script("subject", 300, "model", "en_us_001", "") == "bold text"


def test_generate_long_script_shows_each_section_the_whole_outline(monkeypatch):
    prompts = []

    def generate(prompt, ai_model):
        prompts.append(prompt)
        return '["Hook", "Middle", "Close"]' if len(prompts) == 1 else "text."

    monkeypatch.setattr(gpt, "generate_response", generate)
    gpt.generate_long_script("subject", 300, "model", "en_us_001", "")

    # Without the plan in view the model repeats itself and drifts — the
    # failure this function exists to avoid.
    for prompt in prompts[1:]:
        assert "Hook" in prompt and "Middle" in prompt and "Close" in prompt


def test_generate_long_script_asks_for_a_sane_section_length(monkeypatch):
    prompts = []

    def generate(prompt, ai_model):
        prompts.append(prompt)
        return '["A", "B", "C", "D", "E", "F"]' if len(prompts) == 1 else "text."

    monkeypatch.setattr(gpt, "generate_response", generate)
    gpt.generate_long_script("subject", LONG.target_words, "model", "en_us_001", "")

    # 550 over 6 sections is ~91 words, the size the model already handles for
    # Shorts.
    assert "About 91 words" in prompts[1]


def test_generate_long_script_never_asks_for_a_scrap(monkeypatch):
    prompts = []

    def generate(prompt, ai_model):
        prompts.append(prompt)
        return '["A", "B", "C"]' if len(prompts) == 1 else "text."

    monkeypatch.setattr(gpt, "generate_response", generate)
    gpt.generate_long_script("subject", 30, "model", "en_us_001", "")

    assert f"About {gpt.LONG_SECTION_MIN_WORDS} words" in prompts[1]


def test_generate_long_script_passes_the_custom_prompt_through(monkeypatch):
    prompts = []

    def generate(prompt, ai_model):
        prompts.append(prompt)
        return '["A"]' if len(prompts) == 1 else "text."

    monkeypatch.setattr(gpt, "generate_response", generate)
    gpt.generate_long_script("subject", 300, "model", "en_us_001", "Be sceptical.")

    assert "Be sceptical." in prompts[1]


def test_generate_outline_asks_for_non_overlapping_sections(monkeypatch):
    prompts = []

    def generate(prompt, ai_model):
        prompts.append(prompt)
        return '["A", "B"]'

    monkeypatch.setattr(gpt, "generate_response", generate)
    gpt.generate_outline("subject", 2, "model")

    # Observed on a real generation: without this the model plans overlapping
    # sections and the same idea gets explained three times.
    assert "must not overlap" in prompts[0]
