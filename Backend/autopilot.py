"""Autopilot: invents topics, queues generation jobs on a schedule, reports to Telegram.

Run:  uv run python Backend/autopilot.py
"""

import functools
import os
import sys
import textwrap
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from dotenv import load_dotenv
from sqlalchemy.orm import Session

from autopilot_config import AutopilotConfig, ConfigError, slot_available
from db import SessionLocal, init_db
from gpt import extract_json_object, generate_response
from logstream import log
from models import Topic
from notify import send_telegram
from repository import (
    add_topic,
    as_utc,
    count_topics_used_today,
    get_job,
    has_active_jobs,
    last_topic_used_at,
    list_artifacts,
    mark_topic_finished,
    next_planned_topic,
    normalize_subject,
    queue_topic_job,
    recent_topic_subjects,
    topics_awaiting_result,
)
from utils import ENV_FILE, OUTPUT_DIR

TICK_SECONDS = 60
CLEANUP_INTERVAL_SECONDS = 3600
TOPIC_ATTEMPTS = 3
TOPIC_FAILURE_NOTIFY_AFTER = 10
RECENT_TOPICS_LIMIT = 50
TOPIC_MIN_WORDS = 4
TOPIC_MAX_WORDS = 12
ERROR_TEXT_LIMIT = 500
STALL_WARNING_SECONDS = 3 * 3600


def build_payload(
    config: AutopilotConfig, subject: str, format_name: str = "short"
) -> dict:
    """Same shape as Frontend/app.js sends, plus upload flag and thread count."""
    return {
        "videoSubject": subject,
        "format": format_name,
        "aiModel": config.model,
        "voice": config.voice,
        "paragraphNumber": config.paragraphs,
        "automateYoutubeUpload": True,
        "useMusic": config.use_music,
        "threads": os.cpu_count() or 2,
        "subtitlesPosition": config.subtitles_position,
        "customPrompt": config.custom_prompt,
        "color": config.color,
    }


def build_notifier(config: AutopilotConfig) -> Callable[[str], bool]:
    return functools.partial(
        send_telegram, token=config.telegram_bot_token, chat_id=config.telegram_chat_id
    )


def build_topic_prompt(niche: str, recent: list[str]) -> str:
    # Substitution happens after dedent/strip (via .format, not an f-string): recent
    # subjects are unindented, and interpolating them before dedent would drag the
    # whole block's common-indent calculation down to zero, leaving every other line
    # still indented.
    recent_block = "\n".join(f"- {subject}" for subject in recent) or "- (none yet)"
    template = textwrap.dedent(
        """
        You pick topics for short vertical YouTube videos (YouTube Shorts).

        Channel niche: {niche}

        Propose ONE new video topic. Requirements:
        - Written in English.
        - Between {min_words} and {max_words} words.
        - A concrete fact, question or claim, not a broad category.
        - Must not repeat or rephrase any of the topics already used below.

        Topics already used (do not repeat, do not paraphrase):
        {recent_block}

        Return ONLY a JSON object: {{"subject": "..."}}
        """
    ).strip()
    return template.format(
        niche=niche,
        min_words=TOPIC_MIN_WORDS,
        max_words=TOPIC_MAX_WORDS,
        recent_block=recent_block,
    )


