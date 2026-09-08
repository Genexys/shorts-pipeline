from datetime import datetime, timezone
from autopilot_config import next_format, week_start
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from autopilot_config import AutopilotConfig, ConfigError, parse_window, slot_available


BASE_ENV = {"AUTOPILOT_NICHE": "space facts"}


def test_from_env_defaults():
    config = AutopilotConfig.from_env(BASE_ENV)

    assert config.enabled is True
    assert config.niche == "space facts"
    assert config.videos_per_day == 2
    assert config.window_start == time(9, 0)
    assert config.window_end == time(21, 0)
    assert config.model == "llama3.1:8b"
    assert config.voice == "en_us_001"
    assert config.paragraphs == 1
    assert config.subtitles_position == "center,center"
    assert config.color == "#FFFF00"
    assert config.use_music is False
    assert config.custom_prompt == ""
    assert config.output_retention_days == 7
    assert config.telegram_bot_token == ""
    assert config.telegram_chat_id == ""
    assert config.tz_name == "UTC"
    assert config.window_label == "09:00-21:00"
    assert config.window_seconds == 12 * 3600
    assert config.min_gap_seconds == 6 * 3600


def test_from_env_reads_overrides():
    config = AutopilotConfig.from_env(
        {
            **BASE_ENV,
            "AUTOPILOT_ENABLED": "False",
            "AUTOPILOT_VIDEOS_PER_DAY": "4",
            "AUTOPILOT_WINDOW": "08:30-12:30",
            "OLLAMA_MODEL": "llama3.2:3b",
            "AUTOPILOT_MODEL": "qwen3:8b",
            "AUTOPILOT_VOICE": "en_uk_001",
            "AUTOPILOT_PARAGRAPHS": "2",
            "AUTOPILOT_USE_MUSIC": "yes",
            "AUTOPILOT_CUSTOM_PROMPT": "be funny",
            "OUTPUT_RETENTION_DAYS": "3",
            "TELEGRAM_BOT_TOKEN": "t",
            "TELEGRAM_CHAT_ID": "c",
            "TZ": "Europe/Berlin",
        }
    )

    assert config.enabled is False
    assert config.videos_per_day == 4
    assert config.window_label == "08:30-12:30"
    assert config.min_gap_seconds == 3600
    assert config.model == "qwen3:8b"
    assert config.voice == "en_uk_001"
    assert config.paragraphs == 2
    assert config.use_music is True
    assert config.custom_prompt == "be funny"
    assert config.output_retention_days == 3
    assert config.tz == ZoneInfo("Europe/Berlin")


def test_model_falls_back_to_ollama_model():
    config = AutopilotConfig.from_env({**BASE_ENV, "OLLAMA_MODEL": "llama3.2:3b"})
    assert config.model == "llama3.2:3b"


def test_niche_required_when_enabled():
    with pytest.raises(ConfigError, match="AUTOPILOT_NICHE"):
        AutopilotConfig.from_env({})

    disabled = AutopilotConfig.from_env({"AUTOPILOT_ENABLED": "false"})
    assert disabled.niche == ""


@pytest.mark.parametrize("value", ["0", "7", "abc"])
def test_videos_per_day_must_be_between_1_and_6(value):
    with pytest.raises(ConfigError, match="AUTOPILOT_VIDEOS_PER_DAY"):
        AutopilotConfig.from_env({**BASE_ENV, "AUTOPILOT_VIDEOS_PER_DAY": value})


@pytest.mark.parametrize("value", [1, 6])
def test_videos_per_day_accepts_boundary_values(value):
    config = AutopilotConfig.from_env({**BASE_ENV, "AUTOPILOT_VIDEOS_PER_DAY": str(value)})
    assert config.videos_per_day == value


@pytest.mark.parametrize("value", ["21:00-09:00", "09:00-09:00", "9-21", "09:00", "25:00-26:00"])
def test_window_validation(value):
    with pytest.raises(ConfigError, match="AUTOPILOT_WINDOW"):
        parse_window(value)


def test_parse_window_ok():
    assert parse_window("09:00-21:00") == (time(9, 0), time(21, 0))
    assert parse_window(" 07:15 - 08:45 ") == (time(7, 15), time(8, 45))


def test_parse_window_accepts_full_day_boundary():
    assert parse_window("00:00-23:59") == (time(0, 0), time(23, 59))


def test_invalid_timezone_is_config_error():
    with pytest.raises(ConfigError, match="TZ"):
        AutopilotConfig.from_env({**BASE_ENV, "TZ": "Mars/Olympus"})


def test_invalid_bool_error_names_the_env_var():
    with pytest.raises(ConfigError, match="AUTOPILOT_ENABLED"):
        AutopilotConfig.from_env({**BASE_ENV, "AUTOPILOT_ENABLED": "maybe"})


