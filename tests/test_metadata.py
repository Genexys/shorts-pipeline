import time

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
    text = 'Result: {"title": "Use {curly} braces", "description": "x", "tags": []} done'
    result = extract_json_object(text)
    assert result == {
        "title": "Use {curly} braces",
        "description": "x",
        "tags": [],
    }
    assert result["title"] == "Use {curly} braces"


def test_extract_json_object_skips_non_dict_fragment_and_finds_next_object():
    text = '{not json} {"title": "T", "description": "D", "tags": []}'
    assert extract_json_object(text) == {"title": "T", "description": "D", "tags": []}


def test_extract_json_object_gives_up_on_pathological_input():
    start = time.perf_counter()
    result = extract_json_object("{" * 50000)
    elapsed = time.perf_counter() - start

    assert result is None
    assert elapsed < 1.0

    text = "{" * 100 + '{"title": "T", "description": "D", "tags": []}'
    assert extract_json_object(text) is None


def test_validate_metadata_cleans_markdown_and_truncates_title():
    long_title = "**Why** the #sky is \"blue\" " + "word " * 40
    title, _, _ = validate_metadata({"title": long_title, "description": "d"}, "sky")

    assert "*" not in title and "#" not in title and '"' not in title
    assert len(title) <= 100
    assert not title.endswith(" ")
    assert title.startswith("Why the sky is blue")


def test_validate_metadata_strips_angle_brackets_from_title_and_description():
    title, description, _ = validate_metadata(
        {
            "title": 'Why 3 < 5 "matters" #now',
            "description": "Intro <bold> text",
        },
        "s",
    )

    assert "<" not in title and ">" not in title
    assert title == "Why 3 5 matters now"
    assert "<" not in description and ">" not in description
    assert description == "Intro bold text"


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


def test_validate_metadata_packs_shorter_tag_after_oversized_one():
    # An oversized tag is skipped, not a stop sign: a shorter tag after it
    # should still be packed into the budget.
    raw_tags = ["a" * 40, "short"]
    _, _, tags = validate_metadata({"tags": raw_tags}, "s")
    assert "short" in tags


def test_validate_metadata_packs_shorter_tag_after_budget_overflow(monkeypatch):
    # A tag that individually fits TAG_MAX_CHARS but would blow the
    # total-length budget must not stop shorter tags after it from being
    # packed in (continue, not break).
    import gpt as gpt_module

    monkeypatch.setattr(gpt_module, "TAGS_MAX_TOTAL_CHARS", 10)
    raw_tags = ["x" * 20, "short"]
    _, _, tags = validate_metadata({"tags": raw_tags}, "s")
    assert "short" in tags


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
    # Hashtags are appended to every description, so the summary is the prefix.
    assert description.startswith("Desc")
    assert description.endswith("#Shorts #A #BC")
    assert tags == ["a", "b c"]


def test_generate_metadata_falls_back_when_response_is_not_json(monkeypatch):
    monkeypatch.setattr(gpt, "generate_response", lambda prompt, ai_model: "I cannot do that")

    title, description, tags = generate_metadata("ocean facts", "script", "model")

    assert title == "Ocean facts"
    assert description.startswith("Ocean facts")
    assert "#Shorts" in description
    # Derived from the subject rather than left empty: an unparseable answer
    # used to publish a video with no tags at all.
    assert tags == ["ocean", "facts"]


# -- hashtags ---------------------------------------------------------------


def test_to_hashtag_camel_cases_words():
    assert gpt.to_hashtag("ocean pressure") == "#OceanPressure"


def test_to_hashtag_strips_punctuation():
    assert gpt.to_hashtag("deep-sea, life!") == "#DeepSeaLife"


def test_to_hashtag_rejects_unusable_text():
    assert gpt.to_hashtag("!!!") == ""
    assert gpt.to_hashtag("") == ""


def test_to_hashtag_rejects_overlong_result():
    assert gpt.to_hashtag("a" * (gpt.HASHTAG_MAX_CHARS + 5)) == ""


def test_build_hashtags_always_includes_the_required_ones():
    assert gpt.build_hashtags([], "") == list(gpt.ALWAYS_HASHTAGS)


def test_build_hashtags_derives_from_tags():
    hashtags = gpt.build_hashtags(["deep sea", "marine biology"], "subject")
    assert hashtags[0] == "#Shorts"
    assert "#DeepSea" in hashtags and "#MarineBiology" in hashtags


def test_build_hashtags_falls_back_to_subject_when_tags_are_unusable():
    hashtags = gpt.build_hashtags(["!!!", "-"], "octopus puzzle solving")
    assert hashtags == ["#Shorts", "#OctopusPuzzleSolving"]


def test_build_hashtags_deduplicates_case_insensitively():
    hashtags = gpt.build_hashtags(["shorts", "Deep Sea", "deep sea"], "subject")
    assert hashtags.count("#Shorts") == 1
    assert hashtags.count("#DeepSea") == 1


def test_build_hashtags_respects_the_cap():
    tags = [f"tag number {i}" for i in range(20)]
    assert len(gpt.build_hashtags(tags, "subject")) == gpt.HASHTAG_MAX_COUNT


def test_append_hashtags_puts_them_on_their_own_line():
    result = gpt.append_hashtags("A summary.", ["#Shorts", "#DeepSea"])
    assert result == "A summary.\n\n#Shorts #DeepSea"


def test_append_hashtags_keeps_the_description_within_the_limit():
    long_description = "x" * gpt.DESCRIPTION_MAX_CHARS
    result = gpt.append_hashtags(long_description, ["#Shorts"])
    assert len(result) <= gpt.DESCRIPTION_MAX_CHARS
    assert result.endswith("#Shorts")


