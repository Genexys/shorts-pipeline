"""The sentence that delivers what the topic line promises reaches the writer.

The Sputnik Short of 2026-10-04 promised "how a beeping sphere revealed Earth's
upper atmosphere". The Wikipedia page it read says so in one sentence, but that
sentence has no figure in it, so it was never stored. The brief was filtered
by the anniversary's event line, which never mentions the atmosphere either.
"""

import knowledge
from research import Source

SUBJECT = "Sputnik: how a beeping sphere revealed Earth's upper atmosphere"
# keywords_from_subject() of the anniversary's event line, which is what the
# pipeline filters the brief by.
EVENT_KEYWORDS = ["sputnik", "becomes", "first", "artificial", "satellite"]

ATMOSPHERE = (
    "The density of the upper atmosphere could be deduced from its drag on the "
    "orbit, and the propagation of its radio signals gave data about the ionosphere."
)

# The snippets the job had, cut down.
SOURCES = [
    Source(
        "Sputnik 1 - Wikipedia",
        "https://en.wikipedia.org/wiki/Sputnik_1",
        "Sputnik 1 was the first artificial Earth satellite. It was launched into "
        "an elliptical low Earth orbit by the Soviet Union on 4 October 1957.",
    ),
    Source(
        "Dawn of the Space Age - NASA",
        "https://www.nasa.gov/history/dawn-of-the-space-age/",
        "The course of history changed on October 4, 1957, when the Soviet Union "
        "successfully launched Sputnik 1.",
    ),
    Source(
        "What was Sputnik?",
        "https://airandspace.si.edu/stories/editorial/what-was-sputnik",
        "Sputnik, launched by the USSR on October 4, 1957, marked the placement "
        "of the first human-made satellite into Earth orbit.",
    ),
]

MEASUREMENTS = [
    "Sputnik 1 had a mass of 83.6 kilograms and a polished sphere 58 centimetres across.",
    "The satellite travelled at about 8 km/s, taking 96.20 minutes to complete each orbit.",
    "The signals continued for 22 days until the transmitter batteries depleted on 26 October 1957.",
    "Each antenna was made up of two whip-like parts, 2.4 and 2.9 metres in length.",
    "The batteries had an expected lifetime of two weeks, and operated for 22 days.",
    "Signals on the first frequency were transmitted in 0.3 s pulses, near a frequency of 3 Hz.",
    "If the temperature inside the satellite exceeded 36 degrees, the fan was turned on.",
    "The initial elliptical orbit was 223 km by 950 km, with an inclination of 65.10 degrees.",
    "The core stage engine shut down 295.4 seconds into the flight of the satellite.",
    "The first launch of an R-7 rocket for the satellite programme occurred on 15 May 1957.",
    "The rocket's length with the satellite attached was 29.167 metres at liftoff.",
    "The thrust of the rocket at liftoff was 3.90 MN, more than any before it.",
    "Its radio signal was easily detectable, and the 65 degree orbit covered most of the Earth.",
    "The satellite was launched from Site No.1/5, at the 5th Tyuratam range, in 1957.",
]

FILLER = [
    "Its launch came as a shock to the public and prompted a long period of rivalry.",
    "The satellite was a polished metal ball with four external radio antennas attached.",
]


def _page(sentences):
    return "\n\n".join(sentences) + "\n"


SPUTNIK_PAGE = _page(MEASUREMENTS[:7] + [ATMOSPHERE] + FILLER + MEASUREMENTS[7:])


def _numbered(count):
    return [
        f"Probe number {n} weighed {n * 3} kilograms when it was measured in the lab."
        for n in range(1, count + 1)
    ]


# -- what the topic line promises ---------------------------------------------------


def test_promise_words_are_what_only_the_topic_line_says():
    promise = knowledge.promise_words(SUBJECT, SOURCES)
    assert {"upper", "atmosphere", "beeping", "sphere"} <= promise
    # In every snippet, so every page on the topic is about them.
    assert "sputnik" not in promise
    assert "earth" not in promise
    # Salesmanship, not subject matter.
    assert "revealed" not in promise


