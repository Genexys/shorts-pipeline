"""Per-video numbers from the YouTube Analytics API.

Deliberately separate from `youtube.py`. It is a different service with a
different scope and a different client, and its failures must never reach the
upload path: a channel with no processed analytics still has to publish.

**Absent means unprocessed, not zero.** `reports.query` returns only finalized
data, roughly three days behind. A video uploaded yesterday has no row at all,
which is indistinguishable from a video nobody watched unless the caller keeps
the difference. So a video that is missing from the response is missing from
the returned mapping — never present with zeroes. Callers that need "did this
video get no views" must ask about a video old enough to have settled.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Dict, Iterator, List, Optional, Sequence

from googleapiclient.discovery import Resource, build

from logstream import log
from youtube import ANALYTICS_SCOPE, YouTubeAuthError, has_scope, load_credentials

ANALYTICS_API_SERVICE_NAME = "youtubeAnalytics"
ANALYTICS_API_VERSION = "v2"

# The owner's own channel. This project never reads anyone else's.
CHANNEL_IDS = "channel==MINE"

RETENTION_METRICS = ("views", "averageViewPercentage", "averageViewDuration")

# Thumbnail impressions and click-through rate are NOT available here. Studio
# shows them, but reports.query rejects both `impressions` and
# `impressionClickThroughRate` with "Unknown identifier" — verified against the
# live API on 2026-09-09, with a query shape the same call accepts for `views`.
# Only the bulk Reporting API carries them, which is a separate service with
# daily generated jobs. So long form ranks on view duration alone; there is no
# CTR to rank by.

# The `video==` filter accepts up to 500 ids. Well under it, because a rejected
# batch costs a whole refresh and this is not a hot path.
MAX_IDS_PER_QUERY = 200


@dataclass(frozen=True)
class VideoMetrics:
    """One video's settled performance."""

    video_id: str
    views: int
    average_view_percentage: float
    average_view_duration: float
    measured_at: datetime


def batched(items: Sequence[str], size: Optional[int] = None) -> Iterator[List[str]]:
    """Splits ids into query-sized chunks. Empty input yields nothing.

    The default is resolved here rather than in the signature, where it would
    bind MAX_IDS_PER_QUERY once at import and quietly ignore any later change
    to it.
    """
    size = MAX_IDS_PER_QUERY if size is None else size
    if size < 1:
        raise ValueError("size must be at least 1")
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def get_analytics_service() -> Resource:
    """Authenticated Analytics client.

    Raises:
        YouTubeAuthError: If there is no token, or it predates the analytics
            scope. Reported rather than attempted, because the API's own error
            for a missing scope is opaque.
    """
    credentials = load_credentials()
    if credentials is None:
        raise YouTubeAuthError("No valid YouTube credentials.")
    if not has_scope(credentials, ANALYTICS_SCOPE):
        raise YouTubeAuthError(
            "The saved token was not granted the analytics scope. Re-run "
            "Backend/youtube_auth.py to mint a token that includes "
            f"{ANALYTICS_SCOPE}."
        )
    return build(
        ANALYTICS_API_SERVICE_NAME,
        ANALYTICS_API_VERSION,
        credentials=credentials,
        cache_discovery=False,
    )


def parse_report(response: dict) -> Dict[str, Dict[str, float]]:
    """Turns one reports.query response into {video_id: {metric: value}}.

    Reads values by column name rather than by position: the API is documented
    to return the columns in the requested order, but a report that quietly
    reorders them would corrupt every number rather than fail.
    """
    headers = [column.get("name") for column in response.get("columnHeaders") or []]
    if "video" not in headers:
        return {}
    video_index = headers.index("video")

    parsed: Dict[str, Dict[str, float]] = {}
    for row in response.get("rows") or []:
        if len(row) != len(headers):
            continue
        video_id = row[video_index]
        if not isinstance(video_id, str):
            continue
        values = {
            name: row[index]
            for index, name in enumerate(headers)
            if name and name != "video"
        }
        parsed[video_id] = values
    return parsed


def _query(
    service: Resource, metrics: Sequence[str], video_ids: Sequence[str],
    since: date, until: date,
) -> dict:
    # sort and maxResults are not optional decoration: a `video` dimension
    # query without them is rejected outright.
    return (
        service.reports()
        .query(
            ids=CHANNEL_IDS,
            startDate=since.isoformat(),
            endDate=until.isoformat(),
            metrics=",".join(metrics),
            dimensions="video",
            filters="video==" + ",".join(video_ids),
            sort="-views",
            maxResults=len(video_ids),
        )
        .execute()
    )


def fetch_metrics(
    video_ids: Sequence[str],
    since: date,
    until: Optional[date] = None,
    service: Optional[Resource] = None,
) -> Dict[str, VideoMetrics]:
    """Settled metrics for a batch of videos.

    Args:
        video_ids: Videos to ask about. Order is not preserved.
        since: First day of the reporting window.
        until: Last day, defaulting to today. Days newer than the API's
            processing lag simply contribute no rows.
        service: Injected client, for tests.

    Returns:
        Dict[str, VideoMetrics]: Keyed by video id. **Videos with no processed
            data are absent from this mapping rather than present with zeroes.**

    Raises:
        YouTubeAuthError: If credentials are missing or lack the scope.
    """
    if not video_ids:
        return {}
    until = until or datetime.now(timezone.utc).date()
    client = service or get_analytics_service()
    measured_at = datetime.now(timezone.utc)

    results: Dict[str, VideoMetrics] = {}
    for batch in batched(list(video_ids)):
        retention = parse_report(_query(client, RETENTION_METRICS, batch, since, until))
        for video_id, values in retention.items():
            results[video_id] = VideoMetrics(
                video_id=video_id,
                views=int(values.get("views") or 0),
                average_view_percentage=float(values.get("averageViewPercentage") or 0.0),
                average_view_duration=float(values.get("averageViewDuration") or 0.0),
                measured_at=measured_at,
            )

    missing = [video_id for video_id in video_ids if video_id not in results]
    if missing:
        # Said out loud because the silent version of this is what produced a
        # wrong "nobody watched it" reading on 2026-09-08.
        log(
            f"[*] {len(missing)} video(s) have no processed analytics yet "
            f"(the API runs about three days behind): {', '.join(missing[:5])}"
            f"{' ...' if len(missing) > 5 else ''}",
            "info",
        )
    return results
