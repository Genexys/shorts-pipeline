"""The creative half of the pipeline, optionally written by a stronger model.

The calls that go here are the ones where judgement shows: the topic, the
script, the stock search terms, and the title and description — especially for
a curio, where the difference between a subject that is genuinely absurd and
one that merely sounds like it is exactly the discrimination a small model
lacks. The music mood is structured extraction; the local model does it well
and for free.

Optional by construction. With no ANTHROPIC_API_KEY the pipeline runs entirely
on Ollama, as it always has.
"""

import os
import time
from typing import Dict, Iterable, NamedTuple, Optional, Tuple

from logstream import log
from notify import send_telegram

# The current Opus, at $4 / $20 per million tokens against Claude Opus 5's $5 /
# $25 (moved on 2026-10-03). The old estimate here, two dollars a month, counted
# three videos a day and the topic and script alone; it is now four a day plus
# search terms, metadata and the source judge, and the model's thinking is
# billed as output too. Still small next to the narration bill, but unmeasured:
# the console's usage page is the number to trust. Cheaper models exist; judging
# what is absurd, and what a source actually supports, is where the strongest
# model earns its keep.
DEFAULT_MODEL = "claude-opus-5-5"
# How hard the model thinks. Claude Opus 5.5 defaults to "medium", a level below
# what Claude Opus 5 did with no setting at all, so moving model without this
# would also have quietly cut the thinking behind every script. Every current
# Claude model accepts it; Claude Haiku 4.5 does not, so a SCRIPT_MODEL set to
# that would fail and go to the local model.
EFFORT = "high"
# When the model's safety classifiers decline a request, the API re-runs it on
# the model Anthropic recommends for that kind of refusal, in the same call.
# Claude Opus 5.5 added biology to the categories it declines, and this channel
# is half biology — horned lizards, opossums, onion chemistry. Without it a false
# positive costs the retry below and then goes to the local model.
SERVER_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Asked only when the primary is overloaded or down. Sonnet is a step down from
# Opus and a long way up from the 8B model. On 2026-09-28 the drop went from
# Opus straight to llama3.1:8b for five of a long video's eight sections, and
# the video went out public with a fabricated claim about a real doctor
# (https://youtu.be/uynIVYwuEvw).
DEFAULT_FALLBACK_MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 16000
REQUEST_TIMEOUT_SECONDS = 120

# How long to wait before asking the primary again while it is overloaded or
# unreachable. On 2026-09-28 claude-opus-5 answered 529 overloaded_error
# through most of a long video. The SDK's own two retries, a fraction of a
# second apart, gave up at once, and llama3.1:8b wrote five of the eight
# sections and the search terms. An overload lasts minutes, so the waits do
# too: three and a half minutes in all before the primary is given up on.
OUTAGE_BACKOFF_SECONDS = (30, 60, 120)
# "Not now" rather than "not this request". A 429 is a rate limit rather than
# an outage, but the cure is the same: wait.
OUTAGE_STATUSES = frozenset({429, 500, 502, 503, 504, 529})
# Once the ladder has run out, how long later calls skip it. A long video makes
# eight section calls and then the search terms; three and a half minutes each
# would hold the queue for half an hour to learn what the first call knew.
OUTAGE_MEMORY_SECONDS = 600
# The fallback's own retries: the SDK's default, three tries within a couple of
# seconds. Enough for a blip. By the time the fallback is asked the primary's
# ladder has already shown this is an outage, and a second ladder would stall
# the queue just the same.
FALLBACK_SDK_RETRIES = 2

# On 2026-09-27 at 15:00 the credit balance ran out, and every call after it
# answered 400 invalid_request_error "Your credit balance is too low to access
# the Anthropic API". Each one fell back to llama3.1:8b without a word, that
# video's search terms included, and nobody knew until a video was reviewed by
# hand. Waiting does not fix an account and neither does the fallback model,
# which bills the same one: the owner has to be told.
ACCOUNT_STATUSES = frozenset({401, 402, 403})
CREDIT_BALANCE_TEXT = "credit balance is too low"
# Often enough to be a reminder, rarely enough that one long video is not
# eight messages.
ACCOUNT_ALERT_INTERVAL_SECONDS = 4 * 60 * 60

# What a failed request means for the next one.
WROTE = "wrote"
REFUSED = "refused"
OUTAGE = "outage"
ACCOUNT = "account"
FAILED = "failed"

# Who wrote a script, weakest first. A long script is written a section at a
# time and can change hands part way, so callers compare writers rather than
# names: Opus, Sonnet and llama in turn make a llama script.
LOCAL = 0
FALLBACK = 1
PRIMARY = 2

