"""Write a community post for a video the pipeline published.

There is no API for posting: the YouTube Data API has no community post
resource. This prints text to paste into Studio by hand.

Run:
  uv run python Backend/make_post.py --latest
  uv run python Backend/make_post.py SQpx4yg1SL8 --kind poll
  uv run python Backend/make_post.py --latest --count 3
"""

import argparse
import os
import sys
from typing import List, Optional

from dotenv import load_dotenv

from db import SessionLocal, init_db
from posts import POST_KINDS, available_kinds, generate_post
from repository import get_research_sources, get_script, list_published_videos
from utils import ENV_FILE


def resolve_video(session, video_id: Optional[str]) -> tuple[str, str, str]:
    """(video_id, job_id, subject) for a given video, or the newest one.

    Raises:
        LookupError: If nothing is published, or the id is not one of ours.
    """
    published = list_published_videos(session)
    if not published:
        raise LookupError("No published videos yet.")

    if video_id is None:
        chosen = published[-1]
    else:
        matches = [row for row in published if row[0] == video_id]
        if not matches:
            raise LookupError(
                f"{video_id} is not a video this pipeline published. "
                f"Known: {', '.join(row[0] for row in published)}"
            )
        chosen = matches[0]

    from models import Topic

    subject = (
        session.query(Topic.subject).filter(Topic.job_id == chosen[1]).scalar() or ""
    )
    return chosen[0], chosen[1], subject


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video_id", nargs="?", help="defaults to the newest video")
    parser.add_argument("--latest", action="store_true", help="explicit form of the default")
    parser.add_argument("--kind", choices=POST_KINDS, help="default: a random kind per post")
    parser.add_argument("--count", type=int, default=1, help="how many to draft (default 1)")
    parser.add_argument("--model", help="override AUTOPILOT_MODEL / OLLAMA_MODEL")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    load_dotenv(ENV_FILE)
    args = build_parser().parse_args(argv)
    init_db()

    model = (
        args.model
        or os.getenv("AUTOPILOT_MODEL", "").strip()
        or os.getenv("OLLAMA_MODEL", "").strip()
        or "llama3.1:8b"
    )

    with SessionLocal() as session:
        try:
            video_id, job_id, subject = resolve_video(session, args.video_id)
        except LookupError as err:
            print(err)
            return 1
        script = get_script(session, job_id) or ""
        stored = get_research_sources(session, job_id)
    notes = "\n\n".join(
        f"[{index}] {row.title or row.url}\n{row.snippet}"
        for index, row in enumerate(stored, 1)
    )

    title = subject or video_id
    print(f"Video:   https://youtu.be/{video_id}")
    print(f"Subject: {subject or '(unknown)'}")
    if not script:
        print("Script:  not stored for this job; writing from the subject only.")
    if not notes:
        # Worth saying plainly: without the notes a fact post can only repeat
        # the script or invent, so that kind is off the table.
        print(
            "Sources: none stored for this job, so only these kinds are "
            f"available: {', '.join(available_kinds(script, notes))}."
        )
    else:
        print(f"Sources: {len(stored)} stored.")
    print()

    written = 0
    for _ in range(max(1, args.count)):
        try:
            post = generate_post(
                subject, title, script, model, kind=args.kind, research=notes
            )
        except ValueError as err:
            print(err)
            return 1
        if post is None:
            continue
        written += 1
        print(f"--- {post.kind} ---")
        print(post.render())
        print()

    if written == 0:
        print("Nothing usable came back. Run it again.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
