import time

from dotenv import load_dotenv

from db import SessionLocal, init_db
from logstream import log
from pipeline import PipelineCancelled, run_generation_pipeline
from repository import (
    add_artifact,
    append_event,
    claim_next_queued_job,
    get_job,
    mark_cancelled,
    mark_completed,
    mark_failed,
    recover_running_jobs,
    requeue_for_retry,
)
from utils import ENV_FILE, SUBTITLES_DIR, TEMP_DIR, check_env_vars, clean_dir


POLL_SECONDS = 1.0
RETRY_DELAY_SECONDS = 30

_sleep = time.sleep


def _job_cancelled(job_id: str) -> bool:
    with SessionLocal() as session:
        job = get_job(session, job_id)
        if not job:
            return True
        return bool(job.cancel_requested or job.status == "cancelled")


def _log_event(job_id: str, message: str, level: str) -> None:
    with SessionLocal() as session:
        append_event(session, job_id, "log", level, str(message))
        session.commit()


def process_next_job() -> bool:
    with SessionLocal() as session:
        job = claim_next_queued_job(session)

    if not job:
        return False

    job_id = job.id

    clean_dir(str(TEMP_DIR))
    clean_dir(str(SUBTITLES_DIR))

    try:
        result = run_generation_pipeline(
            data={**job.payload, "jobId": job_id},
            is_cancelled=lambda: _job_cancelled(job_id),
            on_log=lambda message, level: _log_event(job_id, message, level),
        )
    except PipelineCancelled as err:
        with SessionLocal() as session:
            mark_cancelled(session, job_id, str(err))
    except Exception as err:
        should_delay_retry = False
        with SessionLocal() as session:
            current = get_job(session, job_id)
            if current and current.cancel_requested:
                mark_cancelled(session, job_id, "Cancelled during failure handling.")
            elif current and (current.attempt_count or 0) < current.max_attempts:
                requeue_for_retry(session, job_id, str(err))
                should_delay_retry = True
            else:
                mark_failed(session, job_id, str(err))
        if should_delay_retry:
            _sleep(RETRY_DELAY_SECONDS)
    else:
        try:
            with SessionLocal() as session:
                add_artifact(
                    session,
                    job_id,
                    "video",
                    result.archived_path,
                    {
                        "title": result.title,
                        "uploadError": result.upload_error,
                        "format": result.format_name,
                    },
                    commit=False,
                )
                if result.youtube_video_id:
                    add_artifact(
                        session,
                        job_id,
                        "youtube_video",
                        f"https://youtu.be/{result.youtube_video_id}",
                        {
                            "videoId": result.youtube_video_id,
                            "privacyStatus": result.privacy_status,
                        },
                        commit=False,
                    )
                mark_completed(session, job_id, result.video_path)
        except Exception as err:
            log(f"[-] Bookkeeping failed for job {job_id}: {err}", "error")
            try:
                with SessionLocal() as session:
                    mark_failed(session, job_id, f"bookkeeping failed: {err}")
            except Exception as inner_err:
                log(
                    f"[-] Could not mark job {job_id} failed after bookkeeping "
                    f"error: {inner_err}",
                    "error",
                )

    return True


def main() -> None:
    load_dotenv(ENV_FILE)
    check_env_vars()
    init_db()

    with SessionLocal() as session:
        recovered = recover_running_jobs(session)
    if recovered:
        print(f"[worker] recovered {len(recovered)} interrupted job(s)")

    while True:
        processed = process_next_job()
        if not processed:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
