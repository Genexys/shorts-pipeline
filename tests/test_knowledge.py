import pytest

import knowledge
from research import Source

# Shaped like what Firecrawl returns for a Wikipedia article: escaped citation
# links, link targets with parentheses inside them, an image, a maintenance tag,
# an editorial bracket, and a references section full of numbers.
WIKI_PAGE = """# Petrichor

[![](https://upload.wikimedia.org/leaf.jpg)](https://en.wikipedia.org/wiki/File:Leaf.jpg) A leaf with droplets

With regard to specific chemicals, the human nose is sensitive, for instance, to [geosmin](https://en.wikipedia.org/wiki/Geosmin "Geosmin"), and can detect it at concentrations as low as 0.4 parts per billion.[\\[18\\]](https://en.wikipedia.org/wiki/Petrichor#cite_note-18)

The authors describe how the scent derives from compounds exuded by plants during dry periods.\\[ _[according to whom?](https://en.wikipedia.org/wiki/Wikipedia:Manual)_\\] In a follow-up paper, Bear and Thomas suggested in 1965 that these compounds also slow seed germination.

A popular summary states that the "\\[t\\]he smell is a concoction of some 50 chemicals" released by rain.

Most people notice a distinctive smell in the air after it rains.

## Citations

18. [↑](https://en.wikipedia.org/wiki/Petrichor#cite_ref-18 "Jump up")Polak, E. H.; Provasi, J. (1992). "Odor Sensitivity to Geosmin Enantiomers". **17** (1): 23–26\\. [doi](https://en.wikipedia.org/wiki/Doi_(identifier) "Doi (identifier)"): 10.1093/chemse/17.1.23.
"""


def _texts(markdown):
    return [text for text, _, _ in knowledge.extract_facts(markdown)]


# -- cleaning ---------------------------------------------------------------------


def test_clean_markdown_unwraps_links_and_drops_citations():
    text = " ".join(knowledge.clean_markdown(WIKI_PAGE))
    assert "to geosmin, and can detect it" in text
    assert "cite_note" not in text and "[18]" not in text
    assert "wikimedia" not in text


def test_clean_markdown_stops_at_the_references():
    # The first version kept "doi "Doi (identifier)")" lines as facts.
    text = " ".join(knowledge.clean_markdown(WIKI_PAGE))
    assert "Polak" not in text
    assert "identifier" not in text


def test_clean_markdown_keeps_an_editorial_letter_and_drops_a_tag():
    text = " ".join(knowledge.clean_markdown(WIKI_PAGE))
    assert '"the smell is' in text
    assert "according to whom" not in text


def test_a_removed_tag_lets_the_sentences_either_side_split():
    sentences = knowledge.sentences(knowledge.clean_markdown(WIKI_PAGE))
    assert any(s.startswith("In a follow-up paper") for s in sentences)


@pytest.mark.parametrize(
    "sentence",
    [
        # Verbatim from en.wikipedia.org/wiki/Royal_touch, which the knowledge
        # base stored on 2026-09-22 as "1702–1714) reintroduced the practice…"
        # and "1285–1314) reportedly instructed his son and heir, Louis X (r.".
        "Anne (r. 1702–1714) reintroduced the practice almost as soon as she acceded.",
        "Philip IV (r. 1285–1314) reportedly instructed his son and heir, Louis X "
        "(r. 1314–1316), to touch the sick.",
        "Charles X (r. 1824–1830) touched 121 of his subjects at his coronation on "
        "29 May 1825.",
        "Marc Bloch (b. 1886, d. 1944) argued that it was a later invention.",
        "The chapel was built c. 1500 by the monks of the abbey.",
        "He was received by Dr. Watson and later moved to St. Petersburg for good.",
        "Some metals, e.g. Gallium, melt in the hand.",
    ],
)
def test_an_abbreviation_does_not_end_the_sentence(sentence):
    assert knowledge.sentences([sentence]) == [sentence]


