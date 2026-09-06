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
