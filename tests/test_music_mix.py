from pathlib import Path

import pytest

import gpt
import utils
import video


# -- choose_random_song: theme folders ------------------------------------


def test_choose_random_song_prefers_theme_folder(monkeypatch, tmp_path: Path):
    songs_dir = tmp_path / "Songs"
    (songs_dir / "history").mkdir(parents=True)
    flat = songs_dir / "flat.mp3"
    theme_track = songs_dir / "history" / "quiet.mp3"
    flat.write_text("flat")
    theme_track.write_text("history")

    monkeypatch.setattr(utils, "SONGS_DIR", songs_dir)
    monkeypatch.setattr(utils.random, "choice", lambda songs: songs[0])

    assert utils.choose_random_song("history") == str(theme_track)


def test_choose_random_song_falls_back_when_theme_folder_is_empty(
    monkeypatch, tmp_path: Path
):
    songs_dir = tmp_path / "Songs"
    (songs_dir / "dark").mkdir(parents=True)
    flat = songs_dir / "flat.mp3"
    flat.write_text("flat")

    monkeypatch.setattr(utils, "SONGS_DIR", songs_dir)
    monkeypatch.setattr(utils.random, "choice", lambda songs: songs[0])

    assert utils.choose_random_song("dark") == str(flat)


def test_choose_random_song_falls_back_when_theme_folder_is_missing(
    monkeypatch, tmp_path: Path
):
    songs_dir = tmp_path / "Songs"
    songs_dir.mkdir()
    flat = songs_dir / "flat.mp3"
    flat.write_text("flat")

    monkeypatch.setattr(utils, "SONGS_DIR", songs_dir)
    monkeypatch.setattr(utils.random, "choice", lambda songs: songs[0])

    assert utils.choose_random_song("nosuchtheme") == str(flat)


def test_choose_random_song_ignores_non_mp3_in_theme_folder(
    monkeypatch, tmp_path: Path
):
    songs_dir = tmp_path / "Songs"
    (songs_dir / "history").mkdir(parents=True)
    (songs_dir / "history" / "notes.txt").write_text("ignore")
    flat = songs_dir / "flat.mp3"
    flat.write_text("flat")

    monkeypatch.setattr(utils, "SONGS_DIR", songs_dir)
    monkeypatch.setattr(utils.random, "choice", lambda songs: songs[0])

    assert utils.choose_random_song("history") == str(flat)


# -- choose_random_song: recent tracks --------------------------------------


def _theme_folder(tmp_path: Path, names: list) -> Path:
    songs_dir = tmp_path / "Songs"
    (songs_dir / "space").mkdir(parents=True)
    for name in names:
        (songs_dir / "space" / name).write_text(name)
    return songs_dir


def test_choose_random_song_skips_recently_played_tracks(monkeypatch, tmp_path: Path):
    songs_dir = _theme_folder(tmp_path, ["a.mp3", "b.mp3", "c.mp3"])
    monkeypatch.setattr(utils, "SONGS_DIR", songs_dir)
    for _ in range(20):
        chosen = utils.choose_random_song("space", avoid=["a.mp3", "b.mp3"])
        assert chosen == str(songs_dir / "space" / "c.mp3")


def test_choose_random_song_repeats_rather_than_going_silent(monkeypatch, tmp_path: Path):
    # A theme whose every track played recently still gets music.
    songs_dir = _theme_folder(tmp_path, ["a.mp3", "b.mp3"])
    monkeypatch.setattr(utils, "SONGS_DIR", songs_dir)
    chosen = utils.choose_random_song("space", avoid=["a.mp3", "b.mp3"])
    assert chosen in {str(songs_dir / "space" / "a.mp3"), str(songs_dir / "space" / "b.mp3")}


# -- select_music_theme ------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_claude(monkeypatch):
    # The picker asks the stronger model first; these tests drive the local
    # fallback unless they say otherwise.
    monkeypatch.setattr(gpt.writer, "write_with_model", lambda prompt: None)


