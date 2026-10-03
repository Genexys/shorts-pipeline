from types import SimpleNamespace

import pytest
import srt

import video
from formats import LONG, SHORT
from speech import narration_plan


# Job 5051ae56, 2026-10-02: the script, and the words AssemblyAI returned for
# its narration (transcript c2c6d103). The video was captioned "ATNL 36R.".
RUNWAY_SCRIPT = (
    "Airports sometimes close a runway, grind off its painted number, and "
    "repaint it one digit higher. The number looks like a name, but it is "
    "really a compass reading: take the runway's magnetic heading, round to "
    "the nearest ten degrees, and drop the final zero. Denver's runway 17L–35R "
    "currently sits at 172.5 degrees; about four more degrees of drift and it "
    "becomes 18L–36R. Declination there shifts roughly a tenth of a degree a "
    "year. The paint doesn't go wrong. Magnetic north moves."
)
RUNWAY_WORDS = [
    ('Airports', 96, 594), ('sometimes', 594, 1157), ('close', 1237, 1623),
    ('a', 1623, 1671), ('runway,', 1719, 2153), ('grind', 2683, 3149),
    ('off', 3149, 3390), ('its', 3390, 3567), ('painted', 3567, 3937),
    ('number,', 3985, 4339), ('and', 5078, 5255), ('repaint', 5319, 5801),
    ('it', 5801, 6010), ('one', 6058, 6187), ('digit', 6299, 6621),
    ('higher.', 6621, 6958), ('The', 7183, 7280), ('number', 7280, 7649),
    ('looks', 7649, 7906), ('like', 7906, 8083), ('a', 8131, 8163),
    ('name,', 8163, 8453), ('but', 9015, 9112), ('it', 9112, 9257), ('is', 9257, 9321),
    ('really', 9499, 9805), ('a', 9983, 10031), ('compass', 10144, 10531),
    ('reading.', 10708, 10934), ('Take', 11853, 12159), ('the', 12159, 12256),
    ("runway's", 12256, 12837), ('magnetic', 12837, 13530), ('heading,', 13530, 13836),
    ('round', 14498, 14965), ('to', 14997, 15094), ('the', 15094, 15175),
    ('nearest', 15175, 15723), ('10', 15723, 15933), ('degrees,', 15965, 16497),
    ('and', 17220, 17366), ('drop', 17415, 17806), ('the', 17969, 18034),
    ('final', 18050, 18425), ('zero.', 18474, 18897), ("Denver's", 20500, 20821),
    ('runway', 20934, 21369), ('17L/35R', 21594, 22818), ('currently', 23510, 23929),
    ('sits', 23929, 24331), ('at', 24476, 24605), ('172.5', 24701, 26231),
    ('degrees.', 26231, 26617), ('About', 27655, 27946), ('4', 27962, 28155),
    ('more', 28203, 28397), ('degrees', 28397, 28752), ('of', 28864, 28945),
    ('drift,', 28945, 29413), ('and', 29670, 29735), ('it', 29832, 29912),
    ('becomes', 29912, 30364), ('ATNL', 30557, 31170), ('36R.', 31347, 31782),
    ('Declination.', 33539, 34152), ('There', 35619, 35732), ('shifts', 35780, 36135),
    ('roughly', 36183, 36538), ('a', 36538, 36554), ('tenth', 36667, 36941),
    ('of', 36941, 37086), ('a', 37086, 37102), ('degree', 37150, 37424),
    ('a', 37714, 37730), ('year.', 37794, 37923), ('The', 38922, 38970),
    ('paint', 39019, 39260), ("doesn't", 39260, 39583), ('go', 39583, 39744),
    ('wrong.', 39744, 40034), ('Magnetic', 40791, 41290), ('north', 41338, 41612),
    ('moves.', 41741, 42160),
]

