import requests

import notify


class _FakeResponse:
    def __init__(self, status_code: int = 200):
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


def test_send_telegram_posts_message(monkeypatch):
    captured: dict = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(notify.requests, "post", fake_post)

    assert notify.send_telegram("hello", token="TOKEN", chat_id="42") is True
    assert captured["url"] == "https://api.telegram.org/botTOKEN/sendMessage"
    assert captured["json"] == {"chat_id": "42", "text": "hello", "disable_web_page_preview": True}
    assert captured["timeout"] == 10


def test_send_telegram_reads_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "ENVTOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    captured: dict = {}
    monkeypatch.setattr(
        notify.requests,
        "post",
        lambda url, json=None, timeout=None: captured.update(url=url) or _FakeResponse(),
    )

    assert notify.send_telegram("hi") is True
    assert captured["url"] == "https://api.telegram.org/botENVTOKEN/sendMessage"


def test_send_telegram_returns_false_when_disabled(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    assert notify.send_telegram("no config") is False
    assert "[telegram disabled] no config" in capsys.readouterr().out


def test_send_telegram_never_raises(monkeypatch):
    def failing_post(url, json=None, timeout=None):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(notify.requests, "post", failing_post)

    assert notify.send_telegram("x", token="T", chat_id="1") is False

    monkeypatch.setattr(
        notify.requests, "post", lambda url, json=None, timeout=None: _FakeResponse(401)
    )
    assert notify.send_telegram("x", token="T", chat_id="1") is False


def test_send_telegram_truncates_long_text(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        notify.requests,
        "post",
        lambda url, json=None, timeout=None: captured.update(json=json) or _FakeResponse(),
    )

    notify.send_telegram("x" * 5000, token="T", chat_id="1")

    assert len(captured["json"]["text"]) == 4000
