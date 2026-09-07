from pathlib import Path

import pytest

import gpt
import utils
import video


# -- choose_random_song: mood folders -------------------------------------


def test_choose_random_song_prefers_mood_folder(monkeypatch, tmp_path: Path):
    songs_dir = tmp_path / "Songs"
    (songs_dir / "calm").mkdir(parents=True)
    flat = songs_dir / "flat.mp3"
    mood_track = songs_dir / "calm" / "quiet.mp3"
    flat.write_text("flat")
    mood_track.write_text("calm")

    monkeypatch.setattr(utils, "SONGS_DIR", songs_dir)
    monkeypatch.setattr(utils.random, "choice", lambda songs: songs[0])

    assert utils.choose_random_song("calm") == str(mood_track)


def test_choose_random_song_falls_back_when_mood_folder_is_empty(
    monkeypatch, tmp_path: Path
):
    songs_dir = tmp_path / "Songs"
    (songs_dir / "tense").mkdir(parents=True)
    flat = songs_dir / "flat.mp3"
    flat.write_text("flat")

    monkeypatch.setattr(utils, "SONGS_DIR", songs_dir)
    monkeypatch.setattr(utils.random, "choice", lambda songs: songs[0])

    assert utils.choose_random_song("tense") == str(flat)


def test_choose_random_song_falls_back_when_mood_folder_is_missing(
    monkeypatch, tmp_path: Path
):
    songs_dir = tmp_path / "Songs"
    songs_dir.mkdir()
    flat = songs_dir / "flat.mp3"
    flat.write_text("flat")

    monkeypatch.setattr(utils, "SONGS_DIR", songs_dir)
    monkeypatch.setattr(utils.random, "choice", lambda songs: songs[0])

    assert utils.choose_random_song("nosuchmood") == str(flat)


def test_choose_random_song_ignores_non_mp3_in_mood_folder(
    monkeypatch, tmp_path: Path
):
    songs_dir = tmp_path / "Songs"
    (songs_dir / "calm").mkdir(parents=True)
    (songs_dir / "calm" / "notes.txt").write_text("ignore")
    flat = songs_dir / "flat.mp3"
    flat.write_text("flat")

    monkeypatch.setattr(utils, "SONGS_DIR", songs_dir)
    monkeypatch.setattr(utils.random, "choice", lambda songs: songs[0])

    assert utils.choose_random_song("calm") == str(flat)


# -- select_music_mood ------------------------------------------------------


def test_select_music_mood_returns_known_mood(monkeypatch):
    monkeypatch.setattr(gpt, "generate_response", lambda p, m: '{"mood": "Calm"}')
    assert gpt.select_music_mood("subject", "script", "model") == "calm"


def test_select_music_mood_reads_mood_out_of_surrounding_text(monkeypatch):
    monkeypatch.setattr(
        gpt, "generate_response", lambda p, m: 'Sure! {"mood": "tense"} hope that helps'
    )
    assert gpt.select_music_mood("subject", "script", "model") == "tense"


def test_select_music_mood_rejects_unknown_mood(monkeypatch):
    monkeypatch.setattr(gpt, "generate_response", lambda p, m: '{"mood": "spooky"}')
    assert gpt.select_music_mood("subject", "script", "model") is None


def test_select_music_mood_returns_none_on_ollama_failure(monkeypatch):
    def boom(prompt, model):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(gpt, "generate_response", boom)
    assert gpt.select_music_mood("subject", "script", "model") is None


def test_select_music_mood_returns_none_for_garbage(monkeypatch):
    monkeypatch.setattr(gpt, "generate_response", lambda p, m: "no json here")
    assert gpt.select_music_mood("subject", "script", "model") is None


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
