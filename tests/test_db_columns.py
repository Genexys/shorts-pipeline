from sqlalchemy import create_engine, inspect, text

import db


def _old_video_metrics(engine):
    # The table as deployed before engaged_views existed.
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE video_metrics (video_id VARCHAR(32) PRIMARY KEY, views INTEGER)"
        ))


def test_add_missing_columns_adds_engaged_views_to_an_existing_table(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    _old_video_metrics(engine)

    db.add_missing_columns(engine)

    columns = {c["name"] for c in inspect(engine).get_columns("video_metrics")}
    assert "engaged_views" in columns


def test_add_missing_columns_is_safe_to_run_on_every_start(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    _old_video_metrics(engine)

    db.add_missing_columns(engine)
    db.add_missing_columns(engine)

    names = [c["name"] for c in inspect(engine).get_columns("video_metrics")]
    assert names.count("engaged_views") == 1


def test_add_missing_columns_skips_a_table_that_does_not_exist_yet(tmp_path):
    # create_all makes it, with the column, on a fresh database.
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")

    db.add_missing_columns(engine)

    assert "video_metrics" not in inspect(engine).get_table_names()
