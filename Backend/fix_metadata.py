"""Repair the metadata of an already-published video.

The pipeline sets title, description and tags at insert time, so this is not
part of any automated path. It exists for videos that went up before a
metadata bug was fixed, and it is deliberately a separate entry point: a
`videos.update` is the one call in this project that can degrade a live video,
and it should take a person deciding to run it.

Run:
  uv run python Backend/fix_metadata.py <video_id> --print
  uv run python Backend/fix_metadata.py <video_id> --tags "a, b, c" --append-hashtags
"""

import argparse
import sys
from typing import List, Optional

from dotenv import load_dotenv

from gpt import append_hashtags, build_hashtags
from utils import ENV_FILE
from youtube import get_video_snippet, update_video_metadata


def parse_tags(raw: Optional[str]) -> Optional[List[str]]:
    """Comma-separated tags, or None when the flag was not given."""
    if raw is None:
        return None
    return [tag.strip() for tag in raw.split(",") if tag.strip()]


def describe(snippet: dict) -> str:
    tags = snippet.get("tags") or []
    return (
        f"title:       {snippet.get('title')}\n"
        f"categoryId:  {snippet.get('categoryId')}\n"
        f"tags ({len(tags)}):    {', '.join(tags) if tags else '(none)'}\n"
        f"description:\n{snippet.get('description') or '(empty)'}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video_id")
    parser.add_argument("--print", action="store_true", help="show current metadata and exit")
    parser.add_argument("--title")
    parser.add_argument("--description")
    parser.add_argument("--tags", help='comma-separated, e.g. "space, plants"')
    parser.add_argument(
        "--append-hashtags",
        action="store_true",
        help="append hashtags derived from the tags to the description",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    load_dotenv(ENV_FILE)
    args = build_parser().parse_args(argv)

    snippet = get_video_snippet(args.video_id)
    if args.print:
        print(describe(snippet))
        return 0

    tags = parse_tags(args.tags)
    description = args.description
    if args.append_hashtags:
        # Built from the tags being written, falling back to the ones already
        # on the video, so this never invents hashtags for a different subject.
        source_tags = tags if tags is not None else list(snippet.get("tags") or [])
        hashtags = build_hashtags(source_tags, snippet.get("title") or "", ())
        base = description if description is not None else snippet.get("description") or ""
        description = append_hashtags(base, hashtags)

    if args.title is None and description is None and tags is None:
        print("Nothing to change. Pass --title, --description, --tags or --append-hashtags.")
        return 1

    written = update_video_metadata(
        args.video_id, title=args.title, description=description, tags=tags
    )
    print(describe(written))
    return 0


if __name__ == "__main__":
    sys.exit(main())
