# Configuration

MoneyPrinter reads configuration from `.env` (project root).

Use `.env.example` as your template.

## Required

| Variable | Description |
|---|---|
| `TIKTOK_SESSION_ID` | TikTok session cookie (`sessionid`) used for TTS voice endpoint calls. |
| `PEXELS_API_KEY` | API key used to fetch stock video clips. |

## Optional

| Variable | Description | Default |
|---|---|---|
| `IMAGEMAGICK_BINARY` | Absolute path to ImageMagick executable. If empty, auto-detected from `PATH`. | auto-detect |
| `OLLAMA_BASE_URL` | Ollama server base URL used for model listing and chat generation. | `http://localhost:11434` |
| `OLLAMA_MODEL` | Fallback model if frontend does not send a model value. | `llama3.1:8b` |
| `ASSEMBLY_AI_API_KEY` | If set, subtitles are generated with AssemblyAI; otherwise local subtitle generation is used. | empty |
| `POSTGRES_DB` | Database name for Docker Postgres service. | `moneyprinter` |
| `POSTGRES_USER` | Database user for Docker Postgres service. | `moneyprinter` |
| `POSTGRES_PASSWORD` | Database password for Docker Postgres service. | `moneyprinter` |
| `DATABASE_URL` | SQLAlchemy DSN used by API and worker (`postgresql+psycopg://...` or `sqlite:///...`). | `sqlite:///moneyprinter.db` |

## YouTube upload

| Variable | Description | Default |
|---|---|---|
| `YOUTUBE_PRIVACY_STATUS` | Privacy of uploaded videos: `private`, `unlisted` or `public`. Invalid values fall back to `private` with a warning. | `private` |
| `YOUTUBE_CATEGORY_ID` | YouTube category id for uploads (`28` = Science & Technology). | `28` |

Files (both git-ignored, both live in `Backend/`):

- `client_secret.json`: OAuth client of type "Desktop app" from Google Cloud Console (YouTube Data API v3 enabled, consent screen published "In production", scope `youtube.upload`).
- `youtube_token.json`: created once by `uv run python Backend/youtube_auth.py` on a machine with a browser. The worker refreshes the access token automatically and never opens a browser.

## Autopilot

See `docs/autopilot.md` for behaviour. All variables are read once at startup.

| Variable | Description | Default |
|---|---|---|
| `AUTOPILOT_ENABLED` | `false` pauses job creation; notifications keep working. | `true` |
| `AUTOPILOT_NICHE` | Channel niche in free text. Required when enabled; the process exits with code 1 without it. | none |
| `AUTOPILOT_VIDEOS_PER_DAY` | Maximum jobs per local day, 1..6. | `2` |
| `AUTOPILOT_WINDOW` | `HH:MM-HH:MM` in `TZ`, start before end, same day. | `09:00-21:00` |
| `AUTOPILOT_MODEL` | Ollama model for topics and scripts. | `OLLAMA_MODEL` |
| `AUTOPILOT_VOICE` | TikTok TTS voice. | `en_us_001` |
| `AUTOPILOT_PARAGRAPHS` | Paragraphs in the script. | `1` |
| `AUTOPILOT_SUBTITLES_POSITION` | Same values as the UI. | `center,center` |
| `AUTOPILOT_COLOR` | Subtitle colour. | `#FFFF00` |
| `AUTOPILOT_USE_MUSIC` | Mix a random MP3 from `Songs/`. | `false` |
| `AUTOPILOT_CUSTOM_PROMPT` | Custom script prompt. | empty |
| `OUTPUT_RETENTION_DAYS` | Days to keep `output/*.mp4`. | `7` |
| `TELEGRAM_BOT_TOKEN` | Bot token; empty logs notifications instead of sending. | empty |
| `TELEGRAM_CHAT_ID` | Chat that receives notifications. | empty |
| `TZ` | Timezone for the window and the daily counter. | `UTC` |

## Notes

- Ollama models shown in the frontend are fetched from backend endpoint `/api/models`, which queries `OLLAMA_BASE_URL/api/tags`.
- Pull models before use, for example:

```bash
ollama pull llama3.1:8b
```

- If ImageMagick is not discovered automatically, set `IMAGEMAGICK_BINARY` explicitly.
- New architecture uses a database-backed job queue. In Docker, use Postgres via `DATABASE_URL`.
