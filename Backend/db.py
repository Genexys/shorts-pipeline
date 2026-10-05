import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from dotenv import load_dotenv
from utils import ENV_FILE


load_dotenv(ENV_FILE)


class Base(DeclarativeBase):
    pass


def _database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url
    return "sqlite:///moneyprinter.db"


DATABASE_URL = _database_url()

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False}
    if DATABASE_URL.startswith("sqlite")
    else {},
)

SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
)


def init_db() -> None:
    from models import (  # noqa: F401
        Artifact,
        GenerationEvent,
        GenerationJob,
        Project,
        ResearchSource,
        Script,
        Topic,
        VideoMetric,
        VideoStat,
    )

    Base.metadata.create_all(bind=engine)
    add_missing_columns()


# Columns added to tables that already exist in a deployed database.
# create_all only creates missing tables, never missing columns, and the
# project has no migration tool; each entry here is plain, nullable DDL that
# both SQLite and Postgres accept.
ADDED_COLUMNS = (("video_metrics", "engaged_views", "INTEGER"),)


def add_missing_columns(bind=None) -> None:
    """Adds any ADDED_COLUMNS entry the live table lacks. Safe to run every start."""
    bind = bind or engine
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    with bind.begin() as connection:
        for table, column, ddl_type in ADDED_COLUMNS:
            if table not in tables:
                continue
            present = {c["name"] for c in inspector.get_columns(table)}
            if column not in present:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
