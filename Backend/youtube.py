import random
import time
from pathlib import Path
from typing import List, Optional, Tuple

import httplib2
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from logstream import log

# Retries are handled below, not by the transport.
httplib2.RETRIES = 1

MAX_RETRIES = 10
RETRIABLE_EXCEPTIONS = (httplib2.HttpLib2Error, IOError, httplib2.ServerNotFoundError)
RETRIABLE_STATUS_CODES = (500, 502, 503, 504)

BASE_DIR = Path(__file__).resolve().parent
CLIENT_SECRETS_FILE = BASE_DIR / "client_secret.json"
TOKEN_FILE = BASE_DIR / "youtube_token.json"

# Upload-only scope: enough for videos.insert and nothing else.
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"

VALID_PRIVACY_STATUSES = ("public", "private", "unlisted")


class YouTubeAuthError(RuntimeError):
    """Raised when no usable YouTube credentials are available."""


def load_credentials(token_file: Path = TOKEN_FILE) -> Optional[Credentials]:
    """Load saved credentials, refreshing the access token if it expired."""
    if not token_file.exists():
        return None
    try:
        credentials = Credentials.from_authorized_user_file(str(token_file), SCOPES)
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
        except OSError as err:
            log(f"[!] Could not persist refreshed YouTube token: {err}", "warning")
        return credentials

    return None


def get_authenticated_service():
    """Build the YouTube API client from the saved token. Never interactive."""
    credentials = load_credentials()
    if credentials is None:
        raise YouTubeAuthError(
            "No valid YouTube credentials. Run Backend/youtube_auth.py on a machine "
            "with a browser and copy Backend/youtube_token.json to the server."
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
    body = {
        "snippet": {
            "title": options["title"],
            "description": options["description"],
            "tags": options["tags"] or None,
            "categoryId": options["category"],
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
            sleep_seconds = random.random() * (2**retry)
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
        },
    )
    return str(response["id"])
