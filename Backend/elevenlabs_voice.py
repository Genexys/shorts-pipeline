"""ElevenLabs text-to-speech, shaped like tiktokvoice.tts so either can be used.

Only the synthesis endpoint is touched. The API key is restricted to it, and
nothing here needs voice listing or history.
"""

import os
from typing import Optional

import requests

from logstream import log

ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech"
# Fallback for callers that do not name one; the format normally decides.
ELEVENLABS_MODEL = "eleven_multilingual_v2"
ELEVENLABS_TIMEOUT = 120


def api_key() -> str:
    """The configured key, or "" when the integration is switched off."""
    return os.getenv("ELEVENLABS_API_KEY", "").strip()


def is_configured() -> bool:
    return bool(api_key())


def scrub(text: str, key: Optional[str] = None) -> str:
    """Removes the API key from arbitrary text.

    The key travels in a header rather than the URL, so it rarely appears in
    errors — but "rarely" is not "never", and a leaked key here would be live.
    """
    key = api_key() if key is None else key
    return text.replace(key, "<key>") if key else text


def tts(
    text: str, voice_id: str, filename: str, model: str = ELEVENLABS_MODEL
) -> None:
    """Synthesizes one piece of text to an mp3 file.

    Args:
        text (str): What to say.
        voice_id (str): ElevenLabs voice id.
        filename (str): Where to write the mp3.
        model (str): ElevenLabs model id. Cheaper models bill fewer credits.

    Raises:
        RuntimeError: On any failure, with the key removed from the message.
    """
    key = api_key()
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set.")

    try:
        response = requests.post(
            f"{ELEVENLABS_URL}/{voice_id}",
            headers={"xi-api-key": key, "Content-Type": "application/json"},
            json={"text": text, "model_id": model},
            timeout=ELEVENLABS_TIMEOUT,
        )
        response.raise_for_status()
    except Exception as err:
        raise RuntimeError(f"ElevenLabs request failed: {scrub(str(err))}") from err

    if not response.content:
        raise RuntimeError("ElevenLabs returned an empty audio body.")

    with open(filename, "wb") as handle:
        handle.write(response.content)
    log(f"[+] ElevenLabs audio saved as '{filename}'", "success")
