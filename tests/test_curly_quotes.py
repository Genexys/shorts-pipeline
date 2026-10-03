import pytest

import gpt


# The writer's answer for job a9e1a6f6 on 2026-10-01, "Why does a glass of
# water sweat on a hot day?", verbatim from the worker log.
CONDENSATION_RESPONSE = (
    "[“condensation on glass”, “iced drink glass”, “water droplets forming”, "
    "“ice cubes melting”, “humid summer day”]"
)


def test_curly_quoted_array_parses():
    assert gpt.parse_string_array(CONDENSATION_RESPONSE) == [
        "condensation on glass",
        "iced drink glass",
        "water droplets forming",
        "ice cubes melting",
        "humid summer day",
    ]


def test_curly_quoted_array_parses_inside_prose():
    response = f"Here are the search terms:\n{CONDENSATION_RESPONSE}\nHope that helps."
    assert gpt.parse_string_array(response)[0] == "condensation on glass"


def test_low_opening_quotes_parse():
    # German-style „…“ pairs: the opening quote sits on the baseline.
    assert gpt.parse_string_array("[„dew point“, „cold glass“]") == [
        "dew point",
        "cold glass",
    ]


def test_apostrophes_inside_terms_are_kept():
    assert gpt.parse_string_array("[“Newton’s cradle”, “pendulum swing”]") == [
        "Newton’s cradle",
        "pendulum swing",
    ]


def test_curly_quotes_inside_a_valid_array_stay_content():
    # Straightening first would turn this into invalid JSON and split the term.
    assert gpt.parse_string_array('["the “big” bang", "galaxy"]') == [
        "the “big” bang",
        "galaxy",
    ]


def test_mixed_straight_and_curly_quotes_parse():
    assert gpt.parse_string_array('[“dew point”, "cold glass"]') == [
        "dew point",
        "cold glass",
    ]


def test_curly_quoted_strings_without_brackets_fall_back_to_quoted_strings():
    assert gpt.parse_string_array("“dew point” and “cold glass”") == [
        "dew point",
        "cold glass",
    ]


@pytest.mark.parametrize("response", [None, "", "no arrays here", "“”"])
def test_nothing_usable_is_still_empty(response):
    assert gpt.parse_string_array(response) == []


def test_search_terms_survive_curly_quotes(monkeypatch):
    warnings = []
    # write_creative rather than the writer underneath it, so the test does not
    # depend on how the writer reports which model answered.
    monkeypatch.setattr(
        gpt, "write_creative",
        lambda prompt, model, report_model=None: CONDENSATION_RESPONSE,
    )
    monkeypatch.setattr(
        gpt, "log",
        lambda message, level="info": warnings.append(message) if level == "warning" else None,
    )

    terms = gpt.get_search_terms("Why does a glass of water sweat?", 5, "script", "model")

    assert terms[:2] == ["condensation on glass", "iced drink glass"]
    assert not any("no usable search terms" in message for message in warnings)
