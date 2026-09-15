"""A knowledge base of facts quoted verbatim from the pages research finds.

Search results reach the script as a sentence or two per page, and that
sentence is rarely the page's best. A Short about the smell of rain had the
Wikipedia article on petrichor among its five sources and never learned the one
thing that makes the subject remarkable, because the snippet it was handed was
a line about Streptomyces. The writer followed its rules exactly: it may not
state a figure the notes do not contain, and the notes did not contain it.

So the best of each video's sources are read in full, and the sentences on them
that carry something specific — a figure, a comparison, a first or a record —
are stored and handed to the writer alongside the snippets.

Two properties matter more than cleverness:

- Verbatim. Every fact is a sentence that appears word for word on the page it
  cites. Nothing is summarised or rephrased by a model, so nothing can be
  invented on the way in; the writer does the rephrasing, under the same
  research rules as before.
- Read once. A page is fetched the first time a video cites it and never again.
  Reference pages recur across related subjects, and each fetch is a credit.

Never fatal, like research itself: no key, a failed fetch or a database error
all mean a brief without facts, not a failed video.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence
from urllib.parse import urlparse

import requests

import research
from logstream import log

SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"
REQUEST_TIMEOUT_SECONDS = 60

# Pages read per video. One credit each, and a stored page costs nothing the
# second time. Two because the best source is not always the first result, and
# more would mostly add near-duplicate sentences from pages saying the same.
PAGES_PER_VIDEO = 2

# Facts kept per page, and offered to the writer per video. The writer is the
# stronger model and chooses; this only has to put the good ones in reach
# without burying them.
FACTS_PER_PAGE = 25
FACTS_IN_BRIEF = 12

# A sentence shorter than this is a heading or a fragment; longer is usually two
# sentences the splitter could not separate, or a reference-list entry.
FACT_MIN_WORDS = 8
FACT_MAX_WORDS = 60

# Pages worth reading first when a video has a choice. Encyclopaedic and
# institutional pages are dense with specifics and rarely wrong about them.
REFERENCE_HOSTS = ("wikipedia.org", "britannica.com", "nasa.gov", "nih.gov", "noaa.gov")
REFERENCE_SUFFIXES = (".gov", ".edu", ".ac.uk")

_NUMBER = re.compile(r"(?<![\w-])\d[\d,.]*(?![\w-])")
_YEAR = re.compile(r"\b(?:1[5-9]|20)\d{2}s?\b")
_SCALE = re.compile(
    r"\b(?:percent|per cent|parts per (?:million|billion|trillion)|million|billion|"
    r"trillion|thousand|hundred|dozen|kilomet(?:re|er)s?|km|miles?|met(?:re|er)s?|"
    r"feet|kilograms?|kg|tonnes?|tons?|degrees?|seconds?|minutes?|hours?|days?|"
    r"weeks?|years?|centuries|decades?|times)\b|%|°",
    re.IGNORECASE,
)
# Not "most", "least", "unique" or "never": on a commercial page they mark
# chatter — "Most people notice a distinctive smell", "this unique and
# mysterious smell" — far more often than a record.
_EXTREME = re.compile(
    r"\b(?:first|only|largest|smallest|oldest|youngest|fastest|slowest|longest|"
    r"shortest|highest|lowest|deepest|rarest|deadliest|strongest|record)\b",
    re.IGNORECASE,
)
# A sentence talking to the reader is the page's voice, not a fact.
_ADDRESS = re.compile(r"\b(?:you|your|you'll|you're|let's|we'll|we're)\b", re.IGNORECASE)
_COMPARE = re.compile(
    r"\b(?:than|as (?:low|high|much|many|little|few|small|large) as|up to|"
    r"at least|at most)\b",
    re.IGNORECASE,
)

# A sentence containing any of these is page furniture or a citation, not a
# fact about the subject.
_BOILERPLATE = re.compile(
    r"https?://|www\.|cookie|subscribe|newsletter|sign up|log in|click|©|"
    r"copyright|all rights reserved|advertis|retrieved|archived from|isbn|doi:|"
    r"\bet al\.|privacy policy|terms of (?:use|service)|this article|"
    r"citation needed|read more|share this",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Passage:
    """One stored fact, with where it came from."""

    text: str
    title: str
    url: str
    score: float


def api_key() -> str:
    return research.api_key()


def is_configured() -> bool:
    return research.is_configured()


# -- reading a page ---------------------------------------------------------------


def fetch_markdown(url: str) -> Optional[str]:
    """The page's main content as markdown, or None. Never raises."""
    key = api_key()
    if not key:
        return None
    try:
        response = requests.post(
            SCRAPE_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as err:
        log(f"[!] Could not read {url[:80]} in full ({err}).", "warning")
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    markdown = data.get("markdown") if isinstance(data, dict) else None
    return markdown if isinstance(markdown, str) and markdown.strip() else None


# Where a page stops being about its subject. Everything from the first of
# these headings on is citations, link lists and navigation; on the Wikipedia
# article on petrichor it was the source of every false "fact" the first
# version of this extractor kept.
_END_OF_CONTENT = re.compile(
    r"^#{1,6}\s*(?:see also|references|citations|notes|footnotes|sources|"
    r"bibliography|further reading|external links|related (?:articles|posts|stories))\b",
    re.IGNORECASE | re.MULTILINE,
)
# A link target, allowing one level of parentheses inside it:
# (https://en.wikipedia.org/wiki/Doi_(identifier) "Doi (identifier)")
_TARGET = r"\((?:[^()]|\([^()]*\))*\)"


def clean_markdown(markdown: str) -> List[str]:
    """The page as plain-text paragraphs: no links, images, citations or tables."""
    text = markdown or ""
    end = _END_OF_CONTENT.search(text)
    if end:
        text = text[: end.start()]
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    # Markdown escapes brackets and dots it does not want read as syntax.
    text = re.sub(r"\\([\[\]().*_#+\-!])", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]" + _TARGET, " ", text)                  # images
    text = re.sub(r"\[\s*\]" + _TARGET, " ", text)                      # emptied links
    text = re.sub(r"\[\[\d+\]\]" + _TARGET, "", text)                   # [[14]](…#cite)
    text = re.sub(r"\[([^\[\]]+)\]" + _TARGET, r"\1", text)             # [text](url)
    # A bracketed letter inside a word is an editorial change and stays; one on
    # its own is a footnote marker and goes. Wikipedia quotes "[t]he smell", and
    # removing that bracket with its letter left "he smell".
    text = re.sub(r"\[([A-Za-z])\](?=[a-z])", r"\1", text)
    text = re.sub(r"\[\s*(?:\d+|[a-z]|note \d+)\s*\]", "", text)
    # Maintenance tags and interlanguage links: "[ according to whom?]",
    # "[ non-primary source needed]", "[ fr]". Short and all lower case.
    text = re.sub(r"\[[\s_]*(?:[a-z?'-]+[\s_]*){1,5}\]", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    paragraphs = []
    for block in re.split(r"\n\s*\n", text):
        lines = []
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "|", ">")):
                continue
            stripped = re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", stripped)
            lines.append(stripped)
        paragraph = " ".join(lines)
        paragraph = re.sub(r"(?<!\w)[*_]{1,3}|[*_]{1,3}(?!\w)", "", paragraph)
        paragraph = " ".join(paragraph.split())
        if paragraph:
            paragraphs.append(paragraph)
    return paragraphs