# --- slot_available -------------------------------------------------------

UTC = ZoneInfo("UTC")
CONFIG = AutopilotConfig.from_env({**BASE_ENV, "AUTOPILOT_VIDEOS_PER_DAY": "2"})
# window 09:00-21:00 UTC, min gap 6h


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 6, hour, minute, tzinfo=UTC)


def test_slot_available_inside_window_first_of_day():
    assert slot_available(_at(9, 0), CONFIG, used_today=0, last_used_at=None) is True


def test_slot_not_available_outside_window():
    assert slot_available(_at(8, 59), CONFIG, 0, None) is False
    assert slot_available(_at(21, 0), CONFIG, 0, None) is False
    assert slot_available(_at(23, 30), CONFIG, 0, None) is False


def test_slot_not_available_when_daily_limit_reached():
    assert slot_available(_at(12, 0), CONFIG, used_today=2, last_used_at=_at(9, 0)) is False


def test_slot_respects_minimum_gap():
    assert slot_available(_at(14, 59), CONFIG, 1, _at(9, 0)) is False
    assert slot_available(_at(15, 0), CONFIG, 1, _at(9, 0)) is True


def test_slot_ignores_last_used_before_today_window():
    yesterday = _at(20, 0) - timedelta(days=1)
    assert slot_available(_at(9, 0), CONFIG, 0, yesterday) is True
    before_window_today = _at(3, 0)
    assert slot_available(_at(9, 0), CONFIG, 0, before_window_today) is True


def test_slot_disabled_config():
    disabled = AutopilotConfig.from_env({**BASE_ENV, "AUTOPILOT_ENABLED": "false"})
    assert slot_available(_at(12, 0), disabled, 0, None) is False


def test_slot_uses_local_timezone():
    berlin = AutopilotConfig.from_env({**BASE_ENV, "TZ": "Europe/Berlin"})
    # 07:30 UTC == 09:30 Berlin (CEST): inside the window
    assert slot_available(datetime(2026, 9, 6, 7, 30, tzinfo=UTC), berlin, 0, None) is True
    # 19:30 UTC == 21:30 Berlin: outside
    assert slot_available(datetime(2026, 9, 6, 19, 30, tzinfo=UTC), berlin, 0, None) is False


# -- weekly long-form budget ------------------------------------------------


def _cfg(**overrides):
    env = {"AUTOPILOT_NICHE": "ocean facts", **overrides}
    return AutopilotConfig.from_env(env)


def test_longform_is_off_by_default():
    # An existing deployment must not start making long videos on upgrade.
    assert _cfg().longform_per_week == 0


def test_longform_budget_is_read_from_the_environment():
    assert _cfg(AUTOPILOT_LONGFORM_PER_WEEK="3").longform_per_week == 3


def test_longform_budget_is_bounded():
    with pytest.raises(ConfigError):
        _cfg(AUTOPILOT_LONGFORM_PER_WEEK="8")
    with pytest.raises(ConfigError):
        _cfg(AUTOPILOT_LONGFORM_PER_WEEK="-1")


def test_next_format_is_short_when_long_form_is_off():
    assert next_format(0, _cfg()) == "short"


def test_next_format_prefers_long_while_the_budget_has_room():
    config = _cfg(AUTOPILOT_LONGFORM_PER_WEEK="2")
    # The scarcer slot goes first: leaving it to the end of the week risks
    # losing it to a day the machine is off.
    assert next_format(0, config) == "long"
    assert next_format(1, config) == "long"


def test_next_format_falls_back_to_short_once_the_budget_is_spent():
    config = _cfg(AUTOPILOT_LONGFORM_PER_WEEK="2")
    assert next_format(2, config) == "short"
    assert next_format(5, config) == "short"


# -- week boundary ----------------------------------------------------------


def test_week_start_is_local_monday_midnight():
    tz = ZoneInfo("Asia/Tbilisi")
    # A Thursday afternoon.
    now = datetime(2026, 9, 10, 15, 30, tzinfo=tz)
    start = week_start(now, tz)
    local = start.astimezone(tz)
    assert (local.year, local.month, local.day) == (2026, 9, 7)
    assert (local.hour, local.minute) == (0, 0)


def test_week_start_on_monday_is_that_morning():
    tz = ZoneInfo("Asia/Tbilisi")
    now = datetime(2026, 9, 7, 9, 5, tzinfo=tz)
    assert week_start(now, tz).astimezone(tz).day == 7


def test_week_start_is_returned_as_utc():
    tz = ZoneInfo("Asia/Tbilisi")
    assert week_start(datetime(2026, 9, 10, 15, 30, tzinfo=tz), tz).tzinfo is timezone.utc
