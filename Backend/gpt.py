import re
import os
import json
import random
from ollama import Client, ResponseError

from dotenv import load_dotenv
from logstream import log
from speech import split_sentences
import writer
from typing import Callable, List, Optional, Tuple
from utils import ENV_FILE, MUSIC_MOODS

# Load environment variables
load_dotenv(ENV_FILE)

# Set environment variables
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "180"))

# Reasoning models emit a long chain of thought before answering, and put it in
# a separate `thinking` field that never reaches `content`. Measured on this
# box: qwen3.5:4b spent 521 seconds producing 12 151 characters of reasoning
# and returned an empty answer, where the same request with thinking off took
# 17 seconds. The pipeline needs the answer, not the reasoning. Models that
# cannot reason accept the flag and ignore it, so it is sent unconditionally.
THINKING_DISABLED = os.getenv("OLLAMA_THINKING", "").strip().lower() in (
    "",
    "0",
    "off",
    "false",
    "no",
)


def _ollama_client() -> Client:
    return Client(host=OLLAMA_BASE_URL, timeout=OLLAMA_TIMEOUT)


def _extract_model_name(model_obj) -> str:
    if hasattr(model_obj, "model") and getattr(model_obj, "model"):
        return str(getattr(model_obj, "model")).strip()
    if hasattr(model_obj, "name") and getattr(model_obj, "name"):
        return str(getattr(model_obj, "name")).strip()
    if isinstance(model_obj, dict):
        return str(model_obj.get("model") or model_obj.get("name") or "").strip()
    return ""


def list_ollama_models() -> Tuple[List[str], str]:
    """
    Returns available Ollama model names and configured default model.

    Returns:
        Tuple[List[str], str]: (available model names, default model)
    """
    try:
        response = _ollama_client().list()
    except Exception as err:
        raise RuntimeError(f"Failed to fetch Ollama models: {err}") from err

    models = []
    if hasattr(response, "models") and getattr(response, "models") is not None:
        models = list(getattr(response, "models"))
    elif isinstance(response, dict):
        models = response.get("models") or []

    model_names = [_extract_model_name(model) for model in models]
    model_names = [name for name in model_names if name]

    unique_names = list(dict.fromkeys(model_names))

    if OLLAMA_MODEL and OLLAMA_MODEL in unique_names:
        default_model = OLLAMA_MODEL
    elif unique_names:
        default_model = unique_names[0]
    else:
        default_model = OLLAMA_MODEL if OLLAMA_MODEL else ""

    return unique_names, default_model


def _chat(client, model_name: str, messages: list, disable_thinking):
    """One chat call, optionally asking the model not to think out loud."""
    if disable_thinking is None:
        return client.chat(model=model_name, messages=messages, stream=False)
    return client.chat(
        model=model_name, messages=messages, stream=False, think=not disable_thinking
    )


def write_creative(
    prompt: str,
    ai_model: str,
    report_model: Optional[Callable[[str], None]] = None,
) -> str:
    """A completion for the two calls where judgement shows: topic and script.

    Prefers the stronger model when one is configured and falls back to Ollama
    on any failure. Everything else in the pipeline is structured extraction
    and stays local.

    `report_model` is told which model actually wrote it. Without it the caller
    can only record what it asked for, which is how every script in the
    database came to be filed under llama3.1:8b regardless of who wrote it —
    and that is exactly the field we need when comparing retention by author.
    """
    written = writer.write(prompt)
    if written:
        if report_model:
            report_model(writer.model_name())
        return written
    if report_model:
        report_model((ai_model or "").strip() or OLLAMA_MODEL)
    return generate_response(prompt, ai_model)


def generate_response(prompt: str, ai_model: str) -> str:
    """
    Generate a script for a video, depending on the subject of the video.

    Args:
        video_subject (str): The subject of the video.
        ai_model (str): The AI model to use for generation.


    Returns:

        str: The response from the AI model.

    """

    model_name = (ai_model or "").strip() or OLLAMA_MODEL

    try:
        client = _ollama_client()
        messages = [{"role": "user", "content": prompt}]
        try:
            try:
                response = _chat(client, model_name, messages, THINKING_DISABLED)
            except TypeError:
                # An ollama client too old to know the parameter. Reasoning
                # models will be slow rather than broken.
                response = _chat(client, model_name, messages, None)
        except ResponseError as err:
            if err.status_code == 404:
                try:
                    response = client.generate(
                        model=model_name, prompt=prompt, stream=False
                    )
                except ResponseError as fallback_err:
                    if (
                        fallback_err.status_code == 404
                        and "not found" in str(fallback_err).lower()
                    ):
                        available_models, _ = list_ollama_models()
                        available = (
                            ", ".join(available_models) if available_models else "none"
                        )
                        raise RuntimeError(
                            f"Ollama model '{model_name}' is not installed. Available models: {available}. "
                            f"Install it with: ollama pull {model_name}"
                        ) from fallback_err
                    raise
            else:
                raise
    except RuntimeError:
        raise
    except Exception as err:
        raise RuntimeError(f"Failed to connect to Ollama: {err}") from err

    content = ""
    if hasattr(response, "message") and getattr(response, "message") is not None:
        message = getattr(response, "message")
        if hasattr(message, "content") and getattr(message, "content"):
            content = str(getattr(message, "content")).strip()
        elif isinstance(message, dict):
            content = str(message.get("content") or "").strip()

    if not content:
        if hasattr(response, "response") and getattr(response, "response"):
            content = str(getattr(response, "response")).strip()
        elif isinstance(response, dict):
            content = (
                str(response.get("message", {}).get("content") or "")
                or str(response.get("response") or "")
            ).strip()

    if not content:
        raise RuntimeError("Ollama returned an empty response.")

    return content


# Every video used to come out of one prompt, so they all opened the same way
# and unfolded the same way. YouTube's inauthentic-content policy asks that
# "the substance of each video should be materially varied", and a fixed
# narrative shape is exactly what "produced using a template" describes.
# One of these is drawn per video and steers the structure, not the subject.
SCRIPT_ANGLES = (
    "Open with one specific, surprising number or fact, then explain why it is true.",
    "Name a belief most people hold about this, then show what is actually the case.",
    "Walk through what happens step by step, in the order it happens.",
    "Compare two things that look alike and explain the one difference that matters.",
    "Start from the question a curious person would ask first, and answer it directly.",
    "Trace how this was worked out, and what it changed once it was known.",
    "Describe the problem this solves, and what the world looked like before it.",
    "Take the reader from the everyday version of this to the surprising one underneath.",
)


# What kind of thing the video is about. Not a tone of voice: asking an 8B
# model to be funny produces strained puns and "science is amazing!". Asking it
# to report something absurd but true, plainly, produces the laugh by itself —
# the delivery stays deadpan and the subject does the work.
EXPLAINER = "explainer"
CURIO = "curio"
ANNIVERSARY = "anniversary"
REGISTERS = (EXPLAINER, CURIO, ANNIVERSARY)

