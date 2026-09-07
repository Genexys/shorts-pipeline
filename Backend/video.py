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
    audio_path: str, sentences: List[str], audio_clips: List[AudioFileClip], voice: str
) -> str:
    """
    Generates subtitles from a given audio file and returns the path to the subtitles.

    Args:
        audio_path (str): The path to the audio file to generate subtitles from.
        sentences (List[str]): all the sentences said out loud in the audio clips
        audio_clips (List[AudioFileClip]): all the individual audio clips which will make up the final audio track

    Returns:
        str: The path to the generated subtitles.
    """

    def equalize_subtitles(srt_path: str, max_chars: int = 10) -> None:
        # Equalize subtitles
        srt_equalizer.equalize_srt_file(srt_path, srt_path, max_chars)

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
    equalize_subtitles(str(subtitles_path))

    log("[+] Subtitles generated.", "success")

    return str(subtitles_path)


ASPECT_9_16 = 9 / 16

# ASS alignment values (numpad layout) for the UI's vertical positions.
SUBTITLE_ALIGNMENT = {"top": 8, "center": 5, "bottom": 2}
SUBTITLE_TOP_MARGIN_PX = 80
SUBTITLE_SIDE_MARGIN_PX = 60
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
SUBTITLE_FONT_NAME = "The Bold Font"
# Chosen by measuring glyph height against the previous MoviePy render:
# at 100 the caps came out 64 px against its 72 px, an 11% shrink.
SUBTITLE_FONT_SIZE = 112
SUBTITLE_OUTLINE = 5


def plan_clip_segments(
    sources: List[Tuple[str, float]],
    max_duration: float,
    max_clip_duration: float,
) -> List[Tuple[str, float]]:
    """Chooses which clip to show for how long, cycling until the audio is covered.

    Pure, so the selection rule can be tested without touching ffmpeg.

    Args:
        sources: (path, source duration) pairs, in the order they should cycle.
        max_duration: Total duration to fill, normally the voiceover length.
        max_clip_duration: Longest single segment.

    Returns:
        (path, segment duration) pairs whose durations sum to max_duration.

    Raises:
        ValueError: If no sources were given.
        RuntimeError: If no source is long enough to make progress.
    """
    if not sources:
        raise ValueError("No source videos were provided for concatenation.")

    required = max_duration / len(sources)
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
            target = min(required, max_clip_duration, remaining, usable)
            if target <= 0:
                continue
            segments.append((path, target))
            total += target
            progressed = True
        if not progressed:
            raise RuntimeError("Could not reach target duration from source videos.")

    return segments


def build_concat_filter(segment_count: int) -> str:
    """Crops each segment to 9:16, scales it to 1080x1920, then concatenates.

    The crop is written as an expression so one filter handles both cases:
    footage narrower than 9:16 is cut top and bottom, anything wider is cut at
    the sides. Both stay centred.
    """
    crop = (
        f"crop=w='if(lt(iw/ih,{ASPECT_9_16}),iw,ih*{ASPECT_9_16})'"
        f":h='if(lt(iw/ih,{ASPECT_9_16}),iw/{ASPECT_9_16},ih)'"
        ":x='(iw-ow)/2':y='(ih-oh)/2'"
    )
    chains = [
        f"[{index}:v]{crop},scale=1080:1920,setsar=1,fps=30,"
        f"setpts=PTS-STARTPTS[v{index}]"
        for index in range(segment_count)
    ]
    inputs = "".join(f"[v{index}]" for index in range(segment_count))
    chains.append(f"{inputs}concat=n={segment_count}:v=1:a=0[vout]")
    return ";".join(chains)


