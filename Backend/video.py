import difflib
import os
import re
import subprocess
import unicodedata

from PIL import Image
import uuid

import requests
import srt_equalizer
import assemblyai as aai

from typing import Callable, Dict, List, Optional, Set, Tuple
from pathlib import Path
# Only the audio clip type survives here: the video path is ffmpeg now, and
# AudioFileClip is still what the pipeline hands to generate_subtitles.
from moviepy import AudioFileClip
from dotenv import load_dotenv
from formats import SHORT, VideoFormat
from logstream import log
from thumbnail import score_frame
from search import DOWNLOAD_TIMEOUT
from utils import ENV_FILE, TEMP_DIR, SUBTITLES_DIR, FONTS_DIR

load_dotenv(ENV_FILE)

ASSEMBLY_AI_API_KEY = os.getenv("ASSEMBLY_AI_API_KEY")
FRAME_EPSILON = 1 / 120

# Background-music mix. The bed is attenuated, faded, and then ducked under the
# voice by a sidechain compressor keyed off the voice track, so the music stays
# audible in the gaps instead of sitting at one flat level under the narration.
# The bed is levelled to an absolute target rather than scaled by a fixed
# multiplier. A multiplier cannot work: tracks arrive mastered at very different
# levels, and what decides whether a bed fights the narration is not its overall
# loudness but how much energy it puts in the band the voice occupies. Measured
# across one library, two tracks matched to the same integrated loudness still
# needed gains 4.6 dB apart to sound equally present, and the full spread was
# 10 dB. So each track is measured in the voice band and normalized to this
# target, settled by ear across three renders at -26, -30 and -33 dB.
VOICE_BAND_LOW_HZ = 300
VOICE_BAND_HIGH_HZ = 3000
MUSIC_VOICEBAND_TARGET_LUFS = -33.0

# Used when the measurement pass fails, so a bed is still laid rather than the
# job silently losing its music.
MUSIC_FALLBACK_GAIN_DB = -6.0

# Seconds of the track to analyse. Long enough to be representative, short
# enough that a 20-minute ambient piece does not stall the job.
MUSIC_ANALYSIS_SECONDS = 45

MUSIC_FADE_IN_SECONDS = 1.5
MUSIC_FADE_OUT_SECONDS = 2.0

# A bed must be a texture, not a performance. Left alone, a track with normal
# dynamics loses its melody once attenuated and ducked: only the transients stay
# above the masking threshold, so the music reads as random stabs. dynaudnorm
# evens the track out in a moving window before anything else touches it —
# measured on a real track it took LRA from 4.5 to 3.1 and added 2.5 dB of
# density. f/g are deliberately shorter than the defaults; longer windows level
# too slowly to help inside a 30-second short.
MUSIC_LEVELER = "dynaudnorm=f=250:g=15"

# Sidechain duck. The ratio is moderate on purpose: with the bed already
# levelled, a heavy ratio crushes it back into inaudibility.
MUSIC_DUCK_THRESHOLD = 0.05
MUSIC_DUCK_RATIO = 4

# YouTube normalizes playback to roughly -14 LUFS. Matching it here keeps the
# perceived loudness stable across videos instead of tracking whatever level
# the TTS service happened to return.
LOUDNESS_TARGET_LUFS = -14.0
LOUDNESS_TRUE_PEAK_DB = -1.5
LOUDNESS_RANGE = 11.0

# loudnorm runs its analysis at 192 kHz and hands that rate to whatever follows,
# so the AAC encoder clamped it to its own maximum and every video we have ever
# shipped carries a 96 kHz track. YouTube accepts it silently, which is why it
# went unnoticed; Instagram Reels and Threads both specify AAC at 48 kHz or
# below. Setting it here also stops us storing four times the sample rate the
# voice was ever recorded at — ElevenLabs returns 44.1 kHz.
#
# An output option rather than an aresample filter: appending aresample after
# loudnorm fails with "Cannot select channel layout for the link between
# filters", because the filter is asked to negotiate a layout loudnorm has not
# settled. -ar lets ffmpeg insert the resampler where it already knows both
# ends. Applied at every site that encodes audio, so no path can ship 96 kHz.
#
# Verified on the image's ffmpeg 5.1, not the host's. The host here runs 4.4,
# where the channel-layout API predates the rewrite and the music graph fails
# to negotiate sidechaincompress at any sample rate — a difference that makes
# host-side ffmpeg tests of this pipeline worthless.
DELIVERY_SAMPLE_RATE = 48000
DELIVERY_AUDIO_ARGS = ["-c:a", "aac", "-b:a", "192k", "-ar", str(DELIVERY_SAMPLE_RATE)]

# Every file we have published carries its moov atom after the media data, so a
# player cannot start until it has the whole file. Instagram Reels does not just
# prefer otherwise, it specifies "moov atom at the front of the file", and the
# upload is rejected without it. ffmpeg writes moov last by default because it
# only knows the final index once the media is written; +faststart makes a
# second pass that moves it. Verified on the image's ffmpeg: the atom order goes
# from "ftyp free mdat moov" to "ftyp moov free mdat".
DELIVERY_CONTAINER_ARGS = ["-movflags", "+faststart"]


def save_video(video_url: str, directory: str = str(TEMP_DIR)) -> str:
    """
    Saves a video from a given URL and returns the path to the video.

    Args:
        video_url (str): The URL of the video to save.
        directory (str): The path of the temporary directory to save the video to

    Returns:
        str: The path to the saved video.
    """
    destination = Path(directory).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    video_id = uuid.uuid4()
    video_path = destination / f"{video_id}.mp4"
    with open(video_path, "wb") as f:
        f.write(requests.get(video_url, timeout=DOWNLOAD_TIMEOUT).content)

    return str(video_path)


# Captions. They used to be AssemblyAI's transcript of the narration, so they
# carried its spelling rather than the script's: on 2026-10-02 a runway Short
# captioned "18L–36R" as "ATNL 36R.", and on 2026-09-30 the physicist Jiwon Han
# was captioned "Jiwan". The script is the text, so AssemblyAI is now used for
# timing only: its words are aligned to the narrated script, and the script's
# own words are shown at the times AssemblyAI heard them.

# Under this share of the script's words found word for word in the
# transcript, the two are too far apart to pin one to the other, and
# AssemblyAI's own captions are used as before.
CAPTION_MIN_MATCH = 0.5

# A silence this long clears the caption, as the breaks between AssemblyAI's
# cues did; a shorter one holds the caption until the next word. It is also
# how much of a neighbouring silence a word AssemblyAI missed may borrow.
CAPTION_PAUSE_SECONDS = 0.5

# (text, start seconds, end seconds)
TimedWord = Tuple[str, float, float]


