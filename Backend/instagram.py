"""Publish a finished Reel to Instagram through the Graph API.

The account is reached with the "Instagram API with Facebook Login" chain: a
long-lived Page access token, and the Instagram user id discovered from the
Page it is connected to. The alternative chain, Instagram Login, cannot upload
bytes at all — it only accepts a public `video_url` that Meta fetches — which
would mean standing up file hosting for videos that already sit on this disk.

Publishing is four calls, and every one of them is on a different footing:

    1. create a container   graph.facebook.com    Authorization: Bearer
    2. push the bytes       rupload.facebook.com  Authorization: OAuth
    3. poll until FINISHED  graph.facebook.com    Authorization: Bearer
    4. publish              graph.facebook.com    Authorization: Bearer

Step 2 is the odd one: a different host, and `OAuth` where the rest take
`Bearer`. Meta documents both spellings and they are not interchangeable.
"""

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from logstream import log

BASE_DIR = Path(__file__).resolve().parent


def _path_from_env(name: str, default: Path) -> Path:
    """Absolute or relative path from env; empty/blank keeps the default."""
    raw = os.getenv(name, "").strip()
    return Path(raw).expanduser() if raw else default


TOKEN_FILE = _path_from_env("INSTAGRAM_TOKEN_FILE", BASE_DIR / "instagram_token.json")

# Pinned rather than "latest". Meta ships a new version roughly quarterly and
# keeps old ones working for two years; an unpinned client changes behaviour
# under us on their schedule instead of ours.
GRAPH_VERSION = "v26.0"
GRAPH_HOST = "https://graph.facebook.com"

# Meta's documented caption limits.
CAPTION_MAX_CHARS = 2200
CAPTION_MAX_HASHTAGS = 30

# The docs say "query a container's status once per minute, for no more than 5
# minutes". A minute is a long time to sit on a 38-second video that is
# probably already done, so the first checks are closer together and the total
# still respects their five-minute ceiling.
POLL_SCHEDULE_SECONDS = (5, 10, 15, 30, 30, 60, 60, 60)

# Reels specs we can check before spending an upload. The rest — codec, frame
# rate, the moov atom at the front — are guaranteed by video.py's encoder
# arguments instead, since they cannot change per file.
MAX_FILE_BYTES = 300 * 1024 * 1024
MIN_DURATION_SECONDS = 3
MAX_DURATION_SECONDS = 15 * 60

# Error subcodes worth naming when they come back, from Meta's "Error Codes
# Defined" table. Anything absent from here is reported with whatever message
# the API returned.
RETRIABLE_SUBCODES = {
    2207001: "Instagram server error.",
    2207003: "Meta timed out downloading the media.",
    2207008: "The container expired between creating and publishing it.",
    2207027: "The media was not finished processing.",
    2207032: "Meta failed to create the container.",
    2207053: "Unknown upload error.",
}
FATAL_SUBCODES = {
    2207010: "Caption too long: max 2200 characters, 30 hashtags, 20 @ tags.",
    2207020: "The uploaded media expired before publishing.",
    2207026: "Unsupported video format; Reels takes MOV or MP4.",
    2207042: "Daily publishing limit reached; try again tomorrow.",
    2207050: "The Instagram account is restricted or needs attention in the app.",
    2207051: "Meta flagged the publishing action as spam.",
}


class InstagramAuthError(RuntimeError):
    """Raised when no usable Instagram credentials are available."""


class InstagramUploadError(RuntimeError):
    """Raised when a publish attempt fails for a reason worth surfacing."""


class Credentials:
    """What instagram_auth.py leaves behind: a token and the two ids it unlocks."""

    def __init__(self, access_token: str, ig_user_id: str, page_id: str = "") -> None:
        self.access_token = access_token
        self.ig_user_id = ig_user_id
        self.page_id = page_id


def load_credentials(token_file: Path = TOKEN_FILE) -> Optional[Credentials]:
    """Read the saved token, or None when it is missing or unreadable.

    Nothing is refreshed here. A Page access token derived from a long-lived
    User token carries no expiry, so unlike the YouTube path there is no
    refresh step that could fail at upload time.
    """
    if not token_file.exists():
        return None
    try:
        data = json.loads(token_file.read_text())
    except (ValueError, OSError) as err:
        log(f"[-] Could not read Instagram token file {token_file}: {err}", "error")
        return None

    token = str(data.get("access_token") or "").strip()
    ig_user_id = str(data.get("ig_user_id") or "").strip()
    if not token or not ig_user_id:
        log(
            f"[-] Instagram token file {token_file} is missing access_token "
            "or ig_user_id.",
            "error",
        )
        return None
    return Credentials(token, ig_user_id, str(data.get("page_id") or ""))


