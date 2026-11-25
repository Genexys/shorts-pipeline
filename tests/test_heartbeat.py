import requests

import heartbeat
from heartbeat import Heartbeat

URL = "https://hc-ping.com/0f1e2d3c-secret-uuid"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Response:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


def _recorder(status_code: int = 200):
    calls = []

    def get(url, timeout):
        calls.append((url, timeout))
        return Response(status_code)

    return calls, get


def test_off_without_a_url():
    calls, get = _recorder()
    beat = Heartbeat("  ", get=get)
    assert not beat.enabled
    assert beat.beat() is False
    assert calls == []
    assert "off" in beat.describe()


def test_pings_once_per_interval():
    calls, get = _recorder()
    clock = Clock()
    beat = Heartbeat(URL, every=300, get=get, clock=clock)

    assert beat.beat() is True
    clock.now += 60
    assert beat.beat() is False
    clock.now += 240
    assert beat.beat() is True
    assert calls == [(URL, heartbeat.TIMEOUT_SECONDS)] * 2


def test_withheld_when_not_alive_and_asked_only_when_due():
    calls, get = _recorder()
    clock = Clock()
    asked = []

    def alive():
        asked.append(clock.now)
        return False

    beat = Heartbeat(URL, every=300, get=get, clock=clock)
    assert beat.beat(alive=alive) is False
    clock.now += 60
    assert beat.beat(alive=alive) is False
    # A dead database is not hammered: asked once per interval.
    assert asked == [1000.0]
    assert calls == []


def test_an_alive_check_that_raises_withholds_the_ping():
    calls, get = _recorder()

    def alive():
        raise RuntimeError("boom")

    assert Heartbeat(URL, get=get).beat(alive=alive) is False
    assert calls == []


def test_a_failing_monitor_never_raises_and_never_logs_the_url(capsys):
    def get(url, timeout):
        raise requests.ConnectionError(f"cannot reach {url}")

    assert Heartbeat(URL, get=get).beat() is False
    out = capsys.readouterr().out
    assert "Heartbeat ping failed" in out
    assert "secret-uuid" not in out


def test_a_refused_ping_counts_as_failed(capsys):
    _, get = _recorder(status_code=404)
    assert Heartbeat(URL, get=get).beat() is False
    assert "HTTP 404" in capsys.readouterr().out


def test_from_env(monkeypatch):
    monkeypatch.setenv(heartbeat.HEALTHCHECK_URL_ENV, URL)
    assert Heartbeat.from_env().url == URL
    monkeypatch.delenv(heartbeat.HEALTHCHECK_URL_ENV)
    assert not Heartbeat.from_env().enabled
