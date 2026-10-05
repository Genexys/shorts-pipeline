"""A video asking why something happens gets the sentences that say why.

The shower-curtain Short of 2026-10-05 read the Wikipedia page whose answer is
that the spray "drives a horizontal vortex. This vortex has a low-pressure zone
in the centre, which sucks the curtain." Neither sentence has a figure, so
neither was stored, and the Short said David Schmidt won an Ig Nobel "for a
partial solution" without ever saying what it was. The BBC page read for the
voice Short gave no facts at all: its explanation says "your skull".
"""

import knowledge
from research import Source

SUBJECT = "Why does a shower curtain billow inward toward you?"
TOPIC = ["shower", "curtain", "pressure", "air"]

# Wikipedia, "Shower-curtain effect", as Firecrawl returned it, cut down.
WIKIPEDIA = """The **shower-curtain effect** in [physics](https://en.wikipedia.org/wiki/Physics "Physics") describes the phenomenon of a [shower curtain](https://en.wikipedia.org/wiki/Shower_curtain "Shower curtain") being blown inward when a shower is running. The problem of identifying the cause of this effect has been featured in _Scientific American_ magazine, with several hypotheses given to explain the phenomenon but no definite conclusion.

### Buoyancy hypothesis

Also called the chimney effect or stack effect, this observes that warm air (from the hot shower) rises out over the shower curtain as cooler air (near the floor) pushes in under the curtain to replace the rising air. However, the shower-curtain effect persists when cold water is used, implying that this is not the sole mechanism.[\\[1\\]](https://en.wikipedia.org/wiki/Shower-curtain_effect#cite_note-straight-1)

### Horizontal vortex hypothesis

A computer simulation of a typical bathroom found that none of the above theories pan out in their analysis, but instead found that the spray from the [shower-head](https://en.wikipedia.org/wiki/Shower_head "Shower head") drives a horizontal [vortex](https://en.wikipedia.org/wiki/Vortex "Vortex"). This vortex has a low-pressure zone in the centre, which sucks the curtain.[\\[1\\]](https://en.wikipedia.org/wiki/Shower-curtain_effect#cite_note-straight-1)

David Schmidt of the University of Massachusetts was awarded the 2001 [Ig Nobel Prize](https://en.wikipedia.org/wiki/Ig_Nobel_Prize "Ig Nobel Prize") in Physics for his partial solution to the question of why shower curtains billow inwards.
"""

VORTEX = "This vortex has a low-pressure zone in the centre, which sucks the curtain."
SPRAY = (
    "A computer simulation of a typical bathroom found that none of the above theories "
    "pan out in their analysis, but instead found that the spray from the shower-head "
    "drives a horizontal vortex."
)
NOT_SOLE = (
    "However, the shower-curtain effect persists when cold water is used, implying "
    "that this is not the sole mechanism."
)

# BBC Future, "Why does your voice sound different on a recording?", cut down.
BBC = (
    "What makes a recording of our voice sound so different... and awful? It’s because "
    "when you speak you hear your own voice in two different ways. Greg Foot explains all.\n"
)
TWO_WAYS = "It’s because when you speak you hear your own voice in two different ways."


def texts(markdown, **kwargs):
    return [text for text, _, _ in knowledge.extract_facts(markdown, **kwargs)]


# -- extraction -----------------------------------------------------------------------


def test_the_mechanism_is_stored_though_it_has_no_figure():
    stored = texts(WIKIPEDIA, topic=TOPIC)
    assert VORTEX in stored
    assert SPRAY in stored


def test_without_topic_words_extraction_is_unchanged():
    stored = texts(WIKIPEDIA)
    assert VORTEX not in stored
    assert SPRAY not in stored


def test_the_limit_on_a_mechanism_is_stored_as_a_hedge():
    # Not an explanation slot: "not the sole mechanism" limits a claim.
    facts = {text: score for text, score, _ in knowledge.extract_facts(WIKIPEDIA)}
    assert facts[NOT_SOLE] >= 3.0


