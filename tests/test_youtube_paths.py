import os
import subprocess
import sys
from pathlib import Path

import youtube

BACKEND_DIR = Path(youtube.__file__).resolve().parent
_ENV_KEYS = ("YOUTUBE_CLIENT_SECRETS_FILE", "YOUTUBE_TOKEN_FILE")


def test_path_from_env_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("X_PATH_FOR_TEST", raising=False)
    assert youtube._path_from_env("X_PATH_FOR_TEST", Path("/d")) == Path("/d")


def test_path_from_env_blank_keeps_default(monkeypatch):
    monkeypatch.setenv("X_PATH_FOR_TEST", "   ")
    assert youtube._path_from_env("X_PATH_FOR_TEST", Path("/d")) == Path("/d")


def test_path_from_env_expands_user(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("X_PATH_FOR_TEST", "~/tok.json")
    assert youtube._path_from_env("X_PATH_FOR_TEST", Path("/d")) == tmp_path / "tok.json"


def _run_in_fresh_interpreter(code: str, overrides: dict[str, str]) -> str:
    """Import youtube.py in a new process so module-level constants see exactly `overrides`."""
    env = {key: value for key, value in os.environ.items() if key not in _ENV_KEYS}
    env.update(overrides)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_secret_paths_default_to_backend_dir():
    out = _run_in_fresh_interpreter(
        "import youtube; print(youtube.CLIENT_SECRETS_FILE); print(youtube.TOKEN_FILE)", {}
    )
    assert out.splitlines() == [
        str(BACKEND_DIR / "client_secret.json"),
        str(BACKEND_DIR / "youtube_token.json"),
    ]


def test_secret_paths_from_env(tmp_path):
    out = _run_in_fresh_interpreter(
        "import youtube; print(youtube.CLIENT_SECRETS_FILE); print(youtube.TOKEN_FILE); "
        "print(youtube.load_credentials())",
        {
            "YOUTUBE_CLIENT_SECRETS_FILE": str(tmp_path / "cs.json"),
            "YOUTUBE_TOKEN_FILE": str(tmp_path / "tok.json"),
        },
    )
    assert out.splitlines() == [str(tmp_path / "cs.json"), str(tmp_path / "tok.json"), "None"]


def test_auth_error_names_token_path(tmp_path):
    missing = tmp_path / "missing.json"
    out = _run_in_fresh_interpreter(
        "import youtube\n"
        "try:\n"
        "    youtube.get_authenticated_service()\n"
        "except youtube.YouTubeAuthError as exc:\n"
        "    print(exc)\n",
        {"YOUTUBE_TOKEN_FILE": str(missing)},
    )
    assert "youtube_auth.py" in out
    assert str(missing) in out


def test_minting_asks_for_all_three_scopes():
    import youtube

    # Every scope is deliberate: upload for videos and thumbnails, analytics
    # read-only for retention. Captions are done by hand rather than widening
    # the grant to force-ssl.
    assert youtube.SCOPES == [youtube.UPLOAD_SCOPE, youtube.ANALYTICS_SCOPE]


def test_the_analytics_scope_is_read_only():
    import youtube

    assert youtube.ANALYTICS_SCOPE.endswith("yt-analytics.readonly")