# Job 15f8c60b, 2026-09-30, transcript 45a76c13. Captioned "Jiwan" Han.
COFFEE_SCRIPT = (
    "Walking backwards with a cup of coffee spills less than walking forwards. "
    "That's from Korean physicist Jiwon Han, whose study won an Ig Nobel Prize "
    "for fluid dynamics.\n\n"
    "A related model explains part of it: a string or pendulum between the cup "
    "and the carrying hand lessens rigidity and sharply lowers the lowest "
    "resonant frequency, diminishing sloshing at almost all frequencies. Which "
    "is why a claw grip over the mug beats holding it by the handle."
)
COFFEE_WORDS = [
    ('Walking', 96, 321), ('backwards', 433, 963), ('with', 995, 1155),
    ('a', 1155, 1219), ('cup', 1219, 1412), ('of', 1412, 1524), ('coffee', 1556, 1894),
    ('spills', 1894, 2407), ('less', 2504, 2632), ('than', 2841, 2953),
    ('walking', 3065, 3354), ('forwards.', 3467, 4012), ("That's", 4205, 4462),
    ('from', 4510, 4638), ('Korean', 4686, 5056), ('physicist', 5152, 5874),
    ('Jiwan', 5955, 6324), ('Han,', 6452, 6805), ('whose', 7656, 7865),
    ('study', 7897, 8266), ('won', 8362, 8507), ('an', 8603, 8747), ('IG', 8844, 9036),
    ('Nobel', 9181, 9534), ('Prize', 9727, 10112), ('for', 10369, 10497),
    ('fluid', 10529, 10995), ('dynamics.', 11027, 11685), ('A', 12712, 12728),
    ('related', 12792, 13210), ('model', 13258, 13579), ('explains', 13836, 14365),
    ('part', 14398, 14654), ('of', 14654, 14735), ('it:', 14735, 14959),
    ('a', 15601, 15617), ('string', 15617, 16051), ('or', 16163, 16308),
    ('pendulum', 16308, 16853), ('between', 16885, 17174), ('the', 17206, 17303),
    ('cup', 17367, 17560), ('and', 17993, 18057), ('the', 18105, 18234),
    ('carrying', 18234, 18699), ('hand', 18796, 19149), ('lessens', 20015, 20433),
    ('rigidity', 20481, 21027), ('and', 21460, 21524), ('sharply', 21605, 22070),
    ('lowers', 22166, 22471), ('the', 22584, 22744), ('lowest', 22744, 23210),
    ('resonant', 23210, 23675), ('frequency,', 23691, 24237),
    ('diminishing', 24751, 25328), ('sloshing', 25409, 26003), ('at', 26259, 26372),
    ('almost', 26500, 26869), ('all', 27062, 27110), ('frequencies.', 27158, 27848),
    ('Which', 29333, 29462), ('is', 29511, 29624), ('why', 29721, 29850),
    ('a', 30125, 30141), ('claw', 30206, 30416), ('grip', 30529, 30852),
    ('over', 30852, 31014), ('the', 31014, 31079), ('mug', 31095, 31450),
    ('beats', 31596, 32000), ('holding', 32000, 32372), ('it', 32404, 32566),
    ('by', 32566, 32792), ('the', 32792, 32954), ('handle.', 32954, 33293),
]


def narrated(script):
    """The sentences the pipeline hands generate_subtitles for a Short."""
    return [chunk for section in narration_plan(script, False) for chunk in section]


def timing_of(timed, text):
    matches = [(start, end) for token, start, end in timed if token == text]
    assert matches, f"{text!r} not in the captions"
    return matches[0]


def cues(srt_text):
    return [(cue.start.total_seconds(), cue.end.total_seconds(), cue.content)
            for cue in srt.parse(srt_text)]


# -- tokens and keys ----------------------------------------------------------


def test_free_standing_punctuation_rides_with_a_word():
    assert video.caption_tokens(["Paint — then dry."]) == ["Paint —", "then", "dry."]
    assert video.caption_tokens(["— and then"]) == ["— and", "then"]


def test_tokens_span_every_sentence_in_order():
    assert video.caption_tokens(["One two.", "Three."]) == ["One", "two.", "Three."]


@pytest.mark.parametrize(
    "written, heard",
    [("17L–35R", "17L/35R"), ("number,", "Number"), ("Röntgen", "Rontgen"), ("Ig", "IG")],
)
def test_keys_ignore_case_accents_and_punctuation(written, heard):
    assert video._caption_key(written) == video._caption_key(heard)


# -- align_script_to_words: the two published mistakes -------------------------


def test_the_runway_caption_shows_the_scripts_designation():
    timed = video.align_script_to_words(
        video.caption_tokens(narrated(RUNWAY_SCRIPT)), RUNWAY_WORDS
    )

    shown = [token for token, _, _ in timed]
    assert "ATNL" not in shown and "36R." not in shown
    # The one script word takes the whole stretch AssemblyAI split in two.
    assert timing_of(timed, "18L–36R.") == pytest.approx((30.557, 31.782))
    # Equal under the key, so it keeps its own timing exactly.
    assert timing_of(timed, "17L–35R") == pytest.approx((21.594, 22.818))


