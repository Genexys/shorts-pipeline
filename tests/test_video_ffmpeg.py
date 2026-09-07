import pytest

import video
from formats import LONG, SHORT


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
    # Footage narrower than the target is cut top and bottom, wider is cut at
    # the sides; a single expression must cover both.
    assert "if(lt(iw/ih," in graph
    assert "setsar=1" in graph


def test_build_concat_filter_defaults_to_the_short_format():
    # Callers that pass no format must keep producing today's vertical output.
    assert video.build_concat_filter(1) == video.build_concat_filter(1, SHORT)


def test_build_concat_filter_scales_to_the_format_size():
    assert f"scale={SHORT.width}:{SHORT.height}" in video.build_concat_filter(1, SHORT)
    assert f"scale={LONG.width}:{LONG.height}" in video.build_concat_filter(1, LONG)


def test_build_concat_filter_crops_to_the_format_ratio():
    assert str(SHORT.aspect_ratio) in video.build_concat_filter(1, SHORT)
    assert str(LONG.aspect_ratio) in video.build_concat_filter(1, LONG)


def test_build_concat_filter_differs_between_formats():
    # Guards against the ratio being wired in while the scale is left behind.
    assert video.build_concat_filter(2, SHORT) != video.build_concat_filter(2, LONG)


def test_combine_videos_takes_the_clip_cap_from_the_format(monkeypatch):
    captured = {}
    monkeypatch.setattr(video, "probe_duration", lambda path: 60.0)
    monkeypatch.setattr(
        video, "plan_clip_segments",
        lambda sources, duration, cap: captured.update(cap=cap) or [("a.mp4", 5.0)],
    )
    monkeypatch.setattr(video.subprocess, "run", lambda command, **kw: None)

    video.combine_videos(["a.mp4"], 30.0, 4, LONG)

    assert captured["cap"] == LONG.max_clip_duration


def test_combine_videos_defaults_to_short(monkeypatch):
    captured = {}
    monkeypatch.setattr(video, "probe_duration", lambda path: 60.0)
    monkeypatch.setattr(
        video, "plan_clip_segments",
        lambda sources, duration, cap: captured.update(cap=cap) or [("a.mp4", 5.0)],
    )
    monkeypatch.setattr(video.subprocess, "run", lambda command, **kw: None)

    video.combine_videos(["a.mp4"], 30.0, 4)

    assert captured["cap"] == SHORT.max_clip_duration


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
    assert fields[2] == str(SHORT.subtitle_font_size)


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
    assert f"PlayResX: {SHORT.width}" in patched
    assert f"PlayResY: {SHORT.height}" in patched
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
    assert f"PlayResY: {SHORT.height}" in patched


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


# -- format-driven subtitles ------------------------------------------------


def test_patch_ass_script_uses_the_format_resolution():
    for fmt in (SHORT, LONG):
        patched = video.patch_ass_script(
            ASS_FROM_FFMPEG, "center,center", "#FFFF00", fmt
        )
        assert f"PlayResX: {fmt.width}" in patched
        assert f"PlayResY: {fmt.height}" in patched


def test_build_style_line_uses_the_format_font_size():
    for fmt in (SHORT, LONG):
        assert (
            video.build_style_line("center,center", "#FFFF00", fmt).split(",")[2]
            == str(fmt.subtitle_font_size)
        )


# -- optional burn-in -------------------------------------------------------


def _render_command(fmt, monkeypatch):
    monkeypatch.setattr(video, "prepare_ass_subtitles", lambda *a, **k: "/tmp/x.ass")
    monkeypatch.setattr(video, "nvenc_available", lambda: False)
    return video.build_render_command(
        "in.mp4", "voice.mp3", "subs.srt", "out.mp4", 4, "center,center", "#FFFF00", fmt
    )


def test_render_burns_subtitles_for_short(monkeypatch):
    command = _render_command(SHORT, monkeypatch)
    assert "-vf" in command
    assert "ass=" in command[command.index("-vf") + 1]


