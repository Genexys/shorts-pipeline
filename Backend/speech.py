"""Chooses which voice service narrates a video, and falls back cleanly.

The fallback is whole-video on purpose. Retrying a single failed sentence on
the other service would splice two different narrators into one video, which
is worse than the cheaper voice throughout.
"""

from typing import Callable, List, Optional, Tuple

import elevenlabs_voice
import tiktokvoice

TIKTOK = "tiktok"
ELEVENLABS = "elevenlabs"


def choose_provider(elevenlabs_voice_id: Optional[str]) -> str:
    """ElevenLabs only when this format asks for it and a key is configured."""
    if elevenlabs_voice_id and elevenlabs_voice.is_configured():
        return ELEVENLABS
    return TIKTOK


def synthesize_sentences(
    sentences: List[str],
    make_path: Callable[[], str],
    tiktok_voice: str,
    elevenlabs_voice_id: Optional[str] = None,
    on_log: Optional[Callable[[str, str], None]] = None,
) -> Tuple[List[str], str]:
    """Narrates every sentence with one service, falling back as a whole.

    Args:
        sentences: The lines to speak, in order.
        make_path: Returns a fresh path for the next audio file.
        tiktok_voice: Voice id for the TikTok service.
        elevenlabs_voice_id: Voice id for ElevenLabs, or None to skip it.
        on_log: Optional progress sink.

    Returns:
        (paths, provider) with one path per sentence.

    Raises:
        Exception: Whatever TikTok raises, if the fallback fails too.
    """

    def emit(message: str, level: str = "info") -> None:
        if on_log:
            on_log(message, level)

    provider = choose_provider(elevenlabs_voice_id)

    if provider == ELEVENLABS:
        paths: List[str] = []
        try:
            for sentence in sentences:
                path = make_path()
                elevenlabs_voice.tts(sentence, elevenlabs_voice_id, path)
                paths.append(path)
            return paths, ELEVENLABS
        except Exception as err:
            # Quota runs out mid-video too, so this is not a rare path.
            emit(
                f"[!] ElevenLabs unavailable ({err}). Narrating with TikTok instead.",
                "warning",
            )

    paths = []
    for sentence in sentences:
        path = make_path()
        tiktokvoice.tts(sentence, tiktok_voice, filename=path)
        paths.append(path)
    return paths, TIKTOK
