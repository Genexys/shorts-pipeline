from datetime import datetime, timedelta, timezone

from analytics import VideoMetrics
from models import VideoMetric
from repository import (
    add_artifact,
    add_topic,
    create_job,
    list_published_videos,
    top_performing_subjects,
    upsert_metrics,
    worst_performing_subjects,
)

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def _metrics(video_id: str, percentage: float, duration: float, views: int = 100):
    return VideoMetrics(
        video_id=video_id,
        views=views,
        average_view_percentage=percentage,
        average_view_duration=duration,
        measured_at=NOW,
    )


def _published(session, subject: str, format_name: str, video_id: str):
    """A topic with a finished job and a youtube_video artifact, as the pipeline leaves it."""
    job = create_job(session, payload={"videoSubject": subject, "format": format_name})
    topic = add_topic(session, subject, niche=None, source="manual")
    topic.job_id = job.id
    add_artifact(
        session, job.id, "youtube_video", f"https://youtu.be/{video_id}",
        {"videoId": video_id}, commit=False,
    )
    session.commit()
    return job.id


# -- listing ----------------------------------------------------------------


def test_list_published_videos_returns_id_job_format_and_time(session_factory):
    with session_factory() as session:
        job_id = _published(session, "Why the ocean is salty", "short", "vid1")

        rows = list_published_videos(session)

    assert len(rows) == 1
    video_id, listed_job, format_name, published_at = rows[0]
    assert (video_id, listed_job, format_name) == ("vid1", job_id, "short")
    assert published_at.tzinfo is not None


def test_list_published_videos_skips_an_artifact_without_a_video_id(session_factory):
    with session_factory() as session:
        job = create_job(session, payload={"videoSubject": "s", "format": "short"})
        add_artifact(session, job.id, "youtube_video", "https://youtu.be/x", {})
        session.commit()

        assert list_published_videos(session) == []


def test_list_published_videos_ignores_other_artifact_types(session_factory):
    with session_factory() as session:
        job = create_job(session, payload={"videoSubject": "s", "format": "long"})
        add_artifact(session, job.id, "video", "output/x.mp4", {"videoId": "nope"})
        session.commit()

        assert list_published_videos(session) == []


def test_list_published_videos_defaults_a_missing_format_to_short(session_factory):
    # Jobs queued before formats existed carry no format key.
    with session_factory() as session:
        job = create_job(session, payload={"videoSubject": "s"})
        add_artifact(session, job.id, "youtube_video", "u", {"videoId": "old1"})
        session.commit()

        assert list_published_videos(session)[0][2] == "short"


# -- upsert -----------------------------------------------------------------


def test_upsert_metrics_writes_a_row(session_factory):
    with session_factory() as session:
        job_id = _published(session, "Salt", "short", "vid1")

        record = upsert_metrics(session, _metrics("vid1", 62.5, 31.0), job_id, "short", NOW)

        assert record.views == 100
        assert record.average_view_percentage == 62.5
        assert record.average_view_duration == 31.0


def test_upsert_metrics_replaces_rather_than_appends(session_factory):
    # Only the latest reading matters; a history table would grow unread.
    with session_factory() as session:
        job_id = _published(session, "Salt", "short", "vid1")
        upsert_metrics(session, _metrics("vid1", 40.0, 20.0), job_id, "short", NOW)

        upsert_metrics(session, _metrics("vid1", 71.0, 35.0, views=900), job_id, "short", NOW)

        rows = session.query(VideoMetric).all()
        assert len(rows) == 1
        assert rows[0].average_view_percentage == 71.0
        assert rows[0].views == 900


# -- ranking ----------------------------------------------------------------


