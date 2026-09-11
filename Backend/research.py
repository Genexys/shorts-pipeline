"""Web research that grounds a script in sources it can cite.

The point is not caution, it is credibility. A script that says "a 2019
Stanford study found 47%" is worth more than one that says "some research
suggests" — but only if the number is real and a viewer who checks finds it.
So the specifics come from search results, and the sources go in the video
description for anyone who wants to verify them.

Never fatal. Research is an improvement to a video, not a precondition for
one: no key, a timeout, a bad response and a rate limit all produce an empty
brief and a script written the old way.
"""

import os
from dataclasses import dataclass
from typing import List, Optional, Sequence
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from logstream import log

SEARCH_URL = "https://api.firecrawl.dev/v2/search"
REQUEST_TIMEOUT_SECONDS = 30

# Search bills 2 credits per 10 results, and the free tier is 1000 a month. At
# three videos a day one query each, that is under 200 credits a month.
DEFAULT_RESULT_COUNT = 8

# How many results to ask the API for, regardless of how many are wanted back.
# Filtering removes most of them — a search for "mushrooms have built-in
# umbrellas" returned eight results of which six were Facebook, Reddit,
# YouTube, Instagram and Pinterest, leaving two. Asking for ten and keeping
# what survives costs the same two credits as asking for five, so `limit` means
# usable sources wanted, not results requested.
SEARCH_PAGE_SIZE = 10

# Below this, a script is better off written with no specifics at all. One
# thin source is worse than none: it is enough to make the model feel grounded
# and not enough to hold it up, which is how a video came to explain mushroom
# "umbrellas" as waxy filaments that reflect sunlight — a mechanism its single
# source, one sentence about spores spreading in damp air, never mentioned.
MIN_USABLE_SOURCES = 2

# Places where absurd-but-true material is dense enough to find by searching.
# Better than asking a model to invent one: an invented curio is either an
# overstatement it will later defend by fabricating, or a fact it half-recalls.
# These return real, checkable episodes, and the model only has to pick.
CURIO_SEEDS = (
    "Ig Nobel prize winning research",
    "bizarre animal defence mechanisms biologists documented",
    "strangest experiments in the history of science",
    "medieval medical treatments that were actually used",
    "absurd military projects that were genuinely built",
    "surprising failures of famous inventions",
    "unusual legal cases and laws that really existed",
    "strange behaviours animals use to find a mate",
    "historical misunderstandings that changed everything",
    "engineering mistakes with unexpected consequences",
)


def curio_seed(rng=None) -> str:
    """One search query likely to turn up something absurd and real."""
    import random as _random

    return (rng or _random).choice(list(CURIO_SEEDS))
SNIPPET_MAX_CHARS = 500
BRIEF_MAX_SOURCES = 12
# Sources are listed in the description, which YouTube caps; and a wall of
# links reads as spam whatever the cap allows.
DESCRIPTION_MAX_SOURCES = 5

# Dropped before they reach either the brief or the description. Not snobbery
# about the web: a Facebook group post listed under "Sources:" costs exactly
# the credibility the citation was there to buy, and as grounding these return
# somebody's comment rather than a finding. Observed on the first live search,
# which returned Reddit, Facebook and YouTube among its eight results.
EXCLUDED_DOMAINS = frozenset(
    {
        "facebook.com",
        "instagram.com",
        "pinterest.com",
        "quora.com",
        "reddit.com",
        "threads.net",
        "tiktok.com",
        "twitter.com",
        "x.com",
        "youtube.com",
        "youtu.be",
    }
)


@dataclass(frozen=True)
class Source:
    """One search result, reduced to what a script can be written from."""

    title: str
    url: str
    snippet: str


def api_key() -> str:
    return os.getenv("FIRECRAWL_API_KEY", "").strip()


def is_configured() -> bool:
    return bool(api_key())


# Tracking parameters, stripped before a URL is shown or compared. They make a
# cited link look like an affiliate link, and two URLs differing only by one
# would otherwise survive deduplication as separate sources.
TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "msclkid",
        "srsltid",
        "yclid",
    }
)


