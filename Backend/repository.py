import re
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Optional
from uuid import uuid4

from sqlalchemy import and_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import Artifact, GenerationEvent, GenerationJob, Topic


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_job(session: Session, payload: dict, max_attempts: int, message: str) -> GenerationJob:
    job = GenerationJob(
        id=str(uuid4()),
        status="queued",
        payload=payload,
        max_attempts=max_attempts,
        cancel_requested=False,
    )
    session.add(job)
    session.flush()
    append_event(session, job.id, "queued", "info", message)
    return job


def create_job(session: Session, payload: dict, max_attempts: int = 1) -> GenerationJob:
    job = _new_job(session, payload, max_attempts, "Job queued.")
    session.commit()
    session.refresh(job)
    return job


def append_event(
    session: Session,
    job_id: str,
    event_type: str,
    level: str,
    message: str,
    payload: Optional[dict] = None,
) -> GenerationEvent:
    event = GenerationEvent(
        job_id=job_id,
        event_type=event_type,
        level=level,
        message=message,
        payload=payload,
    )
    session.add(event)
    session.flush()
    return event


def get_job(session: Session, job_id: str) -> Optional[GenerationJob]:
    return session.get(GenerationJob, job_id)


def list_job_events(
    session: Session, job_id: str, after_id: int = 0, limit: int = 200
) -> list[GenerationEvent]:
    stmt = (
        select(GenerationEvent)
        .where(
            and_(
                GenerationEvent.job_id == job_id,
                GenerationEvent.id > after_id,
            )
        )
        .order_by(GenerationEvent.id.asc())
        .limit(limit)
    )
    return list(session.scalars(stmt).all())


def request_cancel(session: Session, job_id: str) -> bool:
    job = get_job(session, job_id)
    if not job:
        return False

    if job.status in ("completed", "failed", "cancelled"):
        return True

    job.cancel_requested = True
    job.updated_at = utcnow()
    append_event(
        session, job.id, "cancel_requested", "warning", "Cancellation requested."
    )

    if job.status == "queued":
        job.status = "cancelled"
        job.completed_at = utcnow()
        append_event(
            session, job.id, "cancelled", "warning", "Job cancelled before execution."
        )

    session.commit()
    return True


