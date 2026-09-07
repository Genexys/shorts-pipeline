from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from repository import (
    add_topic,
    claim_next_queued_job,
    count_topics_used_today,
    create_job,
    get_job,
    has_active_jobs,
    last_topic_used_at,
    list_job_events,
    list_topics,
    mark_completed,
    mark_topic_finished,
    next_planned_topic,
    normalize_subject,
    queue_topic_job,
    recent_topic_subjects,
    topics_awaiting_result,
)


def test_normalize_subject_lowercases_and_strips_punctuation():
    assert normalize_subject("  Why do Cats   PURR?! ") == "why do cats purr"
    assert normalize_subject("Top-5 facts: octopus") == "top5 facts octopus"


def test_add_topic_returns_none_on_duplicate(session):
    first = add_topic(session, "Why do cats purr?", "animals", "manual")
    duplicate = add_topic(session, "why do CATS purr", None, "ollama")

    assert first is not None
    assert first.status == "planned"
    assert first.normalized == "why do cats purr"
    assert duplicate is None
    assert len(list_topics(session, None, 10)) == 1


def test_add_topic_rejects_blank_subject(session):
    assert add_topic(session, "   ", None, "manual") is None


def test_next_planned_topic_returns_oldest_planned(session):
    older = add_topic(session, "first topic", None, "manual")
    add_topic(session, "second topic", None, "manual")

    picked = next_planned_topic(session)

    assert picked is not None
    assert picked.id == older.id


def test_queue_topic_job_creates_job_and_marks_topic(session):
    topic = add_topic(session, "queued topic", "niche", "ollama")

    job = queue_topic_job(session, topic, {"videoSubject": "queued topic"})

    assert job.status == "queued"
    assert job.max_attempts == 2
    assert job.payload == {"videoSubject": "queued topic"}
    assert topic.status == "queued"
    assert topic.job_id == job.id
    assert topic.used_at is not None
    assert list_job_events(session, job.id)[0].message == "Job queued by autopilot."
    assert next_planned_topic(session) is None
    assert [t.id for t in topics_awaiting_result(session)] == [topic.id]


def test_mark_topic_finished_sets_status_and_completed_at(session):
    topic = add_topic(session, "finish me", None, "manual")
    queue_topic_job(session, topic, {"videoSubject": "finish me"})

    mark_topic_finished(session, topic.id, "done")

    session.refresh(topic)
    assert topic.status == "done"
    assert topic.completed_at is not None
    assert topics_awaiting_result(session) == []


def test_recent_topic_subjects_newest_first(session):
    for index in range(3):
        add_topic(session, f"topic number {index}", None, "ollama")

    assert recent_topic_subjects(session, limit=2) == ["topic number 2", "topic number 1"]


def test_count_topics_used_today_respects_timezone(session):
    tz = ZoneInfo("Europe/Berlin")
    now = datetime(2026, 9, 6, 1, 30, tzinfo=tz)  # 23:30 UTC on Sep 5
    used_today = add_topic(session, "used today", None, "ollama")
    used_yesterday = add_topic(session, "used yesterday", None, "ollama")
    planned = add_topic(session, "planned only", None, "manual")
    queue_topic_job(session, used_today, {"videoSubject": "a"})
    queue_topic_job(session, used_yesterday, {"videoSubject": "b"})
    used_today.used_at = (now - timedelta(minutes=10)).astimezone(timezone.utc)
    used_yesterday.used_at = (now - timedelta(hours=3)).astimezone(timezone.utc)  # 22:30 Berlin on Sep 5
    session.commit()

    assert count_topics_used_today(session, now, tz) == 1
    assert planned.used_at is None


def test_last_topic_used_at_is_utc_aware(session):
    assert last_topic_used_at(session) is None

    topic = add_topic(session, "last used", None, "ollama")
    queue_topic_job(session, topic, {"videoSubject": "c"})

    last = last_topic_used_at(session)
    assert last is not None
    assert last.tzinfo is not None
    assert abs((datetime.now(timezone.utc) - last).total_seconds()) < 60


def test_topics_awaiting_result_includes_queued_without_job(session):
    stranded = add_topic(session, "vanished job topic", None, "manual")
    stranded.status = "queued"
    session.commit()
    add_topic(session, "still planned", None, "manual")

    result = topics_awaiting_result(session)

    assert [t.id for t in result] == [stranded.id]


def test_has_active_jobs(session):
    assert has_active_jobs(session) is False
    job = create_job(session, payload={"videoSubject": "ui job"})
    assert has_active_jobs(session) is True
    claim_next_queued_job(session)
    assert has_active_jobs(session) is True
    mark_completed(session, job.id, "output.mp4")
    assert has_active_jobs(session) is False


def test_list_topics_filters_by_status_newest_first(session):
    planned = add_topic(session, "planned one", None, "manual")
    queued = add_topic(session, "queued one", None, "manual")
    queue_topic_job(session, queued, {"videoSubject": "queued one"})

    only_planned = list_topics(session, "planned", 50)
    everything = list_topics(session, None, 50)

    assert [t.id for t in only_planned] == [planned.id]
    assert [t.id for t in everything] == [queued.id, planned.id]
    assert get_job(session, queued.job_id) is not None
