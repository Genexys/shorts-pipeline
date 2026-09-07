import pytest

import video


# -- clip selection ---------------------------------------------------------


def test_plan_clip_segments_fills_the_requested_duration():
    segments = video.plan_clip_segments([("a.mp4", 30.0), ("b.mp4", 30.0)], 20.0, 5.0)
    assert sum(duration for _, duration in segments) == pytest.approx(20.0)


def test_plan_clip_segments_respects_the_per_clip_cap():
    segments = video.plan_clip_segments([("a.mp4", 60.0)], 30.0, 5.0)
    assert all(duration <= 5.0 for _, duration in segments)


def test_plan_clip_segments_never_exceeds_a_source_length():
    # A 2 s source must not be asked for 5 s, or ffmpeg reads past the end.
    segments = video.plan_clip_segments([("short.mp4", 2.0), ("long.mp4", 60.0)], 20.0, 5.0)
    for path, duration in segments:
        assert duration <= (2.0 if path == "short.mp4" else 60.0)


def test_plan_clip_segments_cycles_sources_until_covered():
    segments = video.plan_clip_segments([("a.mp4", 10.0), ("b.mp4", 10.0)], 18.0, 3.0)
    assert len(segments) > 2
    assert {path for path, _ in segments} == {"a.mp4", "b.mp4"}


def test_plan_clip_segments_rejects_an_empty_source_list():
    with pytest.raises(ValueError):
        video.plan_clip_segments([], 10.0, 5.0)


def test_plan_clip_segments_raises_when_no_source_is_usable():
    # Every source shorter than one frame: looping forever would hang the job.
    with pytest.raises(RuntimeError):
        video.plan_clip_segments([("a.mp4", 0.0)], 10.0, 5.0)


# -- concat filter ----------------------------------------------------------


def test_build_concat_filter_chains_every_segment():
    graph = video.build_concat_filter(3)
    assert "[v0][v1][v2]concat=n=3:v=1:a=0[vout]" in graph
    assert graph.count("scale=1080:1920") == 3


def test_build_concat_filter_crops_both_orientations_in_one_expression():
    graph = video.build_concat_filter(1)
    # Footage narrower than 9:16 is cut top and bottom, wider is cut at the
    # sides; a single expression must cover both.
    assert "if(lt(iw/ih," in graph
    assert "setsar=1" in graph


# -- subtitle styling -------------------------------------------------------


def test_ass_colour_converts_rgb_to_ass_bgr():
    assert video.ass_colour("#FFFF00") == "&H0000FFFF"
    assert video.ass_colour("#FF0000") == "&H000000FF"
    assert video.ass_colour("#0000FF") == "&H00FF0000"


def test_ass_colour_tolerates_a_missing_hash():
    assert video.ass_colour("00FF00") == "&H0000FF00"


def test_ass_colour_falls_back_on_garbage():
    assert video.ass_colour("nonsense") == "&H0000FFFF"
    assert video.ass_colour("") == "&H0000FFFF"


@pytest.mark.parametrize(
    "position,alignment",
    [("center,top", 8), ("center,center", 5), ("center,bottom", 2)],
)
def test_build_style_line_maps_every_ui_position(position, alignment):
    fields = video.build_style_line(position, "#FFFF00").split(",")
    assert fields[18] == str(alignment)


def test_build_style_line_defaults_to_centre_for_unknown_positions():
    assert video.build_style_line("center,sideways", "#FFFF00").split(",")[18] == "5"


def test_build_style_line_names_the_bundled_font_and_size():
    fields = video.build_style_line("center,center", "#FFFF00").split(",")
    assert fields[1] == video.SUBTITLE_FONT_NAME
    assert fields[2] == str(video.SUBTITLE_FONT_SIZE)


def test_build_style_line_only_offsets_vertically_off_centre():
    centred = video.build_style_line("center,center", "#FFFF00").split(",")
    top = video.build_style_line("center,top", "#FFFF00").split(",")
    assert centred[21] == "0"
    assert top[21] == str(video.SUBTITLE_TOP_MARGIN_PX)


# -- ASS script sizing ------------------------------------------------------


ASS_FROM_FFMPEG = """[Script Info]
ScriptType: v4.00+
PlayResX: 384
PlayResY: 288

[V4+ Styles]
Format: Name, Fontname, Fontsize
Style: Default,Arial,16

[Events]
Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,hello
"""


def test_patch_ass_script_declares_the_real_frame_size():
    # The whole bug: ffmpeg writes 384x288, font sizes are relative to it, and
    # a size meant for a 1920-tall frame renders almost seven times too big.
    patched = video.patch_ass_script(ASS_FROM_FFMPEG, "center,center", "#FFFF00")
    assert f"PlayResX: {video.VIDEO_WIDTH}" in patched
    assert f"PlayResY: {video.VIDEO_HEIGHT}" in patched
    assert "PlayResY: 288" not in patched


def test_patch_ass_script_replaces_the_style():
    patched = video.patch_ass_script(ASS_FROM_FFMPEG, "center,top", "#FF0000")
    assert "Style: Default,Arial,16" not in patched
    assert video.SUBTITLE_FONT_NAME in patched
    assert "&H000000FF" in patched


def test_patch_ass_script_keeps_the_dialogue():
    patched = video.patch_ass_script(ASS_FROM_FFMPEG, "center,center", "#FFFF00")
    assert "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,hello" in patched


def test_patch_ass_script_inserts_resolution_when_absent():
    script = "[Script Info]\nScriptType: v4.00+\n\n[Events]\n"
    patched = video.patch_ass_script(script, "center,center", "#FFFF00")
    assert f"PlayResY: {video.VIDEO_HEIGHT}" in patched


# -- encoder choice ---------------------------------------------------------


def test_encoder_args_uses_nvenc_when_available(monkeypatch):
    monkeypatch.setattr(video, "nvenc_available", lambda: True)
    assert video.encoder_args(12, final=True)[:2] == ["-c:v", video.NVENC_CODEC]


def test_encoder_args_falls_back_to_software(monkeypatch):
    monkeypatch.setattr(video, "nvenc_available", lambda: False)
    args = video.encoder_args(12, final=True)
    assert args[:2] == ["-c:v", video.SOFTWARE_CODEC]
    # Threads only mean something to the software encoder.
    assert "-threads" in args


def test_encoder_args_does_not_pass_threads_to_nvenc(monkeypatch):
    monkeypatch.setattr(video, "nvenc_available", lambda: True)
    assert "-threads" not in video.encoder_args(12, final=False)


def test_encoder_args_uses_a_faster_setting_for_the_intermediate(monkeypatch):
    monkeypatch.setattr(video, "nvenc_available", lambda: False)
    intermediate = video.encoder_args(4, final=False)
    final = video.encoder_args(4, final=True)
    assert "ultrafast" in intermediate and "medium" in final


def test_encoder_args_always_sets_a_delivery_pixel_format(monkeypatch):
    monkeypatch.setattr(video, "nvenc_available", lambda: True)
    args = video.encoder_args(0, final=True)
    assert args[-2:] == ["-pix_fmt", "yuv420p"]


def test_nvenc_availability_is_probed_once(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return None

    monkeypatch.setattr(video, "_encoder_cache", {})
    monkeypatch.setattr(video.subprocess, "run", fake_run)

    video.nvenc_available()
    video.nvenc_available()

    assert len(calls) == 1
