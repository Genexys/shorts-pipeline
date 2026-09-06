import json
from pathlib import Path

import pytest

import youtube
from youtube import YouTubeAuthError, resolve_privacy_status


def test_load_credentials_returns_none_without_token_file(tmp_path: Path):
    assert youtube.load_credentials(tmp_path / "missing.json") is None


def test_load_credentials_returns_none_for_malformed_file(tmp_path: Path):
    token_file = tmp_path / "youtube_token.json"
    token_file.write_text("not json")
    assert youtube.load_credentials(token_file) is None


class _FakeCredentials:
    def __init__(self, valid: bool, expired: bool, refresh_token: str | None):
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token
        self.refreshed = False

    def refresh(self, request) -> None:
        self.refreshed = True
        self.valid = True
        self.expired = False

    def to_json(self) -> str:
        return json.dumps({"token": "new"})


def test_load_credentials_refreshes_expired_token_and_rewrites_file(monkeypatch, tmp_path: Path):
    token_file = tmp_path / "youtube_token.json"
    token_file.write_text(json.dumps({"token": "old"}))
    fake = _FakeCredentials(valid=False, expired=True, refresh_token="r")
    monkeypatch.setattr(
        youtube.Credentials, "from_authorized_user_file", lambda path, scopes: fake
    )

    credentials = youtube.load_credentials(token_file)

    assert credentials is fake
    assert fake.refreshed is True
    assert json.loads(token_file.read_text()) == {"token": "new"}


def test_load_credentials_returns_none_when_refresh_fails(monkeypatch, tmp_path: Path):
    token_file = tmp_path / "youtube_token.json"
    token_file.write_text(json.dumps({"token": "old"}))
    fake = _FakeCredentials(valid=False, expired=True, refresh_token="r")

    def failing_refresh(request) -> None:
        raise RuntimeError("invalid_grant")

    fake.refresh = failing_refresh
    monkeypatch.setattr(
        youtube.Credentials, "from_authorized_user_file", lambda path, scopes: fake
    )

    assert youtube.load_credentials(token_file) is None


def test_get_authenticated_service_raises_without_credentials(monkeypatch):
    monkeypatch.setattr(youtube, "load_credentials", lambda token_file=None: None)

    with pytest.raises(YouTubeAuthError, match="youtube_auth.py"):
        youtube.get_authenticated_service()


def test_resolve_privacy_status_defaults_and_warns():
    assert resolve_privacy_status(None) == ("private", None)
    assert resolve_privacy_status("") == ("private", None)
    assert resolve_privacy_status("Unlisted") == ("unlisted", None)
    status, warning = resolve_privacy_status("secret")
    assert status == "private"
    assert "secret" in warning


def test_upload_video_returns_video_id(monkeypatch):
    captured: dict = {}

    monkeypatch.setattr(youtube, "get_authenticated_service", lambda: object())

    def fake_initialize_upload(service, options: dict) -> dict:
        captured.update(options)
        return {"id": "abc123"}

    monkeypatch.setattr(youtube, "initialize_upload", fake_initialize_upload)

    video_id = youtube.upload_video(
        "/tmp/v.mp4", "Title", "Desc", "28", ["a", "b"], "private"
    )

    assert video_id == "abc123"
    assert captured["tags"] == ["a", "b"]
    assert captured["privacyStatus"] == "private"


def test_scopes_only_upload():
    assert youtube.SCOPES == ["https://www.googleapis.com/auth/youtube.upload"]
