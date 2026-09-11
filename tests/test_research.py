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
    # limit is what survives filtering; the API is always asked for a full page.
    assert recorder[0]["json"] == {
        "query": "why do we hiccup",
        "limit": research.SEARCH_PAGE_SIZE,
    }


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
    # The cap applies across queries: one search now returns at most `limit`,
    # so reaching BRIEF_MAX_SOURCES takes several of them.
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-abc")

    def page(start):
        return {
            "data": {
                "web": [
                    {"url": f"https://e.org/{i}", "title": "t", "description": "text"}
                    for i in range(start, start + 8)
                ]
            }
        }

    responses = [page(0), page(8), page(16)]
    calls = {"n": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        response = _FakeResponse(responses[min(calls["n"], len(responses) - 1)])
        calls["n"] += 1
        return response

    monkeypatch.setattr(research.requests, "post", fake_post)

    assert len(gather(["a", "b", "c"])) == research.BRIEF_MAX_SOURCES


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


# -- source quality ---------------------------------------------------------


def test_host_of_strips_www_and_lowercases():
    assert research.host_of("https://WWW.Example.ORG/a") == "example.org"


def test_host_of_returns_empty_for_junk():
    assert research.host_of("not a url") == ""


@pytest.mark.parametrize(
    "url",
    [
        "https://www.facebook.com/groups/123/posts/456",
        "https://reddit.com/r/space/comments/abc",
        "https://old.reddit.com/r/space/comments/abc",
        "https://www.youtube.com/watch?v=abc",
        "https://youtu.be/abc",
        "https://x.com/someone/status/1",
        "https://www.quora.com/Why-do-we-hiccup",
    ],
)
def test_social_sources_are_excluded(url):
    # A Facebook post under "Sources:" costs exactly the credibility the
    # citation was there to buy.
    assert research.is_allowed(url) is False


@pytest.mark.parametrize(
    "url",
    [
        "https://www.nasa.gov/growing-plants-in-space/",
        "https://www.ars.usda.gov/oc/utm/growing-plants-in-space/",
        "https://en.wikipedia.org/wiki/Plants_in_space",
        "https://space.stackexchange.com/questions/31762/x",
        "https://notyoutube.com/article",
    ],
)
def test_real_sources_are_kept(url):
    assert research.is_allowed(url) is True


def test_search_drops_excluded_domains(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-abc")
    payload = {
        "data": {
            "web": [
                {"url": "https://www.nasa.gov/a", "title": "NASA", "description": "Real."},
                {"url": "https://www.reddit.com/r/x/1", "title": "Thread", "description": "A comment."},
                {"url": "https://www.facebook.com/groups/1", "title": "Post", "description": "A post."},
            ]
        }
    }
    _patch_post(monkeypatch, payload)

    assert [s.url for s in search("q")] == ["https://www.nasa.gov/a"]


def test_clean_url_strips_tracking_parameters():
    dirty = "https://e.org/a?srsltid=AfmBOor&utm_source=x&UTM_medium=y&id=7"
    assert research.clean_url(dirty) == "https://e.org/a?id=7"


def test_clean_url_leaves_a_clean_url_alone():
    assert research.clean_url("https://e.org/a?id=7") == "https://e.org/a?id=7"


def test_clean_url_drops_a_query_that_was_only_tracking():
    assert research.clean_url("https://e.org/a?gclid=abc") == "https://e.org/a"


def test_search_cleans_the_urls_it_returns(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-abc")
    payload = {
        "data": {
            "web": [
                {"url": "https://e.org/a?srsltid=AfmBOor", "title": "t", "description": "real"}
            ]
        }
    }
    _patch_post(monkeypatch, payload)

    assert search("q")[0].url == "https://e.org/a"


def test_gather_deduplicates_urls_that_differ_only_by_tracking(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-abc")
    payload = {
        "data": {
            "web": [
                {"url": "https://e.org/a?utm_source=one", "title": "t", "description": "x"},
                {"url": "https://e.org/a?utm_source=two", "title": "t", "description": "x"},
            ]
        }
    }
    _patch_post(monkeypatch, payload)

    assert len(gather(["q"])) == 1


def test_search_asks_for_a_full_page_whatever_the_limit(monkeypatch):
    # Filtering removes most results and a page of ten costs the same two
    # credits as a page of five, so the API is always asked for the full page.
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-abc")
    recorder: list = []
    _patch_post(monkeypatch, RESPONSE, recorder=recorder)

    search("q", limit=2)

    assert recorder[0]["json"]["limit"] == research.SEARCH_PAGE_SIZE


def test_search_returns_no_more_than_the_limit_asks_for(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-abc")
    _patch_post(monkeypatch, RESPONSE)

    assert len(search("q", limit=1)) == 1


def test_search_says_so_when_everything_was_filtered_out(monkeypatch, capsys):
    # The real case: eight results, six of them Facebook, Reddit, YouTube,
    # Instagram and Pinterest.
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-abc")
    payload = {
        "data": {
            "web": [
                {"url": "https://reddit.com/r/x/1", "title": "t", "description": "d"},
                {"url": "https://facebook.com/g/1", "title": "t", "description": "d"},
            ]
        }
    }
    _patch_post(monkeypatch, payload)

    assert search("mushroom umbrellas") == []
    assert "were social or unusable" in capsys.readouterr().out


def test_min_usable_sources_is_more_than_one():
    # One thin source is worse than none: enough to make the model feel
    # grounded, not enough to hold it up.
    assert research.MIN_USABLE_SOURCES >= 2
