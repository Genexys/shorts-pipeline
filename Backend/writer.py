"""The creative half of the pipeline, optionally written by a stronger model.

Only two calls go here: the topic and the script. Those are where judgement
shows — especially for a curio, where the difference between a subject that is
genuinely absurd and one that merely sounds like it is exactly the
discrimination a small model lacks. Search terms, metadata and music mood are
structured extraction; the local model does them well and for free.

Optional by construction. With no ANTHROPIC_API_KEY the pipeline runs entirely
on Ollama, as it always has.
"""

import os
from typing import Optional, Tuple

from logstream import log

# Roughly 240k input and 32k output tokens a month at three videos a day, which
# is about two dollars on Opus. Cheaper models exist; humour is the one task
# where the strongest model earns its keep, and the absolute cost is noise next
# to the narration bill.
DEFAULT_MODEL = "claude-opus-5"
MAX_TOKENS = 16000
REQUEST_TIMEOUT_SECONDS = 120


def api_key() -> str:
    return os.getenv("ANTHROPIC_API_KEY", "").strip()


def model_name() -> str:
    return os.getenv("SCRIPT_MODEL", "").strip() or DEFAULT_MODEL


def is_configured() -> bool:
    return bool(api_key())


def scrub(text: str) -> str:
    """Removes the key from a message before it reaches a log."""
    key = api_key()
    return text.replace(key, "<redacted>") if key else text


# What a refusal on a harmless subject costs, and why one retry is worth it.
# A curio about a real published experiment — cattle painted with zebra stripes
# to see whether it deters biting flies — was declined outright, and the 8B
# fallback wrote four sentences of which two repeated the other two. The prompt
# it refused arrives as a wall of terse prohibitions with no statement of what
# any of it is for; saying that plainly is not a trick, it is the context that
# was missing. One extra call costs about a cent.
RETRY_PREAMBLE = """\
The request below is for the narration of a short educational video on a
general-audience science channel. It is read aloud as written, and it is not
roleplay or dialogue. Write the narration and nothing else.
"""


def _attempt(prompt: str) -> Tuple[Optional[str], bool]:
    """One request. Returns the text, and whether the model declined.

    The two are distinguished because only a refusal is worth retrying
    differently: a rate limit or a timeout will not care how the prompt is
    worded.
    """
    try:
        import anthropic

        client = anthropic.Anthropic(
            api_key=api_key(), timeout=REQUEST_TIMEOUT_SECONDS
        )
        response = client.messages.create(
            model=model_name(),
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as err:
        log(f"[!] {model_name()} unavailable ({scrub(str(err))}).", "warning")
        return None, False

    if getattr(response, "stop_reason", None) == "refusal":
        return None, True

    text = "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    ).strip()
    if not text:
        log(f"[!] {model_name()} returned nothing usable.", "warning")
        return None, False
    return text, False


def write(prompt: str) -> Optional[str]:
    """One completion from the stronger model, or None if it cannot be had.

    Never raises. A missing key, a network failure, a rate limit or a refusal
    that survives the retry all return None, and the caller falls back to the
    local model — a video written by Ollama beats no video.
    """
    if not is_configured():
        return None

    text, refused = _attempt(prompt)
    if text or not refused:
        return text

    log(
        f"[!] {model_name()} declined this one; asking again with the brief stated.",
        "warning",
    )
    text, refused = _attempt(f"{RETRY_PREAMBLE}\n{prompt}")
    if refused:
        log(
            f"[!] {model_name()} declined it twice; the local model writes this one.",
            "warning",
        )
    return text