def require_credentials(token_file: Path = TOKEN_FILE) -> Credentials:
    """Credentials or a message saying exactly how to produce them."""
    credentials = load_credentials(token_file)
    if credentials is None:
        raise InstagramAuthError(
            "No valid Instagram credentials. Run Backend/instagram_auth.py on a "
            f"machine with a browser and copy the token file to {token_file} "
            "(see docs/instagram.md)."
        )
    return credentials


def _hashtags(text: str) -> List[str]:
    return [word for word in text.split() if word.startswith("#") and len(word) > 1]


def build_caption(
    title: str,
    description: str,
    max_chars: int = CAPTION_MAX_CHARS,
    max_hashtags: int = CAPTION_MAX_HASHTAGS,
) -> str:
    """Turn the YouTube metadata into an Instagram caption.

    Three differences from the description we upload to YouTube:

    The title leads. On YouTube it sits above the player; in a Reels caption
    there is no title field at all, so the best line in the video would
    otherwise be thrown away.

    The Sources block goes. Links are not clickable in an Instagram caption, so
    five bare URLs cost a few hundred characters and buy nothing. The sources
    still stand on the YouTube description of the same video.

    #Shorts becomes #Reels. It names the wrong platform's format, and a tag
    nobody on Instagram searches is a wasted slot out of thirty.
    """
    body = (description or "").split("Sources:")[0].strip()
    lines = [line for line in body.splitlines() if line.strip()]

    # Keep the hashtag line last, wherever validate_metadata put it.
    tag_lines = [line for line in lines if line.strip().startswith("#")]
    prose = [line for line in lines if not line.strip().startswith("#")]

    tags: List[str] = []
    for line in tag_lines:
        for tag in _hashtags(line):
            replacement = "#Reels" if tag.lower() == "#shorts" else tag
            if replacement not in tags:
                tags.append(replacement)
    tags = tags[:max_hashtags]

    parts = [part for part in [(title or "").strip(), "\n".join(prose)] if part]
    caption = "\n\n".join(parts)
    if tags:
        caption = f"{caption}\n\n{' '.join(tags)}" if caption else " ".join(tags)

    if len(caption) <= max_chars:
        return caption
    # Drop prose before tags: the tags are how the post is found at all.
    keep = max_chars - (len(" ".join(tags)) + 2) if tags else max_chars
    trimmed = caption[:keep].rsplit(" ", 1)[0].rstrip() if keep > 0 else ""
    return f"{trimmed}\n\n{' '.join(tags)}".strip() if tags else trimmed[:max_chars]


def _graph_error(response: requests.Response) -> Tuple[Optional[int], str]:
    """The subcode and a readable message from a Graph API error body."""
    try:
        error = response.json().get("error") or {}
    except ValueError:
        return None, f"HTTP {response.status_code}: {response.text[:200]}"
    subcode = error.get("error_subcode")
    message = error.get("message") or f"HTTP {response.status_code}"
    known = RETRIABLE_SUBCODES.get(subcode) or FATAL_SUBCODES.get(subcode)
    return subcode, f"{known} ({message})" if known else message


def publishing_quota(
    credentials: Credentials, session: Optional[requests.Session] = None
) -> Tuple[int, int]:
    """(used, total) posts in the rolling 24-hour window.

    Meta's own pages give the cap as both 50 and 100 depending on where you
    read; this asks the account rather than picking one.
    """
    http = session or requests
    response = http.get(
        f"{GRAPH_HOST}/{GRAPH_VERSION}/{credentials.ig_user_id}/content_publishing_limit",
        params={
            "fields": "quota_usage,config",
            "access_token": credentials.access_token,
        },
        timeout=30,
    )
    if not response.ok:
        _, message = _graph_error(response)
        raise InstagramUploadError(f"Could not read the publishing quota: {message}")
    rows = (response.json().get("data") or [{}])[0]
    config = rows.get("config") or {}
    return int(rows.get("quota_usage") or 0), int(config.get("quota_total") or 0)


def create_container(
    credentials: Credentials,
    caption: str,
    share_to_feed: bool = True,
    session: Optional[requests.Session] = None,
) -> Tuple[str, str]:
    """Open a resumable Reels container. Returns (container_id, upload_uri).

    The upload URI comes back from this call and is used verbatim: it already
    carries the API version, so building it by hand is one more place for the
    pinned version to drift out of step.
    """
    http = session or requests
    response = http.post(
        f"{GRAPH_HOST}/{GRAPH_VERSION}/{credentials.ig_user_id}/media",
        params={
            "media_type": "REELS",
            "upload_type": "resumable",
            "caption": caption,
            "share_to_feed": "true" if share_to_feed else "false",
            "access_token": credentials.access_token,
        },
        timeout=60,
    )
    if not response.ok:
        _, message = _graph_error(response)
        raise InstagramUploadError(f"Could not create the Reels container: {message}")
    payload = response.json()
    container_id = str(payload.get("id") or "")
    uri = str(payload.get("uri") or "")
    if not container_id or not uri:
        raise InstagramUploadError(
            f"Container response had no id or upload uri: {payload}"
        )
    return container_id, uri