def test_render_skips_the_burn_for_long(monkeypatch):
    # Long form ships an .srt caption track instead.
    command = _render_command(LONG, monkeypatch)
    assert "-vf" not in command


def test_render_copies_the_video_stream_when_nothing_is_drawn(monkeypatch):
    # Nothing is composited, so re-encoding would cost minutes and lose quality
    # for no change at all.
    command = _render_command(LONG, monkeypatch)
    assert command[command.index("-c:v") + 1] == "copy"


def test_render_re_encodes_when_subtitles_are_burned(monkeypatch):
    command = _render_command(SHORT, monkeypatch)
    assert command[command.index("-c:v") + 1] != "copy"


def test_render_always_attaches_the_voiceover_and_clamps_length(monkeypatch):
    for fmt in (SHORT, LONG):
        command = _render_command(fmt, monkeypatch)
        assert command[command.index("-map") + 1] == "0:v:0"
        assert "1:a:0" in command
        assert "-shortest" in command


def test_render_does_not_prepare_subtitles_it_will_not_draw(monkeypatch):
    calls = []
    monkeypatch.setattr(
        video, "prepare_ass_subtitles", lambda *a, **k: calls.append(a) or "/tmp/x.ass"
    )
    monkeypatch.setattr(video, "nvenc_available", lambda: False)
    video.build_render_command(
        "in.mp4", "voice.mp3", "subs.srt", "out.mp4", 4, "center,center", "#FFFF00", LONG
    )
    assert calls == []


# -- camera motion ----------------------------------------------------------


@pytest.mark.parametrize("style", video.MOTION_STYLES)
def test_motion_expressions_are_driven_by_the_frame_counter(style):
    # zoompan's own `zoom` restarts at 1 for every input frame when d=1, so an
    # expression like zoom+0.0008 never grows and the filter silently does
    # nothing. Shipped exactly that once; the frame counter is what moves.
    graph = video.build_motion_filter(style, SHORT, 90)
    assert "on" in graph
    assert "zoom+" not in graph
    assert "zoom-" not in graph


@pytest.mark.parametrize("style", video.MOTION_STYLES)
def test_motion_advances_one_output_frame_per_input_frame(style):
    # Without d=1 zoompan holds a still and multiplies the frame count.
    assert ":d=1:" in video.build_motion_filter(style, SHORT, 90)


@pytest.mark.parametrize("style", video.MOTION_STYLES)
def test_motion_renders_at_the_format_size(style):
    graph = video.build_motion_filter(style, LONG, 90)
    assert f"s={LONG.width}x{LONG.height}" in graph


def test_short_shots_move_as_much_as_long_ones():
    # The step is derived from the shot length, so a 1-second shot is not left
    # looking static next to a 5-second one.
    short_shot = video.build_motion_filter("push_in", SHORT, 30)
    long_shot = video.build_motion_filter("push_in", SHORT, 150)
    assert short_shot != long_shot


def test_neighbouring_shots_do_not_move_the_same_way():
    graph = video.build_concat_filter(4, SHORT, [3.0, 3.0, 3.0, 3.0])
    chains = graph.split(";")[:4]
    assert len({chain.split("zoompan=")[1][:40] for chain in chains}) == 4


# -- shot rhythm ------------------------------------------------------------


def test_shot_rhythm_averages_to_one():
    # Otherwise the shot count drifts and footage starts repeating.
    assert sum(video.SHOT_RHYTHM) / len(video.SHOT_RHYTHM) == pytest.approx(1.0)


def test_plan_clip_segments_varies_shot_length():
    segments = video.plan_clip_segments([("a.mp4", 60.0)] * 6, 18.0, 5.0)
    assert len({round(duration, 2) for _, duration in segments}) > 1


def test_plan_clip_segments_still_sums_to_the_audio_length():
    segments = video.plan_clip_segments([("a.mp4", 60.0)] * 6, 18.0, 5.0)
    assert sum(duration for _, duration in segments) == pytest.approx(18.0)


