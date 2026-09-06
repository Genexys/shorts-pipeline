from pipeline import PipelineResult
from repository import create_job, get_job, list_artifacts, list_job_events
import worker


def _disable_cleanup(monkeypatch):
    monkeypatch.setattr(worker, "clean_dir", lambda _: None)


def _no_sleep(monkeypatch) -> list[int]:
    """Patch worker._sleep so retry-branch tests never actually sleep."""
    sleeps: list[int] = []
    monkeypatch.setattr(worker, "_sleep", lambda seconds: sleeps.append(seconds))
    return sleeps


def test_process_next_job_returns_false_when_queue_is_empty(
    monkeypatch, session_factory
):
    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    _disable_cleanup(monkeypatch)

    assert worker.process_next_job() is False


def test_process_next_job_marks_completed_and_records_artifacts(
    monkeypatch, session_factory
):
    with session_factory() as session:
        job = create_job(session, payload={"videoSubject": "worker success"})

    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    _disable_cleanup(monkeypatch)

    def fake_pipeline(data, is_cancelled, on_log):
        assert data["videoSubject"] == "worker success"
        assert data["jobId"] == job.id
        assert is_cancelled() is False
        on_log("pipeline started", "info")
        return PipelineResult(
            video_path="output.mp4",
            archived_path=f"output/{data['jobId']}.mp4",
            title="Great title",
            youtube_video_id="vid123",
            upload_error=None,
            privacy_status="private",
        )

    monkeypatch.setattr(worker, "run_generation_pipeline", fake_pipeline)

    assert worker.process_next_job() is True

    with session_factory() as session:
        updated_job = get_job(session, job.id)
        assert updated_job is not None
        assert updated_job.status == "completed"
        assert updated_job.result_path == "output.mp4"
        assert updated_job.payload == {"videoSubject": "worker success"}

        event_types = [event.event_type for event in list_job_events(session, job.id)]
        assert "running" in event_types
        assert "log" in event_types
        assert "complete" in event_types

        artifacts = {a.artifact_type: a for a in list_artifacts(session, job.id)}
        assert artifacts["video"].path == f"output/{job.id}.mp4"
        assert artifacts["video"].metadata_json == {"title": "Great title", "uploadError": None}
        assert artifacts["youtube_video"].path == "https://youtu.be/vid123"
        assert artifacts["youtube_video"].metadata_json == {
            "videoId": "vid123",
            "privacyStatus": "private",
        }


