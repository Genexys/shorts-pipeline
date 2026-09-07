# Docker Compose

`docker-compose.yml` runs the whole system on one machine:

| Service | Image | Role |
|---|---|---|
| `postgres` | `postgres:16-alpine` | job queue, events, topics |
| `ollama` | `ollama/ollama` | LLM server (CPU), models in the `ollama_models` volume, `OLLAMA_KEEP_ALIVE=2m` |
| `ollama-init` | `ollama/ollama` | one-shot `ollama pull $OLLAMA_MODEL`, exits when done |
| `api` | built from `Dockerfile` | Flask API on `127.0.0.1:8080` |
| `worker` | same image | executes generation jobs, uploads to YouTube |
| `autopilot` | same image | schedules topics and jobs, Telegram reports, output cleanup |
| `frontend` | same image | static UI on `127.0.0.1:8001` |

`api`, `worker` and `autopilot` start only after Postgres is healthy and `ollama-init` has finished; `worker` and `autopilot` also wait until the API answers `GET /api/topics`, which guarantees the schema exists.

`OLLAMA_KEEP_ALIVE=2m` unloads the model between the script and the metadata calls, which frees RAM during rendering but means the model is read from disk again for the metadata step; raise `OLLAMA_TIMEOUT` if that step times out.

## Prerequisites

- Docker Engine with the compose plugin (`docker compose version` prints v2.x).
- `.env` created from `.env.example` and filled in. The compose file overrides `OLLAMA_BASE_URL`, `DATABASE_URL`, `IMAGEMAGICK_BINARY` and the two `YOUTUBE_*_FILE` paths for the containers, so leave those alone.
- Memory: `llama3.1:8b` needs about 6 GB of RAM for Ollama alone; use `OLLAMA_MODEL=llama3.2:3b` on an 8 GB machine.

## First start

```bash
cp .env.example .env            # edit: TIKTOK_SESSION_ID, PEXELS_API_KEY, AUTOPILOT_NICHE, TELEGRAM_*, TZ, POSTGRES_PASSWORD
mkdir -p secrets output Songs
# copy client_secret.json and youtube_token.json into secrets/ (see docs/deploy.md)
docker compose up -d --build
docker compose logs -f ollama-init   # first model pull, a few minutes
docker compose logs -f autopilot worker
```

The autopilot logs `Autopilot started. Niche: ...` and, inside the configured window, `Autopilot queued job ...`.

## Ports and the UI

Only the loopback interface is published. From your laptop:

```bash
ssh -L 8080:127.0.0.1:8080 -L 8001:127.0.0.1:8001 <user>@<vps>
```

then open `http://localhost:8001`. The page talks to `http://localhost:8080`, which is the tunnelled API. Manual topics:

```bash
curl -X POST http://localhost:8080/api/topics -H "Content-Type: application/json" \
  -d '{"subject": "Why octopuses have three hearts"}'
```

## Host directories and volumes

| Path | Mounted into | Purpose |
|---|---|---|
| `./secrets/` | `worker` | `client_secret.json`, `youtube_token.json`; the refreshed access token is written back here |
| `./output/` | `worker`, `autopilot` | `<job_id>.mp4` archives, pruned after `OUTPUT_RETENTION_DAYS` |
| `./Songs/` | `api`, `worker` | background music uploaded via the UI |
| `postgres_data` | `postgres` | database |
| `ollama_models` | `ollama` | pulled models |

`temp/` and `subtitles/` live inside the worker container and are cleared per job.

## Day-to-day

```bash
docker compose ps                                  # status and health
docker compose logs -f --tail=100 worker           # follow one service
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'  # inspect the database
git pull && docker compose up -d --build           # update
docker compose down                                # stop (volumes are kept)
```

Pause the autopilot without stopping anything else: set `AUTOPILOT_ENABLED=false` in `.env`, then `docker compose up -d autopilot`. Finished jobs are still reported to Telegram.

Change the model: edit `OLLAMA_MODEL` in `.env`, then `docker compose up -d` (ollama-init pulls the new model, the app services restart).

## Local runs on a Mac

Docker Desktop gives the `ollama` container no GPU, and CPU inference inside its VM is far slower than native Ollama on the same machine: measured 0.3 tokens/s for `llama3.2:3b` with every vCPU busy. Jobs then fail with `Failed to connect to Ollama: timed out`. For local runs keep `ollama serve` running on the Mac, pull the model natively (`ollama pull llama3.1:8b`), and point the app containers at it with an override file:

```yaml
# compose.mac.yml (untracked)
services:
  api:
    environment:
      OLLAMA_BASE_URL: http://host.docker.internal:11434
  worker:
    environment:
      OLLAMA_BASE_URL: http://host.docker.internal:11434
  autopilot:
    environment:
      OLLAMA_BASE_URL: http://host.docker.internal:11434
```

```bash
docker compose -f docker-compose.yml -f compose.mac.yml up -d --build
```

The `ollama` and `ollama-init` services still start and pull the model but sit idle. None of this applies to a Linux VPS, where Ollama runs natively inside the container.

## Troubleshooting

- `ollama-init` exits non-zero: no internet access or unknown model name; `docker compose logs ollama-init`.
- `api` never becomes healthy: `docker compose logs api`; usually a missing required variable (`TIKTOK_SESSION_ID`, `PEXELS_API_KEY`).
- `autopilot` restarts in a loop with `configuration error`: `AUTOPILOT_NICHE` is empty or `AUTOPILOT_WINDOW` is invalid.
- Upload skipped with `No valid YouTube credentials`: `secrets/youtube_token.json` is missing or invalid; recreate it on a machine with a browser (docs/deploy.md).
- Files in `output/` are owned by root: containers run as root; `sudo chown -R $USER output` if you need to edit them.
- `worker` or `autopilot` restart a few times right after a host reboot: `restart: always` ignores `depends_on`, so they can start before Postgres answers; they settle within a minute.
