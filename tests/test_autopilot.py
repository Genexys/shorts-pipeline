import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import autopilot
from autopilot import Autopilot, build_notifier, build_payload, build_topic_prompt
from autopilot_config import AutopilotConfig
from repository import (
    add_artifact,
    add_topic,
    claim_next_queued_job,
    create_job,
    get_job,
    list_topics,
    mark_cancelled,
    mark_completed,
    mark_failed,
    queue_topic_job,
    topics_awaiting_result,
)

UTC = timezone.utc
NOON = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


def _config(**overrides) -> AutopilotConfig:
    env = {"AUTOPILOT_NICHE": "ocean facts", "AUTOPILOT_VIDEOS_PER_DAY": "2"}
    env.update({k: str(v) for k, v in overrides.items()})
    return AutopilotConfig.from_env(env)


@pytest.fixture
def notifications():
    sent: list[str] = []
    return sent


@pytest.fixture
def pilot(session_factory, tmp_path, notifications):
    def generate(prompt: str, ai_model: str) -> str:
        return json.dumps({"subject": "Why the ocean is salty"})

    return Autopilot(
        config=_config(),
        session_factory=session_factory,
        notify=lambda text: notifications.append(text) or True,
        generate=generate,
        output_dir=tmp_path / "output",
    )


def test_build_payload_matches_frontend_shape():
    payload = build_payload(_config(), "Why the ocean is salty")

    assert payload == {
        "videoSubject": "Why the ocean is salty",
        "aiModel": "llama3.1:8b",
        "voice": "en_us_001",
        "paragraphNumber": 1,
        "automateYoutubeUpload": True,
        "useMusic": False,
        "threads": payload["threads"],
        "subtitlesPosition": "center,center",
        "customPrompt": "",
        "color": "#FFFF00",
    }
    assert isinstance(payload["threads"], int) and payload["threads"] >= 1


def test_build_topic_prompt_mentions_niche_and_recent():
    prompt = build_topic_prompt("ocean facts", ["Old topic one", "Old topic two"])
    assert "ocean facts" in prompt
    assert "Old topic one" in prompt
    assert '{"subject"' in prompt


def test_start_message(pilot):
    assert pilot.start_message() == (
        "Autopilot started. Niche: ocean facts. 2/day, window 09:00-21:00 UTC."
    )


def test_maybe_create_job_uses_planned_topic_first(pilot, session_factory):
    with session_factory() as session:
        planned = add_topic(session, "Manual planned topic", None, "manual")

    job_id = pilot.maybe_create_job(NOON)

    assert job_id is not None
    with session_factory() as session:
        job = get_job(session, job_id)
        assert job.status == "queued"
        assert job.max_attempts == 2
        assert job.payload["videoSubject"] == "Manual planned topic"
        assert job.payload["automateYoutubeUpload"] is True
        topics = list_topics(session, None, 10)
        assert [t.id for t in topics] == [planned.id]
        assert topics[0].status == "queued"
        assert topics[0].job_id == job_id


def test_maybe_create_job_generates_topic_with_ollama(pilot, session_factory):
    job_id = pilot.maybe_create_job(NOON)

    assert job_id is not None
    with session_factory() as session:
        topics = list_topics(session, None, 10)
        assert len(topics) == 1
        assert topics[0].subject == "Why the ocean is salty"
        assert topics[0].source == "ollama"
        assert topics[0].niche == "ocean facts"
        assert get_job(session, job_id).payload["videoSubject"] == "Why the ocean is salty"


def test_maybe_create_job_respects_slot_rule(pilot, session_factory):
    assert pilot.maybe_create_job(datetime(2026, 9, 6, 8, 0, tzinfo=UTC)) is None

    first = pilot.maybe_create_job(NOON)
    assert first is not None
    with session_factory() as session:
        mark_completed(session, first, "output.mp4")

    # gap 6h not elapsed
    assert pilot.maybe_create_job(NOON + timedelta(hours=1)) is None


def test_maybe_create_job_waits_for_active_jobs(pilot, session_factory):
    with session_factory() as session:
        create_job(session, payload={"videoSubject": "ui job"})

    assert pilot.maybe_create_job(NOON) is None