def test_generate_metadata_always_appends_hashtags(monkeypatch):
    monkeypatch.setattr(
        gpt,
        "generate_response",
        lambda p, m: '{"title": "T", "description": "D", "tags": ["deep sea"]}',
    )
    _title, description, _tags = gpt.generate_metadata("subject", "script", "model")
    assert "#Shorts" in description
    assert "#DeepSea" in description


def test_generate_metadata_appends_hashtags_even_when_the_model_fails(monkeypatch):
    # The whole point of building them in code: a bad generation must not ship
    # a video without hashtags.
    monkeypatch.setattr(gpt, "generate_response", lambda p, m: "not json at all")
    _title, description, _tags = gpt.generate_metadata("octopus facts", "script", "model")
    assert "#Shorts" in description


def test_long_form_does_not_claim_to_be_a_short():
    # #Shorts on a three-minute landscape video misleads the viewer and the
    # platform about what it is.
    from formats import LONG, SHORT

    assert LONG.always_hashtags == ()
    assert SHORT.always_hashtags == ("#Shorts",)


def test_build_hashtags_honours_the_format(monkeypatch):
    from formats import LONG

    hashtags = gpt.build_hashtags(["deep ocean"], "subject", LONG.always_hashtags)
    assert "#Shorts" not in hashtags
    assert "#DeepOcean" in hashtags


def test_long_form_still_gets_topical_hashtags(monkeypatch):
    from formats import LONG

    monkeypatch.setattr(
        gpt, "generate_response",
        lambda p, m: '{"title":"T","description":"D","tags":["deep ocean","glow"]}',
    )
    _t, description, _k = gpt.generate_metadata(
        "subject", "script", "model", LONG.always_hashtags
    )
    assert "#Shorts" not in description
    assert "#DeepOcean" in description


def test_validate_metadata_splits_joined_tag_words():
    # Observed from a real run: the model returned "atmosphere-scattering" and
    # "light_scattering". YouTube accepts them, but search matches the spaced
    # form, so a joined tag costs reach for nothing.
    _t, _d, tags = validate_metadata(
        {"tags": ["atmosphere-scattering", "light_scattering", "blue sky"]}, "subject"
    )
    assert tags == ["atmosphere scattering", "light scattering", "blue sky"]


def test_validate_metadata_still_collapses_whitespace_in_tags():
    _t, _d, tags = validate_metadata({"tags": ["  deep   ocean  "]}, "subject")
    assert tags == ["deep ocean"]


def test_metadata_prompt_asks_for_spaced_tags(monkeypatch):
    prompts = []
    monkeypatch.setattr(
        gpt, "generate_response",
        lambda p, m: prompts.append(p) or '{"title":"T","description":"D","tags":["a"]}',
    )
    gpt.generate_metadata("subject", "script", "model")
    assert "No hyphens" in prompts[0]


def test_keywords_from_subject_drops_question_scaffolding():
    assert gpt.keywords_from_subject("Can plants grow in space without light?") == [
        "plants",
        "grow",
        "space",
        "light",
    ]


def test_keywords_from_subject_deduplicates_and_caps():
    keywords = gpt.keywords_from_subject(
        "Space space rockets engines fuel orbit gravity", limit=3
    )
    assert keywords == ["space", "rockets", "engines"]


def test_keywords_from_subject_survives_an_empty_subject():
    assert gpt.keywords_from_subject("") == []


def test_build_hashtags_falls_back_past_an_overlong_subject():
    # The real 2026-09-09 failure: "#CanPlantsGrowInSpaceWithoutLight" is 33
    # characters, to_hashtag returns "", and long form forces no hashtags, so
    # the video published with none at all.
    subject = "Can plants grow in space without light?"
    assert gpt.to_hashtag(subject) == ""
    hashtags = gpt.build_hashtags([], subject, ())
    assert hashtags == ["#Plants", "#Grow", "#Space"]


def test_build_hashtags_prefers_the_whole_subject_when_it_fits():
    assert gpt.build_hashtags([], "Why do we hiccup?", ())[0] == "#WhyDoWeHiccup"


def test_generate_metadata_derives_tags_when_the_model_returns_none(monkeypatch):
    monkeypatch.setattr(
        gpt, "generate_response",
        lambda p, m: '{"title":"T","description":"D","tags":[]}',
    )
    _t, description, tags = gpt.generate_metadata(
        "Can plants grow in space without light?", "script", "model", ()
    )
    assert tags == ["plants", "grow", "space", "light"]
    assert "#Plants" in description


def test_generate_metadata_derives_tags_when_the_model_returns_junk(monkeypatch):
    monkeypatch.setattr(
        gpt, "generate_response", lambda p, m: "not json at all"
    )
    _t, _d, tags = gpt.generate_metadata(
        "How do octopuses taste with their arms?", "script", "model", ()
    )
    assert tags == ["octopuses", "taste", "arms"]


def test_generate_metadata_prompt_names_the_actual_format(monkeypatch):
    from formats import LONG, SHORT

    prompts = []
    monkeypatch.setattr(
        gpt, "generate_response",
        lambda p, m: prompts.append(p) or '{"title":"T","description":"D","tags":["a"]}',
    )
    gpt.generate_metadata("s", "script", "model", LONG.always_hashtags, LONG.metadata_label)
    assert "Shorts" not in prompts[0]
    assert "landscape" in prompts[0]

    gpt.generate_metadata("s", "script", "model", SHORT.always_hashtags, SHORT.metadata_label)
    assert "Shorts" in prompts[1]
