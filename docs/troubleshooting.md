# Troubleshooting

## No Ollama models in dropdown

- Ensure Ollama is running: `ollama serve`
- Ensure at least one model exists: `ollama list`
- Pull a model if needed: `ollama pull llama3.1:8b`
- Verify backend can reach Ollama base URL in `.env` (`OLLAMA_BASE_URL`)

## Frontend cannot connect to backend

- Confirm backend is running on port `8080`
- Confirm frontend is opened from local server (for example `python3 -m http.server`)
- Check browser console/network for `/api/generate` or `/api/models` failures

## ImageMagick not detected

- Install ImageMagick and ensure executable is on `PATH`
- Or set explicit path in `.env`, for example:

```env
IMAGEMAGICK_BINARY="/usr/local/bin/magick"
```

Windows example:

```env
IMAGEMAGICK_BINARY="C:\\Program Files\\ImageMagick-7.1.1-Q16-HDRI\\magick.exe"
```

## No stock videos found

- Verify `PEXELS_API_KEY` is valid
- Try a broader video subject
- Retry generation; stock results vary by query

## Subtitles fail

- If using AssemblyAI, verify `ASSEMBLY_AI_API_KEY`
- If not using AssemblyAI, local subtitle generation should still work

## YouTube upload skipped

The job still completes; the video stays in `output/`. Check the job events for the reason:

- `No valid YouTube credentials`: run `uv run python Backend/youtube_auth.py` on a machine with a browser, then copy `Backend/youtube_token.json` next to `Backend/client_secret.json` on the server.
- `invalid_grant` while refreshing: the OAuth consent screen is still in "Testing" status (refresh tokens expire after 7 days). Publish the app ("In production") and run `youtube_auth.py` again.
- `quotaExceeded`: the API project used its 10,000 daily units (one upload costs 1,600). Wait for the daily reset.
- Videos uploaded through an API project that has not passed the YouTube API compliance audit are locked to `private` regardless of `YOUTUBE_PRIVACY_STATUS`.

## A published video has wrong or missing metadata

The pipeline sets title, description and tags when it uploads, so this means a
bug that has since been fixed, or a generation the model answered badly.
`Backend/fix_metadata.py` repairs one video in place:

```bash
uv run python Backend/fix_metadata.py <video_id> --print
uv run python Backend/fix_metadata.py <video_id> --tags "space farming, botany" --append-hashtags
```

It is a hand-run tool on purpose and is never called by the pipeline: a
`videos.update` is the one API call in this project that can degrade a live
video. It reads the current snippet and overlays only the fields you pass,
because `videos.update` replaces the whole `snippet` part and would otherwise
clear everything the body omitted. It refuses to leave a video untitled.

Needs the `youtube.force-ssl` scope, the same one captions already use.

## `Autopilot step failed: No valid YouTube credentials.`

The daily metrics refresh reads the OAuth token, so `autopilot` needs
`./secrets` mounted. It is mounted read-only there on purpose: only the worker
should rewrite a refreshed token, since two processes writing the same file
could truncate it and take uploads down. Autopilot logs a warning when it
cannot persist a refresh and carries on with the in-memory credential.

Nothing else is affected when this appears — the step is isolated, and video
generation continues.

## A community post just repeats the video

`fact` posts are written from the **research notes**, not from the script. The
script is what the video already said, so it is the one thing such a post must
not restate — and a 120-word Short exhausts its own subject, leaving a model
given only the script no room to add anything true.

Notes are stored per job in `research_sources` from 2026-09-10 onward. Videos
published before that have none, so `make_post.py` offers only `question` and
`poll` for them and says so.