TOPIC_BRIEFS = {
    EXPLAINER: (
        "A concrete fact, question or claim about how something works, not a "
        "broad category."
    ),
    CURIO: (
        "Something genuinely absurd that is nevertheless true: a real "
        "phenomenon, experiment, animal behaviour or historical episode that "
        "sounds invented. It must be verifiable, not a joke, not an urban "
        "legend, and not a 'fun fact' that is merely mildly interesting. If a "
        "reader would say \"that cannot be real\", it qualifies. "
        "State it exactly, without overselling: no 'literally', no 'never', no "
        "superlative the evidence does not carry. An overstated premise is one "
        "the script then has to defend, and it will defend it by inventing."
    ),
    ANNIVERSARY: (
        "Pick ONE event from the dated list below and make a topic of it. It "
        "must be a discovery, an invention, an experiment, an expedition or a "
        "first — something about how the world works or how we found out. If "
        "nothing in the list is about science, technology, nature or the "
        "history behind an everyday thing, return the JSON object with an "
        "empty subject rather than forcing one. Keep the date out of the topic "
        "line itself — it is where the subject comes from, not what the video "
        "announces — but return the event you picked, verbatim, in an "
        "\"anchor\" field alongside the subject. The script is written later by "
        "something that will not see this list, and without the anchor it "
        "writes about whatever the words happen to match."
    ),
}

SCRIPT_REGISTER_RULES = {
    EXPLAINER: "",
    ANNIVERSARY: "",
    CURIO: (
        "    Tone: completely straight. The subject is absurd on its own and "
        "needs no help.\n"
        "    Do not make jokes, puns or asides. Do not tell the viewer that "
        "this is funny,\n"
        "    weird or amazing, and do not use exclamation marks. State it "
        "plainly and let it land.\n"
    ),
}


def register_rules(register: Optional[str]) -> str:
    """The tone instruction for a register, or "" for the default one."""
    return SCRIPT_REGISTER_RULES.get(register or EXPLAINER, "")


def choose_script_angle() -> str:
    """Picks the narrative shape for one video."""
    return random.choice(SCRIPT_ANGLES)

def parse_string_array(response: str) -> List[str]:
    """Parses a JSON array of strings out of an LLM response, tolerating noise.

    Tries the whole response, then the first bracketed span, then falls back to
    collecting quoted strings. Entries that are not strings are dropped: a
    number surviving into the result used to crash the caller that joins it
    for logging.

    Args:
        response (str): Raw model output.

    Returns:
        List[str]: Non-empty strings, in order. Empty when nothing parses.
    """

    def usable(items: object) -> List[str]:
        if not isinstance(items, list):
            return []
        return [
            item.strip()
            for item in items
            if isinstance(item, str) and item.strip()
        ]

    try:
        found = usable(json.loads(response))
        if found:
            return found
    except (json.JSONDecodeError, TypeError):
        pass

    match = re.search(r"\[[\s\S]*\]", response or "")
    if match:
        try:
            found = usable(json.loads(match.group()))
            if found:
                return found
        except json.JSONDecodeError:
            pass

    quoted = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', response or "")
    return [item.strip() for item in quoted if item.strip()]


# The model announces itself despite being told not to: a published video
# opened with the narrator saying 'Here is the script for section 1: "The Stench
# of Ignorance".' Anchored to the start of a line and required to end at a
# sentence break, so it cannot eat real prose.
SCRIPT_PREAMBLE_RE = re.compile(
    r"^[ \t]*(?:here(?:'s| is)|below is|this is)"
    r"[^\n:.]{0,60}\b(?:script|section|narration)\b[^\n:.]{0,60}"
    r"[:.][ \t]*(?:\"[^\"\n]*\"[. \t]*)?$\n*",
    re.IGNORECASE | re.MULTILINE,
)


def trim_to_words(script: str, target: int, ceiling: Optional[int] = None) -> str:
    """Cuts a script back to about `target` words, on sentence boundaries.

    Paragraph granularity is too coarse to land on a word count. A real script
    came back as paragraphs of 37, 63, 68 and 52 words: two paragraphs give 100
    and three give 168, and the 120 asked for falls in the gap. Keeping all four
    shipped a 94-second Short where 50 was intended.

    Sentences are the same boundaries the narration is chunked on, so a cut here
    never lands mid-utterance. The sentence that crosses the target is kept, so
    the result is at or just over it rather than under — undershooting is what
    the retry in generate_script is for.

    `ceiling` is the limit the result must not cross, where `target` is only the
    point at which it stops looking for more. Keeping the crossing sentence is
    fine when the overshoot is a few words and ruinous when it is thirty: a
    120-word target produced a 152-word script and sixty-four seconds of
    narration. Given a ceiling, a sentence that would cross it is dropped rather
    than kept, so the length is bounded instead of approximate. The first
    sentence is never dropped — a ceiling too small for it is a misconfiguration,
    and an empty script is worse than a long one. Nor is the closing sentence,
    if it is the only one left and it fits within CLOSING_GRACE_WORDS of the
    limit: a second of audio is a cheaper price than an ending.
    """
    written = len(script.split())
    if target <= 0:
        return script
    if written <= target and (ceiling is None or written <= ceiling):
        return script

    blocks = [
        split_sentences(block)
        for block in re.split(r"\n\s*\n", script)
        if block.strip()
    ]
    flat = [sentence for sentences in blocks for sentence in sentences]

    taken = 0
    words = 0
    for sentence in flat:
        length = len(sentence.split())
        if taken and ceiling is not None and words + length > ceiling:
            break
        taken += 1
        words += length
        if words >= target:
            break

    # Never leave exactly one sentence behind. A lone closing sentence is the
    # payoff far more often than it is padding, and cutting it turns the
    # sentence before it into a setup with nothing after it. Measured on a
    # published Short: the draft ended "Hotter water doesn't just catch up. It
    # wins." — the target was reached two words early, "It wins" was dropped,
    # and the video went out on the setup alone.
    if taken == len(flat) - 1:
        closing = len(flat[-1].split())
        allowance = (ceiling if ceiling is not None else target) + CLOSING_GRACE_WORDS
        if words + closing <= allowance:
            taken += 1

    kept: List[str] = []
    used = 0
    for sentences in blocks:
        room = min(len(sentences), taken - used)
        if room > 0:
            kept.append(" ".join(sentences[:room]))
            used += room
        if used >= taken:
            break
    return "\n\n".join(kept)


