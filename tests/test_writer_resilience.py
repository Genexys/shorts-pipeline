"""The writer through an overload, an empty account, and a script it only half wrote.

The client, the sleep, the clock and Telegram are all replaced: nothing here
waits for real or reaches a network. The errors are the SDK's own classes, so
the classification is tested against what the SDK actually raises.
"""

import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

import gpt
import pipeline
import worker
import writer
from autopilot import Autopilot
from autopilot_config import AutopilotConfig
from formats import LONG, SHORT
from repository import (
    add_artifact,
    add_topic,
    create_job,
    list_artifacts,
    mark_completed,
    queue_topic_job,
)

PRIMARY = "claude-opus-5"
FALLBACK = "claude-sonnet-5-5"
URL = "https://api.anthropic.com/v1/messages"
CREDIT_MESSAGE = (
    "Your credit balance is too low to access the Anthropic API. "
    "Please go to Plans & Billing to upgrade or purchase credits."
)


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
    monkeypatch.setattr(writer, "_last_account_alert", None)

    clock = _Clock()
    sleeps = []
    alerts = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock.now += seconds

    monkeypatch.setattr(writer, "_sleep", sleep)
    monkeypatch.setattr(writer, "_clock", clock)
    monkeypatch.setattr(writer, "send_telegram", lambda text: alerts.append(text) or True)

    def calls(primary=(), fallback=()) -> _Calls:
        recorder = _Calls({PRIMARY: primary, FALLBACK: fallback})
        monkeypatch.setattr(writer, "_client", recorder.factory)
        return recorder

    return SimpleNamespace(clock=clock, sleeps=sleeps, alerts=alerts, calls=calls)


# -- the ladder --------------------------------------------------------------


def test_an_overload_is_waited_out_on_the_primary(env):
    # On 2026-09-28 claude-opus-5 answered 529 repeatedly and the SDK's two
    # sub-second retries gave up at once; llama wrote five of eight sections.
    calls = env.calls(primary=[_status_error(529), _status_error(529), _Response("Opus.")])

    assert writer.write_with_model("prompt") == writer.Written("Opus.", PRIMARY)
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

    assert writer.write_with_model("prompt") == writer.Written("Opus.", PRIMARY)
    assert env.sleeps == [30]


def test_an_exhausted_ladder_hands_over_to_the_fallback_model(env):
    calls = env.calls(primary=[_status_error(529)] * 4, fallback=[_Response("Sonnet.")])

    assert writer.write_with_model("prompt") == writer.Written("Sonnet.", FALLBACK)
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

    writer.write_with_model("section one")
    env.sleeps.clear()
    env.clock.now += writer.OUTAGE_MEMORY_SECONDS - 60

    assert writer.write_with_model("section two") == writer.Written("Two.", FALLBACK)
    assert writer.write_with_model("section three") == writer.Written("Three.", FALLBACK)
    assert env.sleeps == []
    assert calls.models().count(PRIMARY) == 4


def test_the_primary_is_tried_again_once_the_memory_lapses(env):
    calls = env.calls(
        primary=[_status_error(529)] * 4 + [_Response("Opus is back.")],
        fallback=[_Response("Sonnet.")],
    )

    writer.write_with_model("first")
    env.clock.now += writer.OUTAGE_MEMORY_SECONDS + 1

    assert writer.write_with_model("later") == writer.Written("Opus is back.", PRIMARY)
    assert calls.models()[-1] == PRIMARY


def test_a_failed_fallback_leaves_the_call_to_the_local_model(env):
    env.calls(primary=[_status_error(529)] * 4, fallback=[_status_error(529)])

    assert writer.write_with_model("prompt") is None


def test_an_empty_fallback_setting_goes_straight_to_the_local_model(env, monkeypatch):
    monkeypatch.setenv("SCRIPT_FALLBACK_MODEL", "")
    calls = env.calls(primary=[_status_error(529)] * 4)

    assert writer.fallback_model_name() == ""
    assert writer.write_with_model("prompt") is None
    # And during the outage, without waiting through the ladder again.
    env.sleeps.clear()
    assert writer.write_with_model("next") is None
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

    assert writer.write_with_model("Subject: painted cows") == writer.Written("A script.", PRIMARY)
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

    assert writer.write_with_model("prompt") == writer.Written("Sonnet.", FALLBACK)
    assert sent == [PRIMARY] * 4 + [FALLBACK]