def test_select_music_theme_prefers_the_stronger_model(monkeypatch):
    monkeypatch.setattr(
        gpt.writer, "write_with_model",
        lambda prompt: gpt.writer.Written('{"theme": "space"}', "claude-opus-5-5"),
    )

    def local(prompt, model):
        raise AssertionError("the local model must not be asked")

    monkeypatch.setattr(gpt, "generate_response", local)
    assert gpt.select_music_theme("Sputnik", "A beeping sphere.", "llama3.1:8b") == "space"


def test_every_theme_is_described_by_subject():
    # The picker reads the descriptions, and the library folders are named after
    # the keys; a theme without a description could never be chosen well.
    assert set(utils.MUSIC_THEMES) == {
        "everyday", "body", "space", "invention", "history", "quirky", "dark",
    }
    assert all(len(about.split()) >= 5 for about in utils.MUSIC_THEMES.values())


def test_select_music_theme_shows_the_model_every_theme(monkeypatch):
    prompts = []
    monkeypatch.setattr(
        gpt, "generate_response", lambda p, m: prompts.append(p) or '{"theme": "space"}'
    )
    gpt.select_music_theme("Sputnik", "A beeping sphere.", "model")
    for name, about in utils.MUSIC_THEMES.items():
        assert f"- {name}: {about}" in prompts[0]


def test_select_music_theme_returns_known_theme(monkeypatch):
    monkeypatch.setattr(gpt, "generate_response", lambda p, m: '{"theme": "Space"}')
    assert gpt.select_music_theme("subject", "script", "model") == "space"


def test_select_music_theme_reads_theme_out_of_surrounding_text(monkeypatch):
    monkeypatch.setattr(
        gpt, "generate_response", lambda p, m: 'Sure! {"theme": "dark"} hope that helps'
    )
    assert gpt.select_music_theme("subject", "script", "model") == "dark"


def test_select_music_theme_rejects_an_old_mood(monkeypatch):
    # The four moods are gone; a model that still answers with one gets the
    # flat-folder fallback, not a folder that no longer exists.
    monkeypatch.setattr(gpt, "generate_response", lambda p, m: '{"theme": "curious"}')
    assert gpt.select_music_theme("subject", "script", "model") is None


def test_select_music_theme_returns_none_on_ollama_failure(monkeypatch):
    def boom(prompt, model):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(gpt, "generate_response", boom)
    assert gpt.select_music_theme("subject", "script", "model") is None


def test_select_music_theme_returns_none_for_garbage(monkeypatch):
    monkeypatch.setattr(gpt, "generate_response", lambda p, m: "no json here")
    assert gpt.select_music_theme("subject", "script", "model") is None


# -- recent_music_tracks -----------------------------------------------------


def test_recent_music_tracks_reads_the_newest_videos_first(session_factory):
    from repository import add_artifact, create_job, recent_music_tracks

    with session_factory() as session:
        for music in ("old.mp3", None, "mid.mp3", "new.mp3"):
            job = create_job(session, payload={"videoSubject": "x"})
            add_artifact(session, job.id, "video", f"output/{job.id}.mp4", {"music": music})
            add_artifact(session, job.id, "thumbnail", f"output/{job.id}.jpg", {"music": "not.mp3"})
        assert recent_music_tracks(session, limit=3) == ["new.mp3", "mid.mp3"]
        assert recent_music_tracks(session, limit=10) == ["new.mp3", "mid.mp3", "old.mp3"]


def test_recent_music_tracks_tolerates_videos_without_metadata(session_factory):
    from repository import add_artifact, create_job, recent_music_tracks

    with session_factory() as session:
        job = create_job(session, payload={"videoSubject": "x"})
        add_artifact(session, job.id, "video", f"output/{job.id}.mp4", None)
        assert recent_music_tracks(session) == []


# -- build_music_filter -----------------------------------------------------


def test_build_music_filter_places_fade_out_before_the_end():
    graph = video.build_music_filter(30.0)
    assert f"afade=t=out:st={30.0 - video.MUSIC_FADE_OUT_SECONDS:.3f}" in graph


def test_build_music_filter_clamps_fade_out_for_very_short_video():
    graph = video.build_music_filter(1.0)
    assert "afade=t=out:st=0.000" in graph


def test_build_music_filter_keys_sidechain_off_the_voice():
    graph = video.build_music_filter(30.0)
    # The voice must be split so one copy drives the compressor and the other
    # is mixed; keying off the mix would make the ducking self-referential.
    assert "[0:a]asplit=2[voice][key]" in graph
    assert "[bed][key]sidechaincompress=" in graph
    assert "[voice][duck]amix=" in graph