class Autopilot:
    def __init__(
        self,
        config: AutopilotConfig,
        session_factory: Callable[[], Session],
        notify: Callable[[str], bool] = send_telegram,
        generate: Callable[[str, str], str] = generate_response,
        output_dir: Path = OUTPUT_DIR,
    ) -> None:
        self.config = config
        self.session_factory = session_factory
        self.notify = notify
        self.generate = generate
        self.output_dir = Path(output_dir)
        self.failed_topic_ticks = 0
        self.topic_failure_notified = False
        self.last_cleanup_at: Optional[datetime] = None
        self.stall_notified: set[int] = set()

    # -- messages ------------------------------------------------------------

    def start_message(self) -> str:
        return (
            f"Autopilot started. Niche: {self.config.niche}. "
            f"{self.config.videos_per_day}/day, window {self.config.window_label} "
            f"{self.config.tz_name}."
        )

    # -- tick ----------------------------------------------------------------

    def run_tick(self, now: datetime) -> None:
        for step in (
            lambda: self.finish_completed_topics(),
            lambda: self.warn_stalled_topics(now),
            lambda: self.maybe_create_job(now),
            lambda: self.cleanup_output(now),
        ):
            try:
                step()
            except Exception as err:
                log(f"[-] Autopilot step failed: {err}", "error")

    # -- step 1 --------------------------------------------------------------

    def finish_completed_topics(self) -> int:
        finished = 0
        with self.session_factory() as session:
            for topic in topics_awaiting_result(session):
                job = get_job(session, topic.job_id) if topic.job_id else None
                if job is None:
                    job_ref = topic.job_id or "unknown"
                    mark_topic_finished(session, topic.id, "failed")
                    self.notify(f"❌ {topic.subject}\njob {job_ref} not found\njob {job_ref}, attempts 0")
                    finished += 1
                    continue
                if job.status == "completed":
                    message = self._success_message(session, topic, job.id)
                    mark_topic_finished(session, topic.id, "done")
                elif job.status == "failed":
                    error = (job.error_message or "unknown error")[:ERROR_TEXT_LIMIT]
                    message = f"❌ {topic.subject}\n{error}\njob {job.id}, attempts {job.attempt_count}"
                    mark_topic_finished(session, topic.id, "failed")
                elif job.status == "cancelled":
                    message = f"⚠️ {topic.subject} cancelled\njob {job.id}"
                    mark_topic_finished(session, topic.id, "failed")
                else:
                    continue
                finished += 1
                self.notify(message)
        return finished

    def _success_message(self, session: Session, topic: Topic, job_id: str) -> str:
        title = topic.subject
        second_line = "upload skipped: unknown reason"
        for artifact in list_artifacts(session, job_id):
            metadata = artifact.metadata_json or {}
            if artifact.artifact_type == "video":
                title = metadata.get("title") or title
                if metadata.get("uploadError"):
                    second_line = f"upload skipped: {metadata['uploadError']}"
            elif artifact.artifact_type == "youtube_video":
                second_line = artifact.path
        return f"✅ {title}\n{second_line}\njob {job_id}"

    def warn_stalled_topics(self, now: datetime) -> int:
        """Notify once per topic when its job has been queued/running for too long."""
        warned = 0
        with self.session_factory() as session:
            for topic in topics_awaiting_result(session):
                if topic.id in self.stall_notified or topic.job_id is None:
                    continue
                job = get_job(session, topic.job_id)
                if job is None or job.status not in ("queued", "running"):
                    continue
                used_at = as_utc(topic.used_at)
                if used_at is None or (now - used_at).total_seconds() < STALL_WARNING_SECONDS:
                    continue
                hours = int((now - used_at).total_seconds() // 3600)
                self.stall_notified.add(topic.id)
                self.notify(
                    f"⚠️ {topic.subject}\njob {job.id} {job.status} for {hours}h, worker may be stuck"
                )
                warned += 1
        return warned

    # -- step 2 --------------------------------------------------------------

    def maybe_create_job(self, now: datetime) -> Optional[str]:
        with self.session_factory() as session:
            used_today = count_topics_used_today(session, now, self.config.tz)
            last_used = last_topic_used_at(session)
            if not slot_available(now, self.config, used_today, last_used):
                return None
            # Not atomic against a concurrent POST /api/generate: the worker only runs
            # one job at a time, so a race here costs one extra queued job, not a crash.
            if has_active_jobs(session):
                log("[*] Autopilot: a job is already queued or running, waiting.", "info")
                return None

            topic = next_planned_topic(session) or self.generate_topic(session)
            if topic is None:
                return None

            job = queue_topic_job(
                session, topic, build_payload(self.config, topic.subject), now=now
            )
            log(f"[+] Autopilot queued job {job.id} for topic '{topic.subject}'", "success")
            return job.id

    def generate_topic(self, session: Session) -> Optional[Topic]:
        recent = recent_topic_subjects(session, RECENT_TOPICS_LIMIT)
        prompt = build_topic_prompt(self.config.niche, recent)
        last_problem = "no response"
        for attempt in range(1, TOPIC_ATTEMPTS + 1):
            try:
                response = self.generate(prompt, self.config.model)
            except Exception as err:
                last_problem = f"Ollama error: {err}"
                log(f"[-] Topic attempt {attempt}/{TOPIC_ATTEMPTS}: {last_problem}", "warning")
                continue
            parsed = extract_json_object(response)
            subject = parsed.get("subject") if isinstance(parsed, dict) else None
            if not isinstance(subject, str) or not subject.strip():
                last_problem = "response is not JSON with a 'subject' key"
                log(f"[-] Topic attempt {attempt}/{TOPIC_ATTEMPTS}: {last_problem}", "warning")
                continue
            subject = " ".join(subject.split())
            word_count = len(subject.split())
            if word_count < TOPIC_MIN_WORDS or word_count > TOPIC_MAX_WORDS:
                last_problem = f"subject has {word_count} words: '{subject}'"
                log(f"[-] Topic attempt {attempt}/{TOPIC_ATTEMPTS}: {last_problem}", "warning")
                continue
            topic = add_topic(session, subject, self.config.niche, "ollama")
            if topic is None:
                last_problem = f"duplicate topic '{normalize_subject(subject)}'"
                log(f"[-] Topic attempt {attempt}/{TOPIC_ATTEMPTS}: {last_problem}", "warning")
                continue
            self.failed_topic_ticks = 0
            self.topic_failure_notified = False
            return topic

        self.failed_topic_ticks += 1
        log(
            f"[!] Autopilot could not get a topic ({last_problem}). "
            f"Failed ticks in a row: {self.failed_topic_ticks}.",
            "warning",
        )
        if self.failed_topic_ticks >= TOPIC_FAILURE_NOTIFY_AFTER and not self.topic_failure_notified:
            self.topic_failure_notified = True
            self.notify(
                f"⚠️ Autopilot: topic generation failed {self.failed_topic_ticks} ticks in a row. "
                f"Last problem: {last_problem}"
            )
        return None

    # -- step 3 --------------------------------------------------------------

    def cleanup_output(self, now: datetime) -> int:
        if self.last_cleanup_at is not None and (
            now - self.last_cleanup_at
        ) < timedelta(seconds=CLEANUP_INTERVAL_SECONDS):
            return 0
        self.last_cleanup_at = now
        if not self.output_dir.exists():
            return 0
        cutoff = now.timestamp() - self.config.output_retention_days * 86400
        deleted = 0
        for path in self.output_dir.glob("*.mp4"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    deleted += 1
            except OSError as err:
                log(f"[-] Could not delete {path}: {err}", "warning")
        if deleted:
            log(f"[+] Autopilot deleted {deleted} old video(s) from {self.output_dir}", "info")
        return deleted


def main() -> int:
    load_dotenv(ENV_FILE)
    init_db()
    try:
        config = AutopilotConfig.from_env()
    except ConfigError as err:
        print(f"[autopilot] configuration error: {err}")
        return 1

    pilot = Autopilot(config, SessionLocal, notify=build_notifier(config))
    log(f"[+] {pilot.start_message()}", "success")
    pilot.notify(pilot.start_message())

    while True:
        pilot.run_tick(datetime.now(timezone.utc))
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
