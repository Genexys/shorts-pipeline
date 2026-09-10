import random

import pytest

import posts
from posts import (
    POLL,
    POLL_OPTION_MAX_CHARS,
    POST_MAX_CHARS,
    QUESTION,
    Post,
    build_post,
    build_prompt,
    choose_post_kind,
    clean_options,
    generate_post,
)


# -- options ----------------------------------------------------------------


def test_clean_options_keeps_order_and_collapses_whitespace():
    assert clean_options(["  a  b ", "c"]) == ["a b", "c"]


def test_clean_options_drops_an_overlong_option_rather_than_truncating():
    # A poll option cut mid-word reads worse than a poll with fewer answers.
    long_option = "x" * (POLL_OPTION_MAX_CHARS + 1)
    assert clean_options(["fine", long_option]) == ["fine"]


def test_clean_options_keeps_one_exactly_at_the_limit():
    exact = "x" * POLL_OPTION_MAX_CHARS
    assert clean_options([exact]) == [exact]


def test_clean_options_deduplicates_case_insensitively():
    assert clean_options(["Yes", "yes", "No"]) == ["Yes", "No"]


def test_clean_options_caps_at_four():
    assert len(clean_options([f"option {i}" for i in range(9)])) == 4


def test_clean_options_ignores_non_strings_and_junk():
    assert clean_options(["ok", 5, None, "   "]) == ["ok"]


def test_clean_options_handles_a_non_list():
    assert clean_options("yes, no") == []


# -- building ---------------------------------------------------------------


def test_build_post_returns_none_without_text():
    assert build_post({"text": "   "}, QUESTION) is None
    assert build_post(None, QUESTION) is None


def test_build_post_truncates_an_overlong_body():
    post = build_post({"text": "y" * (POST_MAX_CHARS + 50)}, QUESTION)
    assert len(post.text) == POST_MAX_CHARS


def test_build_post_ignores_options_for_a_non_poll():
    post = build_post({"text": "Why?", "options": ["a", "b"]}, QUESTION)
    assert post.options == ()


def test_build_post_keeps_a_valid_poll():
    post = build_post({"text": "Which?", "options": ["a", "b", "c"]}, POLL)
    assert post.kind == POLL
    assert post.options == ("a", "b", "c")


def test_build_post_demotes_a_poll_with_too_few_options():
    # A poll with one answer is not a poll, but the question is still a post.
    post = build_post({"text": "Which one?", "options": ["only"]}, POLL)
    assert post.kind == QUESTION
    assert post.options == ()
    assert post.text == "Which one?"


def test_render_numbers_poll_options():
    post = Post(kind=POLL, text="Which?", options=("a", "b"))
    assert post.render() == "Which?\n\n  1. a\n  2. b"


def test_render_leaves_a_plain_post_alone():
    assert Post(kind=QUESTION, text="Why?").render() == "Why?"


# -- prompt -----------------------------------------------------------------


def test_prompt_asks_for_poll_options_only_for_a_poll():
    poll_prompt = build_prompt(POLL, "subject", "title", "script", "notes")
    question_prompt = build_prompt(QUESTION, "subject", "title", "script", "notes")
    assert "2 to 4 answers" in poll_prompt
    assert "an empty list" in question_prompt


def test_prompt_says_so_when_no_script_is_stored():
    prompt = build_prompt(QUESTION, "subject", "title", "   ", "notes")
    assert "script is not available" in prompt


def test_prompt_forbids_the_engagement_boilerplate():
    # "Like and subscribe" on every post is the tell this channel cannot afford.
    prompt = build_prompt(QUESTION, "s", "t", "script", "notes")
    assert "like, subscribe or comment" in prompt


def test_prompt_includes_the_script():
    assert "the whole script here" in build_prompt(QUESTION, "s", "t", "the whole script here", "notes")


# -- kinds ------------------------------------------------------------------


def test_choose_post_kind_returns_a_known_kind():
    assert choose_post_kind("a script", random.Random(0), "notes") in posts.POST_KINDS


def test_choose_post_kind_is_not_always_the_same():
    rng = random.Random(1)
    drawn = {choose_post_kind("a script", rng, "notes") for _ in range(40)}
    assert len(drawn) > 1


def test_choose_post_kind_never_picks_fact_without_a_script():
    rng = random.Random(2)
    drawn = {choose_post_kind("", rng, "") for _ in range(60)}
    assert posts.FACT not in drawn
    assert drawn == {posts.QUESTION, posts.POLL}


