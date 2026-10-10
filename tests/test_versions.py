"""Choosing between versions of the same API that all define the matching path."""

import httpx
import pytest

from ghes_openapi_mcp.config import Settings
from ghes_openapi_mcp.finder import find_spec
from ghes_openapi_mcp.github_client import GHESClient
from ghes_openapi_mcp.spec import version_key


def spec(version: str) -> str:
    return f"""
openapi: 3.0.3
info: {{title: Payments API, version: {version}}}
servers: [{{url: https://api.example-bank.com/payments/v1}}]
paths:
  /payments/{{paymentId}}:
    get: {{}}
"""


def make_client(files: dict[str, str]) -> GHESClient:
    shas = {path: f"sha{i}" for i, path in enumerate(files)}
    by_sha = {sha: files[path] for path, sha in shas.items()}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v3/repos/o/r":
            return httpx.Response(200, json={"default_branch": "main"})
        if path == "/api/v3/repos/o/r/git/trees/main":
            tree = [{"type": "blob", "path": p, "sha": s} for p, s in shas.items()]
            return httpx.Response(200, json={"sha": "root", "truncated": False, "tree": tree})
        if path.startswith("/api/v3/repos/o/r/git/blobs/"):
            return httpx.Response(200, text=by_sha[path.rsplit("/", 1)[1]])
        return httpx.Response(404, json={"message": "Not Found"})

    settings = Settings("https://ghes.test/api/v3", "t", None, None, True, 5, 4)
    return GHESClient(settings, transport=httpx.MockTransport(handler))


VERSIONS = ["1.8", "1.9", "1.10", "1.11", "1.12", "1.13", "1.14"]


@pytest.mark.parametrize("active", ["1.14", "1.9"])
async def test_active_status_wins_a_tie(active):
    files = {}
    for v in VERSIONS:
        files[f"payments/apis/P_v{v}.yaml"] = spec(f"{v}.0")
        status = "ACTIVE" if v == active else "DEPRECATED"
        files[f"payments/apis/P_v{v}.metadata.yaml"] = f"specFile: P_v{v}.yaml\nstatus: {status}\n"
    client = make_client(files)
    result = await find_spec(client, "payments/v1/payments/PAY1", "o", "r", method="GET")
    await client.aclose()

    assert result["best"]["path"] == f"payments/apis/P_v{active}.yaml"
    assert result["matches"][0]["status"] == "ACTIVE"
    assert result["matches"][0]["metadata_path"] == f"payments/apis/P_v{active}.metadata.yaml"
    # All 7 tied versions are listed, not just the first 5.
    assert result["total_matches"] == 7
    assert len(result["matches"]) == 7
    assert "status ACTIVE" in result["notes"][0]


async def test_highest_version_wins_without_metadata():
    client = make_client({f"payments/apis/P_v{v}.yaml": spec(f"{v}.0") for v in VERSIONS})
    result = await find_spec(client, "payments/v1/payments/PAY1", "o", "r", include_content=False)
    await client.aclose()

    assert result["best"]["path"] == "payments/apis/P_v1.14.yaml"
    assert [m["version"] for m in result["matches"]][:3] == ["1.14.0", "1.13.0", "1.12.0"]
    assert all(m["status"] is None for m in result["matches"])
    assert "highest version" in result["notes"][0]


async def test_score_still_beats_status():
    files = {
        "a/Exact_v1.0.yaml": spec("1.0.0"),
        "a/Exact_v1.0.metadata.yaml": "status: DEPRECATED\n",
        # Same path under another base, so it only matches as a suffix (a weaker match).
        "b/Other_v9.0.yaml": spec("9.0.0").replace("https://api.example-bank.com/payments/v1", "/other"),
        "b/Other_v9.0.metadata.yaml": "status: ACTIVE\n",
    }
    client = make_client(files)
    result = await find_spec(client, "payments/v1/payments/PAY1", "o", "r", include_content=False)
    await client.aclose()
    assert [(m["path"], m["matched_operations"][0]["match_type"]) for m in result["matches"]] == [
        ("a/Exact_v1.0.yaml", "exact"),  # wins although DEPRECATED and an older version
        ("b/Other_v9.0.yaml", "suffix"),
    ]
    assert result["best"]["path"] == "a/Exact_v1.0.yaml"


def test_version_key_compares_numbers():
    assert version_key("1.14.0") > version_key("1.9.0") > version_key("1.8")
    assert version_key(None) == ()