def test_generate_topic_retries_and_skips_duplicates(session_factory, tmp_path, notifications):
    answers = iter(
        [
            "garbage",
            json.dumps({"subject": "Existing topic about whales"}),
            json.dumps({"subject": "A fresh topic about tides"}),
        ]
    )
    calls: list[str] = []

    def generate(prompt: str, ai_model: str) -> str:
        calls.append(prompt)
        return next(answers)

    pilot = Autopilot(_config(), session_factory, lambda text: notifications.append(text) or True, generate, tmp_path)
    with session_factory() as session:
        existing = add_topic(session, "existing topic about whales", None, "manual")
        queue_topic_job(session, existing, {"videoSubject": "existing topic about whales"})
        mark_completed(session, existing.job_id, "output.mp4")
    pilot.finish_completed_topics()

    with session_factory() as session:
        topic = pilot.generate_topic(session)

    assert topic is not None
    assert topic.subject == "A fresh topic about tides"
    assert len(calls) == 3
    assert "existing topic about whales" in calls[0]
    assert pilot.failed_topic_ticks == 0


def test_generate_topic_gives_up_after_three_attempts(session_factory, tmp_path, notifications):
    pilot = Autopilot(_config(), session_factory, lambda text: notifications.append(text) or True, lambda p, m: "nope", tmp_path)

    with session_factory() as session:
        assert pilot.generate_topic(session) is None
    assert pilot.failed_topic_ticks == 1


def test_topic_failures_notify_once_after_ten_ticks(session_factory, tmp_path, notifications):
    pilot = Autopilot(_config(), session_factory, lambda text: notifications.append(text) or True, lambda p, m: "nope", tmp_path)

    for _ in range(12):
        pilot.maybe_create_job(NOON)

    assert pilot.failed_topic_ticks == 12
    assert len(notifications) == 1
    assert "topic generation" in notifications[0].lower()


def test_generate_topic_rejects_wrong_length(session_factory, tmp_path, notifications):
    answers = iter([json.dumps({"subject": "Too short"}), json.dumps({"subject": "This subject has exactly six words"})])
    pilot = Autopilot(_config(), session_factory, lambda text: notifications.append(text) or True, lambda p, m: next(answers), tmp_path)

    with session_factory() as session:
        topic = pilot.generate_topic(session)

    assert topic.subject == "This subject has exactly six words"


def test_finish_completed_topics_marks_done_and_notifies_once(pilot, session_factory, notifications):
    job_id = pilot.maybe_create_job(NOON)
    with session_factory() as session:
        claim_next_queued_job(session)
        mark_completed(session, job_id, "output.mp4")
        add_artifact(session, job_id, "video", f"output/{job_id}.mp4", {"title": "Salty seas", "uploadError": None})
        add_artifact(session, job_id, "youtube_video", "https://youtu.be/abc", {"videoId": "abc", "privacyStatus": "private"})

    assert pilot.finish_completed_topics() == 1
    assert pilot.finish_completed_topics() == 0

    assert notifications == [f"✅ Salty seas\nhttps://youtu.be/abc\njob {job_id}"]
    with session_factory() as session:
        topic = list_topics(session, None, 1)[0]
        assert topic.status == "done"
        assert topic.completed_at is not None
        assert topics_awaiting_result(session) == []


def test_finish_completed_topics_reports_upload_skipped(pilot, session_factory, notifications):
    job_id = pilot.maybe_create_job(NOON)
    with session_factory() as session:
        claim_next_queued_job(session)
        mark_completed(session, job_id, "output.mp4")
        add_artifact(session, job_id, "video", f"output/{job_id}.mp4", {"title": "Salty seas", "uploadError": "No valid YouTube credentials"})

    pilot.finish_completed_topics()

    assert notifications == [
        f"✅ Salty seas\nupload skipped: No valid YouTube credentials\njob {job_id}"
    ]


def test_finish_completed_topics_reports_failure_and_cancel(pilot, session_factory, notifications):
    failed_id = pilot.maybe_create_job(NOON)
    with session_factory() as session:
        claim_next_queued_job(session)
        mark_failed(session, failed_id, "boom " * 200)
    pilot.finish_completed_topics()

    # A planned topic is used first; 7h after NOON the 6h gap has passed.
    with session_factory() as session:
        add_topic(session, "Second ocean topic", None, "manual")
    cancelled_id = pilot.maybe_create_job(NOON + timedelta(hours=7))
    assert cancelled_id is not None
    with session_factory() as session:
        mark_cancelled(session, cancelled_id)
    pilot.finish_completed_topics()

    assert notifications[0].startswith("❌ Why the ocean is salty\n")
    assert notifications[0].endswith(f"\njob {failed_id}, attempts 1")
    assert len(notifications[0].split("\n")[1]) <= 500
    assert notifications[1] == f"⚠️ Second ocean topic cancelled\njob {cancelled_id}"
    with session_factory() as session:
        statuses = sorted(t.status for t in list_topics(session, None, 10))
        assert statuses == ["failed", "failed"]


