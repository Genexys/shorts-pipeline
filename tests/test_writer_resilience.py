"""The writer through an overload.

The client, the sleep and the clock are all replaced: nothing here
waits for real or reaches a network. The errors are the SDK's own classes, so
the classification is tested against what the SDK actually raises.
"""

import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

import gpt
import writer

PRIMARY = "claude-opus-5"
FALLBACK = "claude-sonnet-5-5"
URL = "https://api.anthropic.com/v1/messages"


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Response:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_Block(text)] if text is not None else []
        self.stop_reason = stop_reason


def _status_error(status: int, message: str = "Overloaded", kind: str = "overloaded_error"):
    """The exception the SDK raises for `status`, built the way the SDK builds it."""
    body = {"type": "error", "error": {"type": kind, "message": message}}
    response = httpx2.Response(status, request=httpx2.Request("POST", URL), json=body)
    classes = {
        400: anthropic.BadRequestError,
        401: anthropic.AuthenticationError,
        403: anthropic.PermissionDeniedError,
        404: anthropic.NotFoundError,
        429: anthropic.RateLimitError,
        529: anthropic.OverloadedError,
    }
    cls = classes.get(status) or (
        anthropic.InternalServerError if status >= 500 else anthropic.APIStatusError
    )
    return cls(f"Error code: {status} - {body}", response=response, body=body)


def _connection_error():
    return anthropic.APIConnectionError(request=httpx2.Request("POST", URL))


def _timeout_error():
    return anthropic.APITimeoutError(request=httpx2.Request("POST", URL))


