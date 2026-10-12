from pathlib import Path

import pytest

import elevenlabs_voice
import persona
import video
from autopilot_config import AutopilotConfig, choose_persona
from formats import SHORT

ARENA = persona.PERSONAS[persona.ARENA]


# -- the persona ------------------------------------------------------------------


def test_the_arena_persona_has_its_poses_on_disk():
    for pose in ARENA.poses:
        assert persona.pose_path(ARENA, pose).is_file()


def test_the_arena_rules_keep_the_facts():
    assert "never in the facts" in " ".join(ARENA.rules.split())
    assert "FACT... CONFIRMED." in ARENA.rules


def test_get_is_forgiving_and_none_for_unknown():
    assert persona.get(" Arena ") is ARENA
    assert persona.get("") is None and persona.get(None) is None and persona.get("x") is None


# -- poses ------------------------------------------------------------------------


def test_fallback_poses_hype_the_hook_and_celebrate_the_end():
    assert persona.fallback_poses(4) == ["hyped", "talking", "talking", "celebrating"]
    assert persona.fallback_poses(1) == ["hyped"]


def test_choose_poses_takes_the_model_answer():
    ask = lambda p, m: '{"poses": ["hyped", "impressed", "celebrating"]}'
    assert persona.choose_poses(ARENA, ["a.", "b.", "c."], ask) == ["hyped", "impressed", "celebrating"]


def test_choose_poses_replaces_an_unknown_pose():
    ask = lambda p, m: '{"poses": ["moonwalking", "hyped"]}'
    assert persona.choose_poses(ARENA, ["a.", "b."], ask) == ["talking", "hyped"]


def test_choose_poses_falls_back_on_a_wrong_count_or_failure():
    assert persona.choose_poses(ARENA, ["a.", "b.", "c."], lambda p, m: '{"poses": ["hyped"]}') == \
        persona.fallback_poses(3)

    def boom(p, m):
        raise RuntimeError("down")

    assert persona.choose_poses(ARENA, ["a.", "b."], boom) == persona.fallback_poses(2)


def test_sentence_starts_follow_the_caption_cues():
    sentences = ["An onion fights back.", "It fires a gas."]
    cues = [(0.0, 0.4, "AN"), (0.4, 0.9, "ONION"), (0.9, 1.4, "FIGHTS"), (1.4, 1.9, "BACK."),
            (2.3, 2.6, "IT"), (2.6, 3.0, "FIRES"), (3.0, 3.2, "A"), (3.2, 3.7, "GAS.")]
    assert persona.sentence_starts(sentences, cues) == [0.0, 2.3]


def test_pose_plan_covers_the_video(tmp_path):
    cues = [(0.0, 0.5, "ONE"), (0.5, 1.0, "TWO."), (1.2, 1.6, "THREE"), (1.6, 2.0, "FOUR.")]
    plan = persona.pose_plan(ARENA, "One two. Three four.", cues, 2.5,
                             lambda p, m: '{"poses": ["hyped", "celebrating"]}')
    assert [(p.stem, s, e) for p, s, e in plan] == [("hyped", 0.0, 1.2), ("celebrating", 1.2, 2.5)]


def test_read_cues_reads_an_srt(tmp_path):
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:00,500\nONE\n\n2\n00:00:00,500 --> 00:00:01,000\nTWO\n")
    assert persona.read_cues(str(srt)) == [(0.0, 0.5, "ONE"), (0.5, 1.0, "TWO")]
    assert persona.read_cues(str(tmp_path / "missing.srt")) == []


# -- the autopilot draw ----------------------------------------------------------


def _config(share):
    return AutopilotConfig.from_env({"AUTOPILOT_NICHE": "n", "AUTOPILOT_PERSONA_SHARE": share})


def test_persona_is_off_by_default():
    assert AutopilotConfig.from_env({"AUTOPILOT_NICHE": "n"}).persona_share == 0
    assert choose_persona(AutopilotConfig.from_env({"AUTOPILOT_NICHE": "n"}), "short", roll=1) == ""


def test_persona_follows_the_roll_and_skips_long_form():
    config = _config("50")
    assert choose_persona(config, "short", roll=50) == persona.ARENA
    assert choose_persona(config, "short", roll=51) == ""
    assert choose_persona(_config("100"), "long", roll=1) == ""


