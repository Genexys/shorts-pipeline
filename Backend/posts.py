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

from gpt import extract_json_object, generate_response, write_creative
from logstream import log

# Poll shape is documented by YouTube: at most four options, 65 characters
# each. The body limit is not documented in the help pages; 1500 is the widely
# reported figure and the generator asks for far less anyway, so this is a
# backstop rather than a target.
POST_MAX_CHARS = 1500
POLL_MAX_OPTIONS = 4
POLL_MIN_OPTIONS = 2
POLL_OPTION_MAX_CHARS = 65

# Quizzes are a different post type in Studio, with different limits: answers up
# to 80 characters, exactly one marked correct, and an explanation of up to 500
# characters. Three answers at least, because a two-answer quiz is a coin toss.
QUIZ_MAX_OPTIONS = 4
QUIZ_MIN_OPTIONS = 3
QUIZ_OPTION_MAX_CHARS = 80
QUIZ_EXPLANATION_MAX_CHARS = 500

QUESTION = "question"
FACT = "fact"
POLL = "poll"
QUIZ = "quiz"
POST_KINDS: Tuple[str, ...] = (QUESTION, FACT, POLL, QUIZ)

# Kinds that need grounding to be written at all. A "fact" post is a bare
# verifiable claim published in the channel's name, and an 8B model asked for
# one with no source produces plausible specifics with the details wrong: on
# 2026-09-09 it attributed the variation in left-handedness across countries to
# "social mobility", where the real finding is cultural pressure against writing
# left-handed. Questions and polls have nothing to fabricate.
#
# Research notes are the grounding that actually works. The script alone is not
# enough: a 120-word Short exhausts its own subject, so a model given only the
# script can restate it or invent, and nothing else. The notes hold everything
# research found — including the majority that never reached the video.
#
# A quiz needs it for the same reason and more: its correct answer is marked as
# correct in the channel's name, and a wrong one is corrected in the comments.
GROUNDED_KINDS: Tuple[str, ...] = (FACT, QUIZ)

# What each kind is for, in the prompt's own words.
KIND_BRIEFS = {
    QUESTION: (
        "Ask the audience one open question that the video raises but does not "
        "settle. It must be answerable from ordinary experience, not from "
        "expertise, so that answering costs a viewer nothing."
    ),
    FACT: (
        "State one concrete detail from the research notes that the script does "
        "NOT already say. Read the script first and rule out anything it "
        "covers, however differently worded. Restating the video's own point is "
        "the failure to avoid: on 2026-09-10 a post about the immortal "
        "jellyfish said only that it is biologically immortal, which was the "
        "video's opening line. Use nothing that is not in the notes."
    ),
    POLL: (
        "Pose a question with clearly distinct answers. The options must be "
        "genuinely arguable — a poll whose answer is obvious collects no "
        "signal. No two options may mean the same thing: a 'yes' split across "
        "two wordings divides the votes and measures nothing. The text must "
        "not state, hint at or reason towards the answer: a viewer who has "
        "already been told what to think does not vote. This is an opinion "
        "poll: it has no correct answer, and none of the options may be a "
        "fact that the others get wrong."
    ),
    QUIZ: (
        "Ask one question whose answer is a specific detail stated in the "
        "research notes. Prefer a detail the script does NOT already say. "
        "Give exactly one correct answer, taken from the notes, and wrong "
        "answers that a viewer who has not read the notes might believe but "
        "that the notes show to be wrong. No wrong answer may also be true, "
        "and the question must not give the answer away. The explanation says "
        "why the correct answer is right, using only the notes."
    ),
}


