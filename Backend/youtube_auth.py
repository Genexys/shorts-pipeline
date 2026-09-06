"""One-time YouTube OAuth flow. Run on a machine with a browser:

    uv run python Backend/youtube_auth.py

Writes Backend/youtube_token.json, which the worker (and the server) use
without any further interaction.
"""

import sys

from google_auth_oauthlib.flow import InstalledAppFlow

from youtube import CLIENT_SECRETS_FILE, SCOPES, TOKEN_FILE


def main() -> int:
    if not CLIENT_SECRETS_FILE.exists():
        print(
            f"Missing {CLIENT_SECRETS_FILE}. Create an OAuth client (type: Desktop app) "
            "in Google Cloud Console and download its JSON to that path."
        )
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_FILE), SCOPES)
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    TOKEN_FILE.write_text(credentials.to_json())
    print(f"Saved credentials to {TOKEN_FILE}")
    if not credentials.refresh_token:
        print("WARNING: no refresh token received. Revoke app access at "
              "https://myaccount.google.com/permissions and run again.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
