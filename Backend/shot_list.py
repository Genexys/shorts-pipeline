"""Footage chosen line by line, and cut to the narration.

A Short used to get four or five search terms for the whole video. Their clips
were downloaded into one pool and cut into shots of a few seconds each, in the
order the searches returned them, so what was on screen had nothing to do with
what was being said at that moment. The twelve Shorts of 2026-10-11 to 10-13
each ran six to ten such shots. A cracked-knuckles video could show an x-ray
while the narrator talked about the sound, and the joint while he talked about
the x-ray.

So the script is split into beats: one sentence, or a few consecutive ones,
that one picture can carry. Each beat gets its own search term, its clips are
found and checked for it, and they play from its first word to the next
beat's, timed from the captions, which carry the script's words on the
narration's timing.

Optional by construction: a plan that cannot be had or does not cover the
script in order returns no beats, and the caller falls back to the pool.
"""

import json
import math
import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

from logstream import log
from speech import _letters, for_narration, sentence_starts, split_sections, split_sentences
from video import effective_clip_cap, plan_clip_segments

# Roughly one beat per this many words, so a 80-word Short gets six to ten.
# Fewer and a beat outlasts its picture; more and a shot is over before the
# viewer has read it.
WORDS_PER_BEAT_LOW = 8
WORDS_PER_BEAT_HIGH = 14
MIN_BEATS = 3
# A beat shorter than this is not given a shot of its own: its time goes to
# the beat before. A cut every second reads as a glitch, not as an edit.
MIN_BEAT_SECONDS = 1.5
# The search pass of a beat's backup term, as stored with its stock clips.
BACKUP_PASS = "beat-backup"

PROMPT = """\
These are the sentences of a short factual video, narrated over stock footage.
Plan the footage: split the sentences into beats, each one picture the viewer
sees while those words are spoken, and give every beat a stock search term.

Video subject: {subject}

Sentences:
{sentences}

Beats:
- A beat is one sentence or a few consecutive ones. Cover every sentence, in
  order, each in exactly one beat.
- Aim for {low} to {high} beats. Start a new beat where the picture should
  change: a new object, place, person or step. Keep a very short sentence with
  its neighbour.

Search terms:
- 1-3 words naming something a camera can film and a stock library would
  have: "chopping onion", "knuckles closeup", "hand x-ray".
- Show what that beat's words are about. When they are about something no
  camera can film (a feeling, a number, a process inside the body), show the
  real thing a viewer would connect with it: the person, the object, the place.
- Take the term from what is meant, never from figures of speech. A script
  that calls an onion's irritant "tear gas" needs onions, not riot footage.
- Never an abstract noun ("mechanism", "process", "system", "memory") and
  never a period scene stock footage will not have (a 1920s laboratory): name
  what can still be filmed, such as the object, the material or the place.
- Every beat a different term, so no two beats show the same clips.
- backup: a second, broader term for the same picture, for when the first
  finds nothing.

Return ONLY a JSON object:
{{"beats": [{{"first": 1, "last": 2, "term": "...", "backup": "..."}}]}}
"""


@dataclass(frozen=True)
class Beat:
    """Consecutive sentences shown over one picture. Indexes are 0-based."""

    first: int
    last: int
    text: str
    term: str
    backup: str

    @property
    def words(self) -> int:
        return len(self.text.split())


def narrated_sentences(script: str) -> List[str]:
    """The sentences as the narrator reads them, which is what the captions show."""
    return [
        sentence
        for section in split_sections(for_narration(script))
        for sentence in split_sentences(section)
    ]


def beat_range(word_count: int, sentence_count: int) -> Tuple[int, int]:
    """How many beats to ask for."""
    high = max(MIN_BEATS, round(word_count / WORDS_PER_BEAT_LOW))
    low = max(MIN_BEATS, round(word_count / WORDS_PER_BEAT_HIGH))
    high = max(1, min(high, sentence_count))
    return min(low, high), high


def parse_beats(reply: str, sentences: Sequence[str]) -> List[Beat]:
    """The beats in a reply, or [] unless they cover every sentence in order."""
    match = re.search(r"\{.*\}", reply or "", re.DOTALL)
    if not match:
        return []
    try:
        items = json.loads(match.group())["beats"]
    except (ValueError, KeyError, TypeError):
        return []
    if not isinstance(items, list):
        return []
    beats: List[Beat] = []
    expected = 1
    for item in items:
        try:
            first, last = int(item["first"]), int(item["last"])
            term = str(item["term"]).strip()
            backup = str(item.get("backup") or "").strip()
        except (KeyError, TypeError, ValueError):
            return []
        if first != expected or last < first or last > len(sentences) or not term:
            return []
        beats.append(
            Beat(
                first=first - 1,
                last=last - 1,
                text=" ".join(sentences[first - 1 : last]),
                term=term,
                backup=backup if backup.lower() != term.lower() else "",
            )
        )
        expected = last + 1
    if expected != len(sentences) + 1:
        return []
    return beats


