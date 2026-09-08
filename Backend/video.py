import os
import subprocess
import uuid

import requests
import srt_equalizer
import assemblyai as aai

from typing import List, Optional, Tuple
from pathlib import Path
# Only the audio clip type survives here: the video path is ffmpeg now, and
# AudioFileClip is still what the pipeline hands to generate_subtitles.
from moviepy import AudioFileClip
from dotenv import load_dotenv
from formats import SHORT, VideoFormat
from logstream import log
from search import PEXELS_TIMEOUT
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
        f.write(requests.get(video_url, timeout=PEXELS_TIMEOUT).content)

    return str(video_path)


def __generate_subtitles_assemblyai(audio_path: str, voice: str) -> str:
    """
    Generates subtitles from a given audio file and returns the path to the subtitles.

    Args:
        audio_path (str): The path to the audio file to generate subtitles from.

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
    subtitles = transcript.export_subtitles_srt()

    return subtitles


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

    def convert_to_srt_time_format(total_seconds: float) -> str:
        # Convert total seconds to the SRT time format: HH:MM:SS,mmm
        milliseconds_total = int(round(total_seconds * 1000))
        hours, remainder = divmod(milliseconds_total, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, milliseconds = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

    start_time = 0
    subtitles = []

    for i, (sentence, audio_clip) in enumerate(zip(sentences, audio_clips), start=1):
        duration = audio_clip.duration
        end_time = start_time + duration

        # Format: subtitle index, start time --> end time, sentence
        subtitle_entry = f"{i}\n{convert_to_srt_time_format(start_time)} --> {convert_to_srt_time_format(end_time)}\n{sentence}\n"
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
        # readable lines.
        srt_equalizer.equalize_srt_file(srt_path, srt_path, width)

    # Save subtitles
    SUBTITLES_DIR.mkdir(parents=True, exist_ok=True)
    subtitles_path = SUBTITLES_DIR / f"{uuid.uuid4()}.srt"

    if ASSEMBLY_AI_API_KEY is not None and ASSEMBLY_AI_API_KEY != "":
        log("[+] Creating subtitles using AssemblyAI", "info")
        subtitles = __generate_subtitles_assemblyai(audio_path, voice)
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


def build_concat_filter(
    segment_count: int,
    fmt: VideoFormat = SHORT,
    durations: Optional[List[float]] = None,
) -> str:
    """Crops each segment to the format's ratio, scales it, then concatenates.

    The crop is an expression rather than arithmetic in Python, so one filter
    handles both cases and no source has to be probed for its dimensions:
    footage narrower than the target is cut top and bottom, anything wider is
    cut at the sides. Both stay centred.
    """
    ratio = fmt.aspect_ratio
    crop = (
        f"crop=w='if(lt(iw/ih,{ratio}),iw,ih*{ratio})'"
        f":h='if(lt(iw/ih,{ratio}),iw/{ratio},ih)'"
        ":x='(iw-ow)/2':y='(ih-oh)/2'"
    )
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
    chains.append(f"{inputs}concat=n={segment_count}:v=1:a=0[vout]")
    return ";".join(chains)


def combine_videos(
    video_paths: List[str],
    max_duration: float,
    threads: int,
    fmt: VideoFormat = SHORT,
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

    Returns:
        str: The path to the combined video.
    """
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    combined_video_path = TEMP_DIR / f"{uuid.uuid4()}.mp4"

    sources = [(path, probe_duration(path)) for path in video_paths]
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

    log("[+] Combining videos...", "info")
    log(f"[+] {len(segments)} segments covering {max_duration:.1f}s.", "info")

    command = [_ffmpeg_binary(), "-y"]
    for path, duration in segments:
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


def patch_ass_script(
    script: str, subtitles_position: str, text_colour: str, fmt: VideoFormat = SHORT
) -> str:
    """Sets the script's resolution and replaces its style definition.

    ffmpeg converts SRT to ASS with PlayResX/Y of 384x288. Font sizes are
    relative to that, so a size meant for a 1920-tall frame is scaled up by
    almost seven and the text runs off the screen. Declaring the real frame
    size makes the size mean pixels.
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
        else:
            lines.append(line)
            if stripped.startswith("[Script Info]") and not (
                seen_play_res_x and seen_play_res_y
            ):
                lines.append(f"PlayResX: {fmt.width}")
                lines.append(f"PlayResY: {fmt.height}")
                seen_play_res_x = seen_play_res_y = True
    return "\n".join(lines) + "\n"


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
        "-c:a",
        "aac",
        "-b:a",
        "192k",
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
        "-c:a",
        "aac",
        "-b:a",
        "192k",
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
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        output_path,
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    log(
        f"[+] Background music mixed at {gain_db:+.1f} dB and loudness-normalized.",
        "success",
    )
    return output_path
