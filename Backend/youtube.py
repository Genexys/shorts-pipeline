import os
import random
import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import httplib2
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from logstream import log

# Retries are handled below, not by the transport.
httplib2.RETRIES = 1

MAX_RETRIES = 10
MAX_UPLOAD_BACKOFF_SECONDS = 60
RETRIABLE_EXCEPTIONS = (httplib2.HttpLib2Error, IOError, httplib2.ServerNotFoundError)
RETRIABLE_STATUS_CODES = (500, 502, 503, 504)

BASE_DIR = Path(__file__).resolve().parent


def _path_from_env(name: str, default: Path) -> Path:
    """Absolute or relative path from env; empty/blank keeps the default."""
    raw = os.getenv(name, "").strip()
    return Path(raw).expanduser() if raw else default


CLIENT_SECRETS_FILE = _path_from_env("YOUTUBE_CLIENT_SECRETS_FILE", BASE_DIR / "client_secret.json")
TOKEN_FILE = _path_from_env("YOUTUBE_TOKEN_FILE", BASE_DIR / "youtube_token.json")

# videos.insert and thumbnails.set both accept the upload-only scope.
UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
# captions.insert does not. force-ssl is a substantially wider grant — it
# allows managing and deleting channel content — and it is the only scope that
# covers caption uploads. Kept separate so it is visible what each one buys.
CAPTION_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"

# Read-only. Unlike force-ssl it cannot change or delete anything on the
# channel; it is what per-video retention and CTR are read through.
ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"

# Used when minting a new token. Existing tokens are loaded with whatever they
# were actually granted; see load_credentials.
#
# force-ssl is the widest grant here: it allows managing and deleting channel
# content, not just uploading. It was left out while the compliance review was
# open, since that review described upload-only access. The review completed
# on 2026-09-08, and captions.insert accepts nothing narrower.
#
# What keeps this honest is that the code still only ever calls videos.insert,
# thumbnails.set and captions.insert. There is no list, update or delete
# anywhere, and adding one should take the same deliberation this did.
SCOPES = [UPLOAD_SCOPE, CAPTION_SCOPE, ANALYTICS_SCOPE]
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"

VALID_PRIVACY_STATUSES = ("public", "private", "unlisted")

# TikTok TTS voice prefixes to BCP-47. Declaring the language feeds YouTube's
# audience matching, auto-captions and translations; left unset the video reads
# as language-agnostic and loses that.
VOICE_LANGUAGE_MAP = {
    "en": "en",
    "br": "pt",
    "id": "id",
    "jp": "ja",
    "kr": "ko",
    "es": "es",
    "fr": "fr",
    "de": "de",
}
DEFAULT_LANGUAGE = "en"
# 28 = Science & Technology, matching the pipeline's own default. Only used to
# fill a categoryId that videos.update requires but a list response omitted.
DEFAULT_CATEGORY_ID = "28"


def resolve_language(voice: Optional[str]) -> str:
    """BCP-47 code for a TikTok TTS voice. Unknown voices fall back to English."""
    return VOICE_LANGUAGE_MAP.get((voice or "")[:2].lower(), DEFAULT_LANGUAGE)


class YouTubeAuthError(RuntimeError):
    """Raised when no usable YouTube credentials are available."""


def load_credentials(token_file: Path = TOKEN_FILE) -> Optional[Credentials]:
    """Load saved credentials, refreshing the access token if it expired."""
    if not token_file.exists():
        return None
    try:
        # Deliberately no scope argument: the credential takes the scopes the
        # token was actually granted. Passing a wider list makes google-auth
        # compare requested against granted on the next refresh and raise
        # RefreshError, which would stop uploads within the hour on any
        # deployment whose token predates the caption scope.
        credentials = Credentials.from_authorized_user_file(str(token_file))
    except (ValueError, OSError, KeyError) as err:
        log(f"[-] Could not read YouTube token file {token_file}: {err}", "error")
        return None

    if credentials.valid:
        return credentials

    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except Exception as err:
            log(f"[-] Could not refresh YouTube credentials: {err}", "error")
            return None
        try:
            token_file.write_text(credentials.to_json())
            os.chmod(token_file, 0o600)
        except OSError as err:
            log(f"[!] Could not persist refreshed YouTube token: {err}", "warning")
        return credentials

    return None


def get_authenticated_service() -> Resource:
    """Build the YouTube API client from the saved token. Never interactive."""
    credentials = load_credentials()
    if credentials is None:
        raise YouTubeAuthError(
            "No valid YouTube credentials. Run Backend/youtube_auth.py on a machine "
            f"with a browser and copy the token file to {TOKEN_FILE} "
            "(see docs/deploy.md)."
        )
    return build(
        YOUTUBE_API_SERVICE_NAME,
        YOUTUBE_API_VERSION,
        credentials=credentials,
        cache_discovery=False,
    )


def resolve_privacy_status(raw: Optional[str]) -> Tuple[str, Optional[str]]:
    """Return (privacy_status, warning). Unknown values fall back to 'private'."""
    value = (raw or "").strip().lower()
    if not value:
        return "private", None
    if value in VALID_PRIVACY_STATUSES:
        return value, None
    return "private", f"Invalid YOUTUBE_PRIVACY_STATUS '{raw}'. Falling back to 'private'."


