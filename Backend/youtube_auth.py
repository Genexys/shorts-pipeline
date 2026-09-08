"""One-time YouTube OAuth flow. Run on a machine with a browser:

    uv run python Backend/youtube_auth.py

Writes Backend/youtube_token.json, which the worker (and the server) use
without any further interaction.
"""

import os
import sys

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

from utils import ENV_FILE

# Must run before importing youtube: YOUTUBE_CLIENT_SECRETS_FILE/YOUTUBE_TOKEN_FILE are
# resolved at youtube.py's import time, so .env has to be loaded first for overrides to apply.
load_dotenv(ENV_FILE)

from youtube import CLIENT_SECRETS_FILE, SCOPES, TOKEN_FILE  # noqa: E402


def main() -> int:
    if not CLIENT_SECRETS_FILE.exists():
        print(
            f"Missing {CLIENT_SECRETS_FILE}. Create an OAuth client (type: Desktop app) "
            "in Google Cloud Console and download its JSON to that path."
        )
        return 1

    # A token already in place is replaced, so keep a copy: if the new grant is
    # wrong the old one still uploads.
    if TOKEN_FILE.exists():
        backup = TOKEN_FILE.with_suffix(".json.bak")
        backup.write_text(TOKEN_FILE.read_text())
        os.chmod(backup, 0o600)
        print(f"Existing token backed up to {backup}")

    print("Requesting scopes:")
    for scope in SCOPES:
        print(f"  {scope}")

    # A fixed port and no auto-launch make this runnable from a container,
    # where there is no browser and a random port cannot be published.
    port = int(os.getenv("YOUTUBE_AUTH_PORT", "0"))
    open_browser = os.getenv("YOUTUBE_AUTH_OPEN_BROWSER", "1") != "0"

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_FILE), SCOPES)
    credentials = flow.run_local_server(
        port=port,
        # The redirect Google is sent stays localhost, because that is what the
        # OAuth client has registered. The socket has to listen on every
        # interface separately: inside a container "localhost" is the
        # container's own loopback, so a published port reaches nothing and the
        # browser gets a connection reset after consent.
        host="localhost",
        bind_addr=os.getenv("YOUTUBE_AUTH_BIND", "0.0.0.0"),
        open_browser=open_browser,
        access_type="offline",
        prompt="consent",
    )
    TOKEN_FILE.write_text(credentials.to_json())
    os.chmod(TOKEN_FILE, 0o600)
    print(f"Saved credentials to {TOKEN_FILE}")
    print(f"Granted scopes: {credentials.scopes}")
    if not credentials.refresh_token:
        print("WARNING: no refresh token received. Revoke app access at "
              "https://myaccount.google.com/permissions and run again.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
