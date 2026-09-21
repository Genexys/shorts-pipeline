import json
from pathlib import Path

import pytest

import instagram


class FakeResponse:
    def __init__(self, payload, ok: bool = True, status_code: int = 200, text=None):
        self._payload = payload
        self.ok = ok
        self.status_code = status_code
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Records every call and replays queued responses in order."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        return self._next("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._next("POST", url, **kwargs)


def credentials():
    return instagram.Credentials("TOKEN", "17841400000000000", "1234567890")


# -- captions -----------------------------------------------------------------


DESCRIPTION = (
    "Cats purr at 25-50 hertz, matching bone-healing frequencies.\n"
    "\n"
    "#Shorts #CatPurr #BoneHealing\n"
    "\n"
    "Sources:\n"
    "The complicated truth about a cat's purr - BBC - https://www.bbc.com/future/x\n"
)


def test_caption_leads_with_the_title():
    # A Reels caption has no title field, so the best line in the video would
    # otherwise never be shown at all.
    caption = instagram.build_caption("Cats purr at healing frequencies", DESCRIPTION)
    assert caption.startswith("Cats purr at healing frequencies")


def test_caption_drops_the_sources_block():
    # Links are not clickable in an Instagram caption; five bare URLs cost a
    # few hundred characters and buy nothing.
    caption = instagram.build_caption("Title", DESCRIPTION)
    assert "Sources:" not in caption
    assert "bbc.com" not in caption


def test_caption_renames_the_youtube_tag():
    caption = instagram.build_caption("Title", DESCRIPTION)
    assert "#Shorts" not in caption
    assert "#Reels" in caption
    assert "#CatPurr" in caption


def test_caption_keeps_the_tags_when_it_has_to_cut():
    # Tags are how the post is found; prose is what goes.
    long_description = ("word " * 900) + "\n\n#Shorts #Physics\n"
    caption = instagram.build_caption("Title", long_description, max_chars=200)
    assert len(caption) <= 200
    assert caption.endswith("#Reels #Physics")


def test_caption_respects_the_hashtag_ceiling():
    tags = " ".join(f"#tag{n}" for n in range(40))
    caption = instagram.build_caption("Title", f"Body\n\n{tags}\n")
    assert caption.count("#") == instagram.CAPTION_MAX_HASHTAGS


def test_caption_survives_a_description_with_no_tags():
    assert instagram.build_caption("Title", "Just prose.") == "Title\n\nJust prose."


# -- credentials --------------------------------------------------------------


def test_load_credentials_returns_none_when_the_file_is_missing(tmp_path: Path):
    assert instagram.load_credentials(tmp_path / "nope.json") is None


def test_load_credentials_rejects_a_file_without_an_account_id(tmp_path: Path):
    token = tmp_path / "instagram_token.json"
    token.write_text(json.dumps({"access_token": "T"}))
    assert instagram.load_credentials(token) is None


def test_load_credentials_reads_the_token(tmp_path: Path):
    token = tmp_path / "instagram_token.json"
    token.write_text(json.dumps({"access_token": "T", "ig_user_id": "42", "page_id": "7"}))
    loaded = instagram.load_credentials(token)
    assert (loaded.access_token, loaded.ig_user_id, loaded.page_id) == ("T", "42", "7")


def test_require_credentials_names_the_script_that_makes_them(tmp_path: Path):
    with pytest.raises(instagram.InstagramAuthError, match="instagram_auth.py"):
        instagram.require_credentials(tmp_path / "nope.json")


# -- the four calls -----------------------------------------------------------


def test_create_container_asks_for_a_resumable_reel():
    session = FakeSession(FakeResponse({"id": "C1", "uri": "https://rupload/x/C1"}))
    container_id, uri = instagram.create_container(
        credentials(), "caption here", session=session
    )
    assert (container_id, uri) == ("C1", "https://rupload/x/C1")
    params = session.calls[0]["params"]
    assert params["media_type"] == "REELS"
    assert params["upload_type"] == "resumable"
    assert params["caption"] == "caption here"


def test_create_container_reports_a_named_subcode():
    session = FakeSession(
        FakeResponse(
            {"error": {"error_subcode": 2207010, "message": "caption too long"}},
            ok=False,
            status_code=400,
        )
    )
    with pytest.raises(instagram.InstagramUploadError, match="2200 characters"):
        instagram.create_container(credentials(), "x" * 3000, session=session)


def test_upload_bytes_uses_oauth_not_bearer(tmp_path: Path):
    # The rupload host takes "OAuth <token>"; the graph host takes "Bearer".
    # They are not interchangeable and this is the only call that differs.
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"0123456789")
    session = FakeSession(FakeResponse({"success": True}))
    instagram.upload_bytes("https://rupload/x/C1", "TOKEN", video, session=session)
    headers = session.calls[0]["headers"]
    assert headers["Authorization"] == "OAuth TOKEN"
    assert headers["offset"] == "0"
    assert headers["file_size"] == "10"


def test_upload_bytes_refuses_a_file_over_the_limit(tmp_path: Path, monkeypatch):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(instagram, "MAX_FILE_BYTES", 0)
    with pytest.raises(instagram.InstagramUploadError, match="300 MB"):
        instagram.upload_bytes("https://rupload/x/C1", "T", video)


def test_upload_bytes_rejects_a_body_that_is_not_success(tmp_path: Path):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"x")
    session = FakeSession(FakeResponse({"debug_info": {"type": "ProcessingFailedError"}}))
    with pytest.raises(instagram.InstagramUploadError, match="not accepted"):
        instagram.upload_bytes("https://rupload/x/C1", "T", video, session=session)


def test_wait_until_ready_returns_on_finished():
    session = FakeSession(
        FakeResponse({"status_code": "IN_PROGRESS"}),
        FakeResponse({"status_code": "FINISHED"}),
    )
    instagram.wait_until_ready(
        credentials(), "C1", schedule=(0, 0), session=session, sleep=lambda _: None
    )
    assert session.calls[0]["params"]["fields"] == "status_code,status"


def test_wait_until_ready_surfaces_the_error_subcode():
    session = FakeSession(FakeResponse({"status_code": "ERROR", "status": "2207026"}))
    with pytest.raises(instagram.InstagramUploadError, match="2207026"):
        instagram.wait_until_ready(
            credentials(), "C1", schedule=(0,), session=session, sleep=lambda _: None
        )


def test_wait_until_ready_gives_up_rather_than_polling_forever():
    session = FakeSession(*[FakeResponse({"status_code": "IN_PROGRESS"})] * 2)
    with pytest.raises(instagram.InstagramUploadError, match="still IN_PROGRESS"):
        instagram.wait_until_ready(
            credentials(), "C1", schedule=(0, 0), session=session, sleep=lambda _: None
        )


def test_publish_container_returns_the_media_id():
    session = FakeSession(FakeResponse({"id": "17920238422030506"}))
    media_id = instagram.publish_container(credentials(), "C1", session=session)
    assert media_id == "17920238422030506"
    assert session.calls[0]["params"]["creation_id"] == "C1"


def test_publishing_quota_asks_the_account_rather_than_guessing():
    # Meta's own pages give the cap as both 50 and 100.
    session = FakeSession(
        FakeResponse({"data": [{"quota_usage": 2, "config": {"quota_total": 50}}]})
    )
    assert instagram.publishing_quota(credentials(), session=session) == (2, 50)


def test_poll_schedule_stays_inside_metas_five_minute_ceiling():
    assert sum(instagram.POLL_SCHEDULE_SECONDS) <= 300
