"""The writer's cut: when it runs, what it may not remove, and the name check."""

import gpt

# The Appleton Short as drafted on 2026-09-30, and as the cut left it.
APPLETON_DRAFT = (
    "The world's first hydroelectric central station was a pulp mill in Appleton, "
    "Wisconsin. Sitting on the Fox River, thirty miles southwest of Green Bay, the "
    "Appleton Paper and Pulp Company paired a prime water-power site with one of "
    "Edison's new generators. The Appleton Edison Electric Light Company incorporated "
    "that May. The first generator sputtered on September 27, 1882, failed, and after "
    "days of troubleshooting ran successfully on September 30. A second followed on "
    "Vulcan Street in November. Edison's steam plant at Pearl Street had beaten it by "
    "twenty-six days, but steam electricity proved more expensive. Within four years, "
    "nearly fifty hydroelectric projects were announced across North America, and by "
    "the turn of the century water was supplying more than forty percent of America's "
    "electricity."
)
APPLETON_CUT = (
    "The world's first hydroelectric central station was a pulp mill in Appleton, "
    "Wisconsin. The first generator sputtered on September 27, 1882, failed, and after "
    "days of troubleshooting ran successfully on September 30. Edison's steam plant at "
    "Pearl Street had beaten it by twenty-six days, but steam electricity proved more "
    "expensive. Within four years, nearly fifty hydroelectric projects were announced "
    "across North America, and by the turn of the century water was supplying more "
    "than forty percent of America's electricity."
)
# What a cut that kept the names could look like.
APPLETON_KEPT = (
    "The world's first hydroelectric central station was a pulp mill in Appleton, "
    "Wisconsin. The Appleton Edison Electric Light Company incorporated that May, on "
    "the Fox River near Green Bay, with Edison's generators at the Appleton Paper and "
    "Pulp Company. The first generator ran on September 30, 1882; a second followed on "
    "Vulcan Street in November, weeks after Pearl Street. Within four years, nearly fifty hydroelectric projects "
    "were announced across North America, and by the turn of the century water was "
    "supplying more than forty percent of America's electricity."
)


def test_names_in_finds_names_but_not_sentence_openers():
    names = gpt.names_in(APPLETON_DRAFT)
    assert {"Vulcan", "Street", "November", "Electric", "Edison", "Appleton"} <= names
    # "Sitting on the Fox River" opens a sentence on an ordinary word.
    assert "Sitting" not in names
    # A possessive opener is a name; so is one followed by another name.
    assert "Denver" in gpt.names_in("It moved. Denver's runway sits at 172 degrees.")
    assert "Wilson" in gpt.names_in("It began. Wilson Greatbatch built an oscillator.")


def test_names_dropped_reports_what_the_appleton_cut_lost():
    dropped = gpt.names_dropped(APPLETON_DRAFT, APPLETON_CUT)
    assert {"Vulcan", "Company", "Electric", "Light", "November"} <= set(dropped)
    # Still in the cut, in another sentence: "Pearl Street", "Edison's steam plant".
    assert not {"Street", "Edison", "Appleton"} & set(dropped)


def _cuts(monkeypatch, *replies):
    prompts = []
    queue = list(replies)

    def fake(prompt, model, report_model=None):
        prompts.append(prompt)
        return queue.pop(0)

    monkeypatch.setattr(gpt, "write_creative", fake)
    return prompts


def test_a_cut_that_drops_names_is_asked_for_again_and_the_second_kept(monkeypatch):
    prompts = _cuts(monkeypatch, APPLETON_CUT, APPLETON_KEPT)
    result = gpt.tighten_script(APPLETON_DRAFT, 85, 90, 72, "model", subject="s")
    assert result == APPLETON_KEPT
    assert len(prompts) == 2
    assert "Your last cut removed" in prompts[1] and "Vulcan" in prompts[1]


def test_over_the_ceiling_a_cut_that_still_drops_names_beats_the_trim(monkeypatch):
    # 124 words against a 90-word ceiling: refusing the cut hands the script to
    # the trim, which keeps the opening and loses the ending.
    _cuts(monkeypatch, APPLETON_CUT, APPLETON_CUT)
    result = gpt.tighten_script(APPLETON_DRAFT, 85, 90, 72, "model", subject="s")
    assert result == APPLETON_CUT


def test_under_the_ceiling_a_draft_beats_a_cut_that_drops_names(monkeypatch):
    draft = (
        "A runway's number is a compass reading. Denver's runway 17L sits at 172.5 "
        "degrees; Fairbanks renumbered its runway in 2009. Declination shifts a tenth "
        "of a degree a year. The paint doesn't go wrong. Magnetic north moves."
    )
    cut = (
        "A runway's number is a compass reading. Declination shifts a tenth of a "
        "degree a year. The paint doesn't go wrong. Magnetic north moves."
    )
    _cuts(monkeypatch, cut, cut)
    assert gpt.tighten_script(draft, 20, 90, 17, "model", subject="s") is None


def test_the_cut_is_told_the_question_and_what_answers_it(monkeypatch):
    prompts = _cuts(monkeypatch, APPLETON_KEPT)
    gpt.tighten_script(
        APPLETON_DRAFT, 85, 90, 72, "model",
        subject="Why does a glass of water sweat on a hot day?",
    )
    instructions = " ".join(prompts[0].split("Script:")[0].split())
    assert "The video answers this: Why does a glass of water sweat on a hot day?" in instructions
    assert "Who built, made, ran, owned or discovered something" in instructions
    assert "Any word that narrows a claim" in instructions
    assert "The real case that shows something actually happened" in instructions
    assert "A longer script is fine; a wrong one is not." in instructions


def test_a_draft_a_dozen_words_over_its_target_is_left_alone(monkeypatch):
    # The pacemaker draft ran 79 words against a 70-word target and lost "and
    # heart sounds", the accurate half of what the device recorded.
    draft = " ".join(f"word{i}" for i in range(79)) + ". It ends here."
    calls = []

    def fake(prompt, model, report_model=None):
        calls.append("tighten" if "Cut it to about" in prompt else "write")
        return draft

    monkeypatch.setattr(gpt, "write_creative", fake)
    gpt.generate_script("s", 1, "model", "en_us_001", "", target_words=70, max_words=90)
    assert len(draft.split()) == 70 + gpt.TARGET_SLACK_WORDS
    assert calls == ["write"]


def test_a_figure_followed_by_punctuation_is_not_a_new_figure():
    # "September 30." in the draft and "September 30, 1882;" in the cut are the
    # same numbers; read with their punctuation they looked like new ones, and
    # a faithful cut was thrown away for "introducing a figure".
    assert gpt._DIGITS.findall("on September 30, 1882; then 1.5 percent.") == ["30", "1882", "1.5"]