# -- voice -------------------------------------------------------------------------


def test_a_persona_brings_its_own_v4_settings():
    body = elevenlabs_voice._body("x", "eleven_v4", None, None, {"stability": 0.35})
    assert body["voice_settings"] == {"stability": 0.35}
    assert elevenlabs_voice._body("x", "eleven_v4", None, None)["voice_settings"] == \
        elevenlabs_voice.V4_VOICE_SETTINGS


# -- burning captions -------------------------------------------------------------


def test_an_ordinary_word_is_full_size_and_layered():
    assert video.fire_fit("ONION", SHORT.width) == 100
    lines = video.fire_dialogue("0:00:00.00", "0:00:00.50", "ONION")
    assert len(lines) == 4
    assert all(line.endswith("ONION") for line in lines)
    assert r"\pos(540,960)" in lines[0]


def test_a_long_chemical_name_breaks_at_a_hyphen():
    lines = video.fire_dialogue("0:00:00.00", "0:00:00.50", "SYN-PROPANETHIAL-S-OXIDE.")
    assert len(lines) == 8
    texts = {line.rsplit("}", 1)[1] for line in lines}
    assert texts == {"SYN-PROPANETHIAL-", "S-OXIDE."}


def test_a_long_word_without_a_hyphen_shrinks():
    word = "SUPERCALIFRAGILISTIC"
    scale = video.fire_fit(word, SHORT.width)
    assert scale < 100
    assert rf"\fscx{scale}\fscy{scale})" in video.fire_dialogue("0:00:00.00", "0:00:00.50", word)[0]


def test_patch_ass_script_burns_fire_only_when_asked():
    script = ("[Script Info]\n[V4+ Styles]\nStyle: Default,Arial,16,&H00FFFFFF,&H00FFFFFF,&H00000000,"
              "&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1\n[Events]\n"
              "Dialogue: 0,0:00:00.00,0:00:00.50,Default,,0,0,0,,onion\n")
    plain = video.patch_ass_script(script, "center,center", "#FFFFFF")
    fire = video.patch_ass_script(script, "center,center", "#FFFFFF", caption_style="fire")
    assert "Black Ops One" not in plain and plain.count("Dialogue:") == 1
    assert "Black Ops One" in fire and fire.count("Dialogue:") == 4 and "ONION" in fire


# -- the mascot overlay ------------------------------------------------------------


def test_mascot_filter_overlays_each_segment_in_turn():
    graph = video.mascot_filter("ass='x.ass'", [("a.png", 0.0, 1.5), ("b.png", 1.5, 3.0)])
    assert graph.startswith("[0:v]ass='x.ass'[c0]")
    assert "[2:v]scale=302:-1[m0]" in graph and "[3:v]scale=302:-1[m1]" in graph
    assert "enable='between(t,0.00,1.50)'[c1]" in graph
    assert graph.endswith("enable='between(t,1.50,3.00)'[vout]")


def test_render_command_with_a_mascot_loops_the_poses(monkeypatch):
    monkeypatch.setattr(video, "prepare_ass_subtitles", lambda *a, **k: "subs.ass")
    monkeypatch.setattr(video, "encoder_args", lambda threads, final: ["-c:v", "libx264"])
    command = video.build_render_command("v.mp4", "a.mp3", "s.srt", "o.mp4", 2, "center,center",
                                         "#FFF", SHORT, "fire", [("talking.png", 0.0, 2.0)])
    assert command[command.index("talking.png") - 3:command.index("talking.png") + 1] == \
        ["-loop", "1", "-i", "talking.png"]
    assert "-filter_complex" in command and command[command.index("-map") + 1] == "[vout]"


def test_render_command_without_a_mascot_is_unchanged(monkeypatch):
    monkeypatch.setattr(video, "prepare_ass_subtitles", lambda *a, **k: "subs.ass")
    monkeypatch.setattr(video, "encoder_args", lambda threads, final: ["-c:v", "libx264"])
    command = video.build_render_command("v.mp4", "a.mp3", "s.srt", "o.mp4", 2, "center,center", "#FFF")
    assert "-vf" in command and "-filter_complex" not in command
    assert command[command.index("-map") + 1] == "0:v:0"
