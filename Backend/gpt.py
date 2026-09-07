import re
import os
import json
from ollama import Client, ResponseError

from dotenv import load_dotenv
from logstream import log
from typing import Tuple, List, Optional
from utils import ENV_FILE, MUSIC_MOODS

# Load environment variables
load_dotenv(ENV_FILE)

# Set environment variables
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "180"))


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
        try:
            response = client.chat(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                stream=False,
            )
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


def clean_script_text(response: str) -> str:
    """Removes the formatting the model is told not to produce but sometimes does.

    Deliberately does not strip surrounding whitespace: generate_script splits
    on blank lines afterwards, and trimming here would change which paragraphs
    it selects.
    """
    cleaned = (response or "").replace("*", "").replace("#", "")
    cleaned = re.sub(r"\[.*\]", "", cleaned)
    return re.sub(r"\(.*\)", "", cleaned)

def generate_script(
    video_subject: str,
    paragraph_number: int,
    ai_model: str,
    voice: str,
    customPrompt: str,
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
        prompt = customPrompt
    else:
        prompt = """
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

    prompt += f"""
    
    Subject: {video_subject}
    Number of paragraphs: {paragraph_number}
    Language: {voice}

    """

    # Generate script
    response = generate_response(prompt, ai_model)

    log(response, "info")

    # Return the generated script
    if response:
        response = clean_script_text(response)

        # Split the script into paragraphs
        paragraphs = response.split("\n\n")

        # Select the specified number of paragraphs
        selected_paragraphs = paragraphs[:paragraph_number]

        # Join the selected paragraphs into a single string
        final_script = "\n\n".join(selected_paragraphs)

        # Print to console the number of paragraphs used
        log(f"Number of paragraphs used: {len(selected_paragraphs)}", "success")

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

    Each search term should consist of 1-3 words,
    always add the main subject of the video.
    
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
    response = generate_response(prompt, ai_model)
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
# Always present, whatever the model returns.
ALWAYS_HASHTAGS = ("#Shorts",)
MAX_JSON_CANDIDATES = 32


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
            tag = re.sub(r"\s+", " ", item.replace(",", " ")).strip()
            if not tag or len(tag) > TAG_MAX_CHARS or tag.lower() in seen:
                continue
            if len(tags) >= TAGS_MAX_COUNT or total_length + len(tag) > TAGS_MAX_TOTAL_CHARS:
                continue
            tags.append(tag)
            seen.add(tag.lower())
            total_length += len(tag)

    return title, description, tags


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


def build_hashtags(tags: List[str], subject: str) -> List[str]:
    """Hashtags for a video. Never empty: ALWAYS_HASHTAGS is the floor.

    Derived from the tags rather than requested from the model, so a video
    cannot end up without them because one generation forgot.
    """
    hashtags: List[str] = []
    seen: set[str] = set()

    for candidate in list(ALWAYS_HASHTAGS) + [to_hashtag(tag) for tag in tags]:
        if not candidate or candidate.lower() in seen:
            continue
        hashtags.append(candidate)
        seen.add(candidate.lower())
        if len(hashtags) >= HASHTAG_MAX_COUNT:
            break

    # Tags can all be unusable (empty, punctuation-only, too long); fall back to
    # the subject so the video still carries something topical.
    if len(hashtags) == len(ALWAYS_HASHTAGS):
        from_subject = to_hashtag(subject)
        if from_subject and from_subject.lower() not in seen:
            hashtags.append(from_subject)

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


def generate_metadata(
    video_subject: str, script: str, ai_model: str
) -> Tuple[str, str, List[str]]:
    """
    Generate YouTube title, description and tags with a single JSON request.

    Returns:
        Tuple[str, str, List[str]]: validated (title, description, tags).
    """
    prompt = f"""
    You write metadata for a short vertical YouTube video (YouTube Shorts).

    Subject: {video_subject}

    Script:
    {script}

    Return ONLY a JSON object with exactly these keys:
    {{"title": "...", "description": "...", "tags": ["...", "..."]}}

    Rules:
    - title: catchy, at most 70 characters, plain text, no hashtags, no quotes, no emojis.
    - description: 2-3 sentences that summarize the video, plain text, no hashtags (they are appended automatically).
    - tags: 5 to 10 short keywords, each 1-3 words, no commas inside a tag.
    - Do not add any text before or after the JSON object.
    """

    response = generate_response(prompt, ai_model)

    raw = extract_json_object(response)
    if raw is None:
        log("[*] Metadata response was not valid JSON. Using subject as fallback.", "warning")
        log(response[:500], "info")

    title, description, tags = validate_metadata(raw, video_subject)
    hashtags = build_hashtags(tags, video_subject)
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
    video_subject: str, section_count: int, ai_model: str
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

    Returns:
        Optional[str]: The joined script, or None if nothing usable came back.
    """
    outline = generate_outline(video_subject, section_count, ai_model)
    if not outline:
        log("[-] Could not plan the video: no outline returned.", "error")
        return None

    words_per_section = max(LONG_SECTION_MIN_WORDS, target_words // len(outline))
    plan = "\n".join(f"{index}. {heading}" for index, heading in enumerate(outline, 1))
    sections: List[str] = []

    for index, heading in enumerate(outline, 1):
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
        - Do not mention sections, the outline, or this prompt.
        {custom_prompt}
        """

        try:
            section = clean_script_text(generate_response(prompt, ai_model)).strip()
        except Exception as err:
            # One bad section is worth losing; the video is not.
            log(f"[!] Section {index} failed: {err}", "warning")
            continue

        if section:
            sections.append(section)

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
