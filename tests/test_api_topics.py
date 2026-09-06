import os

import pytest

from repository import add_topic, queue_topic_job


os.environ.setdefault("PEXELS_API_KEY", "test-key")
os.environ.setdefault("TIKTOK_SESSION_ID", "test-session")
os.environ.setdefault("IMAGEMAGICK_BINARY", "/bin/echo")
os.environ.setdefault("DATABASE_URL", "sqlite:///moneyprinter_api_bootstrap.db")

import main


@pytest.fixture
def client(monkeypatch, session_factory):
    monkeypatch.setattr(main, "SessionLocal", session_factory)
    return main.app.test_client()


def test_post_topic_creates_planned_manual_topic(client):
    response = client.post("/api/topics", json={"subject": "  Why do cats purr?  "})

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["status"] == "success"
    topic = payload["topic"]
    assert topic["subject"] == "Why do cats purr?"
    assert topic["status"] == "planned"
    assert topic["source"] == "manual"
    assert topic["jobId"] is None
    assert topic["createdAt"]


def test_post_topic_duplicate_returns_409(client):
    assert client.post("/api/topics", json={"subject": "Why do cats purr?"}).status_code == 201

    response = client.post("/api/topics", json={"subject": "why do CATS purr"})

    assert response.status_code == 409
    assert response.get_json() == {"status": "error", "message": "Topic already exists."}


@pytest.mark.parametrize("body", [{}, {"subject": ""}, {"subject": "   "}, {"subject": 5}])
def test_post_topic_rejects_empty_subject(client, body):
    response = client.post("/api/topics", json=body)

    assert response.status_code == 400
    assert response.get_json() == {"status": "error", "message": "subject is required."}


def test_get_topics_filters_and_orders(client, session_factory):
    with session_factory() as session:
        add_topic(session, "planned one", None, "manual")
        queued = add_topic(session, "queued one", "niche", "ollama")
        queue_topic_job(session, queued, {"videoSubject": "queued one"})
        add_topic(session, "planned two", None, "manual")

    all_response = client.get("/api/topics")
    planned_response = client.get("/api/topics?status=planned&limit=1")

    assert all_response.status_code == 200
    assert [t["subject"] for t in all_response.get_json()["topics"]] == [
        "planned two",
        "queued one",
        "planned one",
    ]
    queued_json = all_response.get_json()["topics"][1]
    assert queued_json["status"] == "queued"
    assert queued_json["jobId"]
    assert queued_json["usedAt"]
    assert [t["subject"] for t in planned_response.get_json()["topics"]] == ["planned two"]


def test_get_topics_rejects_unknown_status(client):
    response = client.get("/api/topics?status=bogus")

    assert response.status_code == 400
    assert response.get_json()["status"] == "error"
