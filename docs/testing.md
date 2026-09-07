# Testing

MoneyPrinter uses `pytest` for backend tests.

## Install test dependencies

Install dev dependencies (includes `pytest`):

```bash
uv sync --group dev
```

## Run tests

Run all tests:

```bash
uv run pytest
```

Run one test file:

```bash
uv run pytest tests/test_repository.py
```

Run one test:

```bash
uv run pytest tests/test_repository.py::test_create_job_persists_payload_and_queued_event
```

## Current test scope

- `tests/test_api_jobs.py`: API queue, job status/events, and cancellation endpoints.
- `tests/test_api_misc.py`: API model listing fallback and song upload endpoint behavior.
- `tests/test_api_topics.py`: POST/GET `/api/topics` validation (empty, overlong, non-dict body), duplicate rejection, and status/limit filtering.
- `tests/test_autopilot.py`: autopilot tick behavior — payload/prompt building, slot-aware job creation, topic generation retries, finishing completed/vanished-job topics, the stall watchdog, the Telegram notifier binding, output cleanup, and end-to-end `run_tick`.
- `tests/test_autopilot_config.py`: `AutopilotConfig.from_env` parsing/validation and the `slot_available` scheduling rule.
- `tests/test_metadata.py`: JSON extraction from Ollama responses and YouTube metadata validation/generation.
- `tests/test_notify.py`: Telegram message sending, env-var fallback, disabled-path logging, token scrubbing, and text truncation.
- `tests/test_repository.py`: queue/repository behavior for create, claim, cancel, and completion events.
- `tests/test_repository_topics.py`: topic repository helpers — normalization, duplicate rejection, scheduling queries, and job linkage.
- `tests/test_search.py`: Pexels stock video search timeout and largest-file selection.
- `tests/test_tiktokvoice.py`: TikTok TTS endpoint fallback, text chunking, and voice validation.
- `tests/test_utils.py`: filesystem cleanup, song selection, and ImageMagick binary resolution.
- `tests/test_worker.py`: worker loop behavior for success, cancellation, failure, and empty queue.
- `tests/test_youtube.py`: YouTube OAuth token loading/refresh, upload request building, and privacy status resolution.
- `tests/conftest.py`: isolated SQLite session fixture per test.
