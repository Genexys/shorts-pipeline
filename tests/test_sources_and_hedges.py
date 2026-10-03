"""Which sources a video may stand on, and keeping what limits a claim."""

import gpt
import knowledge
import research
from research import Source

LOOP_RECORDER = Source(
    title="Asystole Detected by Implantable Loop Recorders: True or False?",
    url="https://pmc.ncbi.nlm.nih.gov/articles/PMC6932227/",
    snippet="We report a case of a false asystole detected by an implantable loop recorder.",
)
GREATBATCH = Source(
    title="The Accidental Invention of the Lifesaving Pacemaker",
    url="https://www.saturdayeveningpost.com/2019/09/the-accidental-invention/",
    snippet="... in 1956 when he installed the wrong resistor and accidentally invented ...",
)
TIMES = Source(
    title="In 1956, one wrong resistor sparked the discovery of the implantable pacemaker",
    url="https://timesofindia.indiatimes.com/science/wrong-resistor/articleshow/1.cms",
    snippet="The device Greatbatch was trying to build was an oscillator ...",
)


# -- domains ----------------------------------------------------------------------


def test_shops_answer_sites_and_the_new_social_domains_are_not_sources():
    for url in (
        "https://www.threads.com/@localizefood.app/post/DdiURcADnN0/a-fake-fat",
        "https://www.linkedin.com/posts/today-in-tech-history",
        "https://www.amazon.com/Stainless-Remover/dp/B06X9FL4RL",
        "https://www.amazon.co.uk/dp/B06X9FL4RL",
        "https://smile.amazon.de/dp/B06X9FL4RL",
        "https://www.justanswer.com/appliance/l29c8-microwave-hot-cold-spots.html",
    ):
        assert not research.is_allowed(url), url


def test_lookalike_hosts_are_still_allowed():
    for url in (
        "https://en.wikipedia.org/wiki/Stainless_steel_soap",
        "https://s3.amazonaws.com/some-journal/paper.html",
        "https://www.amazonconservation.org/news",
    ):
        assert research.is_allowed(url), url


# -- the relevance judge ----------------------------------------------------------


def test_the_judge_keeps_what_it_names_in_order():
    seen = {}

    def judge(prompt):
        seen["prompt"] = prompt
        return "[1, 3]"

    kept = research.keep_relevant(
        [GREATBATCH, LOOP_RECORDER, TIMES], "A wrong resistor made a pacemaker", judge
    )
    assert kept == [GREATBATCH, TIMES]
    assert "A wrong resistor made a pacemaker" in seen["prompt"]
    # The host is shown, so a shop or a test-prep site can be told apart.
    assert "(pmc.ncbi.nlm.nih.gov)" in seen["prompt"]


def test_no_judge_or_an_unreadable_one_keeps_everything():
    sources = [GREATBATCH, LOOP_RECORDER]
    assert research.keep_relevant(sources, "s", lambda prompt: None) == sources
    assert research.keep_relevant(sources, "s", lambda prompt: "I think both?") == sources

    def broken(prompt):
        raise RuntimeError("overloaded")

    assert research.keep_relevant(sources, "s", broken) == sources


def test_a_judge_may_keep_nothing():
    # The brief then degrades honestly, as it does with too few sources.
    assert research.keep_relevant([LOOP_RECORDER], "s", lambda prompt: "[]") == []


def test_the_judge_reads_numbers_out_of_a_wordy_reply():
    reply = "Sources 2 and 3 are about the subject.\n[2, 3]"
    kept = research.keep_relevant([LOOP_RECORDER, GREATBATCH, TIMES], "s", lambda p: reply)
    assert kept == [GREATBATCH, TIMES]


# -- pages read in full -----------------------------------------------------------


def test_one_paper_on_two_sites_is_read_once():
    plos = Source(
        title="Cows painted with zebra-like striping can avoid biting fly attack | PLOS One",
        url="https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0223447",
        snippet="s",
    )
    pmc = Source(
        title="Cows painted with zebra-like striping can avoid biting fly attack - PMC - NIH",
        url="https://pmc.ncbi.nlm.nih.gov/articles/PMC6776349/",
        snippet="s",
    )
    guardian = Source(
        title="If the shoo fits: cows painted with zebra stripes keep flies in line",
        url="https://www.theguardian.com/world/2019/oct/11/if-the-shoo-fits",
        snippet="s",
    )
    chosen = knowledge.choose_pages([plos, guardian, pmc], 2)
    assert chosen == [pmc, guardian]


COMET = (
    "The shape of the passenger windows was not indicated in any failure mode "
    "detailed in the accident report and was not viewed as a contributing factor."
)
STEEL = (
    "Lorch and Buckley conducted some citizen science in 2016 to test this mechanism "
    "but there is no conclusive, rigorous evidence for it."
)
ZEBRA_RESULT = (
    "However, the frequency of skin twitches of B&W was significantly higher than "
    "that of CONT and B (p <0.05) cows."
)


def test_a_sentence_that_limits_a_claim_is_a_fact():
    # Neither carries a figure the old scoring would have counted for much; the
    # Comet sentence carries none at all and was never stored.
    assert knowledge.specificity(COMET) >= 3.0
    assert knowledge.specificity(STEEL) > knowledge.specificity(
        "Lorch and Buckley conducted some citizen science in 2016 to test this mechanism."
    )


def test_a_result_with_its_p_value_is_kept_and_methods_are_not():
    assert knowledge.looks_like_prose(ZEBRA_RESULT)
    assert not knowledge.looks_like_prose(
        "The difference was small but significant, p = 0.0307, Supplementary Table S5."
    )


def test_extract_facts_stores_the_comet_and_zebra_sentences_verbatim():
    markdown = f"# Page\n\n{COMET}\n\nThe airliner first flew in 1949.\n\n{ZEBRA_RESULT}\n"
    texts = [text for text, _, _ in knowledge.extract_facts(markdown)]
    assert COMET in texts
    assert ZEBRA_RESULT in texts


# -- what the writer is told --------------------------------------------------------


def test_the_writer_is_told_to_claim_no_more_than_the_notes():
    rules = " ".join(gpt.RESEARCH_RULES.split())
    assert "Keep their hedges." in rules
    assert "Keep a figure's scope." in rules
    assert '"helped create" or "first implantable" keeps its qualifier' in rules
    assert "This holds for the first sentence most of all." in rules


def test_an_anchor_line_loses_to_the_notes():
    rules = " ".join(gpt.anchor_rules("Thomas Edison's first hydroelectric plant").split())
    assert "Where the notes disagree with it, the notes win." in rules
    assert "If you include it, say when it did happen." in rules
