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

from analytics import fetch_metrics
from autopilot_config import (
    AutopilotConfig,
    ConfigError,
    choose_register,
    next_format,
    slot_available,
    week_start,
)
from db import SessionLocal, init_db
from gpt import ANNIVERSARY, CURIO, EXPLAINER, TOPIC_BRIEFS, extract_json_object, write_creative
from logstream import log
from models import Topic
from notify import TELEGRAM_MAX_CAPTION, send_telegram, send_telegram_photo
from repository import (
    add_topic,
    as_utc,
    count_longform_since,
    list_published_videos,
    upsert_metrics,
    count_topics_used_today,
    get_job,
    has_active_jobs,
    last_topic_used_at,
    daily_movement,
    get_research_sources,
    get_script,
    record_stats,
    top_performing_subjects,
    worst_performing_subjects,
    list_artifacts,
    mark_topic_finished,
    next_planned_topic,
    normalize_subject,
    queue_topic_job,
    recent_topic_subjects,
    topics_awaiting_result,
)
import anniversary
import research
from posts import generate_post
from thumbnail import pick_still
from utils import ENV_FILE, OUTPUT_DIR, PROJECT_ROOT, TEMP_DIR
from video import probe_duration
from youtube import fetch_statistics

TICK_SECONDS = 60
CLEANUP_INTERVAL_SECONDS = 3600
TOPIC_ATTEMPTS = 3
TOPIC_FAILURE_NOTIFY_AFTER = 10
RECENT_TOPICS_LIMIT = 50
TOPIC_MIN_WORDS = 4
TOPIC_MAX_WORDS = 12
ERROR_TEXT_LIMIT = 500
STALL_WARNING_SECONDS = 3 * 3600
OUTPUT_RETENTION_PATTERNS = ("*.mp4", "*.jpg")
# Daily is plenty: the numbers move slowly, the API runs about three days
# behind anyway, and its quota is shared with uploads.
METRICS_REFRESH_INTERVAL_SECONDS = 24 * 3600
# How far back to ask. Comfortably longer than the channel's history, and the
# window costs nothing extra — one query covers every video in it.
METRICS_LOOKBACK_DAYS = 90
# A still lifts a community post's reach, and the post is meant to be pasted
# into Studio, so the caption is the post text and nothing else — a prefix
# would be copied along with it.
POST_STILL_NAME = "post_still.jpg"

# Counts are read once a day, in the morning, so each reading covers a whole
# finished day rather than a partial one. The Data API is current, unlike
# Analytics — that is the whole reason this exists alongside it.
STATS_HOUR = 9
# Retention comes from the Analytics API, which the documentation puts 48 to 72
# hours behind. A daily digest of it would mostly repeat itself; a week is the
# shortest interval over which the numbers actually move.
DIGEST_WEEKDAY = 0  # Monday
DIGEST_HOUR = 10
# Nothing younger than this has settled data to rank on. See §7.5 of the plan:
# the threshold is arithmetic, not caution.
DIGEST_MIN_AGE_DAYS = 7


