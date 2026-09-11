# Autopilot

`Backend/autopilot.py` runs next to the API and the worker. Every minute it:

1. Checks topics whose job finished: `completed` marks the topic `done`, `failed` or `cancelled` marks it `failed`, and sends a Telegram message; and warns once when a job has been queued or running for more than 3 hours. A published video is followed by a second message: a ready-to-paste community post, captioned onto a still from the video (see [Community posts](#community-posts)).
2. Decides whether to queue a new job (see below) using a manually added topic first, otherwise a topic invented by Ollama for `AUTOPILOT_NICHE`.
3. Once a day pulls settled performance numbers for every published video into `video_metrics`.
4. Each morning records every published video's current view, like and comment counts, and reports the day's movement to Telegram.
5. Each Monday sends a retention digest built from the settled Analytics numbers.
6. Once an hour deletes `output/*.mp4` and `output/*.jpg` older than `OUTPUT_RETENTION_DAYS`.

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

## Community posts

YouTube has no API for creating community posts, so after each upload the
autopilot sends one to Telegram to be pasted into Studio by hand.

The caption **is the post text and nothing else**. Telegram copies a caption
whole, so a heading or the video link would be pasted into Studio along with
it. The video link is in the preceding success message instead.

A still from the video rides along, because a community post with a picture
reaches further than one without. It is a plain frame — `pick_still`, not
`build_thumbnail` — since the post carries its own words and a title burned
into the image would only repeat them.

A post that cannot be written is logged and dropped. It never costs the
notification that the video is live, and there is no placeholder text: an
obviously generated post is worse than no post.

Posts of the `fact` kind need the video's research notes, which are stored from
2026-09-10 onward. Older videos get a question or a poll.

## Two clocks

The two sets of numbers come from different APIs with different freshness, and
conflating them is how you end up reading a fresh video as a failure.

**Counts — views, likes, comments — come from the Data API and are current.**
They are read once each morning, so every reading covers a whole finished day
rather than a partial one, and stored as one row per video per day. That makes
a curve: how fast a video picked up and whether it kept going.

**Retention comes from the Analytics API, which Google documents as running 48
to 72 hours behind.** A daily digest of it would mostly repeat itself, so the
digest is weekly, and it only ranks videos older than seven days — anything
younger has no settled data at all and reads as zero rather than as unknown.
