"""Burned captions are upper-cased, so letters the font lacks come out as capitals.

The Neanderthal Short of 2026-10-03 showed "PääBO": the bundled font draws its
lowercase as capitals but has no "ä", and libass took it from a fallback font
in lowercase.
"""

import video


ASS = (
    "[Script Info]\n"
    "ScriptType: v4.00+\n"
    "\n"
    "[V4+ Styles]\n"
    "Format: Name, Fontname, Fontsize\n"
    "Style: Default,Arial,16\n"
    "\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Svante Pääbo, Röntgen\n"
)


def dialogue(script: str) -> str:
    return next(line for line in script.splitlines() if line.startswith("Dialogue:"))


def test_dialogue_text_is_upper_cased_including_accented_letters():
    patched = video.patch_ass_script(ASS, "center,center", "#FFFF00")
    assert dialogue(patched).endswith(",SVANTE PÄÄBO, RÖNTGEN")


def test_the_timing_and_style_fields_are_untouched():
    patched = video.patch_ass_script(ASS, "center,center", "#FFFF00")
    assert dialogue(patched).startswith("Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,")


def test_commas_inside_the_text_survive():
    line = "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,one, two, three"
    assert video.uppercase_dialogue(line).endswith(",,ONE, TWO, THREE")


def test_override_tags_and_escapes_keep_their_case():
    # \i1 upper-cased would be \I1, which libass does not know; \n is a soft
    # break and \N a hard one, so changing the case changes the layout.
    text = r"{\i1}gödel{\i0}\Nwon\nprize\hnow"
    assert video.uppercase_caption(text) == r"{\i1}GÖDEL{\i0}\NWON\nPRIZE\hNOW"


def test_other_lines_are_left_as_they_were():
    patched = video.patch_ass_script(ASS, "center,center", "#FFFF00")
    assert "ScriptType: v4.00+" in patched
    assert "Format: Layer, Start, End, Style, Name" in patched


def test_a_malformed_dialogue_line_is_left_alone():
    assert video.uppercase_dialogue("Dialogue: broken") == "Dialogue: broken"
