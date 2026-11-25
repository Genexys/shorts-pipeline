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
ones are recorded, logged and listed in the autopilot's report. With the check
off or failing, the video goes out as before.

What holds is decided here, not by the model's word alone. In the two weeks
after 2026-11-11 the hold kept 15 of 59 Shorts private. Of the 14 still
private on 2026-11-25, a look at each found 8 fine to publish: an opening the review itself called "acceptable but
weak", generic rolling-mill footage under a line about foil, a balloon
illustrating air pushing out. The ones worth holding all showed something the
viewer would take for the thing named: a stock actor for a named doctor, a
bluebottle for a hover fly, a modern clown doll for Edison's.
"""

import base64
import json
import os
import re
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

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
# The opening decides whether a Short is watched, and what it shows is what
# the viewer takes the subject to be: on 2026-11-03 the opossum Short opened
# for 7.6 s on what the review itself called a sloth, rated minor, and went out.
# A stand-in there holds however briefly it shows.
OPENING_SECONDS = 5.0
# Past the opening, a stand-in holds once the shots showing one add up to this
# long. The held stand-ins of November ran 2.4 to 7.8 s; a shot that flashes
# by under the name is noted, not held.
STAND_IN_SECONDS = 3.0
STAND_IN = "stand_in"
UNRELATED = "unrelated"
KINDS = ("black", UNRELATED, STAND_IN, "caption", "text_overlay", "watermark", "other")

PROMPT = """\
These are {count} frames taken at even intervals from a finished popular-science
video, in order, just before it is published. Captions, where present, are
burned in. Narration:
{narration}

Report only real problems a viewer would notice:
- "black": a black, blank or frozen frame where footage should be.
- "stand_in": footage the viewer would take for the specific animal, object,
  place or real person the narration names at that point, but which shows
  something else: a related species (a fire salamander or an axolotl for a
  ribbed newt), another vehicle, object or doll than the one named, a stock
  actor while the narration names a real person.
- "unrelated": footage plainly about something else than the narration around
  that point, which no viewer would take for the thing named (generic but
  suitable stock footage is fine, and so is an obvious illustration, such as
  a balloon for air that pushes out).
- "caption": caption text that is garbled, misspelt, cut off or does not match
  the narration's words.
- "text_overlay": the stock clip's own burned-in text, title or subtitles.
- "watermark": a stock site's watermark or logo.
- "other": anything else that would embarrass the channel.
Severity "major" means the video should not go out until a person has looked;
"minor" means worth knowing but publishable. A stand-in is major: the viewer
takes it for the thing named. A caption is misspelt only if its letters differ
from the narration's word; do not report one that matches. An empty list is
the normal answer.
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

    @property
    def minor(self) -> List[Problem]:
        return [p for p in self.problems if p.severity == MINOR]

    def summary(self) -> dict:
        return {
            "checked": self.checked,
            "problems": [p.describe() for p in self.problems],
            "minor": [p.describe() for p in self.minor],
        }


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


def _quoted(note: str) -> List[str]:
    return [word for word in re.findall(r"[\"“']([A-Za-z][A-Za-z'-]+)[\"”']", note) if len(word) > 2]


def _shot_at(seconds: float, shots: Sequence[Tuple[float, float]]) -> Optional[int]:
    for index, (start, duration) in enumerate(shots):
        if start <= seconds < start + duration:
            return index
    return None


def _stand_in_seconds(
    problems: Sequence[Problem], shots: Optional[Sequence[Tuple[float, float]]]
) -> Optional[float]:
    """How long the shots showing a stand-in play, or None if that is unknown."""
    if not shots:
        return None
    indexes = set()
    for problem in problems:
        if problem.kind != STAND_IN:
            continue
        index = _shot_at(problem.seconds, shots)
        if index is None:
            return None
        indexes.add(index)
    return sum(shots[index][1] for index in indexes)


def settle(
    problems: Sequence[Problem],
    narration: str,
    shots: Optional[Sequence[Tuple[float, float]]] = None,
) -> List[Problem]:
    """The problems with their severity checked against what can be checked.

    `shots` is the footage timeline as (start, seconds) pairs, when known.

    - A stand-in holds in the opening, and anywhere once the shots showing one
      play for STAND_IN_SECONDS in all. Without a timeline it holds.
    - Unrelated footage never holds: it is noted. On 2026-11-11..24 the review
      held four Shorts on an opening it called "only a minor mismatch" or
      "acceptable but weak", through a rule that made any unrelated opening
      major. The case that rule was for, a sloth under "an opossum", is a
      stand-in.
    - A misspelt caption whose quoted words are all in the narration is not
      one: the captions are the script's own words, and on 2026-11-08 the X-ray
      Short was held for "SKELETONS", spelt as the script spells it.
    """
    words = {word.lower() for word in re.findall(r"[A-Za-z'-]+", narration or "")}
    stand_in_seconds = _stand_in_seconds(problems, shots)
    settled = []
    for problem in problems:
        severity = problem.severity
        if problem.kind == STAND_IN:
            long_enough = stand_in_seconds is None or stand_in_seconds >= STAND_IN_SECONDS
            severity = MAJOR if long_enough or problem.seconds <= OPENING_SECONDS else MINOR
        elif problem.kind == UNRELATED:
            severity = MINOR
        elif problem.kind == "caption" and severity == MAJOR:
            quoted = _quoted(problem.note)
            if quoted and all(word.lower() in words for word in quoted):
                severity = MINOR
        settled.append(replace(problem, severity=severity))
    return settled


def review(
    video_path: str,
    narration: str,
    duration: float,
    work_dir: Path,
    ffmpeg: str = "ffmpeg",
    ask: Optional[Callable[[Sequence[tuple], str], List[Problem]]] = None,
    shots: Optional[Sequence[Tuple[float, float]]] = None,
) -> Review:
    """Looks at the finished video. Never raises.

    `shots` is the footage timeline as (start, seconds) pairs; it decides
    whether a stand-in shows long enough to hold the video (see settle).
    """
    if not is_enabled():
        return Review()
    frames = extract_frames(video_path, duration, work_dir, ffmpeg)
    if not frames:
        return Review()
    try:
        problems = settle((ask or _ask)(frames, narration), narration, shots)
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
