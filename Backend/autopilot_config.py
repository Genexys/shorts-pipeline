import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigError(ValueError):
    """Invalid autopilot configuration. The process should exit with code 1."""


def _parse_bool(value: str, default: bool) -> bool:
    cleaned = value.strip().lower()
    if not cleaned:
        return default
    if cleaned in ("1", "true", "yes", "on"):
        return True
    if cleaned in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"Expected a boolean, got '{value}'.")


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

        enabled = _parse_bool(get("AUTOPILOT_ENABLED"), default=True)
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
            use_music=_parse_bool(get("AUTOPILOT_USE_MUSIC"), default=False),
            custom_prompt=get("AUTOPILOT_CUSTOM_PROMPT"),
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
