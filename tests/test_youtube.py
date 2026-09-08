import json
import stat
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
        youtube.Credentials, "from_authorized_user_file", lambda path: fake
    )

    credentials = youtube.load_credentials(token_file)

    assert credentials is fake
    assert fake.refreshed is True
    assert json.loads(token_file.read_text()) == {"token": "new"}
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600


def test_load_credentials_returns_none_when_refresh_fails(monkeypatch, tmp_path: Path):
    token_file = tmp_path / "youtube_token.json"
    token_file.write_text(json.dumps({"token": "old"}))
    fake = _FakeCredentials(valid=False, expired=True, refresh_token="r")

    def failing_refresh(request) -> None:
        raise RuntimeError("invalid_grant")

    fake.refresh = failing_refresh
    monkeypatch.setattr(
        youtube.Credentials, "from_authorized_user_file", lambda path: fake
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


def test_scopes_are_exactly_what_the_calls_need():
    # Every scope here has to be justified: force-ssl allows managing and
    # deleting channel content, and is only present because captions.insert
    # accepts nothing narrower.
    assert youtube.SCOPES == [youtube.UPLOAD_SCOPE, youtube.CAPTION_SCOPE]
    assert youtube.UPLOAD_SCOPE.endswith("/youtube.upload")
    assert youtube.CAPTION_SCOPE.endswith("/youtube.force-ssl")


def test_existing_tokens_are_loaded_with_their_own_scopes(monkeypatch, tmp_path):
    # Passing a wider list makes google-auth compare requested against granted
    # on the next refresh and raise RefreshError, which would stop uploads
    # within the hour on any deployment whose token predates the caption scope.
    captured = {}

    class FakeCredentials:
        valid = True
        scopes = [youtube.UPLOAD_SCOPE]

    def fake_from_file(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeCredentials()

    token = tmp_path / "token.json"
    token.write_text("{}")
    monkeypatch.setattr(youtube.Credentials, "from_authorized_user_file", fake_from_file)

    youtube.load_credentials(token)

    assert len(captured["args"]) == 1
    assert not captured["kwargs"]


def test_upload_captions_refuses_an_upload_only_token(monkeypatch):
    class FakeCredentials:
        scopes = [youtube.UPLOAD_SCOPE]

    monkeypatch.setattr(youtube, "load_credentials", lambda *a, **k: FakeCredentials())
    with pytest.raises(youtube.YouTubeAuthError, match="upload-only"):
        youtube.upload_captions("vid", "subs.srt")


def test_has_scope_reads_the_granted_list():
    class WithScope:
        scopes = [youtube.UPLOAD_SCOPE, youtube.CAPTION_SCOPE]

    class WithoutScope:
        scopes = None

    assert youtube.has_scope(WithScope(), youtube.CAPTION_SCOPE)
    assert not youtube.has_scope(WithoutScope(), youtube.CAPTION_SCOPE)


class _FakeInsertRequest:
    def __init__(self, response: dict):
        self._response = response

    def next_chunk(self):
        return None, self._response


class _FakeVideosResource:
    def __init__(self):
        self.insert_kwargs: dict = {}

    def insert(self, **kwargs):
        self.insert_kwargs = kwargs
        return _FakeInsertRequest({"id": "fake-video-id"})


class _FakeYouTube:
    def __init__(self):
        self._videos = _FakeVideosResource()

    def videos(self):
        return self._videos


def test_initialize_upload_builds_expected_request_body(monkeypatch, tmp_path: Path):
    video_file = tmp_path / "v.mp4"
    video_file.write_bytes(b"not really a video")
    fake_youtube = _FakeYouTube()

    response = youtube.initialize_upload(
        fake_youtube,
        {
            "file": str(video_file),
            "title": "My Title",
            "description": "My Description",
            "category": "28",
            "tags": [],
            "privacyStatus": "private",
        },
    )

    assert response == {"id": "fake-video-id"}
    kwargs = fake_youtube._videos.insert_kwargs
    assert kwargs["part"] == "snippet,status"

    body = kwargs["body"]
    assert body["snippet"]["title"] == "My Title"
    assert body["snippet"]["description"] == "My Description"
    assert body["snippet"]["tags"] is None
    assert body["snippet"]["categoryId"] == "28"
    assert isinstance(body["snippet"]["categoryId"], str)
    assert body["status"]["privacyStatus"] == "private"

    media_body = kwargs["media_body"]
    assert isinstance(media_body, youtube.MediaFileUpload)


def test_initialize_upload_keeps_non_empty_tags(tmp_path: Path):
    video_file = tmp_path / "v.mp4"
    video_file.write_bytes(b"not really a video")
    fake_youtube = _FakeYouTube()

    youtube.initialize_upload(
        fake_youtube,
        {
            "file": str(video_file),
            "title": "T",
            "description": "D",
            "category": "28",
            "tags": ["a", "b"],
            "privacyStatus": "public",
        },
    )

    body = fake_youtube._videos.insert_kwargs["body"]
    assert body["snippet"]["tags"] == ["a", "b"]
