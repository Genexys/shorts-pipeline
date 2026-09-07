import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional
from uuid import uuid4

from moviepy import AudioFileClip, concatenate_audioclips

from formats import SHORT
from gpt import (
    generate_metadata,
    generate_script,
    get_search_terms,
    select_music_mood,
)
from logstream import log
from search import search_for_stock_videos
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
    generate_subtitles,
    generate_video,
    mix_background_music,
    save_video,
)
from youtube import resolve_language, resolve_privacy_status, upload_video


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

    # An explicit choice from the request wins; otherwise the format decides,
    # so Shorts and long form do not share a narrator.
    voice = data.get("voice", "")
    if not voice:
        voice = SHORT.voice
        emit(f'[!] No voice was selected. Using "{voice}"', "warning")
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
        data["videoSubject"], SHORT.search_term_count, script, ai_model
    )

    video_urls = []
    it = 15
    min_dur = 10

    for search_term in search_terms:
        guard_cancelled()
        found_urls = search_for_stock_videos(
            search_term, os.getenv("PEXELS_API_KEY"), it, min_dur
        )
        taken = 0
        for url in found_urls:
            if url in video_urls:
                continue
            video_urls.append(url)
            taken += 1
            if taken >= SHORT.clips_per_term:
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

    guard_cancelled()
    audio_paths, provider = synthesize_sentences(
        sentences,
        make_path=lambda: str(TEMP_DIR / f"{uuid4()}.mp3"),
        tiktok_voice=voice,
        elevenlabs_voice_id=SHORT.elevenlabs_voice_id,
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
            max_chars=SHORT.subtitle_max_chars,
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
        # The format still resolves to SHORT for every payload; reading it from
        # the request is a later step.
        combined_video_path = combine_videos(
            video_paths, temp_audio.duration, n_threads or 2, SHORT
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
            SHORT,
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
        shutil.copy2(rendered_video_path, final_output_path)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    archived_path = f"{OUTPUT_DIR.name}/{job_id}.mp4"
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
                language=resolve_language(voice),
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
