"""One-time Instagram (Facebook Login) OAuth flow. Run on a machine with a browser:

    uv run python Backend/instagram_auth.py

Writes Backend/instagram_token.json — a long-lived Page access token plus the
Instagram user id it unlocks — which the worker then uses without any further
interaction.

Unlike the YouTube flow this cannot open a local server and catch the redirect:
Meta rejects http://localhost as a redirect URI for anything but a configured
app, so the code is copied out of the address bar by hand. It runs once.
"""

import json
import os
import sys
from typing import List
from urllib.parse import parse_qs, urlparse

import requests
from dotenv import load_dotenv

from utils import ENV_FILE

# Must run before importing instagram: INSTAGRAM_TOKEN_FILE is resolved at that
# module's import time, so .env has to be loaded first for an override to apply.
load_dotenv(ENV_FILE)

from instagram import GRAPH_HOST, GRAPH_VERSION, TOKEN_FILE  # noqa: E402

# instagram_basic and instagram_content_publish do the publishing;
# pages_show_list finds the Page and pages_read_engagement reads the Instagram
# account connected to it. Meta's pages list slightly different subsets for
# each call in the flow, so this is the union of all four.
SCOPES = [
    "instagram_basic",
    "instagram_content_publish",
    "pages_show_list",
    "pages_read_engagement",
]

# Whatever is configured as a Valid OAuth Redirect URI on the app. Nothing is
# served here — the point is only that the browser lands somewhere with ?code=
# in the address bar.
REDIRECT_URI = os.getenv("INSTAGRAM_REDIRECT_URI", "https://localhost/").strip()


def _fail(message: str) -> int:
    print(f"\n[-] {message}")
    return 1


def _code_from(raw: str) -> str:
    """Accept either the bare code or the whole redirected URL."""
    raw = raw.strip()
    if not raw:
        return ""
    if raw.startswith("http"):
        values = parse_qs(urlparse(raw).query).get("code") or []
        return values[0] if values else ""
    return raw


def main() -> int:
    app_id = os.getenv("META_APP_ID", "").strip()
    app_secret = os.getenv("META_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        return _fail(
            "Set META_APP_ID and META_APP_SECRET in .env. Both come from the app "
            "you created at developers.facebook.com (Settings -> Basic)."
        )

    print(f"Add this to the app's Valid OAuth Redirect URIs: {REDIRECT_URI}\n")
    dialog = (
        f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"
        f"?client_id={app_id}&redirect_uri={REDIRECT_URI}"
        f"&response_type=code&scope={','.join(SCOPES)}"
    )
    print("1. Open this in a browser and approve:\n")
    print(f"   {dialog}\n")
    print("2. The page will fail to load — that is expected. Copy the whole URL")
    print("   from the address bar (it contains ?code=...) and paste it here.\n")
    code = _code_from(input("   URL or code: "))
    if not code:
        return _fail("No code found in what you pasted.")

    print("\n[*] Exchanging the code for a short-lived token...")
    short = requests.get(
        f"{GRAPH_HOST}/{GRAPH_VERSION}/oauth/access_token",
        params={
            "client_id": app_id,
            "redirect_uri": REDIRECT_URI,
            "client_secret": app_secret,
            "code": code,
        },
        timeout=30,
    )
    if not short.ok:
        return _fail(f"Code exchange failed: {short.text[:300]}")
    short_token = short.json().get("access_token", "")
    if not short_token:
        return _fail(f"No access_token in the response: {short.text[:300]}")

    print("[*] Exchanging it for a long-lived one...")
    long = requests.get(
        f"{GRAPH_HOST}/{GRAPH_VERSION}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_token,
        },
        timeout=30,
    )
    if not long.ok:
        return _fail(f"Long-lived exchange failed: {long.text[:300]}")
    long_token = long.json().get("access_token", "")
    if not long_token:
        return _fail(f"No access_token in the response: {long.text[:300]}")

    print("[*] Listing the Pages you can post to...")
    accounts = requests.get(
        f"{GRAPH_HOST}/{GRAPH_VERSION}/me/accounts",
        params={"access_token": long_token},
        timeout=30,
    )
    if not accounts.ok:
        return _fail(f"Could not list Pages: {accounts.text[:300]}")
    pages: List[dict] = accounts.json().get("data") or []
    if not pages:
        return _fail(
            "No Pages came back. The account you approved with needs a role on a "
            "Facebook Page, and that Page must be connected to the Instagram "
            "professional account."
        )

    if len(pages) == 1:
        page = pages[0]
    else:
        print()
        for index, candidate in enumerate(pages, start=1):
            print(f"   {index}. {candidate.get('name')} ({candidate.get('id')})")
        choice = input("\n   Which Page? ").strip()
        if not choice.isdigit() or not 1 <= int(choice) <= len(pages):
            return _fail("Not one of the listed numbers.")
        page = pages[int(choice) - 1]

    # The Page token, not the user token: derived from a long-lived user token
    # it carries no expiry date, so the worker never has to refresh anything.
    page_token = page.get("access_token", "")
    page_id = str(page.get("id") or "")
    if not page_token:
        return _fail(f"No access token came back for Page {page_id}.")

    print(f"[*] Finding the Instagram account connected to {page.get('name')}...")
    connected = requests.get(
        f"{GRAPH_HOST}/{GRAPH_VERSION}/{page_id}",
        params={"fields": "instagram_business_account", "access_token": page_token},
        timeout=30,
    )
    if not connected.ok:
        return _fail(f"Could not read the Page: {connected.text[:300]}")
    ig_account = connected.json().get("instagram_business_account") or {}
    ig_user_id = str(ig_account.get("id") or "")
    if not ig_user_id:
        return _fail(
            f"Page {page.get('name')} has no Instagram professional account "
            "connected. Connect it from the Instagram app's settings, then run "
            "this again."
        )

    if TOKEN_FILE.exists():
        backup = TOKEN_FILE.with_suffix(".json.bak")
        backup.write_text(TOKEN_FILE.read_text())
        os.chmod(backup, 0o600)
        print(f"[*] Existing token backed up to {backup}")

    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(
        json.dumps(
            {
                "access_token": page_token,
                "ig_user_id": ig_user_id,
                "page_id": page_id,
                "page_name": page.get("name", ""),
            },
            indent=2,
        )
    )
    os.chmod(TOKEN_FILE, 0o600)
    print(f"\n[+] Wrote {TOKEN_FILE}")
    print(f"    Page:      {page.get('name')} ({page_id})")
    print(f"    Instagram: {ig_user_id}")
    print("\nCopy that file to secrets/ on the machine that runs the worker.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
