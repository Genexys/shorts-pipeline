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
