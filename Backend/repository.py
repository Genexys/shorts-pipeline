import re
from datetime import datetime, timedelta, timezone, tzinfo
from typing import TYPE_CHECKING, Optional, Sequence
from uuid import uuid4

from sqlalchemy import and_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from formats import resolve_format
from models import (
    Artifact,
    GenerationEvent,
    GenerationJob,
    ResearchSource,
    Script,
    Topic,
    VideoMetric,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to type checkers
    from analytics import VideoMetrics
    from research import Source


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


def as_utc(value: Optional[datetime]) -> Optional[datetime]:
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
        if (converted := as_utc(used_at)) is not None and converted >= day_start_utc
    )


def last_topic_used_at(session: Session) -> Optional[datetime]:
    stmt = (
        select(Topic.used_at)
        .where(Topic.used_at.is_not(None))
        .order_by(Topic.used_at.desc())
        .limit(1)
    )
    return as_utc(session.scalars(stmt).first())


def topics_awaiting_result(session: Session) -> list[Topic]:
    stmt = (
        select(Topic)
        .where(Topic.status == "queued")
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


def count_longform_since(session: Session, since: datetime) -> int:
    """Long-form jobs the autopilot queued since `since`, whatever their state.

    Counts jobs rather than finished artifacts on purpose: a long video that is
    queued or still rendering has already claimed its slot, and counting only
    completed ones would queue a second before the first finishes.

    Only the autopilot's own jobs count. The budget is a publishing schedule,
    and a job someone submitted by hand is not part of it — testing a change
    should not silently cost the channel a week of long-form. Autopilot jobs
    are exactly those a topic points at; `queue_topic_job` is the only path
    that makes one, so this cannot drift from reality the way a payload flag
    could.

    Compared in Python for the same reason as count_topics_used_today: SQLite
    stores tz-aware datetimes as naive strings, so a SQL comparison against an
    aware bound is unreliable. The table is small.
    """
    scheduled = set(
        session.scalars(select(Topic.job_id).where(Topic.job_id.is_not(None))).all()
    )
    rows = session.execute(
        select(GenerationJob.id, GenerationJob.payload, GenerationJob.created_at)
    ).all()
    return sum(
        1
        for job_id, payload, created_at in rows
        if job_id in scheduled
        and isinstance(payload, dict)
        and payload.get("format") == "long"
        and (converted := as_utc(created_at)) is not None
        and converted >= since
    )


def list_published_videos(session: Session) -> list[tuple[str, str, str, datetime]]:
    """(video_id, job_id, format_name, published_at) for everything uploaded.

    published_at is the artifact row's creation time, i.e. when the pipeline
    finished the upload. Close enough to the publication time for an age
    threshold measured in days.
    """
    stmt = (
        select(Artifact, GenerationJob.payload)
        .join(GenerationJob, GenerationJob.id == Artifact.job_id)
        .where(Artifact.artifact_type == "youtube_video")
        .order_by(Artifact.created_at)
    )
    rows: list[tuple[str, str, str, datetime]] = []
    for artifact, payload in session.execute(stmt):
        video_id = (artifact.metadata_json or {}).get("videoId")
        if not video_id:
            continue
        format_name = (payload or {}).get("format") or "short"
        created = as_utc(artifact.created_at)
        if created is None:
            continue
        rows.append((str(video_id), artifact.job_id, str(format_name), created))
    return rows


def upsert_metrics(
    session: Session,
    metrics: "VideoMetrics",
    job_id: Optional[str],
    format_name: str,
    published_at: datetime,
    commit: bool = True,
) -> VideoMetric:
    """Writes one video's numbers, replacing any earlier reading."""
    record = session.get(VideoMetric, metrics.video_id)
    if record is None:
        record = VideoMetric(video_id=metrics.video_id, published_at=published_at)
        session.add(record)
    record.job_id = job_id
    record.format_name = format_name
    record.published_at = published_at
    record.views = metrics.views
    record.average_view_percentage = metrics.average_view_percentage
    record.average_view_duration = metrics.average_view_duration
    record.measured_at = metrics.measured_at
    if commit:
        session.commit()
    return record


def _ranked_subjects(
    session: Session,
    format_name: str,
    limit: int,
    min_age_days: int,
    best: bool,
    now: Optional[datetime] = None,
) -> list[str]:
    """Subjects of the best or worst videos of one format.

    Ranked on the column the format nominates: percentage for Shorts, seconds
    for long form. Comparing the two would be meaningless.

    `min_age_days` is not optional and not caution. A video younger than that
    has no settled data — the API reports nothing for it, which reads as zero
    rather than as unknown — and the recommender has not finished placing it.
    """
    fmt = resolve_format(format_name)
    column = getattr(VideoMetric, fmt.ranking_metric)
    cutoff = (now or utcnow()) - timedelta(days=min_age_days)
    stmt = (
        select(Topic.subject)
        .join(VideoMetric, VideoMetric.job_id == Topic.job_id)
        .where(
            and_(
                VideoMetric.format_name == fmt.name,
                VideoMetric.published_at <= cutoff,
            )
        )
        .order_by(column.desc() if best else column.asc())
        .limit(limit)
    )
    return [subject for subject in session.scalars(stmt)]


def top_performing_subjects(
    session: Session, format_name: str, limit: int, min_age_days: int
) -> list[str]:
    """Subjects that held attention best, within one format."""
    return _ranked_subjects(session, format_name, limit, min_age_days, best=True)


def worst_performing_subjects(
    session: Session, format_name: str, limit: int, min_age_days: int
) -> list[str]:
    """Subjects that lost viewers earliest, within one format.

    Worth as much as the winners and cheaper to act on: it costs nothing to
    stop making something that demonstrably fails.
    """
    return _ranked_subjects(session, format_name, limit, min_age_days, best=False)


def add_script(
    session: Session,
    job_id: str,
    content: str,
    model_name: Optional[str] = None,
    commit: bool = True,
) -> Optional[Script]:
    """Keeps the narration script for a finished job.

    The table existed from the start and nothing ever wrote to it. Community
    posts are the first thing that needs the script after the video is made:
    without it a post can only restate the topic, which is the difference
    between adding something and repeating yourself.
    """
    if not content or not content.strip():
        return None
    script = Script(job_id=job_id, content=content, model_name=model_name)
    session.add(script)
    if commit:
        session.commit()
    return script


def get_script(session: Session, job_id: str) -> Optional[str]:
    """The stored script for a job, newest first, or None."""
    stmt = (
        select(Script.content)
        .where(Script.job_id == job_id)
        .order_by(Script.id.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()


def add_research_sources(
    session: Session,
    job_id: str,
    sources: "Sequence[Source]",
    commit: bool = True,
) -> int:
    """Keeps the sources a script was written from. Returns how many were kept.

    The leftovers matter more than the ones that made it in: a 120-word Short
    uses a few facts out of several sources, and what it left behind is what a
    community post can say that the video did not.
    """
    kept = 0
    for source in sources or []:
        url = getattr(source, "url", "")
        if not url:
            continue
        session.add(
            ResearchSource(
                job_id=job_id,
                title=(getattr(source, "title", "") or "")[:512],
                url=url[:1024],
                snippet=getattr(source, "snippet", "") or "",
            )
        )
        kept += 1
    if commit:
        session.commit()
    return kept


def get_research_sources(session: Session, job_id: str) -> list[ResearchSource]:
    """Sources stored for a job, in the order they were found."""
    stmt = (
        select(ResearchSource)
        .where(ResearchSource.job_id == job_id)
        .order_by(ResearchSource.id)
    )
    return list(session.scalars(stmt))