def combine_videos(
    video_paths: List[str], max_duration: float, max_clip_duration: float, threads: int
) -> str:
    """
    Combines stock clips into one 9:16 video of the requested duration.

    Runs entirely in ffmpeg. The previous MoviePy implementation moved every
    frame through Python to crop and resize it, which cost roughly 40x what the
    same work costs inside ffmpeg's filter graph.

    Args:
        video_paths (List): A list of paths to the videos to combine.
        max_duration (int): The maximum duration of the combined video.
        max_clip_duration (int): The maximum duration of each clip.
        threads (int): Threads for the encoder.

    Returns:
        str: The path to the combined video.
    """
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    combined_video_path = TEMP_DIR / f"{uuid.uuid4()}.mp4"

    sources = [(path, probe_duration(path)) for path in video_paths]
    segments = plan_clip_segments(sources, float(max_duration), float(max_clip_duration))

    log("[+] Combining videos...", "info")
    log(f"[+] {len(segments)} segments covering {max_duration:.1f}s.", "info")

    command = [_ffmpeg_binary(), "-y"]
    for path, duration in segments:
        command += ["-t", f"{duration:.3f}", "-i", path]
    command += [
        "-filter_complex",
        build_concat_filter(len(segments)),
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


def build_style_line(subtitles_position: str, text_colour: str) -> str:
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
        str(SUBTITLE_FONT_SIZE),
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


def patch_ass_script(script: str, subtitles_position: str, text_colour: str) -> str:
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
            lines.append(f"PlayResX: {VIDEO_WIDTH}")
            seen_play_res_x = True
        elif stripped.startswith("PlayResY:"):
            lines.append(f"PlayResY: {VIDEO_HEIGHT}")
            seen_play_res_y = True
        elif stripped.startswith("Style:"):
            lines.append(build_style_line(subtitles_position, text_colour))
        else:
            lines.append(line)
            if stripped.startswith("[Script Info]") and not (
                seen_play_res_x and seen_play_res_y
            ):
                lines.append(f"PlayResX: {VIDEO_WIDTH}")
                lines.append(f"PlayResY: {VIDEO_HEIGHT}")
                seen_play_res_x = seen_play_res_y = True
    return "\n".join(lines) + "\n"


def prepare_ass_subtitles(
    subtitles_path: str, subtitles_position: str, text_colour: str
) -> str:
    """Converts the .srt to a styled .ass sized for the real frame."""
    ass_path = TEMP_DIR / f"{uuid.uuid4()}.ass"
    subprocess.run(
        [_ffmpeg_binary(), "-y", "-i", str(subtitles_path), str(ass_path)],
        check=True, capture_output=True, text=True,
    )
    ass_path.write_text(
        patch_ass_script(
            ass_path.read_text(encoding="utf-8"), subtitles_position, text_colour
        ),
        encoding="utf-8",
    )
    return str(ass_path)


def generate_video(
    combined_video_path: str,
    tts_path: str,
    subtitles_path: str,
    threads: int,
    subtitles_position: str,
    text_color: str,
) -> str:
    """
    Burns the subtitles in and attaches the voiceover.

    One ffmpeg pass using libass, replacing a MoviePy composite that rendered
    every subtitle frame through ImageMagick.

    Args:
        combined_video_path (str): The path to the combined video.
        tts_path (str): The path to the text-to-speech audio.
        subtitles_path (str): The path to the subtitles.
        threads (int): Threads for the encoder.
        subtitles_position (str): The position of the subtitles.
        text_color (str): Subtitle fill colour.

    Returns:
        str: "output.mp4", relative to the project root.
    """
    output_path = TEMP_DIR / "output.mp4"

    # libass needs the font by family name, and finds it only if told where to
    # look; the file lives outside any system font directory.
    ass_path = prepare_ass_subtitles(subtitles_path, subtitles_position, text_color)
    subtitle_filter = f"ass='{ass_path}':fontsdir='{FONTS_DIR}'"

    command = [
        _ffmpeg_binary(),
        "-y",
        "-i",
        str(combined_video_path),
        "-i",
        tts_path,
        "-vf",
        subtitle_filter,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        *encoder_args(threads, final=True),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        # Video and audio are clamped to whichever ends first, as the MoviePy
        # version did, so the last frame is never held over a silent tail.
        "-shortest",
        str(output_path),
    ]

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
