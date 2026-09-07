import os
import subprocess
import uuid

import requests
import srt_equalizer
import assemblyai as aai

from typing import List, Optional
from pathlib import Path
from moviepy import (
    AudioFileClip,
    CompositeVideoClip,
    TextClip,
    VideoFileClip,
    concatenate_videoclips,
)
from dotenv import load_dotenv
from logstream import log
from moviepy.video.tools.subtitles import SubtitlesClip
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
# target, derived from a mix that was judged right by ear.
VOICE_BAND_LOW_HZ = 300
VOICE_BAND_HIGH_HZ = 3000
MUSIC_VOICEBAND_TARGET_LUFS = -26.0

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


def combine_videos(
    video_paths: List[str], max_duration: int, max_clip_duration: int, threads: int
) -> str:
    """
    Combines a list of videos into one video and returns the path to the combined video.

    Args:
        video_paths (List): A list of paths to the videos to combine.
        max_duration (int): The maximum duration of the combined video.
        max_clip_duration (int): The maximum duration of each clip.
        threads (int): The number of threads to use for the video processing.

    Returns:
        str: The path to the combined video.
    """
    video_id = uuid.uuid4()
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    combined_video_path = TEMP_DIR / f"{video_id}.mp4"

    if not video_paths:
        raise ValueError("No source videos were provided for concatenation.")

    max_duration = float(max_duration)
    max_clip_duration = float(max_clip_duration)

    # Required duration of each clip
    req_dur = max_duration / len(video_paths)

    log("[+] Combining videos...", "info")
    log(f"[+] Each clip will be maximum {req_dur} seconds long.", "info")

    clips = []
    tot_dur = 0
    # Add downloaded clips over and over until the duration of the audio (max_duration) has been reached
    while tot_dur < (max_duration - FRAME_EPSILON):
        progressed = False
        for video_path in video_paths:
            remaining = max_duration - tot_dur
            if remaining <= FRAME_EPSILON:
                break

            clip = VideoFileClip(video_path)
            clip = clip.without_audio()
            max_safe_source_duration = clip.duration - FRAME_EPSILON
            if max_safe_source_duration <= 0:
                clip.close()
                continue

            target_duration = min(req_dur, max_clip_duration, remaining)
            target_duration = min(target_duration, max_safe_source_duration)

            if target_duration <= 0:
                clip.close()
                continue

            if target_duration < clip.duration:
                clip = clip.subclipped(0, target_duration)
            clip = clip.with_fps(30)

            # Not all videos are same size,
            # so we need to resize them
            if round((clip.w / clip.h), 4) < 0.5625:
                clip = clip.cropped(
                    width=clip.w,
                    height=round(clip.w / 0.5625),
                    x_center=clip.w / 2,
                    y_center=clip.h / 2,
                )
            else:
                clip = clip.cropped(
                    width=round(0.5625 * clip.h),
                    height=clip.h,
                    x_center=clip.w / 2,
                    y_center=clip.h / 2,
                )
            clip = clip.resized(new_size=(1080, 1920))

            clips.append(clip)
            tot_dur += clip.duration
            progressed = True

        if not progressed:
            raise RuntimeError("Could not reach target duration from source videos.")

    if not clips:
        raise RuntimeError("No valid clips were produced for concatenation.")

    final_clip = concatenate_videoclips(clips, method="compose")
    final_clip = final_clip.with_fps(30).with_duration(max_duration)
    try:
        final_clip.write_videofile(
            str(combined_video_path),
            threads=threads,
            fps=30,
            codec="libx264",
            # Intermediate file: generate_video() re-encodes it, so compressing it
            # well here is wasted CPU. A low CRF keeps this pass visually lossless
            # so the final encode inherits no artifacts from it.
            preset="ultrafast",
            ffmpeg_params=["-crf", "18"],
            audio=False,
        )
    finally:
        final_clip.close()
        for clip in clips:
            clip.close()

    return str(combined_video_path)


def generate_video(
    combined_video_path: str,
    tts_path: str,
    subtitles_path: str,
    threads: int,
    subtitles_position: str,
    text_color: str,
) -> str:
    """
    This function creates the final video, with subtitles and audio.

    Args:
        combined_video_path (str): The path to the combined video.
        tts_path (str): The path to the text-to-speech audio.
        subtitles_path (str): The path to the subtitles.
        threads (int): The number of threads to use for the video processing.
        subtitles_position (str): The position of the subtitles.

    Returns:
        str: The path to the final video.
    """
    # Make a generator that returns a TextClip when called with consecutive
    font_path = str((FONTS_DIR / "bold_font.ttf").resolve())
    generator = lambda txt: TextClip(
        font=font_path,
        text=txt,
        font_size=100,
        color=text_color,
        stroke_color="black",
        stroke_width=5,
    )

    # Split the subtitles position into horizontal and vertical
    horizontal_subtitles_position, vertical_subtitles_position = (
        subtitles_position.split(",")
    )

    # Burn the subtitles into the video
    subtitles = SubtitlesClip(subtitles_path, make_textclip=generator)
    subtitle_vertical_position = vertical_subtitles_position
    if vertical_subtitles_position == "top":
        subtitle_vertical_position = 80

    base_video = VideoFileClip(str(combined_video_path))
    audio = AudioFileClip(tts_path)
    target_duration = min(base_video.duration, audio.duration)

    result = CompositeVideoClip(
        [
            base_video.subclipped(0, target_duration),
            subtitles.with_position(
                (horizontal_subtitles_position, subtitle_vertical_position)
            ).with_duration(target_duration),
        ]
    )

    # Clamp audio/video to exactly the same duration to avoid end-frame overreads.
    result = result.with_audio(audio.subclipped(0, target_duration)).with_duration(
        target_duration
    )

    output_path = TEMP_DIR / "output.mp4"
    try:
        result.write_videofile(
            str(output_path),
            threads=threads or 2,
            fps=30,
            codec="libx264",
            audio_codec="aac",
            preset="medium",
        )
    finally:
        result.close()
        subtitles.close()
        audio.close()
        base_video.close()

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
