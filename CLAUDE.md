# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MoneyPrinter automates YouTube Shorts creation from text topics. It uses Ollama for script generation, TikTok TTS for voiceover, Pexels for stock footage, and moviepy/ImageMagick for video composition. Output: a 9:16 vertical video (`output.mp4`).

## Commands

### Setup
```bash
cp .env.example .env        # then fill in API keys
uv sync                     # install dependencies
ollama serve                # start Ollama (separate terminal)
ollama pull llama3.1:8b     # pull default model
```

### Run (local)
```bash
uv run python Backend/main.py                              # API on :8080
uv run python Backend/worker.py                            # queue worker
uv run python Backend/autopilot.py                         # scheduled topic → job creator
python3 -m http.server 3000 --directory Frontend           # frontend on :3000
```

### Run (Docker)
```bash
mkdir -p secrets output Songs      # secrets/: client_secret.json + youtube_token.json
docker compose up -d --build       # postgres, ollama (+model pull), api :8080, worker, autopilot, frontend :8001
docker compose logs -f autopilot worker
```
Ports bind to 127.0.0.1 only; on a server use `ssh -L 8080:127.0.0.1:8080 -L 8001:127.0.0.1:8001`. See `docs/docker.md` and `docs/deploy.md`.

### Verify
```bash
uv run python -m compileall Backend          # syntax check
curl http://localhost:8080/api/models         # API smoke test
```

### Tests
No test suite exists yet. If added, use pytest:
```bash
uv run pytest -q                                           # all tests
uv run pytest tests/test_file.py::test_name -q             # single test
```

## Architecture

### Video Generation Pipeline (end-to-end flow)

```
User input (Frontend) → POST /api/generate → generation_jobs (Postgres queue)
  → worker.py claims queued job
  → gpt.py: generate_script() via Ollama
  → gpt.py: get_search_terms() → JSON keywords
  → search.py: Pexels API → download stock clips to temp/
  → tiktokvoice.py: TTS per sentence → MP3 chunks (threaded)
  → video.py: generate_subtitles() → .srt (AssemblyAI or local timestamps)
  → video.py: combine_videos() → concatenate/crop to 9:16
  → video.py: generate_video() → burn subtitles via ImageMagick, merge audio
  → (optional) mix background music from Songs/ at 10% volume
  → copy to output.mp4 and output/<job_id>.mp4
  → (optional) youtube.py: upload via saved token; failure is non-fatal (upload_error)
  → PipelineResult → worker stores Artifact rows (video, youtube_video)
```

### Frontend ↔ Backend Communication
- **REST**: JSON payloads to Flask endpoints (`/api/generate`, `/api/jobs/:id`, `/api/jobs/:id/events`, `/api/jobs/:id/cancel`, `/api/models`, `/api/upload-songs`, `/api/topics`)
- **Polling**: frontend polls job status and persisted generation events.

### Key Backend Modules
| File | Responsibility |
|------|---------------|
| `main.py` | Flask app and queue/job endpoints |
| `worker.py` | Job consumer that executes generation pipeline |
| `db.py`/`models.py`/`repository.py` | DB engine, schema, queue/event persistence |
| `gpt.py` | Ollama client: script generation, search terms, YouTube metadata |
| `video.py` | Video processing: combine clips, burn subtitles, merge audio |
| `search.py` | Pexels stock video search and download |
| `tiktokvoice.py` | TikTok TTS API (60+ voices, 300-char chunking, threaded) |
| `youtube.py` | YouTube upload via google-auth-oauthlib token file (Backend/youtube_token.json); youtube_auth.py creates it once |
| `utils.py` | Path constants, env validation, ImageMagick detection |
| `pipeline.py` | Reusable generation pipeline used by worker |
| `autopilot.py` | Scheduled loop: topic generation, job queuing, Telegram reports, output cleanup |
| `autopilot_config.py` | `AutopilotConfig.from_env()` and pure `slot_available` rule |
| `notify.py` | Telegram `send_telegram`, never raises |

### Frontend
- `index.html`: UI with inline CSS, form fields, live log viewer
- `app.js`: API client (`apiRequest()`), job polling UI, localStorage persistence

### Runtime Directories
- `temp/`: intermediate video/audio files (cleared each generation)
- `subtitles/`: generated .srt files (cleared each generation)
- `Songs/`: user-uploaded background music MP3s
- `fonts/`: subtitle font (`bold_font.ttf`)
- `output/`: archived videos `<job_id>.mp4`

## Required Environment Variables

- `TIKTOK_SESSION_ID` — TikTok cookie for TTS
- `PEXELS_API_KEY` — stock video API
- `IMAGEMAGICK_BINARY` — leave empty to auto-detect from PATH

Optional: `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `ASSEMBLY_AI_API_KEY`, `DATABASE_URL`, `YOUTUBE_PRIVACY_STATUS`, `YOUTUBE_CATEGORY_ID`, `YOUTUBE_CLIENT_SECRETS_FILE`, `YOUTUBE_TOKEN_FILE`, `AUTOPILOT_*`, `OUTPUT_RETENTION_DAYS`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TZ` (see `docs/autopilot.md`)

## Conventions

- **Python**: 4-space indent, `snake_case`, type hints on all new/modified signatures, `pathlib.Path` for filesystem ops
- **JS**: `camelCase`, centralized API calls via `apiRequest()`
- **API responses**: `{"status": "success|error", ...}` with proper HTTP codes
- **Long-running work**: database-backed queue and separate worker process
- **Concurrency**: multiple jobs can be queued, but run exactly one `worker.py` process at a time. `recover_running_jobs` requeues (or fails) *every* `running` job at startup, and each job clears `temp/`/`subtitles/` before it runs — both are destructive if a second worker is processing a different job concurrently.
- Update `docs/` when setup, env vars, or runtime behavior changes