def test_build_music_filter_normalizes_loudness_last():
    graph = video.build_music_filter(30.0)
    assert graph.index("amix=") < graph.index("loudnorm=")
    assert f"loudnorm=I={video.LOUDNESS_TARGET_LUFS}" in graph


def test_build_music_filter_applies_requested_gain():
    assert "volume=-7.50dB," in video.build_music_filter(30.0, gain_db=-7.5)


def test_build_music_filter_levels_the_bed_before_attenuating_it():
    graph = video.build_music_filter(30.0)
    # Levelling has to happen on the raw track. Attenuating first would leave
    # only the transients above the masking threshold for the leveller to see.
    assert graph.startswith(f"[1:a]{video.MUSIC_LEVELER},")
    assert graph.index(video.MUSIC_LEVELER) < graph.index("volume=")


def test_build_music_filter_uses_a_moderate_duck_ratio():
    # A levelled bed crushed by a heavy ratio is inaudible again.
    assert video.MUSIC_DUCK_RATIO <= 4
    assert f"ratio={video.MUSIC_DUCK_RATIO}" in video.build_music_filter(30.0)


# -- mix_background_music ---------------------------------------------------


def test_mix_background_music_copies_video_and_loops_music(monkeypatch):
    captured = {}

    monkeypatch.setattr(video, "probe_duration", lambda path: 25.0)

    def fake_run(command, **kwargs):
        captured["command"] = command
        return None

    monkeypatch.setattr(video.subprocess, "run", fake_run)

    video.mix_background_music("in.mp4", "song.mp3", "out.mp4", gain_db=-6.0)

    command = captured["command"]
    # Never re-encode the picture for an audio-only change.
    assert "-c:v" in command and command[command.index("-c:v") + 1] == "copy"
    # The bed must loop to cover the whole video, then be trimmed.
    assert "-stream_loop" in command
    assert command[command.index("-stream_loop") + 1] == "-1"
    assert "-shortest" in command
    assert command[-1] == "out.mp4"


def test_mix_background_music_propagates_ffmpeg_failure(monkeypatch):
    monkeypatch.setattr(video, "probe_duration", lambda path: 25.0)

    def fake_run(command, **kwargs):
        raise video.subprocess.CalledProcessError(1, command, stderr="boom")

    monkeypatch.setattr(video.subprocess, "run", fake_run)

    with pytest.raises(video.subprocess.CalledProcessError):
        video.mix_background_music("in.mp4", "song.mp3", "out.mp4", gain_db=-6.0)


def test_mix_background_music_measures_the_track_when_no_gain_given(monkeypatch):
    captured = {}
    monkeypatch.setattr(video, "probe_duration", lambda path: 25.0)
    monkeypatch.setattr(video, "resolve_music_gain_db", lambda path: -9.25)
    monkeypatch.setattr(
        video.subprocess, "run", lambda command, **kw: captured.update(command=command)
    )

    video.mix_background_music("in.mp4", "song.mp3", "out.mp4")

    graph = captured["command"][captured["command"].index("-filter_complex") + 1]
    assert "volume=-9.25dB," in graph


# -- voice-band measurement -------------------------------------------------


EBUR128_SUMMARY = """[Parsed_ebur128_0 @ 0x1] Summary:

  Integrated loudness:
    I:         -20.1 LUFS
    Threshold: -30.4 LUFS

  Loudness range:
    LRA:         3.1 LU
    Threshold: -40.2 LUFS
"""


def test_parse_integrated_loudness_reads_the_i_value():
    assert video._parse_integrated_loudness(EBUR128_SUMMARY) == -20.1


def test_parse_integrated_loudness_ignores_the_threshold_line():
    # 'Threshold' sits directly under 'I:' and carries a similar-looking number;
    # picking by offset instead of by label would return it.
    assert video._parse_integrated_loudness(EBUR128_SUMMARY) != -30.4


def test_parse_integrated_loudness_returns_none_without_a_summary():
    assert video._parse_integrated_loudness("ffmpeg: no such file") is None


