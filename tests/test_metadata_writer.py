"""Who writes the title and description, from how much of the script."""

import gpt

LONG_SCRIPT = " ".join(f"word{i}" for i in range(800)) + "."
REPLY = '{"title": "The first CT scan", "description": "D", "tags": ["a tag"]}'


def test_the_writer_reads_the_whole_long_script(monkeypatch):
    # On 2026-10-01 a video about the first CT scan went up as "The Dawn of
    # Radiography": the metadata model saw sections one and two, which were
    # about plain X-rays, and the scan itself was in section seven.
    seen = {}

    def fake_write(prompt):
        seen["prompt"] = prompt
        return REPLY

    def no_local(prompt, model):
        raise AssertionError("the local model should not be asked")

    monkeypatch.setattr(gpt.writer, "write", fake_write)
    monkeypatch.setattr(gpt, "generate_response", no_local)

    title, _, tags = gpt.generate_metadata("subject", LONG_SCRIPT, "model")

    assert title == "The first CT scan" and tags == ["a tag"]
    assert "word799" in seen["prompt"]
    assert "not only its opening" in seen["prompt"]


def test_the_local_model_still_gets_the_excerpt_when_the_writer_cannot(monkeypatch):
    seen = {}

    def fake_local(prompt, model):
        seen["prompt"] = prompt
        return REPLY

    monkeypatch.setattr(gpt.writer, "write", lambda prompt: None)
    monkeypatch.setattr(gpt, "generate_response", fake_local)

    gpt.generate_metadata("subject", LONG_SCRIPT, "model")

    assert "word199" in seen["prompt"]
    assert "word400" not in seen["prompt"]
    # Told to describe the whole video, a model shown only its opening would
    # be asked to guess at the rest.
    assert "not only its opening" not in seen["prompt"]


def test_both_prompts_forbid_adding_or_strengthening_claims():
    # "creating the first pacemaker by mistake" under a source that said
    # "helped create"; "Edison's Hydroelectric Rival" for an Edison licensee.
    for whole in (True, False):
        prompt = gpt.metadata_prompt("s", "A script.", "label", whole)
        assert "must be one the script" in prompt
        assert '"Helped create" stays "helped create"' in prompt
        assert "rival" in prompt


def test_a_writer_reply_that_is_not_json_falls_back_to_the_subject(monkeypatch):
    monkeypatch.setattr(gpt.writer, "write", lambda prompt: "Sorry, no.")
    monkeypatch.setattr(
        gpt, "generate_response", lambda p, m: (_ for _ in ()).throw(AssertionError)
    )

    title, description, _ = gpt.generate_metadata("black holes", "A script.", "model")

    assert title == "Black holes"
    assert description.startswith("Black holes")
