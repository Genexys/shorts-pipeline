"""Who writes the title and description, from how much of the script."""

import gpt

LONG_SCRIPT = " ".join(f"word{i}" for i in range(800)) + "."
REPLY = '{"title": "The first CT scan", "description": "D", "tags": ["a tag"]}'


def test_claude_reads_the_whole_long_script(monkeypatch):
    # On 2026-10-01 a video about the first CT scan went up as "The Dawn of
    # Radiography": the metadata model saw sections one and two, which were
    # about plain X-rays, and the scan itself was in section seven.
    seen = {}

    def fake(prompt, model):
        seen["prompt"] = prompt
        return REPLY

    monkeypatch.setattr(gpt, "generate_response", fake)

    title, _, tags = gpt.generate_metadata("subject", LONG_SCRIPT, "model")

    assert title == "The first CT scan" and tags == ["a tag"]
    assert "word799" in seen["prompt"]
    assert "not only its opening" in seen["prompt"]


def test_no_model_leaves_the_subject_rather_than_a_guess(monkeypatch):
    # The local model used to write it from an excerpt; with it gone, a missing
    # model costs the title its flair, not its truth.
    def unavailable(prompt, model):
        raise gpt.ModelUnavailable("no model")

    monkeypatch.setattr(gpt, "generate_response", unavailable)

    title, description, _ = gpt.generate_metadata("black holes", LONG_SCRIPT, "model")

    assert title == "Black holes"
    assert description.startswith("Black holes")


def test_both_prompts_forbid_adding_or_strengthening_claims():
    # "creating the first pacemaker by mistake" under a source that said
    # "helped create"; "Edison's Hydroelectric Rival" for an Edison licensee.
    for whole in (True, False):
        prompt = gpt.metadata_prompt("s", "A script.", "label", whole)
        assert "must be one the script" in prompt
        assert '"Helped create" stays "helped create"' in prompt
        assert "rival" in prompt


def test_a_reply_that_is_not_json_falls_back_to_the_subject(monkeypatch):
    monkeypatch.setattr(gpt, "generate_response", lambda p, m: "Sorry, no.")

    title, description, _ = gpt.generate_metadata("black holes", "A script.", "model")

    assert title == "Black holes"
    assert description.startswith("Black holes")