def clean_script_text(response: str) -> str:
    """Removes the formatting the model is told not to produce but sometimes does.

    Deliberately does not strip surrounding whitespace: generate_script splits
    on blank lines afterwards, and trimming here would change which paragraphs
    it selects.
    """
    cleaned = (response or "").replace("*", "").replace("#", "")
    cleaned = re.sub(r"\[.*\]", "", cleaned)
    cleaned = re.sub(r"\(.*\)", "", cleaned)
    return SCRIPT_PREAMBLE_RE.sub("", cleaned)

def generate_script(
    video_subject: str,
    paragraph_number: int,
    ai_model: str,
    voice: str,
    customPrompt: str,
    angle: Optional[str] = None,
    target_words: Optional[int] = None,
    research: str = "",
    register: Optional[str] = None,
    lead_with_payoff: bool = False,
    anchor: str = "",
    max_words: Optional[int] = None,
    report_model: Optional[Callable[[str], None]] = None,
    _retry: bool = True,
) -> Optional[str]:
    """
    Generate a script for a video, depending on the subject of the video, the number of paragraphs, and the AI model.



    Args:

        video_subject (str): The subject of the video.

        paragraph_number (int): The number of paragraphs to generate.

        ai_model (str): The AI model to use for generation.



    Returns:

        str: The script for the video.

    """

    # Build prompt

    if customPrompt:
        # An explicit prompt from the caller wins, as it always has.
        prompt = customPrompt
    else:
        angle = angle or choose_script_angle()
        log(f"[+] Script angle: {angle}", "info")
        prompt = f"""
            Structure this script like so: {angle}

        """ + """
            Generate a script for a video, depending on the subject of the video.

            The script is to be returned as a string with the specified number of paragraphs.

            Here is an example of a string:
            "This is an example string."

            Do not under any circumstance reference this prompt in your response.

            Get straight to the point, don't start with unnecessary things like, "welcome to this video".

            Obviously, the script should be related to the subject of the video.

            YOU MUST NOT INCLUDE ANY TYPE OF MARKDOWN OR FORMATTING IN THE SCRIPT, NEVER USE A TITLE.
            YOU MUST WRITE THE SCRIPT IN THE LANGUAGE SPECIFIED IN [LANGUAGE].
            ONLY RETURN THE RAW CONTENT OF THE SCRIPT. DO NOT INCLUDE "VOICEOVER", "NARRATOR" OR SIMILAR INDICATORS OF WHAT SHOULD BE SPOKEN AT THE BEGINNING OF EACH PARAGRAPH OR LINE. YOU MUST NOT MENTION THE PROMPT, OR ANYTHING ABOUT THE SCRIPT ITSELF. ALSO, NEVER TALK ABOUT THE AMOUNT OF PARAGRAPHS OR LINES. JUST WRITE THE SCRIPT.

        """

    # "One paragraph" is whatever the model feels like: measured across real
    # runs it produced anything from 11 to 33 seconds of speech. A word count
    # is the only instruction that actually pins the length down.
    # A word count and a paragraph count are rival instructions, and asking for
    # both is how a 167-word script became an 89-word one: the model obeyed the
    # words, wrote two paragraphs, and the paragraph cap threw the second away.
    # Where a word target exists it is the only length instruction given.
    if target_words:
        floor = int(target_words * SCRIPT_WORD_FLOOR_RATIO)
        length = (
            f"    Length: about {target_words} words, and no fewer than {floor}.\n"
            f"    Use as many paragraphs as that takes.\n"
        )
    else:
        length = f"    Number of paragraphs: {paragraph_number}\n"

    prompt += f"""
    
    Subject: {video_subject}
{length}    Language: {voice}
{OPENING_RULES if lead_with_payoff else ""}{ENDING_RULES}{anchor_rules(anchor)}{register_rules(register)}{research_rules(research)}
    """

    # Generate script
    response = write_creative(prompt, ai_model, report_model=report_model)

    log(response, "info")

    # Return the generated script
    if response:
        response = clean_script_text(response)

        # Split the script into paragraphs
        paragraphs = [block for block in response.split("\n\n") if block.strip()]

        # Drop a leading title. The prompt forbids one and the model writes it
        # anyway; selecting it as the whole script yields a few seconds of audio.
        while (
            len(paragraphs) > 1
            and len(paragraphs[0].split()) <= TITLE_FRAGMENT_MAX_WORDS
        ):
            dropped = paragraphs.pop(0).strip()
            log(f"[*] Dropped a title-like opening line: {dropped[:60]}", "warning")

        # Keep everything when a word target set the length; the paragraph
        # count is only an instrument when no target exists.
        selected_paragraphs = paragraphs if target_words else paragraphs[:paragraph_number]

        # Join the selected paragraphs into a single string
        final_script = "\n\n".join(selected_paragraphs)

        cut_short = False
        if target_words:
            before = len(final_script.split())
            final_script = trim_to_words(final_script, target_words, ceiling=max_words)
            after = len(final_script.split())
            # Word counts rather than string equality: the rebuild rejoins each
            # paragraph's sentences with single spaces, so a script that lost
            # nothing can still differ by whitespace.
            cut_short = after < before
            if after < before:
                capped = f", capped at {max_words}" if max_words else ""
                log(
                    f"[*] Trimmed the script from {before} to {after} words "
                    f"for a {target_words}-word target{capped}.",
                    "info",
                )

        # After the trim, not before: the cut is itself a way to end badly. The
        # ceiling stops inside whichever paragraph it reaches, which on a real
        # script left the video ending on "Then things turn."
        word_floor = int(target_words * SCRIPT_WORD_FLOOR_RATIO) if target_words else 0
        landed = drop_weak_ending(final_script, word_floor, cut_short=cut_short)
        if landed != final_script:
            dropped = (split_sentences(final_script) or [""])[-1]
            log(f"[*] Dropped a trailing aside: {dropped[:80]}", "info")
            final_script = landed

        log(f"Number of paragraphs used: {len(selected_paragraphs)}", "success")

        # Three ways a draft can be wrong, and one retry covers all of them.
        # The floor was only ever a sentence in the prompt; a weak opening is
        # what the retention numbers actually punish; and an ending that does
        # not land is the last thing anyone who stayed will hear.
        problem = None
        if target_words:
            written = len(final_script.split())
            floor = int(target_words * SCRIPT_WORD_FLOOR_RATIO)
            if written < floor:
                problem = (
                    f"came back {written} words, under the {floor} floor "
                    f"for a {target_words}-word target"
                )
        if problem is None and lead_with_payoff and opens_weakly(final_script):
            opening = (split_sentences(final_script) or [""])[0]
            problem = f'opens on setup rather than the fact: "{opening[:80]}"'
        if problem is None and ends_weakly(final_script, cut_short=cut_short):
            # Only reached when the sentence could not simply be dropped, i.e.
            # removing it would leave the script under its floor.
            closing = (split_sentences(final_script) or [""])[-1]
            problem = f'ends on an aside rather than the point: "{closing[:80]}"'

        if problem:
            log(
                f"[!] Script {problem}."
                + (" Retrying once." if _retry else ""),
                "warning",
            )
            if _retry:
                return generate_script(
                    video_subject,
                    paragraph_number,
                    ai_model,
                    voice,
                    customPrompt,
                    angle=angle,
                    target_words=target_words,
                    research=research,
                    register=register,
                    lead_with_payoff=lead_with_payoff,
                    anchor=anchor,
                    max_words=max_words,
                    report_model=report_model,
                    _retry=False,
                ) or final_script

        return final_script
    else:
        log("[-] GPT returned an empty response.", "error")
        return None


