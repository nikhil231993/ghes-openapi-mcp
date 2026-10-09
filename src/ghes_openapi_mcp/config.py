"""Settings loaded from environment variables, or from a .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(*paths: Path) -> None:
    """Read KEY=VALUE lines from the first .env file found into os.environ.

    Variables already set in the environment (e.g. by the MCP client's "env" block) win.
    """
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip().removeprefix("export ").strip(), value.strip().strip("'\"")
            if value and not os.environ.get(key):
                os.environ[key] = value
        return


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    api_url: str
    token: str
    default_owner: str | None
    default_repo: str | None
    verify: bool | str  # True/False, or a path to a CA bundle
    timeout: float
    max_concurrency: int

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(Path.cwd() / ".env", PROJECT_ROOT / ".env")
        base_url = os.environ.get("GHES_BASE_URL", "").strip().rstrip("/")
        token = os.environ.get("GHES_TOKEN", "").strip()
        if not base_url:
            raise RuntimeError(
                "GHES_BASE_URL is not set (e.g. https://github.example.internal)"
            )
        if not token:
            raise RuntimeError("GHES_TOKEN is not set (a GitHub Enterprise PAT)")

        # GHES serves its REST API under /api/v3. Accept either form. github.com itself
        # (handy for testing against a public repo) uses api.github.com instead.
        if base_url in {"https://github.com", "https://api.github.com"}:
            api_url = "https://api.github.com"
        elif base_url.endswith("/api/v3"):
            api_url = base_url
        else:
            api_url = f"{base_url}/api/v3"

        ca_bundle = os.environ.get("GHES_CA_BUNDLE", "").strip()
        verify: bool | str = ca_bundle or _env_bool("GHES_VERIFY_SSL", True)

        return cls(
            api_url=api_url,
            token=token,
            default_owner=os.environ.get("GHES_DEFAULT_OWNER") or None,
            default_repo=os.environ.get("GHES_DEFAULT_REPO") or None,
            verify=verify,
            timeout=float(os.environ.get("GHES_TIMEOUT", "30")),
            max_concurrency=int(os.environ.get("GHES_MAX_CONCURRENCY", "8")),
        )
