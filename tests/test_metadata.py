import gpt
from gpt import extract_json_object, generate_metadata, validate_metadata


def test_extract_json_object_parses_clean_json():
    assert extract_json_object('{"title": "A"}') == {"title": "A"}


def test_extract_json_object_finds_object_inside_noise():
    text = 'Sure! Here you go:\n```json\n{"title": "A", "tags": ["x"]}\n```\nEnjoy'
    assert extract_json_object(text) == {"title": "A", "tags": ["x"]}


def test_extract_json_object_returns_none_for_garbage():
    assert extract_json_object("no json here") is None
    assert extract_json_object('["a", "b"]') is None


def test_extract_json_object_ignores_stray_brace_after_object():
    text = 'Here is the JSON: {"title": "A", "description": "B", "tags": ["c"]} (note: braces like } are fine)'
    assert extract_json_object(text) == {"title": "A", "description": "B", "tags": ["c"]}


def test_extract_json_object_handles_braces_inside_string_value():
    text = '{"title": "Use {curly} braces", "description": "x", "tags": []}'
    assert extract_json_object(text) == {
        "title": "Use {curly} braces",
        "description": "x",
        "tags": [],
    }


def test_extract_json_object_skips_non_dict_fragment_and_finds_next_object():
    text = '{not json} {"title": "T", "description": "D", "tags": []}'
    assert extract_json_object(text) == {"title": "T", "description": "D", "tags": []}


def test_validate_metadata_cleans_markdown_and_truncates_title():
    long_title = "**Why** the #sky is \"blue\" " + "word " * 40
    title, _, _ = validate_metadata({"title": long_title, "description": "d"}, "sky")

    assert "*" not in title and "#" not in title and '"' not in title
    assert len(title) <= 100
    assert not title.endswith(" ")
    assert title.startswith("Why the sky is blue")


def test_validate_metadata_takes_first_line_of_title():
    title, _, _ = validate_metadata({"title": "First line\nSecond line"}, "s")
    assert title == "First line"


def test_validate_metadata_falls_back_to_capitalized_subject():
    title, description, tags = validate_metadata({}, "why cats purr")
    assert title == "Why cats purr"
    assert description == "Why cats purr"
    assert tags == []

    title, _, _ = validate_metadata({"title": "ab"}, "short title case")
    assert title == "Short title case"


def test_validate_metadata_truncates_description():
    _, description, _ = validate_metadata({"description": "x" * 5000}, "s")
    assert len(description) == 4500


def test_validate_metadata_limits_tags():
    raw_tags = ["a,b", "c" * 31, 42, " spaced  tag ", "dup", "DUP"] + [f"tag{i}" for i in range(20)]
    _, _, tags = validate_metadata({"tags": raw_tags}, "s")

    assert tags[0] == "a b"
    assert all("," not in tag for tag in tags)
    assert all(len(tag) <= 30 for tag in tags)
    assert "c" * 31 not in tags
    assert "spaced tag" in tags
    assert tags.count("dup") == 1 and "DUP" not in tags
    assert len(tags) <= 15
    assert sum(len(tag) for tag in tags) <= 400


def test_validate_metadata_respects_total_tag_length():
    raw_tags = ["x" * 30] * 20
    _, _, tags = validate_metadata({"tags": raw_tags}, "s")
    assert len(tags) == 1  # duplicates removed, single 30-char tag


def test_generate_metadata_makes_single_call_and_validates(monkeypatch):
    prompts: list[str] = []

    def fake_generate_response(prompt: str, ai_model: str) -> str:
        prompts.append(prompt)
        return 'Here: {"title": "**Big** title", "description": "Desc", "tags": ["a", "b,c"]}'

    monkeypatch.setattr(gpt, "generate_response", fake_generate_response)

    title, description, tags = generate_metadata("subject", "script text", "model")

    assert len(prompts) == 1
    assert "script text" in prompts[0]
    assert title == "Big title"
    assert description == "Desc"
    assert tags == ["a", "b c"]


def test_generate_metadata_falls_back_when_response_is_not_json(monkeypatch):
    monkeypatch.setattr(gpt, "generate_response", lambda prompt, ai_model: "I cannot do that")

    title, description, tags = generate_metadata("ocean facts", "script", "model")

    assert title == "Ocean facts"
    assert description == "Ocean facts"
    assert tags == []