def get_search_terms(
    video_subject: str, amount: int, script: str, ai_model: str
) -> List[str]:
    """
    Generate a JSON-Array of search terms for stock videos,
    depending on the subject of a video.

    Goes through write_creative rather than straight to Ollama. This looked
    like structured extraction and is not: deciding whether "chemical defense"
    names something a camera can point at is the same judgement a small model
    lacks everywhere else. Measured on one subject, the local model returned
    "sulfur compounds" and "lacrimal glands" where the stronger one returned
    "onion slices closeup" and "knife cutting board".

    Args:
        video_subject (str): The subject of the video.
        amount (int): The amount of search terms to generate.
        script (str): The script of the video.
        ai_model (str): The AI model to use for generation.

    Returns:
        List[str]: The search terms for the video subject.
    """

    # Build prompt
    prompt = f"""
    Generate {amount} search terms for stock videos,
    depending on the subject of a video.
    Subject: {video_subject}

    The search terms are to be returned as
    a JSON-Array of strings.

    Each search term is 1-3 words naming something filmable.

    Every term must describe something a viewer would accept as a picture of
    THIS subject. Apply this test to each one: if the clip it returns would sit
    just as naturally in a video about something else, the term is wrong.

    Take the terms from the subject itself, never from the script's figures of
    speech. A script about onions may call the irritant "tear gas"; searching
    that returns riot footage and birds scattering off a river, because a stock
    library matches the words and not the meaning. The same goes for abstract
    nouns — "mechanism", "process", "reaction", "system" — which return
    whatever the library happens to have filed under them.

    Cover different angles so the results do not overlap: the setting, the
    creatures or objects, the physical process, the human activity around it,
    the scale. Repeating the subject in every term returns the same handful of
    clips over and over, which is the one thing to avoid.
    
    YOU MUST ONLY RETURN THE JSON-ARRAY OF STRINGS.
    YOU MUST NOT RETURN ANYTHING ELSE. 
    YOU MUST NOT RETURN THE SCRIPT.
    
    The search terms must be related to the subject of the video.
    Here is an example of a JSON-Array of strings:
    ["search term 1", "search term 2", "search term 3"]

    For context, here is the full text:
    {script}
    """

    # Generate search terms
    response = write_creative(prompt, ai_model)
    log(response, "info")

    search_terms = parse_string_array(response)
    if not search_terms:
        log("[*] GPT returned no usable search terms.", "warning")

    # Let user know
    log(f"\nGenerated {len(search_terms)} search terms: {', '.join(search_terms)}", "info")

    # Return search terms
    return search_terms


TITLE_MAX_CHARS = 100
DESCRIPTION_MAX_CHARS = 4500
TAG_MAX_CHARS = 30
TAGS_MAX_TOTAL_CHARS = 400
TAGS_MAX_COUNT = 15

# Hashtags live in the description, not in snippet.tags: YouTube renders the
# first three above the title. It ignores every hashtag in a description that
# carries more than 15, so the cap stays well clear of that.
HASHTAG_MAX_COUNT = 5
HASHTAG_MAX_CHARS = 30
# Kept for callers that do not name a format; the format overrides it.
ALWAYS_HASHTAGS = ("#Shorts",)
# Question scaffolding, not subject matter. Autopilot topics are whole
# questions ("Can plants grow in space without light?"), and the words a viewer
# would actually search for are the ones left after these come out.
SUBJECT_STOPWORDS = frozenset(
    """a an and are as at be been but by can could did do does for from get
    had has have how in into is it its like make more new not of on or our so
    than that the their them then there these they this to up was we were what
    when where which who why will with without you your""".split()
)
SUBJECT_KEYWORD_COUNT = 5

# What the first sentence must do. Measured on the channel's first week: the
# average view was 5.6 seconds against a 38-second video, and the opening
# sentence of five videos out of six was pure setup — "Sleep is a vital part of
# our lives", "Animals migrate to find more abundant food sources". At roughly
# 2.5 words a second, twelve words of preamble is five seconds. Viewers were
# leaving exactly when the interesting part would have started.
OPENING_RULES = """
    Your FIRST sentence must contain the surprising thing itself. Not a
    definition of the subject, not why it matters, not that scientists have
    wondered about it, not what the video will cover. If the first sentence
    would still make sense with the subject swapped for another, it is wasted.
    Say the fact, then explain it.
"""

# Openings that say nothing, each taken from a published video's first
# sentence. A blocklist is a heuristic and will not catch every evasion — the
# instruction above does the work; this catches the ones it misses.
WEAK_OPENING_PATTERNS = (
    r"\bis (?:a |an )?(?:vital|essential|important|fascinating|complex|common|remarkable)\b",
    r"\b(?:have|has) (?:long )?(?:puzzled|fascinated|intrigued|baffled)\b",
    r"\bscientists have (?:long )?\b",
    r"\b(?:have|has) adapted to\b",
    r"\bplays? (?:a|an) (?:vital|important|key|crucial|significant) role\b",
    r"\bin this video\b",
    r"\b(?:have|has) you ever wondered\b",
    r"\bone of the most (?:fascinating|interesting|common|remarkable)\b",
    r"\bfor centuries\b",
    r"\bwhen it comes to\b",
)
_WEAK_OPENING_RE = re.compile("|".join(WEAK_OPENING_PATTERNS), re.IGNORECASE)


def opens_weakly(script: str) -> bool:
    """Whether the first sentence spends itself on setup instead of the fact."""
    first = (split_sentences(script or "") or [""])[0]
    return bool(_WEAK_OPENING_RE.search(first))


# What the last sentence must do. The counterpart to OPENING_RULES, and it comes
# from the same failure at the other end. A Short about scratching built to a
# real twist — scratching releases serotonin, which makes the itch worse — and
# then spent its closing eight seconds on "the peripheral nervous system appears
# to play a powerful role in this relief too, since itch is carried by a specific
# subpopulation of nerve fibers". That is a research note the writer had left
# over, not an ending, and it is the last thing the viewer hears.
ENDING_RULES = """
    Your LAST sentence is the one the viewer leaves on. End on the part that
    lands: the consequence, the turn, the thing they would repeat to someone
    else. Do not end on a hedge, a caveat, an aside, a second mechanism you had
    no room to explain, or a fact that is in the script only because a source
    happened to mention it. If the strongest thing you have to say is in the
    middle, the script is in the wrong order.
"""