def test_the_coffee_caption_spells_jiwon():
    timed = video.align_script_to_words(
        video.caption_tokens(narrated(COFFEE_SCRIPT)), COFFEE_WORDS
    )

    shown = [token for token, _, _ in timed]
    assert "Jiwan" not in shown
    assert timing_of(timed, "Jiwon") == pytest.approx((5.955, 6.324))
    assert timing_of(timed, "Ig") == pytest.approx((8.844, 9.036))


def test_spelled_numbers_keep_the_script_spelling_on_the_digits_timing():
    timed = video.align_script_to_words(
        video.caption_tokens(narrated(RUNWAY_SCRIPT)), RUNWAY_WORDS
    )
    assert timing_of(timed, "four") == pytest.approx((27.962, 28.155))
    assert timing_of(timed, "ten") == pytest.approx((15.723, 15.933))


def test_every_script_word_is_shown_once_in_order():
    tokens = video.caption_tokens(narrated(COFFEE_SCRIPT))
    timed = video.align_script_to_words(tokens, COFFEE_WORDS)
    assert [token for token, _, _ in timed] == tokens
    starts = [start for _, start, _ in timed]
    assert starts == sorted(starts)


# -- align_script_to_words: the rules -------------------------------------------


def test_words_assemblyai_added_are_dropped():
    timed = video.align_script_to_words(
        ["the", "dog", "barked"],
        [("the", 0, 200), ("um", 200, 500), ("dog", 500, 800), ("barked", 800, 1200)],
    )
    assert timed == [("the", 0.0, 0.2), ("dog", 0.5, 0.8), ("barked", 0.8, 1.2)]


def test_missed_words_share_the_word_before_and_the_silence_after():
    timed = video.align_script_to_words(
        ["the", "big", "red", "dog"], [("the", 0, 200), ("dog", 900, 1200)]
    )
    # "the" plus half a second of the silence before "dog", in three.
    assert [round(start, 3) for _, start, _ in timed] == [0.0, 0.233, 0.467, 0.9]
    assert timed[2][2] == pytest.approx(0.7)


def test_a_missed_first_word_borrows_from_the_word_after():
    timed = video.align_script_to_words(
        ["So", "the", "dog"], [("the", 600, 800), ("dog", 800, 1100)]
    )
    assert timed[0][1] == pytest.approx(0.1)
    assert timed[1][2] == pytest.approx(0.8)
    assert timed[2] == ("dog", 0.8, 1.1)


def test_a_missed_last_word_borrows_from_the_word_before():
    timed = video.align_script_to_words(
        ["Magnetic", "north", "moves."], [("Magnetic", 40791, 41290), ("north", 41338, 41612)]
    )
    assert timed[1][1] == pytest.approx(41.338)
    assert timed[2][2] == pytest.approx(42.112)
    assert timed[1][2] == pytest.approx(timed[2][1])


def test_a_transcript_of_something_else_is_not_trusted():
    tokens = video.caption_tokens(narrated(RUNWAY_SCRIPT))
    assert video.align_script_to_words(tokens, COFFEE_WORDS) is None


def test_half_the_words_matching_is_enough():
    assert video.align_script_to_words(
        ["one", "two", "three", "four"], [("one", 0, 100), ("two", 100, 200)]
    ) is not None
    assert video.align_script_to_words(
        ["one", "two", "three", "four", "five"], [("one", 0, 100), ("two", 100, 200)]
    ) is None


@pytest.mark.parametrize("tokens, words", [([], [("a", 0, 1)]), (["a"], [])])
def test_nothing_to_align_gives_none(tokens, words):
    assert video.align_script_to_words(tokens, words) is None


# -- build_caption_srt -------------------------------------------------------------


def _runway_srt(max_chars):
    timed = video.align_script_to_words(
        video.caption_tokens(narrated(RUNWAY_SCRIPT)), RUNWAY_WORDS
    )
    return video.build_caption_srt(timed, max_chars)


def test_short_captions_are_a_word_or_two():
    for _, _, text in cues(_runway_srt(SHORT.subtitle_max_chars)):
        assert len(text) < SHORT.subtitle_max_chars or " " not in text


def test_long_captions_are_readable_lines():
    lines = [text for _, _, text in cues(_runway_srt(LONG.subtitle_max_chars))]
    assert all(len(text) < LONG.subtitle_max_chars for text in lines)
    assert any(len(text.split()) >= 4 for text in lines)


