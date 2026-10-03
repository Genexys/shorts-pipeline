import subprocess

import pytest

import video
from formats import LONG, SHORT


@pytest.fixture(autouse=True)
def software_encoder(monkeypatch):
    # nvenc_available caches its answer for the process. Probed through a
    # patched subprocess.run it would cache "yes", and the real render below
    # would then ask for an encoder this machine does not have.
    monkeypatch.setattr(video, "nvenc_available", lambda: False)


# blackdetect's stderr from the probe of the flower clip behind ffbe3dc8,
# cropped to 9:16 over its first four seconds.
FFBE_STDERR = (
    "[blackdetect @ 0x58e384efc880] black_start:0 black_end:3.96229 "
    "black_duration:3.96229\n"
)


# -- parse_black_spans --------------------------------------------------------


def test_parse_black_spans_reads_every_span():
    stderr = (
        "frame=  100 fps=0.0 q=-0.0 size=N/A\n"
        "[blackdetect @ 0x5f8] black_start:0 black_end:1.58 black_duration:1.58\n"
        "[blackdetect @ 0x5f8] black_start:2.86 black_end:3.8 black_duration:0.94\n"
    )
    assert video.parse_black_spans(stderr) == [(0.0, 1.58), (2.86, 3.8)]


def test_parse_black_spans_survives_no_output():
    assert video.parse_black_spans("") == []
    assert video.parse_black_spans(None) == []


# -- usable_footage: measured on the clips behind the October black spans ------


def test_a_fade_in_longer_than_the_window_drops_the_clip():
    # ffbe3dc8: the flower clip is black for 4.7 s; its 2.7 s shot was all black.
    assert video.usable_footage(video.parse_black_spans(FFBE_STDERR), 4.0, 26.9) is None


def test_a_clip_black_once_cropped_is_dropped():
    # 06fd2432: 1920x426, 0.87 s of black at full width, black for all four
    # seconds once cut to 9:16.
    assert video.usable_footage([(0.0, 3.967)], 4.0, 8.07) is None


@pytest.mark.parametrize(
    "black_end, duration",
    [
        (1.043, 10.01),  # 30604e66, refrigerator shelf
        (0.968, 10.05),  # c6ff4f9a, hieroglyphic inscription
        (0.56, 60.0),    # 5051ae56, runway line painting
        (0.334, 10.86),  # f6e106e1, moon behind buildings
    ],
)
def test_a_black_lead_in_moves_the_start_past_it(black_end, duration):
    start, seconds = video.usable_footage([(0.0, black_end)], 4.0, duration)
    assert start == pytest.approx(black_end)
    assert seconds == pytest.approx(duration - black_end)


def test_a_clip_that_flickers_to_black_is_dropped():
    # The filament bulb in bee482f4 and 5ae3b3fc: off at 0, 2.8 and 6.8 s.
    assert video.usable_footage([(0.0, 1.58), (2.86, 3.8)], 4.0, 13.32) is None


def test_black_after_the_picture_has_started_drops_the_clip():
    # 08aa9bfb: a laser clip that cuts to black at half a second.
    assert video.usable_footage([(0.5005, 2.2105)], 4.0, 18.35) is None
    # 0257be12: a skeleton on black that dips at 2.3 s, and again at 8.3 s.
    assert video.usable_footage([(2.333, 3.4)], 4.0, 11.63) is None


def test_clean_footage_is_used_whole():
    assert video.usable_footage([], 4.0, 12.5) == (0.0, 12.5)


def test_black_running_off_the_window_ends_the_usable_part():
    # Black that may go on past where we looked: stop before it.
    assert video.usable_footage([(3.2, 3.97)], 4.0, 20.0) == pytest.approx((0.0, 3.2))


def test_a_short_clip_keeps_what_lies_between_its_fades():
    start, seconds = video.usable_footage([(0.0, 0.5), (3.0, 3.5)], 3.5, 3.5)
    assert (start, seconds) == pytest.approx((0.5, 2.5))


def test_too_little_left_between_fades_drops_the_clip():
    assert video.usable_footage([(0.0, 0.6), (1.2, 1.5)], 1.5, 1.5) is None


# -- find_usable_footage --------------------------------------------------------


def _patch_probes(monkeypatch, spans_by_path, duration=10.0):
    monkeypatch.setattr(video, "probe_duration", lambda path: duration)
    checked = []

    def detect(path, seconds, fmt=SHORT, min_seconds=video.BLACK_MIN_SECONDS):
        checked.append((path, seconds))
        return spans_by_path.get(path, [])

    monkeypatch.setattr(video, "detect_black_spans", detect)
    return checked