# Endings that trail off. As with the opening list, the instruction above does
# the work and this catches what it misses — hedges, tacked-on second causes,
# and the "more research is needed" close that a sourced script drifts into.
WEAK_ENDING_PATTERNS = (
    r"\b(?:appears?|seems?) to\b",
    r"\bis (?:thought|believed|considered) to\b",
    r"\bmay (?:also )?(?:play|be|help|contribute|explain)\b",
    r"\b(?:also|too) (?:plays?|contributes?|matters?)\b",
    r"\bplays? (?:a|an) (?:powerful|important|key|crucial|significant|vital) role\b",
    r"\b(?:more|further) (?:research|study|work)\b",
    r"\b(?:scientists|researchers) (?:are still|continue to|have yet to|do not yet)\b",
    r"\bremains? (?:unclear|unknown|a mystery|to be seen)\b",
    r"\b(?:too|as well)\s*[.!?]*\s*$",
)
_WEAK_ENDING_RE = re.compile("|".join(WEAK_ENDING_PATTERNS), re.IGNORECASE)


# A closing sentence this short is a stub — when something was cut from after
# it. Trimming the scratching script to fit ended it on "Then things turn.", a
# promise the video never keeps. Length alone does not decide it: a deliberate
# short ending is the best kind, and the very next Short was written to close on
# "It wins." So this applies only where the trim actually removed what followed.
TRAILING_STUB_MAX_WORDS = 3


def ends_weakly(script: str, cut_short: bool = False) -> bool:
    """Whether the last sentence trails off instead of landing.

    `cut_short` says the trim removed what came after it, which is the only
    circumstance in which a very short closing sentence is evidence of anything.
    """
    sentences = split_sentences(script or "")
    if not sentences:
        return False
    if _WEAK_ENDING_RE.search(sentences[-1]):
        return True
    # A script of one sentence is whatever it is; there is nothing to stub.
    return (
        cut_short
        and len(sentences) > 1
        and len(sentences[-1].split()) <= TRAILING_STUB_MAX_WORDS
    )


def drop_weak_ending(script: str, floor: int, cut_short: bool = False) -> str:
    """Removes a trailing sentence that trails off, when there is room for it.

    Cheaper and safer than asking for a rewrite: everything before the last
    sentence is already what was wanted, and a second draft puts that at risk to
    fix eight seconds. Only one sentence goes, and only while the result stays
    above the word floor — beyond that it is a rewrite, which is what the retry
    is for.
    """
    if not ends_weakly(script, cut_short=cut_short):
        return script

    blocks = [block for block in re.split(r"\n\s*\n", script) if block.strip()]
    if not blocks:
        return script

    sentences = split_sentences(blocks[-1])
    if len(sentences) > 1:
        trimmed = blocks[:-1] + [" ".join(sentences[:-1])]
    elif len(blocks) > 1:
        # The weak sentence is a paragraph of its own; there is another to end on.
        trimmed = blocks[:-1]
    else:
        return script

    candidate = "\n\n".join(trimmed)
    return script if len(candidate.split()) < floor else candidate

# The instruction that makes concrete detail safe. Specifics are what make a
# script worth trusting — a real figure a viewer can check beats "some research
# suggests" — but an 8B model asked for one with no source invents it, and an
# invented citation is worse than no citation: it looks verifiable and is not.
RESEARCH_RULES = """
    Use the numbered research notes below as your only source of specifics.
    Concrete detail is wanted: figures, dates, place names and named studies
    make the script worth trusting, so use the ones the notes give you.
    You MUST NOT state any number, date, percentage, institution, researcher
    or study that does not appear in the notes.
    A date printed on a page is not the date of the event it describes. Pages
    carry publication and update stamps, and a note updated this year may be
    describing something from decades ago. Give a year only when the notes say
    the event happened then; otherwise say when it happened in words the notes
    support, or leave the timing out.
    You MUST NOT invent how something works. Any mechanism, structure, cause or
    process you describe has to come from the notes. If the notes do not explain
    the subject, say what they do support and stop — do not fill the gap with a
    plausible-sounding explanation.
    If the subject itself is not borne out by the notes, write about what the
    notes actually show rather than defending the premise.
    Where the notes do not support a specific, write the general statement
    instead. Do not cite the notes by number, and do not mention that notes
    exist.
"""
NO_RESEARCH_RULES = """
    You have no sources, so you must not invent the appearance of one. Do not
    state any specific figure, percentage, date, named study, named researcher
    or named institution, and do not describe any mechanism, structure or
    process you are not certain of. Write what is generally established, in
    general terms, without fabricated precision and without inventing an
    explanation to fill the length.
"""


def figures_used(sections: List[str]) -> set:
    """Numbers already stated across the sections written so far.

    Bare digits are matched rather than parsed: the point is to stop the same
    headline figure appearing in every section, and "1 trillion", "400" and
    "2014" all count equally for that.
    """
    used: set = set()
    for section in sections:
        for match in re.findall(r"\b\d[\d.,]*\b", section):
            cleaned = match.rstrip(".,")
            if len(cleaned) > 1:
                used.add(cleaned)
    return used


# The last section of a long video is the only one that has to close, and it is
# the one most likely to open something instead. A five-minute video about why
# fingers wrinkle in water ended on "while conditions like Raynaud's shrink those
# vessels with no water at all" — a named condition arriving in the final clause
# with nowhere left to land. The per-section rules say not to cover what later
# sections will cover, which for the last section says nothing at all.
LONG_ENDING_RULES = """        - This is the last section, so it has to close the video rather than
          only itself. End on what the whole video was for: the consequence,
          the turn, what the viewer now knows that they did not.
        - Do not introduce a new named thing in your closing sentences — a
          condition, a researcher, an institution, a second phenomenon.
          Anything named this late has no room to land.
        - Do not end on a hedge, a caveat, or a note about what remains unknown.
"""


ANCHOR_RULES = """
    This video is about one specific event, given below. Write about that event
    and nothing else. If the research notes are mostly about something adjacent
    — a later development, a different team, a modern version — they are the
    wrong notes: say what you can about the event itself and stop. Do not
    transplant the story onto whoever the notes happen to describe.
"""


def anchor_rules(anchor: str) -> str:
    """The grounding line for a video built from one named event."""
    if not (anchor or "").strip():
        return ""
    return f"{ANCHOR_RULES}\n    The event: {anchor.strip()}\n"


