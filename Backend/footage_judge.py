"""A look at every stock clip before it goes into a video.

Stock search matches words, not pictures. On 2026-10-03 the tomato Short
opened on an AI-drawn pelican selling fish ("vegetable market stall"), and the
Neanderthal one on a cartoon dog with a bone ("ancient bone fragment"); both
were Pixabay clips typed "animation", and the opening is picked for contrast,
which cartoons have most of. The same videos showed a tennis court for
"supreme court building", a Halloween skull for "Neanderthal skull", and the
Sputnik one an American rocket. No search term could have avoided those; only
looking at the picture can.

So one frame per clip — the one the viewer sees first, cropped as it will be
shown — goes to a Claude model with the subject, the narration and the term
that found it. A cartoon, or a clip that would mislead, is replaced by the
next search result. Realistic renders are kept but never open the video.

Optional by construction, like writer.py: with no ANTHROPIC_API_KEY, with
FOOTAGE_MODEL=off, or on any failure, every clip is used as before.
"""

import base64
import json
import os
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from logstream import log
import writer

# A frame is a judgement any current model makes well; Sonnet costs half of
# Opus and answers faster. About a cent for a Short's ten frames.
DEFAULT_MODEL = "claude-sonnet-5-5"
OFF = "off"
# Telling a cartoon from a film needs no deliberation.
EFFORT = "low"
# Thinking counts against it, so it is sized for that and not for the reply.
MAX_TOKENS = 8000
REQUEST_TIMEOUT_SECONDS = 90
# Frames per request. A long video's forty clips go in two requests rather
# than one, keeping each well inside the API's image limits.
BATCH_SIZE = 20
# How many times rejected clips are replaced by the next search results.
# The second round is for replacements that turn out no better.
REPLACEMENT_ROUNDS = 2
# Of the narration, enough to know the story; a long video's script is
# thousands of words, and the subject line carries most of it anyway.
NARRATION_CHARS = 1500

FOOTAGE = "footage"
RENDER = "render"
CARTOON = "cartoon"
KINDS = (FOOTAGE, RENDER, CARTOON)