def upload_bytes(
    upload_uri: str,
    access_token: str,
    video_path: Path,
    session: Optional[requests.Session] = None,
) -> None:
    """Send the file to rupload.facebook.com in one request.

    Despite the name, "resumable" describes only the endpoint: Meta documents
    no way to query how many bytes it already holds, and no Content-Range. So
    an interrupted upload cannot be continued — the caller starts a new
    container instead.
    """
    size = video_path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise InstagramUploadError(
            f"{video_path.name} is {size / 1024 / 1024:.0f} MB; Reels takes 300 MB."
        )
    http = session or requests
    with video_path.open("rb") as handle:
        response = http.post(
            upload_uri,
            headers={
                # OAuth, not Bearer. The graph host takes the other spelling.
                "Authorization": f"OAuth {access_token}",
                "offset": "0",
                "file_size": str(size),
            },
            data=handle,
            timeout=600,
        )
    if not response.ok:
        raise InstagramUploadError(
            f"Byte upload failed: HTTP {response.status_code} {response.text[:300]}"
        )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if payload.get("success") is not True:
        raise InstagramUploadError(f"Byte upload was not accepted: {payload}")


def wait_until_ready(
    credentials: Credentials,
    container_id: str,
    schedule: Tuple[int, ...] = POLL_SCHEDULE_SECONDS,
    session: Optional[requests.Session] = None,
    sleep=time.sleep,
) -> None:
    """Block until the container reports FINISHED, or raise saying why not."""
    http = session or requests
    last = "IN_PROGRESS"
    for delay in schedule:
        sleep(delay)
        response = http.get(
            f"{GRAPH_HOST}/{GRAPH_VERSION}/{container_id}",
            params={
                "fields": "status_code,status",
                "access_token": credentials.access_token,
            },
            timeout=30,
        )
        if not response.ok:
            _, message = _graph_error(response)
            raise InstagramUploadError(f"Could not read container status: {message}")
        payload = response.json()
        last = str(payload.get("status_code") or "")
        if last == "FINISHED":
            return
        if last in ("ERROR", "EXPIRED"):
            # status carries the error subcode when status_code is ERROR.
            raise InstagramUploadError(
                f"Container {container_id} came back {last}: "
                f"{payload.get('status') or 'no detail'}"
            )
    raise InstagramUploadError(
        f"Container {container_id} was still {last} after "
        f"{sum(schedule)}s of processing."
    )


def publish_container(
    credentials: Credentials,
    container_id: str,
    session: Optional[requests.Session] = None,
) -> str:
    """Publish a finished container. Returns the Instagram media id."""
    http = session or requests
    response = http.post(
        f"{GRAPH_HOST}/{GRAPH_VERSION}/{credentials.ig_user_id}/media_publish",
        params={
            "creation_id": container_id,
            "access_token": credentials.access_token,
        },
        timeout=60,
    )
    if not response.ok:
        _, message = _graph_error(response)
        raise InstagramUploadError(f"Could not publish the container: {message}")
    media_id = str(response.json().get("id") or "")
    if not media_id:
        raise InstagramUploadError("Publish returned no media id.")
    return media_id


def upload_reel(
    video_path: str,
    title: str,
    description: str,
    share_to_feed: bool = True,
    token_file: Path = TOKEN_FILE,
    session: Optional[requests.Session] = None,
) -> str:
    """Publish one Reel and return its Instagram media id.

    Raises InstagramAuthError or InstagramUploadError; the caller decides
    whether a failed cross-post is worth failing the job over. It is not — the
    video is already on YouTube by the time this runs.
    """
    credentials = require_credentials(token_file)
    path = Path(video_path)
    if not path.exists():
        raise InstagramUploadError(f"No such video: {path}")

    caption = build_caption(title, description)
    container_id, upload_uri = create_container(
        credentials, caption, share_to_feed, session
    )
    upload_bytes(upload_uri, credentials.access_token, path, session)
    wait_until_ready(credentials, container_id, session=session)
    media_id = publish_container(credentials, container_id, session)
    log(f"[+] Published to Instagram: {media_id}", "success")
    return media_id