def test_find_usable_footage_skips_lead_ins_and_drops_black_clips(monkeypatch):
    checked = _patch_probes(
        monkeypatch,
        {"fade.mp4": [(0.0, 1.0)], "black.mp4": [(0.0, 3.96)]},
    )

    footage = video.find_usable_footage(["clean.mp4", "fade.mp4", "black.mp4"])

    assert footage == {"clean.mp4": (0.0, 10.0), "fade.mp4": (1.0, 9.0)}
    assert all(seconds == video.BLACK_PROBE_SECONDS for _, seconds in checked)


def test_find_usable_footage_probes_no_further_than_a_short_clip_runs(monkeypatch):
    checked = _patch_probes(monkeypatch, {}, duration=2.5)
    video.find_usable_footage(["short.mp4"])
    assert checked == [("short.mp4", 2.5)]


def test_find_usable_footage_uses_a_clip_it_could_not_check(monkeypatch):
    monkeypatch.setattr(video, "probe_duration", lambda path: 10.0)
    monkeypatch.setattr(video, "detect_black_spans", lambda *args, **kwargs: None)
    assert video.find_usable_footage(["a.mp4"]) == {"a.mp4": (0.0, 10.0)}


def test_find_usable_footage_keeps_everything_rather_than_nothing(monkeypatch):
    _patch_probes(monkeypatch, {"a.mp4": [(0.0, 3.96)], "b.mp4": [(0.0, 3.96)]})
    assert video.find_usable_footage(["a.mp4", "b.mp4"]) == {
        "a.mp4": (0.0, 10.0),
        "b.mp4": (0.0, 10.0),
    }


# -- combine_videos -------------------------------------------------------------


def _capture_runs(monkeypatch):
    commands = []
    monkeypatch.setattr(
        video.subprocess, "run", lambda command, **kwargs: commands.append(command)
    )
    return commands


def _inputs(command):
    """Each -i path with the input options in front of it."""
    found = []
    options = []
    for index, arg in enumerate(command):
        if arg == "-i":
            found.append((command[index + 1], options))
            options = []
        elif index and command[index - 1] == "-i":
            continue
        elif arg in ("-ss", "-t"):
            options.append((arg, command[index + 1]))
    return found


