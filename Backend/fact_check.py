"""A second reading of every script, claim by claim, before it is recorded.

The script writer is told to say only what its sources say, and mostly does.
The misses reached the channel anyway and were caught by hand, after
publishing:
- Doolittle's hands outside the cockpit, which were Kelsey's;
- Kruszelnicki credited with Steinhauser's shaving experiment;
- "Edison's Rival" for an Edison licensee;
- "created the first pacemaker" where the source said "helped create".

Each was a claim the writer stated more firmly, or about someone other than,
its evidence did. A separate pass whose only job is to doubt finds those
before narration, when fixing one costs a rewrite rather than a pinned
correction.

The check lists the script's claims, marks each supported, unsupported or
wrong against the research brief, and returns a corrected script when
anything fails. The correction is read again. What survives that goes up
private for review instead of public.
"""

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from logstream import log

SUPPORTED = "supported"
UNSUPPORTED = "unsupported"
WRONG = "wrong"
VERDICTS = (SUPPORTED, UNSUPPORTED, WRONG)

# How far a corrected script may move from the original's length. Shrinking is
# what removing claims does: on 2026-10-12 a zebra-cow script lost six of its
# claims and went from 65 words to 46, and refusing that would have held the
# video instead of fixing it. Growing is new material, which a correction must
# not add, and the narration ceiling was set on the original.
MAX_SHRINK = 0.45
MAX_GROWTH = 0.15

CHECK_PROMPT = """
You check the narration script of a popular-science YouTube video before it
is recorded. Facts matter more than anything else on this channel: a wrong
claim goes out under its name to thousands of people.

Subject: {subject}

Script:
{script}

Evidence the script was written from (search results, and sentences quoted
verbatim from the pages they came from):
{evidence}

List every factual claim in the script: numbers, dates, names, who did what,
causes, firsts, onlys, records. Judge each one:
- "supported": the evidence says it, or it is uncontroversial common
  knowledge that any encyclopedia states plainly.
- "unsupported": the evidence does not say it and it is not common
  knowledge; or the script says it more strongly than the evidence does
  ("helped create" told as "created", a hypothesis told as settled, a
  correlation told as a cause, "one of the first" told as "the first"); or
  it credits the wrong person, place or year for something the evidence
  credits elsewhere.
- "wrong": the evidence or well-established knowledge contradicts it.

If every claim is supported, return the script unchanged. Otherwise rewrite
it so every claim is supported. Remove a claim, soften it to exactly what
the evidence says, or correct it. Change nothing else: keep the first
sentence unless it carries a bad claim, keep the paragraph breaks, and add
no new facts. It may come out shorter; it must not come out longer.

Return ONLY a JSON object:
{{"claims": [{{"claim": "...", "verdict": "supported|unsupported|wrong", "evidence": "a short quote, or common knowledge", "fix": "what changed, or empty"}}], "script": "..."}}
"""

METADATA_PROMPT = """
A YouTube video's title and description must say nothing its script does
not. The script below has been checked against its sources; the title and
description have not.

Script:
{script}

Title: {title}
Description: {description}

Look only at claims. If the title or the description asserts anything the
script does not, or says it more strongly, rewrite only that, keeping the rest
word for word. Do not shorten, restyle or improve anything else; length and
style are not your concern, and an empty description is fine. "problems"
lists only claims the script does not support, and is empty when there are
none.

Return ONLY a JSON object:
{{"problems": ["..."], "title": "...", "description": "..."}}
"""

_DIGITS = re.compile(r"\d+(?:[.,]\d+)?")


@dataclass
class Claim:
    text: str
    verdict: str
    evidence: str = ""
    fix: str = ""


@dataclass
class ScriptCheck:
    """What the check found, and the script to use."""

    script: str
    claims: List[Claim] = field(default_factory=list)
    # Claims that were not supported in the original.
    fixed: List[Claim] = field(default_factory=list)
    # Claims still not supported in the script that will be used.
    unresolved: List[Claim] = field(default_factory=list)
    checked: bool = False

    def summary(self) -> dict:
        """For the video artifact's metadata."""
        return {
            "checked": self.checked,
            "claims": len(self.claims),
            "fixed": [f"{c.verdict}: {c.text}" for c in self.fixed],
            "unresolved": [f"{c.verdict}: {c.text}" for c in self.unresolved],
        }


def _extract_json(text: str) -> Optional[dict]:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_check(reply: str) -> Optional[tuple]:
    """(claims, script) from a check reply, or None if it is unusable."""
    parsed = _extract_json(reply)
    if parsed is None:
        return None
    claims: List[Claim] = []
    for item in parsed.get("claims") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("claim") or "").strip()
        verdict = str(item.get("verdict") or "").strip().lower()
        if not text or verdict not in VERDICTS:
            continue
        claims.append(
            Claim(text, verdict, str(item.get("evidence") or "").strip(),
                  str(item.get("fix") or "").strip())
        )
    script = parsed.get("script")
    if not isinstance(script, str) or not script.strip():
        return None
    return claims, script.strip()


