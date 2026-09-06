import os
from typing import Optional

import requests

from logstream import log

TELEGRAM_TIMEOUT = 10
TELEGRAM_MAX_TEXT = 4000


def send_telegram(
    text: str, token: Optional[str] = None, chat_id: Optional[str] = None
) -> bool:
    """Send a plain-text Telegram message. Returns False on any problem, never raises."""
    token = token if token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID", "")
    message = text[:TELEGRAM_MAX_TEXT]

    if not token or not chat_id:
        print(f"[telegram disabled] {message}")
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
        detail = str(err).replace(token, "<token>")
        log(f"[-] Telegram notification failed: {detail}", "warning")
        return False
