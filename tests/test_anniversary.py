from datetime import date

import anniversary


class _FakeResponse:
    def __init__(self, payload, error=None):
        self._payload, self._error = payload, error

    def raise_for_status(self):
        if self._error:
            raise self._error

    def json(self):
        return self._payload


def _patch(monkeypatch, payload=None, error=None, recorder=None):
    def fake_get(url, headers=None, timeout=None):
        if recorder is not None:
            recorder.append({"url": url, "headers": headers})
        if error:
            raise error
        return _FakeResponse(payload)

    monkeypatch.setattr(anniversary.requests, "get", fake_get)


def _events(*texts):
    return {"events": [{"year": 1900 + i, "text": t} for i, t in enumerate(texts)]}


# -- what gets cut -----------------------------------------------------------


GRIM = (
    "Flight 51 crashes short of the runway, killing 4",
    "A building collapse saw the deaths of 115 people",
    "The 2008 train collision between two trains",
    "Former President is convicted of plunder",
    "Israeli-Palestinian conflict: the disengagement is completed",
    "Typhoon Maemi, the strongest recorded typhoon, made landfall",
    "A radioactive object is stolen from an abandoned hospital",
    "Largest anti-Apartheid march in South Africa",
)


def test_grim_events_are_rejected():
    # A science channel narrating an air disaster in the register it uses for
    # snail mating, over music from a folder called "curious", would be grotesque.
    for text in GRIM:
        assert anniversary.is_suitable(text) is False, text


TOPICAL = (
    "NASA confirms that its Voyager 1 probe has entered interstellar space",
    "Cave paintings are discovered in Lascaux, France",
    "The IBM 305 RAMAC is introduced, the first commercial computer to use disk storage",
    "Hannibal Goodwin patents celluloid photographic film",
)


def test_topical_events_survive_and_are_recognised():
    for text in TOPICAL:
        assert anniversary.is_suitable(text) is True, text
        assert anniversary.looks_topical(text) is True, text


def test_an_ordinary_event_survives_but_is_not_topical():
    # Exclusion decides what is unusable; topicality only decides reading order.
    text = "The first public library opens in the town of Example"
    assert anniversary.is_suitable(text) is True
    assert anniversary.looks_topical(text) is False


# -- ordering ----------------------------------------------------------------


def test_topical_events_are_read_first(monkeypatch):
    # The feed is newest-first, so a straight truncation keeps this decade's
    # politics and cuts the nineteenth-century discovery that is the point.
    monkeypatch.setattr(anniversary, "MAX_EVENTS", 2)
    _patch(
        monkeypatch,
        _events(
            "A committee is formed to review the town charter",
            "A new ferry timetable comes into effect",
            "Cave paintings are discovered in Lascaux, France",
        ),
    )

    events = anniversary.fetch_events(date(2026, 9, 12))

    assert "Lascaux" in events[0].text


def test_ordering_is_stable_within_each_group(monkeypatch):
    _patch(
        monkeypatch,
        _events(
            "Voyager 1 probe enters interstellar space",
            "A ferry timetable changes",
            "Celluloid photographic film is patented",
        ),
    )

    events = anniversary.fetch_events(date(2026, 9, 12))

    assert [e.text.split()[0] for e in events] == ["Voyager", "Celluloid", "A"]


# -- failure -----------------------------------------------------------------


def test_a_failed_lookup_returns_nothing(monkeypatch):
    _patch(monkeypatch, error=RuntimeError("503"))
    assert anniversary.fetch_events(date(2026, 9, 12)) == []


def test_a_malformed_payload_returns_nothing(monkeypatch):
    _patch(monkeypatch, {"events": "not a list"})
    assert anniversary.fetch_events(date(2026, 9, 12)) == []


def test_events_without_a_year_are_skipped(monkeypatch):
    _patch(monkeypatch, {"events": [{"text": "Something was discovered"}]})
    assert anniversary.fetch_events(date(2026, 9, 12)) == []


def test_the_request_identifies_itself(monkeypatch):
    # Wikimedia asks clients to.
    recorder: list = []
    _patch(monkeypatch, _events("Celluloid film is patented"), recorder=recorder)

    anniversary.fetch_events(date(2026, 9, 12))

    assert recorder[0]["url"].endswith("/09/12")
    assert "shorts-pipeline" in recorder[0]["headers"]["User-Agent"]


def test_format_events_is_empty_without_any():
    assert anniversary.format_events([]) == ""
