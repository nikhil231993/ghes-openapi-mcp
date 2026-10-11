"""MCP server exposing GitHub Enterprise Server search and OpenAPI lookup tools."""

from __future__ import annotations

import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import finder
from .config import Settings
from .github_client import GHESClient
from .spec import is_candidate

mcp = FastMCP("ghes-openapi")

_settings: Settings | None = None
_client: GHESClient | None = None


def _get_client() -> tuple[GHESClient, Settings]:
    global _settings, _client
    if _client is None:
        _settings = Settings.from_env()
        _client = GHESClient(_settings)
    assert _settings is not None
    return _client, _settings


def _repo(owner: str | None, repo: str | None, settings: Settings) -> tuple[str, str]:
    owner = owner or settings.default_owner
    repo = repo or settings.default_repo
    if not owner or not repo:
        raise ValueError(
            "owner and repo are required (or set GHES_DEFAULT_OWNER and GHES_DEFAULT_REPO)."
        )
    return owner, repo


@mcp.tool()
async def search_code(
    query: str,
    sort: str | None = None,
    order: str | None = None,
    page: int = 1,
    perPage: int = 30,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Search code on GitHub Enterprise using GitHub code-search syntax.

    Same parameters as the official GitHub MCP server's search_code. Qualifiers like
    repo:, org:, path:, filename:, extension:, in:file and in:path work. sort can be
    "indexed" with order "asc" or "desc". If owner/repo are given (or defaults are
    configured) and the query has no repo:/org:/user: qualifier, the search is limited
    to that repo.

    Limits of GitHub code search: punctuation (. : / ? = & # quotes) is ignored, so search
    for a distinctive word or path segment instead of a full URL. Only the default branch
    is indexed, and large files (over ~384 KB) are not searchable. For those, use
    get_repository_tree + get_file_contents, or find_openapi_spec.
    """
    client, settings = _get_client()
    if not re.search(r"\b(repo|org|user):", query):
        o, r = owner or settings.default_owner, repo or settings.default_repo
        if o and r:
            query = f"{query} repo:{o}/{r}"
    if len(query) > 256:
        raise ValueError(f"Query is {len(query)} characters; GitHub allows at most 256.")

    data = await client.search_code(query, per_page=perPage, page=page, sort=sort, order=order)
    return {
        "query": query,
        "total_count": data.get("total_count", 0),
        "incomplete_results": data.get("incomplete_results", False),
        "items": [
            {
                "name": item.get("name"),
                "repository": item.get("repository", {}).get("full_name"),
                "path": item.get("path"),
                "sha": item.get("sha"),
                "html_url": item.get("html_url"),
                "text_matches": [
                    m.get("fragment") for m in item.get("text_matches", []) if m.get("fragment")
                ],
            }
            for item in data.get("items", [])
        ],
    }


@mcp.tool()
async def get_repository_tree(
    owner: str | None = None,
    repo: str | None = None,
    tree_sha: str | None = None,
    recursive: bool = False,
    path_filter: str | None = None,
    spec_files_only: bool = False,
    limit: int = 2000,
) -> dict[str, Any]:
    """List the files and directories in a repo. Defaults to the default branch.

    Same parameters as the official GitHub MCP server's get_repository_tree. tree_sha is a
    branch, tag, commit SHA or tree SHA. Like the official tool, only the top level is
    listed unless recursive=True.
    path_filter keeps only paths starting with that prefix. spec_files_only (extra) keeps
    only YAML/JSON files that could be OpenAPI specs.
    """
    client, settings = _get_client()
    owner, repo = _repo(owner, repo, settings)
    tree = await client.get_tree(owner, repo, tree_sha, recursive=recursive)
    # Plain prefix match, like the official server: "payments/" excludes the "payments"
    # entry itself and siblings such as "payments-legacy/".
    prefix = (path_filter or "").lstrip("/")
    entries = [
        {"path": e["path"], "type": e.get("type"), "size": e.get("size"), "sha": e.get("sha")}
        for e in tree["entries"]
        if (not prefix or e["path"].startswith(prefix))
        and (not spec_files_only or (e.get("type") == "blob" and is_candidate(e["path"])))
    ]
    return {
        "repo": f"{owner}/{repo}",
        "ref": tree["ref"],
        "tree_sha": tree["sha"],
        "tree_truncated": tree["truncated"],
        "total_entries": len(entries),
        "entries": entries[:limit],
        "limited": len(entries) > limit,
    }


@mcp.tool()
async def get_file_contents(
    path: str = "/",
    owner: str | None = None,
    repo: str | None = None,
    ref: str | None = None,
    sha: str | None = None,
) -> dict[str, Any]:
    """Get the raw contents of a file, or list a directory.

    Same parameters as the official GitHub MCP server's get_file_contents. ref may be a
    branch, tag, refs/heads/<branch>, refs/tags/<tag> or refs/pull/<n>/head. sha (a commit
    SHA) takes precedence over ref. Works for files up to 100 MB, unlike code search.
    If an OpenAPI spec uses relative $ref to other files, call this again for each one.
    """
    client, settings = _get_client()
    owner, repo = _repo(owner, repo, settings)
    content = await client.get_contents(owner, repo, path, sha or ref)
    result: dict[str, Any] = {"repo": f"{owner}/{repo}", "path": path, "ref": sha or ref}
    if isinstance(content, list):
        result["type"] = "dir"
        result["entries"] = [
            {"name": e.get("name"), "path": e.get("path"), "type": e.get("type"), "size": e.get("size")}
            for e in content
        ]
    else:
        result["type"] = "file"
        result["content"] = content
    return result


@mcp.tool()
async def find_openapi_spec(
    api_url: str,
    method: str | None = None,
    owner: str | None = None,
    repo: str | None = None,
    ref: str | None = None,
    path_prefix: str | None = None,
    include_content: bool = True,
    resolve_refs: bool = True,
    max_candidates: int = 200,
) -> dict[str, Any]:
    """Find the OpenAPI spec that defines an API URL, and return it.

    api_url can be a full URL (https://api.example.com/v1/payments/123), host + path, or
    just a path, with or without a leading slash (customers/v1/address). Concrete values
    match templated paths like /v1/payments/{paymentId}. Like search_code, a fragment of a
    path also matches (v1/address finds /customers/{id}/v1/address), and if no spec defines
    a matching path, specs whose text mentions it are returned with text_matches.
    method (GET, POST, ...) is optional and breaks ties.

    Scans the repo tree directly, so it is not subject to code-search limits (large
    files, punctuation, non-default branches). Returns the ranked matching specs, the
    matched operations, and the best spec's content plus any files it pulls in via
    relative $ref. Use path_prefix to limit the scan to a folder in large repos.
    """
    client, settings = _get_client()
    owner, repo = _repo(owner, repo, settings)
    return await finder.find_spec(
        client,
        api_url,
        owner,
        repo,
        ref=ref,
        method=method,
        path_prefix=path_prefix,
        max_candidates=max_candidates,
        include_content=include_content,
        resolve_refs=resolve_refs,
        concurrency=settings.max_concurrency,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
