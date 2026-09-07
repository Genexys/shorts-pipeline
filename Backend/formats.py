"""Output shapes the pipeline can produce.

One frozen preset per shape, so every hardcoded 9:16 assumption has somewhere
to move to. Nothing reads this yet; wiring happens in later steps.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class VideoFormat:
    """Everything the pipeline needs to know about an output shape."""

    name: str
    width: int
    height: int
    max_clip_duration: float
    search_term_count: int
    clips_per_term: int
    subtitle_font_size: int
    burn_subtitles: bool
    target_words: int
    subtitle_max_chars: int
    voice: str
    elevenlabs_voice_id: Optional[str]
    build_thumbnail: bool

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height

    @property
    def stock_video_count(self) -> int:
        return self.search_term_count * self.clips_per_term


SHORT = VideoFormat(
    name="short",
    width=1080,
    height=1920,
    max_clip_duration=5.0,
    search_term_count=5,
    # Two per term, not one. Five clips capped at 5 s covered 25 s, and Shorts
    # run past 30 s, so footage repeated within a single video by arithmetic.
    clips_per_term=2,
    # 112, not 100: matched by measuring glyph height against the previous
    # MoviePy render. See SUBTITLE_FONT_SIZE in video.py.
    subtitle_font_size=112,
    burn_subtitles=True,
    target_words=90,
    # Word-by-word captions, the usual Shorts style.
    subtitle_max_chars=10,
    voice="en_us_001",
    # Shorts stay on the free service. Thirty seconds of synthetic narration is
    # tolerable, and the paid credits are worth more where minutes are at stake.
    elevenlabs_voice_id=None,
    # Shorts are chosen from a vertical feed, not from a thumbnail grid, and a
    # 16:9 still does not represent them anyway.
    build_thumbnail=False,
)

LONG = VideoFormat(
    name="long",
    width=1920,
    height=1080,
    max_clip_duration=12.0,
    search_term_count=10,
    clips_per_term=2,
    # Proportional to SHORT against the shorter frame: 112 * 1080/1920.
    subtitle_font_size=63,
    # Long form ships the .srt as a caption track instead, so YouTube can
    # translate it and viewers can turn it off.
    burn_subtitles=False,
    target_words=550,
    # Readable subtitle lines rather than single words.
    subtitle_max_chars=42,
    # Deliberately different from the Shorts voice, so the two formats do not
    # sound like the same channel output run twice.
    voice="en_au_002",
    # George: British, labelled narrative_story. Four minutes of obviously
    # synthetic narration is where retention goes to die.
    elevenlabs_voice_id="JBFqnCBsd6RMkjVDRZzb",
    # For a long video the thumbnail decides whether anyone opens it at all.
    build_thumbnail=True,
)

FORMATS = {fmt.name: fmt for fmt in (SHORT, LONG)}


def resolve_format(name: Optional[str]) -> VideoFormat:
    """Looks up a format by name.

    Unknown or missing names fall back to SHORT, so a payload written before
    formats existed keeps producing exactly what it produces today.
    """
    return FORMATS.get((name or "").strip().lower(), SHORT)