def _seed_ranked(session, format_name, values, age_days=30):
    """One published, measured video per (subject, metric value)."""
    published_at = NOW - timedelta(days=age_days)
    for index, (subject, value) in enumerate(values):
        video_id = f"v{index}{format_name}"
        job_id = _published(session, subject, format_name, video_id)
        metrics = (
            _metrics(video_id, value, 0.0)
            if format_name == "short"
            else _metrics(video_id, 0.0, value)
        )
        upsert_metrics(session, metrics, job_id, format_name, published_at, commit=False)
    session.commit()


def test_top_performing_subjects_ranks_shorts_by_percentage(session_factory):
    with session_factory() as session:
        _seed_ranked(session, "short", [("weak", 20.0), ("strong", 80.0), ("middling", 50.0)])

        assert top_performing_subjects(session, "short", 2, min_age_days=7) == [
            "strong",
            "middling",
        ]


def test_worst_performing_subjects_is_the_other_end(session_factory):
    with session_factory() as session:
        _seed_ranked(session, "short", [("weak", 20.0), ("strong", 80.0), ("middling", 50.0)])

        assert worst_performing_subjects(session, "short", 1, min_age_days=7) == ["weak"]


def test_long_form_ranks_by_duration_not_percentage(session_factory):
    # Percentage flatters short videos; long form is judged in seconds. Every
    # row here has percentage 0, so a query using it would rank arbitrarily.
    with session_factory() as session:
        _seed_ranked(session, "long", [("brief", 40.0), ("held them", 210.0)])

        assert top_performing_subjects(session, "long", 1, min_age_days=7) == ["held them"]


def test_ranking_excludes_videos_younger_than_the_threshold(session_factory):
    # The whole reason min_age_days is required: a two-day-old video has no
    # settled data and the recommender has not finished placing it.
    with session_factory() as session:
        _seed_ranked(session, "short", [("fresh", 95.0)], age_days=2)
        _seed_ranked(session, "short", [("settled", 30.0)], age_days=30)

        assert top_performing_subjects(session, "short", 5, min_age_days=7) == ["settled"]


def test_ranking_does_not_mix_formats(session_factory):
    with session_factory() as session:
        _seed_ranked(session, "short", [("a short", 90.0)])
        _seed_ranked(session, "long", [("a long one", 300.0)])

        assert top_performing_subjects(session, "short", 5, min_age_days=7) == ["a short"]
        assert top_performing_subjects(session, "long", 5, min_age_days=7) == ["a long one"]


def test_ranking_returns_nothing_when_no_video_is_old_enough(session_factory):
    with session_factory() as session:
        _seed_ranked(session, "short", [("fresh", 95.0)], age_days=1)

        assert top_performing_subjects(session, "short", 5, min_age_days=7) == []


# -- research sources --------------------------------------------------------


class _Source:
    def __init__(self, title, url, snippet):
        self.title, self.url, self.snippet = title, url, snippet


def test_research_sources_round_trip(session_factory):
    from repository import add_research_sources, get_research_sources

    with session_factory() as session:
        job_id = _published(session, "Jellyfish", "short", "vid1")

        kept = add_research_sources(
            session,
            job_id,
            [
                _Source("NHM", "https://nhm.ac.uk/a", "It is 4.5 mm across."),
                _Source("AMNH", "https://amnh.org/b", "Transdifferentiation."),
            ],
        )

        assert kept == 2
        stored = get_research_sources(session, job_id)
        assert [s.url for s in stored] == ["https://nhm.ac.uk/a", "https://amnh.org/b"]
        assert stored[0].snippet == "It is 4.5 mm across."


def test_research_sources_skip_entries_without_a_url(session_factory):
    from repository import add_research_sources, get_research_sources

    with session_factory() as session:
        job_id = _published(session, "Jellyfish", "short", "vid1")

        kept = add_research_sources(session, job_id, [_Source("t", "", "s")])

        assert kept == 0
        assert get_research_sources(session, job_id) == []


def test_research_sources_are_empty_for_an_unknown_job(session_factory):
    from repository import get_research_sources

    with session_factory() as session:
        assert get_research_sources(session, "no-such-job") == []
