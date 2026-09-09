# Autopilot

`Backend/autopilot.py` runs next to the API and the worker. Every minute it:

1. Checks topics whose job finished: `completed` marks the topic `done`, `failed` or `cancelled` marks it `failed`, and sends a Telegram message; and warns once when a job has been queued or running for more than 3 hours.
2. Decides whether to queue a new job (see below) using a manually added topic first, otherwise a topic invented by Ollama for `AUTOPILOT_NICHE`.
3. Once an hour deletes `output/*.mp4` and `output/*.jpg` older than `OUTPUT_RETENTION_DAYS`.

Run locally:

```bash
uv run python Backend/autopilot.py
```

The worker must be running as well; the autopilot only queues jobs.

## When a job is created

All of these must hold:

- `AUTOPILOT_ENABLED=true`;
- local time (`TZ`) is inside `AUTOPILOT_WINDOW`;
- fewer than `AUTOPILOT_VIDEOS_PER_DAY` topics were used today;
- at least `window length / AUTOPILOT_VIDEOS_PER_DAY` has passed since the last topic was used (with the default `09:00-21:00` and 2 per day that is 6 hours);
- no job is `queued` or `running`, including jobs started from the UI.

Autopilot jobs are created with two attempts: a transient failure (TTS down, Ollama timeout) is retried once by the worker.

## Topics

Every subject ever used is stored in the `topics` table with a normalized form (lowercase, letters, digits and spaces). Duplicates are rejected, and the last 50 subjects are passed to Ollama with an instruction not to repeat them.

Add your own topics ahead of time:

```bash
curl -X POST http://localhost:8080/api/topics -H "Content-Type: application/json" \
  -d '{"subject": "Why octopuses have three hearts"}'
curl "http://localhost:8080/api/topics?status=planned&limit=50"
```

Responses: `201` with the topic, `409` if it already exists, `400` if the subject is empty.

## Telegram

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token into `TELEGRAM_BOT_TOKEN`.
2. Send any message to the bot, then open `https://api.telegram.org/bot<token>/getUpdates` and copy `message.chat.id` into `TELEGRAM_CHAT_ID`.

Messages:

- `Autopilot started. Niche: <niche>. <N>/day, window <window> <tz>.`
- `✅ <title>` + YouTube link (or `upload skipped: <reason>`) + job id
- `❌ <subject>` + error + job id and attempts
- `⚠️ <subject> cancelled` + job id
- `⚠️ <subject>` + `job <id> queued for <N>h, worker may be stuck` — once per topic after 3 hours without a result
- one warning after 10 consecutive minutes without a usable topic from Ollama

If the token or chat id is empty, messages are printed to the log with a `[telegram disabled]` prefix.

## Pausing

Set `AUTOPILOT_ENABLED=false` and restart the autopilot process. Finished jobs are still reported. In Docker, see the pause command in `docs/docker.md`.