def plan_beats(subject: str, script: str, ask: Callable[[str, str], str]) -> List[Beat]:
    """The script's beats with a search term each, or [] for the old pool.

    Never raises: footage chosen the old way beats a failed job.
    """
    sentences = narrated_sentences(script)
    if not sentences:
        return []
    low, high = beat_range(sum(len(s.split()) for s in sentences), len(sentences))
    numbered = "\n".join(f"{n}. {sentence}" for n, sentence in enumerate(sentences, 1))
    try:
        reply = ask(
            PROMPT.format(subject=subject.strip(), sentences=numbered, low=low, high=high),
            "",
        )
    except Exception as err:
        log(f"[!] No shot list ({err}); choosing footage for the whole video.", "warning")
        return []
    beats = parse_beats(reply, sentences)
    if not beats:
        log(
            "[!] The shot list did not cover the script in order; choosing footage "
            "for the whole video.",
            "warning",
        )
        return []
    log(
        "[+] Shot list: "
        + " | ".join(
            f"{beat.first + 1}"
            + (f"-{beat.last + 1}" if beat.last > beat.first else "")
            + f' "{beat.term}"'
            for beat in beats
        ),
        "info",
    )
    return beats


def backup_first(entries: list) -> list:
    """A beat's searches with its backup term's first, the rest in order.

    For replacing a rejected clip. Once the footage check has turned one down,
    the next result for the same term is usually more of the same: on
    2026-10-13 "hemlock plant" offered a tree trunk, dahlias and a mossy wall,
    and "henbane flower" a mallow, poppies and a delphinium, so both lines
    lost their footage while the backup terms were never searched.
    """
    return sorted(entries, key=lambda entry: entry["search_pass"] != BACKUP_PASS)


def clips_wanted(beat: Beat, words_per_second: float, max_clip_seconds: float) -> int:
    """Clips to download for a beat: enough that no shot outruns the cap."""
    seconds = beat.words / words_per_second
    return max(1, math.ceil(seconds / max_clip_seconds))


def beat_windows(
    beats: Sequence[Beat],
    sentences: Sequence[str],
    cues: Sequence[Tuple[float, float, str]],
    duration: float,
) -> List[Tuple[float, float]]:
    """(start, end) of each beat, covering 0 to `duration` with no gaps.

    A beat starts with the caption holding its first word. With no captions
    to go by, the time is shared out by letters, which is the narrator's pace
    near enough.
    """
    if cues:
        starts = sentence_starts(sentences, cues)
    else:
        total = sum(_letters(s) for s in sentences) or 1
        starts, before = [], 0
        for sentence in sentences:
            starts.append(duration * before / total)
            before += _letters(sentence)
    beat_starts = [min(max(starts[beat.first], 0.0), duration) for beat in beats]
    if beat_starts:
        beat_starts[0] = 0.0
    for index in range(1, len(beat_starts)):
        beat_starts[index] = max(beat_starts[index], beat_starts[index - 1])
    ends = beat_starts[1:] + [duration]
    return list(zip(beat_starts, ends))


def plan_segments(
    windows: Sequence[Tuple[float, float]],
    clips: Sequence[Sequence[Tuple[str, float]]],
    max_clip_seconds: float,
) -> List[Tuple[str, float]]:
    """The (path, seconds) shots in playing order, one beat after another.

    `clips` holds each beat's (path, usable seconds), in the order they should
    play. A beat with no clips, or too short for a shot, gives its time to the
    beat before it; one at the very start gives it to the beat after.

    Raises:
        ValueError: If no beat has a clip.
    """
    kept: List[list] = []
    carried = 0.0
    for (start, end), sources in zip(windows, clips):
        length = end - start
        if sources and length + carried >= MIN_BEAT_SECONDS and (
            not kept or length >= MIN_BEAT_SECONDS
        ):
            kept.append([length + carried, list(sources)])
            carried = 0.0
        elif kept:
            kept[-1][0] += length
        else:
            carried += length
    if not kept:
        raise ValueError("No beat has a clip to show.")
    kept[-1][0] += carried

    segments: List[Tuple[str, float]] = []
    for length, sources in kept:
        cap = effective_clip_cap(length, len(sources), max_clip_seconds)
        # One even rhythm: the varied one cuts a lone clip short and then
        # shows it again to fill the beat.
        segments.extend(plan_clip_segments(sources, length, cap, rhythm=(1.0,)))
    return segments


def beat_of_each(
    beats: Sequence[Beat], paths: Sequence[str], index_of: Callable[[str], Optional[int]]
) -> List[List[str]]:
    """The paths grouped by beat, in their original order."""
    grouped: List[List[str]] = [[] for _ in beats]
    for path in paths:
        index = index_of(path)
        if index is not None and 0 <= index < len(grouped):
            grouped[index].append(path)
    return grouped
