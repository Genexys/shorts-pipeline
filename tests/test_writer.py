import pytest

import gpt
import writer


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Response:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_Block(text)] if text is not None else []
        self.stop_reason = stop_reason


def _patch_client(monkeypatch, response=None, error=None, recorder=None):
    class _Messages:
        def create(self, **kwargs):
            if recorder is not None:
                recorder.append(kwargs)
            if error:
                raise error
            return response

    class _Client:
        def __init__(self, **kwargs):
            if recorder is not None:
                recorder.append(kwargs)
            self.messages = _Messages()

    import types

    fake = types.SimpleNamespace(Anthropic=_Client)
    monkeypatch.setitem(__import__("sys").modules, "anthropic", fake)


# -- configuration -----------------------------------------------------------


def test_not_configured_without_a_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert writer.is_configured() is False
    assert writer.write("anything") is None


def test_the_model_defaults_to_opus(monkeypatch):
    monkeypatch.delenv("SCRIPT_MODEL", raising=False)
    assert writer.model_name() == "claude-opus-5"


def test_the_model_can_be_overridden(monkeypatch):
    monkeypatch.setenv("SCRIPT_MODEL", "claude-sonnet-5")
    assert writer.model_name() == "claude-sonnet-5"


def test_scrub_removes_the_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    assert "sk-secret" not in writer.scrub("failed with key sk-secret")


# -- writing -----------------------------------------------------------------


def test_write_returns_the_text(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    _patch_client(monkeypatch, response=_Response("A script."))
    assert writer.write("prompt") == "A script."


def test_write_survives_an_api_failure(monkeypatch):
    # A rate limit, a network error or an outage must not fail a video.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    _patch_client(monkeypatch, error=RuntimeError("429 rate limited"))
    assert writer.write("prompt") is None


def test_write_treats_a_refusal_as_no_answer(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    _patch_client(monkeypatch, response=_Response("", stop_reason="refusal"))
    assert writer.write("prompt") is None


def test_write_treats_empty_content_as_no_answer(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    _patch_client(monkeypatch, response=_Response(None))
    assert writer.write("prompt") is None


# -- the fallback ------------------------------------------------------------


def test_creative_calls_fall_back_to_ollama(monkeypatch):
    # The whole point of optional: no key, no behaviour change.
    monkeypatch.setattr(writer, "write", lambda prompt: None)
    monkeypatch.setattr(gpt, "generate_response", lambda p, m: "from ollama")
    assert gpt.write_creative("prompt", "llama3.1:8b") == "from ollama"


def test_creative_calls_prefer_the_stronger_model(monkeypatch):
    monkeypatch.setattr(writer, "write", lambda prompt: "from claude")
    monkeypatch.setattr(
        gpt, "generate_response",
        lambda p, m: pytest.fail("should not have asked Ollama"),
    )
    assert gpt.write_creative("prompt", "llama3.1:8b") == "from claude"


def test_search_terms_go_through_the_creative_path(monkeypatch):
    # Picking filmable shots is judgement, not extraction: the local model
    # returned "sulfur compounds" and "lacrimal glands" where the stronger one
    # returned "onion slices closeup" and "knife cutting board".
    monkeypatch.setattr(
        writer, "write", lambda prompt: '["chopping onion", "knife cutting board"]'
    )
    monkeypatch.setattr(
        gpt, "generate_response",
        lambda p, m: pytest.fail("should not have asked Ollama"),
    )

    assert gpt.get_search_terms("onions", 2, "script", "llama3.1:8b") == [
        "chopping onion",
        "knife cutting board",
    ]


def test_search_terms_still_fall_back_to_ollama(monkeypatch):
    monkeypatch.setattr(writer, "write", lambda prompt: None)
    monkeypatch.setattr(gpt, "generate_response", lambda p, m: '["onion cutting"]')

    assert gpt.get_search_terms("onions", 1, "script", "llama3.1:8b") == ["onion cutting"]


def test_write_creative_reports_the_model_that_wrote(monkeypatch):
    import gpt

    seen = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.setattr(gpt.writer, "write", lambda prompt: "Written by Opus.")

    text = gpt.write_creative("p", "llama3.1:8b", report_model=seen.append)

    assert text == "Written by Opus."
    assert seen == ["claude-opus-5"]


def test_write_creative_reports_the_fallback_model(monkeypatch):
    import gpt

    seen = []
    monkeypatch.setattr(gpt.writer, "write", lambda prompt: None)
    monkeypatch.setattr(gpt, "generate_response", lambda p, m: "Written by Ollama.")

    text = gpt.write_creative("p", "llama3.1:8b", report_model=seen.append)

    assert text == "Written by Ollama."
    # The point of the field: a fallback must not be filed under the model that
    # was asked for and did not write it.
    assert seen == ["llama3.1:8b"]


def _patch_sequence(monkeypatch, responses):
    """Like _patch_client, but hands out `responses` in order.

    Returns the list of prompts actually sent, so a test can check what the
    retry changed.
    """
    sent = []

    class _Messages:
        def create(self, **kwargs):
            sent.append(kwargs["messages"][0]["content"])
            return responses.pop(0)

    class _Client:
        def __init__(self, **kwargs):
            self.messages = _Messages()

    import types

    fake = types.SimpleNamespace(Anthropic=_Client)
    monkeypatch.setitem(__import__("sys").modules, "anthropic", fake)
    return sent


def test_write_retries_once_when_the_model_declines(monkeypatch):
    # A curio about cattle painted with zebra stripes to deter biting flies was
    # refused outright, and the 8B fallback then wrote four sentences of which
    # two repeated the other two.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    sent = _patch_sequence(
        monkeypatch,
        [_Response(None, stop_reason="refusal"), _Response("A script.")],
    )

    assert writer.write("Subject: painted cows") == "A script."
    assert len(sent) == 2
    assert sent[0] == "Subject: painted cows"
    # The retry says what the request is for, and still carries the original.
    assert "general-audience science channel" in sent[1]
    assert "Subject: painted cows" in sent[1]


def test_write_gives_up_after_a_second_refusal(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    sent = _patch_sequence(
        monkeypatch,
        [_Response(None, stop_reason="refusal")] * 2,
    )

    assert writer.write("Subject: painted cows") is None
    assert len(sent) == 2


def test_write_does_not_retry_a_network_failure(monkeypatch):
    # Rewording will not fix a timeout, and the caller has a local model
    # waiting. Only a refusal is worth asking differently.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    calls = []
    _patch_client(monkeypatch, error=RuntimeError("connection reset"), recorder=calls)

    assert writer.write("Subject: anything") is None
    # One client, one create; a second attempt would add two more entries.
    assert len(calls) == 2


def test_write_does_not_retry_a_first_attempt_that_worked(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    sent = _patch_sequence(monkeypatch, [_Response("A script.")])

    assert writer.write("Subject: anything") == "A script."
    assert len(sent) == 1
