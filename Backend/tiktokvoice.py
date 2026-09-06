# author: GiorDior aka Giorgio
# date: 12.06.2023
# topic: TikTok-Voice-TTS
# version: 1.0
# credits: https://github.com/oscie57/tiktok-voice

# --- MODIFIED VERSION --- #

import base64
import threading
import time
from typing import List, Optional

import requests

from logstream import log


VOICES = [
    # DISNEY VOICES
    "en_us_ghostface",  # Ghost Face
    "en_us_chewbacca",  # Chewbacca
    "en_us_c3po",  # C3PO
    "en_us_stitch",  # Stitch
    "en_us_stormtrooper",  # Stormtrooper
    "en_us_rocket",  # Rocket
    # ENGLISH VOICES
    "en_au_001",  # English AU - Female
    "en_au_002",  # English AU - Male
    "en_uk_001",  # English UK - Male 1
    "en_uk_003",  # English UK - Male 2
    "en_us_001",  # English US - Female (Int. 1)
    "en_us_002",  # English US - Female (Int. 2)
    "en_us_006",  # English US - Male 1
    "en_us_007",  # English US - Male 2
    "en_us_009",  # English US - Male 3
    "en_us_010",  # English US - Male 4
    # EUROPE VOICES
    "fr_001",  # French - Male 1
    "fr_002",  # French - Male 2
    "de_001",  # German - Female
    "de_002",  # German - Male
    "es_002",  # Spanish - Male
    # AMERICA VOICES
    "es_mx_002",  # Spanish MX - Male
    "br_001",  # Portuguese BR - Female 1
    "br_003",  # Portuguese BR - Female 2
    "br_004",  # Portuguese BR - Female 3
    "br_005",  # Portuguese BR - Male
    # ASIA VOICES
    "id_001",  # Indonesian - Female
    "jp_001",  # Japanese - Female 1
    "jp_003",  # Japanese - Female 2
    "jp_005",  # Japanese - Female 3
    "jp_006",  # Japanese - Male
    "kr_002",  # Korean - Male 1
    "kr_003",  # Korean - Female
    "kr_004",  # Korean - Male 2
    # SINGING VOICES
    "en_female_f08_salut_damour",  # Alto
    "en_male_m03_lobby",  # Tenor
    "en_female_f08_warmy_breeze",  # Warmy Breeze
    "en_male_m03_sunshine_soon",  # Sunshine Soon
    # OTHER
    "en_male_narration",  # narrator
    "en_male_funny",  # wacky
    "en_female_emotional",  # peaceful
]

ENDPOINTS = [
    "https://tiktok-tts.weilnet.workers.dev/api/generation",
    "https://ottsy.weilbyte.dev/api/generation",
]
REQUEST_TIMEOUT = 30
MAX_ATTEMPTS_PER_ENDPOINT = 3
BACKOFF_SECONDS = (2, 4, 8)
# in one conversion, the text can have a maximum length of 300 characters
TEXT_BYTE_LIMIT = 300

_sleep = time.sleep


class TTSError(RuntimeError):
    """Raised when no TTS endpoint could produce audio."""


def split_string(string: str, chunk_size: int) -> List[str]:
    """Split text into chunks of at most chunk_size characters on word boundaries."""
    words = string.split()
    result: List[str] = []
    current_chunk = ""
    for word in words:
        if len(current_chunk) + len(word) + 1 <= chunk_size:
            current_chunk += f" {word}"
        else:
            if current_chunk:
                result.append(current_chunk.strip())
            current_chunk = word
    if current_chunk:
        result.append(current_chunk.strip())
    return result


def save_audio_file(base64_data: str, filename: str = "output.mp3") -> None:
    audio_bytes = base64.b64decode(base64_data)
    with open(filename, "wb") as file:
        file.write(audio_bytes)


def _request_audio(endpoint: str, text: str, voice: str) -> str:
    response = requests.post(
        endpoint,
        headers={"Content-Type": "application/json"},
        json={"text": text, "voice": voice},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, str) or not data:
        error = payload.get("error") if isinstance(payload, dict) else payload
        raise TTSError(f"no audio in response: {error}")
    if "base64," in data:
        data = data.split("base64,", 1)[1]
    return data


def generate_audio(text: str, voice: str) -> str:
    """Return base64 audio for text, trying every endpoint with backoff."""
    last_error = "no TTS endpoints configured"
    total_attempts = len(ENDPOINTS) * MAX_ATTEMPTS_PER_ENDPOINT
    attempt_number = 0
    for endpoint in ENDPOINTS:
        for attempt in range(MAX_ATTEMPTS_PER_ENDPOINT):
            attempt_number += 1
            try:
                return _request_audio(endpoint, text, voice)
            except Exception as err:
                last_error = f"{endpoint}: {err}"
                log(
                    f"[-] TTS attempt {attempt + 1}/{MAX_ATTEMPTS_PER_ENDPOINT} failed: {last_error}",
                    "warning",
                )
                if attempt_number < total_attempts:
                    _sleep(BACKOFF_SECONDS[attempt])
    raise TTSError(f"TTS failed after {total_attempts} attempts. Last error: {last_error}")


def tts(text: str, voice: str = "none", filename: str = "output.mp3") -> None:
    """Create an MP3 file for text. Raises TTSError on any failure."""
    if voice == "none" or voice not in VOICES:
        raise TTSError(f"Voice '{voice}' is not available.")
    if not text or not text.strip():
        raise TTSError("Text for TTS is empty.")

    if len(text) < TEXT_BYTE_LIMIT:
        parts = [text]
    else:
        parts = split_string(text, 299)

    chunks: List[Optional[str]] = [None] * len(parts)
    errors: List[str] = []

    def generate_chunk(index: int, part: str) -> None:
        try:
            chunks[index] = generate_audio(part, voice)
        except Exception as err:
            errors.append(str(err))

    threads = [
        threading.Thread(target=generate_chunk, args=(index, part))
        for index, part in enumerate(parts)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    if errors or any(chunk is None for chunk in chunks):
        raise TTSError(errors[0] if errors else "TTS returned no audio.")

    # Decode each chunk individually before concatenating: base64 padding
    # inside a joined string is not valid base64, so each part must be
    # decoded on its own and the raw bytes concatenated.
    audio_bytes = b"".join(base64.b64decode(chunk) for chunk in chunks if chunk)
    with open(filename, "wb") as file:
        file.write(audio_bytes)
    log(f"[+] Audio file saved successfully as '{filename}'", "success")
