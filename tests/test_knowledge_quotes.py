"""Quoted passages on a page are read, and joined to the sentence introducing them.

The beer froth Short of 2026-10-04 had Improbable Research's page on the Ig
Nobel result in full and still ended on "one experimental research project has
challenged the idea": the challenge itself was a blockquote, and blockquotes
were dropped with the tables.
"""

import knowledge


# Improbable Research, "Exponential beer froth decay – disputed" (2017), as
# Firecrawl returned it.
IMPROBABLE = """Arnd Leike of the University of Munich was awarded the [2002 Ig Noble Physics prize](https://improbable.com/ig/winners/#ig2002) for demonstrating that beer froth obeys the mathematical Law of Exponential Decay.

Since then, investigations into the decay of beer froth have continued – and one experimental research project in particular has challenged the idea that it decays exponentially. A team from the [Institut für Angewandte und Physikalische Chemie–Arbeitsgruppe Chemische Synergetik](http://www.iapc.uni-bremen.de/index.php?lang=de), Universität Bremen, Germany, “call in question the results presented by Leike”, saying that :

> “ [![](https://improbable.com/wp-content/uploads/2017/06/BeerFoam.jpg)](https://www.hindawi.com/journals/ddns/2006/079717/abs/) **It is our finding that the foam volume does not shrink simple exponentially** but in terms of higher order according to the equation lnV(t) = a−bt −ct2.5 (3.1). The term ct2.5 describes the reorganisation of the bubble arrangements which will lead to an Apollonian gasket of bubbles. \\[our emphasis\\]”

See: [The Apollonian decay of beer foam bubble size distribution](http://downloads.hindawi.com/journals/ddns/2006/079717.pdf)
"""


def test_the_quoted_finding_is_stored_with_who_said_it():
    facts = [text for text, _, _ in knowledge.extract_facts(IMPROBABLE)]
    finding = next(fact for fact in facts if "does not shrink simple exponentially" in fact)
    assert finding.startswith("A team from the Institut für Angewandte")
    assert "Universität Bremen" in finding
    assert "saying that : “ It is our finding that the foam volume" in finding


def test_a_quote_after_a_colon_joins_the_paragraph_before_it():
    paragraphs = knowledge.clean_markdown("He wrote:\n\n> The sea was calm.\n\nThe end.")
    assert paragraphs == ["He wrote: The sea was calm.", "The end."]


def test_any_other_quote_is_a_paragraph_of_its_own():
    paragraphs = knowledge.clean_markdown("The storm passed.\n\n> The sea was calm.")
    assert paragraphs == ["The storm passed.", "The sea was calm."]


def test_nested_and_multi_line_quotes_lose_every_marker():
    paragraphs = knowledge.clean_markdown("> > First line\n> second line.\n>\n> Third.")
    assert paragraphs == ["First line second line. Third."]


def test_a_heading_or_table_inside_a_quote_is_still_dropped():
    paragraphs = knowledge.clean_markdown("> ## Pull quote\n> | a | b |\n> Kept.")
    assert paragraphs == ["Kept."]
