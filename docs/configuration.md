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
| `OLLAMA_TIMEOUT` | Seconds to wait for one Ollama response. | `180` |
| `ASSEMBLY_AI_API_KEY` | If set, subtitles are generated with AssemblyAI; otherwise local subtitle generation is used. | empty |
| `POSTGRES_DB` | Database name for Docker Postgres service. | `moneyprinter` |
| `POSTGRES_USER` | Database user for Docker Postgres service. | `moneyprinter` |
| `POSTGRES_PASSWORD` | Database password for Docker Postgres service. | `moneyprinter` |
| `DATABASE_URL` | SQLAlchemy DSN used by API and worker (`postgresql+psycopg://...` or `sqlite:///...`). | `sqlite:///moneyprinter.db` |

## API

| Variable | Description | Default |
|---|---|---|
| `CORS_ORIGINS` | Comma-separated browser origins allowed to call the API. | local UI on ports 8001 and 3000 |

## YouTube upload

| Variable | Description | Default |
|---|---|---|
| `YOUTUBE_PRIVACY_STATUS` | Privacy of uploaded videos: `private`, `unlisted` or `public`. Invalid values fall back to `private` with a warning. | `private` |
| `YOUTUBE_CATEGORY_ID` | YouTube category id for uploads (`28` = Science & Technology). | `28` |
| `YOUTUBE_CLIENT_SECRETS_FILE` | Path to the OAuth client JSON. | `Backend/client_secret.json` |
| `YOUTUBE_TOKEN_FILE` | Path to the saved OAuth token. | `Backend/youtube_token.json` |

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
| `AUTOPILOT_PARAGRAPHS` | Paragraphs in the script, 1..10. | `1` |
| `AUTOPILOT_SUBTITLES_POSITION` | Same values as the UI. | `center,center` |
| `AUTOPILOT_COLOR` | Subtitle colour. | `#FFFF00` |
| `AUTOPILOT_USE_MUSIC` | Mix a background music bed from `Songs/`. See [Background music](#background-music). | `false` |
| `AUTOPILOT_CUSTOM_PROMPT` | Custom script prompt. | empty |
| `OUTPUT_RETENTION_DAYS` | Days to keep `output/*.mp4`, 1..365. | `7` |
| `TELEGRAM_BOT_TOKEN` | Bot token; empty logs notifications instead of sending. | empty |
| `TELEGRAM_CHAT_ID` | Chat that receives notifications. | empty |
| `TZ` | Timezone for the window and the daily counter. | `UTC` |

## Background music

Videos always carry the TTS voiceover; the music bed is optional and off by
default. Turn it on per job with the **Use music** toggle in the UI, or for
autopilot with `AUTOPILOT_USE_MUSIC=true`. With no tracks available the job
logs a warning and continues with voice only — it never fails for this.

### Where tracks live

`Songs/` is a bind mount, so dropping MP3s into it on the host is enough; no
restart and no upload through the UI are needed.

Two layouts are supported:

```
Songs/track.mp3            # flat: any track may be picked
Songs/calm/track.mp3       # mood folders, preferred when they hold tracks
Songs/curious/track.mp3
Songs/tense/track.mp3
Songs/upbeat/track.mp3
```

With mood folders present, Ollama picks one of `calm`, `curious`, `tense` or
`upbeat` for each script and a random track is taken from that folder. The
choice is best effort: if Ollama is unreachable or answers with something
unknown, the pipeline falls back to the flat `Songs/` folder. A flat library
keeps working unchanged.

Licensing is your responsibility. Prefer the **YouTube Audio Library** — it is
free, mostly attribution-free, and being YouTube's own library it does not
trigger Content ID claims on YouTube.

### How the mix is built

One ffmpeg pass (`Backend/video.py:mix_background_music`), never re-encoding the
picture:

1. The bed is attenuated to `MUSIC_VOLUME` (0.25) and looped to cover the video.
2. Fades in over 1.5 s and out over the last 2 s.
3. `sidechaincompress` ducks the music under the voice, keyed off a split copy
   of the voice track, so the bed drops during narration and returns in gaps.
4. `loudnorm` normalizes the finished mix to -14 LUFS, matching the level
   YouTube normalizes playback to, so loudness is stable across videos.

The video stream is copied (`-c:v copy`), so adding music costs seconds rather
than a full re-render. If the mix fails for any reason the already-rendered
voice-only video is kept and the job still completes.

## Notes

- Ollama models shown in the frontend are fetched from backend endpoint `/api/models`, which queries `OLLAMA_BASE_URL/api/tags`.
- Pull models before use, for example:

```bash
ollama pull llama3.1:8b
```

- If ImageMagick is not discovered automatically, set `IMAGEMAGICK_BINARY` explicitly.
- New architecture uses a database-backed job queue. In Docker, use Postgres via `DATABASE_URL`.
- Under Docker Compose, `OLLAMA_BASE_URL`, `DATABASE_URL`, `IMAGEMAGICK_BINARY`, `YOUTUBE_CLIENT_SECRETS_FILE` and `YOUTUBE_TOKEN_FILE` are set by `docker-compose.yml`.
- Set `FLASK_DEBUG=1` to enable the Werkzeug debugger and reloader locally; never set it in Docker.
