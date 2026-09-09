import pytest

import research
from research import Source, append_sources, format_brief, gather, search, source_lines

RESPONSE = {
    "success": True,
    "data": {
        "web": [
            {
                "url": "https://example.org/a",
                "title": "Handedness across cultures",
                "description": "  Measured rates run   about 10% in western Europe.  ",
            },
            {
                "url": "https://example.org/b",
                "title": "Cohort study",
                "description": "Hazard ratio 1.52.",
            },
        ]
    },
}


class _FakeResponse:
    def __init__(self, payload, error=None):
        self._payload = payload
        self._error = error

    def raise_for_status(self):
        if self._error:
            raise self._error

    def json(self):
        return self._payload


def _patch_post(monkeypatch, payload, error=None, recorder=None):
    def fake_post(url, headers=None, json=None, timeout=None):
        if recorder is not None:
            recorder.append({"url": url, "headers": headers, "json": json})
        return _FakeResponse(payload, error)

    monkeypatch.setattr(research.requests, "post", fake_post)


# -- configuration ----------------------------------------------------------


def test_is_configured_follows_the_key(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-abc")
    assert research.is_configured() is True
    monkeypatch.setenv("FIRECRAWL_API_KEY", "   ")
    assert research.is_configured() is False


def test_search_returns_nothing_without_a_key(monkeypatch):
    # And makes no request at all: an unkeyed call would just burn a timeout.
    monkeypatch.setenv("FIRECRAWL_API_KEY", "")

    def explode(*args, **kwargs):
        raise AssertionError("should not have been called")

    monkeypatch.setattr(research.requests, "post", explode)
    assert search("anything") == []


# -- parsing ----------------------------------------------------------------


def test_search_parses_results(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-abc")
    _patch_post(monkeypatch, RESPONSE)

    sources = search("handedness")

    assert [s.url for s in sources] == ["https://example.org/a", "https://example.org/b"]
    assert sources[0].snippet == "Measured rates run about 10% in western Europe."


def test_search_sends_the_key_and_the_query(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-abc")
    recorder: list = []
    _patch_post(monkeypatch, RESPONSE, recorder=recorder)

    search("why do we hiccup", limit=3)

    assert recorder[0]["url"] == research.SEARCH_URL
    assert recorder[0]["headers"]["Authorization"] == "Bearer fc-abc"
    assert recorder[0]["json"] == {"query": "why do we hiccup", "limit": 3}


def test_search_truncates_a_long_snippet(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-abc")
    payload = {"data": {"web": [{"url": "https://e.org/x", "title": "t", "description": "y" * 900}]}}
    _patch_post(monkeypatch, payload)

    assert len(search("q")[0].snippet) == research.SNIPPET_MAX_CHARS


def test_search_skips_items_with_no_usable_content(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-abc")
    payload = {
        "data": {
            "web": [
                {"url": "https://e.org/ok", "title": "t", "description": "real"},
                {"url": "https://e.org/empty", "title": "t", "description": "   "},
                {"url": "not-a-url", "description": "text"},
                "junk",
            ]
        }
    }
    _patch_post(monkeypatch, payload)

    assert [s.url for s in search("q")] == ["https://e.org/ok"]


def test_search_falls_back_to_markdown_when_there_is_no_description(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-abc")
    payload = {"data": {"web": [{"url": "https://e.org/x", "title": "t", "markdown": "# Body text"}]}}
    _patch_post(monkeypatch, payload)

    assert search("q")[0].snippet == "# Body text"


# -- failure is never fatal -------------------------------------------------


def test_search_survives_a_transport_error(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-abc")

    def fake_post(*args, **kwargs):
        raise research.requests.RequestException("connection reset")

    monkeypatch.setattr(research.requests, "post", fake_post)
    assert search("q") == []


def test_search_survives_an_http_error(monkeypatch):
    # A 402 for exhausted credits and a 429 for rate limiting both land here.
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-abc")
    _patch_post(monkeypatch, {}, error=research.requests.HTTPError("402 Payment Required"))
    assert search("q") == []


def test_search_survives_a_malformed_payload(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-abc")
    _patch_post(monkeypatch, {"data": {"web": "not a list"}})
    assert search("q") == []


# -- gathering --------------------------------------------------------------


def test_gather_deduplicates_across_queries(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-abc")
    _patch_post(monkeypatch, RESPONSE)

    sources = gather(["one", "two"])

    assert [s.url for s in sources] == ["https://example.org/a", "https://example.org/b"]


def test_gather_ignores_blank_queries(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-abc")
    recorder: list = []
    _patch_post(monkeypatch, RESPONSE, recorder=recorder)

    gather(["", "   ", "real"])

    assert len(recorder) == 1


def test_gather_caps_the_brief(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-abc")
    many = {
        "data": {
            "web": [
                {"url": f"https://e.org/{i}", "title": "t", "description": "text"}
                for i in range(40)
            ]
        }
    }
    _patch_post(monkeypatch, many)

    assert len(gather(["q"])) == research.BRIEF_MAX_SOURCES


# -- rendering --------------------------------------------------------------


SOURCES = [
    Source(title="A study", url="https://e.org/a", snippet="Ten percent."),
    Source(title="Another", url="https://e.org/b", snippet="Ratio 1.52."),
]


def test_format_brief_numbers_the_sources():
    assert format_brief(SOURCES) == (
        "[1] A study\nTen percent.\n\n[2] Another\nRatio 1.52."
    )


def test_format_brief_is_empty_without_sources():
    assert format_brief([]) == ""


def test_source_lines_caps_the_list():
    many = [Source(title=f"t{i}", url=f"https://e.org/{i}", snippet="s") for i in range(9)]
    assert len(source_lines(many)) == research.DESCRIPTION_MAX_SOURCES


def test_source_lines_falls_back_to_the_url_as_a_title():
    lines = source_lines([Source(title="", url="https://e.org/a", snippet="s")])
    assert lines == ["https://e.org/a — https://e.org/a"]


def test_append_sources_puts_them_under_the_description():
    result = append_sources("Body text.\n\n#Tag", SOURCES)
    assert result == (
        "Body text.\n\n#Tag\n\nSources:\nA study — https://e.org/a\nAnother — https://e.org/b"
    )


def test_append_sources_leaves_the_description_alone_when_there_are_none():
    assert append_sources("Body text.", []) == "Body text."
