import base64
from pathlib import Path

import pytest
import requests

import tiktokvoice
from tiktokvoice import TTSError


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


def _no_sleep(monkeypatch):
    sleeps: list[int] = []
    monkeypatch.setattr(tiktokvoice, "_sleep", lambda seconds: sleeps.append(seconds))
    return sleeps


def test_generate_audio_falls_back_to_second_endpoint(monkeypatch):
    sleeps = _no_sleep(monkeypatch)
    calls: list[str] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        assert timeout == 30
        if url == tiktokvoice.ENDPOINTS[0]:
            raise requests.ConnectionError("down")
        return _FakeResponse({"success": True, "data": "QUJD", "error": None})

    monkeypatch.setattr(tiktokvoice.requests, "post", fake_post)

    assert tiktokvoice.generate_audio("hello", "en_us_001") == "QUJD"
    assert calls == [tiktokvoice.ENDPOINTS[0]] * 3 + [tiktokvoice.ENDPOINTS[1]]
    assert sleeps == [2, 4, 8]


def test_generate_audio_raises_tts_error_when_all_endpoints_fail(monkeypatch):
    _no_sleep(monkeypatch)

    def fake_post(url, headers=None, json=None, timeout=None):
        return _FakeResponse({"success": False, "data": None, "error": "rate limited"})

    monkeypatch.setattr(tiktokvoice.requests, "post", fake_post)

    with pytest.raises(TTSError, match="rate limited"):
        tiktokvoice.generate_audio("hello", "en_us_001")


def test_generate_audio_strips_data_uri_prefix(monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setattr(
        tiktokvoice.requests,
        "post",
        lambda url, headers=None, json=None, timeout=None: _FakeResponse(
            {"data": "data:audio/mp3;base64,QUJD"}
        ),
    )

    assert tiktokvoice.generate_audio("hello", "en_us_001") == "QUJD"


def test_tts_writes_decoded_audio_file(monkeypatch, tmp_path: Path):
    encoded = base64.b64encode(b"ID3fake").decode()
    monkeypatch.setattr(tiktokvoice, "generate_audio", lambda text, voice: encoded)
    target = tmp_path / "out.mp3"

    tiktokvoice.tts("hello world", "en_us_001", filename=str(target))

    assert target.read_bytes() == b"ID3fake"


def test_tts_joins_chunks_for_long_text(monkeypatch, tmp_path: Path):
    # Each part gets its own distinct byte, keyed by its position in the
    # split (not by call/completion order, since chunks are generated on
    # separate threads), so this test can actually detect chunks being
    # joined out of order or a stale/padding decode bug.
    long_text = " ".join(f"word{i}" for i in range(200))  # unique words, > TEXT_BYTE_LIMIT
    parts = tiktokvoice.split_string(long_text, 299)
    assert len(parts) >= 2
    index_by_part = {part: index for index, part in enumerate(parts)}
    assert len(index_by_part) == len(parts)  # every part is distinct

    def fake_generate(text: str, voice: str) -> str:
        return base64.b64encode(bytes([65 + index_by_part[text]])).decode()

    monkeypatch.setattr(tiktokvoice, "generate_audio", fake_generate)
    target = tmp_path / "long.mp3"

    tiktokvoice.tts(long_text, "en_us_001", filename=str(target))

    assert target.read_bytes() == bytes(range(65, 65 + len(parts)))


def test_tts_rejects_unknown_voice(tmp_path: Path):
    with pytest.raises(TTSError, match="not available"):
        tiktokvoice.tts("hello", "xx_999", filename=str(tmp_path / "x.mp3"))


def test_tts_rejects_empty_text_without_calling_network(monkeypatch, tmp_path: Path):
    calls: list[str] = []
    monkeypatch.setattr(
        tiktokvoice.requests, "post", lambda *args, **kwargs: calls.append(args)
    )

    with pytest.raises(TTSError, match="empty"):
        tiktokvoice.tts("   ", "en_us_001", filename=str(tmp_path / "empty.mp3"))

    assert calls == []


def test_tts_propagates_chunk_failure(monkeypatch, tmp_path: Path):
    def failing(text: str, voice: str) -> str:
        raise TTSError("endpoint exploded")

    monkeypatch.setattr(tiktokvoice, "generate_audio", failing)

    with pytest.raises(TTSError, match="endpoint exploded"):
        tiktokvoice.tts("hello", "en_us_001", filename=str(tmp_path / "x.mp3"))
