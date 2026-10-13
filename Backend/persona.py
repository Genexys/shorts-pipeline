"""On-screen personas: a character who tells the facts, with its own voice,
captions and mascot.

The channel's narrator was a calm documentary voice, and viewers swiped past
more than half of the Shorts within seconds (median engaged share 0.45 in the
first month). A persona is a bet that a character holds attention better. It
is chosen per video by the autopilot (AUTOPILOT_PERSONA_SHARE), so it can be
measured against the plain narrator rather than adopted on faith.

The facts do not change with the character. The persona's rules say so to the
writer, and the fact check runs on its script like on any other.

One persona so far, picked by the owner on 2026-10-12 from four samples: the
Fact Arena announcer, voiced by "Xavier" from the ElevenLabs library, with
burning captions and the TITAN robot in the corner.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from logstream import log
from speech import sentence_starts, split_sentences
from utils import PROJECT_ROOT

ARENA = "arena"

TALKING = "talking"
HYPED = "hyped"
IMPRESSED = "impressed"
CELEBRATING = "celebrating"


@dataclass(frozen=True)
class Persona:
    name: str
    # Appended to the script prompt, after the register's rules.
    rules: str
    # ElevenLabs voice and stability for v4. Lower stability lets the voice act.
    voice_id: str
    stability: float
    # The persona's delivery against the calm narrator's. The arena announcer
    # read 80 words in 50.6 s, past the 40 s ceiling; 64 words ran 36.5 s. The
    # word target and the word ceiling are both scaled by this.
    pace: float
    # video.CAPTION_STYLES key.
    caption_style: str
    mascot_dir: Path
    poses: Tuple[str, ...]
    default_pose: str


PERSONAS = {
    ARENA: Persona(
        name="Fact Arena announcer",
        rules="""
    You are the Fact Arena announcer: a booming, retro-futuristic tournament
    announcer who calls everyday science like a championship match. Short,
    punchy, hyped lines; dramatic build-ups; call the key fact like a final
    blow. End with: "FACT... CONFIRMED." The drama is in the delivery, never
    in the facts: every claim stays exactly as true as the sources say.
""",
        # "Xavier - Dominating, Metalic Announcer", a professional (verified)
        # clone in the public ElevenLabs library, 730-day notice period.
        voice_id="YOq2y2Up4RgXP2HyXjE5",
        stability=0.35,
        pace=0.8,
        caption_style="fire",
        mascot_dir=PROJECT_ROOT / "assets" / "mascot" / "titan",
        poses=(TALKING, HYPED, IMPRESSED, CELEBRATING),
        default_pose=TALKING,
    ),
}


def get(name: Optional[str]) -> Optional[Persona]:
    return PERSONAS.get((name or "").strip().lower())


def pose_path(persona: Persona, pose: str) -> Path:
    return persona.mascot_dir / f"{pose}.png"


POSE_PROMPT = """
A mascot reacts on screen while this narration is read, one pose per
sentence. Poses:
- talking: ordinary narration, setting things up or explaining a step.
- hyped: the surprising fact itself, or a big claim called like a final blow.
- impressed: a twist, a "wait, really?" moment, a correction of what people think.
- celebrating: the payoff and the sign-off.

Sentences:
{sentences}

Return ONLY a JSON object with one pose per sentence, in order:
{{"poses": ["...", "..."]}}
"""


def fallback_poses(count: int) -> List[str]:
    """Hyped on the hook, celebrating on the sign-off, talking between."""
    if count <= 0:
        return []
    poses = [TALKING] * count
    poses[0] = HYPED
    if count > 1:
        poses[-1] = CELEBRATING
    return poses


def choose_poses(persona: Persona, sentences: Sequence[str], ask) -> List[str]:
    """A pose per sentence, from the model, or the fallback. Never raises."""
    if not sentences:
        return []
    numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(sentences, 1))
    try:
        reply = ask(POSE_PROMPT.format(sentences=numbered), "")
        match = re.search(r"\{.*\}", reply or "", re.DOTALL)
        poses = json.loads(match.group())["poses"] if match else None
    except Exception as err:
        log(f"[!] Could not choose mascot poses ({err}); using the default order.", "warning")
        poses = None
    if not isinstance(poses, list) or len(poses) != len(sentences):
        return fallback_poses(len(sentences))
    return [p if p in persona.poses else persona.default_pose for p in poses]


def pose_plan(
    persona: Persona,
    script: str,
    cues: Sequence[Tuple[float, float, str]],
    duration: float,
    ask,
) -> List[Tuple[Path, float, float]]:
    """(pose image, start, end) covering the whole video, one per sentence."""
    sentences = [s for block in re.split(r"\n\s*\n", script or "") for s in split_sentences(block)]
    if not sentences or duration <= 0:
        return []
    poses = choose_poses(persona, sentences, ask)
    starts = sentence_starts(sentences, cues)
    starts[0] = 0.0
    plan = []
    for i, (pose, start) in enumerate(zip(poses, starts)):
        end = starts[i + 1] if i + 1 < len(starts) else duration
        if end > start:
            plan.append((pose_path(persona, pose), round(start, 2), round(end, 2)))
    log("[+] Mascot poses: " + ", ".join(f"{p.stem}@{s:.1f}s" for p, s, _ in plan), "info")
    return plan


def read_cues(srt_path: str) -> List[Tuple[float, float, str]]:
    """(start, end, text) for every cue in an SRT file; [] if it cannot be read."""
    import srt

    try:
        subs = list(srt.parse(Path(srt_path).read_text(encoding="utf-8")))
    except Exception:
        return []
    return [(s.start.total_seconds(), s.end.total_seconds(), s.content) for s in subs]