def problems(claims: Sequence[Claim]) -> List[Claim]:
    return [claim for claim in claims if claim.verdict != SUPPORTED]


def acceptable_revision(original: str, revised: str, evidence: str) -> Optional[str]:
    """Why a corrected script cannot be used, or None if it can.

    A correction may remove and soften. It may not shrink the video by more
    than MAX_SHRINK or grow it by more than MAX_GROWTH, and it may not bring in
    a figure that neither the script nor its evidence had.
    """
    before, after = len(original.split()), len(revised.split())
    if before and (after < before * (1 - MAX_SHRINK) or after > before * (1 + MAX_GROWTH)):
        return f"length moved from {before} to {after} words"
    known = set(_DIGITS.findall(original)) | set(_DIGITS.findall(evidence or ""))
    new = set(_DIGITS.findall(revised)) - known
    if new:
        return f"introduced {', '.join(sorted(new))}"
    return None


def check_script(
    subject: str, script: str, evidence: str, ask
) -> ScriptCheck:
    """Checks a script and returns the one to record.

    `ask` is gpt.generate_response. Never raises: with no model, or an answer
    that cannot be read, the original is returned unchecked and says so, and
    the caller decides what an unchecked script means.
    """
    def run(text: str) -> Optional[tuple]:
        prompt = CHECK_PROMPT.format(
            subject=subject.strip(),
            script=text.strip(),
            evidence=(evidence or "").strip() or "(none: judge against common knowledge only)",
        )
        try:
            return parse_check(ask(prompt, ""))
        except Exception as err:
            log(f"[!] Fact check unavailable ({err}).", "warning")
            return None

    first = run(script)
    if first is None:
        return ScriptCheck(script=script)
    claims, revised = first
    bad = problems(claims)
    for claim in claims:
        log(f"    [{claim.verdict}] {claim.text}", "info" if claim.verdict == SUPPORTED else "warning")
    if not bad:
        log(f"[+] Fact check: {len(claims)} claims, all supported.", "success")
        return ScriptCheck(script=script, claims=claims, checked=True)

    reason = acceptable_revision(script, revised, evidence)
    if reason or revised.strip() == script.strip():
        log(f"[!] Fact check: {len(bad)} claim(s) not supported, and the correction "
            f"cannot be used ({reason or 'it changed nothing'}).", "warning")
        return ScriptCheck(script=script, claims=claims, unresolved=bad, checked=True)

    # The correction is read again: a rewrite is new text, and new text is
    # exactly what this pass exists to doubt.
    second = run(revised)
    if second is None:
        log("[!] Fact check: the corrected script could not be re-checked.", "warning")
        return ScriptCheck(script=revised, claims=claims, fixed=bad, unresolved=bad, checked=True)
    again, _ = second
    still = problems(again)
    log(f"[+] Fact check: corrected {len(bad)} claim(s); "
        f"{len(still)} still unsupported after the rewrite.",
        "warning" if still else "success")
    return ScriptCheck(script=revised, claims=claims, fixed=bad, unresolved=still, checked=True)


def split_hashtags(description: str) -> tuple:
    """(body, hashtag line) of a description; the line is "" when there is none."""
    lines = (description or "").rstrip().split("\n")
    if lines and lines[-1].strip() and all(w.startswith("#") for w in lines[-1].split()):
        return "\n".join(lines[:-1]).rstrip(), lines[-1].strip()
    return (description or "").rstrip(), ""


def check_metadata(script: str, title: str, description: str, ask) -> tuple:
    """(title, description, problems) with anything the script does not say removed.

    Never raises; with no usable answer the inputs come back unchanged.
    """
    # The hashtag line is appended by generate_metadata and is not a claim; it
    # is set aside so the rewrite cannot drop it, and put back after.
    body, tags = split_hashtags(description)
    try:
        reply = ask(METADATA_PROMPT.format(script=script.strip(), title=title,
                                           description=body), "")
    except Exception as err:
        log(f"[!] Metadata check unavailable ({err}).", "warning")
        return title, description, []
    parsed = _extract_json(reply)
    if parsed is None:
        return title, description, []
    found = [str(p).strip() for p in parsed.get("problems") or [] if str(p).strip()]
    if not found:
        return title, description, []
    new_title = str(parsed.get("title") or "").strip() or title
    new_body = str(parsed.get("description") or "").strip() or body
    new_description = f"{new_body}\n\n{tags}" if tags else new_body
    if len(new_title) > 100:
        new_title = title
    for problem in found:
        log(f"[!] Metadata check: {problem}", "warning")
    return new_title, new_description, found
