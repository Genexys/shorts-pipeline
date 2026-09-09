from datetime import date

import pytest

import analytics
from analytics import batched, fetch_metrics, parse_report

# Shaped like a real reports.query response: columnHeaders describing the
# columns, rows as positional lists.
RETENTION_RESPONSE = {
    "columnHeaders": [
        {"name": "video", "columnType": "DIMENSION", "dataType": "STRING"},
        {"name": "views", "columnType": "METRIC", "dataType": "INTEGER"},
        {"name": "averageViewPercentage", "columnType": "METRIC", "dataType": "FLOAT"},
        {"name": "averageViewDuration", "columnType": "METRIC", "dataType": "INTEGER"},
    ],
    "rows": [
        ["abc123", 835, 62.5, 31],
        ["def456", 12, 41.0, 19],
    ],
}

SINCE = date(2026, 9, 1)
UNTIL = date(2026, 9, 8)


class _FakeRequest:
    def __init__(self, response):
        self._response = response

    def execute(self):
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _FakeReports:
    def __init__(self, responses, recorder):
        self._responses = list(responses)
        self._recorder = recorder

    def query(self, **kwargs):
        self._recorder.append(kwargs)
        return _FakeRequest(self._responses.pop(0))


class _FakeService:
    def __init__(self, responses, recorder):
        self._reports = _FakeReports(responses, recorder)

    def reports(self):
        return self._reports


# -- batching ---------------------------------------------------------------


def test_batched_splits_into_chunks():
    assert list(batched(["a", "b", "c"], 2)) == [["a", "b"], ["c"]]


def test_batched_yields_nothing_for_no_ids():
    assert list(batched([], 10)) == []


def test_batched_keeps_a_short_list_whole():
    assert list(batched(["a", "b"], 200)) == [["a", "b"]]


def test_batched_rejects_a_useless_size():
    with pytest.raises(ValueError):
        list(batched(["a"], 0))


# -- parsing ----------------------------------------------------------------


def test_parse_report_maps_videos_to_named_metrics():
    assert parse_report(RETENTION_RESPONSE) == {
        "abc123": {"views": 835, "averageViewPercentage": 62.5, "averageViewDuration": 31},
        "def456": {"views": 12, "averageViewPercentage": 41.0, "averageViewDuration": 19},
    }


def test_parse_report_reads_by_column_name_not_position():
    # Same data, columns in a different order.
    reordered = {
        "columnHeaders": [
            {"name": "views"},
            {"name": "video"},
            {"name": "averageViewPercentage"},
        ],
        "rows": [[835, "abc123", 62.5]],
    }
    assert parse_report(reordered) == {
        "abc123": {"views": 835, "averageViewPercentage": 62.5}
    }


def test_parse_report_handles_a_response_with_no_rows():
    assert parse_report({"columnHeaders": [{"name": "video"}], "rows": []}) == {}


def test_parse_report_handles_an_empty_response():
    assert parse_report({}) == {}


def test_parse_report_ignores_a_report_without_the_video_dimension():
    assert parse_report({"columnHeaders": [{"name": "views"}], "rows": [[5]]}) == {}


def test_parse_report_skips_a_malformed_row():
    response = {
        "columnHeaders": [{"name": "video"}, {"name": "views"}],
        "rows": [["abc123", 835], ["truncated"]],
    }
    assert parse_report(response) == {"abc123": {"views": 835}}


# -- fetch ----------------------------------------------------------


def test_fetch_metrics_parses_retention():
    service = _FakeService([RETENTION_RESPONSE], [])

    metrics = fetch_metrics(["abc123", "def456"], SINCE, UNTIL, service=service)

    assert metrics["abc123"].views == 835
    assert metrics["abc123"].average_view_percentage == 62.5
    assert metrics["abc123"].average_view_duration == 31
    assert metrics["def456"].views == 12


def test_fetch_metrics_omits_videos_with_no_processed_data():
    # The whole point: a video the API has not processed must be absent, not
    # present with zeroes, or a fresh upload reads as a failure.
    service = _FakeService([RETENTION_RESPONSE], [])

    metrics = fetch_metrics(["abc123", "def456", "brandnew"], SINCE, UNTIL, service=service)

    assert "brandnew" not in metrics
    assert set(metrics) == {"abc123", "def456"}


def test_fetch_metrics_returns_early_without_ids():
    # No client is built, so an unauthenticated caller asking about nothing
    # gets an empty answer rather than an auth error.
    assert fetch_metrics([], SINCE) == {}


def test_fetch_metrics_queries_the_owner_channel_and_the_window():
    recorder: list = []
    service = _FakeService([RETENTION_RESPONSE], recorder)

    fetch_metrics(["abc123"], SINCE, UNTIL, service=service)

    assert len(recorder) == 1
    assert recorder[0]["ids"] == "channel==MINE"
    assert recorder[0]["startDate"] == "2026-09-01"
    assert recorder[0]["endDate"] == "2026-09-08"
    assert recorder[0]["dimensions"] == "video"
    assert recorder[0]["filters"] == "video==abc123"
    assert recorder[0]["metrics"] == "views,averageViewPercentage,averageViewDuration"


def test_fetch_metrics_sends_the_sort_and_limit_the_api_requires():
    # A `video` dimension query without them is rejected outright; verified
    # against the live API on 2026-09-09.
    recorder: list = []
    service = _FakeService([RETENTION_RESPONSE], recorder)

    fetch_metrics(["abc123", "def456"], SINCE, UNTIL, service=service)

    assert recorder[0]["sort"] == "-views"
    assert recorder[0]["maxResults"] == 2


def test_fetch_metrics_asks_for_no_metric_the_api_rejects():
    # `impressions` and `impressionClickThroughRate` do not exist in
    # reports.query, whatever Studio shows. Asking for either fails the whole
    # query, taking the retention numbers down with it.
    assert "impressions" not in ",".join(analytics.RETENTION_METRICS)


def test_fetch_metrics_batches_large_id_lists(monkeypatch):
    monkeypatch.setattr(analytics, "MAX_IDS_PER_QUERY", 2)
    recorder: list = []
    empty = {"columnHeaders": [{"name": "video"}], "rows": []}
    service = _FakeService([empty] * 2, recorder)

    fetch_metrics(["a", "b", "c"], SINCE, UNTIL, service=service)

    assert len(recorder) == 2
    assert recorder[0]["filters"] == "video==a,b"
    assert recorder[1]["filters"] == "video==c"


def test_get_analytics_service_refuses_a_token_without_the_scope(monkeypatch):
    class UploadOnly:
        scopes = ["https://www.googleapis.com/auth/youtube.upload"]

    monkeypatch.setattr(analytics, "load_credentials", lambda *a, **k: UploadOnly())

    with pytest.raises(analytics.YouTubeAuthError):
        analytics.get_analytics_service()


def test_get_analytics_service_refuses_a_missing_token(monkeypatch):
    monkeypatch.setattr(analytics, "load_credentials", lambda *a, **k: None)

    with pytest.raises(analytics.YouTubeAuthError):
        analytics.get_analytics_service()
