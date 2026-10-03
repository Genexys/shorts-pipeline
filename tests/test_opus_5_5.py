"""The writer on Claude Opus 5.5: its effort, the refusal fallback, who answered."""

import json

import anthropic
import httpx2
import pytest

import writer


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("SCRIPT_MODEL", raising=False)
    monkeypatch.delenv("SCRIPT_FALLBACK_MODEL", raising=False)
    monkeypatch.setattr(writer, "_down_until", {})
    monkeypatch.setattr(writer, "_sleep", lambda seconds: None)


def _real_client(monkeypatch, reply_model):
    sent = []

    def handler(request):
        sent.append((dict(request.headers), json.loads(request.content)))
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": reply_model,
            "content": [{"type": "text", "text": "A script."}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    def client(max_retries):
        return anthropic.Anthropic(
            api_key=writer.api_key(),
            max_retries=max_retries,
            http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
        )

    monkeypatch.setattr(writer, "_client", client)
    return sent


def test_the_default_model_is_opus_5_5(env):
    assert writer.model_name() == "claude-opus-5-5"


def test_every_request_asks_for_high_effort_and_the_refusal_fallback(env, monkeypatch):
    # Claude Opus 5.5 thinks at "medium" unless told otherwise, a level below
    # what Claude Opus 5 did with no setting at all.
    sent = _real_client(monkeypatch, "claude-opus-5-5")

    assert writer.write_with_model("prompt") == ("A script.", "claude-opus-5-5")

    headers, body = sent[0]
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"] == {"effort": "high"}
    assert body["fallbacks"] == "default"
    assert writer.SERVER_FALLBACK_BETA in headers.get("anthropic-beta", "")


def test_a_reply_from_the_refusal_fallback_is_filed_under_that_model(env, monkeypatch):
    # The API answered with another model after Opus 5.5 declined: the script
    # is not Opus 5.5's, and the record has to say whose it is.
    _real_client(monkeypatch, "claude-opus-5")

    written = writer.write_with_model("Subject: a lizard that squirts blood")

    assert written == ("A script.", "claude-opus-5")
    assert writer.rank(written.model) == writer.FALLBACK


def test_any_claude_model_outranks_the_local_one(env):
    assert writer.rank("claude-opus-5-5") == writer.PRIMARY
    assert writer.rank("claude-sonnet-5-5") == writer.FALLBACK
    assert writer.rank("claude-opus-4-8") == writer.FALLBACK
    assert writer.rank("llama3.1:8b") == writer.LOCAL
    assert writer.weakest(["claude-opus-5-5", "claude-opus-4-8"]) == "claude-opus-4-8"
