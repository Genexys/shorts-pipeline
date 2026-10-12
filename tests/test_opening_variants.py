import json

import gpt

SCRIPT = (
    "Onions make you cry because cutting them releases a gas. "
    "That gas reaches your eyes and turns into a mild acid. "
    "Your eyes flush it out with tears."
)


def chosen(text):
    return lambda prompt, model: json.dumps({"variants": [text], "chosen": text})


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("OPENING_VARIANTS", raising=False)
    assert gpt.opening_variant_count() == 0
    monkeypatch.setattr(gpt, "generate_response", lambda p, m: (_ for _ in ()).throw(AssertionError))
    assert gpt.strengthen_opening(SCRIPT, "onions") == SCRIPT


def test_count_is_read_and_bounded(monkeypatch):
    monkeypatch.setenv("OPENING_VARIANTS", "4")
    assert gpt.opening_variant_count() == 4
    monkeypatch.setenv("OPENING_VARIANTS", "50")
    assert gpt.opening_variant_count() == 8
    monkeypatch.setenv("OPENING_VARIANTS", "lots")
    assert gpt.opening_variant_count() == 0


def test_a_stronger_opening_replaces_the_first_sentence(monkeypatch):
    better = "The onion on your cutting board fights back with a gas."
    monkeypatch.setattr(gpt, "generate_response", chosen(better))

    result = gpt.strengthen_opening(SCRIPT, "onions", count=3)

    assert result.startswith(better)
    assert result.endswith("Your eyes flush it out with tears.")


def test_the_prompt_carries_the_arm_rules(monkeypatch):
    prompts = []
    monkeypatch.setattr(gpt, "generate_response",
                        lambda p, m: prompts.append(p) or json.dumps({"chosen": ""}))
    gpt.strengthen_opening(SCRIPT, "onions", opening_arm=gpt.OPENING_EVERYDAY, count=3)
    assert "viewer's own life" in prompts[0]
    assert "FIRST sentence must contain the surprising thing" in prompts[0]


def test_an_opening_with_a_new_figure_is_refused(monkeypatch):
    monkeypatch.setattr(gpt, "generate_response", chosen("Onions release 40 gases when cut."))
    assert gpt.strengthen_opening(SCRIPT, "onions", count=3) == SCRIPT


def test_an_overlong_opening_is_refused(monkeypatch):
    long = " ".join(["word"] * 20) + "."
    monkeypatch.setattr(gpt, "generate_response", chosen(long))
    assert gpt.strengthen_opening(SCRIPT, "onions", count=3) == SCRIPT


def test_a_weak_opening_is_refused(monkeypatch):
    monkeypatch.setattr(gpt, "generate_response", chosen("Have you ever wondered why onions sting?"))
    assert gpt.strengthen_opening(SCRIPT, "onions", count=3) == SCRIPT


def test_no_model_keeps_the_script(monkeypatch):
    def down(prompt, model):
        raise gpt.ModelUnavailable("down")

    monkeypatch.setattr(gpt, "generate_response", down)
    assert gpt.strengthen_opening(SCRIPT, "onions", count=3) == SCRIPT
