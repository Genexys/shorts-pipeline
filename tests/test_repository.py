from repository import (
    claim_next_queued_job,
    create_job,
    get_job,
    list_job_events,
    mark_failed,
    mark_completed,
    mark_cancelled,
    recover_running_jobs,
    request_cancel,
    requeue_for_retry,
)


def test_create_job_persists_payload_and_queued_event(session):
    payload = {"videoSubject": "money basics", "paragraphNumber": 1}

    job = create_job(session, payload=payload)

    assert job.id
    assert job.status == "queued"
    assert job.payload == payload

    events = list_job_events(session, job.id)
    assert len(events) == 1
    assert events[0].event_type == "queued"
    assert events[0].message == "Job queued."


def test_request_cancel_cancels_queued_job_and_tracks_events(session):
    job = create_job(session, payload={"videoSubject": "cancel me"})

    cancelled = request_cancel(session, job.id)

    assert cancelled is True

    events = list_job_events(session, job.id)
    event_types = [event.event_type for event in events]
    assert "cancel_requested" in event_types
    assert "cancelled" in event_types


def test_claim_next_queued_job_marks_running_and_skips_cancelled(session):
    first_job = create_job(session, payload={"videoSubject": "first"})
    second_job = create_job(session, payload={"videoSubject": "second"})
    request_cancel(session, first_job.id)

    claimed_job = claim_next_queued_job(session)

    assert claimed_job is not None
    assert claimed_job.id == second_job.id
    assert claimed_job.status == "running"
    assert claimed_job.attempt_count == 1


def test_mark_completed_updates_status_and_emits_complete_event(session):
    job = create_job(session, payload={"videoSubject": "done"})
    running_job = claim_next_queued_job(session)
    assert running_job is not None

    mark_completed(session, job.id, result_path="/tmp/output.mp4")

    events = list_job_events(session, job.id)
    complete_events = [event for event in events if event.event_type == "complete"]
    assert len(complete_events) == 1
    assert complete_events[0].payload == {"path": "/tmp/output.mp4"}


def test_mark_failed_updates_error_message_and_event(session):
    job = create_job(session, payload={"videoSubject": "bad run"})

    mark_failed(session, job.id, error_message="render crash")

    events = list_job_events(session, job.id)
    assert events[-1].event_type == "error"
    assert events[-1].message == "render crash"


def test_mark_cancelled_sets_status_and_writes_cancelled_event(session):
    job = create_job(session, payload={"videoSubject": "stop"})

    mark_cancelled(session, job.id, reason="cancelled in worker")

    events = list_job_events(session, job.id)
    assert events[-1].event_type == "cancelled"
    assert events[-1].message == "cancelled in worker"


def test_requeue_for_retry_sets_queued_and_retry_event(session):
    job = create_job(session, payload={"videoSubject": "retry"}, max_attempts=2)
    claim_next_queued_job(session)

    requeue_for_retry(session, job.id, error_message="tts down")

    updated = get_job(session, job.id)
    assert updated.status == "queued"
    assert updated.attempt_count == 1
    assert updated.error_message == "tts down"
    events = list_job_events(session, job.id)
    assert events[-1].event_type == "retry"
    assert events[-1].level == "warning"
    assert events[-1].message == "tts down"


def test_recover_running_jobs_requeues_or_fails(session):
    retryable = create_job(session, payload={"videoSubject": "a"}, max_attempts=2)
    exhausted = create_job(session, payload={"videoSubject": "b"}, max_attempts=1)
    claim_next_queued_job(session)
    claim_next_queued_job(session)
    assert get_job(session, retryable.id).status == "running"
    assert get_job(session, exhausted.id).status == "running"

    touched = recover_running_jobs(session)

    assert set(touched) == {retryable.id, exhausted.id}
    assert get_job(session, retryable.id).status == "queued"
    assert get_job(session, exhausted.id).status == "failed"
    assert get_job(session, exhausted.id).error_message == "worker restarted"
    assert list_job_events(session, retryable.id)[-1].event_type == "retry"
    assert list_job_events(session, exhausted.id)[-1].event_type == "error"


def test_recover_running_jobs_cancels_when_cancel_requested(session):
    job = create_job(session, payload={"videoSubject": "c"}, max_attempts=2)
    claim_next_queued_job(session)
    request_cancel(session, job.id)

    recover_running_jobs(session)

    assert get_job(session, job.id).status == "cancelled"