def test_combine_videos_seeks_past_a_black_opening(monkeypatch, tmp_path):
    monkeypatch.setattr(video, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(video, "report_black_spans", lambda *args: [])
    commands = _capture_runs(monkeypatch)
    footage = {"fade.mp4": (1.043, 8.967), "clean.mp4": (0.0, 30.0)}

    video.combine_videos(["fade.mp4", "clean.mp4"], 9.0, 2, SHORT, footage=footage)

    inputs = _inputs(commands[-1])
    fades = [options for path, options in inputs if path == "fade.mp4"]
    cleans = [options for path, options in inputs if path == "clean.mp4"]
    assert fades and all(options[0] == ("-ss", "1.043") for options in fades)
    assert cleans and all(not any(o == "-ss" for o, _ in options) for options in cleans)


def test_combine_videos_plans_from_the_usable_part_only(monkeypatch, tmp_path):
    monkeypatch.setattr(video, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(video, "report_black_spans", lambda *args: [])
    _capture_runs(monkeypatch)
    planned = {}
    monkeypatch.setattr(
        video, "plan_clip_segments",
        lambda sources, duration, cap: planned.update(sources=sources) or [("a.mp4", 5.0)],
    )

    video.combine_videos(
        ["a.mp4", "dropped.mp4"], 5.0, 2, SHORT, footage={"a.mp4": (2.0, 6.0)}
    )

    assert planned["sources"] == [("a.mp4", 6.0)]


def test_combine_videos_measures_footage_when_not_given_it(monkeypatch, tmp_path):
    monkeypatch.setattr(video, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(video, "report_black_spans", lambda *args: [])
    commands = _capture_runs(monkeypatch)
    _patch_probes(monkeypatch, {"black.mp4": [(0.0, 3.96)]}, duration=30.0)

    video.combine_videos(["black.mp4", "clean.mp4"], 9.0, 2, SHORT)

    assert {path for path, _ in _inputs(commands[-1])} == {"clean.mp4"}


def test_combine_videos_audits_what_it_rendered(monkeypatch, tmp_path):
    monkeypatch.setattr(video, "TEMP_DIR", tmp_path)
    _capture_runs(monkeypatch)
    audited = []
    monkeypatch.setattr(
        video, "report_black_spans",
        lambda path, duration, fmt: audited.append((path, duration, fmt)) or [],
    )

    output = video.combine_videos(
        ["a.mp4"], 9.0, 2, LONG, footage={"a.mp4": (0.0, 30.0)}
    )

    assert audited == [(output, 9.0, LONG)]


# -- report_black_spans ---------------------------------------------------------


def test_report_black_spans_names_each_span(monkeypatch):
    warnings = []
    monkeypatch.setattr(
        video, "log",
        lambda message, level="info": warnings.append(message) if level == "warning" else None,
    )
    monkeypatch.setattr(
        video, "detect_black_spans",
        lambda path, seconds, fmt, min_seconds: [(13.53, 16.23), (23.5, 26.17)],
    )

    spans = video.report_black_spans("combined.mp4", 36.0, SHORT)

    assert spans == [(13.53, 16.23), (23.5, 26.17)]
    assert len(warnings) == 1
    assert "13.53-16.23s" in warnings[0] and "23.50-26.17s" in warnings[0]


def test_report_black_spans_asks_for_spans_of_half_a_second(monkeypatch):
    asked = {}
    monkeypatch.setattr(
        video, "detect_black_spans",
        lambda path, seconds, fmt, min_seconds: asked.update(
            seconds=seconds, min_seconds=min_seconds
        ) or [],
    )
    assert video.report_black_spans("combined.mp4", 36.0, SHORT) == []
    assert asked["min_seconds"] == video.RENDER_BLACK_MIN_SECONDS == 0.5
    # Bounded by the video's own length.
    assert asked["seconds"] >= 36.0


def test_report_black_spans_never_fails_the_render(monkeypatch):
    def broken(command, **kwargs):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(video.subprocess, "run", broken)
    assert video.report_black_spans("combined.mp4", 36.0, SHORT) == []


# -- detect_black_spans ---------------------------------------------------------


def test_detect_black_spans_is_bounded_and_judges_the_framed_picture(monkeypatch):
    seen = {}

    class Result:
        stderr = FFBE_STDERR

    def run(command, **kwargs):
        seen.update(command=command, kwargs=kwargs)
        return Result()

    monkeypatch.setattr(video.subprocess, "run", run)

    spans = video.detect_black_spans("clip.mp4", 4.0, SHORT)

    command = seen["command"]
    assert spans == [(0.0, 3.96229)]
    assert command[command.index("-t") + 1] == "4.000"
    assert command.index("-t") < command.index("-i")
    graph = command[command.index("-vf") + 1]
    assert graph.startswith(video.build_crop_filter(SHORT))
    assert "blackdetect=d=0.2:pix_th=0.1" in graph
    assert seen["kwargs"]["timeout"] > 0


# -- promote_strongest_opening ----------------------------------------------------


def test_the_opening_is_judged_where_its_shot_starts(monkeypatch, tmp_path):
    seeks = {}

    def run(command, **kwargs):
        seeks[command[command.index("-i") + 1]] = command[command.index("-ss") + 1]
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(video.subprocess, "run", run)

    video.promote_strongest_opening(
        ["fade.mp4", "clean.mp4"], tmp_path, starts={"fade.mp4": 1.043}
    )

    assert seeks == {"fade.mp4": "1.543", "clean.mp4": "0.500"}


# -- against real ffmpeg ----------------------------------------------------------


def _fade_in_clip(path, black_seconds=1.0, picture_seconds=3.0):
    # Black, then a test pattern: a clip that opens on a black lead-in.
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"color=c=black:s=640x360:d={black_seconds}:r=30",
            "-f", "lavfi", "-i", f"testsrc=s=640x360:d={picture_seconds}:r=30",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True, capture_output=True,
    )
    return str(path)


def test_a_real_black_lead_in_is_found_and_skipped(tmp_path):
    clip = _fade_in_clip(tmp_path / "fade.mp4")

    footage = video.find_usable_footage([clip], SHORT)

    start, seconds = footage[clip]
    assert start == pytest.approx(1.0, abs=0.05)
    assert seconds == pytest.approx(3.0, abs=0.1)


def test_a_real_render_from_a_faded_clip_has_no_black(monkeypatch, tmp_path):
    monkeypatch.setattr(video, "TEMP_DIR", tmp_path)
    clip = _fade_in_clip(tmp_path / "fade.mp4")

    combined = video.combine_videos([clip], 2.0, 2, SHORT)

    assert video.detect_black_spans(combined, 3.0, SHORT, 0.1) == []