def _srt_timestamp(total_seconds: float) -> str:
    """Seconds in the SRT time format, HH:MM:SS,mmm."""
    milliseconds_total = int(round(total_seconds * 1000))
    hours, remainder = divmod(milliseconds_total, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def _caption_key(token: str) -> str:
    """A word as compared rather than shown: no case, accents or punctuation.

    "17L–35R" in the script and "17L/35R" in the transcript are the same
    word, and so are "number," and "number".
    """
    decomposed = unicodedata.normalize("NFKD", token.casefold())
    return "".join(char for char in decomposed if char.isalnum())


def caption_tokens(sentences: List[str]) -> List[str]:
    """The narrated words as they should be shown, punctuation attached.

    A token with no letters or digits in it, such as a free-standing dash,
    cannot be matched to anything heard, so it rides along with the word
    before it.
    """
    tokens: List[str] = []
    leading = ""
    for token in " ".join(sentences).split():
        if not _caption_key(token):
            if tokens:
                tokens[-1] = f"{tokens[-1]} {token}"
            else:
                leading = f"{leading}{token} "
            continue
        tokens.append(f"{leading}{token}")
        leading = ""
    return tokens


def _spread(tokens: List[str], start: float, end: float) -> List[TimedWord]:
    """Shares a stretch of time between tokens in proportion to their length."""
    weights = [max(len(token), 1) for token in tokens]
    total = sum(weights)
    timed: List[TimedWord] = []
    cursor = start
    for token, weight in zip(tokens, weights):
        share = (end - start) * weight / total
        timed.append((token, cursor, cursor + share))
        cursor += share
    return timed


def _borrow_for_missed(
    tokens: List[str], times: List[Optional[Tuple[float, float]]]
) -> None:
    """Times the script words AssemblyAI did not hear, in place.

    Each run of them shares the time of the word before it, plus up to
    CAPTION_PAUSE_SECONDS of the silence that follows; at the very start, the
    word after it and the silence before. Never called with every token
    missing: the alignment is rejected long before that.
    """
    index = 0
    while index < len(tokens):
        if times[index] is not None:
            index += 1
            continue
        stop = index
        while stop < len(tokens) and times[stop] is None:
            stop += 1
        if index > 0:
            first, last = index - 1, stop
            start = times[first][0]
            end = times[first][1] + CAPTION_PAUSE_SECONDS
            if stop < len(tokens):
                end = max(times[first][1], min(end, times[stop][0]))
        else:
            first, last = index, stop + 1
            start = max(0.0, times[stop][0] - CAPTION_PAUSE_SECONDS)
            end = times[stop][1]
        for offset, (_, word_start, word_end) in enumerate(
            _spread(tokens[first:last], start, end)
        ):
            times[first + offset] = (word_start, word_end)
        index = stop


def align_script_to_words(
    tokens: List[str], words: List[Tuple[str, int, int]]
) -> Optional[List[TimedWord]]:
    """The script's own words, timed from what AssemblyAI heard.

    Pure. `tokens` is caption_tokens() of the narrated sentences; `words` is
    AssemblyAI's transcript as (text, start ms, end ms). The two are matched on
    _caption_key with difflib:

    - where they agree, the script's word takes the transcript's timing;
    - where they differ ("18L–36R." heard as "ATNL 36R."), the script's words
      share the time the transcript's words took;
    - words AssemblyAI added are dropped;
    - script words it missed borrow time from their neighbours.

    Returns None when there is nothing to align, or when under
    CAPTION_MIN_MATCH of the script's words were heard as written.
    """
    if not tokens or not words:
        return None
    matcher = difflib.SequenceMatcher(
        None,
        [_caption_key(token) for token in tokens],
        [_caption_key(text) for text, _, _ in words],
        # Common words are what pin a long script to its transcript; the
        # heuristic would ignore them past 200 tokens.
        autojunk=False,
    )
    times: List[Optional[Tuple[float, float]]] = [None] * len(tokens)
    matched = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            matched += i2 - i1
            for offset in range(i2 - i1):
                _, start, end = words[j1 + offset]
                times[i1 + offset] = (start / 1000, end / 1000)
        elif tag == "replace":
            spread = _spread(
                tokens[i1:i2], words[j1][1] / 1000, words[j2 - 1][2] / 1000
            )
            for offset, (_, start, end) in enumerate(spread):
                times[i1 + offset] = (start, end)
    if matched < CAPTION_MIN_MATCH * len(tokens):
        return None
    _borrow_for_missed(tokens, times)
    return [(token, start, end) for token, (start, end) in zip(tokens, times)]


def build_caption_srt(timed: List[TimedWord], max_chars: int) -> str:
    """SRT cues of at most `max_chars`, each timed from its own words.

    The words are packed with the greedy rule srt_equalizer re-wraps with,
    so the lines are the ones viewers saw before. The cut is made here rather
    than left to srt_equalizer because it times each piece of a cue by its
    share of the characters, and AssemblyAI's cues run three or four seconds:
    on the runway Short "About 4" appeared 1.4 s before it was said, because
    "172.5" before it takes a second and a half to say and five characters to
    write. Over that video and the coffee one, a caption was on average 0.3 s
    away from its words.

    A cue never runs across a silence of CAPTION_PAUSE_SECONDS. Inside speech
    it stays up until the next one starts, so the captions do not blink
    between words.
    """
    groups: List[List[TimedWord]] = []
    for word in timed:
        if groups:
            current = groups[-1]
            text = " ".join(token for token, _, _ in current)
            paused = word[1] - current[-1][2] >= CAPTION_PAUSE_SECONDS
            # srt_equalizer's own test, trailing space included.
            if not paused and len(text) + 1 + len(word[0]) + 1 <= max_chars:
                current.append(word)
                continue
        groups.append([word])

    cues = []
    for index, group in enumerate(groups):
        start, end = group[0][1], group[-1][2]
        if index + 1 < len(groups):
            following = groups[index + 1][0][1]
            if following - end < CAPTION_PAUSE_SECONDS:
                end = following
        # srt drops a cue that ends where it starts, which at millisecond
        # precision a very short word can.
        end = max(end, start + 0.001)
        text = " ".join(token for token, _, _ in group)
        cues.append(
            f"{len(cues) + 1}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{text}\n"
        )
    return "\n".join(cues)


def __generate_subtitles_assemblyai(
    audio_path: str,
    voice: str,
    sentences: Optional[List[str]] = None,
    max_chars: int = SHORT.subtitle_max_chars,
) -> str:
    """
    Generates subtitles from a given audio file and returns the path to the subtitles.

    The text is the narrated script's; AssemblyAI supplies the timing. When its
    words cannot be lined up with the script, its own captions are used.

    Args:
        audio_path (str): The path to the audio file to generate subtitles from.
        voice (str): The voice's language prefix.
        sentences (List[str]): What was narrated, in order.
        max_chars (int): Characters per caption.

    Returns:
        str: The generated subtitles
    """

    language_mapping = {
        "br": "pt",
        "id": "en",  # AssemblyAI doesn't have Indonesian
        "jp": "ja",
        "kr": "ko",
    }

    if voice in language_mapping:
        lang_code = language_mapping[voice]
    else:
        lang_code = voice

    aai.settings.api_key = ASSEMBLY_AI_API_KEY
    config = aai.TranscriptionConfig(language_code=lang_code)
    transcriber = aai.Transcriber(config=config)
    transcript = transcriber.transcribe(audio_path)

    heard = [(word.text, word.start, word.end) for word in transcript.words or []]
    timed = align_script_to_words(caption_tokens(sentences or []), heard)
    if timed is None:
        log(
            "[!] AssemblyAI's words do not line up with the script; using its "
            "own captions.",
            "warning",
        )
        return transcript.export_subtitles_srt()

    log("[+] Captions use the script's words on AssemblyAI's timing.", "info")
    return build_caption_srt(timed, max_chars)


def __generate_subtitles_locally(
    sentences: List[str], audio_clips: List[AudioFileClip]
) -> str:
    """
    Generates subtitles from a given audio file and returns the path to the subtitles.

    Args:
        sentences (List[str]): all the sentences said out loud in the audio clips
        audio_clips (List[AudioFileClip]): all the individual audio clips which will make up the final audio track
    Returns:
        str: The generated subtitles
    """

    start_time = 0
    subtitles = []

    for i, (sentence, audio_clip) in enumerate(zip(sentences, audio_clips), start=1):
        duration = audio_clip.duration
        end_time = start_time + duration

        # Format: subtitle index, start time --> end time, sentence
        subtitle_entry = f"{i}\n{_srt_timestamp(start_time)} --> {_srt_timestamp(end_time)}\n{sentence}\n"
        subtitles.append(subtitle_entry)

        start_time += duration  # Update start time for the next subtitle

    return "\n".join(subtitles)


def generate_subtitles(
    audio_path: str,
    sentences: List[str],
    audio_clips: List[AudioFileClip],
    voice: str,
    max_chars: int = SHORT.subtitle_max_chars,
) -> str:
    """
    Generates subtitles from a given audio file and returns the path to the subtitles.

    Args:
        audio_path (str): The path to the audio file to generate subtitles from.
        sentences (List[str]): all the sentences said out loud in the audio clips
        audio_clips (List[AudioFileClip]): all the individual audio clips which will make up the final audio track
        max_chars (int): Characters per subtitle cue after re-wrapping.

    Returns:
        str: The path to the generated subtitles.
    """

    def equalize_subtitles(srt_path: str, width: int) -> None:
        # Re-wrap the cues. Shorts want one word at a time; longer videos want
        # readable lines. Captions aligned to the script arrive cut to this
        # width already (build_caption_srt), and pass through unchanged.
        srt_equalizer.equalize_srt_file(srt_path, srt_path, width)

    # Save subtitles
    SUBTITLES_DIR.mkdir(parents=True, exist_ok=True)
    subtitles_path = SUBTITLES_DIR / f"{uuid.uuid4()}.srt"

    if ASSEMBLY_AI_API_KEY is not None and ASSEMBLY_AI_API_KEY != "":
        log("[+] Creating subtitles using AssemblyAI", "info")
        subtitles = __generate_subtitles_assemblyai(
            audio_path, voice, sentences, max_chars
        )
    else:
        log("[+] Creating subtitles locally", "info")
        subtitles = __generate_subtitles_locally(sentences, audio_clips)
        # print(colored("[-] Local subtitle generation has been disabled for the time being.", "red"))
        # print(colored("[-] Exiting.", "red"))
        # sys.exit(1)

    with open(subtitles_path, "w", encoding="utf-8") as file:
        file.write(subtitles)

    # Equalize subtitles
    equalize_subtitles(str(subtitles_path), max_chars)

    log("[+] Subtitles generated.", "success")

    return str(subtitles_path)


# Shot rhythm. Every shot used to run exactly the same length, which reads as
# machine-cut however varied the footage is. These multiply the nominal shot
# length and average to 1.0, so the shot count and total duration are unchanged.
SHOT_RHYTHM = (1.0, 0.8, 1.2)

# Shorter than this and a shot reads as a flicker rather than a cut.
MIN_SHOT_SECONDS = 0.9
# A shot this much shorter than its neighbours reads as a mistake even when it
# is seconds long: two seconds among ten-second shots is as jarring as a
# quarter-second among three-second ones.
SHORT_SHOT_FRACTION = 0.5

# A stock library does not always have enough distinct clips for a long video,
# especially on a narrow subject. Holding each shot longer is better than
# showing the same footage twice — but only up to a point, past which a static
# shot is worse than a repeat.
MAX_ADAPTIVE_SHOT_SECONDS = 20.0

# Slow camera moves, cycled per shot so neighbours never move the same way.
# Static stock footage cut together is what the monetization policy calls an
# image slideshow; a drift across the frame reads as a deliberate edit.
# Values are per-frame zoom steps at 30 fps, kept small enough to be felt
# rather than seen.
MOTION_ZOOM_STEP = 0.0008
MOTION_MAX_ZOOM = 1.14
# Pans hold a fixed slight zoom, which is the headroom they slide across.
MOTION_PAN_ZOOM = 1.10
MOTION_STYLES = ("push_in", "pull_out", "pan_right", "pan_left")

# ASS alignment values (numpad layout) for the UI's vertical positions.
SUBTITLE_ALIGNMENT = {"top": 8, "center": 5, "bottom": 2}
SUBTITLE_TOP_MARGIN_PX = 80
SUBTITLE_SIDE_MARGIN_PX = 60
SUBTITLE_FONT_NAME = "The Bold Font"
SUBTITLE_OUTLINE = 5
# The bundled font draws its lowercase letters as capitals, so captions read as
# all caps. It has no Ä, Ö, Ü, Ñ or Ç, though, and libass borrows those from a
# fallback font exactly as typed: "Pääbo" came out as "PääBO". Upper-casing the
# text changes nothing for the font's own letters and makes the borrowed ones
# capitals too. Override tags and the \N, \n and \h escapes are left alone.
ASS_UNTOUCHED = re.compile(r"(\{[^}]*\}|\\[Nnh])")


def effective_clip_cap(
    duration: float,
    clip_count: int,
    base_cap: float,
    ceiling: float = MAX_ADAPTIVE_SHOT_SECONDS,
) -> float:
    """The per-shot cap to actually use, given how much footage arrived.

    Searching does not always return what the format asked for. Rather than
    cycling back through the clips, each shot is held longer so the footage
    stretches to cover the runtime — up to `ceiling`, beyond which a shot
    outstays its welcome more than a repeat would.
    """
    if clip_count <= 0:
        return base_cap
    return min(max(base_cap, duration / clip_count), ceiling)

def _fit_rhythm(
    rhythm: Tuple[float, ...], required: float, max_clip_duration: float
) -> Tuple[float, ...]:
    """Compresses the rhythm so its longest beat still fits under the cap.

    Without this the cap truncates the long beats while the short ones stay,
    so the average shot comes out under `required` and the run needs one more
    shot than there are clips — which means footage repeats. Compressing
    toward 1.0 keeps the variation as wide as the cap allows and the average
    at exactly 1.0.
    """
    if not rhythm or required <= 0:
        return rhythm
    longest = max(rhythm)
    if longest <= 1.0:
        return rhythm
    head_room = max_clip_duration / required
    if longest <= head_room:
        return rhythm
    scale = max(0.0, (head_room - 1.0) / (longest - 1.0))
    return tuple(1.0 + (beat - 1.0) * scale for beat in rhythm)

def plan_clip_segments(
    sources: List[Tuple[str, float]],
    max_duration: float,
    max_clip_duration: float,
    rhythm: Tuple[float, ...] = SHOT_RHYTHM,
) -> List[Tuple[str, float]]:
    """Chooses which clip to show for how long, cycling until the audio is covered.

    Pure, so the selection rule can be tested without touching ffmpeg.

    Args:
        sources: (path, source duration) pairs, in the order they should cycle.
        max_duration: Total duration to fill, normally the voiceover length.
        max_clip_duration: Longest single segment.
        rhythm: Multipliers cycled over the shots so they are not all the same
            length. Must average 1.0 or the shot count drifts.

    Returns:
        (path, segment duration) pairs whose durations sum to max_duration.

    Raises:
        ValueError: If no sources were given.
        RuntimeError: If no source is long enough to make progress.
    """
    if not sources:
        raise ValueError("No source videos were provided for concatenation.")

    required = max_duration / len(sources)
    rhythm = _fit_rhythm(rhythm, required, max_clip_duration)
    segments: List[Tuple[str, float]] = []
    total = 0.0

    while total < (max_duration - FRAME_EPSILON):
        progressed = False
        for path, source_duration in sources:
            remaining = max_duration - total
            if remaining <= FRAME_EPSILON:
                break
            usable = source_duration - FRAME_EPSILON
            if usable <= 0:
                continue
            beat = rhythm[len(segments) % len(rhythm)] if rhythm else 1.0
            target = min(required * beat, max_clip_duration, remaining, usable)
            if target <= 0:
                continue
            segments.append((path, target))
            total += target
            progressed = True
        if not progressed:
            raise RuntimeError("Could not reach target duration from source videos.")

    return _absorb_trailing_sliver(
        segments, sources, required, max_clip_duration
    )


def _absorb_trailing_sliver(
    segments: List[Tuple[str, float]],
    sources: List[Tuple[str, float]],
    required: float = 0.0,
    max_clip_duration: float = float("inf"),
) -> List[Tuple[str, float]]:
    """Folds a too-short final shot into the one before it.

    The rhythm cycle rarely divides the clip count evenly, so the last partial
    cycle leaves a remainder. As its own shot that remainder is both visually
    wrong and one shot too many, which pulls in an extra clip and makes the
    footage repeat.

    Only folds when the earlier shot can take the extra time without passing
    the per-shot cap or running past its own source; otherwise the short shot
    stays, which beats either.
    """
    if len(segments) < 2:
        return segments

    threshold = max(MIN_SHOT_SECONDS, required * SHORT_SHOT_FRACTION)
    last_path, last_duration = segments[-1]
    if last_duration >= threshold:
        return segments

    previous_path, previous_duration = segments[-2]
    merged = previous_duration + last_duration
    usable = dict(sources).get(previous_path, 0.0) - FRAME_EPSILON
    if merged > usable or merged > max_clip_duration:
        return segments

    return segments[:-2] + [(previous_path, previous_duration + last_duration)]


def build_motion_filter(style: str, fmt: VideoFormat, frames: int) -> str:
    """A slow camera move over one shot, as a zoompan expression.

    Everything is expressed against `on`, the output frame counter, not against
    zoompan's own `zoom` accumulator. With d=1 — which is what makes zoompan
    advance one output frame per input frame instead of holding a still —
    `zoom` restarts at 1 for every input frame, so an expression like
    zoom+0.0008 never grows and the filter silently does nothing.

    Args:
        style (str): One of MOTION_STYLES.
        fmt (VideoFormat): Output shape.
        frames (int): Length of this shot in frames, so the move finishes with it.

    Returns:
        str: A zoompan filter string.
    """
    span = max(frames, 1)
    # Reach the same amount of movement whatever the shot length, so short
    # shots are not left looking static.
    step = (MOTION_MAX_ZOOM - 1.0) / span
    centre_x = "iw/2-(iw/zoom/2)"
    centre_y = "ih/2-(ih/zoom/2)"

    if style == "pull_out":
        zoom = f"max({MOTION_MAX_ZOOM}-{step:.6f}*on,1.0)"
        x, y = centre_x, centre_y
    elif style == "pan_right":
        zoom = f"{MOTION_PAN_ZOOM}"
        x, y = f"(iw-iw/zoom)*on/{span}", centre_y
    elif style == "pan_left":
        zoom = f"{MOTION_PAN_ZOOM}"
        x, y = f"(iw-iw/zoom)*(1-on/{span})", centre_y
    else:  # push_in
        zoom = f"min(1+{step:.6f}*on,{MOTION_MAX_ZOOM})"
        x, y = centre_x, centre_y

    return f"zoompan=z='{zoom}':x='{x}':y='{y}':d=1:s={fmt.width}x{fmt.height}:fps=30"


def build_fade_filter(fmt: VideoFormat, duration: float) -> str:
    """Fade in from and out to black, or "" when this format wants neither.

    Applied here rather than in the final render because this pass already
    re-encodes; long form copies its video stream afterwards, and adding a fade
    there would mean re-encoding five minutes of finished footage for the sake
    of two seconds of it.
    """
    parts = []
    if fmt.video_fade_in_seconds > 0:
        parts.append(f"fade=t=in:st=0:d={fmt.video_fade_in_seconds:.3f}")
    if fmt.video_fade_out_seconds > 0:
        start = max(0.0, duration - fmt.video_fade_out_seconds)
        parts.append(
            f"fade=t=out:st={start:.3f}:d={fmt.video_fade_out_seconds:.3f}"
        )
    return ",".join(parts)


def build_crop_filter(fmt: VideoFormat = SHORT) -> str:
    """Centre-crops any frame to the format's ratio.

    The crop is an expression rather than arithmetic in Python, so one filter
    handles both cases and no source has to be probed for its dimensions:
    footage narrower than the target is cut top and bottom, anything wider is
    cut at the sides. Both stay centred.
    """
    ratio = fmt.aspect_ratio
    return (
        f"crop=w='if(lt(iw/ih,{ratio}),iw,ih*{ratio})'"
        f":h='if(lt(iw/ih,{ratio}),iw/{ratio},ih)'"
        ":x='(iw-ow)/2':y='(ih-oh)/2'"
    )


def build_concat_filter(
    segment_count: int,
    fmt: VideoFormat = SHORT,
    durations: Optional[List[float]] = None,
) -> str:
    """Crops each segment to the format's ratio, scales it, then concatenates."""
    crop = build_crop_filter(fmt)
    chains = []
    for index in range(segment_count):
        seconds = durations[index] if durations else 4.0
        motion = build_motion_filter(
            MOTION_STYLES[index % len(MOTION_STYLES)], fmt, int(seconds * 30)
        )
        chains.append(
            f"[{index}:v]{crop},scale={fmt.width}:{fmt.height},setsar=1,fps=30,"
            f"{motion},setsar=1,setpts=PTS-STARTPTS[v{index}]"
        )
    inputs = "".join(f"[v{index}]" for index in range(segment_count))
    total = sum(durations) if durations else segment_count * 4.0
    fades = build_fade_filter(fmt, total)
    label = "[vfaded]" if fades else "[vout]"
    chains.append(f"{inputs}concat=n={segment_count}:v=1:a=0{label}")
    if fades:
        chains.append(f"[vfaded]{fades}[vout]")
    return ";".join(chains)


# Black openings. Every shot used to start on its clip's first frame, and stock
# clips often open on a fade up from black. blackdetect over the 25 videos
# rendered from 2026-09-27 to 10-03 found black in ten, and every span was black
# in that clip's own footage: in nine of the ten it began on a shot's first
# frame, and the rest were clips that dip to black part way in. The worst,
# ffbe3dc8, showed 2.7 s of pure black under the subtitles: its flower clip
# stays black for 4.7 s, longer than the whole shot. The check runs on the
# picture as it will be framed, because the crop decides what is left: a
# 1920x426 Pixabay clip measured 0.9 s of black at full width and was black for
# its entire 2.7 s shot once cut to 9:16.
BLACK_PROBE_SECONDS = 4.0
# blackdetect's own threshold, and the one the renders were audited with.
BLACK_PIXEL_THRESHOLD = 0.10
# Shortest black worth acting on at the start of a clip. Below this it passes
# as a cut.
BLACK_MIN_SECONDS = 0.2
# How near the edge of the probed window a span must reach to count as running
# into it: blackdetect reports a lead-in from 0 and ends a span that runs off
# the window up to a frame before its end.
BLACK_EDGE_SECONDS = 0.1
# A finished video is audited for black that lasts at least this long.
RENDER_BLACK_MIN_SECONDS = 0.5
BLACK_PROBE_TIMEOUT_SECONDS = 60


def parse_black_spans(ffmpeg_stderr: str) -> List[Tuple[float, float]]:
    """The (start, end) pairs blackdetect printed, in seconds."""
    return [
        (float(start), float(end))
        for start, end in re.findall(
            r"black_start:\s*(-?[\d.]+)\s+black_end:\s*(-?[\d.]+)", ffmpeg_stderr or ""
        )
    ]


def detect_black_spans(
    path: str,
    seconds: float,
    fmt: VideoFormat = SHORT,
    min_seconds: float = BLACK_MIN_SECONDS,
) -> Optional[List[Tuple[float, float]]]:
    """Stretches of black in the first `seconds` of a video, as the format frames it.

    Returns None if the check could not run. It is advisory, so a failure
    leaves the footage to be used as it is rather than costing the render.
    """
    command = [
        _ffmpeg_binary(), "-hide_banner", "-nostats",
        "-t", f"{seconds:.3f}", "-i", path,
        "-an",
        "-vf",
        f"{build_crop_filter(fmt)},"
        f"blackdetect=d={min_seconds}:pix_th={BLACK_PIXEL_THRESHOLD}",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(
            command, check=True, capture_output=True, text=True,
            timeout=max(BLACK_PROBE_TIMEOUT_SECONDS, seconds),
        )
        return parse_black_spans(result.stderr)
    except Exception as err:
        log(f"[!] Could not check {Path(path).name} for black: {err}", "warning")
        return None


def usable_footage(
    spans: List[Tuple[float, float]], probed: float, duration: float
) -> Optional[Tuple[float, float]]:
    """Where a clip's shot should start and how much of it may be used.

    Pure, so the rule can be tested without ffmpeg. `spans` is the black found
    in the first `probed` seconds of a clip `duration` long.

    Black from the first frame is a fade-in: the shot starts after it. Black
    running off the end of the window is a fade-out, or black that may go on
    past where we looked: the shot stops before it. Black that comes and goes
    inside the window means the clip dips to black, and it does it again where
    we did not look — the filament-bulb clip in 5ae3b3fc flickered off at 0,
    2.8 and 6.8 seconds, and a skeleton on a black ground in 0257be12 dipped at
    2.3 and again at 8.3 — so the clip is not used. Nor is one that is black
    for the whole window, or that leaves too little to make a shot.

    Returns:
        (start offset, usable seconds), or None to drop the clip.
    """
    start, end = 0.0, duration
    for black_start, black_end in spans:
        leading = black_start <= BLACK_EDGE_SECONDS
        trailing = black_end >= probed - BLACK_EDGE_SECONDS
        if leading and trailing:
            return None
        if leading:
            start = max(start, black_end)
        elif trailing:
            end = min(end, black_start)
        else:
            return None
    if end - start < MIN_SHOT_SECONDS:
        return None
    return start, end - start


def find_usable_footage(
    video_paths: List[str], fmt: VideoFormat = SHORT, keep_all_if_none: bool = True
) -> Dict[str, Tuple[float, float]]:
    """Each usable clip's (start offset, usable seconds), keyed by path.

    Clips that are black where it matters are left out. If that would leave
    nothing, every clip is used from its first frame as before: a shot that
    opens on black beats a failed render. Not so for `keep_all_if_none=False`,
    which is for replacement clips: the video already has footage, and a
    replacement that opens on black is no replacement.
    """
    durations = {path: probe_duration(path) for path in video_paths}
    footage: Dict[str, Tuple[float, float]] = {}
    for path in video_paths:
        duration = durations[path]
        probed = min(BLACK_PROBE_SECONDS, duration)
        spans = detect_black_spans(path, probed, fmt)
        if spans is None:
            footage[path] = (0.0, duration)
            continue
        window = usable_footage(spans, probed, duration)
        if window is None:
            shown = ", ".join(f"{start:.2f}-{end:.2f}s" for start, end in spans)
            log(f"[!] Not using {Path(path).name}: black at {shown}.", "warning")
            continue
        if window[0] > 0:
            log(
                f"[+] Starting {Path(path).name} at {window[0]:.2f}s, after its "
                "black opening.",
                "info",
            )
        footage[path] = window

    if not footage and video_paths and keep_all_if_none:
        log(
            "[!] Every clip is black where it would play; using them as they are.",
            "warning",
        )
        return {path: (0.0, durations[path]) for path in video_paths}
    return footage


def report_black_spans(
    video_path: str, duration: float, fmt: VideoFormat = SHORT
) -> List[Tuple[float, float]]:
    """Warns about every stretch of black in a combined video. Advisory only.

    Run on the combined picture, so a regression in the opening check, or a
    kind of black it does not catch, shows up in the job log rather than in a
    published video.
    """
    spans = detect_black_spans(
        video_path, duration + 1.0, fmt, RENDER_BLACK_MIN_SECONDS
    )
    if spans is None:
        return []
    if spans:
        shown = ", ".join(f"{start:.2f}-{end:.2f}s" for start, end in spans)
        log(f"[!] Black in the combined video at {shown}.", "warning")
    return spans


def combine_videos(
    video_paths: List[str],
    max_duration: float,
    threads: int,
    fmt: VideoFormat = SHORT,
    on_segments: Optional[Callable[[List[Tuple[str, float]]], None]] = None,
    footage: Optional[Dict[str, Tuple[float, float]]] = None,
) -> str:
    """
    Combines stock clips into one video of the format's shape and the requested
    duration.

    Runs entirely in ffmpeg. The previous MoviePy implementation moved every
    frame through Python to crop and resize it, which cost roughly 40x what the
    same work costs inside ffmpeg's filter graph.

    Args:
        video_paths (List): A list of paths to the videos to combine.
        max_duration (float): The maximum duration of the combined video.
        threads (int): Threads for the encoder.
        fmt (VideoFormat): Output shape and the per-clip duration cap.
        on_segments: Told the planned (path, seconds) sequence before encoding,
            so the caller can record which footage plays when.
        footage: find_usable_footage() for these clips, when the caller has
            already measured it. Clips missing from it are not used.

    Returns:
        str: The path to the combined video.
    """
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    combined_video_path = TEMP_DIR / f"{uuid.uuid4()}.mp4"

    if footage is None:
        footage = find_usable_footage(video_paths, fmt)
    sources = [(path, footage[path][1]) for path in video_paths if path in footage]
    starts = {path: start for path, (start, _) in footage.items()}
    cap = effective_clip_cap(
        float(max_duration), len(sources), float(fmt.max_clip_duration)
    )
    if cap > fmt.max_clip_duration:
        log(
            f"[!] Only {len(sources)} clips for {max_duration:.0f}s; holding each "
            f"shot up to {cap:.1f}s instead of {fmt.max_clip_duration:.0f}s "
            f"rather than repeating footage.",
            "warning",
        )
    segments = plan_clip_segments(sources, float(max_duration), cap)
    if on_segments:
        on_segments(list(segments))

    log("[+] Combining videos...", "info")
    log(f"[+] {len(segments)} segments covering {max_duration:.1f}s.", "info")

    command = [_ffmpeg_binary(), "-y"]
    for path, duration in segments:
        start = starts.get(path, 0.0)
        if start > 0:
            # An input seek, so the decoder skips the black instead of the
            # filter graph receiving and discarding it.
            command += ["-ss", f"{start:.3f}"]
        command += ["-t", f"{duration:.3f}", "-i", path]
    command += [
        "-filter_complex",
        build_concat_filter(
            len(segments), fmt, [duration for _, duration in segments]
        ),
        "-map",
        "[vout]",
        "-an",
        *encoder_args(threads, final=False),
    ]
    command.append(str(combined_video_path))

    subprocess.run(command, check=True, capture_output=True, text=True)
    report_black_spans(str(combined_video_path), float(max_duration), fmt)
    return str(combined_video_path)


def ass_colour(hex_colour: str) -> str:
    """#RRGGBB to the ASS &HAABBGGRR form. Unparseable input falls back to yellow."""
    value = (hex_colour or "").strip().lstrip("#")
    if len(value) != 6:
        return "&H0000FFFF"
    try:
        red, green, blue = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return "&H0000FFFF"
    return f"&H00{blue:02X}{green:02X}{red:02X}"


def build_style_line(
    subtitles_position: str, text_colour: str, fmt: VideoFormat = SHORT
) -> str:
    """The ASS `Style:` line for the subtitles.

    Written into the script rather than passed as force_style, because the size
    only means anything alongside the PlayRes the script declares.
    """
    _, _, vertical = (subtitles_position or "center,center").partition(",")
    alignment = SUBTITLE_ALIGNMENT.get(vertical.strip().lower(), 5)
    margin_v = 0 if alignment == 5 else SUBTITLE_TOP_MARGIN_PX
    fields = [
        "Default",
        SUBTITLE_FONT_NAME,
        str(fmt.subtitle_font_size),
        ass_colour(text_colour),   # PrimaryColour
        ass_colour(text_colour),   # SecondaryColour
        "&H00000000",              # OutlineColour
        "&H00000000",              # BackColour
        "-1",                      # Bold
        "0", "0", "0",             # Italic, Underline, StrikeOut
        "100", "100",              # ScaleX, ScaleY
        "0", "0",                  # Spacing, Angle
        "1",                       # BorderStyle: outline
        str(SUBTITLE_OUTLINE),
        "0",                       # Shadow
        str(alignment),
        str(SUBTITLE_SIDE_MARGIN_PX),
        str(SUBTITLE_SIDE_MARGIN_PX),
        str(margin_v),
        "1",                       # Encoding
    ]
    return "Style: " + ",".join(fields)


def uppercase_caption(text: str) -> str:
    """A dialogue line's text in capitals, its override tags and escapes intact."""
    return "".join(
        part if ASS_UNTOUCHED.fullmatch(part) else part.upper()
        for part in ASS_UNTOUCHED.split(text)
    )


def uppercase_dialogue(line: str) -> str:
    """A `Dialogue:` line with its text, the field after the ninth comma, in capitals."""
    fields = line.split(",", 9)
    if len(fields) < 10:
        return line
    fields[9] = uppercase_caption(fields[9])
    return ",".join(fields)


def _ass_centiseconds(timestamp: str) -> int:
    """An ASS time, H:MM:SS.cc, in centiseconds."""
    hours, minutes, seconds = timestamp.strip().split(":")
    whole, _, fraction = seconds.partition(".")
    return (int(hours) * 3600 + int(minutes) * 60 + int(whole)) * 100 + int(
        (fraction + "00")[:2]
    )


def end_dialogue_before_next(lines: List[str]) -> List[str]:
    """The script's lines, with any `Dialogue:` that runs into the next one cut short.

    ffmpeg writes an SRT cue into ASS in centiseconds, rounding its start and
    its duration separately, so a cue can end 0.01 s after the next one
    begins: 00:00:26,007 --> 00:00:26,994 becomes 26.01 to 27.00, while the
    next cue starts at 26.99. A frame inside that hundredth of a second shows
    both, libass moves the newer one out of the older one's way, and the newer
    one keeps that lower place until it ends. In the four Shorts of 2026-10-04
    and 2026-10-05, seven captions jumped 120 px down like this, "EXPONENTIAL"
    and "MOVES IN," among them.

    Captions never overlap on purpose, so each line now ends no later than the
    next one starts. A line that starts at the same time as the next, or a time
    that does not parse, is left as it is.
    """
    ended = list(lines)
    dialogue = [i for i, line in enumerate(lines) if line.lstrip().startswith("Dialogue:")]
    for current, following in zip(dialogue, dialogue[1:]):
        fields = ended[current].split(",", 9)
        next_fields = lines[following].split(",", 9)
        if len(fields) < 10 or len(next_fields) < 10:
            continue
        try:
            start, end, next_start = (
                _ass_centiseconds(fields[1]),
                _ass_centiseconds(fields[2]),
                _ass_centiseconds(next_fields[1]),
            )
        except ValueError:
            continue
        if start < next_start < end:
            fields[2] = next_fields[1]
            ended[current] = ",".join(fields)
    return ended


def patch_ass_script(
    script: str, subtitles_position: str, text_colour: str, fmt: VideoFormat = SHORT
) -> str:
    """Sets the script's resolution and replaces its style definition.

    ffmpeg converts SRT to ASS with PlayResX/Y of 384x288. Font sizes are
    relative to that, so a size meant for a 1920-tall frame is scaled up by
    almost seven and the text runs off the screen. Declaring the real frame
    size makes the size mean pixels.

    The dialogue is upper-cased on the way through (see ASS_UNTOUCHED), and
    each line ends where the next begins (see end_dialogue_before_next).
    """
    lines = []
    seen_play_res_x = seen_play_res_y = False
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("PlayResX:"):
            lines.append(f"PlayResX: {fmt.width}")
            seen_play_res_x = True
        elif stripped.startswith("PlayResY:"):
            lines.append(f"PlayResY: {fmt.height}")
            seen_play_res_y = True
        elif stripped.startswith("Style:"):
            lines.append(build_style_line(subtitles_position, text_colour, fmt))
        elif stripped.startswith("Dialogue:"):
            lines.append(uppercase_dialogue(line))
        else:
            lines.append(line)
            if stripped.startswith("[Script Info]") and not (
                seen_play_res_x and seen_play_res_y
            ):
                lines.append(f"PlayResX: {fmt.width}")
                lines.append(f"PlayResY: {fmt.height}")
                seen_play_res_x = seen_play_res_y = True
    return "\n".join(end_dialogue_before_next(lines)) + "\n"


def prepare_ass_subtitles(
    subtitles_path: str,
    subtitles_position: str,
    text_colour: str,
    fmt: VideoFormat = SHORT,
) -> str:
    """Converts the .srt to a styled .ass sized for the real frame."""
    ass_path = TEMP_DIR / f"{uuid.uuid4()}.ass"
    subprocess.run(
        [_ffmpeg_binary(), "-y", "-i", str(subtitles_path), str(ass_path)],
        check=True, capture_output=True, text=True,
    )
    ass_path.write_text(
        patch_ass_script(
            ass_path.read_text(encoding="utf-8"), subtitles_position, text_colour, fmt
        ),
        encoding="utf-8",
    )
    return str(ass_path)


def build_render_command(
    combined_video_path: str,
    tts_path: str,
    subtitles_path: str,
    output_path: str,
    threads: int,
    subtitles_position: str,
    text_color: str,
    fmt: VideoFormat = SHORT,
) -> List[str]:
    """The ffmpeg command that attaches the voiceover, burning subtitles if asked.

    When the format does not burn subtitles there is nothing to draw, so the
    video stream is copied rather than re-encoded: the picture already left
    combine_videos in the right shape and codec.
    """
    command = [
        _ffmpeg_binary(),
        "-y",
        "-i",
        str(combined_video_path),
        "-i",
        tts_path,
    ]

    if fmt.burn_subtitles:
        # libass needs the font by family name, and finds it only if told where
        # to look; the file lives outside any system font directory.
        ass_path = prepare_ass_subtitles(
            subtitles_path, subtitles_position, text_color, fmt
        )
        command += ["-vf", f"ass='{ass_path}':fontsdir='{FONTS_DIR}'"]
        video_args = encoder_args(threads, final=True)
    else:
        video_args = ["-c:v", "copy"]

    command += [
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        *video_args,
        *DELIVERY_AUDIO_ARGS,
        *DELIVERY_CONTAINER_ARGS,
        # Video and audio are clamped to whichever ends first, so the last frame
        # is never held over a silent tail.
        "-shortest",
        str(output_path),
    ]
    return command


def generate_video(
    combined_video_path: str,
    tts_path: str,
    subtitles_path: str,
    threads: int,
    subtitles_position: str,
    text_color: str,
    fmt: VideoFormat = SHORT,
) -> str:
    """
    Attaches the voiceover, burning the subtitles in when the format wants them.

    One ffmpeg pass using libass, replacing a MoviePy composite that rendered
    every subtitle frame through ImageMagick. Formats that ship subtitles as a
    caption track instead skip the burn, and with it the whole re-encode.

    Args:
        combined_video_path (str): The path to the combined video.
        tts_path (str): The path to the text-to-speech audio.
        subtitles_path (str): The path to the subtitles.
        threads (int): Threads for the encoder.
        subtitles_position (str): The position of the subtitles.
        text_color (str): Subtitle fill colour.
        fmt (VideoFormat): Decides sizing and whether subtitles are burned in.

    Returns:
        str: "output.mp4", relative to the project root.
    """
    output_path = TEMP_DIR / "output.mp4"
    command = build_render_command(
        combined_video_path,
        tts_path,
        subtitles_path,
        str(output_path),
        threads,
        subtitles_position,
        text_color,
        fmt,
    )
    subprocess.run(command, check=True, capture_output=True, text=True)
    return "output.mp4"


def _ffmpeg_binary() -> str:
    """The ffmpeg the worker should use.

    Honours FFMPEG_BINARY, which compose.win.yml sets to Debian's ffmpeg. The
    imageio-ffmpeg build MoviePy defaults to is compiled without nvenc, so the
    two are not interchangeable.
    """
    return os.getenv("FFMPEG_BINARY", "").strip() or "ffmpeg"


def _ffprobe_binary() -> str:
    """ffprobe living next to the configured ffmpeg, else whatever is on PATH."""
    ffmpeg = Path(_ffmpeg_binary())
    sibling = ffmpeg.with_name(ffmpeg.name.replace("ffmpeg", "ffprobe"))
    if sibling.exists():
        return str(sibling)
    return "ffprobe"


# Hardware encoding. NVENC is roughly 4.7x faster than libx264 at preset medium
# on the same footage, but it is absent on machines without an NVIDIA GPU and
# fused off on some GA107 boards, so availability is probed once and cached.
NVENC_CODEC = "h264_nvenc"
SOFTWARE_CODEC = "libx264"
_encoder_cache: dict[str, bool] = {}


def nvenc_available() -> bool:
    """True if this ffmpeg can actually encode with NVENC right now.

    Listing the encoder is not enough: -encoders reports compile-time support,
    which says nothing about the GPU being reachable from this container.
    """
    if NVENC_CODEC in _encoder_cache:
        return _encoder_cache[NVENC_CODEC]
    try:
        subprocess.run(
            [
                _ffmpeg_binary(), "-hide_banner", "-f", "lavfi",
                "-i", "nullsrc=size=256x256:duration=0.1:rate=10",
                "-c:v", NVENC_CODEC, "-f", "null", "-",
            ],
            check=True, capture_output=True, text=True,
        )
        available = True
    except (subprocess.CalledProcessError, OSError):
        available = False
    _encoder_cache[NVENC_CODEC] = available
    log(
        f"[+] Video encoder: {NVENC_CODEC if available else SOFTWARE_CODEC}",
        "info" if available else "warning",
    )
    return available


def encoder_args(threads: int, final: bool) -> List[str]:
    """ffmpeg output arguments for the chosen encoder.

    The intermediate concat is re-encoded by the subtitle pass, so it is tuned
    for speed at near-lossless quality; the final pass is tuned for delivery.
    """
    if nvenc_available():
        preset, quality = ("p4", "23") if final else ("p1", "18")
        args = ["-c:v", NVENC_CODEC, "-preset", preset, "-cq", quality]
    else:
        preset, quality = ("medium", "23") if final else ("ultrafast", "18")
        args = ["-c:v", SOFTWARE_CODEC, "-preset", preset, "-crf", quality]
        if threads:
            args += ["-threads", str(threads)]
    return args + ["-pix_fmt", "yuv420p"]

def probe_duration(media_path: str) -> float:
    """Container duration in seconds, via ffprobe.

    Args:
        media_path (str): Path to the media file.

    Returns:
        float: Duration in seconds.
    """
    result = subprocess.run(
        [
            _ffprobe_binary(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            media_path,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def _parse_integrated_loudness(ffmpeg_stderr: str) -> Optional[float]:
    """Reads the `I:` value out of an ebur128 summary. None if absent.

    The summary prints `I:` under `Integrated loudness:` and a second, unrelated
    `Threshold:` under it, so the line is located by its own label rather than
    by offset.
    """
    lines = ffmpeg_stderr.splitlines()
    for index, line in enumerate(lines):
        if "Integrated loudness" not in line:
            continue
        for candidate in lines[index + 1 : index + 4]:
            stripped = candidate.strip()
            if stripped.startswith("I:"):
                try:
                    return float(stripped.split()[1])
                except (IndexError, ValueError):
                    return None
    return None


def measure_voiceband_loudness(song_path: str) -> Optional[float]:
    """Loudness of a track inside the band the voice occupies, in LUFS.

    Measured after levelling, because that is the signal the mix actually uses.
    Returns None if ffmpeg or the summary cannot be read; callers fall back to
    a fixed gain rather than dropping the music.
    """
    command = [
        _ffmpeg_binary(),
        "-nostats",
        "-t",
        str(MUSIC_ANALYSIS_SECONDS),
        "-stream_loop",
        "-1",
        "-i",
        song_path,
        "-af",
        f"{MUSIC_LEVELER},"
        f"highpass=f={VOICE_BAND_LOW_HZ},lowpass=f={VOICE_BAND_HIGH_HZ},ebur128",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, OSError) as err:
        log(f"[!] Could not measure {os.path.basename(song_path)}: {err}", "warning")
        return None
    return _parse_integrated_loudness(result.stderr)


def resolve_music_gain_db(song_path: str) -> float:
    """Gain that puts this track's voice-band content on the shared target."""
    measured = measure_voiceband_loudness(song_path)
    if measured is None:
        log(
            f"[!] Falling back to {MUSIC_FALLBACK_GAIN_DB} dB for "
            f"{os.path.basename(song_path)}.",
            "warning",
        )
        return MUSIC_FALLBACK_GAIN_DB
    return MUSIC_VOICEBAND_TARGET_LUFS - measured


def build_music_filter(duration: float, gain_db: float = MUSIC_FALLBACK_GAIN_DB) -> str:
    """Builds the filter_complex graph that ducks a music bed under the voice.

    Input 0 is the rendered video (its audio is the voice), input 1 is the music.
    The voice is split: one copy is mixed, the other is the sidechain key.

    Args:
        duration (float): Duration of the rendered video, for the fade-out start.
        gain_db (float): Gain applied to the levelled bed, from
            resolve_music_gain_db().

    Returns:
        str: An ffmpeg filter_complex expression producing [aout].
    """
    fade_out_start = max(0.0, duration - MUSIC_FADE_OUT_SECONDS)
    return (
        # Level the bed first: attenuating a dynamic track leaves only its
        # transients audible under the voice.
        f"[1:a]{MUSIC_LEVELER},"
        f"volume={gain_db:.2f}dB,"
        f"afade=t=in:st=0:d={MUSIC_FADE_IN_SECONDS},"
        f"afade=t=out:st={fade_out_start:.3f}:d={MUSIC_FADE_OUT_SECONDS}[bed];"
        "[0:a]asplit=2[voice][key];"
        "[bed][key]sidechaincompress="
        f"threshold={MUSIC_DUCK_THRESHOLD}:ratio={MUSIC_DUCK_RATIO}:"
        "attack=5:release=300[duck];"
        "[voice][duck]amix=inputs=2:duration=first:dropout_transition=0,"
        f"loudnorm=I={LOUDNESS_TARGET_LUFS}:TP={LOUDNESS_TRUE_PEAK_DB}:"
        f"LRA={LOUDNESS_RANGE}[aout]"
    )


def normalize_audio(video_path: str, output_path: str) -> str:
    """Brings a finished video to the delivery loudness without touching the picture.

    Loudness normalization used to live inside the music mix, so a video
    without music shipped at whatever level the TTS happened to produce —
    measured at -17.8 LUFS against the -14 YouTube normalizes to, i.e. audibly
    quieter than everything around it.

    Args:
        video_path (str): The rendered video.
        output_path (str): Where to write the normalized copy.

    Returns:
        str: `output_path`.

    Raises:
        subprocess.CalledProcessError: If ffmpeg fails.
    """
    command = [
        _ffmpeg_binary(),
        "-y",
        "-i",
        video_path,
        "-af",
        f"loudnorm=I={LOUDNESS_TARGET_LUFS}:TP={LOUDNESS_TRUE_PEAK_DB}:"
        f"LRA={LOUDNESS_RANGE}",
        "-c:v",
        "copy",
        *DELIVERY_AUDIO_ARGS,
        *DELIVERY_CONTAINER_ARGS,
        output_path,
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    log("[+] Audio loudness normalized.", "success")
    return output_path

def mix_background_music(
    video_path: str,
    song_path: str,
    output_path: str,
    gain_db: Optional[float] = None,
) -> str:
    """Adds a ducked, loudness-normalized music bed to an already rendered video.

    One ffmpeg pass. The video stream is copied, never re-encoded: this changes
    audio only, so re-encoding the picture would cost minutes and lose quality
    for nothing. The music is looped to cover the whole video and trimmed by
    -shortest.

    Args:
        video_path (str): The rendered video, whose audio track is the voice.
        song_path (str): The music file to lay underneath.
        output_path (str): Where to write the muxed result.
        gain_db (Optional[float]): Bed gain. Measured from the track when None.

    Returns:
        str: `output_path`.

    Raises:
        subprocess.CalledProcessError: If ffmpeg or ffprobe fails.
    """
    duration = probe_duration(video_path)
    if gain_db is None:
        gain_db = resolve_music_gain_db(song_path)
    command = [
        _ffmpeg_binary(),
        "-y",
        "-i",
        video_path,
        # Loop the bed indefinitely; -shortest cuts it back to the video.
        "-stream_loop",
        "-1",
        "-i",
        song_path,
        "-filter_complex",
        build_music_filter(duration, gain_db),
        "-map",
        "0:v:0",
        "-map",
        "[aout]",
        "-c:v",
        "copy",
        *DELIVERY_AUDIO_ARGS,
        *DELIVERY_CONTAINER_ARGS,
        "-shortest",
        output_path,
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    log(
        f"[+] Background music mixed at {gain_db:+.1f} dB and loudness-normalized.",
        "success",
    )
    return output_path


def make_silence(seconds: float, output_path: str) -> str:
    """Writes a silent mp3 of the given length.

    Used between narrated sections. Generated rather than shipped as an asset
    so the length is free to change, and encoded as mp3 so it concatenates with
    the narration without a format conversion.
    """
    command = [
        _ffmpeg_binary(),
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r=44100:cl=mono",
        "-t",
        f"{max(seconds, 0.01):.3f}",
        "-c:a",
        "libmp3lame",
        "-q:a",
        "9",
        output_path,
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    return output_path


# Where to sample a clip when judging how striking it opens, counted from where
# its shot starts. Far enough in to clear a title card, early enough to be what
# the viewer actually sees first.
OPENING_SAMPLE_SECONDS = 0.5

# The frame the footage check (footage_judge.py) sees, at a size a model reads
# comfortably and cheaply: about 200 image tokens for a Short.
CHECK_FRAME_LONG_SIDE = 512


def sample_frame(
    path: str, at: float, fmt: VideoFormat = SHORT, ffmpeg: str = "ffmpeg"
) -> Optional[bytes]:
    """One JPEG frame `at` seconds into a clip, centre-cropped to the format.

    Cropped the way the clip will be shown, so it is judged on what the viewer
    sees rather than on what is cut away. None if the frame cannot be read.
    """
    ratio = f"{fmt.aspect_ratio:.6f}"
    side = CHECK_FRAME_LONG_SIDE
    try:
        result = subprocess.run(
            [
                ffmpeg, "-v", "error",
                "-ss", f"{at:.3f}",
                "-i", path,
                "-frames:v", "1",
                "-vf",
                f"crop='min(iw,ih*{ratio})':'min(ih,iw/{ratio})',"
                f"scale={side}:{side}:force_original_aspect_ratio=decrease",
                "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "4",
                "pipe:1",
            ],
            check=True, capture_output=True, timeout=60,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    return result.stdout or None


def promote_strongest_opening(
    video_paths: List[str],
    work_dir: Path,
    ffmpeg: str = "ffmpeg",
    starts: Optional[Dict[str, float]] = None,
    eligible: Optional[Set[str]] = None,
) -> List[str]:
    """Moves the most striking clip to the front, leaving the rest in order.

    The first frame decides whether a Short is watched at all, and until now it
    was whichever clip the first search term happened to return. Only the
    opening is promoted rather than sorting the whole list: ranking every clip
    by contrast would front-load the good footage and leave a dull tail, and
    the order after the first shot is already varied on purpose.

    `starts` is where each clip's shot begins once its black opening is
    skipped (find_usable_footage). Judged at a fixed half second instead, a
    clip that fades up over four seconds is scored on a black frame, and one
    whose shot starts later is scored on a frame nobody sees.

    `eligible` limits the choice to those clips: the footage check's real
    footage, since contrast alone opened two videos on cartoons. None, or none
    of them in the list, means any clip.

    Returns the list unchanged if fewer than two clips, or if no frame can be
    read — a worse opening beats a failed render.
    """
    if len(video_paths) < 2:
        return list(video_paths)

    starts = starts or {}
    candidates = set(video_paths)
    if eligible and candidates & set(eligible):
        candidates &= set(eligible)
    work_dir.mkdir(parents=True, exist_ok=True)
    best_index, best_score = 0, None
    for index, path in enumerate(video_paths):
        if path not in candidates:
            continue
        frame = work_dir / f"opening_{index}.png"
        sample = starts.get(path, 0.0) + OPENING_SAMPLE_SECONDS
        try:
            subprocess.run(
                [
                    ffmpeg, "-v", "error", "-y",
                    "-ss", f"{sample:.3f}",
                    "-i", path, "-frames:v", "1", str(frame),
                ],
                check=True, capture_output=True, text=True,
            )
            with Image.open(frame) as image:
                score = score_frame(image)
        except (subprocess.CalledProcessError, OSError):
            continue
        if best_score is None or score > best_score:
            best_index, best_score = index, score

    if best_score is None:
        log("[!] Could not judge any opening frame; leaving the order alone.", "warning")
        return list(video_paths)

    ordered = list(video_paths)
    ordered.insert(0, ordered.pop(best_index))
    log(f"[+] Opening on clip {best_index + 1} of {len(ordered)}.", "info")
    return ordered
