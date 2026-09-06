# Deploying to a VPS

Target: Ubuntu 24.04, 4 vCPU, 16 GB RAM (8 GB works with `OLLAMA_MODEL=llama3.2:3b`), 40 GB disk, no GPU. Everything runs under Docker Compose; see `docs/docker.md` for the stack itself.

## 1. One-time: Google Cloud and YouTube

1. Create a Google Cloud project and enable **YouTube Data API v3**.
2. OAuth consent screen: user type **External**, publishing status **In production** (a "Testing" app expires refresh tokens after 7 days). Add the scope `https://www.googleapis.com/auth/youtube.upload`.
3. Credentials → Create OAuth client ID → type **Desktop app**. Download the JSON as `Backend/client_secret.json`.
4. On your laptop (needs a browser):

   ```bash
   uv run python Backend/youtube_auth.py
   ```

   Sign in with the channel's Google account. This writes `Backend/youtube_token.json`.
5. Uploads from an unverified API project are locked to **private**. Submit the [YouTube API compliance audit](https://support.google.com/youtube/contact/yt_api_form) for the project; once approved set `YOUTUBE_PRIVACY_STATUS=public` in `.env` and restart the worker.

Quota: 10,000 units per day, one upload costs about 1,600 units, so `AUTOPILOT_VIDEOS_PER_DAY` up to 6 fits.

## 2. One-time: Telegram

1. Create a bot with @BotFather, copy the token into `TELEGRAM_BOT_TOKEN`.
2. Send the bot any message, open `https://api.telegram.org/bot<token>/getUpdates`, copy `message.chat.id` into `TELEGRAM_CHAT_ID`.

## 3. Prepare the server

```bash
# as a sudo user on the VPS
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker
docker compose version
git clone <your fork url> MoneyPrinter && cd MoneyPrinter
cp .env.example .env
nano .env        # TIKTOK_SESSION_ID, PEXELS_API_KEY, OLLAMA_MODEL, AUTOPILOT_NICHE, TELEGRAM_*, TZ
mkdir -p secrets output Songs
```

Recommended `.env` values for a 16 GB server: `OLLAMA_MODEL="llama3.1:8b"`, `AUTOPILOT_VIDEOS_PER_DAY="2"`, `AUTOPILOT_WINDOW="09:00-21:00"`, `TZ="Europe/Berlin"` (or your zone).

## 4. Copy the secrets from your laptop

```bash
scp Backend/client_secret.json Backend/youtube_token.json <user>@<vps>:MoneyPrinter/secrets/
```

The worker reads them from `/app/secrets/` (read-only mount). Nothing else on the server needs them.

## 5. Start

```bash
docker compose up -d --build
docker compose logs -f ollama-init        # wait for the model pull to finish
docker compose ps                         # api/worker/autopilot "healthy"/"running"
docker compose logs -f autopilot worker
```

Expected Telegram message: `Autopilot started. Niche: ... 2/day, window 09:00-21:00 <tz>.` The first job is created at the next free slot inside the window; each job takes 5–15 minutes on CPU.

## 6. Watching and operating

- UI: `ssh -L 8080:127.0.0.1:8080 -L 8001:127.0.0.1:8001 <user>@<vps>` then `http://localhost:8001`.
- Update: `git pull && docker compose up -d --build`.
- Pause: `AUTOPILOT_ENABLED=false` in `.env`, `docker compose up -d autopilot`.
- Queue your own topics: `curl -X POST http://localhost:8080/api/topics -H "Content-Type: application/json" -d '{"subject": "..."}'` through the tunnel.
- Disk: `output/` keeps `OUTPUT_RETENTION_DAYS` days of videos; `docker system prune` removes old images after updates.

## 7. Rotating credentials

- TikTok session or Pexels key: edit `.env`, `docker compose up -d`.
- YouTube token revoked or expired: rerun `uv run python Backend/youtube_auth.py` on the laptop and `scp` the new `youtube_token.json` into `secrets/`; no restart needed, the worker reads the file on every upload.
