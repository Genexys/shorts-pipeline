from pathlib import Path
from typing import Optional

import search
import video


class _FakeResponse:
    def __init__(self, payload: Optional[dict] = None, content: bytes = b""):
        self._payload = payload
        self.content = content

    def json(self) -> Optional[dict]:
        return self._payload


def test_search_for_stock_videos_uses_timeout_and_picks_largest_file(monkeypatch):
    captured: dict = {}

    def fake_get(
        url: str, headers: Optional[dict] = None, timeout: Optional[int] = None
    ) -> _FakeResponse:
        captured["url"] = url
        captured["timeout"] = timeout
        return _FakeResponse(
            {
                "videos": [
                    {
                        "duration": 20,
                        "video_files": [
                            {"link": "https://x.com/video-files/small.mp4", "width": 640, "height": 360},
                            {"link": "https://x.com/video-files/big.mp4", "width": 1920, "height": 1080},
                        ],
                    }
                ]
            }
        )

    monkeypatch.setattr(search.requests, "get", fake_get)

    urls = search.search_for_stock_videos("ocean", "key", 1, 10)

    assert urls == ["https://x.com/video-files/big.mp4"]
    assert captured["timeout"] == 60
    assert "query=ocean" in captured["url"]


def test_save_video_uses_timeout(monkeypatch, tmp_path: Path):
    captured: dict = {}

    def fake_get(url: str, timeout: Optional[int] = None) -> _FakeResponse:
        captured["timeout"] = timeout
        return _FakeResponse(content=b"mp4bytes")

    monkeypatch.setattr(video.requests, "get", fake_get)

    path = video.save_video("https://x.com/video-files/big.mp4", directory=str(tmp_path))

    assert captured["timeout"] == 60
    assert open(path, "rb").read() == b"mp4bytes"
