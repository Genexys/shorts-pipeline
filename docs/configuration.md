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
| `SCRIPT_MODEL` | *Optional.* Model for the writer's calls — see [The writer](#the-writer). | `claude-opus-5-5` |
| `SCRIPT_FALLBACK_MODEL` | *Optional.* Second Claude model, used only while `SCRIPT_MODEL` is overloaded or down — see [The writer](#the-writer). Unset uses the default; set to an empty value to go straight to Ollama instead. | `claude-sonnet-5-5` |
| `FOOTAGE_MODEL` | *Optional.* Model that looks at a frame of every stock clip before it is used — see [Stock footage](#stock-footage). Needs `ANTHROPIC_API_KEY`; `off` turns the check off. | `claude-sonnet-5-5` |
| `FIRECRAWL_API_KEY` | *Optional.* Grounds scripts in real search results and lists the sources in the description — see [Research](#research). Empty writes scripts with no specifics at all. |

## Optional

| Variable | Description | Default |
|---|---|---|
| `IMAGEMAGICK_BINARY` | Absolute path to ImageMagick executable. If empty, auto-detected from `PATH`. | auto-detect |
| `OLLAMA_BASE_URL` | Ollama server base URL used for model listing and chat generation. | `http://localhost:11434` |
| `OLLAMA_MODEL` | Fallback model if frontend does not send a model value. | `llama3.1:8b` |
| `OLLAMA_TIMEOUT` | Seconds to wait for one Ollama response. | `180` |
| `ELEVENLABS_API_KEY` | Narration for formats that ask for it. Empty keeps everything on the free TikTok voice. See [Narration](#narration). | empty |
| `ASSEMBLY_AI_API_KEY` | If set, AssemblyAI times the subtitles: its transcript is aligned to the script, and the script's own words are shown at the times it heard them (its transcript is shown instead when fewer than half the words line up). If empty, local subtitle generation is used. | empty |
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
| `YOUTUBE_PRIVACY_STATUS` | Privacy of uploaded videos: `private`, `unlisted` or `public`. Invalid values fall back to `private` with a warning. A long video the local model helped write goes up `private` regardless — see [The writer](#the-writer). | `private` |
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
| `AUTOPILOT_COLOR` | Subtitle colour. Cream by default: it matches the channel's amber branding, which pure yellow fights, and near-white inside the black outline the style line already draws is the more legible pairing over unpredictable stock footage. | `#F4EEE3` |
| `AUTOPILOT_USE_MUSIC` | Mix a background music bed from `Songs/`. See [Background music](#background-music). | `false` |
| `AUTOPILOT_CUSTOM_PROMPT` | Custom script prompt. | empty |
| `AUTOPILOT_LONGFORM_PER_WEEK` | Long videos per week, 0..7. `0` keeps the autopilot on Shorts. The budget is paced across the week rather than spent at the start of it: two a week land on Monday and Thursday, three on Monday, Wednesday and Friday. Falling behind (an outage, a day the machine was off) lets the next slots catch up. | `0` |
| `AUTOPILOT_CURIO_SHARE` | Percent of videos that report something absurd but true instead of explaining how something works, 0..100. See [Registers](#registers). | `40` |
| `AUTOPILOT_ANNIVERSARY_SHARE` | Percent of videos built from an event that happened on today's date, 0..100. See [Registers](#registers). | `10` |
| `OUTPUT_RETENTION_DAYS` | Days to keep `output/` videos and thumbnails, 1..365. | `7` |
| `REQUIRE_MOUNTS` | `true` makes `worker` and `autopilot` refuse to start when they cannot see the YouTube token (and, with `AUTOPILOT_USE_MUSIC` on, any `.mp3` under `Songs/`). For hosts where those are known to exist; catches the empty bind mounts Docker Desktop creates when WSL integration is late. See `docs/docker.md`. | `false` |
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
Songs/everyday/track.mp3   # theme folders, preferred when they hold tracks
Songs/body/track.mp3
Songs/space/track.mp3
Songs/invention/track.mp3
Songs/history/track.mp3
Songs/quirky/track.mp3
Songs/dark/track.mp3
```

Themes follow what a video is about, not a mood. The writer model reads the
script and a one-line description of each theme (`MUSIC_THEMES` in
`Backend/utils.py`), picks one, and a random track is taken from that folder.
That is Opus, falling back to Ollama like the script does. On twenty recent
Shorts, llama3.1:8b gave inconsistent answers for seven, and Opus placed all
twenty sensibly.

| Theme | For |
|---|---|
| `everyday` | why ordinary things at home or outdoors behave as they do |
| `body` | the human body, animals and plants |
| `space` | planets, stars, rockets, satellites |
| `invention` | inventions and engineering since the 1800s |
| `history` | older history, or history told calmly |
| `quirky` | odd or funny research, absurd true stories |
| `dark` | poisons, disease, gruesome experiments, harm done |

A track that played under one of the last five videos is skipped while its
folder has anything else. The track and theme are stored on the video
artifact (`music`, `musicTheme`), which is where that history comes from.

The choice is best effort: if no model answers with a known theme, the
pipeline falls back to the flat `Songs/` folder. A flat library
keeps working unchanged.

The library holds 21 tracks made with Eleven Music on 2026-10-05 (three per
theme, instrumental, 90 seconds) plus the earlier YouTube Audio Library
tracks, sorted into the themes. A Short uses only the first ~40 seconds of a
track. A long video loops it, so a 90-second track repeats three times under
five minutes of narration.

Licensing is your responsibility. Eleven Music output is cleared for online
commercial use on any paid ElevenLabs plan. The **YouTube Audio Library** is
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

Both formats narrate with ElevenLabs, each with its own voice; TikTok TTS is
the fallback.

| Format | Model | Voice | Requests | TikTok fallback |
|---|---|---|---|---|
| `short` | `eleven_v4` | Max, Elearning and Documentary | the whole script in one | `en_male_narration` |
| `long` | `eleven_v4` | George, British, `narrative_story` | one per section | `en_au_002` |

The model and the request shape were chosen by ear on 2026-10-05. Ten
narrations of the same Short were compared: Flash v2.5, v4 Turbo, v4 sent a
sentence at a time, and v4 sent whole with different settings. On v4 every
request carries stability 0.5, similarity 0.8, speaker boost, and
`apply_text_normalization: "on"`, so numbers and amounts are always read as
words. Other models get none of this and use the voice's stored settings.

No audio tags. A take directed with `[with renewed energy]` and `[tense]` was
too expressive for popular science, and `[quiet, ominous]` in a long section
turned the narration into a whisper.

Without `ASSEMBLY_AI_API_KEY`, both formats narrate a sentence per request
instead, because the local subtitle timing is built from the individual clips.

`ELEVENLABS_API_KEY` empty switches the integration off entirely and every
format narrates with TikTok, so a deployment without a key still works.

**Failure is whole-video.** If ElevenLabs errors or runs out of credits
part-way through, every sentence is re-narrated with TikTok rather than the
remaining ones. Retrying only the failed sentence would splice two narrators
into one video, which is worse than the cheaper voice throughout. The job logs
which service was used and never fails because of narration.

Cost, measured in plan credits on 2026-10-05:

| Model | Credits per character |
|---|---|
| `eleven_flash_v2_5` | 0.20 |
| `eleven_v3` | 0.40 |
| `eleven_v4` | 0.11 until 12 October 2026 (launch discount), then v3's price |

That makes a Short of ~540 characters about 215 credits and a long video of
~5,000 characters about 2,000. Each response's `character-cost` header is the
exact charge. Restrict the API key to Text to Speech and give it a credit cap:
the autopilot uses it unattended.

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

**Black openings are skipped.** Many stock clips open on a fade up from
black, and a shot used to start on the clip's first frame. Before combining,
the first four seconds of each clip are checked with `blackdetect`, cropped
the way the format frames them:

- black from the first frame: the shot starts after it;
- black running to the end of those four seconds, or the clip's own fade-out:
  the shot stops before it;
- black that comes and goes inside them, or black throughout: the clip is not
  used.

If that would leave no clips, all of them are used as before. The combined
video is then checked again, and any black of half a second or more is logged
as `Black in the combined video at ...`.

**Every clip is looked at.** Stock search matches words, not pictures: on
2026-10-03 the tomato Short opened on an AI-drawn pelican selling fish
("vegetable market stall"), the Neanderthal one on a cartoon dog with a bone,
and a tennis court stood in for the Supreme Court. So after the black check,
the frame each shot opens on, cropped as it will be shown, goes to
`FOOTAGE_MODEL` (Sonnet by default) with the subject, the narration and the
term that found it. The model sorts it into real footage, a realistic render or
a cartoon, and says whether a viewer would accept it as illustrating the video.
Generic footage that suits the topic passes. These are rejected:

- cartoons, drawings, mascots and AI-generated characters;
- something else that only shares a word with the term;
- a picture that contradicts the story, such as another country's flag or a
  modern rocket for a 1957 launch;
- horror imagery in a story that isn't horror.

A rejected clip is logged as `Not using <file> ("<term>"): <kind>, <problem>`
and replaced by the next unused search result. The replacements are checked
too, for two rounds at most. Only real footage may open the video, since the
opening is otherwise picked for contrast and cartoons have the most. One
request covers a Short's ten frames and costs about a cent.

The check never fails a job. With no `ANTHROPIC_API_KEY`, with
`FOOTAGE_MODEL=off`, or when a request fails, clips are used unchecked. If
every clip is rejected and nothing replaces them, they are all used anyway.

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

Results from social networks, shops (Amazon under any country domain, eBay,
Etsy), LinkedIn, Threads and answer sites (JustAnswer, Chegg) are dropped
before they reach the brief or the description.

With `ANTHROPIC_API_KEY` set, the writer then reads every result's title and
snippet and keeps only the ones about the subject itself. A search matches
words, not meaning: in one week it put a case report on loop recorders under a
pacemaker video, a study of men sweating under a video about a glass of water
"sweating", and a school test item under bees. Both papers were on nih.gov, so
the knowledge base read them first. The judge costs about a cent a video, and
one more per section of a long video. Without the key, or if its answer cannot
be read, every result is kept as before.

The prompt also requires the script to claim no more than the notes do. It
keeps their hedges ("may", "a hypothesis", "a single case"), keeps the scope of
each figure, and says "first" or "invented" only where a note does. That holds
for the opening sentence most of all, since it often becomes the title.

Search bills 2 credits per 10 results against a free 1000 a month, so three
videos a day costs well under the free tier. A failed search, an exhausted
balance or a missing key all produce the same thing: an empty brief and a
video that still gets made.

### Knowledge base

Search returns a sentence or two per page, and that sentence is rarely the
page's best. So the two best sources of each video — reference pages such as
Wikipedia, `.gov` and `.edu` first, PDFs never — are read in full, and every
sentence on them that carries something specific (a figure, a comparison, a
first or a record) is stored in the `knowledge_pages` and `facts` tables and
added to the brief under the snippets. So is any sentence that limits a claim:
something unproven, disputed, a myth or a case report, "not a contributing
factor". A finding is kept with its p-value in brackets; the methods
statistics around it are still dropped. One paper on two sites (the journal
and PubMed Central) is read once. Quoted passages are read like the rest of the
page, and one introduced by a sentence ending in a colon ("…, saying that:") is
stored together with that sentence, so the fact says whose words they are.

The topic line's own words count too. Words in the subject that the search
snippets don't share say what this video in particular promises: "upper" and
"atmosphere" in "Sputnik: how a beeping sphere revealed Earth's upper
atmosphere", where "Sputnik" is on every page. Up to five sentences that use
them are stored even without a figure in them. In the brief, each such word a
fact uses also lifts its rank, so the sentence that delivers the promise is
among the first the writer reads. Pages stored before this keep the facts they
had; the lift still applies to those.

So do explanations. A sentence that says what causes what ("because",
"causes", "results in", "drives", "sucks"…) and uses the subject's words is
stored without a figure too, up to eight a page, and may speak to the reader
("vibrations inside your skull"): the answer to a "why" rarely has a number in
it. When the topic line asks why or how (not "how many"), these rank higher in
the brief. "Why does a shower curtain billow inward?" now gets Wikipedia's
"the spray from the shower-head drives a horizontal vortex… which sucks the
curtain", and a hedge such as "this is not the sole mechanism" is kept as one.

Facts are stored **verbatim**: each is a sentence that appears word for word on
the page it cites, so nothing can be invented on the way in. Reference lists,
navigation, maintenance tags and sentences addressed to the reader are dropped.
A sentence whose subject is only "he", "she" or "they" is stored together with
the earlier sentence in its paragraph that names them, with "…" marking any
sentences in between, since on its own it is a fact about nobody. Pages stored
before this keep their facts as they were. Search snippets get the same care:
sentences at the start of a snippet about a "he" or "she" it never names are
left out of the brief, and a snippet with nothing else is not shown at all.

A page is read **once, ever**. The next video citing the same page takes its
facts from the database. A read costs 1 credit, so two pages a video adds at
most about 250 credits a month, less as pages repeat. A failed read is not
remembered and is retried by the next video; any failure in the knowledge base
leaves the brief as snippets only.

## Registers

Every video is an **explainer** — how something works — a **curio** (a real
phenomenon that sounds invented), or an **anniversary**: something that
happened on today's date. `AUTOPILOT_CURIO_SHARE` and
`AUTOPILOT_ANNIVERSARY_SHARE` set how often the second and third are drawn,
per video rather than in rotation. A channel that reliably alternates is as
templated as one that never varies.

The defaults have been 40% curios, 10% anniversaries and 50% explainers since
2026-10-05; before that they were 33 / 20 / 47. The change comes from the first
month's settled Shorts:

| Register | Median views | View percentage | Subscriptions per 1,000 views |
|---|---|---|---|
| curio | 1,172 | 51% | 1.6 |
| explainer | 1,095 | 46% | 1.2 |
| anniversary | 927 | 50% | 0.75 |

A register also sets the length, in `REGISTER_TARGET_WORDS`: a curio is asked
for 70 words, an explainer 80, an anniversary 85. A curio is one absurd fact and
the sentence that lands it; an explainer usually has a mechanism with a second
step; an anniversary spends words on a date, a name and a place before it can
say what happened. Letting the writer pick inside a range instead did not work —
given "59 to 88 words", it wrote to the top every time.

A draft more than `TARGET_SLACK_WORDS` (12) over its target goes back to the
writer to be cut to the target. The cut keeps the first sentence and the final
paragraph, and it may not remove:
- what a kept sentence refers to;
- the surprising detail;
- whatever answers the video's question;
- who built or discovered something;
- a word that narrows a claim;
- the real case that shows something happened.

A cut that drops a name is asked for once more. If the second cut drops it too,
the draft stands, as long as it fits under the ceiling. The slack was 6 until
2026-10-03, when nine of fifteen cuts in a week had removed a fact rather than
padding.

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

With `ANTHROPIC_API_KEY` set, four calls leave Ollama: the **topic**, the
**script**, the **stock search terms**, and the **YouTube title, description
and tags**. The music mood stays behind: that is structured extraction, and the
local model does it well and for free.

Search terms were on that list until an onion video searched "tear gas
escape" — a metaphor from its own script — and came back with birds
scattering off a river. Deciding whether a phrase names something a camera
can point at is judgement, not extraction.

Metadata moved for the same reason. In the week of 2026-09-27 the local model
put more false statements into titles and descriptions than the writer put into
the narration — "Edison's Hydroelectric Rival" for a plant built on Edison's
licence, "the first pacemaker" where the source said "helped create" — and on
long videos it read only the first 200 words, so a video about the first CT
scan was titled and tagged as one about Röntgen. The writer reads the whole
script and may say only what the script says. If it is unavailable, the local
model writes the metadata from the 200-word excerpt, as before.

Those two are where judgement shows. A curio in particular lives or dies on
whether the subject is genuinely absurd rather than merely phrased as though it
were, and telling those apart is exactly the discrimination a small model
lacks. It can write joke-shaped text; it cannot reliably tell whether the joke
landed.

Failure is never fatal: whatever happens, the call ends with some model's text
— a video written locally beats no video. But the local model is the last
resort, not the first, and what happens before it depends on what went wrong.

**Overload or outage.** A 529, 500, 502, 503 or 504, a rate limit (429), a
dropped connection or a timeout is waited out: the primary model is asked again
after 30 s, 60 s and 120 s (`OUTAGE_BACKOFF_SECONDS` in `Backend/writer.py`),
three and a half minutes in all. The SDK's own retries are off for these
requests; they are a fraction of a second apart, and on 2026-09-28 they gave up
on an overloaded Opus at once, so llama3.1:8b wrote five of a long video's
eight sections and its search terms.

If the ladder runs out, `SCRIPT_FALLBACK_MODEL` (Sonnet by default) writes the
call, with only the SDK's quick retries. The process then remembers the outage
for ten minutes: every call in that time goes straight to the fallback model
without climbing the ladder again, since a long video makes eight section calls
and the search terms, and three and a half minutes each would hold the queue
for half an hour. Only if the fallback model fails too does Ollama write. With
`SCRIPT_FALLBACK_MODEL` set to an empty value, Ollama writes instead, and still
without the ladder for those ten minutes.

**Refusal.** Asked once more with the purpose of the request stated, as before.
A refusal is not an outage: no waiting, no fallback model, and a second refusal
goes to Ollama.

**Account problem.** A 400 saying the credit balance is too low, a 402, or an
authentication or permission error (401, 403) cannot be waited out, and the
fallback model bills the same account, so the call goes straight to Ollama and
the owner is told: one Telegram message (via `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_ID`) saying scripts are now written by the local model and the
balance needs topping up — or, for 401/403, the key needs checking. It is sent
at most once every four hours per process, so the worker and the autopilot may
each send one. On 2026-09-27 the balance ran out at 15:00 and every call
quietly fell back to llama until a video was reviewed by hand.

Anything else — an unknown model, a malformed request, an empty answer — goes
to Ollama at once, as before.

**Who wrote it.** The script records the model that actually wrote it
(`scriptModel` on the video artifact). A long script is written a section at a
time and can change hands part way, so it is filed under its weakest writer,
ranked local < fallback Claude < primary: Opus and Sonnet make a Sonnet script,
and any section by Ollama makes an Ollama script. The video artifact carries
`scriptFellBack` (not written by the primary) and `scriptLocal` (written, at
least in part, by the local model instead of Claude); both stay false when no
key is configured, since Ollama writing is then the design and not a fallback.

**A long video the local model helped write goes up private**, whatever
`YOUTUBE_PRIVACY_STATUS` says, and is not cross-posted to Instagram. The
autopilot's Telegram report says so plainly, so it can be watched and
published by hand in YouTube Studio. On 2026-09-28 such a video went out public
with a fabricated claim about a real doctor (https://youtu.be/uynIVYwuEvw).
A Short keeps the configured status, and its report carries the usual
`⚠️ written by …` line. The privacy actually used is recorded on the
`youtube_video` artifact as `privacyStatus`.

**Model and effort.** The default is Claude Opus 5.5 ($4 / $20 per million
input / output tokens; Claude Opus 5, used until 2026-10-03, was $5 / $25).
Every request sets `effort: high`. Opus 5.5 would otherwise think at `medium`,
which is less than Opus 5 did with no setting at all. The model's thinking is
billed as output. At four videos a day, with the topic, script, search terms,
metadata and source judge, this is a few dollars a month, small next to the
narration bill. The console's usage page has the real figure.

**Refusal fallback.** Each request also asks for the API's server-side refusal
fallback (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`). If
the model's safety classifiers decline a request, the same call is re-run on
the model Anthropic recommends for that kind of refusal. Opus 5.5 added biology
to the categories it can decline, which matters on a channel that is half
biology. The script is filed under whichever model actually answered.