PROMPT = """\
You are checking stock footage for a short factual video before it is cut
together. Each image is the first frame the viewer will see of one clip,
cropped as it will be shown, and comes with the stock search term that found it.

Video subject: {subject}

Narration:
{narration}

For each clip, decide:
- kind: "footage" for real camera footage; "render" for a realistic 3D render
  or animation (the Earth from space, molecules, a DNA helix); "cartoon" for a
  cartoon, a drawing, a mascot, or an AI-generated character or scene.
- fits: whether a viewer would accept it as illustrating this video. Stock
  footage is always approximate, so generic footage that suits the topic or
  the search term fits: a market for trade, clouds for the atmosphere, a lab
  for research. It does not fit when it would mislead or jar: it shows
  something else that merely shares a word with the search term (a tennis
  court for "supreme court"); it contradicts the story (another country's
  flag or landmarks, a modern rocket or building standing in for a historical
  one the narration names); it is horror or Halloween imagery in a story that
  is neither; or there is nothing to see (blank, blurred, a title card).
- problem: a few words naming what is wrong, or "" when it fits.

{listing}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "clip": {"type": "integer"},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "fits": {"type": "boolean"},
                    "problem": {"type": "string"},
                },
                "required": ["clip", "kind", "fits", "problem"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Frame:
    """One clip as the judge sees it."""

    path: str
    jpeg: bytes
    term: str


@dataclass(frozen=True)
class Verdict:
    kind: str
    fits: bool
    problem: str

    @property
    def keep(self) -> bool:
        return self.fits and self.kind != CARTOON


def model_name() -> str:
    return os.getenv("FOOTAGE_MODEL", "").strip() or DEFAULT_MODEL


def is_enabled() -> bool:
    return writer.is_configured() and model_name().lower() != OFF


def _client():
    import anthropic

    return anthropic.Anthropic(
        api_key=writer.api_key(), timeout=REQUEST_TIMEOUT_SECONDS, max_retries=2
    )


def _ask(frames: Sequence[Frame], subject: str, narration: str) -> Dict[str, Verdict]:
    """Verdicts for one batch, keyed by path. Raises on any failure."""
    listing = "\n".join(
        f"Clip {number}: found by \"{frame.term}\"" for number, frame in enumerate(frames, 1)
    )
    content: List[dict] = [
        {
            "type": "text",
            "text": PROMPT.format(
                subject=subject.strip(),
                narration=narration.strip()[:NARRATION_CHARS],
                listing=listing,
            ),
        }
    ]
    for number, frame in enumerate(frames, 1):
        content.append({"type": "text", "text": f"Clip {number}:"})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(frame.jpeg).decode("ascii"),
                },
            }
        )
    response = _client().messages.create(
        model=model_name(),
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": content}],
        output_config={
            "effort": EFFORT,
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
    )
    if getattr(response, "stop_reason", None) == "refusal":
        raise RuntimeError("the model declined")
    text = next(block.text for block in response.content if block.type == "text")
    verdicts: Dict[str, Verdict] = {}
    for item in json.loads(text)["clips"]:
        number = item["clip"]
        if 1 <= number <= len(frames):
            verdicts[frames[number - 1].path] = Verdict(
                kind=item["kind"], fits=bool(item["fits"]), problem=item["problem"].strip()
            )
    return verdicts


def judge(frames: Sequence[Frame], subject: str, narration: str) -> Dict[str, Verdict]:
    """A verdict per clip, keyed by path. Never raises.

    A clip missing from the result was not judged and is used as it is: a
    batch that fails costs its clips their check, not the video its footage.
    """
    if not frames or not is_enabled():
        return {}
    verdicts: Dict[str, Verdict] = {}
    for start in range(0, len(frames), BATCH_SIZE):
        batch = list(frames[start : start + BATCH_SIZE])
        try:
            verdicts.update(_ask(batch, subject, narration))
        except Exception as err:
            log(
                f"[!] Could not check {len(batch)} clip(s) ({writer.scrub(str(err))}); "
                "using them unchecked.",
                "warning",
            )
    return verdicts


Footage = Dict[str, Tuple[float, float]]


def vet_footage(
    footage: Footage,
    check: Callable[[List[str]], Dict[str, Verdict]],
    replace: Callable[[int], Footage],
    describe: Callable[[str], str] = lambda path: path,
    rounds: int = REPLACEMENT_ROUNDS,
) -> Tuple[Footage, Set[str]]:
    """The footage to use, and which of it may open the video.

    `footage` is find_usable_footage's map of path to (start, seconds).
    `check` judges paths (judge, with the frames taken); `replace` fetches up
    to n more clips and returns their footage, possibly fewer or none. Clips
    the check rejects are dropped and replaced, for up to `rounds` rounds.

    Openers are the clips judged real footage. Clips nobody judged count as
    openers too, so a check that never ran changes nothing. If every clip is
    rejected and nothing replaces them, they are all used after all: a video
    with poor footage beats no video.
    """
    kept: Footage = {}
    openers: Set[str] = set()
    rejected: Footage = {}
    pending = dict(footage)
    for round_number in range(rounds + 1):
        verdicts = check(list(pending)) if pending else {}
        dropped = 0
        for path, window in pending.items():
            verdict = verdicts.get(path)
            if verdict is None or verdict.keep:
                kept[path] = window
                if verdict is None or verdict.kind == FOOTAGE:
                    openers.add(path)
                continue
            dropped += 1
            rejected[path] = window
            log(
                f"[!] Not using {describe(path)}: {verdict.kind}"
                + (f", {verdict.problem}" if verdict.problem else "")
                + ".",
                "warning",
            )
        if not dropped or round_number == rounds:
            break
        pending = replace(dropped)
        if not pending:
            break
        log(f"[+] Checking {len(pending)} replacement clip(s).", "info")

    if not kept:
        log("[!] The footage check rejected every clip; using them anyway.", "warning")
        return dict(rejected), set(rejected)
    return kept, openers