def test_resolve_music_gain_db_normalizes_to_the_target(monkeypatch):
    monkeypatch.setattr(video, "measure_voiceband_loudness", lambda path: -20.1)
    expected = video.MUSIC_VOICEBAND_TARGET_LUFS - (-20.1)
    assert video.resolve_music_gain_db("song.mp3") == pytest.approx(expected)


def test_resolve_music_gain_db_falls_back_when_measurement_fails(monkeypatch):
    monkeypatch.setattr(video, "measure_voiceband_loudness", lambda path: None)
    assert video.resolve_music_gain_db("song.mp3") == video.MUSIC_FALLBACK_GAIN_DB


def test_measure_voiceband_loudness_returns_none_if_ffmpeg_fails(monkeypatch):
    def boom(command, **kwargs):
        raise video.subprocess.CalledProcessError(1, command, stderr="nope")

    monkeypatch.setattr(video.subprocess, "run", boom)
    assert video.measure_voiceband_loudness("song.mp3") is None


def test_measure_voiceband_loudness_filters_to_the_voice_band(monkeypatch):
    captured = {}

    class Result:
        stderr = EBUR128_SUMMARY

    def fake_run(command, **kwargs):
        captured["command"] = command
        return Result()

    monkeypatch.setattr(video.subprocess, "run", fake_run)

    assert video.measure_voiceband_loudness("song.mp3") == -20.1
    graph = captured["command"][captured["command"].index("-af") + 1]
    assert f"highpass=f={video.VOICE_BAND_LOW_HZ}" in graph
    assert f"lowpass=f={video.VOICE_BAND_HIGH_HZ}" in graph
    # The track is measured after levelling, since that is what the mix uses.
    assert graph.startswith(video.MUSIC_LEVELER)


# -- loudness without music -------------------------------------------------


def test_normalize_audio_copies_the_video_stream(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        video.subprocess, "run", lambda command, **kw: captured.update(command=command)
    )
    video.normalize_audio("in.mp4", "out.mp4")
    command = captured["command"]
    assert command[command.index("-c:v") + 1] == "copy"
    assert f"loudnorm=I={video.LOUDNESS_TARGET_LUFS}" in command[command.index("-af") + 1]
    assert command[-1] == "out.mp4"


def test_normalize_audio_targets_the_same_level_as_the_music_mix(monkeypatch):
    # A video with music and one without must not arrive at different levels.
    monkeypatch.setattr(video.subprocess, "run", lambda command, **kw: None)
    graph = video.build_music_filter(30.0)
    assert f"I={video.LOUDNESS_TARGET_LUFS}" in graph


def test_normalize_audio_sets_the_delivery_sample_rate(monkeypatch):
    # loudnorm hands its 192 kHz analysis rate to the encoder, which clamps to
    # its own 96 kHz maximum. Every video shipped before 2026-09-20 carries a
    # 96 kHz track; YouTube accepts it, Instagram Reels and Threads specify
    # 48 kHz or below.
    captured = {}
    monkeypatch.setattr(
        video.subprocess, "run", lambda command, **kw: captured.update(command=command)
    )
    video.normalize_audio("in.mp4", "out.mp4")
    command = captured["command"]
    assert command[command.index("-ar") + 1] == "48000"


def test_every_audio_encode_sets_the_sample_rate():
    # An output option, not an aresample filter: appending one after loudnorm
    # fails to negotiate a channel layout on the image's ffmpeg 5.1.
    assert "-ar" in video.DELIVERY_AUDIO_ARGS
    assert video.DELIVERY_AUDIO_ARGS[video.DELIVERY_AUDIO_ARGS.index("-ar") + 1] == "48000"
    assert "aresample" not in video.build_music_filter(30.0)


def test_normalize_audio_puts_the_index_at_the_front(monkeypatch):
    # Instagram Reels specifies "moov atom at the front of the file". ffmpeg
    # writes it last by default, so every video published before 2026-09-20
    # has it after the media data.
    captured = {}
    monkeypatch.setattr(
        video.subprocess, "run", lambda command, **kw: captured.update(command=command)
    )
    video.normalize_audio("in.mp4", "out.mp4")
    command = captured["command"]
    assert command[command.index("-movflags") + 1] == "+faststart"
