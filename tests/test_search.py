import search


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self):
        return self._payload


PEXELS_PAYLOAD = {
    "videos": [
        {
            "duration": 20,
            "video_files": [
                {"link": "https://x.com/video-files/small.mp4", "width": 640, "height": 360},
                {"link": "https://x.com/video-files/big.mp4", "width": 1920, "height": 1080},
                {"link": "https://x.com/preview.jpg", "width": 4000, "height": 4000},
            ],
        },
        {"duration": 3, "video_files": [{"link": "https://x.com/video-files/short.mp4", "width": 1920, "height": 1080}]},
    ]
}

PIXABAY_PAYLOAD = {
    "hits": [
        {
            "duration": 18,
            "videos": {
                "tiny": {"url": "https://p.com/tiny.mp4", "width": 640, "height": 360},
                "large": {"url": "https://p.com/large.mp4", "width": 1920, "height": 1080},
            },
        },
        {"duration": 2, "videos": {"large": {"url": "https://p.com/short.mp4", "width": 1920, "height": 1080}}},
    ]
}


# -- Pexels -----------------------------------------------------------------


def test_pexels_takes_the_highest_resolution_download(monkeypatch):
    monkeypatch.setattr(search.requests, "get", lambda *a, **k: FakeResponse(PEXELS_PAYLOAD))
    assert search.search_pexels("q", "key", 5, 10) == ["https://x.com/video-files/big.mp4"]


def test_pexels_skips_clips_shorter_than_asked(monkeypatch):
    monkeypatch.setattr(search.requests, "get", lambda *a, **k: FakeResponse(PEXELS_PAYLOAD))
    assert "short.mp4" not in " ".join(search.search_pexels("q", "key", 5, 10))


def test_pexels_ignores_files_that_are_not_downloads(monkeypatch):
    # The largest entry is a preview image, not a video file.
    monkeypatch.setattr(search.requests, "get", lambda *a, **k: FakeResponse(PEXELS_PAYLOAD))
    assert "preview.jpg" not in " ".join(search.search_pexels("q", "key", 5, 10))


def test_pexels_without_a_key_returns_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(search.requests, "get", lambda *a, **k: called.append(1))
    assert search.search_pexels("q", "", 5, 10) == []
    assert not called


def test_pexels_failure_is_not_fatal(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(search.requests, "get", boom)
    assert search.search_pexels("q", "key", 5, 10) == []


# -- Pixabay ----------------------------------------------------------------


def test_pixabay_takes_the_highest_resolution_rendition(monkeypatch):
    monkeypatch.setattr(search.requests, "get", lambda *a, **k: FakeResponse(PIXABAY_PAYLOAD))
    assert search.search_pixabay("q", "key", 5, 10) == ["https://p.com/large.mp4"]


def test_pixabay_skips_clips_shorter_than_asked(monkeypatch):
    monkeypatch.setattr(search.requests, "get", lambda *a, **k: FakeResponse(PIXABAY_PAYLOAD))
    assert "p.com/short.mp4" not in " ".join(search.search_pixabay("q", "key", 5, 10))


def test_pixabay_is_off_without_a_key(monkeypatch):
    # A deployment that never configures it must behave exactly as before.
    called = []
    monkeypatch.setattr(search.requests, "get", lambda *a, **k: called.append(1))
    assert search.search_pixabay("q", "", 5, 10) == []
    assert not called


def test_pixabay_failure_is_not_fatal(monkeypatch):
    # A second source must add resilience, not a second thing that can break a job.
    def boom(*a, **k):
        raise RuntimeError("pixabay down")

    monkeypatch.setattr(search.requests, "get", boom)
    assert search.search_pixabay("q", "key", 5, 10) == []


# -- merging ----------------------------------------------------------------


def test_interleave_alternates_between_libraries():
    # Concatenating would let the first library fill the video on its own
    # whenever it returns enough, which defeats having a second one.
    assert search.interleave(["a1", "a2", "a3"], ["b1", "b2"]) == [
        "a1", "b1", "a2", "b2", "a3",
    ]


def test_interleave_drops_duplicates():
    assert search.interleave(["a", "b"], ["a", "c"]) == ["a", "b", "c"]


def test_interleave_handles_an_empty_source():
    assert search.interleave(["a", "b"], []) == ["a", "b"]
    assert search.interleave([], []) == []


def test_search_combines_both_libraries(monkeypatch):
    monkeypatch.setattr(search, "search_pexels", lambda *a: ["p1", "p2"])
    monkeypatch.setattr(search, "search_pixabay", lambda *a: ["x1"])
    assert search.search_for_stock_videos("q", "key", 5, 10) == ["p1", "x1", "p2"]


def test_search_still_works_when_one_library_is_empty(monkeypatch):
    monkeypatch.setattr(search, "search_pexels", lambda *a: ["p1", "p2"])
    monkeypatch.setattr(search, "search_pixabay", lambda *a: [])
    assert search.search_for_stock_videos("q", "key", 5, 10) == ["p1", "p2"]
