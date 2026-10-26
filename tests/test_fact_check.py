import json

import fact_check
from fact_check import SUPPORTED, UNSUPPORTED, WRONG

SCRIPT = (
    "Wilson Greatbatch created the first pacemaker by mistake. "
    "He grabbed a resistor of the wrong value in 1956."
)
EVIDENCE = "Greatbatch helped create the implantable pacemaker after using a wrong resistor in 1956."
FIXED = (
    "Wilson Greatbatch helped create the implantable pacemaker by mistake. "
    "He grabbed a resistor of the wrong value in 1956."
)


def reply(claims, script):
    return json.dumps({"claims": [
        {"claim": c, "verdict": v, "evidence": "", "fix": ""} for c, v in claims
    ], "script": script})


class Asker:
    """Hands out replies in order and records the prompts."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def __call__(self, prompt, model):
        self.prompts.append(prompt)
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_a_clean_script_is_kept_and_marked_checked():
    ask = Asker(reply([("1956", SUPPORTED)], SCRIPT))

    result = fact_check.check_script("pacemaker", SCRIPT, EVIDENCE, ask)

    assert result.script == SCRIPT
    assert result.checked and not result.fixed and not result.unresolved
    assert EVIDENCE in ask.prompts[0]


def test_an_overstated_claim_is_corrected_and_the_correction_rechecked():
    # "helped create" told as "created": the shape of a real pinned correction.
    ask = Asker(
        reply([("created the first pacemaker", UNSUPPORTED)], FIXED),
        reply([("helped create the implantable pacemaker", SUPPORTED)], FIXED),
    )

    result = fact_check.check_script("pacemaker", SCRIPT, EVIDENCE, ask)

    assert result.script == FIXED
    assert [c.text for c in result.fixed] == ["created the first pacemaker"]
    assert result.unresolved == []
    assert len(ask.prompts) == 2 and FIXED in ask.prompts[1]


def test_a_problem_that_survives_the_rewrite_is_unresolved():
    ask = Asker(
        reply([("created the first pacemaker", UNSUPPORTED)], FIXED),
        reply([("by mistake", WRONG)], FIXED),
    )

    result = fact_check.check_script("pacemaker", SCRIPT, EVIDENCE, ask)

    assert result.script == FIXED
    assert [c.text for c in result.unresolved] == ["by mistake"]


def test_what_the_recheck_finds_is_corrected_once_more():
    # The droppings Short: the second reading found claims the first had passed.
    second = FIXED.replace(" by mistake", "")
    ask = Asker(
        reply([("created the first pacemaker", UNSUPPORTED)], FIXED),
        reply([("by mistake", UNSUPPORTED)], second),
        reply([("1956", SUPPORTED)], second),
    )

    result = fact_check.check_script("pacemaker", SCRIPT, EVIDENCE, ask)

    assert result.script == second
    assert [c.text for c in result.fixed] == ["created the first pacemaker", "by mistake"]
    assert result.unresolved == []
    assert len(ask.prompts) == 3 and second in ask.prompts[2]


def test_after_two_corrections_what_is_left_is_unresolved():
    second = FIXED.replace(" by mistake", "")
    third = second.replace("of the wrong value ", "")
    ask = Asker(
        reply([("created the first pacemaker", UNSUPPORTED)], FIXED),
        reply([("by mistake", UNSUPPORTED)], second),
        reply([("wrong value", UNSUPPORTED)], third),
    )

    result = fact_check.check_script("pacemaker", SCRIPT, EVIDENCE, ask)

    assert result.script == second
    assert [c.text for c in result.unresolved] == ["wrong value"]
    assert len(ask.prompts) == 3


def test_an_unusable_second_correction_keeps_the_first():
    invented = FIXED.replace("1956", "1958")
    ask = Asker(
        reply([("created the first pacemaker", UNSUPPORTED)], FIXED),
        reply([("by mistake", UNSUPPORTED)], invented),
    )

    result = fact_check.check_script("pacemaker", SCRIPT, EVIDENCE, ask)

    assert result.script == FIXED
    assert [c.text for c in result.unresolved] == ["by mistake"]


def test_the_prompt_asks_for_claims_as_the_sentence_makes_them():
    ask = Asker(reply([], SCRIPT))
    fact_check.check_script("pacemaker", SCRIPT, EVIDENCE, ask)
    assert "the same signal" in ask.prompts[0]


def test_a_correction_that_invents_a_figure_is_not_used():
    invented = FIXED.replace("1956", "1958")
    ask = Asker(reply([("created the first pacemaker", UNSUPPORTED)], invented))

    result = fact_check.check_script("pacemaker", SCRIPT, EVIDENCE, ask)

    assert result.script == SCRIPT
    assert [c.text for c in result.unresolved] == ["created the first pacemaker"]


def test_a_correction_may_shrink_when_claims_come_out():
    shorter = "Wilson Greatbatch grabbed a resistor of the wrong value in 1956."
    ask = Asker(
        reply([("created the first pacemaker by mistake", UNSUPPORTED)], shorter),
        reply([("1956", SUPPORTED)], shorter),
    )

    result = fact_check.check_script("pacemaker", SCRIPT, EVIDENCE, ask)

    assert result.script == shorter and not result.unresolved


def test_a_correction_that_grows_the_video_is_not_used():
    longer = FIXED + " " + " ".join(["More words here."] * 4)
    ask = Asker(reply([("created", UNSUPPORTED)], longer))

    result = fact_check.check_script("pacemaker", SCRIPT, EVIDENCE, ask)

    assert result.script == SCRIPT
    assert result.unresolved


def test_a_correction_that_rewrites_the_video_is_not_used():
    ask = Asker(reply([("created", UNSUPPORTED)], "Pacemakers exist."))

    result = fact_check.check_script("pacemaker", SCRIPT, EVIDENCE, ask)

    assert result.script == SCRIPT
    assert result.unresolved


def test_no_model_leaves_the_script_unchecked_not_failed():
    ask = Asker(RuntimeError("no model"))

    result = fact_check.check_script("pacemaker", SCRIPT, EVIDENCE, ask)

    assert result.script == SCRIPT and not result.checked


def test_an_unreadable_answer_leaves_the_script_unchecked():
    result = fact_check.check_script("pacemaker", SCRIPT, EVIDENCE, Asker("Looks fine to me!"))
    assert result.script == SCRIPT and not result.checked


def test_without_evidence_the_prompt_says_so():
    ask = Asker(reply([], SCRIPT))
    fact_check.check_script("pacemaker", SCRIPT, "", ask)
    assert "common knowledge only" in ask.prompts[0]


def test_summary_lists_what_was_fixed_and_what_was_not():
    ask = Asker(
        reply([("created the first pacemaker", UNSUPPORTED), ("1956", SUPPORTED)], FIXED),
        reply([("by mistake", WRONG)], FIXED),
    )
    summary = fact_check.check_script("pacemaker", SCRIPT, EVIDENCE, ask).summary()
    assert summary == {
        "checked": True,
        "claims": 2,
        "fixed": ["unsupported: created the first pacemaker"],
        "unresolved": ["wrong: by mistake"],
    }


# -- metadata ------------------------------------------------------------------


def test_metadata_drift_is_corrected_and_hashtags_kept():
    description = "He built the first pacemaker.\n\n#Shorts #science"
    ask = Asker(json.dumps({
        "problems": ["description says 'built the first'"],
        "title": "The Pacemaker Mistake",
        "description": "He helped create the implantable pacemaker.",
    }))

    title, new_description, found = fact_check.check_metadata(
        FIXED, "The Pacemaker Mistake", description, ask
    )

    assert found == ["description says 'built the first'"]
    assert new_description == "He helped create the implantable pacemaker.\n\n#Shorts #science"
    # The hashtags were not shown to the model as part of the description.
    assert "#Shorts" not in ask.prompts[0]


def test_clean_metadata_comes_back_unchanged():
    ask = Asker(json.dumps({"problems": [], "title": "x", "description": "y"}))
    assert fact_check.check_metadata(FIXED, "T", "D", ask) == ("T", "D", [])


def test_metadata_check_without_a_model_changes_nothing():
    assert fact_check.check_metadata(FIXED, "T", "D", Asker(RuntimeError("down"))) == ("T", "D", [])


def test_an_overlong_corrected_title_is_not_used():
    ask = Asker(json.dumps({"problems": ["p"], "title": "x" * 150, "description": "D2"}))
    title, description, _ = fact_check.check_metadata(FIXED, "T", "D", ask)
    assert title == "T" and description == "D2"


def test_the_persona_sign_off_is_not_a_claim():
    # On 2026-10-13 a probe of the TITAN persona lost "FACT... CONFIRMED." to
    # the check, judged unsupported as "the doorway effect is settled fact".
    ask = Asker(reply([], SCRIPT))
    fact_check.check_script("pacemaker", SCRIPT, EVIDENCE, ask)
    assert '"FACT...\nCONFIRMED."' in ask.prompts[0]
    assert "not a claim" in ask.prompts[0]


def test_a_bare_surname_is_caught_and_introduced():
    # 2026-10-19: "Then Fry used makeshift sticky notes", Fry never introduced.
    prompt = " ".join(fact_check.CHECK_PROMPT.split())
    assert "surname alone" in prompt and "Art Fry, a 3M colleague" in prompt