def test_a_request_error_is_neither_waited_out_nor_handed_over(env):
    calls = env.calls(primary=[_status_error(404, "model: nope", "not_found_error")])

    assert writer.write_with_model("prompt") is None
    assert env.sleeps == []
    assert calls.models() == [PRIMARY]


# -- the account -------------------------------------------------------------


def test_an_empty_balance_alerts_the_owner_and_skips_the_fallback(env):
    # 2026-09-27, 15:00: every call fell back to llama without a word, and
    # nobody knew until a video was reviewed by hand.
    calls = env.calls(primary=[_status_error(400, CREDIT_MESSAGE, "invalid_request_error")])

    assert writer.write_with_model("prompt") is None
    # The fallback model bills the same account, and waiting will not refill it.
    assert calls.models() == [PRIMARY]
    assert env.sleeps == []
    assert len(env.alerts) == 1
    assert "credit balance" in env.alerts[0]
    assert "local model" in env.alerts[0]
    assert "Top up" in env.alerts[0]


def test_the_alert_is_sent_once_every_few_hours(env):
    credit = _status_error(400, CREDIT_MESSAGE, "invalid_request_error")
    env.calls(primary=[credit] * 3)

    writer.write_with_model("one")
    env.clock.now += 60
    writer.write_with_model("two")
    assert len(env.alerts) == 1

    env.clock.now += writer.ACCOUNT_ALERT_INTERVAL_SECONDS
    writer.write_with_model("three")
    assert len(env.alerts) == 2


@pytest.mark.parametrize(
    "error",
    [
        _status_error(401, "invalid x-api-key", "authentication_error"),
        _status_error(403, "not allowed", "permission_error"),
        _status_error(402, "billing problem", "billing_error"),
    ],
    ids=["401", "403", "402"],
)
def test_key_and_billing_errors_are_account_problems(env, error):
    calls = env.calls(primary=[error])

    assert writer.write_with_model("prompt") is None
    assert calls.models() == [PRIMARY]
    assert len(env.alerts) == 1


def test_a_rejected_key_is_reported_as_the_key(env):
    env.calls(primary=[_status_error(401, "invalid x-api-key", "authentication_error")])

    writer.write_with_model("prompt")

    assert "API key" in env.alerts[0]
    assert "local model" in env.alerts[0]


def test_an_ordinary_bad_request_is_not_an_account_problem(env):
    env.calls(primary=[_status_error(400, "max_tokens: too large", "invalid_request_error")])

    assert writer.write_with_model("prompt") is None
    assert env.alerts == []


def test_the_alert_never_carries_the_key(env):
    env.calls(primary=[_status_error(401, "bad key sk-test-key", "authentication_error")])

    writer.write_with_model("prompt")

    assert "sk-test-key" not in env.alerts[0]


def test_a_failing_alert_does_not_fail_the_call(env, monkeypatch):
    def broken(text):
        raise RuntimeError("telegram is down")

    monkeypatch.setattr(writer, "send_telegram", broken)
    env.calls(primary=[_status_error(400, CREDIT_MESSAGE, "invalid_request_error")])

    assert writer.write_with_model("prompt") is None


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
    monkeypatch.setattr(writer, "write_with_model", lambda prompt: writer.Written("By Sonnet.", FALLBACK))
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
    monkeypatch.setattr(writer, "write_with_model", lambda prompt: next(drafts))
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
    monkeypatch.setattr(writer, "write_with_model", lambda prompt: next(drafts))
    monkeypatch.setattr(gpt, "generate_outline", lambda *a, **k: ["One", "Two"])
    seen = []

    gpt.generate_long_script(
        "s", 100, "llama3.1:8b", "en_us_001", "", section_count=2,
        report_model=seen.append,
    )

    assert seen == [FALLBACK]


# -- held for review ---------------------------------------------------------


def test_a_long_video_the_local_model_helped_write_is_held_for_review():
    # https://youtu.be/uynIVYwuEvw went out public with a fabricated claim
    # about a real doctor after llama wrote five of its eight sections.
    assert pipeline.held_for_review(LONG, script_local=True) is True
    assert pipeline.held_for_review(LONG, script_local=False) is False


def test_a_short_keeps_its_configured_privacy():
    assert pipeline.held_for_review(SHORT, script_local=True) is False


