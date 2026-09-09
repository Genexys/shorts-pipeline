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
    section_count: int
    subtitle_max_chars: int
    voice: str
    elevenlabs_voice_id: Optional[str]
    elevenlabs_model: str
    build_thumbnail: bool
    always_hashtags: tuple
    # How to describe this format to the model writing YouTube metadata.
    metadata_label: str
    # Which VideoMetric column ranks this format. Percentage flatters short
    # videos and punishes long ones, so the two cannot share a metric.
    ranking_metric: str

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
    # About 48 seconds of speech at 150 wpm. Comfortably inside the Shorts
    # limit, and long enough to make a point rather than state one.
    target_words=120,
    # One continuous take; the outline machinery is for long form only.
    section_count=1,
    # Word-by-word captions, the usual Shorts style.
    subtitle_max_chars=10,
    # Not en_us_001. That is the single most recognisable synthetic voice on
    # the internet, and it reads as "content farm" before a word of the script
    # lands. This is only the fallback for when ElevenLabs is unavailable.
    voice="en_male_narration",
    # Max — Elearning and Documentary.
    elevenlabs_voice_id="Gfpl8Yo74Is0W6cPUWWT",
    # Flash bills half a credit per character. Across three Shorts a day that
    # is the difference between fitting a 60k plan and overrunning it, and at
    # this length the cheaper model is hard to tell apart.
    elevenlabs_model="eleven_flash_v2_5",
    # Shorts are chosen from a vertical feed, not from a thumbnail grid, and a
    # 16:9 still does not represent them anyway.
    build_thumbnail=False,
    always_hashtags=("#Shorts",),
    metadata_label="short vertical YouTube video (YouTube Shorts)",
    # The Shorts feed is a swipe test: a viewer either stays past the first
    # seconds or does not, and nothing else about the video matters if they go.
    ranking_metric="average_view_percentage",
)

LONG = VideoFormat(
    name="long",
    width=1920,
    height=1080,
    max_clip_duration=12.0,
    # 800 words runs about 5.4 minutes, which needs 28 distinct clips at the
    # twelve-second cap. Twelve terms of three leaves margin for the overlap
    # between terms and for clips the duration filter rejects.
    search_term_count=12,
    clips_per_term=3,
    # Proportional to SHORT against the shorter frame: 112 * 1080/1920.
    subtitle_font_size=63,
    # Long form ships the .srt as a caption track instead, so YouTube can
    # translate it and viewers can turn it off.
    burn_subtitles=False,
    target_words=800,
    # Length has to come from more ground covered, not longer sections: eight
    # sections of a hundred words each, rather than six of a hundred and thirty.
    section_count=8,
    # Readable subtitle lines rather than single words.
    subtitle_max_chars=42,
    # Deliberately different from the Shorts voice, so the two formats do not
    # sound like the same channel output run twice.
    voice="en_au_002",
    # George: British, labelled narrative_story. Four minutes of obviously
    # synthetic narration is where retention goes to die.
    elevenlabs_voice_id="JBFqnCBsd6RMkjVDRZzb",
    # Same price as v2 multilingual and newer. Minutes of narration are where
    # the better model earns its keep, and long form is few enough to afford it.
    elevenlabs_model="eleven_v3",
    # For a long video the thumbnail decides whether anyone opens it at all.
    build_thumbnail=True,
    # #Shorts on a three-minute landscape video misleads both the viewer and
    # the platform about what it is.
    always_hashtags=(),
    # Calling this a Short in the metadata prompt was costing tag quality: the
    # model wrote for a format the video is not.
    metadata_label="five-minute landscape YouTube video",
    # Seconds, not percent. Click-through rate would be the natural second
    # criterion; the Analytics API does not expose it. See Backend/analytics.py.
    ranking_metric="average_view_duration",
)

FORMATS = {fmt.name: fmt for fmt in (SHORT, LONG)}


def resolve_format(name: Optional[str]) -> VideoFormat:
    """Looks up a format by name.

    Unknown or missing names fall back to SHORT, so a payload written before
    formats existed keeps producing exactly what it produces today.
    """
    return FORMATS.get((name or "").strip().lower(), SHORT)