def test_process_next_job_marks_failed_when_bookkeeping_raises(
    monkeypatch, session_factory
):
    with session_factory() as session:
        job = create_job(session, payload={"videoSubject": "bookkeeping down"})

    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    _disable_cleanup(monkeypatch)
    _no_sleep(monkeypatch)

    def fake_pipeline(data, is_cancelled, on_log):
        return PipelineResult(
            video_path="output.mp4",
            archived_path=f"output/{data['jobId']}.mp4",
            title="Great title",
            youtube_video_id=None,
            upload_error=None,
            privacy_status="private",
        )

    monkeypatch.setattr(worker, "run_generation_pipeline", fake_pipeline)

    def failing_add_artifact(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(worker, "add_artifact", failing_add_artifact)

    assert worker.process_next_job() is True

    with session_factory() as session:
        updated_job = get_job(session, job.id)
        assert updated_job is not None
        assert updated_job.status == "failed"
        assert updated_job.attempt_count == 1
        assert updated_job.error_message is not None
        assert updated_job.error_message.startswith("bookkeeping failed:")
        assert list_artifacts(session, job.id) == []


def test_process_next_job_records_upload_error_without_youtube_artifact(
    monkeypatch, session_factory
):
    with session_factory() as session:
        job = create_job(session, payload={"videoSubject": "no upload"})

    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    _disable_cleanup(monkeypatch)

    def fake_pipeline(data, is_cancelled, on_log):
        return PipelineResult(
            video_path="output.mp4",
            archived_path=f"output/{data['jobId']}.mp4",
            title="T",
            youtube_video_id=None,
            upload_error="No valid YouTube credentials",
            privacy_status="private",
        )

    monkeypatch.setattr(worker, "run_generation_pipeline", fake_pipeline)

    assert worker.process_next_job() is True

    with session_factory() as session:
        assert get_job(session, job.id).status == "completed"
        artifacts = list_artifacts(session, job.id)
        assert [a.artifact_type for a in artifacts] == ["video"]
        assert artifacts[0].metadata_json["uploadError"] == "No valid YouTube credentials"


def test_process_next_job_marks_cancelled_on_pipeline_cancelled(
    monkeypatch, session_factory
):
    with session_factory() as session:
        job = create_job(session, payload={"videoSubject": "worker cancelled"})

    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    _disable_cleanup(monkeypatch)

    def fake_pipeline(*_args, **_kwargs):
        raise worker.PipelineCancelled("cancelled by user")

    monkeypatch.setattr(worker, "run_generation_pipeline", fake_pipeline)

    assert worker.process_next_job() is True

    with session_factory() as session:
        updated_job = get_job(session, job.id)
        assert updated_job is not None
        assert updated_job.status == "cancelled"

        events = list_job_events(session, job.id)
        assert events[-1].event_type == "cancelled"
        assert events[-1].message == "cancelled by user"


def test_process_next_job_marks_failed_on_pipeline_error(monkeypatch, session_factory):
    with session_factory() as session:
        job = create_job(session, payload={"videoSubject": "worker failure"})

    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    _disable_cleanup(monkeypatch)
    _no_sleep(monkeypatch)

    def fake_pipeline(*_args, **_kwargs):
        raise RuntimeError("pipeline exploded")

    monkeypatch.setattr(worker, "run_generation_pipeline", fake_pipeline)

    assert worker.process_next_job() is True

    with session_factory() as session:
        updated_job = get_job(session, job.id)
        assert updated_job is not None
        assert updated_job.status == "failed"
        assert updated_job.error_message == "pipeline exploded"

        events = list_job_events(session, job.id)
        assert events[-1].event_type == "error"
        assert events[-1].message == "pipeline exploded"


def test_job_cancelled_helper_returns_true_for_missing_job(
    monkeypatch, session_factory
):
    monkeypatch.setattr(worker, "SessionLocal", session_factory)

    assert worker._job_cancelled("missing-job-id") is True


def test_job_cancelled_helper_reflects_cancel_flag(monkeypatch, session_factory):
    with session_factory() as session:
        job = create_job(session, payload={"videoSubject": "cancel-check"})

    monkeypatch.setattr(worker, "SessionLocal", session_factory)

    assert worker._job_cancelled(job.id) is False

    with session_factory() as session:
        job_to_update = get_job(session, job.id)
        assert job_to_update is not None
        job_to_update.cancel_requested = True
        session.commit()

    assert worker._job_cancelled(job.id) is True


def test_log_event_helper_persists_log_event(monkeypatch, session_factory):
    with session_factory() as session:
        job = create_job(session, payload={"videoSubject": "log-check"})

    monkeypatch.setattr(worker, "SessionLocal", session_factory)

    worker._log_event(job.id, "hello event", "warning")

    with session_factory() as session:
        events = list_job_events(session, job.id)
        assert events[-1].event_type == "log"
        assert events[-1].level == "warning"
        assert events[-1].message == "hello event"


def test_process_next_job_requeues_when_attempts_remain(monkeypatch, session_factory):
    with session_factory() as session:
        job = create_job(session, payload={"videoSubject": "retry me"}, max_attempts=2)

    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    _disable_cleanup(monkeypatch)
    sleeps = _no_sleep(monkeypatch)

    def fake_pipeline(*_args, **_kwargs):
        raise RuntimeError("tts unavailable")

    monkeypatch.setattr(worker, "run_generation_pipeline", fake_pipeline)

    assert worker.process_next_job() is True
    with session_factory() as session:
        first = get_job(session, job.id)
        assert first.status == "queued"
        assert first.attempt_count == 1
        assert list_job_events(session, job.id)[-1].event_type == "retry"
    assert sleeps == [worker.RETRY_DELAY_SECONDS]

    assert worker.process_next_job() is True
    with session_factory() as session:
        second = get_job(session, job.id)
        assert second.status == "failed"
        assert second.attempt_count == 2
        assert second.error_message == "tts unavailable"
    assert sleeps == [worker.RETRY_DELAY_SECONDS]


def test_process_next_job_cancels_instead_of_requeue_when_cancel_races_failure(
    monkeypatch, session_factory
):
    with session_factory() as session:
        job = create_job(session, payload={"videoSubject": "cancel race"}, max_attempts=2)

    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    _disable_cleanup(monkeypatch)
    sleeps = _no_sleep(monkeypatch)

    def fake_pipeline(*_args, **_kwargs):
        with session_factory() as session:
            racing_job = get_job(session, job.id)
            racing_job.cancel_requested = True
            session.commit()
        raise RuntimeError("boom during cancel race")

    monkeypatch.setattr(worker, "run_generation_pipeline", fake_pipeline)

    assert worker.process_next_job() is True
    with session_factory() as session:
        updated = get_job(session, job.id)
        assert updated.status == "cancelled"
        assert updated.attempt_count == 1
        assert list_job_events(session, job.id)[-1].event_type == "cancelled"
    assert sleeps == []

    # Nothing claimable: the job is cancelled, not stuck in queued.
    assert worker.process_next_job() is False