@dataclass(frozen=True)
class Post:
    """A community post, ready to paste."""

    kind: str
    text: str
    options: Tuple[str, ...] = field(default=())
    # Index into `options` of a quiz's correct answer; None for anything else.
    correct: Optional[int] = None
    # Shown to viewers after they answer a quiz.
    explanation: str = ""

    def render(self) -> str:
        """The draft as it is sent for pasting into Studio.

        A text post is the text alone, so it can be copied whole. A poll and a
        quiz are filled in field by field anyway, and they look identical as a
        list of options: without saying which is which, a poll was being
        entered as a quiz with no way to tell which answer to mark correct.
        """
        if not self.options:
            return self.text
        if self.kind == QUIZ:
            header = "QUIZ — select the ✅ answer as correct"
        else:
            header = "POLL — opinion, no correct answer"
        lines = [header, "", self.text, ""]
        for index, option in enumerate(self.options):
            mark = "  ✅" if index == self.correct else ""
            lines.append(f"  {index + 1}. {option}{mark}")
        if self.kind == QUIZ and self.explanation:
            lines += ["", "Explanation:", self.explanation]
        return "\n".join(lines)


def available_kinds(script: str, research: str = "") -> Tuple[str, ...]:
    """Kinds that can be written from what is actually known about the video.

    A fact needs the research notes, not the script: the script is what the
    video already said, so it is the one thing a fact post must not repeat.
    """
    if (research or "").strip():
        return POST_KINDS
    return tuple(kind for kind in POST_KINDS if kind not in GROUNDED_KINDS)


def choose_post_kind(
    script: str = "", rng: Optional[random.Random] = None, research: str = ""
) -> str:
    """Picks a post kind at random, from the ones this video can support.

    Rotating rather than always asking the same thing: a feed of nothing but
    polls reads as a bot, which is the whole failure mode being avoided.
    """
    return (rng or random).choice(list(available_kinds(script, research)))


def clean_options(raw: object, max_chars: int = POLL_OPTION_MAX_CHARS) -> List[str]:
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
        if not option or len(option) > max_chars:
            continue
        if option.lower() in seen:
            continue
        options.append(option)
        seen.add(option.lower())
        if len(options) >= POLL_MAX_OPTIONS:
            break
    return options


def correct_index(answer: object, options: List[str]) -> Optional[int]:
    """Which option the model marked correct, or None if that is not clear.

    Accepts the answer's own text, which is what the prompt asks for, or its
    number counting from one, which is what a model sometimes returns instead.
    """
    if isinstance(answer, bool):
        return None
    if isinstance(answer, int) or (isinstance(answer, str) and answer.strip().isdigit()):
        number = int(answer)
        return number - 1 if 1 <= number <= len(options) else None
    if not isinstance(answer, str):
        return None
    wanted = " ".join(answer.split()).lower()
    matches = [index for index, option in enumerate(options) if option.lower() == wanted]
    return matches[0] if len(matches) == 1 else None


def build_quiz(data: dict, text: str, rng: Optional[random.Random] = None) -> Post:
    """A quiz with its correct answer known, or the question alone.

    A quiz whose correct answer cannot be identified is exactly what this type
    exists to prevent, so it is demoted to a plain question rather than sent
    with a guess.
    """
    options = clean_options(data.get("options"), QUIZ_OPTION_MAX_CHARS)[:QUIZ_MAX_OPTIONS]
    correct = correct_index(data.get("answer"), options)
    if len(options) < QUIZ_MIN_OPTIONS or correct is None:
        log(
            f"[!] Quiz came back with {len(options)} usable answer(s) and "
            f"{'no identifiable' if correct is None else 'a'} correct one; "
            "posting it as a plain question instead.",
            "warning",
        )
        return Post(kind=QUESTION, text=text)

    # Shuffled, because a model puts the right answer first far more often than
    # chance, and a viewer learns that within a few quizzes.
    order = list(range(len(options)))
    (rng or random).shuffle(order)
    explanation_value = data.get("explanation")
    explanation = (
        " ".join(explanation_value.split())[:QUIZ_EXPLANATION_MAX_CHARS]
        if isinstance(explanation_value, str)
        else ""
    )
    return Post(
        kind=QUIZ,
        text=text,
        options=tuple(options[index] for index in order),
        correct=order.index(correct),
        explanation=explanation,
    )


