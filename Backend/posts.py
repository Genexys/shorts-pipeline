"""Community post text, written from a video the pipeline already published.

There is no API for creating community posts — the Data API has no such
resource, and `activities.insert` was removed years ago. So this generates text
for a person to paste into Studio, and nothing here touches YouTube.

Posts earn their place between uploads: at three videos a day there are four
quiet hours between them, and a post is the only thing that asks the audience
for a signal in that gap.
"""

import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from gpt import extract_json_object, generate_response
from logstream import log

# Poll shape is documented by YouTube: at most four options, 65 characters
# each. The body limit is not documented in the help pages; 1500 is the widely
# reported figure and the generator asks for far less anyway, so this is a
# backstop rather than a target.
POST_MAX_CHARS = 1500
POLL_MAX_OPTIONS = 4
POLL_MIN_OPTIONS = 2
POLL_OPTION_MAX_CHARS = 65

QUESTION = "question"
FACT = "fact"
POLL = "poll"
POST_KINDS: Tuple[str, ...] = (QUESTION, FACT, POLL)

# What each kind is for, in the prompt's own words.
KIND_BRIEFS = {
    QUESTION: (
        "Ask the audience one open question that the video raises but does not "
        "settle. It must be answerable from ordinary experience, not from "
        "expertise, so that answering costs a viewer nothing."
    ),
    FACT: (
        "State one concrete detail connected to the subject that the video "
        "itself does not cover. It must add something, not summarise what was "
        "already said."
    ),
    POLL: (
        "Pose a question with clearly distinct answers. The options must be "
        "genuinely arguable — a poll whose answer is obvious collects no "
        "signal. The text must not state, hint at or reason towards the "
        "answer: a viewer who has already been told what to think does not "
        "vote."
    ),
}


@dataclass(frozen=True)
class Post:
    """A community post, ready to paste."""

    kind: str
    text: str
    options: Tuple[str, ...] = field(default=())

    def render(self) -> str:
        """Plain text for a terminal, options numbered beneath a poll."""
        if not self.options:
            return self.text
        lines = [self.text, ""]
        lines += [f"  {index}. {option}" for index, option in enumerate(self.options, 1)]
        return "\n".join(lines)


def choose_post_kind(rng: Optional[random.Random] = None) -> str:
    """Picks a post kind at random.

    Rotating rather than always asking the same thing: a feed of nothing but
    polls reads as a bot, which is the whole failure mode being avoided.
    """
    return (rng or random).choice(list(POST_KINDS))


def clean_options(raw: object) -> List[str]:
    """Poll options within YouTube's limits, deduplicated, order preserved.

    Options too long to display are dropped rather than truncated: a poll
    option cut mid-word reads worse than a poll with three answers.
    """
    if not isinstance(raw, list):
        return []
    options: List[str] = []
    seen: set = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        option = " ".join(item.split())
        if not option or len(option) > POLL_OPTION_MAX_CHARS:
            continue
        if option.lower() in seen:
            continue
        options.append(option)
        seen.add(option.lower())
        if len(options) >= POLL_MAX_OPTIONS:
            break
    return options


def build_post(raw: Optional[dict], kind: str) -> Optional[Post]:
    """Validates one model answer into a Post, or None if there is nothing usable."""
    data = raw if isinstance(raw, dict) else {}
    text_value = data.get("text")
    text = " ".join(text_value.split()) if isinstance(text_value, str) else ""
    text = text[:POST_MAX_CHARS].strip()
    if not text:
        return None

    if kind != POLL:
        return Post(kind=kind, text=text)

    options = clean_options(data.get("options"))
    if len(options) < POLL_MIN_OPTIONS:
        # A poll with one answer is not a poll. The question is still a decent
        # post, so it is kept and demoted rather than thrown away.
        log(
            f"[!] Poll came back with {len(options)} usable option(s); "
            "posting it as a plain question instead.",
            "warning",
        )
        return Post(kind=QUESTION, text=text)
    return Post(kind=POLL, text=text, options=tuple(options))


def build_prompt(kind: str, subject: str, title: str, script: str) -> str:
    source = script.strip() or "(the script is not available; work from the subject)"
    poll_rule = (
        f'- options: {POLL_MIN_OPTIONS} to {POLL_MAX_OPTIONS} answers, each at '
        f"most {POLL_OPTION_MAX_CHARS} characters.\n"
        if kind == POLL
        else '- options: an empty list.\n'
    )
    return (
        "You write a YouTube community post for a channel that publishes short "
        "educational explainers.\n\n"
        f"Video title: {title}\n"
        f"Subject: {subject}\n\n"
        f"Script:\n{source}\n\n"
        f"Task: {KIND_BRIEFS[kind]}\n\n"
        'Return ONLY a JSON object: {"text": "...", "options": ["...", "..."]}\n\n'
        "Rules:\n"
        "- text: at most 3 sentences, plain English, no hashtags, no emojis, "
        "no links, no quotes around the whole thing.\n"
        "- Do not tell the viewer to like, subscribe or comment.\n"
        "- Do not describe the video. The post stands on its own.\n"
        f"{poll_rule}"
        "- Do not add any text before or after the JSON object."
    )


def generate_post(
    subject: str,
    title: str,
    script: str,
    ai_model: str,
    kind: Optional[str] = None,
    rng: Optional[random.Random] = None,
) -> Optional[Post]:
    """Writes one community post.

    Returns None when the model gives nothing usable. The caller is a person at
    a terminal who can simply run it again, so there is no fallback text: a
    generated-looking placeholder is worse than no post.
    """
    kind = kind or choose_post_kind(rng)
    if kind not in POST_KINDS:
        raise ValueError(f"kind must be one of {', '.join(POST_KINDS)}, got '{kind}'.")

    response = generate_response(build_prompt(kind, subject, title, script), ai_model)
    post = build_post(extract_json_object(response), kind)
    if post is None:
        log("[!] The model returned no usable post text.", "warning")
    return post