@pytest.mark.parametrize(
    "paragraph, count",
    [
        # The cases the abbreviation list must not swallow.
        ("Oranges are rich in vitamin C. The body cannot make it.", 2),
        ("He served in World War I. Afterwards he taught.", 2),
        ("The year was 1492. Ninety men sailed with him.", 2),
        ("He moved to St. Petersburg. There he died.", 2),
    ],
)
def test_real_sentence_ends_still_split(paragraph, count):
    assert len(knowledge.sentences([paragraph])) == count


def test_a_reign_keeps_its_monarch_as_a_stored_fact():
    page = (
        "Charles X (r. 1824–1830) touched 121 of his subjects at his coronation on "
        "29 May 1825 in an attempt to assert continuity with the Ancien Régime.\n"
    )
    [fact] = _texts(page)
    assert fact.startswith("Charles X")


# -- who "he" is ------------------------------------------------------------------

# Verbatim from ethw.org/Milestones:First_Blind_Takeoff,_Flight_and_Landing,_1929.
# The last sentence was stored on its own, and on 2026-09-24 a video said twice
# that Doolittle flew with his hands outside the cockpit. It was Kelsey.
KELSEY = (
    "Benjamin Kelsey graduated M.I.T. with a BS in June 1928, and stayed to teach "
    "and conduct research work in the aeronautics department. He flew for "
    "commercial concerns as well as privately obtaining a transport pilot's "
    "license. He joined the United States Army Air Corps and was commissioned a "
    "second lieutenant on May 2, 1929. He was assigned to the Full Flight "
    "Laboratory at Doolittle's request and at Harry Guggenheim's insistence flew "
    "as Doolittle's safety pilot during the NY-2 Husky instrument flights. During "
    "the first 'blind' instrument flight on September 24, 1929, he showed "
    "observers that he was not in control by keeping his hands visible outside "
    "the cockpit.\n"
)


def test_an_unnamed_he_is_stored_with_the_sentence_that_names_him():
    [hands] = [fact for fact in _texts(KELSEY) if "hands visible" in fact]
    assert hands.startswith("Benjamin Kelsey graduated M.I.T.")
    # Three sentences stood between them, and the quote says so.
    assert " … During the first 'blind' instrument flight" in hands


def test_the_pairing_is_scored_on_the_sentence_itself():
    [text, score, _] = next(
        fact for fact in knowledge.extract_facts(KELSEY) if "hands visible" in fact[0]
    )
    alone = text.split(" … ")[-1]
    assert score == knowledge.specificity(alone)


def test_a_neighbouring_antecedent_is_joined_without_an_ellipsis():
    paragraph = [
        "Ninety men sailed with Columbus from Palos in August.",
        "He reached the Bahamas after 33 days at sea, on 12 October.",
    ]
    assert knowledge.with_antecedent(paragraph, 1) == " ".join(paragraph)


@pytest.mark.parametrize(
    "sentence, unnamed",
    [
        ("He touched 35 people between 1 January and 30 June 1337.", True),
        ("Their study registered 12–15 °C differences between stripes.", True),
        ("In 1926, he received a doctorate in aeronautical engineering.", True),
        # A month is capitalised and still nobody.
        ("During the flight on September 24, 1929, he kept his hands up.", True),
        ("When James Doolittle was selected, he was borrowed from the Army.", False),
        ("Kelsey joined the Army Air Corps on May 2, 1929.", False),
        ("The hood kept him from seeing out, and he flew on the dials.", False),
    ],
)
def test_which_sentences_leave_their_subject_unnamed(sentence, unnamed):
    assert knowledge.leaves_subject_unnamed(sentence) is unnamed


def test_an_unnamed_he_that_opens_its_paragraph_is_stored_as_before():
    # Nothing earlier in the paragraph to borrow; the page title still travels
    # with the fact into the brief.
    page = "He touched 35 people between 1 January and 30 June 1337.\n"
    assert _texts(page) == ["He touched 35 people between 1 January and 30 June 1337."]


