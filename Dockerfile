FROM python:3.11-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg imagemagick \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Use the image's Python 3.11; never download another interpreter.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY Backend Backend
COPY fonts fonts
COPY Frontend Frontend

RUN mkdir -p temp subtitles output Songs secrets

ENV PATH="/app/.venv/bin:$PATH" \
    IMAGEMAGICK_BINARY=/usr/bin/convert \
    PYTHONUNBUFFERED=1