def research_rules(brief: str) -> str:
    """The specifics rule that matches what the writer actually has."""
    if brief and brief.strip():
        return f"{RESEARCH_RULES}\n    Research notes:\n{brief}\n"
    return NO_RESEARCH_RULES
# YouTube renders the first three hashtags above the title, so three is what a
# subject-derived fallback can actually show.
SUBJECT_HASHTAG_COUNT = 3
MAX_JSON_CANDIDATES = 32

# Fewer words than this is a fragment, not a sentence worth putting in a title.
TITLE_MIN_WORDS = 4

# A leading fragment this short is a title, not a paragraph. The prompt forbids
# titles and the model writes them anyway; taking one as the whole script
# produces a three-second video.
TITLE_FRAGMENT_MAX_WORDS = 8

# Models undershoot a word count. Measured: asked for 90, wrote 62. Stating a
# floor as well lands much closer than an approximate target alone.
SCRIPT_WORD_FLOOR_RATIO = 0.85

# Words of narration per second, used to turn a format's duration ceiling into
# a word ceiling. Measured across twelve published Shorts: 118 to 152 words
# against 50.6 to 64.1 seconds of ElevenLabs audio, which is 2.00 to 2.46 words
# a second including the pauses it leaves between sentences. The slowest of
# those is the one to divide by — a cap computed from the average would be
# breached by every below-average run, which is the failure it exists to stop.
NARRATION_WORDS_PER_SECOND = 2.0

# How far the closing sentence may run past the limit rather than be cut. Six
# words is three seconds at the slowest measured pace, and a closing line is
# short by nature — the one this exists for was two words long.
CLOSING_GRACE_WORDS = 6


def words_for_seconds(seconds: Optional[float]) -> Optional[int]:
    """The most words that fit in `seconds` of narration, or None for no limit."""
    if not seconds or seconds <= 0:
        return None
    return max(1, int(seconds * NARRATION_WORDS_PER_SECOND))


def _iter_balanced_brace_spans(text: str):
    """Yield substrings from each '{' to its matching '}', ignoring braces
    that appear inside double-quoted JSON string values.

    Stops after MAX_JSON_CANDIDATES start positions so a pathological or
    unbounded LLM response (e.g. many unmatched '{' characters) cannot make
    this scan quadratic in the length of the text.
    """
    n = len(text)
    attempts = 0
    for i in range(n):
        if text[i] != "{":
            continue
        if attempts >= MAX_JSON_CANDIDATES:
            break
        attempts += 1
        depth = 0
        in_string = False
        escaped = False
        end = None
        j = i
        while j < n:
            ch = text[j]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                j += 1
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = j
                    break
            j += 1
        if end is not None:
            yield text[i : end + 1]


def extract_json_object(response: str) -> Optional[dict]:
    """Parse a JSON object from an LLM response, tolerating surrounding text.

    Tries the whole response as JSON first, then falls back to a bounded,
    string-aware balanced-brace scan. There is no regex fallback: if a
    span from the first '{' to the last '}' were valid JSON, it would be
    balanced, so the scan's first candidate would already have found it.
    """
    try:
        parsed = json.loads(response)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        return parsed

    for candidate in _iter_balanced_brace_spans(response):
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed

    return None