def test_a_pairing_that_would_run_too_long_is_not_made():
    long_first = "Benjamin Kelsey " + "worked on it " * 30 + "for years."
    paragraph = [long_first, "He flew 3 test flights in 1929 over the field."]
    assert knowledge.with_antecedent(paragraph, 1) == paragraph[1]


# -- choosing facts ---------------------------------------------------------------


def test_extract_facts_puts_the_measured_claim_first():
    facts = knowledge.extract_facts(WIKI_PAGE)
    best = max(facts, key=lambda fact: fact[1])
    assert "0.4 parts per billion" in best[0]


def test_extract_facts_keeps_facts_verbatim():
    for text in _texts(WIKI_PAGE):
        # Every fact is a sentence of the cleaned page, word for word.
        assert text in " ".join(knowledge.clean_markdown(WIKI_PAGE))


def test_extract_facts_drops_chatter():
    texts = _texts(WIKI_PAGE)
    assert not any(text.startswith("Most people notice") for text in texts)


def test_extract_facts_drops_sentences_that_address_the_reader():
    page = "That said, you'll notice it after 3 or 4 rainstorms in a row every year.\n"
    assert _texts(page) == []


def test_extract_facts_stores_a_repeated_sentence_once():
    sentence = "The oldest known specimen is 4,600 years old and still growing today."
    assert len(_texts(f"{sentence}\n\n{sentence}\n")) == 1


def test_specificity_ranks_a_quantity_above_a_year():
    quantity = knowledge.specificity("It can detect the compound at 0.4 parts per billion.")
    year = knowledge.specificity("The term was introduced in a 1964 paper in Nature.")
    nothing = knowledge.specificity("The smell is pleasant to a great many people.")
    assert quantity > year > nothing == 0


def test_a_unit_without_a_number_is_not_a_measurement():
    assert knowledge.specificity("You notice it to some degree after rain.") == 0


# -- choosing pages ---------------------------------------------------------------


def _source(url, title="t"):
    return Source(title=title, url=url, snippet="s")


def test_choose_pages_reads_reference_pages_first_and_skips_pdfs():
    sources = [
        _source("https://science.howstuffworks.com/rain"),
        _source("https://www.acs.org/infographic/petrichor.pdf"),
        _source("https://en.wikipedia.org/wiki/Petrichor"),
        _source("https://example.edu/rain"),
    ]
    chosen = [source.url for source in knowledge.choose_pages(sources, 2)]
    assert chosen == ["https://en.wikipedia.org/wiki/Petrichor", "https://example.edu/rain"]


def test_choose_pages_falls_back_to_search_order():
    sources = [_source("https://a.com/x"), _source("https://b.com/y"), _source("https://c.com/z")]
    assert [s.url for s in knowledge.choose_pages(sources, 2)] == [
        "https://a.com/x",
        "https://b.com/y",
    ]


# -- fetching ---------------------------------------------------------------------


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_fetch_markdown_reads_the_scrape_response(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "key")
    sent = {}

    def post(url, headers, json, timeout):
        sent.update(json)
        return _Response({"success": True, "data": {"markdown": "# Page\n\nBody."}})

    monkeypatch.setattr(knowledge.requests, "post", post)

    assert knowledge.fetch_markdown("https://example.com") == "# Page\n\nBody."
    assert sent["onlyMainContent"] is True
    assert sent["formats"] == ["markdown"]


def test_fetch_markdown_returns_none_on_failure(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "key")

    def post(*args, **kwargs):
        raise RuntimeError("402 Payment Required")

    monkeypatch.setattr(knowledge.requests, "post", post)
    assert knowledge.fetch_markdown("https://example.com") is None


def test_fetch_markdown_needs_a_key(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "")
    assert knowledge.fetch_markdown("https://example.com") is None


# -- the knowledge base -----------------------------------------------------------


