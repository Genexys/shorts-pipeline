# Docker Compose

`docker-compose.yml` runs the whole system on one machine:

| Service | Image | Role |
|---|---|---|
| `postgres` | `postgres:16-alpine` | job queue, events, topics |
| `api` | built from `Dockerfile` | Flask API on `127.0.0.1:8080` |
| `worker` | same image | executes generation jobs, uploads to YouTube |
| `autopilot` | same image | schedules topics and jobs, Telegram reports, output cleanup |
| `frontend` | same image | static UI on `127.0.0.1:8001` |

`api`, `worker` and `autopilot` start only after Postgres is healthy; `worker` and `autopilot` also wait until the API answers `GET /api/topics`, which guarantees the schema exists.

There is no model server in the stack. Every model call goes to the Claude API (see "The writer" in docs/configuration.md); the `ollama` and `ollama-init` services were removed on 2026-10-12. On a host that ran them before, `docker compose ... up -d --remove-orphans` removes the old containers. The `ollama_models` volume holding the pulled weights (several GB) is left alone; `docker volume rm shorts-pipeline_ollama_models` frees it.

## Prerequisites

- Docker Engine with the compose plugin (`docker compose version` prints v2.x).
- `.env` created from `.env.example` and filled in. The compose file overrides `DATABASE_URL`, `IMAGEMAGICK_BINARY` and the two `YOUTUBE_*_FILE` paths for the containers, so leave those alone.
- `ANTHROPIC_API_KEY` in `.env`: the worker refuses to start without it.

## First start

```bash
cp .env.example .env            # edit: ANTHROPIC_API_KEY, TIKTOK_SESSION_ID, PEXELS_API_KEY, AUTOPILOT_NICHE, TELEGRAM_*, TZ, POSTGRES_PASSWORD
mkdir -p secrets output Songs
# copy client_secret.json and youtube_token.json into secrets/ (see docs/deploy.md)
docker compose up -d --build
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
| `./secrets/` | `worker`, `autopilot` (read-only) | `client_secret.json`, `youtube_token.json`; the refreshed access token is written back here |
| `./output/` | `worker`, `autopilot` | `<job_id>.mp4` archives and `<job_id>.jpg` thumbnails, pruned after `OUTPUT_RETENTION_DAYS` |
| `./Songs/` | `api`, `worker` | background music uploaded via the UI |
| `postgres_data` | `postgres` | database |

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

Change the model: edit `SCRIPT_MODEL` (and `SCRIPT_FALLBACK_MODEL`) in `.env`, then `docker compose up -d` so the app services pick it up.

## Local runs on a Mac

The stack runs on a Mac as it is; with no model server it no longer needs the
native-Ollama override this section used to describe. Rendering stays on the
CPU, since Docker Desktop's Linux VM has no path to VideoToolbox.

## Local runs on Windows (NVIDIA GPU)

Windows is the better host for unattended runs: WSL2 exposes the NVIDIA GPU to Linux containers, so video encoding runs on hardware. A Mac cannot do this at all — VideoToolbox is a macOS framework and the containers live in Docker Desktop's Linux VM, which has no path to it.

Prerequisites: the NVIDIA driver installed on Windows (not inside WSL — WSL uses the Windows driver), Docker Desktop on the WSL2 backend, and a working GPU passthrough. Verify passthrough before anything else:

```powershell
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

Then start the stack with the tracked override:

```bash
docker compose -f docker-compose.yml -f compose.win.yml up -d --build
```

`compose.win.yml` reserves the GPU for `worker`, and sets `FFMPEG_BINARY=/usr/bin/ffmpeg` on the worker. That last part is load-bearing: MoviePy defaults to the `imageio-ffmpeg` binary, which is built without nvenc, so without it the GPU reservation is a silent no-op and every encode quietly stays on the CPU.

Verify the encoder actually answers before changing any render settings:

```bash
# 1. the GPU is visible inside the worker
docker compose -f docker-compose.yml -f compose.win.yml exec worker nvidia-smi

# 2. the ffmpeg the worker will use has the nvenc encoders
docker compose -f docker-compose.yml -f compose.win.yml exec worker \
  /usr/bin/ffmpeg -hide_banner -encoders | grep nvenc

# 3. an encode really runs on the GPU (watch Task Manager → GPU → Video Encode)
docker compose -f docker-compose.yml -f compose.win.yml exec worker \
  /usr/bin/ffmpeg -hide_banner -f lavfi -i testsrc=size=1080x1920:rate=30:duration=5 \
  -c:v h264_nvenc -f null -
```

Only after all three pass is it worth switching `Backend/video.py` from `libx264` to `h264_nvenc`. Doing it in the other order makes "nvenc is unavailable" indistinguishable from "the render is broken".

Not every GA107 board has an encoder — NVIDIA fused NVENC off on some RTX 3050 SKUs. The Video Encode graph in Task Manager → Performance → GPU tells you which one you have.

### Empty mounts after a reboot

If Docker Desktop starts the containers before its WSL integration is up — typically right after a Windows Update reboot — the bind-mount paths do not resolve, and Docker mounts freshly created empty directories instead. The containers start and report healthy; they just cannot see `secrets/` or `Songs/`. The pipeline treats a missing token as "upload skipped" and missing songs as "no music", both deliberately, so it goes on building silent videos that never upload.