def test_an_explanation_may_speak_to_the_reader():
    assert TWO_WAYS in texts(BBC, topic=["voice", "recording"])
    assert TWO_WAYS not in texts(BBC)


def test_the_page_speaking_of_itself_is_still_dropped():
    page = "Let's see why the curtain moves: because the shower air pushes it inward.\n"
    assert texts(page, topic=TOPIC) == []


def test_a_cause_unrelated_to_the_subject_is_not_stored():
    page = "Heavy traffic on the bridge causes long delays for commuters every morning.\n"
    assert texts(page, topic=TOPIC) == []


def test_an_explanation_is_stored_with_the_floor_score():
    facts = {text: score for text, score, _ in knowledge.extract_facts(WIKIPEDIA, topic=TOPIC)}
    assert facts[VORTEX] == knowledge.EXPLANATION_FACT_SCORE


def test_explanation_places_are_capped_and_fill_with_the_most_topical_first():
    plain = [
        f"Rain on the {side} roof causes the gutter to overflow onto the path below it."
        for side in ("north", "south", "east", "west", "front", "back", "upper", "lower", "inner", "outer")
    ]
    topical = "The rising air causes the shower curtain to swing inward at the bather."
    stored = texts("\n\n".join(plain + [topical]), topic=TOPIC + ["gutter"])
    assert topical in stored
    assert len(stored) == knowledge.EXPLANATION_FACTS_PER_PAGE


# -- which subjects ask why -----------------------------------------------------------


def test_why_and_how_subjects_ask_for_a_mechanism():
    assert knowledge.asks_why(SUBJECT)
    assert knowledge.asks_why("Sputnik: how a beeping sphere revealed Earth's upper atmosphere")
    assert knowledge.asks_why("What makes popcorn pop?")


def test_how_many_and_plain_subjects_do_not():
    assert not knowledge.asks_why("How many bones does a shark have?")
    assert not knowledge.asks_why("A physicist won an Ig Nobel showing beer froth decays exponentially")
    assert not knowledge.asks_why("")


# -- the brief ------------------------------------------------------------------------

SOURCES = [
    Source(
        "Shower-curtain effect - Wikipedia",
        "https://en.wikipedia.org/wiki/Shower-curtain_effect",
        "The shower-curtain effect in physics describes the phenomenon of a shower "
        "curtain being blown inward when a shower is running.",
    ),
    Source(
        "How to Stop a Shower Curtain Blowing In",
        "https://www.westsidebathrooms.co.uk/blog/how-to-stop-a-shower-curtain-blowing-in-when/",
        "The hot air rises as you shower, drawing in cooler air from below. This creates "
        "a pressure imbalance that causes the shower curtain to billow.",
    ),
]


def test_the_brief_of_a_why_video_carries_the_answer(session_factory):
    passages = knowledge.facts_for(
        SOURCES[:1], ["shower", "curtain"], session_factory, lambda url: WIKIPEDIA,
        subject=SUBJECT,
    )
    brief = [passage.text for passage in passages]
    assert VORTEX in brief
    assert SPRAY in brief
    assert NOT_SOLE in brief


def test_a_cause_ranks_higher_when_the_subject_asks_why(session_factory):
    dated = [f"The shower brand was founded in {1900 + n} by a family firm." for n in range(1, 15)]
    page = "\n\n".join(dated + [VORTEX]) + "\n"
    sources = [Source("Showers", "https://example.com/showers", "shower curtain")]
    knowledge.facts_for(sources, ["shower", "curtain"], session_factory, lambda url: page)

    def rank(subject):
        passages = knowledge.facts_for(
            sources, ["shower", "curtain"], session_factory, lambda url: None,
            limit=50, subject=subject,
        )
        return [passage.text for passage in passages].index(VORTEX)

    assert rank(SUBJECT) < rank("Shower curtains of the 1950s")