def sentences(paragraphs: Sequence[str]) -> List[str]:
    parts: List[str] = []
    for paragraph in paragraphs:
        parts.extend(
            piece.strip()
            for piece in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"“])", paragraph)
            if piece.strip()
        )
    return parts


def specificity(sentence: str) -> float:
    """How much a sentence says that a summary would not.

    Zero means not a fact worth storing: nothing in it is a figure, a
    comparison or an extreme, however true it may be.
    """
    # A year on its own dates a claim; a quantity is the claim. "Research in the
    # 2020s has indicated" should not outrank "some 50 chemicals".
    quantities = _NUMBER.search(_YEAR.sub("", sentence))
    has_year = bool(_YEAR.search(sentence))
    has_number = bool(quantities) or has_year
    has_extreme = bool(_EXTREME.search(sentence))
    has_compare = bool(_COMPARE.search(sentence))
    if not (has_number or has_extreme or has_compare):
        return 0.0
    return (
        (2.0 if quantities else 1.0 if has_year else 0.0)
        # A unit only counts next to a quantity: "to some degree" is not one.
        + 1.0 * bool(quantities and _SCALE.search(sentence))
        + 1.0 * has_extreme
        + 1.0 * has_compare
    )


def looks_like_prose(sentence: str) -> bool:
    words = sentence.split()
    if not FACT_MIN_WORDS <= len(words) <= FACT_MAX_WORDS:
        return False
    if not sentence[0].isupper() and not sentence[0].isdigit() and sentence[0] not in "\"“":
        return False
    if sentence[-1] not in ".!?\"”":
        return False
    if _BOILERPLATE.search(sentence) or _ADDRESS.search(sentence):
        return False
    # A run of capitalised words is a title, a reference or a list of names.
    capitalised = sum(1 for word in words[1:] if word[:1].isupper())
    return capitalised <= len(words) * 0.5