def test_a_page_is_read_once_and_then_served_from_the_database(session_factory):
    fetched = []

    def fetch(url):
        fetched.append(url)
        return WIKI_PAGE

    sources = [_source("https://en.wikipedia.org/wiki/Petrichor", "Petrichor")]

    first = knowledge.facts_for(sources, ["rain"], session_factory, fetch)
    second = knowledge.facts_for(sources, ["rain"], session_factory, fetch)

    assert fetched == ["https://en.wikipedia.org/wiki/Petrichor"]
    assert [p.text for p in first] == [p.text for p in second]
    assert first and all(p.title == "Petrichor" for p in first)


def test_a_failed_read_is_not_remembered(session_factory):
    attempts = []

    def fetch(url):
        attempts.append(url)
        return None if len(attempts) == 1 else WIKI_PAGE

    sources = [_source("https://en.wikipedia.org/wiki/Petrichor")]

    assert knowledge.facts_for(sources, [], session_factory, fetch) == []
    # The failure may have been transient; the next video tries again.
    assert knowledge.facts_for(sources, [], session_factory, fetch)
    assert len(attempts) == 2


def test_between_equally_specific_facts_the_one_about_the_subject_ranks_first(
    session_factory,
):
    # Relevance lifts, it does not dominate. On the real petrichor page the best
    # fact ("detect geosmin at 0.4 parts per billion") shares no word with the
    # subject line "smell of rain from bacteria", so a heavy relevance weight
    # would have buried it under weaker sentences that happen to say "rain".
    page = (
        "A bristlecone pine in Nevada was measured at 4,800 years of age.\n\n"
        "Geosmin reaches the human nose from 0.4 parts per billion of air.\n"
    )
    sources = [_source("https://en.wikipedia.org/wiki/Trees")]
    passages = knowledge.facts_for(sources, ["geosmin"], session_factory, lambda u: page)
    assert passages[0].text.startswith("Geosmin")


def test_the_knowledge_base_never_breaks_a_video(monkeypatch):
    def broken_factory():
        raise RuntimeError("database is down")

    sources = [_source("https://en.wikipedia.org/wiki/Petrichor")]
    assert knowledge.facts_for(sources, [], broken_factory, lambda u: WIKI_PAGE) == []


def test_no_sources_means_no_work(session_factory):
    def fetch(url):
        pytest.fail("nothing should be fetched")

    assert knowledge.facts_for([], ["rain"], session_factory, fetch) == []


def test_format_facts_names_where_each_came_from():
    block = knowledge.format_facts(
        [knowledge.Passage("Geosmin is detected at 0.4 ppb.", "Petrichor", "https://w", 4.0)]
    )
    assert block.startswith("Passages quoted word for word")
    assert "- Geosmin is detected at 0.4 ppb. (Petrichor)" in block
    assert knowledge.format_facts([]) == ""


# -- relevance to the subject -------------------------------------------------------
#
# The first live run offered the writer six facts about a horned lizard, four of
# them from elsewhere on the page: an author bio and category blurbs.

LIZARD_SOURCES = [
    Source("Horned lizard", "https://www.discoverwildlife.com/horned-lizard",
           "But their real super-power is their ability to shoot blood from their eyes."),
    Source("Eyes squirt blood", "https://asknature.org/eyes-squirt-blood",
           "## Eyes Squirt Blood ### Reptiles When threatened it can shoot blood from its eyes."),
    Source("Miami Herald", "https://www.miamiherald.com/lizard",
           "When threatened, it can shoot a pressurized stream of blood directly from its eyes."),
]
LIZARD_FACTS = [
    "But their real super-power is their ability to shoot blood from their eyes to a distance of up to nine times their body length.",
    "She has also worked as an ecologist for 20 years, been a field naturalist for much longer, and worked as a journalist.",
    "Animals–organisms that range from microscopic to larger than a bus–embody a wide variety of harms to living systems.",
    "Reptiles retain some of the key characteristics that first enabled vertebrates to live permanently on land.",
    "This means they burn through energy much more slowly than warm blooded creatures of the same size.",
    "The result is a jet stream of blood that can shoot up to four feet from the eye socket, a process known as auto-hemorrhaging.",
]