def test_the_worker_records_a_local_script_and_the_privacy_used(
    monkeypatch, session_factory
):
    with session_factory() as session:
        job = create_job(session, payload={"videoSubject": "Hand washing"})
    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    monkeypatch.setattr(worker, "clean_dir", lambda _: None)
    monkeypatch.setattr(
        worker, "run_generation_pipeline",
        lambda data, is_cancelled, on_log: pipeline.PipelineResult(
            video_path="output.mp4", archived_path=f"output/{data['jobId']}.mp4",
            title="Hand washing", youtube_video_id="vid1", upload_error=None,
            privacy_status="private", format_name="long",
            subtitles_path="subtitles/x.srt", thumbnail_path=None,
            narration_provider="elevenlabs", narration_fell_back=False,
            script="A script.", ai_model="llama3.1:8b", script_model="llama3.1:8b",
            script_fell_back=True, script_local=True, sources=[],
        ),
    )

    assert worker.process_next_job() is True

    with session_factory() as session:
        artifacts = {a.artifact_type: a for a in list_artifacts(session, job.id)}
    assert artifacts["video"].metadata_json["scriptLocal"] is True
    assert artifacts["youtube_video"].metadata_json["privacyStatus"] == "private"


def _pilot(session_factory, sent):
    config = AutopilotConfig.from_env(
        {
            "AUTOPILOT_NICHE": "ocean facts",
            "AUTOPILOT_CURIO_SHARE": "0",
            "AUTOPILOT_ANNIVERSARY_SHARE": "0",
        }
    )
    return Autopilot(
        config=config,
        session_factory=session_factory,
        notify=lambda text: sent.append(text) or True,
        generate=lambda prompt, model: "{}",
        generate_post=lambda *args, **kwargs: None,
        send_photo=lambda *args, **kwargs: True,
    )


def _finished(session_factory, video_metadata, privacy="public"):
    with session_factory() as session:
        topic = add_topic(session, "Hand washing", "niche", "ollama")
        job = queue_topic_job(session, topic, {"videoSubject": "Hand washing"})
        mark_completed(session, job.id, "output.mp4")
        add_artifact(
            session, job.id, "video", f"output/{job.id}.mp4",
            {"title": "Hand washing", "uploadError": None, "narration": "elevenlabs",
             "narrationFellBack": False, **video_metadata},
        )
        if privacy:
            add_artifact(
                session, job.id, "youtube_video", "https://youtu.be/abc",
                {"videoId": "abc", "privacyStatus": privacy},
            )


def test_the_report_says_plainly_that_a_held_video_needs_publishing(session_factory):
    sent = []
    _finished(
        session_factory,
        {"format": "long", "scriptModel": "llama3.1:8b",
         "scriptFellBack": True, "scriptLocal": True},
        privacy="private",
    )

    _pilot(session_factory, sent).finish_completed_topics()

    message = sent[0]
    assert "PRIVATE for review" in message
    assert "publish it by hand" in message
    assert "llama3.1:8b" in message


def test_a_short_by_the_local_model_keeps_the_warning_line(session_factory):
    sent = []
    _finished(
        session_factory,
        {"format": "short", "scriptModel": "llama3.1:8b",
         "scriptFellBack": True, "scriptLocal": True},
    )

    _pilot(session_factory, sent).finish_completed_topics()

    message = sent[0]
    assert "⚠️ written by llama3.1:8b" in message
    assert "PRIVATE" not in message


def test_a_long_video_by_the_fallback_claude_is_only_a_warning(session_factory):
    sent = []
    _finished(
        session_factory,
        {"format": "long", "scriptModel": FALLBACK,
         "scriptFellBack": True, "scriptLocal": False},
    )

    _pilot(session_factory, sent).finish_completed_topics()

    message = sent[0]
    assert f"⚠️ written by {FALLBACK}" in message
    assert "PRIVATE" not in message


def test_a_held_video_that_never_uploaded_keeps_the_warning_line(session_factory):
    # Nothing went up private, so there is nothing to publish by hand.
    sent = []
    _finished(
        session_factory,
        {"format": "long", "scriptModel": "llama3.1:8b",
         "scriptFellBack": True, "scriptLocal": True, "uploadError": "quota"},
        privacy=None,
    )

    _pilot(session_factory, sent).finish_completed_topics()

    message = sent[0]
    assert "⚠️ written by llama3.1:8b" in message
    assert "PRIVATE" not in message
