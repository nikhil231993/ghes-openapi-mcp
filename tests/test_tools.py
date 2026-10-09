"""The GitHub-style tools, called the way the official GitHub MCP server's tools are called."""

import httpx
import pytest

from ghes_openapi_mcp import server
from ghes_openapi_mcp.config import Settings
from ghes_openapi_mcp.github_client import GHESClient

REQUESTS: list[httpx.Request] = []


def handler(request: httpx.Request) -> httpx.Response:
    REQUESTS.append(request)
    path, params = request.url.path, request.url.params
    if path == "/api/v3/search/code":
        item = {
            "name": "openapi.yaml",
            "path": "api/openapi.yaml",
            "sha": "s1",
            "html_url": "https://ghes.test/x",
            "repository": {"full_name": "my-org/my-service"},
            "text_matches": [{"fragment": "  /customers/{customerId}:"}],
        }
        return httpx.Response(200, json={"total_count": 1, "incomplete_results": False, "items": [item]})
    if path == "/api/v3/repos/my-org/my-service":
        return httpx.Response(200, json={"default_branch": "main"})
    if path.startswith("/api/v3/repos/my-org/my-service/git/trees/"):
        tree = [{"type": "tree", "path": "api", "sha": "t1"}]
        if params.get("recursive"):
            tree += [
                {"type": "blob", "path": "api/openapi.yaml", "sha": "b1", "size": 10},
                {"type": "blob", "path": "README.md", "sha": "b2", "size": 5},
            ]
        return httpx.Response(200, json={"sha": "root", "truncated": False, "tree": tree})
    if path == "/api/v3/repos/my-org/my-service/contents/api":
        return httpx.Response(200, json=[{"name": "openapi.yaml", "path": "api/openapi.yaml", "type": "file"}])
    if path == "/api/v3/repos/my-org/my-service/contents/api/openapi.yaml":
        return httpx.Response(200, text=f"openapi: 3.0.0 # ref={params.get('ref')}")
    return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture(autouse=True)
async def ghes(monkeypatch):
    settings = Settings(
        api_url="https://ghes.test/api/v3",
        token="t",
        default_owner=None,
        default_repo=None,
        verify=True,
        timeout=5,
        max_concurrency=4,
    )
    client = GHESClient(settings, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(server, "_settings", settings)
    monkeypatch.setattr(server, "_client", client)
    REQUESTS.clear()
    yield
    await client.aclose()


async def test_search_code_official_params():
    result = await server.search_code(
        "openapi in:path repo:my-org/my-service", sort="indexed", order="asc", perPage=5
    )
    assert result["items"][0]["path"] == "api/openapi.yaml"
    assert result["items"][0]["text_matches"] == ["  /customers/{customerId}:"]
    assert "text-match" in REQUESTS[-1].headers["accept"]
    params = REQUESTS[-1].url.params
    assert (params["sort"], params["order"], params["per_page"]) == ("indexed", "asc", "5")


async def test_search_code_rejects_long_query():
    with pytest.raises(ValueError, match="256"):
        await server.search_code("x" * 257)


async def test_get_repository_tree_recursive_and_filtered():
    result = await server.get_repository_tree("my-org", "my-service", recursive=True, path_filter="api")
    assert [e["path"] for e in result["entries"]] == ["api", "api/openapi.yaml"]
    result = await server.get_repository_tree("my-org", "my-service")  # top level by default
    assert [e["type"] for e in result["entries"]] == ["tree"]


async def test_get_repository_tree_accepts_full_ref_name():
    await server.get_repository_tree("my-org", "my-service", tree_sha="refs/heads/main")
    assert REQUESTS[-1].url.path.endswith("/git/trees/main")


@pytest.mark.parametrize(
    "kwargs, sent_ref",
    [
        ({}, None),
        ({"ref": "refs/heads/release/2.3"}, "release/2.3"),
        ({"ref": "refs/tags/v2.3.0"}, "v2.3.0"),
        ({"ref": "refs/pull/7/head"}, "refs/pull/7/head"),
        ({"ref": "main", "sha": "abc123"}, "abc123"),
    ],
)
async def test_get_file_contents_ref_forms(kwargs, sent_ref):
    result = await server.get_file_contents(
        owner="my-org", repo="my-service", path="api/openapi.yaml", **kwargs
    )
    assert result["type"] == "file"
    assert result["content"] == f"openapi: 3.0.0 # ref={sent_ref}"


async def test_get_file_contents_lists_directory():
    result = await server.get_file_contents(owner="my-org", repo="my-service", path="api/")
    assert result["type"] == "dir"
    assert result["entries"][0]["path"] == "api/openapi.yaml"



def test_settings_read_dotenv_but_env_wins(tmp_path, monkeypatch):
    for key in ("GHES_BASE_URL", "GHES_TOKEN", "GHES_DEFAULT_OWNER", "GHES_DEFAULT_REPO"):
        monkeypatch.setenv(key, "")  # restored after the test; empty counts as unset
    (tmp_path / ".env").write_text(
        "# comment\nGHES_BASE_URL=https://ghes.corp\nGHES_TOKEN='from-file'\nGHES_DEFAULT_REPO=\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GHES_TOKEN", "from-env")
    settings = Settings.from_env()
    assert settings.api_url == "https://ghes.corp/api/v3"
    assert settings.token == "from-env"
    assert settings.default_repo is None
