import pytest

import autopilot
import startup_check
import worker


@pytest.fixture
def mounts(tmp_path):
    """A token and a songs library laid out the way the compose mounts are."""
    token = tmp_path / "secrets" / "youtube_token.json"
    token.parent.mkdir()
    token.write_text('{"token": "x"}')
    songs = tmp_path / "Songs"
    (songs / "curious").mkdir(parents=True)
    (songs / "curious" / "track.mp3").write_bytes(b"ID3")
    return token, songs


@pytest.fixture
def calls():
    return {"notify": [], "sleep": []}


def _run(token, songs, need_songs, calls, marker):
    startup_check.require_mounts(
        "worker",
        token_file=token,
        songs_dir=songs,
        need_songs=need_songs,
        notify=calls["notify"].append,
        sleep=calls["sleep"].append,
        marker=marker,
    )


def test_disabled_by_default_even_when_everything_is_missing(tmp_path, calls, monkeypatch):
    # A deployment with no YouTube and no music is legitimate and must start.
    monkeypatch.delenv("REQUIRE_MOUNTS", raising=False)
    _run(tmp_path / "nope.json", tmp_path / "nope", True, calls, tmp_path / "m")
    assert calls == {"notify": [], "sleep": []}


def test_passes_when_token_and_songs_are_visible(mounts, calls, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REQUIRE_MOUNTS", "true")
    token, songs = mounts
    _run(token, songs, True, calls, tmp_path / "m")
    assert calls == {"notify": [], "sleep": []}
    assert "Startup check passed" in capsys.readouterr().out


def test_refuses_the_empty_secrets_mount_seen_on_15_september(mounts, calls, tmp_path, monkeypatch):
    # What Docker mounts when WSL integration is down: the directory exists and
    # is empty.
    monkeypatch.setenv("REQUIRE_MOUNTS", "true")
    _, songs = mounts
    empty = tmp_path / "empty-secrets"
    empty.mkdir()
    marker = tmp_path / "m"

    with pytest.raises(SystemExit) as exited:
        _run(empty / "youtube_token.json", songs, True, calls, marker)

    assert exited.value.code == 1
    assert len(calls["notify"]) == 1
    assert "force-recreate worker" in calls["notify"][0]
    # Paused before exiting, so a restart loop runs at a steady half minute.
    assert calls["sleep"] == [startup_check.RETRY_DELAY_SECONDS]
    assert marker.exists()


def test_refuses_an_empty_songs_mount_when_music_is_on(mounts, calls, tmp_path, monkeypatch):
    monkeypatch.setenv("REQUIRE_MOUNTS", "true")
    token, _ = mounts
    empty = tmp_path / "empty-songs"
    empty.mkdir()
    with pytest.raises(SystemExit):
        _run(token, empty, True, calls, tmp_path / "m")
    assert "no .mp3 files" in calls["notify"][0]


def test_does_not_need_songs_when_music_is_off(mounts, calls, tmp_path, monkeypatch):
    monkeypatch.setenv("REQUIRE_MOUNTS", "true")
    token, _ = mounts
    _run(token, tmp_path / "no-songs-at-all", False, calls, tmp_path / "m")
    assert calls["notify"] == []


def test_an_empty_token_file_counts_as_missing(mounts, calls, tmp_path, monkeypatch):
    monkeypatch.setenv("REQUIRE_MOUNTS", "true")
    token, songs = mounts
    token.write_text("")
    with pytest.raises(SystemExit):
        _run(token, songs, True, calls, tmp_path / "m")


def test_a_directory_with_no_mp3s_does_not_count_as_songs(mounts, calls, tmp_path, monkeypatch):
    monkeypatch.setenv("REQUIRE_MOUNTS", "true")
    token, _ = mounts
    songs = tmp_path / "just-notes"
    songs.mkdir()
    (songs / "README.txt").write_text("put music here")
    with pytest.raises(SystemExit):
        _run(token, songs, True, calls, tmp_path / "m")


def test_alerts_once_per_container_not_once_per_restart(mounts, calls, tmp_path, monkeypatch):
    # The marker survives a restart of the same container, so a crash loop does
    # not repeat the Telegram message; recreating the container clears it.
    monkeypatch.setenv("REQUIRE_MOUNTS", "true")
    _, songs = mounts
    marker = tmp_path / "m"
    marker.touch()
    with pytest.raises(SystemExit):
        _run(tmp_path / "missing.json", songs, True, calls, marker)
    assert calls["notify"] == []


def test_an_unwritable_marker_still_exits(mounts, calls, tmp_path, monkeypatch):
    monkeypatch.setenv("REQUIRE_MOUNTS", "true")
    _, songs = mounts
    with pytest.raises(SystemExit):
        _run(tmp_path / "missing.json", songs, True, calls, tmp_path / "no" / "such" / "m")
    assert len(calls["notify"]) == 1


def test_songs_required_follows_the_autopilot_music_switch(monkeypatch):
    monkeypatch.setenv("AUTOPILOT_USE_MUSIC", "true")
    assert startup_check.songs_required()
    monkeypatch.setenv("AUTOPILOT_USE_MUSIC", "false")
    assert not startup_check.songs_required()
    monkeypatch.delenv("AUTOPILOT_USE_MUSIC")
    assert not startup_check.songs_required()


# -- wiring: the check has to run before any work --------------------------------


class _Refused(Exception):
    pass


def _refuse(*args, **kwargs):
    raise _Refused


def test_worker_checks_mounts_before_touching_the_database(monkeypatch):
    touched = []
    monkeypatch.setattr(worker, "load_dotenv", lambda path: None)
    monkeypatch.setattr(worker, "require_mounts", _refuse)
    monkeypatch.setattr(worker, "init_db", lambda: touched.append("init_db"))
    monkeypatch.setattr(worker, "recover_running_jobs", lambda s: touched.append("recover"))

    with pytest.raises(_Refused):
        worker.main()

    assert touched == []


def test_autopilot_checks_mounts_before_touching_the_database(monkeypatch):
    touched = []
    monkeypatch.setattr(autopilot, "load_dotenv", lambda path: None)
    monkeypatch.setattr(autopilot, "require_mounts", _refuse)
    monkeypatch.setattr(autopilot, "init_db", lambda: touched.append("init_db"))

    with pytest.raises(_Refused):
        autopilot.main()

    assert touched == []