def test_a_flat_rhythm_gives_identical_shots():
    segments = video.plan_clip_segments([("a.mp4", 60.0)] * 6, 18.0, 5.0, rhythm=(1.0,))
    assert len({round(duration, 2) for _, duration in segments}) == 1


def test_a_trailing_sliver_is_folded_into_the_previous_shot():
    # The rhythm leaves a remainder; as its own segment it is a quarter-second
    # flicker, and it drags in one more clip, reintroducing a repeat.
    segments = video.plan_clip_segments([("a.mp4", 60.0)] * 8, 10.7, 5.0)
    assert all(duration >= video.MIN_SHOT_SECONDS for _, duration in segments)
    assert sum(duration for _, duration in segments) == pytest.approx(10.7)


def test_folding_never_overruns_the_source_clip():
    # Extending a shot past its own footage would read frames that do not exist.
    segments = video.plan_clip_segments(
        [("a.mp4", 2.0), ("b.mp4", 2.0), ("c.mp4", 2.0)], 5.2, 2.0
    )
    lengths = dict([("a.mp4", 2.0), ("b.mp4", 2.0), ("c.mp4", 2.0)])
    for path, duration in segments:
        assert duration <= lengths[path]


def test_folding_leaves_a_single_shot_alone():
    segments = video.plan_clip_segments([("a.mp4", 60.0)], 0.5, 5.0)
    assert len(segments) == 1


# -- one shot per clip ------------------------------------------------------


@pytest.mark.parametrize("fmt", [SHORT, LONG])
@pytest.mark.parametrize("ratio", [0.6, 0.9, 1.0, 1.2])
def test_never_needs_more_shots_than_it_has_clips(fmt, ratio):
    # Repeated footage part way through is what makes an automated upload look
    # mass-produced, and it is invisible in any single-number check.
    import math

    duration = fmt.target_words / 150 * 60 * ratio
    least = max(2, math.ceil(duration / fmt.max_clip_duration))
    for clips in range(least, fmt.stock_video_count + 1):
        segments = video.plan_clip_segments(
            [(f"c{i}.mp4", 60.0) for i in range(clips)], duration, fmt.max_clip_duration
        )
        assert len(segments) <= clips
        assert sum(d for _, d in segments) == pytest.approx(duration)


def test_the_rhythm_is_compressed_to_fit_under_the_cap():
    # A long beat truncated by the cap drags the average shot below the
    # nominal length, so the run needs one more shot than it has clips.
    fitted = video._fit_rhythm((1.0, 0.8, 1.2), required=10.04, max_clip_duration=12.0)
    assert max(fitted) * 10.04 <= 12.0 + 1e-9
    assert sum(fitted) / len(fitted) == pytest.approx(1.0)


def test_the_rhythm_is_left_alone_when_it_already_fits():
    assert video._fit_rhythm((1.0, 0.8, 1.2), 3.0, 12.0) == (1.0, 0.8, 1.2)


def test_a_shot_far_shorter_than_its_neighbours_is_folded_in():
    # Two seconds among ten-second shots reads as a mistake just as much as a
    # quarter-second among three-second ones.
    segments = video.plan_clip_segments([("a.mp4", 60.0)] * 20, 200.8, 12.0)
    shortest = min(d for _, d in segments)
    assert shortest >= 200.8 / 20 * video.SHORT_SHOT_FRACTION


def test_folding_never_exceeds_the_per_shot_cap():
    # Absorbing a remainder must not quietly produce a shot longer than the
    # format allows.
    for clips, duration, cap in ((1, 30.0, 5.0), (3, 20.0, 5.0), (20, 200.8, 12.0)):
        segments = video.plan_clip_segments(
            [(f"c{i}.mp4", 60.0) for i in range(clips)], duration, cap
        )
        assert all(d <= cap + 1e-9 for _, d in segments)
