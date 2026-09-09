"""Chooses which voice service narrates a video, and falls back cleanly.

The fallback is whole-video on purpose. Retrying a single failed sentence on
the other service would splice two different narrators into one video, which
is worse than the cheaper voice throughout.
"""

import re
from typing import Callable, List, Optional, Tuple

import elevenlabs_voice
import tiktokvoice

TIKTOK = "tiktok"
ELEVENLABS = "elevenlabs"


def split_sections(script: str) -> List[str]:
    """The script's paragraph blocks, in order."""
    return [block.strip() for block in re.split(r"\n\s*\n", script or "") if block.strip()]


def split_sentences(block: str) -> List[str]:
    """Sentences within one block.

    Splits on sentence-ending punctuation followed by whitespace, so a chunk
    never spans a paragraph break. The old `script.split(". ")` did: a section
    ending in ".\n\n" did not match the separator, so the last sentence of one
    section and the first of the next were narrated as a single unbroken
    utterance — which is what made the joins sound abrupt.
    """
    parts = re.split(r"(?<=[.!?])\s+", block.strip())
    return [part.strip() for part in parts if part.strip()]


def narration_plan(script: str, by_section: bool) -> List[List[str]]:
    """The script as chunks to narrate, grouped by section.

    One chunk per section reads far better than one per sentence: sent a whole
    paragraph, the voice shapes the rhythm inside it, where sentence-by-sentence
    synthesis gives every sentence the falling intonation of a final one and
    the result sounds clipped. Sentence chunks stay the default for Shorts,
    whose subtitles are timed from the individual clips when AssemblyAI is not
    configured.
    """
    sections = split_sections(script)
    if by_section:
        return [[section] for section in sections]
    return [split_sentences(section) for section in sections]


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
    elevenlabs_model: Optional[str] = None,
    on_log: Optional[Callable[[str, str], None]] = None,
) -> Tuple[List[str], str]:
    """Narrates every sentence with one service, falling back as a whole.

    Args:
        sentences: The lines to speak, in order.
        make_path: Returns a fresh path for the next audio file.
        tiktok_voice: Voice id for the TikTok service.
        elevenlabs_voice_id: Voice id for ElevenLabs, or None to skip it.
        elevenlabs_model: ElevenLabs model id, or None for the module default.
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
            for index, sentence in enumerate(sentences):
                path = make_path()
                elevenlabs_voice.tts(
                    sentence,
                    elevenlabs_voice_id,
                    path,
                    elevenlabs_model or elevenlabs_voice.ELEVENLABS_MODEL,
                    # Context, never synthesized: it carries prosody across the
                    # join so the next chunk does not restart from silence.
                    previous_text=sentences[index - 1] if index else None,
                    next_text=(
                        sentences[index + 1] if index + 1 < len(sentences) else None
                    ),
                )
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