def test_no_topic_line_promises_nothing():
    assert knowledge.promise_words("", SOURCES) == set()


# -- storing the sentence that delivers it -------------------------------------------


def test_a_sentence_with_no_figure_is_stored_when_it_delivers_the_promise():
    promise = sorted(knowledge.promise_words(SUBJECT, SOURCES))
    texts = [text for text, _, _ in knowledge.extract_facts(SPUTNIK_PAGE, promise=promise)]
    assert ATMOSPHERE in texts


def test_without_a_promise_extraction_is_unchanged():
    texts = [text for text, _, _ in knowledge.extract_facts(SPUTNIK_PAGE)]
    assert ATMOSPHERE not in texts
    assert not any("polished metal ball" in text for text in texts)


def test_a_promised_sentence_is_stored_with_at_least_the_floor_score():
    facts = knowledge.extract_facts(SPUTNIK_PAGE, promise=["upper", "atmosphere"])
    score = next(score for text, score, _ in facts if text == ATMOSPHERE)
    assert score == knowledge.PROMISE_FACT_SCORE


def test_promised_places_come_on_top_of_the_specific_ones():
    promising = [
        f"Witness {word} described the upper atmosphere glowing over the town square."
        for word in ("Anna", "Boris", "Clara", "Dmitri", "Elena", "Fyodor", "Galina")
    ]
    page = _page(_numbered(30) + promising)
    facts = knowledge.extract_facts(page, promise=["upper", "atmosphere"])
    texts = [text for text, _, _ in facts]
    assert sum(text.startswith("Probe number") for text in texts) == knowledge.FACTS_PER_PAGE
    assert sum(text.startswith("Witness") for text in texts) == knowledge.PROMISE_FACTS_PER_PAGE


def test_the_sentences_using_most_of_the_promise_are_kept_first():
    one_word = [
        f"Observer {name} watched the atmosphere change colour after the event."
        for name in ("Anna", "Boris", "Clara", "Dmitri", "Elena", "Fyodor")
    ]
    page = _page(one_word + [ATMOSPHERE])
    facts = knowledge.extract_facts(page, promise=["upper", "atmosphere", "ionosphere"])
    texts = [text for text, _, _ in facts]
    assert ATMOSPHERE in texts
    assert len(texts) == knowledge.PROMISE_FACTS_PER_PAGE


def test_page_order_is_kept():
    facts = knowledge.extract_facts(SPUTNIK_PAGE, promise=["upper", "atmosphere"])
    texts = [text for text, _, _ in facts]
    assert texts.index(MEASUREMENTS[6]) < texts.index(ATMOSPHERE) < texts.index(MEASUREMENTS[7])


# -- the brief ------------------------------------------------------------------------


def test_the_brief_offers_the_sentence_the_topic_line_promised(session_factory):
    passages = knowledge.facts_for(
        SOURCES[:1], EVENT_KEYWORDS, session_factory, lambda url: SPUTNIK_PAGE,
        subject=SUBJECT,
    )
    texts = [passage.text for passage in passages]
    assert ATMOSPHERE in texts
    # Not just in reach: among the first few the writer reads.
    assert texts.index(ATMOSPHERE) < 4


def test_the_promise_lifts_a_stored_fact_into_the_brief(session_factory):
    # A page stored before this change, by another video, still has its facts
    # ranked by this video's promise.
    page = _page(_numbered(14) + ["The upper atmosphere was 1,000 times thinner than expected in 1958."])
    sources = [Source("Probes", "https://example.com/probes", "s")]
    knowledge.facts_for(sources, [], session_factory, lambda url: page)
    passages = knowledge.facts_for(
        sources, [], session_factory, lambda url: None, subject=SUBJECT
    )
    assert passages[0].text.startswith("The upper atmosphere")


def test_without_a_topic_line_the_brief_is_as_before(session_factory):
    passages = knowledge.facts_for(
        SOURCES[:1], EVENT_KEYWORDS, session_factory, lambda url: SPUTNIK_PAGE
    )
    assert ATMOSPHERE not in [passage.text for passage in passages]