# Seams for the tests, as in worker.py: nothing here may sleep for real there.
_sleep = time.sleep
_clock = time.monotonic

# Per process. The worker and the autopilot each find out for themselves, and a
# restart forgets, which is the right default after a deploy.
_down_until: Dict[str, float] = {}
_last_account_alert: Optional[float] = None


class Written(NamedTuple):
    """A completion and the model that actually wrote it."""

    text: str
    model: str


def api_key() -> str:
    return os.getenv("ANTHROPIC_API_KEY", "").strip()


def model_name() -> str:
    return os.getenv("SCRIPT_MODEL", "").strip() or DEFAULT_MODEL


def fallback_model_name() -> str:
    """The second Claude model, or "" when there is none.

    Unset means the default; set to an empty value means none, which is the
    way to turn it off. Naming the primary again means none as well: asking an
    overloaded model a second time is what the ladder is for.
    """
    raw = os.getenv("SCRIPT_FALLBACK_MODEL")
    name = DEFAULT_FALLBACK_MODEL if raw is None else raw.strip()
    return "" if name == model_name() else name


def is_configured() -> bool:
    return bool(api_key())


def scrub(text: str) -> str:
    """Removes the key from a message before it reaches a log."""
    key = api_key()
    return text.replace(key, "<redacted>") if key else text


def rank(model: str) -> int:
    """How strong a writer `model` is: LOCAL < FALLBACK < PRIMARY.

    Any Claude model other than the primary counts as a fallback: besides
    SCRIPT_FALLBACK_MODEL, the API's own refusal fallback can answer with a
    model this module never named. Anything else is the local one.
    """
    name = (model or "").strip()
    if name and name == model_name():
        return PRIMARY
    if name and (name == fallback_model_name() or name.startswith("claude-")):
        return FALLBACK
    return LOCAL


def weakest(models: Iterable[str]) -> Optional[str]:
    """The weakest writer among `models`, the earliest on a tie; None if empty."""
    names = list(models)
    return min(names, key=rank) if names else None


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


def _client(max_retries: int):
    """The SDK client, with as many of the SDK's own quick retries as asked."""
    import anthropic

    return anthropic.Anthropic(
        api_key=api_key(), timeout=REQUEST_TIMEOUT_SECONDS, max_retries=max_retries
    )


def _unreachable(err: Exception) -> bool:
    """A connection error or a timeout; the SDK raises both as APIConnectionError."""
    try:
        from anthropic import APIConnectionError
    except ImportError:
        return False
    return isinstance(err, APIConnectionError)


def _classify(err: Exception) -> str:
    """OUTAGE, ACCOUNT or FAILED: whether to wait, to tell the owner, or neither."""
    status = getattr(err, "status_code", None)
    if isinstance(status, int):
        if status in ACCOUNT_STATUSES:
            return ACCOUNT
        if status == 400 and CREDIT_BALANCE_TEXT in str(err).lower():
            return ACCOUNT
        if status in OUTAGE_STATUSES:
            return OUTAGE
        return FAILED
    return OUTAGE if _unreachable(err) else FAILED


def _alert_account_problem(err: Exception) -> None:
    """Tells the owner over Telegram, at most once an interval. Never raises."""
    global _last_account_alert
    now = _clock()
    if (
        _last_account_alert is not None
        and now - _last_account_alert < ACCOUNT_ALERT_INTERVAL_SECONDS
    ):
        return
    # Throttled on the attempt, not on delivery: with Telegram unconfigured or
    # down, every call would otherwise try again and could wait out notify's
    # timeout each time.
    _last_account_alert = now
    try:
        status = getattr(err, "status_code", None)
        if status in (401, 403):
            problem = f"Anthropic rejected the API key ({status})."
            action = "Check ANTHROPIC_API_KEY and its workspace in the Anthropic console."
        else:
            problem = "The Anthropic credit balance has run out."
            action = (
                "Top up the balance in the Anthropic console (Plans & Billing); "
                "Claude takes over again on the next call."
            )
        send_telegram(
            f"⚠️ {problem}\n"
            "Scripts are now being written by the local model.\n"
            f"{action}\n\n"
            f"{scrub(str(err))[:500]}"
        )
    except Exception as alert_err:
        log(f"[-] Could not send the account alert: {scrub(str(alert_err))}", "warning")


