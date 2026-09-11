"""Events that happened on today's date, as grounded topic material.

Not a distribution play. This channel takes 96% of its views from the Shorts
feed and 2.5% from search, so an anniversary buys no reach — and on a round
date the search results belong to channels orders of magnitude larger anyway.

It is a cure for a different problem. The topic generator invents premises the
sources cannot support: "mushrooms have built-in umbrellas", "a jellyfish that
is literally un-killable". A dated event cannot be invented. It has a year, a
place and participants, research finds it immediately, and the script has
something real to stand on.
"""

import re
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

import requests

from logstream import log

FEED_URL = "https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/events"
REQUEST_TIMEOUT_SECONDS = 20
# Wikimedia asks that clients identify themselves.
USER_AGENT = "shorts-pipeline/1.0 (educational shorts; contact via GitHub Genexys)"

# What an "on this day" feed is mostly made of. A science channel narrating an
# air disaster in the same deadpan register it uses for snail mating, over
# music picked from a folder called "curious", would be grotesque. These are
# removed before the model ever sees them, so it cannot pick one.
EXCLUDED_PATTERNS = (
    r"\b(?:kill|killed|killing|dead|deaths?|died|fatal|casualt)",
    r"\b(?:crash|crashes|crashed|collision|collide|derail|sink|sinks|sank|capsiz)",
    r"\b(?:war|wars|battle|invasion|invades?|troops|army|军)",
    r"\b(?:bomb|bombing|bombed|shelling|airstrike|missile)",
    r"\b(?:massacre|genocide|atrocit|execution|executed|hanged)",
    r"\b(?:assassinat|murder|shooting|gunman|stabb)",
    r"\b(?:earthquake|tsunami|hurricane|cyclone|famine|epidemic|outbreak)",
    r"\b(?:collapse|collapses|collapsed|explosion|explodes?|fire destroys)",
    r"\b(?:coup|election|elected|president|parliament|treaty|sanction)",
    r"\b(?:riot|protest|uprising|revolt|rebellion|terroris)",
    r"\b(?:convicted|sentenced|arrested|indicted|impeach)",
    r"\b(?:hijack|kidnap|hostage|siege)",
    r"\b(?:conflict|ceasefire|occupation|apartheid|insurgen|militia)",
    r"\b(?:typhoon|cyclone|flood|wildfire|eruption destroys|landslide)",
    r"\b(?:stolen|theft|robbery|fraud|scandal|guilty|lawsuit|court)",
    r"\b(?:captured|surrender|prisoner|refugee|evacuat)",
    r"\b(?:march|rally|strike by|demonstration)",
)
_EXCLUDED_RE = re.compile("|".join(EXCLUDED_PATTERNS), re.IGNORECASE)

# What the channel is actually about. Used to order the survivors, not to
# filter them: a whitelist narrow enough to be safe would also throw away the
# odd gem it did not anticipate. Exclusion decides what is unusable; this only
# decides what the model reads first.
TOPICAL_PATTERNS = (
    r"\b(?:discover|discovery|invent|invention|patent|experiment)",
    r"\b(?:scien|physic|chemis|biolog|astronom|geolog|medicine|medical)",
    r"\b(?:NASA|ESA|space|orbit|satellite|probe|telescope|rover|spacecraft)",
    r"\b(?:comput|internet|softw|semiconduct|transistor|robot|algorithm)",
    r"\b(?:first .{0,30}(?:flight|ascent|crossing|transmission|broadcast|call))",
    r"\b(?:expedition|summit|dive|voyage|mapped|measured|observed)",
    r"\b(?:vaccine|antibiotic|dna|genome|surgery|anaesth)",
    r"\b(?:engine|railway|bridge|tunnel|canal|electric|radio|telephone)",
    r"\b(?:species|fossil|dinosaur|specimen|botanic|zoolog)",
)
_TOPICAL_RE = re.compile("|".join(TOPICAL_PATTERNS), re.IGNORECASE)

# How many survivors to offer the model. Enough choice to find something that
# fits the niche, few enough that the prompt stays readable.
MAX_EVENTS = 20


@dataclass(frozen=True)
class Event:
    year: int
    text: str

    def render(self) -> str:
        return f"{self.year}: {self.text}"


def is_suitable(text: str) -> bool:
    """Whether an event belongs on a channel about how the world works."""
    return not _EXCLUDED_RE.search(text or "")


def looks_topical(text: str) -> bool:
    """Whether an event reads as science, technology, nature or how-we-found-out."""
    return bool(_TOPICAL_RE.search(text or ""))


def fetch_events(day: Optional[date] = None) -> List[Event]:
    """Events recorded for this calendar day, with the grim ones removed.

    Never raises: a failed lookup returns nothing and the caller picks a
    different kind of topic.
    """
    day = day or date.today()
    try:
        response = requests.get(
            f"{FEED_URL}/{day.month:02d}/{day.day:02d}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as err:
        log(f"[!] Could not read events for {day} ({err}).", "warning")
        return []

    raw = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        # A string here would iterate character by character and raise on the
        # first .get; an unexpected shape should read as "no events".
        return []

    usable: List[Event] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        year = item.get("year")
        if not text or not isinstance(year, int) or not is_suitable(text):
            continue
        usable.append(Event(year=year, text=text))

    # Topical first, then the rest. The feed is ordered newest-first, so a
    # straight truncation would keep this decade's politics and cut the
    # nineteenth-century discovery that is the whole point.
    usable.sort(key=lambda event: not looks_topical(event.text))
    return usable[:MAX_EVENTS]


def format_events(events: List[Event]) -> str:
    """The surviving events as prompt text. Empty string when there are none."""
    return "\n".join(f"- {event.render()}" for event in events)
