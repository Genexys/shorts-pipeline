import importlib
from pathlib import Path

import pytest

import youtube


@pytest.fixture
def reload_youtube(monkeypatch):
    """Reload youtube.py with the current env, and restore defaults afterwards."""

    def _reload():
        return importlib.reload(youtube)

    yield _reload
    monkeypatch.delenv("YOUTUBE_CLIENT_SECRETS_FILE", raising=False)
    monkeypatch.delenv("YOUTUBE_TOKEN_FILE", raising=False)
    importlib.reload(youtube)


def test_secret_paths_default_to_backend_dir(monkeypatch, reload_youtube):
    monkeypatch.delenv("YOUTUBE_CLIENT_SECRETS_FILE", raising=False)
    monkeypatch.delenv("YOUTUBE_TOKEN_FILE", raising=False)

    module = reload_youtube()

    assert module.CLIENT_SECRETS_FILE == module.BASE_DIR / "client_secret.json"
    assert module.TOKEN_FILE == module.BASE_DIR / "youtube_token.json"


def test_empty_env_keeps_defaults(monkeypatch, reload_youtube):
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRETS_FILE", "")
    monkeypatch.setenv("YOUTUBE_TOKEN_FILE", "   ")

    module = reload_youtube()

    assert module.CLIENT_SECRETS_FILE == module.BASE_DIR / "client_secret.json"
    assert module.TOKEN_FILE == module.BASE_DIR / "youtube_token.json"


def test_secret_paths_from_env(monkeypatch, tmp_path, reload_youtube):
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRETS_FILE", str(tmp_path / "cs.json"))
    monkeypatch.setenv("YOUTUBE_TOKEN_FILE", str(tmp_path / "tok.json"))

    module = reload_youtube()

    assert module.CLIENT_SECRETS_FILE == Path(tmp_path / "cs.json")
    assert module.TOKEN_FILE == Path(tmp_path / "tok.json")
    # load_credentials() must use the overridden default, not the old one
    assert module.load_credentials() is None


def test_auth_error_names_token_path(monkeypatch, tmp_path, reload_youtube):
    monkeypatch.setenv("YOUTUBE_TOKEN_FILE", str(tmp_path / "missing.json"))
    module = reload_youtube()

    with pytest.raises(module.YouTubeAuthError, match="youtube_auth.py") as info:
        module.get_authenticated_service()

    assert str(tmp_path / "missing.json") in str(info.value)