def test_a_cue_starts_when_its_first_word_is_said():
    for start, _, text in cues(_runway_srt(SHORT.subtitle_max_chars)):
        if text.startswith("18L–36R."):
            assert start == pytest.approx(30.557)
        if text.startswith("About"):
            # Shown 1.4 s early before, timed by its share of the characters.
            assert start == pytest.approx(27.655)


def test_pauses_clear_the_caption_and_speech_holds_it():
    timed = [("one", 0.0, 0.3), ("two", 0.35, 0.6), ("three", 1.5, 1.9)]
    parsed = cues(video.build_caption_srt(timed, 5))
    assert parsed == [
        (0.0, 0.35, "one"),   # held until "two" starts
        (0.35, 0.6, "two"),   # cleared for the 0.9 s silence
        (1.5, 1.9, "three"),
    ]


def test_packing_matches_srt_equalizer():
    # The same greedy rule, so line breaks are the ones viewers already saw.
    import srt_equalizer
    from datetime import timedelta

    phrase = "grind off its painted number and repaint it one digit higher"
    words = phrase.split()
    timed = [(word, index * 0.3, index * 0.3 + 0.3) for index, word in enumerate(words)]
    ours = [text for _, _, text in cues(video.build_caption_srt(timed, 10))]
    theirs = [
        sub.content
        for sub in srt_equalizer.split_subtitle(
            srt.Subtitle(1, timedelta(0), timedelta(seconds=len(words) * 0.3), phrase), 10
        )
    ]
    assert ours == theirs


@pytest.mark.parametrize("max_chars", [SHORT.subtitle_max_chars, LONG.subtitle_max_chars])
def test_srt_equalizer_leaves_aligned_captions_alone(tmp_path, max_chars):
    import srt_equalizer

    path = tmp_path / "captions.srt"
    built = _runway_srt(max_chars)
    path.write_text(built, encoding="utf-8")
    srt_equalizer.equalize_srt_file(str(path), str(path), max_chars)
    assert cues(path.read_text(encoding="utf-8")) == cues(built)


def test_no_cue_is_empty_or_ends_before_it_starts():
    timed = [("a", 1.0, 1.0), ("b", 1.0, 1.0)]
    for start, end, _ in cues(video.build_caption_srt(timed, 1)):
        assert end > start


# -- generate_subtitles with AssemblyAI ------------------------------------------


def _fake_assemblyai(monkeypatch, words, exported="1\n00:00:00,000 --> 00:00:01,000\nAssemblyAI\n"):
    transcript = SimpleNamespace(
        words=None if words is None else [
            SimpleNamespace(text=text, start=start, end=end, confidence=0.9)
            for text, start, end in words
        ],
        export_subtitles_srt=lambda: exported,
    )
    fake = SimpleNamespace(
        settings=SimpleNamespace(api_key=None),
        TranscriptionConfig=lambda **kwargs: kwargs,
        Transcriber=lambda config: SimpleNamespace(transcribe=lambda path: transcript),
    )
    monkeypatch.setattr(video, "aai", fake)
    monkeypatch.setattr(video, "ASSEMBLY_AI_API_KEY", "key")


def test_generate_subtitles_writes_the_scripts_words(monkeypatch, tmp_path):
    monkeypatch.setattr(video, "SUBTITLES_DIR", tmp_path)
    _fake_assemblyai(monkeypatch, RUNWAY_WORDS)

    path = video.generate_subtitles(
        "voice.mp3", narrated(RUNWAY_SCRIPT), [], "en", SHORT.subtitle_max_chars
    )

    text = open(path, encoding="utf-8").read()
    assert "18L–36R." in text and "17L–35R" in text
    assert "ATNL" not in text and "17L/35R" not in text


def test_generate_subtitles_falls_back_without_a_word_list(monkeypatch, tmp_path):
    monkeypatch.setattr(video, "SUBTITLES_DIR", tmp_path)
    _fake_assemblyai(monkeypatch, None)

    path = video.generate_subtitles("voice.mp3", narrated(RUNWAY_SCRIPT), [], "en", 42)

    assert "AssemblyAI" in open(path, encoding="utf-8").read()


def test_generate_subtitles_falls_back_when_the_words_do_not_line_up(monkeypatch, tmp_path):
    monkeypatch.setattr(video, "SUBTITLES_DIR", tmp_path)
    _fake_assemblyai(monkeypatch, COFFEE_WORDS)

    path = video.generate_subtitles("voice.mp3", narrated(RUNWAY_SCRIPT), [], "en", 42)

    assert "AssemblyAI" in open(path, encoding="utf-8").read()