def digest(sentence: str) -> str:
    normalised = " ".join(sentence.lower().split())
    return hashlib.sha1(normalised.encode("utf-8")).hexdigest()


def extract_facts(markdown: str, limit: int = FACTS_PER_PAGE) -> List[tuple]:
    """The page's most specific sentences as (text, score, digest), in page order."""
    seen = set()
    scored = []
    for position, sentence in enumerate(sentences(clean_markdown(markdown))):
        if not looks_like_prose(sentence):
            continue
        score = specificity(sentence)
        key = digest(sentence)
        if score <= 0 or key in seen:
            continue
        seen.add(key)
        scored.append((position, sentence, score, key))
    best = sorted(scored, key=lambda item: (-item[2], item[0]))[:limit]
    return [(text, score, key) for _, text, score, key in sorted(best)]


# -- choosing and remembering pages -----------------------------------------------


def is_pdf(url: str) -> bool:
    # Skipped rather than parsed: a PDF bills a credit per page, and the ones
    # search returns are usually posters and infographics.
    try:
        return urlparse(url).path.lower().endswith(".pdf")
    except ValueError:
        return True


def is_reference(url: str) -> bool:
    host = research.host_of(url)
    return any(host == h or host.endswith(f".{h}") for h in REFERENCE_HOSTS) or host.endswith(
        REFERENCE_SUFFIXES
    )


def choose_pages(sources: Sequence["research.Source"], count: int = PAGES_PER_VIDEO) -> list:
    """The sources worth reading in full: reference pages first, then search order."""
    candidates = [source for source in sources if not is_pdf(source.url)]
    ranked = sorted(
        enumerate(candidates), key=lambda item: (not is_reference(item[1].url), item[0])
    )
    return [source for _, source in ranked[:count]]


def relevance(text: str, keywords: Sequence[str]) -> float:
    lowered = text.lower()
    return sum(1.0 for keyword in keywords if keyword and keyword.lower() in lowered)


def facts_for(
    sources: Sequence["research.Source"],
    keywords: Sequence[str],
    session_factory: Optional[Callable] = None,
    fetch: Callable[[str], Optional[str]] = fetch_markdown,
    pages: int = PAGES_PER_VIDEO,
    limit: int = FACTS_IN_BRIEF,
) -> List[Passage]:
    """Facts from the best of `sources`, reading each page at most once, ever.

    Ranked for this video by specificity plus how many of the subject's words a
    fact mentions, since a stored page's facts were scored before any subject
    was known. Returns [] rather than raising on any failure.
    """
    if not sources:
        return []
    try:
        from repository import add_knowledge_page, get_knowledge_page, get_page_facts

        if session_factory is None:
            from db import SessionLocal as session_factory

        collected: List[Passage] = []
        with session_factory() as session:
            for source in choose_pages(sources, pages):
                page = get_knowledge_page(session, source.url)
                if page is None:
                    markdown = fetch(source.url)
                    if markdown is None:
                        # Not recorded: the failure may be transient, and the
                        # next video to cite this page should try again.
                        continue
                    page = add_knowledge_page(
                        session, source.url, source.title, extract_facts(markdown)
                    )
                    log(f"[+] Read {source.url[:80]} in full.", "info")
                for fact in get_page_facts(session, page.id):
                    collected.append(
                        Passage(
                            text=fact.text,
                            title=page.title or source.title,
                            url=page.url,
                            score=fact.score,
                        )
                    )
    except Exception as err:
        log(f"[!] Knowledge base unavailable ({err}). Using snippets only.", "warning")
        return []

    ranked = sorted(
        collected, key=lambda passage: -(passage.score + relevance(passage.text, keywords))
    )
    return ranked[:limit]


def format_facts(passages: Sequence[Passage]) -> str:
    """The facts as a block for the research brief. Empty string when none."""
    if not passages:
        return ""
    lines = ["Passages quoted word for word from those pages:"]
    lines.extend(f"- {passage.text} ({passage.title or passage.url})" for passage in passages)
    return "\n".join(lines)
