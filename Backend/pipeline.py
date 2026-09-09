import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional
from uuid import uuid4

from moviepy import AudioFileClip, concatenate_audioclips

import research
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
from speech import ELEVENLABS, narration_plan, synthesize_sentences
from utils import (
    OUTPUT_DIR,
    PROJECT_ROOT,
    SUBTITLES_DIR,
    TEMP_DIR,
    choose_random_song,
)
from video import (
    combine_videos,
    make_silence,
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


def _with_section_pauses(
    plan: list, audio_paths: list, pause_seconds: float
) -> list:
    """Interleaves silence between sections, leaving none at either end.

    `plan` groups the chunks by section, so the boundaries are known without
    re-parsing the script.
    """
    if pause_seconds <= 0 or len(plan) < 2:
        return list(audio_paths)

    combined: list = []
    cursor = 0
    for index, section in enumerate(plan):
        if index:
            combined.append(
                make_silence(pause_seconds, str(TEMP_DIR / f"{uuid4()}.mp3"))
            )
        combined.extend(audio_paths[cursor : cursor + len(section)])
        cursor += len(section)
    return combined


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
    thumbnail_path: Optional[str]  # "output/<job_id>.jpg" relative to PROJECT_ROOT
    narration_provider: str
    narration_fell_back: bool
    script: str                # persisted by the worker; community posts read it
    ai_model: str


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
    # Length is the format's business, but a caller may ask for something else.
    target_words = int(data.get("targetWords") or 0) or None
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

    sources = []
    section_research = None
    if research.is_configured():
        sources = research.gather(
            [data["videoSubject"]], limit=fmt.research_results
        )

        def section_research(heading: str) -> str:
            """A brief for one section, so eight sections do not share one fact.

            Collected into `sources` as well, so the description cites what the
            script was actually written from.
            """
            found = research.gather(
                [f'{data["videoSubject"]} {heading}'], limit=fmt.research_results
            )
            for source in found:
                if all(source.url != existing.url for existing in sources):
                    sources.append(source)
            return research.format_brief(found)
        if sources:
            emit(f"[+] Research: {len(sources)} source(s) found.", "info")
        else:
            # Said out loud: without it the script falls back to writing with
            # no specifics at all, and the difference is invisible otherwise.
            emit("[!] Research returned nothing. Writing without specifics.", "warning")
    brief = research.format_brief(sources)

    if fmt is LONG:
        script = generate_long_script(
            data["videoSubject"],
            target_words or fmt.target_words,
            ai_model,
            voice,
            data["customPrompt"],
            section_count=fmt.section_count,
            research=brief,
            section_research=section_research,
        )
    else:
        script = generate_script(
            data["videoSubject"],
            paragraph_number,
            ai_model,
            voice,
            data["customPrompt"],
            target_words=target_words or fmt.target_words,
            research=brief,
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
    # A clip shorter than a shot cannot fill one, and the shortfall accumulates
    # across every such clip until the run needs one shot more than it has
    # footage for. Measured: seven of twenty clips came in under a twelve-second
    # cap and the video repeated itself once as a result.
    min_dur = int(fmt.max_clip_duration)

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

    # One chunk per section needs AssemblyAI: the local subtitle fallback times
    # cues from each clip's length, and a whole paragraph in one clip would give
    # a single cue lasting a minute.
    by_section = fmt.narrate_by_section and bool(
        os.getenv("ASSEMBLY_AI_API_KEY", "").strip()
    )
    if fmt.narrate_by_section and not by_section:
        emit(
            "[!] ASSEMBLY_AI_API_KEY is not set, so narration falls back to one "
            "chunk per sentence to keep subtitle timing usable.",
            "warning",
        )

    plan = narration_plan(script, by_section)
    sentences = [chunk for section in plan for chunk in section]
    if not sentences:
        raise RuntimeError("The script produced nothing to narrate.")

    guard_cancelled()
    audio_paths, provider = synthesize_sentences(
        sentences,
        make_path=lambda: str(TEMP_DIR / f"{uuid4()}.mp3"),
        tiktok_voice=voice,
        elevenlabs_voice_id=fmt.elevenlabs_voice_id,
        elevenlabs_model=fmt.elevenlabs_model,
        on_log=emit,
    )
    # Whether the format wanted the paid voice and did not get it. Recorded
    # rather than only logged: running out of credits is otherwise discovered
    # by listening to a finished video.
    narration_fell_back = bool(fmt.elevenlabs_voice_id) and provider != ELEVENLABS
    emit(f"[+] Narrated with {provider}", "warning" if narration_fell_back else "info")

    # Silence between sections, so one thought lands before the next starts.
    spoken_paths = _with_section_pauses(plan, audio_paths, fmt.section_pause_seconds)
    if fmt.outro_seconds > 0:
        # The picture is clamped to the audio, so this is what stops the video
        # ending on the final syllable.
        spoken_paths.append(
            make_silence(fmt.outro_seconds, str(TEMP_DIR / f"{uuid4()}.mp3"))
        )

    paths = [AudioFileClip(path) for path in spoken_paths]

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
        data["videoSubject"], script, ai_model, fmt.always_hashtags, fmt.metadata_label
    )

    description = research.append_sources(description, sources)

    emit("[+] Metadata for YouTube upload:", "info")
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
        # Archived next to the video, not left in temp/: the worker wipes temp/
        # at the start of the next job, which would leave the artifact row
        # pointing at a deleted file within hours of the upload.
        archived_thumbnail = f"{OUTPUT_DIR.name}/{job_id}.jpg"
        try:
            build_thumbnail(
                str(PROJECT_ROOT / archived_path),
                title,
                str(PROJECT_ROOT / archived_thumbnail),
                duration=probe_duration(str(PROJECT_ROOT / archived_path)),
                work_dir=TEMP_DIR / "thumbnail",
                ffmpeg=os.getenv("FFMPEG_BINARY", "").strip() or "ffmpeg",
            )
        except Exception as err:
            # A missing thumbnail costs clicks; a failed job costs the video.
            emit(f"[!] Could not build a thumbnail ({err}).", "warning")
        else:
            thumbnail_path = archived_thumbnail

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
                    upload_thumbnail(
                        youtube_video_id, str(PROJECT_ROOT / thumbnail_path)
                    )
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
        script=script,
        ai_model=ai_model,
        video_path=final_video_path,
        archived_path=archived_path,
        title=title,
        youtube_video_id=youtube_video_id,
        upload_error=upload_error,
        privacy_status=privacy_status,
        format_name=fmt.name,
        subtitles_path=subtitles_path,
        thumbnail_path=thumbnail_path,
        narration_provider=provider,
        narration_fell_back=narration_fell_back,
    )