Set `REQUIRE_MOUNTS=true` in `.env` on a host where the token and the music library are known to exist. `worker` and `autopilot` then check before doing any work, and refuse to start if they cannot see them: one clear log line, one Telegram alert per broken container, and a restart every 30 seconds. If the integration has come back by the next attempt, the container starts normally; if not, recreate it:

```bash
docker compose -f docker-compose.yml -f compose.win.yml up -d --force-recreate worker autopilot
```

### Staying up unattended

On 2026-11-04 the laptop running the stack lost power. It crashed while going into hibernation and stayed off until 11-07. Windows then booted and Docker Desktop started, but the Ubuntu distro holding the project only started at 15:51, when a terminal was opened. Until then the containers could not see their files. Nothing reported any of it. Two measures cover the two halves:

**Start the distro without a terminal.** A scheduled task, `shorts-pipeline-wsl-keepalive`, starts `Ubuntu-22.04` 30 seconds after logon and holds it with a hidden `sleep infinity`. It checks again every 10 minutes and restarts the distro if it has stopped. It runs on battery and has no time limit; Task Scheduler's defaults would block it on battery and stop it after three days. Create it from PowerShell, as the user who runs Docker Desktop:

```powershell
$xml = @"
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><LogonTrigger><Enabled>true</Enabled><UserId>$env:USERDOMAIN\$env:USERNAME</UserId><Delay>PT30S</Delay>
    <Repetition><Interval>PT10M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>$env:USERDOMAIN\$env:USERNAME</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>true</StartWhenAvailable><ExecutionTimeLimit>PT0S</ExecutionTimeLimit></Settings>
  <Actions Context="Author"><Exec><Command>C:\Windows\System32\conhost.exe</Command>
    <Arguments>--headless C:\Windows\System32\wsl.exe -d Ubuntu-22.04 --exec sleep infinity</Arguments></Exec></Actions>
</Task>
"@
Register-ScheduledTask -TaskName shorts-pipeline-wsl-keepalive -Xml $xml
Start-ScheduledTask -TaskName shorts-pipeline-wsl-keepalive
```

`conhost.exe --headless` keeps a console window from opening every time the task runs. To remove the task: `Unregister-ScheduledTask -TaskName shorts-pipeline-wsl-keepalive`. It needs a logon. Windows here does not sign in by itself, so after a power cut someone still has to sign in.

**Hear about it when the host is gone.** A machine that is off cannot send its own alert. Set `HEALTHCHECK_URL` to an outside monitor and the autopilot pings it every 5 minutes, once it has passed the mount check and while it can reach the database. The monitor alerts when the pings stop. On [healthchecks.io](https://healthchecks.io) (the free plan is enough):

1. Create a check. Set the period to 5 minutes and the grace time to 30 minutes.
2. Under Integrations, add Telegram. It sends to the chat you link, through its own bot.
3. Copy the check's ping URL into `.env` as `HEALTHCHECK_URL="https://hc-ping.com/<uuid>"`, then recreate the autopilot: `docker compose -f docker-compose.yml -f compose.win.yml up -d --force-recreate --no-deps autopilot`.
4. Check the log for `Heartbeat on: pinging the monitor every 5 min`, and that the check turns green.

The ping URL is the check's only credential. It lives in `.env`, never in git, and never in the log.

## Troubleshooting

- `api` never becomes healthy: `docker compose logs api`; usually a missing required variable (`ANTHROPIC_API_KEY`, `TIKTOK_SESSION_ID`, `PEXELS_API_KEY`).
- `autopilot` restarts in a loop with `configuration error`: `AUTOPILOT_NICHE` is empty or `AUTOPILOT_WINDOW` is invalid.
- Upload skipped with `No valid YouTube credentials`: `secrets/youtube_token.json` is missing or invalid; recreate it on a machine with a browser (docs/deploy.md). If the file is there on the host, the container has an empty mount instead — see "Empty mounts after a reboot" above.
- `worker` or `autopilot` log `refuses to start`: `REQUIRE_MOUNTS` is on and the container cannot see its token or songs. Check from inside, `docker compose exec worker ls /app/secrets /app/Songs`, then recreate the container.
- Renders are still slow on Windows with `compose.win.yml`: the GPU reservation succeeded but `FFMPEG_BINARY` did not take effect. Check `docker compose ... exec worker printenv FFMPEG_BINARY`; if it is empty, MoviePy is using the imageio binary, which has no nvenc.
- `h264_nvenc stopped working; encoding with libx264` in the worker log: the container lost CUDA (`cuInit` fails), typically after the WSL host slept or its driver updated. The job finishes on the CPU; recreate the worker to get the GPU back.
- `h264_nvenc` fails with `Cannot load libnvidia-encode.so`: `NVIDIA_DRIVER_CAPABILITIES` is missing `video`. The default capability set grants compute only.
- Files in `output/` are owned by root: containers run as root; `sudo chown -R $USER output` if you need to edit them.
- `worker` or `autopilot` restart a few times right after a host reboot: `restart: always` ignores `depends_on`, so they can start before Postgres answers; they settle within a minute.