def test_finish_completed_topics_marks_vanished_job_failed(pilot, session_factory, notifications):
    with session_factory() as session:
        topic = add_topic(session, "Vanishing job topic", None, "manual")
        job = queue_topic_job(session, topic, {"videoSubject": "Vanishing job topic"})
        topic_id = topic.id
        job = get_job(session, job.id)
        session.delete(job)
        session.commit()

    assert pilot.finish_completed_topics() == 1

    assert len(notifications) == 1
    assert "not found" in notifications[0]
    with session_factory() as session:
        topic = list_topics(session, None, 10)[0]
        assert topic.id == topic_id
        assert topic.status == "failed"


def test_warn_stalled_topics_notifies_once(pilot, session_factory, notifications):
    with session_factory() as session:
        topic = add_topic(session, "Stalled topic", None, "manual")
        job = queue_topic_job(
            session, topic, {"videoSubject": "Stalled topic"}, now=NOON - timedelta(hours=4)
        )
        job_id = job.id

    assert pilot.warn_stalled_topics(NOON) == 1
    assert len(notifications) == 1
    assert "⚠️" in notifications[0]
    assert "Stalled topic" in notifications[0]
    assert job_id in notifications[0]

    assert pilot.warn_stalled_topics(NOON) == 0
    assert len(notifications) == 1


def test_warn_stalled_topics_ignores_recent_and_finished_jobs(pilot, session_factory, notifications):
    with session_factory() as session:
        recent_topic = add_topic(session, "Recent topic", None, "manual")
        queue_topic_job(
            session,
            recent_topic,
            {"videoSubject": "Recent topic"},
            now=NOON - timedelta(hours=1),
        )

        old_topic = add_topic(session, "Old finished topic", None, "manual")
        old_job = queue_topic_job(
            session,
            old_topic,
            {"videoSubject": "Old finished topic"},
            now=NOON - timedelta(hours=5),
        )
        mark_completed(session, old_job.id, "output.mp4")

    assert pilot.warn_stalled_topics(NOON) == 0
    assert notifications == []


def test_build_notifier_passes_config_credentials(monkeypatch):
    calls = []

    def recorder(text, token=None, chat_id=None):
        calls.append((text, token, chat_id))
        return True

    monkeypatch.setattr(autopilot, "send_telegram", recorder)
    config = _config(TELEGRAM_BOT_TOKEN="T", TELEGRAM_CHAT_ID="7")

    notifier = build_notifier(config)

    assert notifier("hi") is True
    assert calls == [("hi", "T", "7")]


def test_cleanup_output_deletes_old_files_hourly(pilot, tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    old = output / "old.mp4"
    fresh = output / "fresh.mp4"
    other = output / "keep.txt"
    for path in (old, fresh, other):
        path.write_bytes(b"x")
    import os

    eight_days_ago = (NOON - timedelta(days=8)).timestamp()
    os.utime(old, (eight_days_ago, eight_days_ago))
    os.utime(other, (eight_days_ago, eight_days_ago))

    assert pilot.cleanup_output(NOON) == 1
    assert not old.exists() and fresh.exists() and other.exists()
    assert pilot.cleanup_output(NOON + timedelta(minutes=30)) == 0
    assert pilot.last_cleanup_at == NOON


def test_run_tick_isolates_step_failures(pilot, monkeypatch, capsys):
    def boom() -> int:
        raise RuntimeError("db down")

    monkeypatch.setattr(pilot, "finish_completed_topics", boom)
    created: list[datetime] = []
    monkeypatch.setattr(pilot, "maybe_create_job", lambda now: created.append(now))
    monkeypatch.setattr(pilot, "cleanup_output", lambda now: 0)

    pilot.run_tick(NOON)

    assert created == [NOON]
    assert "db down" in capsys.readouterr().out


def test_main_exits_1_on_config_error(monkeypatch):
    monkeypatch.delenv("AUTOPILOT_NICHE", raising=False)
    monkeypatch.setenv("AUTOPILOT_ENABLED", "true")
    monkeypatch.setattr(autopilot, "load_dotenv", lambda path: None)
    monkeypatch.setattr(autopilot, "init_db", lambda: None)

    assert autopilot.main() == 1
