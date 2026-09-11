import os
import random
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone, tzinfo
from typing import Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigError(ValueError):
    """Invalid autopilot configuration. The process should exit with code 1."""


def _parse_bool(name: str, value: str, default: bool) -> bool:
    cleaned = value.strip().lower()
    if not cleaned:
        return default
    if cleaned in ("1", "true", "yes", "on"):
        return True
    if cleaned in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name}: expected a boolean, got '{value}'.")


def _parse_int(name: str, value: str, default: int, minimum: int, maximum: int) -> int:
    cleaned = value.strip()
    if not cleaned:
        return default
    try:
        number = int(cleaned)
    except ValueError as err:
        raise ConfigError(f"{name} must be an integer, got '{value}'.") from err
    if number < minimum or number > maximum:
        raise ConfigError(f"{name} must be between {minimum} and {maximum}, got {number}.")
    return number


def _parse_clock(value: str) -> time:
    parts = value.strip().split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ConfigError(f"AUTOPILOT_WINDOW: '{value}' is not HH:MM.")
    hour, minute = int(parts[0]), int(parts[1])
    if hour > 23 or minute > 59:
        raise ConfigError(f"AUTOPILOT_WINDOW: '{value}' is not a valid time.")
    return time(hour, minute)


def parse_window(raw: str) -> tuple[time, time]:
    pieces = raw.split("-")
    if len(pieces) != 2:
        raise ConfigError(f"AUTOPILOT_WINDOW must look like HH:MM-HH:MM, got '{raw}'.")
    start, end = _parse_clock(pieces[0]), _parse_clock(pieces[1])
    if start >= end:
        raise ConfigError(
            f"AUTOPILOT_WINDOW start must be before end within one day, got '{raw}'."
        )
    return start, end


@dataclass(frozen=True)
class AutopilotConfig:
    enabled: bool
    niche: str
    videos_per_day: int
    window_start: time
    window_end: time
    model: str
    voice: str
    paragraphs: int
    subtitles_position: str
    color: str
    use_music: bool
    custom_prompt: str
    longform_per_week: int
    curio_share: int
    anniversary_share: int
    output_retention_days: int
    telegram_bot_token: str
    telegram_chat_id: str
    tz_name: str

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)

    @property
    def window_label(self) -> str:
        return f"{self.window_start:%H:%M}-{self.window_end:%H:%M}"

    @property
    def window_seconds(self) -> int:
        start = self.window_start.hour * 3600 + self.window_start.minute * 60
        end = self.window_end.hour * 3600 + self.window_end.minute * 60
        return end - start

    @property
    def min_gap_seconds(self) -> int:
        return self.window_seconds // self.videos_per_day

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "AutopilotConfig":
        source = os.environ if env is None else env

        def get(name: str, default: str = "") -> str:
            return source.get(name, default)

        enabled = _parse_bool("AUTOPILOT_ENABLED", get("AUTOPILOT_ENABLED"), default=True)
        niche = get("AUTOPILOT_NICHE").strip()
        if enabled and not niche:
            raise ConfigError(
                "AUTOPILOT_NICHE is required when AUTOPILOT_ENABLED=true "
                "(example: AUTOPILOT_NICHE=\"space and astronomy facts\")."
            )

        tz_name = get("TZ").strip() or "UTC"
        try:
            ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError) as err:
            raise ConfigError(f"TZ '{tz_name}' is not a known timezone.") from err

        window_start, window_end = parse_window(get("AUTOPILOT_WINDOW") or "09:00-21:00")
        ollama_model = get("OLLAMA_MODEL").strip() or "llama3.1:8b"

        return cls(
            enabled=enabled,
            niche=niche,
            videos_per_day=_parse_int("AUTOPILOT_VIDEOS_PER_DAY", get("AUTOPILOT_VIDEOS_PER_DAY"), 2, 1, 6),
            window_start=window_start,
            window_end=window_end,
            model=get("AUTOPILOT_MODEL").strip() or ollama_model,
            voice=get("AUTOPILOT_VOICE").strip() or "en_us_001",
            paragraphs=_parse_int("AUTOPILOT_PARAGRAPHS", get("AUTOPILOT_PARAGRAPHS"), 1, 1, 10),
            subtitles_position=get("AUTOPILOT_SUBTITLES_POSITION").strip() or "center,center",
            color=get("AUTOPILOT_COLOR").strip() or "#FFFF00",
            use_music=_parse_bool("AUTOPILOT_USE_MUSIC", get("AUTOPILOT_USE_MUSIC"), default=False),
            custom_prompt=get("AUTOPILOT_CUSTOM_PROMPT"),
            longform_per_week=_parse_int(
                "AUTOPILOT_LONGFORM_PER_WEEK", get("AUTOPILOT_LONGFORM_PER_WEEK"), 0, 0, 7
            ),
            curio_share=_parse_int(
                "AUTOPILOT_CURIO_SHARE", get("AUTOPILOT_CURIO_SHARE"), 33, 0, 100
            ),
            anniversary_share=_parse_int(
                "AUTOPILOT_ANNIVERSARY_SHARE",
                get("AUTOPILOT_ANNIVERSARY_SHARE"),
                20,
                0,
                100,
            ),
            output_retention_days=_parse_int("OUTPUT_RETENTION_DAYS", get("OUTPUT_RETENTION_DAYS"), 7, 1, 365),
            telegram_bot_token=get("TELEGRAM_BOT_TOKEN").strip(),
            telegram_chat_id=get("TELEGRAM_CHAT_ID").strip(),
            tz_name=tz_name,
        )


