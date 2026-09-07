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


def test_build_music_filter_applies_requested_volume():
    assert "[1:a]volume=0.4," in video.build_music_filter(30.0, music_volume=0.4)


# -- mix_background_music ---------------------------------------------------


def test_mix_background_music_copies_video_and_loops_music(monkeypatch):
    captured = {}

    monkeypatch.setattr(video, "probe_duration", lambda path: 25.0)

    def fake_run(command, **kwargs):
        captured["command"] = command
        return None

    monkeypatch.setattr(video.subprocess, "run", fake_run)

    video.mix_background_music("in.mp4", "song.mp3", "out.mp4")

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
        video.mix_background_music("in.mp4", "song.mp3", "out.mp4")
