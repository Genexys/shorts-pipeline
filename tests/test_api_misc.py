import io
import os

import pytest


os.environ.setdefault("PEXELS_API_KEY", "test-key")
os.environ.setdefault("TIKTOK_SESSION_ID", "test-session")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("IMAGEMAGICK_BINARY", "/bin/echo")
os.environ.setdefault("DATABASE_URL", "sqlite:///moneyprinter_api_misc_bootstrap.db")

import main


@pytest.fixture
def client():
    return main.app.test_client()


def test_models_endpoint_lists_the_claude_models(client, monkeypatch):
    # Claude writes every step; the list is SCRIPT_MODEL and its fallback.
    monkeypatch.setenv("SCRIPT_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("SCRIPT_FALLBACK_MODEL", "claude-sonnet-5-5")

    response = client.get("/api/models")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "success"
    assert payload["models"] == ["claude-opus-5-5", "claude-sonnet-5-5"]
    assert payload["default"] == "claude-opus-5-5"


def test_models_endpoint_without_a_fallback_lists_one(client, monkeypatch):
    monkeypatch.setenv("SCRIPT_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("SCRIPT_FALLBACK_MODEL", "")

    assert client.get("/api/models").get_json()["models"] == ["claude-opus-5-5"]


def test_upload_songs_requires_files(client):
    response = client.post(
        "/api/upload-songs", data={}, content_type="multipart/form-data"
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "error"
    assert payload["message"] == "No files uploaded."


def test_upload_songs_rejects_non_mp3_files(client, monkeypatch, tmp_path):
    songs_dir = tmp_path / "Songs"
    songs_dir.mkdir()

    monkeypatch.setattr(main, "SONGS_DIR", songs_dir)
    monkeypatch.setattr(main, "clean_dir", lambda path: None)

    data = {
        "songs": (io.BytesIO(b"not-mp3"), "track.wav"),
    }
    response = client.post(
        "/api/upload-songs",
        data=data,
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "error"
    assert payload["message"] == "No MP3 files found."
    assert list(songs_dir.iterdir()) == []


def test_upload_songs_saves_mp3_and_sanitizes_filename(client, monkeypatch, tmp_path):
    songs_dir = tmp_path / "Songs"
    songs_dir.mkdir()
    stale_file = songs_dir / "stale.mp3"
    stale_file.write_bytes(b"old")

    def fake_clean_dir(path: str):
        assert path == str(songs_dir)
        for item in songs_dir.iterdir():
            if item.is_file():
                item.unlink()

    monkeypatch.setattr(main, "SONGS_DIR", songs_dir)
    monkeypatch.setattr(main, "clean_dir", fake_clean_dir)

    data = {
        "songs": [
            (io.BytesIO(b"song-a"), "../danger.mp3"),
            (io.BytesIO(b"song-b"), "safe.mp3"),
            (io.BytesIO(b"ignore"), "note.txt"),
        ]
    }
    response = client.post(
        "/api/upload-songs",
        data=data,
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "success"
    assert payload["message"] == "Uploaded 2 song(s)."

    saved_names = sorted(path.name for path in songs_dir.iterdir())
    assert saved_names == ["danger.mp3", "safe.mp3"]