def build_post(
    raw: Optional[dict], kind: str, rng: Optional[random.Random] = None
) -> Optional[Post]:
    """Validates one model answer into a Post, or None if there is nothing usable."""
    data = raw if isinstance(raw, dict) else {}
    text_value = data.get("text")
    text = " ".join(text_value.split()) if isinstance(text_value, str) else ""
    text = text[:POST_MAX_CHARS].strip()
    if not text:
        return None

    if kind == QUIZ:
        return build_quiz(data, text, rng)
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


def build_prompt(
    kind: str, subject: str, title: str, script: str, research: str = ""
) -> str:
    source = script.strip() or "(the script is not available; work from the subject)"
    notes = research.strip() or "(no research notes were kept for this video)"
    if kind == POLL:
        shape = '{"text": "...", "options": ["...", "..."]}'
        option_rule = (
            f"- options: {POLL_MIN_OPTIONS} to {POLL_MAX_OPTIONS} answers, each at "
            f"most {POLL_OPTION_MAX_CHARS} characters.\n"
        )
    elif kind == QUIZ:
        shape = (
            '{"text": "...", "options": ["...", "..."], "answer": "...", '
            '"explanation": "..."}'
        )
        option_rule = (
            f"- options: {QUIZ_MIN_OPTIONS} to {QUIZ_MAX_OPTIONS} answers, each at "
            f"most {QUIZ_OPTION_MAX_CHARS} characters.\n"
            "- answer: the correct option, copied exactly as it appears in options.\n"
            f"- explanation: at most {QUIZ_EXPLANATION_MAX_CHARS} characters, "
            "why that answer is right.\n"
        )
    else:
        shape = '{"text": "...", "options": []}'
        option_rule = "- options: an empty list.\n"
    return (
        "You write a YouTube community post for a channel that publishes short "
        "educational explainers.\n\n"
        f"Video title: {title}\n"
        f"Subject: {subject}\n\n"
        f"What the video already said (the script):\n{source}\n\n"
        f"Research notes, most of which did not reach the video:\n{notes}\n\n"
        f"Task: {KIND_BRIEFS[kind]}\n\n"
        f"Return ONLY a JSON object: {shape}\n\n"
        "Rules:\n"
        "- text: at most 3 sentences, plain English, no hashtags, no emojis, "
        "no links, no quotes around the whole thing.\n"
        "- Do not tell the viewer to like, subscribe or comment.\n"
        "- Do not describe the video. The post stands on its own.\n"
        f"{option_rule}"
        "- Do not add any text before or after the JSON object."
    )


def generate_post(
    subject: str,
    title: str,
    script: str,
    ai_model: str,
    kind: Optional[str] = None,
    rng: Optional[random.Random] = None,
    research: str = "",
) -> Optional[Post]:
    """Writes one community post.

    Returns None when the model gives nothing usable. The caller is a person at
    a terminal who can simply run it again, so there is no fallback text: a
    generated-looking placeholder is worse than no post.
    """
    kind = kind or choose_post_kind(script, rng, research)
    if kind not in POST_KINDS:
        raise ValueError(f"kind must be one of {', '.join(POST_KINDS)}, got '{kind}'.")
    if kind not in available_kinds(script, research):
        raise ValueError(
            f"'{kind}' needs the video's research notes, which are not stored for this "
            f"job. Without them the model can only repeat the script or "
            f"invent. Available here: "
            f"{', '.join(available_kinds(script, research))}."
        )

    prompt = build_prompt(kind, subject, title, script, research)
    # A quiz's marked answer goes out as correct in the channel's name, so it is
    # written by the stronger model where one is configured. The other kinds
    # stay on the local model, which writes them well enough for free.
    write = write_creative if kind == QUIZ else generate_response
    response = write(prompt, ai_model)
    post = build_post(extract_json_object(response), kind, rng)
    if post is None:
        log("[!] The model returned no usable post text.", "warning")
    return post