def test_available_kinds_needs_a_script_for_fact():
    assert posts.FACT in posts.available_kinds("a script", "notes")
    assert posts.FACT not in posts.available_kinds("a script", "   ")


def test_generate_post_refuses_a_fact_without_a_script():
    # A bare "did you know" is a factual claim published in the channel's name.
    # Ungrounded, the model gets the detail wrong in a way that reads fine.
    with pytest.raises(ValueError, match="needs the video's research notes"):
        generate_post("subject", "title", "", "model", kind=posts.FACT)


def test_generate_post_allows_a_fact_when_the_script_is_there(monkeypatch):
    monkeypatch.setattr(
        posts, "generate_response", lambda p, m: '{"text":"A grounded detail.","options":[]}'
    )
    post = generate_post("subject", "title", "the script", "model", kind=posts.FACT, research="notes")
    assert post.kind == posts.FACT


def test_poll_brief_forbids_options_that_mean_the_same_thing():
    # Observed live: a poll offered "Yes, they use the magnetic field" and
    # "Yes, and it is crucial to navigation" as separate answers, splitting the
    # yes vote in two. Exact-match dedup cannot catch that.
    assert "No two options may mean the same thing" in build_prompt(POLL, "s", "t", "script", "notes")


# -- generation -------------------------------------------------------------


def test_generate_post_parses_a_poll(monkeypatch):
    monkeypatch.setattr(
        posts, "generate_response",
        lambda p, m: '{"text":"Which surprised you?","options":["Depth","Pressure"]}',
    )
    post = generate_post("subject", "title", "script", "model", kind=POLL)
    assert post.kind == POLL
    assert post.options == ("Depth", "Pressure")


def test_generate_post_returns_none_on_junk(monkeypatch):
    # No placeholder text: a person is at the terminal and can run it again,
    # and an obviously generated filler post is worse than no post.
    monkeypatch.setattr(posts, "generate_response", lambda p, m: "I cannot do that")
    assert generate_post("subject", "title", "script", "model", kind=QUESTION) is None


def test_generate_post_rejects_an_unknown_kind():
    with pytest.raises(ValueError):
        generate_post("s", "t", "script", "model", kind="essay")


def test_generate_post_picks_a_kind_when_none_is_given(monkeypatch):
    seen = []
    monkeypatch.setattr(
        posts, "generate_response",
        lambda p, m: seen.append(p) or '{"text":"A post.","options":[]}',
    )
    post = generate_post("subject", "title", "script", "model", rng=random.Random(3))
    assert post.kind in posts.POST_KINDS
    assert len(seen) == 1


def test_poll_brief_forbids_giving_the_answer_away():
    # Observed on the first live run: the model wrote "...so it's unlikely they
    # can thrive in space" above its own poll, which collects no signal.
    prompt = build_prompt(POLL, "s", "t", "script", "notes")
    assert "must not state, hint at or reason towards the answer" in prompt


# -- facts come from the notes, not the script -------------------------------


def test_fact_needs_notes_not_a_script():
    # A 120-word Short exhausts its own subject: given only the script, the
    # model can restate it or invent, and nothing else.
    assert posts.FACT not in posts.available_kinds("a full script", "")
    assert posts.FACT in posts.available_kinds("", "[1] Note\nA detail.")


def test_generate_post_refuses_a_fact_without_notes():
    with pytest.raises(ValueError, match="research notes"):
        generate_post("subject", "title", "a script", "model", kind=posts.FACT)


def test_prompt_separates_what_was_said_from_what_was_found():
    prompt = build_prompt(posts.FACT, "s", "t", "THE SCRIPT", "THE NOTES")
    assert "What the video already said" in prompt
    assert "THE SCRIPT" in prompt
    assert "did not reach the video" in prompt
    assert "THE NOTES" in prompt


def test_fact_brief_forbids_restating_the_video():
    # Observed: a post about the immortal jellyfish said only that it is
    # biologically immortal — the video's opening line.
    assert "does NOT already say" in build_prompt(posts.FACT, "s", "t", "sc", "n")


def test_choose_post_kind_offers_facts_once_notes_exist():
    # One generator reused across the draws; a fresh Random(4) per iteration
    # would return the same kind every time and prove nothing.
    rng = random.Random(4)
    drawn = {choose_post_kind("script", rng, "notes") for _ in range(60)}
    assert posts.FACT in drawn
