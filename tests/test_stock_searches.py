import pipeline
import video
from formats import SHORT
from repository import (
    add_search_terms,
    add_stock_clips,
    create_job,
    get_stock_clips,
)
from models import SearchTerm
from sqlalchemy import select


def _entry(term, search_pass, urls):
    return {"term": term, "search_pass": search_pass, "urls": urls}


# -- the timeline ---------------------------------------------------------------


def test_timeline_lays_shots_end_to_end_with_their_search():
    soil = _entry("wet soil", "script", ["u1", "u2"])
    lab = _entry("bacteria lab", "script", ["u3"])
    origin = {"u1": soil, "u2": soil, "u3": lab}
    paths = {"/t/a.mp4": "u1", "/t/b.mp4": "u3", "/t/c.mp4": "u2"}

    timeline = pipeline.footage_timeline(
        [("/t/a.mp4", 3.2), ("/t/b.mp4", 3.25), ("/t/c.mp4", 4.0)], paths, origin
    )

    assert [shot["start_seconds"] for shot in timeline] == [0.0, 3.2, 6.45]
    assert [shot["search_term"] for shot in timeline] == [
        "wet soil",
        "bacteria lab",
        "wet soil",
    ]
    assert timeline[1]["url"] == "u3"
    assert [shot["position"] for shot in timeline] == [0, 1, 2]


def test_timeline_answers_what_plays_at_a_given_second():
    # The question this exists for: on 15 September, "what is the turtle at 24
    # seconds, and which search found it".
    ocean = _entry("microbial life", "script", ["turtle"])
    rain = _entry("rain on soil", "script", ["r1"])
    origin = {"turtle": ocean, "r1": rain}
    segments = [("/t/r.mp4", 3.25)] * 7 + [("/t/turtle.mp4", 3.25)]
    paths = {"/t/r.mp4": "r1", "/t/turtle.mp4": "turtle"}

    timeline = pipeline.footage_timeline(segments, paths, origin)
    at_24 = next(
        shot
        for shot in timeline
        if shot["start_seconds"] <= 24 < shot["start_seconds"] + shot["duration_seconds"]
    )

    assert at_24["search_term"] == "microbial life"


def test_timeline_keeps_a_shot_whose_origin_is_unknown():
    timeline = pipeline.footage_timeline([("/t/x.mp4", 2.0)], {}, {})
    assert timeline[0]["search_term"] == ""
    assert timeline[0]["duration_seconds"] == 2.0


# -- the search summary ---------------------------------------------------------


def test_summary_counts_results_and_what_reached_the_video():
    kept = _entry("rain on soil", "script", ["a", "b", "c"])
    unused = _entry("petrichor", "script", ["d"])
    empty = _entry("geosmin molecule", "script", [])
    origin = {"a": kept, "b": kept, "d": unused}
    timeline = [{"url": "a"}, {"url": "b"}, {"url": "a"}]

    summary = pipeline.summarize_searches([kept, unused, empty], origin, timeline)

    assert summary == [
        {"search_pass": "script", "term": "rain on soil", "results": 3, "used": 2},
        # Chosen, but not in the finished timeline (a failed download).
        {"search_pass": "script", "term": "petrichor", "results": 1, "used": 0},
        # The kind of term get_search_terms should not have written.
        {"search_pass": "script", "term": "geosmin molecule", "results": 0, "used": 0},
    ]


def test_summary_credits_a_retried_term_only_with_its_own_clips():
    first = _entry("rain on soil", "script", ["a"])
    retry = _entry("rain on soil", "short-clips", ["b"])
    origin = {"a": first, "b": retry}

    summary = pipeline.summarize_searches(
        [first, retry], origin, [{"url": "a"}, {"url": "b"}]
    )

    assert [row["used"] for row in summary] == [1, 1]


# -- combine_videos reports its plan ---------------------------------------------


def test_combine_videos_reports_the_planned_segments(monkeypatch):
    planned = [("a.mp4", 5.0), ("b.mp4", 4.5)]
    monkeypatch.setattr(video, "probe_duration", lambda path: 60.0)
    monkeypatch.setattr(video, "plan_clip_segments", lambda sources, d, cap: planned)
    monkeypatch.setattr(video.subprocess, "run", lambda command, **kw: None)
    seen = []

    video.combine_videos(["a.mp4", "b.mp4"], 9.5, 2, SHORT, on_segments=seen.append)

    assert seen == [planned]


# -- storage --------------------------------------------------------------------


def test_search_terms_and_clips_are_stored_and_read_back_in_order(session):
    job = create_job(session, payload={"videoSubject": "rain"})

    add_search_terms(
        session,
        job.id,
        [
            {"search_pass": "script", "term": "rain on soil", "results": 12, "used": 3},
            {"search_pass": "script", "term": "  ", "results": 0, "used": 0},
        ],
        commit=False,
    )
    add_stock_clips(
        session,
        job.id,
        [
            {"position": 1, "start_seconds": 3.2, "duration_seconds": 3.0,
             "search_pass": "script", "search_term": "rain on soil", "url": "u2"},
            {"position": 0, "start_seconds": 0.0, "duration_seconds": 3.2,
             "search_pass": "script", "search_term": "rain on soil", "url": "u1"},
            {"position": 2, "start_seconds": 6.2, "duration_seconds": 3.0,
             "search_pass": "script", "search_term": "x", "url": ""},
        ],
        commit=False,
    )
    session.commit()

    terms = list(session.scalars(select(SearchTerm).where(SearchTerm.job_id == job.id)))
    assert [(t.term, t.results, t.used) for t in terms] == [("rain on soil", 12, 3)]
    assert [clip.url for clip in get_stock_clips(session, job.id)] == ["u1", "u2"]


def test_uncommitted_rows_are_visible_to_a_read_in_the_same_session(session):
    # SessionLocal runs with autoflush=False; the morning report once came back
    # all zeroes because rows added a line earlier were not flushed.
    job = create_job(session, payload={"videoSubject": "rain"})
    add_stock_clips(
        session,
        job.id,
        [{"position": 0, "start_seconds": 0.0, "duration_seconds": 3.0,
          "search_pass": "script", "search_term": "t", "url": "u"}],
        commit=False,
    )
    assert len(get_stock_clips(session, job.id)) == 1
