"""Refuse to start on the empty mounts Docker creates while WSL is down.

On Windows, Docker Desktop resolves a bind mount's host path when a container
starts. If the WSL integration is not up yet, the path does not resolve, and
Docker mounts a freshly created empty directory instead — no error, no warning,
a container that reports healthy and simply cannot see its files.

It has happened twice. On 2026-09-09 only the autopilot came up blind, and
logged missing credentials on every tick. On 2026-09-15, after a Windows Update
reboot, every service did, and the autopilot's catch-up job ran inside the
outage: it built a video with no music, could not upload it, and wrote it into
the container layer, where recreating the container would have deleted it.

Nothing further in can tell an empty mount from a file that genuinely is not
there. A missing token is "upload skipped" and missing songs are "no music",
both deliberately non-fatal, so the pipeline carries on and produces a worse
video without complaint. The only place to catch it is before any work starts.

Opt-in, because a deployment without YouTube or without a music library is a
legitimate one and has to keep starting. Set REQUIRE_MOUNTS=true where the
token and the songs are known to exist.
"""

import os
import time
from pathlib import Path
from typing import Callable, List

from logstream import log

REQUIRE_MOUNTS_ENV = "REQUIRE_MOUNTS"

# Docker's restart backoff doubles from 100 ms and resets once a container has
# stayed up for ten seconds. Pausing here before exiting holds a failed start to
# one attempt every half minute, instead of a fast loop, and gives the WSL
# integration that half minute to come back between attempts.
RETRY_DELAY_SECONDS = 30

# One alert per broken container, not one per restart. The marker lives in the
# container's own filesystem: a restart keeps it, so a crash loop does not spam
# Telegram, and recreating the container — the fix — clears it.
ALERT_MARKER = Path("/tmp/.empty-mount-alert-sent")

TRUE_VALUES = ("1", "true", "yes", "on")


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in TRUE_VALUES


def is_enabled() -> bool:
    return _truthy(REQUIRE_MOUNTS_ENV)


def songs_required() -> bool:
    """Whether this deployment's videos are meant to carry a music bed.

    Read from the same switch the autopilot uses, so a channel that runs
    without music does not refuse to start for want of songs.
    """
    return _truthy("AUTOPILOT_USE_MUSIC")


def has_songs(songs_dir: Path) -> bool:
    return songs_dir.is_dir() and any(songs_dir.rglob("*.mp3"))


def has_token(token_file: Path) -> bool:
    return token_file.is_file() and token_file.stat().st_size > 0


def missing(token_file: Path, songs_dir: Path, need_songs: bool) -> List[str]:
    """What a process that is about to do real work cannot see.

    Within one container every bind mount is set up at the same moment, so they
    fail together: when these two are visible, output/ is too, and checking a
    directory whose contents vary for emptiness would only add false alarms.
    """
    problems: List[str] = []
    if not has_token(token_file):
        problems.append(f"no YouTube token at {token_file}")
    if need_songs and not has_songs(songs_dir):
        problems.append(f"no .mp3 files under {songs_dir}")
    return problems


def require_mounts(
    service: str,
    token_file: Path,
    songs_dir: Path,
    need_songs: bool,
    notify: Callable[[str], object],
    sleep: Callable[[float], None] = time.sleep,
    marker: Path = ALERT_MARKER,
) -> None:
    """Returns if the mounts look right; otherwise alerts once and exits.

    Raises SystemExit(1) so Docker's restart policy tries again. If the
    integration has come back by then, the next start passes on its own; if
    not, the alert has already said what to run.
    """
    if not is_enabled():
        return

    problems = missing(token_file, songs_dir, need_songs)
    if not problems:
        checked = "token" + (" and songs" if need_songs else "")
        log(f"[+] Startup check passed: {checked} visible.", "success")
        return

    detail = "; ".join(problems)
    log(
        f"[-] {service} refuses to start: {detail}. The host files are probably "
        f"fine and this container got empty mounts while WSL integration was "
        f"down (RUNBOOK §2.3a). Retrying in {RETRY_DELAY_SECONDS}s; if it keeps "
        f"failing, recreate: docker compose up -d --force-recreate {service}",
        "error",
    )

    if not marker.exists():
        notify(
            f"⚠️ {service} refuses to start: {detail}.\n"
            f"Almost certainly empty mounts after a WSL/Docker restart — nothing "
            f"will be built or uploaded until it is fixed.\n"
            f"Fix: docker compose up -d --force-recreate {service}"
        )
        try:
            marker.touch()
        except OSError:
            # A read-only /tmp only costs a repeated alert, never the check.
            pass

    sleep(RETRY_DELAY_SECONDS)
    raise SystemExit(1)