def claim_next_queued_job(session: Session) -> Optional[GenerationJob]:
    dialect = session.bind.dialect.name if session.bind else ""

    if dialect == "postgresql":
        row = session.execute(
            text(
                """
                SELECT id
                FROM generation_jobs
                WHERE status = 'queued' AND cancel_requested = false
                ORDER BY created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
        ).first()
        if not row:
            return None
        job = get_job(session, row[0])
    else:
        stmt = (
            select(GenerationJob)
            .where(
                and_(
                    GenerationJob.status == "queued",
                    GenerationJob.cancel_requested.is_(False),
                )
            )
            .order_by(GenerationJob.created_at.asc())
            .limit(1)
        )
        job = session.scalars(stmt).first()

    if not job:
        return None

    job.status = "running"
    job.attempt_count = (job.attempt_count or 0) + 1
    job.started_at = utcnow()
    job.updated_at = utcnow()
    append_event(session, job.id, "running", "info", "Job started.")
    session.commit()
    session.refresh(job)
    return job


def mark_completed(session: Session, job_id: str, result_path: str) -> None:
    job = get_job(session, job_id)
    if not job:
        return
    job.status = "completed"
    job.result_path = result_path
    job.error_message = None
    job.completed_at = utcnow()
    job.updated_at = utcnow()
    append_event(
        session,
        job.id,
        "complete",
        "success",
        "Video generated successfully.",
        {"path": result_path},
    )
    session.commit()


def mark_cancelled(
    session: Session, job_id: str, reason: str = "Job cancelled."
) -> None:
    job = get_job(session, job_id)
    if not job:
        return
    job.status = "cancelled"
    job.completed_at = utcnow()
    job.updated_at = utcnow()
    append_event(session, job.id, "cancelled", "warning", reason)
    session.commit()


def mark_failed(session: Session, job_id: str, error_message: str) -> None:
    job = get_job(session, job_id)
    if not job:
        return
    job.status = "failed"
    job.error_message = error_message
    job.completed_at = utcnow()
    job.updated_at = utcnow()
    append_event(session, job.id, "error", "error", error_message)
    session.commit()


def requeue_for_retry(session: Session, job_id: str, error_message: str) -> None:
    job = get_job(session, job_id)
    if not job:
        return
    job.status = "queued"
    job.error_message = error_message
    job.started_at = None
    job.updated_at = utcnow()
    append_event(
        session,
        job.id,
        "retry",
        "warning",
        error_message,
        {"attempt": job.attempt_count, "maxAttempts": job.max_attempts},
    )
    session.commit()


def recover_running_jobs(session: Session) -> list[str]:
    """Called once at worker startup. Any job still 'running' was interrupted.

    Also clears unclaimable cancelled rows: a job left `queued` with
    `cancel_requested=True` (for example from a cancel that raced a worker
    crash) can never be claimed by `claim_next_queued_job`, so it is resolved
    to `cancelled` here too.
    """
    stmt = select(GenerationJob).where(GenerationJob.status == "running")
    touched: list[str] = []
    for job in list(session.scalars(stmt).all()):
        touched.append(job.id)
        if job.cancel_requested:
            mark_cancelled(session, job.id, "Cancelled while worker restarted.")
        elif (job.attempt_count or 0) < job.max_attempts:
            requeue_for_retry(session, job.id, "worker restarted")
        else:
            mark_failed(session, job.id, "worker restarted")

    stuck_stmt = select(GenerationJob).where(
        and_(
            GenerationJob.status == "queued",
            GenerationJob.cancel_requested.is_(True),
        )
    )
    for job in list(session.scalars(stuck_stmt).all()):
        touched.append(job.id)
        mark_cancelled(session, job.id, "Cancelled before execution.")

    return touched


def add_artifact(
    session: Session,
    job_id: str,
    artifact_type: str,
    path: str,
    metadata: Optional[dict] = None,
    commit: bool = True,
) -> Artifact:
    artifact = Artifact(
        job_id=job_id,
        artifact_type=artifact_type,
        path=path,
        metadata_json=metadata,
    )
    session.add(artifact)
    if not commit:
        session.flush()
        return artifact
    session.commit()
    session.refresh(artifact)
    return artifact


def list_artifacts(session: Session, job_id: str) -> list[Artifact]:
    stmt = (
        select(Artifact)
        .where(Artifact.job_id == job_id)
        .order_by(Artifact.id.asc())
    )
    return list(session.scalars(stmt).all())


# ---------------------------------------------------------------------------
# Topics (autopilot)
# ---------------------------------------------------------------------------


def normalize_subject(subject: str) -> str:
    lowered = subject.lower()
    kept = "".join(ch if ch.isalnum() or ch.isspace() else "" for ch in lowered)
    return re.sub(r"\s+", " ", kept).strip()


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite returns naive datetimes; they were stored as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def add_topic(
    session: Session, subject: str, niche: Optional[str], source: str
) -> Optional[Topic]:
    cleaned = re.sub(r"\s+", " ", subject or "").strip()
    normalized = normalize_subject(cleaned)
    if not normalized:
        return None
    topic = Topic(
        subject=cleaned[:255],
        normalized=normalized[:255],
        niche=niche,
        source=source,
        status="planned",
    )
    session.add(topic)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return None
    session.refresh(topic)
    return topic


def next_planned_topic(session: Session) -> Optional[Topic]:
    stmt = (
        select(Topic)
        .where(Topic.status == "planned")
        .order_by(Topic.created_at.asc(), Topic.id.asc())
        .limit(1)
    )
    return session.scalars(stmt).first()


def queue_topic_job(
    session: Session,
    topic: Topic,
    payload: dict,
    max_attempts: int = 2,
    now: Optional[datetime] = None,
) -> GenerationJob:
    job = _new_job(session, payload, max_attempts, "Job queued by autopilot.")
    topic.status = "queued"
    topic.job_id = job.id
    # SQLAlchemy's DateTime(timezone=True) on SQLite persists the wall-clock
    # value and drops tzinfo, so normalize to UTC before storing to keep the
    # stored representation consistent across SQLite and Postgres.
    topic.used_at = (now or utcnow()).astimezone(timezone.utc)
    session.commit()
    session.refresh(job)
    session.refresh(topic)
    return job


def mark_topic_finished(session: Session, topic_id: int, status: str) -> None:
    topic = session.get(Topic, topic_id)
    if not topic:
        return
    topic.status = status
    topic.completed_at = utcnow()
    session.commit()


def recent_topic_subjects(session: Session, limit: int = 50) -> list[str]:
    stmt = select(Topic.subject).order_by(Topic.id.desc()).limit(limit)
    return list(session.scalars(stmt).all())


def count_topics_used_today(session: Session, now: datetime, tz: tzinfo) -> int:
    """Topics whose used_at falls on the local calendar day of `now` in `tz`.

    Compared in Python on purpose: SQLite stores tz-aware datetimes as naive
    strings, so a SQL `>=` against an aware bound is unreliable. The table is small.
    """
    local_now = now.astimezone(tz)
    day_start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_start_utc = day_start_local.astimezone(timezone.utc)
    used_values = session.scalars(
        select(Topic.used_at).where(Topic.used_at.is_not(None))
    ).all()
    return sum(
        1
        for used_at in used_values
        if (converted := _as_utc(used_at)) is not None and converted >= day_start_utc
    )


def last_topic_used_at(session: Session) -> Optional[datetime]:
    stmt = (
        select(Topic.used_at)
        .where(Topic.used_at.is_not(None))
        .order_by(Topic.used_at.desc())
        .limit(1)
    )
    return _as_utc(session.scalars(stmt).first())


def topics_awaiting_result(session: Session) -> list[Topic]:
    stmt = (
        select(Topic)
        .where(and_(Topic.status == "queued", Topic.job_id.is_not(None)))
        .order_by(Topic.id.asc())
    )
    return list(session.scalars(stmt).all())


def has_active_jobs(session: Session) -> bool:
    stmt = (
        select(GenerationJob.id)
        .where(GenerationJob.status.in_(["queued", "running"]))
        .limit(1)
    )
    return session.scalars(stmt).first() is not None


def list_topics(session: Session, status: Optional[str], limit: int) -> list[Topic]:
    stmt = select(Topic).order_by(Topic.id.desc()).limit(limit)
    if status:
        stmt = stmt.where(Topic.status == status)
    return list(session.scalars(stmt).all())
