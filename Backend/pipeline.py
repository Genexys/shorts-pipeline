import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

from moviepy import (
    AudioFileClip,
    CompositeAudioClip,
    VideoFileClip,
    afx,
    concatenate_audioclips,
)

from gpt import generate_metadata, generate_script, get_search_terms
from logstream import log
from search import search_for_stock_videos
from tiktokvoice import tts
from utils import (
    OUTPUT_DIR,
    PROJECT_ROOT,
    SUBTITLES_DIR,
    TEMP_DIR,
    choose_random_song,
)
from video import combine_videos, generate_subtitles, generate_video, save_video
from youtube import resolve_privacy_status, upload_video


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


def run_generation_pipeline(
    data: dict,
    is_cancelled,
    on_log,
    amount_of_stock_videos: int = 5,
) -> PipelineResult:
    def emit(message: str, level: str = "info") -> None:
        log(message, level)
        if on_log:
            on_log(message, level)

    def guard_cancelled() -> None:
        if is_cancelled and is_cancelled():
            raise PipelineCancelled("Video generation was cancelled.")

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
    emit("   Custom Prompt: " + data["customPrompt"], "info")

    guard_cancelled()

    voice = data.get("voice", "")
    voice_prefix = voice[:2]

    if not voice:
        emit('[!] No voice was selected. Defaulting to "en_us_001"', "warning")
        voice = "en_us_001"
        voice_prefix = voice[:2]

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
        data["videoSubject"], amount_of_stock_videos, script, ai_model
    )

    video_urls = []
    it = 15
    min_dur = 10

    for search_term in search_terms:
        guard_cancelled()
        found_urls = search_for_stock_videos(
            search_term, os.getenv("PEXELS_API_KEY"), it, min_dur
        )
        for url in found_urls:
            if url not in video_urls:
                video_urls.append(url)
                break

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
    paths = []

    for sentence in sentences:
        guard_cancelled()
        current_tts_path = str(TEMP_DIR / f"{uuid4()}.mp3")
        tts(sentence, voice, filename=current_tts_path)
        audio_clip = AudioFileClip(current_tts_path)
        paths.append(audio_clip)

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
        combined_video_path = combine_videos(
            video_paths, temp_audio.duration, 5, n_threads or 2
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
        )
    except Exception as err:
        raise RuntimeError(
            f"Could not render final video. Check subtitle/font/ImageMagick setup. ({err})"
        ) from err

    title, description, keywords = generate_metadata(
        data["videoSubject"], script, ai_model
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
    render_threads = n_threads or (os.cpu_count() or 2)

    guard_cancelled()

    if use_music:
        song_path = choose_random_song()

        if not song_path:
            emit(
                "[-] Could not find songs in Songs/. Continuing without background music.",
                "warning",
            )
            use_music = False

        if use_music:
            video_clip = VideoFileClip(rendered_video_path)
            song_clip = None
            mixed_audio = None
            mixed_audio_path = str(TEMP_DIR / f"{uuid4()}_mixed_audio.m4a")
            try:
                original_duration = video_clip.duration
                original_audio = video_clip.audio
                song_clip = AudioFileClip(song_path).with_fps(44100)
                song_clip = song_clip.with_effects(
                    [afx.AudioLoop(duration=original_duration)]
                )
                song_clip = song_clip.with_volume_scaled(0.1).with_fps(44100)

                mixed_audio = CompositeAudioClip(
                    [original_audio, song_clip]
                ).with_duration(original_duration)
                mixed_audio.write_audiofile(
                    mixed_audio_path,
                    fps=44100,
                    codec="aac",
                    bitrate="192k",
                )
            finally:
                video_clip.close()
                if mixed_audio is not None:
                    mixed_audio.close()
                if song_clip is not None:
                    song_clip.close()

            try:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        rendered_video_path,
                        "-i",
                        mixed_audio_path,
                        "-map",
                        "0:v:0",
                        "-map",
                        "1:a:0",
                        "-c:v",
                        "copy",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "192k",
                        "-shortest",
                        final_output_path,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except Exception:
                emit(
                    "[!] ffmpeg remux failed. Falling back to MoviePy render for music mix.",
                    "warning",
                )
                video_clip = VideoFileClip(rendered_video_path)
                song_clip = None
                try:
                    original_duration = video_clip.duration
                    original_audio = video_clip.audio
                    song_clip = AudioFileClip(song_path).with_fps(44100)
                    song_clip = song_clip.with_effects(
                        [afx.AudioLoop(duration=original_duration)]
                    )
                    song_clip = song_clip.with_volume_scaled(0.1).with_fps(44100)
                    comp_audio = CompositeAudioClip(
                        [original_audio, song_clip]
                    ).with_duration(original_duration)
                    video_clip = (
                        video_clip.with_audio(comp_audio)
                        .with_fps(30)
                        .with_duration(original_duration)
                    )
                    video_clip.write_videofile(
                        final_output_path,
                        threads=render_threads,
                        fps=30,
                        codec="libx264",
                        audio_codec="aac",
                        preset="medium",
                    )
                finally:
                    video_clip.close()
                    if song_clip is not None:
                        song_clip.close()
            finally:
                if os.path.exists(mixed_audio_path):
                    os.remove(mixed_audio_path)

    if not use_music:
        shutil.copy2(rendered_video_path, final_output_path)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    archived_path = f"output/{job_id}.mp4"
    shutil.copy2(final_output_path, str(PROJECT_ROOT / archived_path))

    emit(f"[+] Video generated: {final_video_path} (archived as {archived_path})", "success")

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
            )
            emit(f"[+] Uploaded: https://youtu.be/{youtube_video_id}", "success")
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
    )
