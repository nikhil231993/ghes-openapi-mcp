"""End-to-end test of find_spec against a fake GHES REST API."""

import httpx
import pytest

from ghes_openapi_mcp.config import Settings
from ghes_openapi_mcp.finder import find_spec
from ghes_openapi_mcp.github_client import GHESClient, GHESError

FILES = {
    "payments/openapi.yaml": """
openapi: 3.0.3
info: {title: Payments, version: 1.0.0}
servers: [{url: https://api.example.com}]
paths:
  /v1/payments/{id}:
    get:
      responses:
        '200':
          content:
            application/json:
              schema: {$ref: './schemas/payment.yaml'}
""",
    "payments/schemas/payment.yaml": "type: object\nproperties:\n  amount: {$ref: '../../common/money.yaml'}\n",
    "common/money.yaml": "type: number\n",
    "accounts/swagger.json": '{"swagger": "2.0", "info": {"title": "Accounts"}, "basePath": "/api", "paths": {"/accounts/{id}": {"get": {}}}}',
    "customers/openapi.yaml": """
openapi: 3.0.3
info: {title: Customers, version: 2.0.0}
servers: [{url: https://api.example.com/customers}]
paths:
  /v1/{customerId}/address:
    get:
      description: Replaces the legacy route crm/addressBook.
""",
    "config/app.yaml": "name: not-a-spec\n",
}
SHAS = {path: f"sha{i}" for i, path in enumerate(FILES)}
BY_SHA = {sha: FILES[path] for path, sha in SHAS.items()}


def handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["authorization"] == "Bearer test-token"
    path = request.url.path
    if path == "/api/v3/repos/acme/specs":
        return httpx.Response(200, json={"default_branch": "main"})
    if path == "/api/v3/repos/acme/specs/git/trees/main":
        tree = [{"type": "blob", "path": p, "sha": s, "size": 1} for p, s in SHAS.items()]
        return httpx.Response(200, json={"sha": "root", "truncated": False, "tree": tree})
    if path.startswith("/api/v3/repos/acme/specs/git/blobs/"):
        sha = path.rsplit("/", 1)[1]
        if sha in BY_SHA:
            return httpx.Response(200, text=BY_SHA[sha])
    if path.startswith("/api/v3/repos/acme/specs/contents/"):
        file_path = path.split("/contents/", 1)[1]
        if file_path in FILES:
            return httpx.Response(200, text=FILES[file_path])
    return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture
async def client():
    settings = Settings(
        api_url="https://ghes.test/api/v3",
        token="test-token",
        default_owner=None,
        default_repo=None,
        verify=True,
        timeout=5,
        max_concurrency=4,
    )
    c = GHESClient(settings, transport=httpx.MockTransport(handler))
    yield c
    await c.aclose()


async def test_find_spec_returns_best_match_with_refs(client):
    result = await find_spec(client, "https://api.example.com/v1/payments/p-1", "acme", "specs")
    assert result["ref"] == "main"
    assert result["specs_parsed"] == 3
    assert result["best"]["path"] == "payments/openapi.yaml"
    assert "openapi: 3.0.3" in result["best"]["content"]
    assert set(result["best"]["referenced_files"]) == {
        "payments/schemas/payment.yaml",
        "common/money.yaml",
    }
    assert result["matches"][0]["matched_operations"][0]["path"] == "/v1/payments/{id}"


async def test_find_spec_swagger(client):
    result = await find_spec(client, "/api/accounts/7", "acme", "specs", include_content=False)
    assert result["best"]["path"] == "accounts/swagger.json"
    assert "content" not in result["best"]


async def test_find_spec_no_match(client):
    result = await find_spec(client, "/v9/nothing", "acme", "specs")
    assert result["best"] is None
    assert result["notes"]


@pytest.mark.parametrize(
    "api_url",
    [
        "customers/v1/c-9/address",  # bare path, no leading slash
        "/customers/v1/c-9/address/",
        "v1/c-9/address",  # base path left off
        "v1/c-9",  # just a fragment, like a code search
    ],
)
async def test_find_spec_path_only(client, api_url):
    result = await find_spec(client, api_url, "acme", "specs", include_content=False)
    assert result["best"]["path"] == "customers/openapi.yaml"
    assert result["matches"][0]["matched_operations"][0]["path"] == "/v1/{customerId}/address"


async def test_find_spec_falls_back_to_text_search(client):
    result = await find_spec(client, "crm/addressBook", "acme", "specs", include_content=False)
    assert result["best"]["path"] == "customers/openapi.yaml"
    assert result["matches"][0]["text_matches"][0]["line"] == 8
    assert "mention" in result["notes"][0]


async def test_errors_are_readable(client):
    with pytest.raises(GHESError, match="404"):
        await client.get_file("acme", "specs", "missing.yaml")