def build_payload(
    config: AutopilotConfig,
    subject: str,
    format_name: str = "short",
    register: str = EXPLAINER,
) -> dict:
    """Same shape as Frontend/app.js sends, plus upload flag and thread count."""
    return {
        "videoSubject": subject,
        "format": format_name,
        "register": register,
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


def build_topic_prompt(
    niche: str,
    recent: list[str],
    register: str = EXPLAINER,
    material: str = "",
) -> str:
    # Substitution happens after dedent/strip (via .format, not an f-string): recent
    # subjects are unindented, and interpolating them before dedent would drag the
    # whole block's common-indent calculation down to zero, leaving every other line
    # still indented.
    recent_block = "\n".join(f"- {subject}" for subject in recent) or "- (none yet)"
    template = textwrap.dedent(
        """
        You pick topics for short vertical YouTube videos (YouTube Shorts).

        Channel niche: {niche}

        {material}Propose ONE new video topic. Requirements:
        - Written in English.
        - Between {min_words} and {max_words} words.
        - {brief}
        - Must not repeat or rephrase any of the topics already used below.

        Topics already used (do not repeat, do not paraphrase):
        {recent_block}

        Return ONLY a JSON object: {{"subject": "..."}}
        """
    ).strip()
    material_block = (
        "Real material found by searching. Pick one of these and turn it into a\n"
        "        topic; do not invent something else, and do not use anything the\n"
        f"        notes do not support.\n\n        {material}\n\n        "
        if material.strip()
        else ""
    )
    return template.format(
        niche=niche,
        min_words=TOPIC_MIN_WORDS,
        max_words=TOPIC_MAX_WORDS,
        recent_block=recent_block,
        brief=TOPIC_BRIEFS[register],
        material=material_block,
    )


class Autopilot:
    def __init__(
        self,
        config: AutopilotConfig,
        session_factory: Callable[[], Session],
        notify: Callable[[str], bool] = send_telegram,
        generate: Callable[[str, str], str] = write_creative,
        output_dir: Path = OUTPUT_DIR,
        fetch_metrics: Callable = fetch_metrics,
        generate_post: Callable = generate_post,
        send_photo: Callable = send_telegram_photo,
    ) -> None:
        self.config = config
        self.session_factory = session_factory
        self.notify = notify
        self.generate = generate
        self.output_dir = Path(output_dir)
        self.fetch_metrics = fetch_metrics
        self.generate_post = generate_post
        self.send_photo = send_photo
        self.failed_topic_ticks = 0
        self.topic_failure_notified = False
        self.last_cleanup_at: Optional[datetime] = None
        self.last_metrics_at: Optional[datetime] = None
        self.last_stats_day = None
        self.last_digest_week = None
        self.stall_notified: set[int] = set()

    # -- messages ------------------------------------------------------------

    def start_message(self) -> str:
        return (
            f"Autopilot started. Niche: {self.config.niche}. "
            f"{self.config.videos_per_day}/day, window {self.config.window_label} "
            f"{self.config.tz_name}. Long form: {self.config.longform_per_week}/week."
        )

    # -- tick ----------------------------------------------------------------

    def run_tick(self, now: datetime) -> None:
        for step in (
            lambda: self.finish_completed_topics(),
            lambda: self.warn_stalled_topics(now),
            lambda: self.maybe_create_job(now),
            lambda: self.refresh_metrics(now),
            lambda: self.snapshot_stats(now),
            lambda: self.weekly_digest(now),
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
                    self.notify(message)
                    finished += 1
                    # After the result, never instead of it: a post that cannot
                    # be written must not cost the notification that the video
                    # is live.
                    try:
                        self.send_post_draft(session, topic, job)
                    except Exception as err:
                        log(f"[-] Could not draft a post: {err}", "warning")
                    continue
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
        warning = ""
        for artifact in list_artifacts(session, job_id):
            metadata = artifact.metadata_json or {}
            if artifact.artifact_type == "video":
                title = metadata.get("title") or title
                if metadata.get("uploadError"):
                    second_line = f"upload skipped: {metadata['uploadError']}"
                if metadata.get("narrationFellBack"):
                    # Almost always exhausted ElevenLabs credits. Worth saying
                    # out loud: the video is fine but sounds like a different
                    # channel, and every one after it will too until topped up.
                    warning = (
                        f"\n⚠️ narrated with {metadata.get('narration')} — "
                        "the paid voice was unavailable"
                    )
            elif artifact.artifact_type == "youtube_video":
                second_line = artifact.path
        return f"✅ {title}\n{second_line}{warning}\njob {job_id}"

    def send_post_draft(self, session: Session, topic: Topic, job) -> bool:
        """Sends a ready-to-paste community post, with a still from the video.

        The caption is the post text alone. Telegram copies a caption whole, so
        anything added around it — a heading, the video link — would be pasted
        into Studio too.
        """
        script = get_script(session, job.id) or ""
        notes = "\n\n".join(
            f"[{index}] {row.title or row.url}\n{row.snippet}"
            for index, row in enumerate(get_research_sources(session, job.id), 1)
        )
        title = topic.subject
        video_path = None
        for artifact in list_artifacts(session, job.id):
            if artifact.artifact_type == "video":
                video_path = PROJECT_ROOT / artifact.path
                title = (artifact.metadata_json or {}).get("title") or title

        post = self.generate_post(
            topic.subject, title, script, self.config.model, research=notes
        )
        if post is None:
            log("[!] No usable post text for this video.", "warning")
            return False

        text = post.render()
        still = self._still_for(job.id, video_path)
        sent = False
        if still and len(text) <= TELEGRAM_MAX_CAPTION:
            sent = self.send_photo(still, text)
        elif still:
            # Too long to ride along with the picture; both are still wanted.
            self.send_photo(still, "")
            sent = self.notify(text)
        else:
            sent = self.notify(text)
        log(f"[+] Drafted a {post.kind} post for job {job.id}", "success")
        return bool(sent)

    def _still_for(self, job_id: str, video_path: Optional[Path]) -> Optional[str]:
        """A frame from the finished video, or None if one cannot be read."""
        if not video_path or not Path(video_path).exists():
            return None
        try:
            return pick_still(
                str(video_path),
                str(TEMP_DIR / f"{job_id}-{POST_STILL_NAME}"),
                duration=probe_duration(str(video_path)),
                work_dir=TEMP_DIR / f"{job_id}-still",
                ffmpeg=os.getenv("FFMPEG_BINARY", "").strip() or "ffmpeg",
            )
        except Exception as err:
            log(f"[!] Could not take a still ({err}).", "warning")
            return None

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

            register = choose_register(self.config)
            topic = next_planned_topic(session) or self.generate_topic(
                session, register
            )
            if topic is None:
                return None

            longform_this_week = count_longform_since(
                session, week_start(now, self.config.tz)
            )
            format_name = next_format(longform_this_week, self.config)

            job = queue_topic_job(
                session,
                topic,
                build_payload(self.config, topic.subject, format_name, register),
                now=now,
            )
            log(
                f"[+] Autopilot queued {format_name} {register} job {job.id} "
                f"for topic '{topic.subject}'",
                "success",
            )
            return job.id

    def generate_topic(
        self, session: Session, register: str = EXPLAINER
    ) -> Optional[Topic]:
        recent = recent_topic_subjects(session, RECENT_TOPICS_LIMIT)
        # A curio is found, not invented. Asked to make one up, the model
        # produces either an overstatement it will later defend by fabricating,
        # or a fact it half-remembers.
        material = ""
        if register == CURIO and research.is_configured():
            seed = research.curio_seed()
            found = research.gather([seed], limit=research.DEFAULT_RESULT_COUNT)
            material = research.format_brief(found)
            log(
                f"[+] Curio seed '{seed}': {len(found)} source(s).",
                "info" if found else "warning",
            )
        elif register == ANNIVERSARY:
            events = anniversary.fetch_events()
            material = anniversary.format_events(events)
            log(f"[+] {len(events)} usable events for today.", "info" if events else "warning")
            if not events:
                # Most days are wars and disasters once the grim ones are cut.
                # An explainer beats an anniversary forced out of nothing.
                register = EXPLAINER

        prompt = build_topic_prompt(self.config.niche, recent, register, material)
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

    def refresh_metrics(self, now: datetime) -> int:
        """Pulls settled performance numbers for everything published.

        Returns the number of videos that had processed data. Videos the API
        has not finished processing are simply not written: absent is not zero,
        and storing a zero for a two-day-old video would make every fresh
        upload rank as a failure.

        Like every autopilot step this must not raise; run_tick isolates it,
        and metrics are a nicety while making videos is the job.
        """
        if self.last_metrics_at is not None and (
            now - self.last_metrics_at
        ) < timedelta(seconds=METRICS_REFRESH_INTERVAL_SECONDS):
            return 0
        self.last_metrics_at = now

        with self.session_factory() as session:
            published = list_published_videos(session)
        if not published:
            return 0

        since = (now - timedelta(days=METRICS_LOOKBACK_DAYS)).date()
        # One request for every video, not one per video: the query is cheap
        # next to videos.insert but it is not free, and the quota is shared.
        measured = self.fetch_metrics([row[0] for row in published], since)
        if not measured:
            log(
                f"[*] No settled analytics yet for any of {len(published)} "
                f"published video(s).",
                "info",
            )
            return 0

        with self.session_factory() as session:
            for video_id, job_id, format_name, published_at in published:
                metrics = measured.get(video_id)
                if metrics is None:
                    continue
                upsert_metrics(
                    session, metrics, job_id, format_name, published_at, commit=False
                )
            session.commit()
        log(f"[+] Refreshed metrics for {len(measured)} video(s).", "success")
        return len(measured)

    def snapshot_stats(self, now: datetime) -> int:
        """Records each video's current counts, once per morning.

        These come from the Data API, which is current. The Analytics numbers
        that drive ranking run two to three days behind by design, so without
        this there is no way to see what a video published today is doing.
        """
        local = now.astimezone(self.config.tz)
        if local.hour < STATS_HOUR or self.last_stats_day == local.date():
            return 0

        with self.session_factory() as session:
            published = list_published_videos(session)
        if not published:
            self.last_stats_day = local.date()
            return 0

        counts = fetch_statistics([row[0] for row in published])
        if not counts:
            log("[!] No statistics came back; will try again tomorrow.", "warning")
            self.last_stats_day = local.date()
            return 0

        with self.session_factory() as session:
            record_stats(session, local.date(), counts, commit=False)
            movement = daily_movement(
                session, local.date(), local.date() - timedelta(days=1)
            )
            session.commit()
        self.last_stats_day = local.date()

        self.notify(self._movement_message(movement))
        log(f"[+] Recorded counts for {len(counts)} video(s).", "success")
        return len(counts)

    def _movement_message(self, movement: list) -> str:
        """Yesterday's numbers, busiest first."""
        total = sum(views for _, views, _ in movement)
        gained = sum(gain for _, _, gain in movement if gain is not None)
        lines = [f"📊 {gained:+} views yesterday, {total} on the channel"]
        for video_id, views, gain in movement[:5]:
            change = "new" if gain is None else f"{gain:+}"
            lines.append(f"  {change:>6}  {views:>6} total  youtu.be/{video_id}")
        return "\n".join(lines)

    def weekly_digest(self, now: datetime) -> bool:
        """Once a week, what the settled retention numbers say."""
        local = now.astimezone(self.config.tz)
        week = (local.isocalendar().year, local.isocalendar().week)
        if (
            local.weekday() != DIGEST_WEEKDAY
            or local.hour < DIGEST_HOUR
            or self.last_digest_week == week
        ):
            return False
        self.last_digest_week = week

        with self.session_factory() as session:
            lines = ["🗓 Weekly retention"]
            for format_name in ("short", "long"):
                best = top_performing_subjects(
                    session, format_name, 3, DIGEST_MIN_AGE_DAYS
                )
                worst = worst_performing_subjects(
                    session, format_name, 2, DIGEST_MIN_AGE_DAYS
                )
                if not best:
                    lines.append(
                        f"\n{format_name}: nothing older than "
                        f"{DIGEST_MIN_AGE_DAYS} days has settled data yet."
                    )
                    continue
                lines.append(f"\n{format_name} — held attention:")
                lines += [f"  + {subject}" for subject in best]
                if worst:
                    lines.append(f"{format_name} — lost them early:")
                    lines += [f"  - {subject}" for subject in worst]
        self.notify("\n".join(lines))
        return True

    # -- step 4 --------------------------------------------------------------

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
        # Thumbnails are archived beside their video, so they age out with it.
        for pattern in OUTPUT_RETENTION_PATTERNS:
            for path in self.output_dir.glob(pattern):
                try:
                    if path.is_file() and path.stat().st_mtime < cutoff:
                        path.unlink()
                        deleted += 1
                except OSError as err:
                    log(f"[-] Could not delete {path}: {err}", "warning")
        if deleted:
            log(f"[+] Autopilot deleted {deleted} old file(s) from {self.output_dir}", "info")
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