def clean_url(url: str) -> str:
    """Drops tracking parameters, keeping everything the page actually needs."""
    try:
        parts = urlparse(url)
    except ValueError:
        return url
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS and not key.lower().startswith("utm_")
    ]
    return urlunparse(parts._replace(query=urlencode(kept)))


def host_of(url: str) -> str:
    """Lowercased hostname without a leading www., or "" if unparseable."""
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def is_allowed(url: str) -> bool:
    """Whether a result may be used as a source."""
    host = host_of(url)
    if not host:
        return False
    return not any(
        host == domain or host.endswith(f".{domain}") for domain in EXCLUDED_DOMAINS
    )


def _to_source(item: object) -> Optional[Source]:
    if not isinstance(item, dict):
        return None
    url = item.get("url")
    if not isinstance(url, str) or not url.startswith("http"):
        return None
    if not is_allowed(url):
        return None
    url = clean_url(url)
    title = item.get("title") if isinstance(item.get("title"), str) else ""
    # `description` is what a plain search returns; `markdown` appears only when
    # scraping was requested, and is far longer than a brief should carry.
    body = item.get("description") or item.get("markdown") or ""
    snippet = " ".join(str(body).split())[:SNIPPET_MAX_CHARS]
    if not snippet:
        return None
    return Source(title=" ".join(title.split()), url=url, snippet=snippet)


def search(query: str, limit: int = DEFAULT_RESULT_COUNT) -> List[Source]:
    """One Firecrawl search, returning up to `limit` usable sources.

    `limit` is what survives filtering, not what the API is asked for: see
    SEARCH_PAGE_SIZE. Returns [] on any failure, and says why.
    """
    key = api_key()
    if not key:
        return []
    try:
        response = requests.post(
            SEARCH_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={"query": query, "limit": max(limit, SEARCH_PAGE_SIZE)},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as err:
        # Deliberately broad: a network error, a rate limit, an exhausted
        # credit balance and malformed JSON all mean the same thing here.
        log(f"[!] Research search failed ({err}). Writing without sources.", "warning")
        return []

    web = (payload.get("data") or {}).get("web") if isinstance(payload, dict) else None
    if not isinstance(web, list):
        return []
    sources = [source for source in (_to_source(item) for item in web) if source]
    if len(web) and not sources:
        log(
            f"[!] All {len(web)} results for '{query[:60]}' were social or "
            "unusable. The script will have nothing specific to stand on.",
            "warning",
        )
    return sources[:limit]


def gather(queries: Sequence[str], limit: int = DEFAULT_RESULT_COUNT) -> List[Source]:
    """Searches each query and merges the results, first occurrence winning."""
    collected: List[Source] = []
    seen: set = set()
    for query in queries:
        if not query or not query.strip():
            continue
        for source in search(query.strip(), limit):
            if source.url in seen:
                continue
            collected.append(source)
            seen.add(source.url)
            if len(collected) >= BRIEF_MAX_SOURCES:
                return collected
    return collected


def format_brief(sources: Sequence[Source]) -> str:
    """The sources as prompt text. Empty string when there are none."""
    if not sources:
        return ""
    blocks = [
        f"[{index}] {source.title or source.url}\n{source.snippet}"
        for index, source in enumerate(sources, 1)
    ]
    return "\n\n".join(blocks)


def source_lines(sources: Sequence[Source]) -> List[str]:
    """Lines for the video description, so a viewer can check the claims."""
    lines = []
    for source in sources[:DESCRIPTION_MAX_SOURCES]:
        title = source.title or source.url
        lines.append(f"{title} — {source.url}")
    return lines


def append_sources(description: str, sources: Sequence[Source]) -> str:
    """Puts the sources under the description, after the hashtags."""
    lines = source_lines(sources)
    if not lines:
        return description
    return "\n".join([description, "", "Sources:", *lines])