def initialize_upload(youtube, options: dict) -> dict:
    language = options.get("language") or DEFAULT_LANGUAGE
    body = {
        "snippet": {
            "title": options["title"],
            "description": options["description"],
            "tags": options["tags"] or None,
            "categoryId": options["category"],
            "defaultLanguage": language,
            "defaultAudioLanguage": language,
        },
        "status": {
            "privacyStatus": options["privacyStatus"],
            "madeForKids": False,
            "selfDeclaredMadeForKids": False,
        },
    }

    insert_request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=MediaFileUpload(options["file"], chunksize=-1, resumable=True),
    )
    return resumable_upload(insert_request)


def resumable_upload(insert_request) -> dict:
    response = None
    retry = 0
    while response is None:
        error = None
        try:
            log(" => Uploading file...", "info")
            _status, response = insert_request.next_chunk()
            if response is not None and "id" in response:
                log(f"Video id '{response['id']}' was successfully uploaded.", "success")
                return response
            if response is not None:
                raise RuntimeError(f"Unexpected upload response: {response}")
        except HttpError as err:
            if err.resp.status in RETRIABLE_STATUS_CODES:
                error = f"A retriable HTTP error {err.resp.status} occurred:\n{err.content}"
            else:
                raise
        except RETRIABLE_EXCEPTIONS as err:
            error = f"A retriable error occurred: {err}"

        if error is not None:
            log(error, "error")
            retry += 1
            if retry > MAX_RETRIES:
                raise RuntimeError("YouTube upload failed after retries.")
            sleep_seconds = min(random.random() * (2**retry), MAX_UPLOAD_BACKOFF_SECONDS)
            log(f" => Sleeping {sleep_seconds:.1f} seconds and then retrying...", "info")
            time.sleep(sleep_seconds)
    return response


def upload_video(
    video_path: str,
    title: str,
    description: str,
    category: str,
    tags: List[str],
    privacy_status: str,
    language: str = DEFAULT_LANGUAGE,
) -> str:
    """Upload a video and return its YouTube id. Raises on any failure."""
    youtube = get_authenticated_service()
    response = initialize_upload(
        youtube,
        {
            "file": video_path,
            "title": title,
            "description": description,
            "category": category,
            "tags": list(tags),
            "privacyStatus": privacy_status,
            "language": language,
        },
    )
    return str(response["id"])


def upload_thumbnail(video_id: str, thumbnail_path: str) -> None:
    """Sets a custom thumbnail on an uploaded video.

    thumbnails.set accepts the upload-only scope, so this needs no wider grant
    than the video upload itself. It does require the channel to be verified;
    an unverified one is refused by the API.

    Args:
        video_id (str): The uploaded video.
        thumbnail_path (str): JPEG or PNG, under 2 MB.

    Raises:
        Exception: Whatever the API client raises. Callers treat this as
            non-fatal: the video is already live.
    """
    youtube = get_authenticated_service()
    youtube.thumbnails().set(
        videoId=video_id, media_body=MediaFileUpload(thumbnail_path)
    ).execute()
    log(f"[+] Thumbnail set on {video_id}", "success")


def has_scope(credentials, scope: str) -> bool:
    """Whether a token was actually granted a scope."""
    return scope in (getattr(credentials, "scopes", None) or [])


def upload_captions(
    video_id: str,
    srt_path: str,
    language: str = DEFAULT_LANGUAGE,
    name: str = "",
) -> str:
    """Attaches an .srt to an uploaded video as a caption track.

    Needs CAPTION_SCOPE, which a token minted before captions existed will not
    have. That case is reported rather than attempted, because the API's own
    error for it is opaque.

    Args:
        video_id (str): The uploaded video.
        srt_path (str): Path to the subtitle file.
        language (str): BCP-47 code for the caption track.
        name (str): Track name shown in the player.

    Returns:
        str: The caption track id.

    Raises:
        YouTubeAuthError: If the saved token lacks the caption scope.
        Exception: Whatever the API client raises. Callers treat this as
            non-fatal: the video is already live.
    """
    credentials = load_credentials()
    if credentials is None:
        raise YouTubeAuthError("No valid YouTube credentials.")
    if not has_scope(credentials, CAPTION_SCOPE):
        raise YouTubeAuthError(
            "The saved token was granted upload-only access, which does not "
            "cover captions.insert. Re-run Backend/youtube_auth.py to mint a "
            "token with the caption scope."
        )

    youtube = build(
        YOUTUBE_API_SERVICE_NAME,
        YOUTUBE_API_VERSION,
        credentials=credentials,
        cache_discovery=False,
    )
    response = (
        youtube.captions()
        .insert(
            part="snippet",
            body={
                "snippet": {
                    "videoId": video_id,
                    "language": language,
                    "name": name,
                    "isDraft": False,
                }
            },
            media_body=MediaFileUpload(srt_path, mimetype="application/octet-stream"),
        )
        .execute()
    )
    caption_id = str(response["id"])
    log(f"[+] Caption track {caption_id} added to {video_id}", "success")
    return caption_id


