import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional
from uuid import uuid4

from moviepy import AudioFileClip, concatenate_audioclips

from formats import LONG, resolve_format
from gpt import (
    generate_long_script,
    generate_metadata,
    generate_script,
    get_search_terms,
    select_music_mood,
)
from logstream import log
from search import search_for_stock_videos
from thumbnail import build_thumbnail
from speech import synthesize_sentences
from utils import (
    OUTPUT_DIR,
    PROJECT_ROOT,
    SUBTITLES_DIR,
    TEMP_DIR,
    choose_random_song,
)
from video import (
    combine_videos,
    probe_duration,
    generate_subtitles,
    generate_video,
    mix_background_music,
    normalize_audio,
    save_video,
)
from youtube import (
    resolve_language,
    upload_captions,
    resolve_privacy_status,
    upload_thumbnail,
    upload_video,
)


class PipelineCancelled(Exception):
    pass


@dataclass
class PipelineResult:
    video_path: str            # "output.mp4" relative to PROJECT_ROOT
    archived_path: str         # "output/<job_id>.mp4" relative to PROJECT_ROOT
    title: str
    youtube_video_id: Optional[str]
    upload_error: Optional[str]
    privacy_status: str
    format_name: str
    subtitles_path: str
    thumbnail_path: Optional[str]