def _truncate_at_word(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip()


def _capitalized(subject: str) -> str:
    cleaned = re.sub(r"\s+", " ", subject).strip()
    if not cleaned:
        return "Untitled video"
    return cleaned[:1].upper() + cleaned[1:]


def title_from_opening(script: str) -> str:
    """The script's own first sentence as the title, when it can serve as one.

    Only meaningful where OPENING_RULES applies, because that is what makes the
    first sentence the hook rather than a definition. Where it holds, the best
    line in the video is already written, and the metadata model reliably writes
    something blander: a Short that opened "Scratching works by hurting you"
    went up titled "Scratching for Relief".

    Returns "" when the sentence cannot carry a title — too long to show without
    truncation, or too short to say anything — and the model's own title stands.
    """
    first = (split_sentences(script or "") or [""])[0]
    first = re.sub(r'[*#"<>]', "", first)
    first = re.sub(r"\s+", " ", first).strip()
    # A title carries no full stop; a question mark or an exclamation is part of
    # the line and stays.
    first = first.rstrip(".").strip()
    if len(first) > TITLE_MAX_CHARS or len(first.split()) < TITLE_MIN_WORDS:
        return ""
    return first


def validate_metadata(
    raw: Optional[dict], subject: str
) -> Tuple[str, str, List[str]]:
    """Normalize LLM metadata to YouTube limits. Pure function."""
    data = raw if isinstance(raw, dict) else {}
    fallback = _capitalized(subject)

    title_value = data.get("title")
    title = title_value if isinstance(title_value, str) else ""
    title = re.sub(r'[*#"<>]', "", title).strip()
    title = title.splitlines()[0].strip() if title else ""
    title = re.sub(r"\s+", " ", title)
    title = _truncate_at_word(title, TITLE_MAX_CHARS)
    if len(title) < 3:
        title = _truncate_at_word(fallback, TITLE_MAX_CHARS)

    description_value = data.get("description")
    description = description_value.strip() if isinstance(description_value, str) else ""
    description = re.sub(r"[<>]", "", description)
    description = description[:DESCRIPTION_MAX_CHARS]
    if not description:
        description = fallback

    tags: List[str] = []
    seen: set[str] = set()
    total_length = 0
    tags_value = data.get("tags")
    if isinstance(tags_value, list):
        for item in tags_value:
            if not isinstance(item, str):
                continue
            # Models write "blue-sky" and "light_scattering" whatever the
            # prompt says. YouTube accepts them, but a search matches the
            # spaced form, so joined words cost reach for nothing.
            tag = item.replace(",", " ").replace("-", " ").replace("_", " ")
            tag = re.sub(r"\s+", " ", tag).strip()
            if not tag or len(tag) > TAG_MAX_CHARS or tag.lower() in seen:
                continue
            if len(tags) >= TAGS_MAX_COUNT or total_length + len(tag) > TAGS_MAX_TOTAL_CHARS:
                continue
            tags.append(tag)
            seen.add(tag.lower())
            total_length += len(tag)

    return title, description, tags


def keywords_from_subject(
    subject: str, limit: int = SUBJECT_KEYWORD_COUNT
) -> List[str]:
    """Content words of a topic, in order, for when the model returns no tags.

    Pure and deterministic: this is the floor under an Ollama answer that came
    back without usable tags, and a video with four topical keywords is worth
    considerably more than one with none.
    """
    words = re.findall(r"[A-Za-z0-9]+", subject or "")
    keywords: List[str] = []
    seen: set[str] = set()
    for word in words:
        lowered = word.lower()
        if lowered in SUBJECT_STOPWORDS or len(word) < 3 or lowered in seen:
            continue
        if len(word) > TAG_MAX_CHARS:
            continue
        keywords.append(lowered)
        seen.add(lowered)
        if len(keywords) >= limit:
            break
    return keywords


def to_hashtag(text: str) -> str:
    """Turns a tag into a hashtag, or "" when nothing usable is left.

    YouTube hashtags cannot contain spaces or punctuation, so the words are
    stripped to alphanumerics and joined in CamelCase.
    """
    words = re.findall(r"[A-Za-z0-9]+", text or "")
    if not words:
        return ""
    hashtag = "#" + "".join(word[:1].upper() + word[1:] for word in words)
    return hashtag if len(hashtag) <= HASHTAG_MAX_CHARS else ""


def build_hashtags(
    tags: List[str], subject: str, always: tuple = ALWAYS_HASHTAGS
) -> List[str]:
    """Hashtags for a video. Never empty: ALWAYS_HASHTAGS is the floor.

    Derived from the tags rather than requested from the model, so a video
    cannot end up without them because one generation forgot.
    """
    hashtags: List[str] = []
    seen: set[str] = set()

    for candidate in list(always) + [to_hashtag(tag) for tag in tags]:
        if not candidate or candidate.lower() in seen:
            continue
        hashtags.append(candidate)
        seen.add(candidate.lower())
        if len(hashtags) >= HASHTAG_MAX_COUNT:
            break

    # Tags can all be unusable (empty, punctuation-only, too long); fall back to
    # the subject so the video still carries something topical.
    if len(hashtags) == len(always):
        # The whole subject reads best and stays the first choice. But autopilot
        # topics are 4-12 word questions, whose CamelCase form usually blows past
        # HASHTAG_MAX_CHARS, and to_hashtag then returns "" — a silent nothing
        # that left long-form videos with no hashtags at all. Content words are
        # the fallback under the fallback.
        from_subject = to_hashtag(subject)
        candidates = (
            [from_subject]
            if from_subject
            else [
                to_hashtag(word)
                for word in keywords_from_subject(subject, SUBJECT_HASHTAG_COUNT)
            ]
        )
        for candidate in candidates:
            if not candidate or candidate.lower() in seen:
                continue
            hashtags.append(candidate)
            seen.add(candidate.lower())
            if len(hashtags) >= HASHTAG_MAX_COUNT:
                break

    return hashtags


def append_hashtags(description: str, hashtags: List[str]) -> str:
    """Puts the hashtags on their own line, inside the description limit."""
    if not hashtags:
        return description
    block = " ".join(hashtags)
    body = description.rstrip()
    room = DESCRIPTION_MAX_CHARS - len(block) - 2
    if room < 0:
        return block[:DESCRIPTION_MAX_CHARS]
    return f"{body[:room]}\n\n{block}"


# How much of the script the metadata model needs to see. Five keywords and two
# sentences of summary do not need eight hundred words of context, and asking
# for them with that much cost the tags outright: both long videos published on
# 2026-09-14 came back with a valid title and description and no tags at all,
# while every Short that day — same prompt, ninety words — got five to seven.
# Cut on sentence boundaries, so the model never reads a half-sentence.
METADATA_SCRIPT_WORDS = 200


def metadata_excerpt(script: str) -> str:
    """As much of the script as the metadata model should read.

    Cut on a sentence boundary where there is one, and hard at the word count
    where there is not: trim_to_words never drops a script's first sentence, so
    a script written as one very long sentence would otherwise arrive whole.
    """
    excerpt = trim_to_words(
        script or "", METADATA_SCRIPT_WORDS, ceiling=METADATA_SCRIPT_WORDS
    )
    words = excerpt.split()
    if len(words) <= METADATA_SCRIPT_WORDS:
        return excerpt
    return " ".join(words[:METADATA_SCRIPT_WORDS])


def generate_metadata(
    video_subject: str,
    script: str,
    ai_model: str,
    always_hashtags: tuple = ALWAYS_HASHTAGS,
    format_label: str = "short vertical YouTube video (YouTube Shorts)",
    opening_title: bool = False,
) -> Tuple[str, str, List[str]]:
    """
    Generate YouTube title, description and tags with a single JSON request.

    Returns:
        Tuple[str, str, List[str]]: validated (title, description, tags).
    """
    prompt = f"""
    You write metadata for a {format_label}.

    Subject: {video_subject}

    Script:
    {metadata_excerpt(script)}

    Return ONLY a JSON object with exactly these keys:
    {{"title": "...", "description": "...", "tags": ["...", "..."]}}

    Rules:
    - title: catchy, at most 70 characters, plain text, no hashtags, no quotes, no emojis.
    - description: 2-3 sentences that summarize the video, plain text, no hashtags (they are appended automatically).
    - tags: 5 to 10 short keywords, each 1-3 ordinary words separated by
      spaces. No hyphens, no underscores, no commas inside a tag.
      Write "blue sky", not "blue-sky".
    - Do not add any text before or after the JSON object.
    """

    response = generate_response(prompt, ai_model)

    raw = extract_json_object(response)
    if raw is None:
        log("[*] Metadata response was not valid JSON. Using subject as fallback.", "warning")
        log(response[:500], "info")

    title, description, tags = validate_metadata(raw, video_subject)
    if opening_title:
        # Set by the caller for formats whose first sentence is the hook.
        opening = title_from_opening(script)
        if opening:
            title = opening
    if not tags:
        # snippet.tags goes up empty otherwise. Shorts hid this behind the
        # "#Shorts" floor in the description; long form, which forces no
        # hashtags, published with neither tags nor hashtags.
        tags = keywords_from_subject(video_subject)
        log(
            f"[!] Model returned no usable tags; derived {len(tags)} from the subject.",
            "warning",
        )
        # The JSON parsed, or the branch above would have said so, which leaves
        # a missing key or a tags value that is not a list. Neither was visible
        # anywhere, so two long videos shipped on subject-derived hashtags
        # before the pattern was noticed at all.
        log(f"    Raw metadata response: {(response or '')[:500]}", "info")
    hashtags = build_hashtags(tags, video_subject, always_hashtags)
    description = append_hashtags(description, hashtags)
    log(
        f"[+] Metadata: title='{title}', {len(tags)} tags, "
        f"hashtags {' '.join(hashtags)}",
        "success",
    )
    return title, description, tags


def select_music_mood(
    video_subject: str, script: str, ai_model: str
) -> Optional[str]:
    """
    Picks a background-music mood for a script.

    Best effort by design: the mood only decides which Songs/ subfolder is
    preferred, so an Ollama outage or a malformed answer must not fail a job
    that is otherwise finished. Returns None and lets the caller fall back to
    the flat Songs/ folder.

    Args:
        video_subject (str): The subject of the video.
        script (str): The generated script.
        ai_model (str): The AI model to use for generation.

    Returns:
        Optional[str]: One of MUSIC_MOODS, or None if no usable answer.
    """
    prompt = f"""
    Pick the background music mood for a short vertical video.

    Subject: {video_subject}

    Script:
    {script}

    Choose exactly one of: {", ".join(MUSIC_MOODS)}.

    Return ONLY a JSON object: {{"mood": "..."}}
    """

    try:
        response = generate_response(prompt, ai_model)
    except Exception as err:
        log(f"[!] Could not pick a music mood: {err}", "warning")
        return None

    parsed = extract_json_object(response)
    mood = parsed.get("mood") if isinstance(parsed, dict) else None
    if isinstance(mood, str) and mood.strip().lower() in MUSIC_MOODS:
        selected = mood.strip().lower()
        log(f"[+] Music mood: {selected}", "info")
        return selected

    log(f"[!] Model did not return a known music mood: {response[:120]}", "warning")
    return None


LONG_SECTION_MIN_WORDS = 40


def generate_outline(
    video_subject: str,
    section_count: int,
    ai_model: str,
    angle: Optional[str] = None,
) -> List[str]:
    """
    Asks for the section headings of a longer video.

    Args:
        video_subject (str): The subject of the video.
        section_count (int): How many sections to ask for.
        ai_model (str): The AI model to use for generation.

    Returns:
        List[str]: Headings, at most `section_count`. Empty if none parsed.
    """
    prompt = f"""
    Plan a {section_count}-part explainer video.

    Subject: {video_subject}
    {"Shape the video like so: " + angle if angle else ""}

    Give {section_count} section headings that build on each other, from the
    hook to the closing thought. Each heading is a short phrase, not a sentence.
    Do not number them.

    The sections must not overlap. Each one covers something the others do not,
    so no idea is explained twice across the video.

    Return ONLY a JSON array of strings.
    """

    headings = parse_string_array(generate_response(prompt, ai_model))[:section_count]
    log(f"[+] Outline: {len(headings)} sections", "info")
    return headings


def generate_long_script(
    video_subject: str,
    target_words: int,
    ai_model: str,
    voice: str,
    custom_prompt: str,
    section_count: int = 6,
    angle: Optional[str] = None,
    research: str = "",
    section_research: Optional[Callable[[str], str]] = None,
    register: Optional[str] = None,
    report_model: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """
    Writes a long script one section at a time.

    An 8B model asked for 550 coherent words in one call repeats itself and
    loses the thread. Each section is roughly the length it already handles
    well for Shorts, and every call sees the whole outline so it knows what has
    been covered and what is still coming.

    Args:
        video_subject (str): The subject of the video.
        target_words (int): Total words wanted across all sections.
        ai_model (str): The AI model to use for generation.
        voice (str): Voice id, used to name the language.
        custom_prompt (str): Extra instruction applied to every section.
        section_count (int): How many sections to plan.
        research (str): Numbered source notes used when no per-section
            research is supplied.
        section_research (Optional[Callable[[str], str]]): Given a section
            heading, returns a brief for that section alone. Injected rather
            than imported so this module stays free of the research client.

    Returns:
        Optional[str]: The joined script, or None if nothing usable came back.
    """
    angle = angle or choose_script_angle()
    log(f"[+] Script angle: {angle}", "info")
    outline = generate_outline(video_subject, section_count, ai_model, angle)
    if not outline:
        log("[-] Could not plan the video: no outline returned.", "error")
        return None

    words_per_section = max(LONG_SECTION_MIN_WORDS, target_words // len(outline))
    plan = "\n".join(f"{index}. {heading}" for index, heading in enumerate(outline, 1))
    sections: List[str] = []
    # Which model wrote each section. A script that is part Opus and part Ollama
    # is not an Opus script, so any fallback anywhere is reported as a fallback.
    written_by: List[str] = []

    def note_section_model(name: str) -> None:
        written_by.append(name)

    for index, heading in enumerate(outline, 1):
        # The outline alone was not enough. Every section independently reached
        # for the brief's headline figure, and one published video stated "1
        # trillion odours" in six sections out of eight. A section has to see
        # what was actually written, not just what was planned.
        previous = sections[-1] if sections else ""
        used = figures_used(sections)
        continuity = ""
        if previous:
            continuity = (
                f"\n        The previous section ended like this:\n"
                f"        \"{previous[-400:]}\"\n\n"
                f"        - Open by carrying that thought forward. No summary of it, no\n"
                f"          'in this section', no restating the subject. One sentence that\n"
                f"          follows from the line above, then move on to your own material.\n"
            )
        if used:
            continuity += (
                f"        - These figures have already been said out loud and MUST NOT be\n"
                f"          repeated: {', '.join(sorted(used))}. Find something else to say.\n"
            )

        closing_rules = LONG_ENDING_RULES if index == len(outline) else ""

        section_brief = research
        if section_research is not None:
            section_brief = section_research(heading) or research

        prompt = f"""
        You are writing one section of a spoken video script.

        Subject: {video_subject}
        Language: {voice}

        Full outline:
        {plan}

        Write section {index}: "{heading}".

        Rules:
        - About {words_per_section} words.
        - Continue naturally from the earlier sections; do not recap them.
        - Do not cover what later sections will cover.
        - Plain spoken prose. No heading, no markdown, no stage directions.
        - Do not announce what you are writing. Begin with the narration itself.
        - Do not mention sections, the outline, or this prompt.
{closing_rules}        {continuity}{custom_prompt}
        {register_rules(register)}{research_rules(section_brief)}
        """

        try:
            # Every section reports, not only the first: a run that starts on
            # the stronger model and falls back part way through has still
            # fallen back, and the flag should say so.
            section = clean_script_text(
                write_creative(prompt, ai_model, report_model=note_section_model)
            ).strip()
        except Exception as err:
            # One bad section is worth losing; the video is not.
            log(f"[!] Section {index} failed: {err}", "warning")
            continue

        if section:
            sections.append(section)

    if sections:
        # The same drop the Shorts path does, on the only section that closes.
        # cut_short is false: nothing was trimmed, so a short closing sentence
        # here is the writer's choice and stays.
        landed = drop_weak_ending(sections[-1], LONG_SECTION_MIN_WORDS)
        if landed != sections[-1]:
            dropped = (split_sentences(sections[-1]) or [""])[-1]
            log(f"[*] Dropped a trailing aside from the last section: {dropped[:80]}", "info")
            sections[-1] = landed

    if report_model and written_by:
        strong = writer.model_name()
        weaker = [name for name in written_by if name != strong]
        report_model(weaker[0] if weaker else strong)

    if not sections:
        log("[-] Every section came back empty.", "error")
        return None

    script = "\n\n".join(sections)
    log(
        f"[+] Long script: {len(sections)}/{len(outline)} sections, "
        f"{len(script.split())} words",
        "success",
    )
    return script
