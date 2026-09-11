# Configuration

MoneyPrinter reads configuration from `.env` (project root).

Use `.env.example` as your template.

## Required

| Variable | Description |
|---|---|
| `TIKTOK_SESSION_ID` | TikTok session cookie (`sessionid`) used for TTS voice endpoint calls. |
| `PEXELS_API_KEY` | API key used to fetch stock video clips. |
| `PIXABAY_API_KEY` | *Optional.* A second stock library, merged with Pexels. Empty uses Pexels alone. Long videos need it — see [Stock footage](#stock-footage). |
| `ANTHROPIC_API_KEY` | *Optional.* Writes the topic and the script with a stronger model — see [The writer](#the-writer). Empty keeps everything on Ollama. |
| `SCRIPT_MODEL` | *Optional.* Model for those two calls. | `claude-opus-5` |
| `FIRECRAWL_API_KEY` | *Optional.* Grounds scripts in real search results and lists the sources in the description — see [Research](#research). Empty writes scripts with no specifics at all. |

## Optional

| Variable | Description | Default |
|---|---|---|
| `IMAGEMAGICK_BINARY` | Absolute path to ImageMagick executable. If empty, auto-detected from `PATH`. | auto-detect |
| `OLLAMA_BASE_URL` | Ollama server base URL used for model listing and chat generation. | `http://localhost:11434` |
| `OLLAMA_MODEL` | Fallback model if frontend does not send a model value. | `llama3.1:8b` |
| `OLLAMA_TIMEOUT` | Seconds to wait for one Ollama response. | `180` |
| `ELEVENLABS_API_KEY` | Narration for formats that ask for it. Empty keeps everything on the free TikTok voice. See [Narration](#narration). | empty |
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

Scopes: `youtube.upload` covers `videos.insert` and `thumbnails.set`.
`captions.insert` accepts only `youtube.force-ssl`, which also allows managing
and deleting channel content — a token minted before that scope existed keeps
working for everything else, and caption upload is refused with a message
rather than an opaque API error.

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
| `AUTOPILOT_LONGFORM_PER_WEEK` | Long videos per week, 0..7. `0` keeps the autopilot on Shorts. Long form wins the slot whenever the weekly budget has room. | `0` |
| `AUTOPILOT_CURIO_SHARE` | Percent of videos that report something absurd but true instead of explaining how something works, 0..100. See [Registers](#registers). | `33` |
| `AUTOPILOT_ANNIVERSARY_SHARE` | Percent of videos built from an event that happened on today's date, 0..100. See [Registers](#registers). | `20` |
| `OUTPUT_RETENTION_DAYS` | Days to keep `output/` videos and thumbnails, 1..365. | `7` |
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

## Narration

Two voice services, chosen per format rather than per request.

| Format | Service | Voice |
|---|---|---|
| `short` | TikTok TTS | `en_us_001` |
| `long` | ElevenLabs | George, British, `narrative_story` |

Shorts stay on the free service deliberately. Thirty seconds of synthetic
narration is tolerable; four minutes of it is where retention goes. Paid
credits are worth more on the longer format.

`ELEVENLABS_API_KEY` empty switches the integration off entirely and every
format narrates with TikTok, so a deployment without a key still works.

**Failure is whole-video.** If ElevenLabs errors or runs out of credits
part-way through, every sentence is re-narrated with TikTok rather than the
remaining ones. Retrying only the failed sentence would splice two narrators
into one video, which is worse than the cheaper voice throughout. The job logs
which service was used and never fails because of narration.

Cost, on pay-as-you-go at $0.10 per 1000 characters: about $0.33 for a
long-form video of ~550 words, and about $0.055 for a Short if you ever switch
one over. Restrict the API key to Text to Speech and give it a credit cap — the
autopilot uses it unattended.

## Stock footage

Two libraries are searched per term and the results interleaved, so neither
fills a video on its own. Pixabay is optional: with `PIXABAY_API_KEY` empty
the pipeline uses Pexels alone, exactly as before.

**Long videos need the second source.** A three-and-a-half minute video needs
about 19 distinct clips at the twelve-second shot cap. Measured on a narrow
subject, Pexels returned 13 of the 20 requested — ten search terms about the
same thing return overlapping results, and the video repeated itself part way
through. The two catalogues barely overlap, so a second library roughly
doubles what is available.

Neither library can fail a job. A search that errors or is unconfigured
returns nothing and the other one carries the video; if both come up short,
the shot cap stretches so the footage still covers the runtime without
repeating.

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

## Research

With `FIRECRAWL_API_KEY` set, the pipeline searches the web for the subject
before writing the script and passes the results in as numbered notes. The
prompt then asks for concrete detail — figures, dates, named studies — but
allows only what appears in those notes, and the sources are listed under the
video description.

The point is credibility, not caution. "A 2019 study found 47%" is worth more
to a viewer than "some research suggests", but only if the number is real and
someone who checks finds it. An invented citation is worse than none: it looks
verifiable and is not.

With the key empty, research is skipped and the prompt takes the opposite
instruction — state nothing specific at all, no figure, date, study or
institution. That is the safe fallback rather than the good one: the model
given no sources fabricates plausible specifics, which is exactly the failure
being avoided.

Search bills 2 credits per 10 results against a free 1000 a month, so three
videos a day costs well under the free tier. A failed search, an exhausted
balance or a missing key all produce the same thing: an empty brief and a
video that still gets made.

## Registers

Every video is an **explainer** — how something works — a **curio** (a real
phenomenon that sounds invented), or an **anniversary**: something that
happened on today's date. `AUTOPILOT_CURIO_SHARE` sets how often
the second is drawn, per video rather than in rotation. A channel that reliably
alternates is as templated as one that never varies.

A register is not a tone of voice. An 8B model asked to be funny writes
strained puns and tells the viewer that science is amazing; asked to state
something absurd plainly, it lets the subject do the work. So a curio script is
instructed to stay completely straight — no jokes, no asides, no exclamation
marks.

The topic brief for a curio also forbids overselling: no "literally", no
absolutes the evidence does not carry. An overstated premise is one the script
then has to defend, and it defends it by inventing.

Anniversaries are not a reach play. This channel takes 96% of its views from
the Shorts feed and 2.5% from search, and on a round date the search results
belong to channels orders of magnitude larger. They are there for grounding: a
dated event has a year, a place and participants, research finds it
immediately, and unlike "mushrooms have built-in umbrellas" it cannot be
invented.

Wikimedia's on-this-day feed supplies them, free and unauthenticated.
Disasters, wars, crime and politics are filtered out before the model sees the
day, and what survives is ordered so science and technology are read first —
the feed is newest-first, so a plain truncation would keep this decade's
politics and cut the nineteenth-century discovery. If nothing suitable remains,
the register falls back to an explainer rather than forcing one.

The register is chosen when the job is queued and travels in its payload, since
the script is written in another process.

## The writer

With `ANTHROPIC_API_KEY` set, three calls leave Ollama: the **topic**, the
**script**, and the **stock search terms**. YouTube metadata and the music mood stay behind: those are
structured extraction, and the local model does them well and for free.

Search terms were on that list until an onion video searched "tear gas
escape" — a metaphor from its own script — and came back with birds
scattering off a river. Deciding whether a phrase names something a camera
can point at is judgement, not extraction.

Those two are where judgement shows. A curio in particular lives or dies on
whether the subject is genuinely absurd rather than merely phrased as though it
were, and telling those apart is exactly the discrimination a small model
lacks. It can write joke-shaped text; it cannot reliably tell whether the joke
landed.

Failure is never fatal. A missing key, a rate limit, an outage or a refusal all
fall back to Ollama for that call — a video written locally beats no video.

At three videos a day this is roughly 240k input and 32k output tokens a month:
about two dollars on the default model, and noise beside the narration bill.
