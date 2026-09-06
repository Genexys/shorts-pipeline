import os

os.environ.setdefault("PEXELS_API_KEY", "test-key")
os.environ.setdefault("TIKTOK_SESSION_ID", "test-session")
os.environ.setdefault("IMAGEMAGICK_BINARY", "/bin/echo")
os.environ.setdefault("DATABASE_URL", "sqlite:///moneyprinter_cors_origins_bootstrap.db")

from main import DEFAULT_CORS_ORIGINS, cors_origins


def test_cors_origins_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert cors_origins() == list(DEFAULT_CORS_ORIGINS)


def test_cors_origins_blank_keeps_defaults():
    assert cors_origins("  , ,") == list(DEFAULT_CORS_ORIGINS)


def test_cors_origins_parses_list(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", " http://localhost:9000 ,https://ui.example.com ")
    assert cors_origins() == ["http://localhost:9000", "https://ui.example.com"]
