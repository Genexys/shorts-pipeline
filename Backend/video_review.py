"""A look at the finished video before it is uploaded.

The footage check judges each clip's first frame before the cut. What it cannot
see is the result: the black stretches that turned up in ten of twenty-five
renders in the week of 2026-09-27, a clip that fits its search term and not
the sentence it lands under, a caption that does not match the narration, a
stock clip carrying its own burned-in text or watermark. Those were found on
review, after publishing.

Frames are taken at even intervals from the final file, with the captions
burned in, and read by a vision model alongside the narration. A major problem
holds the upload back as private, through the pipeline's review hold. Minor
ones are recorded and logged. With the check off or failing, the video goes out
as before.
"""

import base64
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from logstream import log
import writer

DEFAULT_MODEL = "claude-sonnet-5-5"
OFF = "off"
# Sonnet at low effort reads a dozen small frames for about a cent.
EFFORT = "low"
MAX_TOKENS = 8000
REQUEST_TIMEOUT_SECONDS = 120
# Enough to cover a forty-second Short every three to four seconds, and a long
# video at a glance. Small, because a black frame or a wrong caption shows at
# any size and the tokens scale with the pixels.
FRAME_COUNT = 12
FRAME_WIDTH = 360
NARRATION_CHARS = 2000

MAJOR = "major"
MINOR = "minor"
KINDS = ("black", "unrelated", "caption", "text_overlay", "watermark", "other")

PROMPT = """\
These are {count} frames taken at even intervals from a finished popular-science
video, in order, just before it is published. Captions, where present, are
burned in. Narration:
{narration}

Report only real problems a viewer would notice:
- "black": a black, blank or frozen frame where footage should be.
- "unrelated": footage plainly about something else than the narration around
  that point (generic but suitable stock footage is fine).
- "caption": caption text that is garbled, misspelt, cut off or does not match
  the narration's words.
- "text_overlay": the stock clip's own burned-in text, title or subtitles.
- "watermark": a stock site's watermark or logo.
- "other": anything else that would embarrass the channel.
Severity "major" means the video should not go out until a person has looked;
"minor" means worth knowing but publishable. An empty list is the normal answer.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "problems": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "frame": {"type": "integer"},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "severity": {"type": "string", "enum": [MAJOR, MINOR]},
                    "note": {"type": "string"},
                },
                "required": ["frame", "kind", "severity", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["problems"],
    "additionalProperties": False,
}


@dataclass
class Problem:
    frame: int
    kind: str
    severity: str
    note: str
    seconds: float = 0.0

    def describe(self) -> str:
        return f"{self.kind} at {self.seconds:.0f}s: {self.note}"


@dataclass
class Review:
    checked: bool = False
    problems: List[Problem] = field(default_factory=list)

    @property
    def major(self) -> List[Problem]:
        return [p for p in self.problems if p.severity == MAJOR]

    def summary(self) -> dict:
        return {"checked": self.checked, "problems": [p.describe() for p in self.problems]}


def model_name() -> str:
    return os.getenv("VIDEO_REVIEW_MODEL", "").strip() or DEFAULT_MODEL


def is_enabled() -> bool:
    return writer.is_configured() and model_name().lower() != OFF


def frame_times(duration: float, count: int = FRAME_COUNT) -> List[float]:
    """Even points inside the video, never on its first or last instant."""
    if duration <= 0 or count <= 0:
        return []
    step = duration / (count + 1)
    return [round(step * (i + 1), 2) for i in range(count)]


def extract_frames(
    video_path: str, duration: float, work_dir: Path, ffmpeg: str = "ffmpeg"
) -> List[tuple]:
    """(seconds, jpeg bytes) for each frame that could be read."""
    work_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for index, seconds in enumerate(frame_times(duration)):
        out = work_dir / f"review_{index:02d}.jpg"
        try:
            subprocess.run(
                [ffmpeg, "-y", "-v", "error", "-ss", f"{seconds:.2f}", "-i", video_path,
                 "-frames:v", "1", "-vf", f"scale={FRAME_WIDTH}:-2", "-q:v", "5", str(out)],
                check=True, capture_output=True, timeout=60,
            )
            frames.append((seconds, out.read_bytes()))
        except (subprocess.SubprocessError, OSError) as err:
            log(f"[!] Could not take a review frame at {seconds:.1f}s ({err}).", "warning")
    return frames


def _client():
    import anthropic

    return anthropic.Anthropic(
        api_key=writer.api_key(), timeout=REQUEST_TIMEOUT_SECONDS, max_retries=2
    )


def _ask(frames: Sequence[tuple], narration: str) -> List[Problem]:
    content: List[dict] = [{
        "type": "text",
        "text": PROMPT.format(count=len(frames), narration=narration.strip()[:NARRATION_CHARS]),
    }]
    for number, (seconds, jpeg) in enumerate(frames, 1):
        content.append({"type": "text", "text": f"Frame {number} ({seconds:.1f}s):"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": base64.standard_b64encode(jpeg).decode("ascii"),
        }})
    response = _client().messages.create(
        model=model_name(),
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
    )
    writer.record_usage(
        getattr(response, "model", None) or model_name(),
        getattr(response, "usage", None),
        "the video review",
    )
    if getattr(response, "stop_reason", None) == "refusal":
        raise RuntimeError("the model declined")
    text = next(block.text for block in response.content if block.type == "text")
    return parse_problems(text, [seconds for seconds, _ in frames])


def parse_problems(text: str, times: Sequence[float]) -> List[Problem]:
    problems = []
    for item in json.loads(text).get("problems") or []:
        number = item.get("frame")
        if not isinstance(number, int) or not 1 <= number <= len(times):
            continue
        if item.get("kind") not in KINDS or item.get("severity") not in (MAJOR, MINOR):
            continue
        problems.append(Problem(number, item["kind"], item["severity"],
                                str(item.get("note") or "").strip(), times[number - 1]))
    return problems


def review(
    video_path: str,
    narration: str,
    duration: float,
    work_dir: Path,
    ffmpeg: str = "ffmpeg",
    ask: Optional[Callable[[Sequence[tuple], str], List[Problem]]] = None,
) -> Review:
    """Looks at the finished video. Never raises."""
    if not is_enabled():
        return Review()
    frames = extract_frames(video_path, duration, work_dir, ffmpeg)
    if not frames:
        return Review()
    try:
        problems = (ask or _ask)(frames, narration)
    except Exception as err:
        log(f"[!] Video review unavailable ({writer.scrub(str(err))}).", "warning")
        return Review()
    result = Review(checked=True, problems=problems)
    if not problems:
        log(f"[+] Video review: {len(frames)} frames, nothing to report.", "success")
    for problem in problems:
        log(f"[!] Video review, {problem.severity}: {problem.describe()}",
            "warning" if problem.severity == MAJOR else "info")
    return result
