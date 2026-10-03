import gpt


def test_accented_name_keeps_its_letters_in_a_hashtag():
    # Published on 2026-10-01 as #WilhelmRNtgen.
    assert gpt.to_hashtag("Wilhelm Röntgen") == "#WilhelmRontgen"


def test_accents_fold_across_languages():
    assert gpt.to_hashtag("Ångström") == "#Angstrom"
    assert gpt.to_hashtag("Gödel incompleteness") == "#GodelIncompleteness"
    assert gpt.to_hashtag("crème brûlée") == "#CremeBrulee"
    assert gpt.to_hashtag("São Paulo") == "#SaoPaulo"


def test_letters_without_a_decomposition_are_transliterated():
    assert gpt.to_hashtag("Hans Christian Ørsted") == "#HansChristianOrsted"
    assert gpt.to_hashtag("Łódź") == "#Lodz"
    assert gpt.to_hashtag("Straße") == "#Strasse"


def test_plain_ascii_hashtags_are_unchanged():
    assert gpt.to_hashtag("ocean pressure") == "#OceanPressure"
    assert gpt.to_hashtag("deep-sea, life!") == "#DeepSeaLife"


def test_text_with_no_latin_letters_still_gives_nothing():
    assert gpt.to_hashtag("東京") == ""
    assert gpt.to_hashtag("!!!") == ""


def test_subject_keywords_keep_accented_words_whole():
    # Before, "Röntgen" became "ntgen" and the "R" was dropped as too short.
    assert gpt.keywords_from_subject("How did Wilhelm Röntgen see through skin?") == [
        "wilhelm",
        "rontgen",
        "see",
        "through",
        "skin",
    ]


def test_build_hashtags_from_an_accented_tag():
    hashtags = gpt.build_hashtags(["X rays", "Wilhelm Röntgen"], "subject", ("#Shorts",))
    assert hashtags == ["#Shorts", "#XRays", "#WilhelmRontgen"]


def test_ascii_words_folds_compatibility_forms():
    # NFKD also unpacks ligatures and full-width letters.
    assert gpt.ascii_words("ﬁsh ＡＢＣ") == ["fish", "ABC"]