class _Calls:
    """Hands out outcomes per model, in order, and records every request."""

    def __init__(self, outcomes):
        self.outcomes = {model: list(items) for model, items in outcomes.items()}
        self.requests = []

    def factory(self, max_retries):
        calls = self

        class _Messages:
            def create(self, **kwargs):
                calls.requests.append(
                    (kwargs["model"], max_retries, kwargs["messages"][0]["content"])
                )
                outcome = calls.outcomes[kwargs["model"]].pop(0)
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome

        class _Client:
            messages = _Messages()

        return _Client()

    def models(self):
        return [model for model, _, _ in self.requests]


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def env(monkeypatch):
    """A configured writer with fresh process state and nothing real behind it."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    monkeypatch.delenv("SCRIPT_MODEL", raising=False)
    monkeypatch.delenv("SCRIPT_FALLBACK_MODEL", raising=False)
    monkeypatch.setattr(writer, "_down_until", {})

    clock = _Clock()
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock.now += seconds

    monkeypatch.setattr(writer, "_sleep", sleep)
    monkeypatch.setattr(writer, "_clock", clock)

    def calls(primary=(), fallback=()) -> _Calls:
        recorder = _Calls({PRIMARY: primary, FALLBACK: fallback})
        monkeypatch.setattr(writer, "_client", recorder.factory)
        return recorder

    return SimpleNamespace(clock=clock, sleeps=sleeps, calls=calls)


# -- the ladder --------------------------------------------------------------


def test_an_overload_is_waited_out_on_the_primary(env):
    # On 2026-09-28 claude-opus-5 answered 529 repeatedly and the SDK's two
    # sub-second retries gave up at once; llama wrote five of eight sections.
    calls = env.calls(primary=[_status_error(529), _status_error(529), _Response("Opus.")])

    assert writer.write("prompt") == writer.Written("Opus.", PRIMARY)
    assert env.sleeps == [30, 60]
    assert calls.models() == [PRIMARY] * 3
    # The ladder is the retry policy; the SDK's own retries would triple it.
    assert {retries for _, retries, _ in calls.requests} == {0}


@pytest.mark.parametrize(
    "error",
    [
        _status_error(429, "Rate limited", "rate_limit_error"),
        _status_error(500, "Internal error", "api_error"),
        _status_error(502, "Bad gateway", "api_error"),
        _status_error(503, "Unavailable", "api_error"),
        _status_error(504, "Timeout", "api_error"),
        _status_error(529),
        _connection_error(),
        _timeout_error(),
    ],
    ids=["429", "500", "502", "503", "504", "529", "connection", "timeout"],
)
def test_every_kind_of_outage_climbs_the_ladder(env, error):
    env.calls(primary=[error, _Response("Opus.")])

    assert writer.write("prompt") == writer.Written("Opus.", PRIMARY)
    assert env.sleeps == [30]


def test_an_exhausted_ladder_hands_over_to_the_fallback_model(env):
    calls = env.calls(primary=[_status_error(529)] * 4, fallback=[_Response("Sonnet.")])

    assert writer.write("prompt") == writer.Written("Sonnet.", FALLBACK)
    assert env.sleeps == list(writer.OUTAGE_BACKOFF_SECONDS)
    assert calls.models() == [PRIMARY] * 4 + [FALLBACK]
    # The fallback keeps the SDK's quick retries for a blip, and no ladder.
    assert calls.requests[-1][1] == writer.FALLBACK_SDK_RETRIES


def test_later_calls_skip_the_ladder_while_the_outage_is_remembered(env):
    # A long video makes eight section calls and the search terms. Three and a
    # half minutes each would hold the queue for half an hour.
    calls = env.calls(
        primary=[_status_error(529)] * 4,
        fallback=[_Response("One."), _Response("Two."), _Response("Three.")],
    )

    writer.write("section one")
    env.sleeps.clear()
    env.clock.now += writer.OUTAGE_MEMORY_SECONDS - 60

    assert writer.write("section two") == writer.Written("Two.", FALLBACK)
    assert writer.write("section three") == writer.Written("Three.", FALLBACK)
    assert env.sleeps == []
    assert calls.models().count(PRIMARY) == 4


def test_the_primary_is_tried_again_once_the_memory_lapses(env):
    calls = env.calls(
        primary=[_status_error(529)] * 4 + [_Response("Opus is back.")],
        fallback=[_Response("Sonnet.")],
    )

    writer.write("first")
    env.clock.now += writer.OUTAGE_MEMORY_SECONDS + 1

    assert writer.write("later") == writer.Written("Opus is back.", PRIMARY)
    assert calls.models()[-1] == PRIMARY


def test_a_failed_fallback_leaves_the_call_to_the_local_model(env):
    env.calls(primary=[_status_error(529)] * 4, fallback=[_status_error(529)])

    assert writer.write("prompt") is None


def test_an_empty_fallback_setting_goes_straight_to_the_local_model(env, monkeypatch):
    monkeypatch.setenv("SCRIPT_FALLBACK_MODEL", "")
    calls = env.calls(primary=[_status_error(529)] * 4)

    assert writer.fallback_model_name() == ""
    assert writer.write("prompt") is None
    # And during the outage, without waiting through the ladder again.
    env.sleeps.clear()
    assert writer.write("next") is None
    assert env.sleeps == []
    assert calls.models() == [PRIMARY] * 4


def test_the_fallback_defaults_to_sonnet_and_can_be_overridden(env, monkeypatch):
    assert writer.fallback_model_name() == "claude-sonnet-5-5"
    monkeypatch.setenv("SCRIPT_FALLBACK_MODEL", "claude-haiku-4-5")
    assert writer.fallback_model_name() == "claude-haiku-4-5"


def test_a_fallback_naming_the_primary_is_no_fallback(env, monkeypatch):
    monkeypatch.setenv("SCRIPT_FALLBACK_MODEL", PRIMARY)
    assert writer.fallback_model_name() == ""


def test_a_refusal_is_not_an_outage(env):
    # Asked again with the brief stated, as before: no wait, no fallback.
    calls = env.calls(
        primary=[_Response(None, stop_reason="refusal"), _Response("A script.")]
    )

    assert writer.write("Subject: painted cows") == writer.Written("A script.", PRIMARY)
    assert env.sleeps == []
    assert calls.models() == [PRIMARY, PRIMARY]
    assert writer.RETRY_PREAMBLE in calls.requests[1][2]


def test_the_real_sdk_retries_nothing_behind_the_ladder(env, monkeypatch):
    # The SDK client itself, over a mock transport: what reaches the wire is
    # exactly what the ladder sends, and the SDK's own errors are classified.
    sent = []
    replies = {
        PRIMARY: [(529, {"type": "error", "error": {"type": "overloaded_error",
                                                    "message": "Overloaded"}})] * 4,
        FALLBACK: [(200, {
            "id": "msg_1", "type": "message", "role": "assistant", "model": FALLBACK,
            "content": [{"type": "text", "text": "Sonnet."}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })],
    }

    def handler(request):
        model = json.loads(request.content)["model"]
        sent.append(model)
        status, body = replies[model].pop(0)
        return httpx2.Response(status, json=body)

    def client(max_retries):
        return anthropic.Anthropic(
            api_key=writer.api_key(),
            max_retries=max_retries,
            http_client=anthropic.DefaultHttpxClient(
                transport=httpx2.MockTransport(handler)
            ),
        )

    monkeypatch.setattr(writer, "_client", client)

    assert writer.write("prompt") == writer.Written("Sonnet.", FALLBACK)
    assert sent == [PRIMARY] * 4 + [FALLBACK]


def test_a_request_error_is_neither_waited_out_nor_handed_over(env):
    calls = env.calls(primary=[_status_error(404, "model: nope", "not_found_error")])

    assert writer.write("prompt") is None
    assert env.sleeps == []
    assert calls.models() == [PRIMARY]


# -- who wrote it ------------------------------------------------------------


def test_models_are_ranked_local_fallback_primary(env):
    assert writer.rank("llama3.1:8b") == writer.LOCAL
    assert writer.rank(FALLBACK) == writer.FALLBACK
    assert writer.rank(PRIMARY) == writer.PRIMARY
    assert writer.LOCAL < writer.FALLBACK < writer.PRIMARY


def test_the_weakest_writer_wins(env):
    assert writer.weakest([PRIMARY, FALLBACK, PRIMARY]) == FALLBACK
    assert writer.weakest([PRIMARY, FALLBACK, "llama3.1:8b"]) == "llama3.1:8b"
    assert writer.weakest([PRIMARY]) == PRIMARY
    assert writer.weakest([]) is None


def test_write_creative_reports_the_fallback_model_that_wrote(env, monkeypatch):
    monkeypatch.setattr(writer, "write", lambda prompt: writer.Written("By Sonnet.", FALLBACK))
    seen = []

    assert gpt.write_creative("p", "llama3.1:8b", report_model=seen.append) == "By Sonnet."
    assert seen == [FALLBACK]


def test_a_long_script_reports_its_weakest_section(env, monkeypatch):
    # Opus, then Sonnet, then llama: reporting the first that was not Opus
    # would file a part-llama script as Sonnet's.
    drafts = iter(
        [
            writer.Written("Opus prose.", PRIMARY),
            writer.Written("Sonnet prose.", FALLBACK),
            None,
        ]
    )
    monkeypatch.setattr(writer, "write", lambda prompt: next(drafts))
    monkeypatch.setattr(gpt, "generate_response", lambda p, m: "Local prose.")
    monkeypatch.setattr(gpt, "generate_outline", lambda *a, **k: ["One", "Two", "Three"])
    seen = []

    gpt.generate_long_script(
        "s", 150, "llama3.1:8b", "en_us_001", "", section_count=3,
        report_model=seen.append,
    )

    assert seen == ["llama3.1:8b"]


def test_a_long_script_part_sonnet_is_reported_as_sonnet(env, monkeypatch):
    drafts = iter([writer.Written("Opus prose.", PRIMARY), writer.Written("Sonnet.", FALLBACK)])
    monkeypatch.setattr(writer, "write", lambda prompt: next(drafts))
    monkeypatch.setattr(gpt, "generate_outline", lambda *a, **k: ["One", "Two"])
    seen = []

    gpt.generate_long_script(
        "s", 100, "llama3.1:8b", "en_us_001", "", section_count=2,
        report_model=seen.append,
    )

    assert seen == [FALLBACK]
