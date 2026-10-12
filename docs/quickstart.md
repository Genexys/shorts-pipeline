# Quickstart

Run MoneyPrinter locally. Every model call goes to the Claude API, so you need an Anthropic API key.

## 1) Clone repository

```bash
git clone https://github.com/FujiwaraChoki/MoneyPrinter.git
cd MoneyPrinter
```

## 2) Quick setup (recommended)

Run the interactive setup script:

```bash
./setup.sh
```

This script checks dependencies, sets up `.env`, installs Python packages with `uv`, and checks that `ANTHROPIC_API_KEY` is set.

## 3) Manual setup

Use this path if you prefer to run each step yourself.

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- FFmpeg
- ImageMagick
- An Anthropic API key

### Install and create env file

```bash
uv sync
cp .env.example .env
```

Windows PowerShell for `.env` copy:

```powershell
Copy-Item .env.example .env
```

## 4) Configure environment

Set required values in `.env`:

- `ANTHROPIC_API_KEY`
- `TIKTOK_SESSION_ID`
- `PEXELS_API_KEY`

See [Configuration](configuration.md) for all variables.

## Optional: Run tests

```bash
uv sync --group dev
uv run pytest
```

## 5) Run backend

```bash
uv run python Backend/main.py
```

## 6) Run worker

In a new terminal:

```bash
uv run python Backend/worker.py
```

## 7) Run frontend

In a new terminal:

```bash
cd Frontend
python3 -m http.server 3000
```

Open `http://localhost:3000`.

## 8) Generate video

1. Enter a video subject.
2. Expand advanced options.
3. The model list shows the Claude models from `SCRIPT_MODEL`; Claude writes every step whatever is selected.
4. Click Generate.

Output file: `output.mp4` at project root.