def _attempt(model: str, prompt: str, max_retries: int) -> Tuple[Optional[Written], str]:
    """One request. Returns the text and who wrote it, or None and what went wrong.

    The kinds are distinguished because each wants something different: a
    refusal is worth asking again with the brief stated, while a rate limit or
    a timeout will not care how the prompt is worded and is waited out
    instead. An account problem is reported here, where the error is in hand.

    Who wrote it is the model the response names, which is not `model` when the
    API's refusal fallback answered instead.
    """
    try:
        response = _client(max_retries).beta.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": EFFORT},
            betas=[SERVER_FALLBACK_BETA],
            fallbacks="default",
        )
    except Exception as err:
        log(f"[!] {model} unavailable ({scrub(str(err))}).", "warning")
        kind = _classify(err)
        if kind == ACCOUNT:
            _alert_account_problem(err)
        return None, kind

    if getattr(response, "stop_reason", None) == "refusal":
        return None, REFUSED

    text = "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    ).strip()
    if not text:
        log(f"[!] {model} returned nothing usable.", "warning")
        return None, FAILED
    served = getattr(response, "model", None)
    served = served if isinstance(served, str) and served else model
    if served != model:
        log(f"[!] {model} declined this one; {served} answered in its place.", "warning")
    return Written(text, served), WROTE


def _attempt_through_outage(model: str, prompt: str) -> Tuple[Optional[Written], str]:
    """One request, asked again on each rung of the ladder while it is an outage.

    The SDK's own retries are off here: the ladder is the retry policy, and a
    timeout the SDK retried twice would triple every rung.
    """
    written, kind = _attempt(model, prompt, max_retries=0)
    for delay in OUTAGE_BACKOFF_SECONDS:
        if kind != OUTAGE:
            break
        log(
            f"[!] {model} is overloaded or unreachable; asking again in {delay} s.",
            "warning",
        )
        _sleep(delay)
        written, kind = _attempt(model, prompt, max_retries=0)
    return written, kind


def _ask(model: str, prompt: str, patient: bool) -> Tuple[Optional[Written], str]:
    """One model's answer, asked a second time with the brief stated if it declines.

    `patient` waits out an outage on the ladder; otherwise the SDK's quick
    retries are all an outage gets.
    """

    def once(request: str) -> Tuple[Optional[Written], str]:
        if patient:
            return _attempt_through_outage(model, request)
        return _attempt(model, request, max_retries=FALLBACK_SDK_RETRIES)

    written, kind = once(prompt)
    if kind != REFUSED:
        return written, kind

    log(
        f"[!] {model} declined this one; asking again with the brief stated.",
        "warning",
    )
    written, kind = once(f"{RETRY_PREAMBLE}\n{prompt}")
    if kind == REFUSED:
        log(
            f"[!] {model} declined it twice; the local model writes this one.",
            "warning",
        )
    return written, kind


def write_with_model(prompt: str) -> Optional[Written]:
    """One completion from a Claude model, and which one; None if none can be had.

    Never raises. The primary is waited out through an overload, then the
    fallback model is asked; an account problem tells the owner and skips the
    fallback, which bills the same account. A missing key, a refusal that
    survives the retry or anything else returns None, and the caller falls back
    to the local model — a video written by Ollama beats no video.
    """
    if not is_configured():
        return None

    primary = model_name()
    fallback = fallback_model_name()
    stand_in = fallback or "the local model"

    remaining = _down_until.get(primary, float("-inf")) - _clock()
    if remaining > 0:
        log(
            f"[!] {primary} ran out of retries recently; {stand_in} writes this "
            f"one, and {primary} is tried again in {int(remaining // 60) + 1} min.",
            "warning",
        )
        kind = OUTAGE
    else:
        written, kind = _ask(primary, prompt, patient=True)
        if written:
            return written
        if kind == OUTAGE:
            _down_until[primary] = _clock() + OUTAGE_MEMORY_SECONDS
            log(
                f"[!] {primary} still unavailable after "
                f"{len(OUTAGE_BACKOFF_SECONDS) + 1} tries; {stand_in} writes this "
                f"one, and every call in the next {OUTAGE_MEMORY_SECONDS // 60} min.",
                "warning",
            )

    if kind != OUTAGE or not fallback:
        return None

    written, kind = _ask(fallback, prompt, patient=False)
    if written:
        return written
    if kind == OUTAGE:
        log(f"[!] {fallback} unavailable too; the local model writes this one.", "warning")
    return None


def write(prompt: str) -> Optional[str]:
    """The completion alone, for callers that do not record who wrote it.

    The metadata and the source judge only need the words; write_creative,
    which files a script under its author, uses write_with_model.
    """
    written = write_with_model(prompt)
    return written.text if written else None
