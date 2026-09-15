import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "Backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from db import Base  # noqa: E402
import models  # noqa: F401,E402


@pytest.fixture
def session_factory(tmp_path: Path):
    database_file = tmp_path / "test.db"
    engine = create_engine(
        f"sqlite:///{database_file}",
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    Base.metadata.create_all(bind=engine)

    yield session_factory

    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def session(session_factory):
    with session_factory() as db_session:
        yield db_session


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No test may reach a paid API.

    gpt.py loads the project .env at import, so a configured ANTHROPIC_API_KEY
    or FIRECRAWL_API_KEY leaks into the suite: write_creative went to Claude
    instead of the patched generate_response, which cost money, made the run
    non-deterministic and took it from 40 seconds to seven minutes. Tests that
    want either key set it themselves.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "")
    # Loaded from .env at import like the keys above. A test that reaches an
    # entry point must not refuse to run because the test box has no mounts.
    monkeypatch.delenv("REQUIRE_MOUNTS", raising=False)
    # Ollama is free, but reaching it is still a live call, and the suite was
    # quietly relying on the host being unable to resolve "ollama" — run the
    # same tests inside the compose network and three of them fail, because a
    # code path that is meant to give up instead succeeds and sends a second
    # Telegram message. Point it at a closed port so the outcome is the same
    # wherever the suite runs. Tests that want a model patch generate_response.
    # Set on the module, not the environment: gpt.py reads OLLAMA_BASE_URL once
    # at import, so an env var set here would arrive far too late.
    import gpt

    monkeypatch.setattr(gpt, "OLLAMA_BASE_URL", "http://127.0.0.1:1")
