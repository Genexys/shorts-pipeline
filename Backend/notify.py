import os
import re
from typing import Optional

import requests

from logstream import log

TELEGRAM_TIMEOUT = 10
TELEGRAM_MAX_TEXT = 4000


# The token also reaches error text through the request URL, where it may be
# percent-encoded and so will not match the raw value. Strip the path segment
# as well, or a failed call prints the bot's credentials into the log.
_BOT_PATH_RE = re.compile(r"/bot[^/\s]+")


def scrub_token(text: str, token: str) -> str:
    """Removes a bot token from arbitrary text, encoded or not."""
    if token:
        text = text.replace(token, "<token>")
    return _BOT_PATH_RE.sub("/bot<token>", text)


def send_telegram(
    text: str, token: Optional[str] = None, chat_id: Optional[str] = None
) -> bool:
    """Send a plain-text Telegram message. Returns False on any problem, never raises."""
    token = token if token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID", "")
    message = text[:TELEGRAM_MAX_TEXT]

    if not token or not chat_id:
        log(f"[telegram disabled] {message}", "info")
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
            timeout=TELEGRAM_TIMEOUT,
        )
        response.raise_for_status()
        return True
    except Exception as err:
        log(f"[-] Telegram notification failed: {scrub_token(str(err), token)}", "warning")
        return False