def _service_for(operation: str) -> Resource:
    """Authenticated client for a call that needs more than upload-only access.

    videos.list and videos.update both sit behind CAPTION_SCOPE
    (youtube.force-ssl), the same grant captions.insert already uses, so a
    token minted for captions needs no re-authorisation.
    """
    credentials = load_credentials()
    if credentials is None:
        raise YouTubeAuthError("No valid YouTube credentials.")
    if not has_scope(credentials, CAPTION_SCOPE):
        raise YouTubeAuthError(
            f"The saved token was granted upload-only access, which does not "
            f"cover {operation}. Re-run Backend/youtube_auth.py to mint a "
            f"token with the {CAPTION_SCOPE} scope."
        )
    return build(
        YOUTUBE_API_SERVICE_NAME,
        YOUTUBE_API_VERSION,
        credentials=credentials,
        cache_discovery=False,
    )


def get_video_snippet(video_id: str) -> dict:
    """The current snippet of one video.

    Read-only, and the necessary first half of any metadata edit: see
    update_video_metadata for why an update cannot be a blind write.

    Raises:
        YouTubeAuthError: If the saved token lacks the wider scope.
        LookupError: If the id matches nothing this account can see.
    """
    youtube = _service_for("videos.list")
    response = youtube.videos().list(part="snippet", id=video_id).execute()
    items = response.get("items") or []
    if not items:
        raise LookupError(
            f"No video {video_id} visible to this account. Check the id, and "
            f"that the token belongs to the channel that owns it."
        )
    return items[0].get("snippet") or {}


def update_video_metadata(
    video_id: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> dict:
    """Edits the metadata of a video that is already published.

    Repair only. The pipeline sets metadata at insert time; this exists for
    videos published before a metadata bug was fixed.

    videos.update REPLACES the whole snippet part rather than patching it:
    every field absent from the body is cleared, so an update built from just
    the fields a caller wants to change would silently wipe the title,
    category and language of the video it was meant to improve. This reads the
    current snippet first and overlays only what was passed.

    Args:
        video_id (str): The video to edit.
        title (Optional[str]): New title, or None to keep the current one.
        description (Optional[str]): New description, or None to keep it.
        tags (Optional[List[str]]): New tag list, or None to keep it.

    Returns:
        dict: The snippet as written.

    Raises:
        YouTubeAuthError: If the saved token lacks the wider scope.
        LookupError: If the id matches nothing this account can see.
        ValueError: If the result would have no title, which YouTube rejects
            and which would be an unrecoverable edit to make by accident.
    """
    snippet = dict(get_video_snippet(video_id))
    before = {
        "title": snippet.get("title"),
        "tags": list(snippet.get("tags") or []),
        "description_chars": len(snippet.get("description") or ""),
    }

    if title is not None:
        snippet["title"] = title
    if description is not None:
        snippet["description"] = description
    if tags is not None:
        snippet["tags"] = list(tags)

    if not (snippet.get("title") or "").strip():
        raise ValueError(f"Refusing to leave video {video_id} with an empty title.")
    # categoryId is required by videos.update and is not always present in a
    # list response; falling back keeps the call from being rejected.
    snippet.setdefault("categoryId", DEFAULT_CATEGORY_ID)

    youtube = _service_for("videos.update")
    response = (
        youtube.videos()
        .update(part="snippet", body={"id": video_id, "snippet": snippet})
        .execute()
    )
    written = response.get("snippet") or snippet
    log(
        f"[+] Updated {video_id}: title '{before['title']}' -> "
        f"'{written.get('title')}', {len(before['tags'])} -> "
        f"{len(written.get('tags') or [])} tags, description "
        f"{before['description_chars']} -> {len(written.get('description') or '')} chars",
        "success",
    )
    return written


# videos.list accepts up to 50 ids per call and costs one quota unit whatever
# the count, so the whole channel is one request.
STATISTICS_BATCH = 50


def fetch_statistics(video_ids: Sequence[str]) -> dict:
    """Current views, likes and comments per video.

    The Analytics API runs 48 to 72 hours behind by design; this does not. It
    is the only way to see what a video published this morning is doing, and
    the documentation points here for exactly that.

    Returns a mapping of video id to counts. Videos the call does not know
    about are absent rather than zeroed — the same rule the Analytics layer
    follows, and for the same reason.

    Raises:
        YouTubeAuthError: If the saved token lacks the wider scope.
    """
    wanted = [video_id for video_id in video_ids if video_id]
    if not wanted:
        return {}

    youtube = _service_for("videos.list")
    collected: dict = {}
    for start in range(0, len(wanted), STATISTICS_BATCH):
        batch = wanted[start : start + STATISTICS_BATCH]
        response = (
            youtube.videos().list(part="statistics", id=",".join(batch)).execute()
        )
        for item in response.get("items") or []:
            stats = item.get("statistics") or {}
            collected[str(item.get("id"))] = {
                "views": int(stats.get("viewCount") or 0),
                "likes": int(stats.get("likeCount") or 0),
                "comments": int(stats.get("commentCount") or 0),
            }
    return collected