def slot_available(
    now: datetime,
    config: AutopilotConfig,
    used_today: int,
    last_used_at: Optional[datetime],
) -> bool:
    """Pure scheduling rule. `now` and `last_used_at` must be timezone-aware."""
    if not config.enabled:
        return False

    local_now = now.astimezone(config.tz)
    if not (config.window_start <= local_now.time() < config.window_end):
        return False

    if used_today >= config.videos_per_day:
        return False

    if last_used_at is None:
        return True

    window_start_today = local_now.replace(
        hour=config.window_start.hour,
        minute=config.window_start.minute,
        second=0,
        microsecond=0,
    )
    last_local = last_used_at.astimezone(config.tz)
    if last_local < window_start_today:
        return True

    return (local_now - last_local) >= timedelta(seconds=config.min_gap_seconds)


def week_start(now: datetime, tz: tzinfo) -> datetime:
    """Local Monday 00:00 for the week containing `now`, as UTC.

    Weekly rather than rolling: a fixed boundary is what someone reading the
    channel's output actually perceives, and it is far easier to reason about
    when a budget looks wrong.
    """
    local_now = now.astimezone(tz)
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight - timedelta(days=local_now.weekday())).astimezone(timezone.utc)


def next_format(longform_this_week: int, config: AutopilotConfig) -> str:
    """Which format the next video should be.

    Long form wins whenever its weekly budget has room: it is the scarcer and
    more valuable slot, and leaving it until the end of the week risks losing
    it to a day the machine happens to be off.

    Deliberately does not take the daily count. slot_available already decides
    whether anything runs at all, and duplicating that gate here would let the
    two disagree.
    """
    if config.longform_per_week <= 0:
        return "short"
    return "long" if longform_this_week < config.longform_per_week else "short"


def choose_register(config: AutopilotConfig, roll: Optional[int] = None) -> str:
    """Whether the next video explains something or reports something absurd.

    A share rather than an alternation: a channel that reliably alternates is
    as templated as one that never varies, and the monetization policy is about
    exactly that.
    """
    from gpt import ANNIVERSARY, CURIO, EXPLAINER

    drawn = random.randint(1, 100) if roll is None else roll
    if drawn <= config.curio_share:
        return CURIO
    if drawn <= config.curio_share + config.anniversary_share:
        return ANNIVERSARY
    return EXPLAINER