def run_generation_pipeline(
    data: dict,
    is_cancelled: Callable[[], bool],
    on_log: Callable[[str, str], None],
) -> PipelineResult:
    def emit(message: str, level: str = "info") -> None:
        log(message, level)
        if on_log:
            on_log(message, level)

    def guard_cancelled() -> None:
        if is_cancelled and is_cancelled():
            raise PipelineCancelled("Video generation was cancelled.")

    fmt = resolve_format(data.get("format"))
    paragraph_number = int(data.get("paragraphNumber", 1))
    ai_model = data.get("aiModel")
    n_threads = data.get("threads")
    subtitles_position = data.get("subtitlesPosition")
    text_color = data.get("color")
    use_music = data.get("useMusic", False)
    automate_youtube_upload = data.get("automateYoutubeUpload", False)
    job_id = str(data.get("jobId") or uuid4())

    emit("[Video to be generated]", "info")
    emit("   Subject: " + data["videoSubject"], "info")
    emit("   AI Model: " + str(ai_model), "info")
    emit(f"   Format: {fmt.name} ({fmt.width}x{fmt.height})", "info")
    emit("   Custom Prompt: " + data["customPrompt"], "info")

    guard_cancelled()

    # An explicit choice from the request wins; otherwise the format decides,
    # so Shorts and long form do not share a narrator.
    voice = data.get("voice", "")
    if not voice:
        voice = fmt.voice
        emit(f'[!] No voice was selected. Using "{voice}"', "warning")
    voice_prefix = voice[:2]

    if fmt is LONG:
        script = generate_long_script(
            data["videoSubject"],
            fmt.target_words,
            ai_model,
            voice,
            data["customPrompt"],
        )
    else:
        script = generate_script(
            data["videoSubject"],
            paragraph_number,
            ai_model,
            voice,
            data["customPrompt"],
        )

    if not script:
        raise RuntimeError(
            "Could not generate a script. Try a different model or prompt."
        )

    search_terms = get_search_terms(
        data["videoSubject"], fmt.search_term_count, script, ai_model
    )

    video_urls = []
    it = 15
    min_dur = 10

    found_per_term = []
    for search_term in search_terms:
        guard_cancelled()
        found_per_term.append(
            search_for_stock_videos(
                search_term, os.getenv("PEXELS_API_KEY"), it, min_dur
            )
        )

    # Round-robin rather than taking a fixed share from each term and stopping:
    # search terms overlap and some return almost nothing, so a fixed share
    # leaves the video short of footage and it starts repeating itself.
    wanted = fmt.stock_video_count
    for depth in range(it):
        if len(video_urls) >= wanted:
            break
        for found_urls in found_per_term:
            if len(video_urls) >= wanted:
                break
            if depth < len(found_urls) and found_urls[depth] not in video_urls:
                video_urls.append(found_urls[depth])

    if not video_urls:
        raise RuntimeError("No videos found to download.")

    video_paths = []
    emit(f"[+] Downloading {len(video_urls)} videos...", "info")

    for video_url in video_urls:
        guard_cancelled()
        try:
            saved_video_path = save_video(video_url)
            video_paths.append(saved_video_path)
        except Exception:
            emit(f"[-] Could not download video: {video_url}", "error")

    emit("[+] Videos downloaded!", "success")
    emit("[+] Script generated!", "success")

    guard_cancelled()

    sentences = script.split(". ")
    sentences = list(filter(lambda x: x != "", sentences))

    guard_cancelled()
    audio_paths, provider = synthesize_sentences(
        sentences,
        make_path=lambda: str(TEMP_DIR / f"{uuid4()}.mp3"),
        tiktok_voice=voice,
        elevenlabs_voice_id=fmt.elevenlabs_voice_id,
        on_log=emit,
    )
    emit(f"[+] Narrated with {provider}", "info")

    paths = [AudioFileClip(path) for path in audio_paths]

    final_audio = concatenate_audioclips(paths)
    tts_path = str(TEMP_DIR / f"{uuid4()}.mp3")
    try:
        final_audio.write_audiofile(tts_path)
    finally:
        final_audio.close()
        for audio_clip in paths:
            audio_clip.close()

    try:
        subtitles_path = generate_subtitles(
            audio_path=tts_path,
            sentences=sentences,
            audio_clips=paths,
            voice=voice_prefix,
            max_chars=fmt.subtitle_max_chars,
        )
    except Exception as err:
        emit(f"[-] Error generating subtitles: {err}", "error")
        subtitles_path = None

    if not subtitles_path:
        raise RuntimeError(
            "Could not generate subtitles. Check AssemblyAI key or local subtitle settings."
        )

    temp_audio = AudioFileClip(tts_path)
    try:
        # One shot per clip only holds while each shot can be long enough.
        # Below this many clips the run needs more shots than it has footage,
        # and the video starts repeating itself part way through.
        least_clips = math.ceil(temp_audio.duration / fmt.max_clip_duration)
        if len(video_paths) < least_clips:
            emit(
                f"[!] Only {len(video_paths)} clips for {temp_audio.duration:.0f}s; "
                f"{least_clips} are needed to avoid repeating footage.",
                "warning",
            )
        combined_video_path = combine_videos(
            video_paths, temp_audio.duration, n_threads or 2, fmt
        )
    finally:
        temp_audio.close()

    try:
        final_video_path = generate_video(
            combined_video_path,
            tts_path,
            subtitles_path,
            n_threads or 2,
            subtitles_position,
            text_color or "#FFFF00",
            fmt,
        )
    except Exception as err:
        raise RuntimeError(
            f"Could not render final video. Check subtitle/font/ImageMagick setup. ({err})"
        ) from err

    title, description, keywords = generate_metadata(
        data["videoSubject"], script, ai_model, fmt.always_hashtags
    )

    emit("[-] Metadata for YouTube upload:", "info")
    emit("   Title:", "info")
    emit(f"   {title}", "info")
    emit("   Description:", "info")
    emit(f"   {description}", "info")
    emit("   Keywords:", "info")
    emit(f"  {', '.join(keywords)}", "info")

    final_output_path = str(PROJECT_ROOT / final_video_path)
    rendered_video_path = str(TEMP_DIR / final_video_path)

    guard_cancelled()

    if use_music:
        mood = select_music_mood(data["videoSubject"], script, ai_model)
        song_path = choose_random_song(mood)

        if not song_path:
            emit(
                "[-] Could not find songs in Songs/. Continuing without background music.",
                "warning",
            )
            use_music = False
        else:
            emit(
                f"[+] Music: {os.path.basename(song_path)} "
                f"(mood: {mood or 'not determined, using Songs/ fallback'})",
                "info",
            )

    if use_music:
        try:
            mix_background_music(rendered_video_path, song_path, final_output_path)
            emit("[+] Music mixed, ducked under the voice, normalized.", "success")
        except Exception as err:
            # The render is already finished and usable. A failed music pass must
            # not discard a job that cost several minutes, so fall back to the
            # voice-only video instead of raising.
            detail = str(err)
            if isinstance(err, subprocess.CalledProcessError) and err.stderr:
                tail = err.stderr.strip().splitlines()
                if tail:
                    detail = tail[-1]
            emit(
                f"[!] Music mix failed ({detail}). Keeping the voice-only render.",
                "warning",
            )
            use_music = False

    if not use_music:
        # Still normalize: without this the video ships at whatever level the
        # TTS produced, which measured 4 dB under what YouTube normalizes to.
        try:
            normalize_audio(rendered_video_path, final_output_path)
        except Exception as err:
            emit(
                f"[!] Could not normalize loudness ({err}). Using the render as is.",
                "warning",
            )
            shutil.copy2(rendered_video_path, final_output_path)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    archived_path = f"{OUTPUT_DIR.name}/{job_id}.mp4"
    shutil.copy2(final_output_path, str(PROJECT_ROOT / archived_path))

    emit(f"[+] Video generated: {final_video_path} (archived as {archived_path})", "success")

    thumbnail_path: Optional[str] = None
    if fmt.build_thumbnail:
        try:
            thumbnail_path = build_thumbnail(
                str(PROJECT_ROOT / archived_path),
                title,
                str(TEMP_DIR / f"{job_id}.jpg"),
                duration=probe_duration(str(PROJECT_ROOT / archived_path)),
                work_dir=TEMP_DIR / "thumbnail",
                ffmpeg=os.getenv("FFMPEG_BINARY", "").strip() or "ffmpeg",
            )
        except Exception as err:
            # A missing thumbnail costs clicks; a failed job costs the video.
            emit(f"[!] Could not build a thumbnail ({err}).", "warning")

    privacy_status, privacy_warning = resolve_privacy_status(
        os.getenv("YOUTUBE_PRIVACY_STATUS")
    )
    category_id = (os.getenv("YOUTUBE_CATEGORY_ID") or "28").strip() or "28"
    youtube_video_id: Optional[str] = None
    upload_error: Optional[str] = None

    if automate_youtube_upload:
        guard_cancelled()
        if privacy_warning:
            emit(f"[!] {privacy_warning}", "warning")
        emit(f"[+] Uploading to YouTube as {privacy_status}...", "info")
        try:
            youtube_video_id = upload_video(
                video_path=str(PROJECT_ROOT / archived_path),
                title=title,
                description=description,
                category=category_id,
                tags=keywords,
                privacy_status=privacy_status,
                language=resolve_language(voice),
            )
            emit(f"[+] Uploaded: https://youtu.be/{youtube_video_id}", "success")
            if thumbnail_path:
                try:
                    upload_thumbnail(youtube_video_id, thumbnail_path)
                    emit("[+] Thumbnail set.", "success")
                except Exception as err:
                    # Needs a phone-verified channel; the video is already live.
                    emit(f"[!] Thumbnail not set: {err}", "warning")
            if not fmt.burn_subtitles:
                try:
                    upload_captions(
                        youtube_video_id,
                        subtitles_path,
                        language=resolve_language(voice),
                    )
                    emit("[+] Caption track uploaded.", "success")
                except Exception as err:
                    # Needs the wider caption scope; a video without captions
                    # is worth far more than a failed job.
                    emit(f"[!] Captions not uploaded: {err}", "warning")
        except Exception as err:
            upload_error = str(err)
            emit(f"[!] YouTube upload skipped: {upload_error}", "warning")

    return PipelineResult(
        video_path=final_video_path,
        archived_path=archived_path,
        title=title,
        youtube_video_id=youtube_video_id,
        upload_error=upload_error,
        privacy_status=privacy_status,
        format_name=fmt.name,
        subtitles_path=subtitles_path,
        thumbnail_path=thumbnail_path,
    )