def _page(sentences):
    return "\n\n".join(sentences) + "\n"


def test_vocabulary_takes_words_two_snippets_share_and_not_one():
    vocabulary = knowledge.topic_vocabulary(LIZARD_SOURCES, [])
    assert {"blood", "eye", "shoot", "threatened"} <= vocabulary
    # "Reptiles" is a category heading in one snippet only.
    assert "reptile" not in vocabulary
    assert "when" not in vocabulary


def test_vocabulary_includes_the_subject_keywords():
    vocabulary = knowledge.topic_vocabulary([], ["horned", "lizards"])
    assert vocabulary == {"horned", "lizard"}


def test_relevance_matches_whole_words_and_plurals():
    assert knowledge.relevance("warm blooded creatures", {"blood"}) == 0
    assert knowledge.relevance("from its eyes", {"eye"}) == 1
    assert knowledge.relevance("shoot blood from the eye", {"blood", "eye", "shoot"}) == (
        knowledge.RELEVANCE_CAP
    )


def test_only_facts_about_the_subject_reach_the_writer(session_factory):
    passages = knowledge.facts_for(
        LIZARD_SOURCES[:1],
        ["texas", "horned", "lizard", "shoots", "blood", "eyes", "predators"],
        session_factory,
        lambda url: _page(LIZARD_FACTS),
    )
    texts = [passage.text for passage in passages]
    assert len(texts) == 2
    assert any("nine times their body length" in text for text in texts)
    assert any("four feet from the eye socket" in text for text in texts)


def test_a_best_fact_without_the_subjects_words_survives_through_the_snippets(
    session_factory,
):
    # The rain video's subject never says "geosmin"; two of its snippets did.
    sources = [
        Source("Petrichor", "https://en.wikipedia.org/wiki/Petrichor", "Bacteria secrete geosmin."),
        Source("ACS", "https://www.acs.org/petrichor", "A compound called geosmin is released."),
    ]
    page = _page([
        "The human nose can detect geosmin at concentrations as low as 0.4 parts per billion.",
        "The oldest bristlecone pine is more than 4,800 years old today.",
    ])
    passages = knowledge.facts_for(
        sources, ["smell", "rain", "bacteria"], session_factory, lambda url: page
    )
    assert [p.text for p in passages] == [
        "The human nose can detect geosmin at concentrations as low as 0.4 parts per billion."
    ]


def test_an_author_bio_is_never_stored_as_a_fact():
    page = _page(["She has also worked as an ecologist for 20 years and as a journalist for 30."])
    assert knowledge.extract_facts(page) == []


def test_with_nothing_to_judge_relevance_by_nothing_is_filtered(session_factory):
    passages = knowledge.facts_for(
        [_source("https://a.com/x")], [], session_factory, lambda url: _page(LIZARD_FACTS[:1])
    )
    assert len(passages) == 1


# -- a research paper's methods are not facts ----------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "This figure shows the first 40 s corresponding to the first 1000 frames.",
        "Therefore, we used a Monte Carlo approach, where we randomly selected 250 samples.",
        "The groups differ only in variables N min ( p = 0.0307, Supplementary Table S5).",
        "The station was installed in a horse farm in Göd (47°43′N, 19°09′E) in 2017.",
    ],
)
def test_methods_and_statistics_are_not_stored(sentence):
    assert knowledge.extract_facts(sentence + "\n") == []


def test_a_finding_from_the_same_paper_is_kept():
    finding = (
        "They registered 12–15 °C differences between the temperatures of black and "
        "white stripes of living zebras in daylight."
    )
    assert [text for text, _, _ in knowledge.extract_facts(finding + "\n")] == [finding]
