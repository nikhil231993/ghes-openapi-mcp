"""Thin async client for the GitHub Enterprise Server REST API (v3)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from .config import Settings

RAW = "application/vnd.github.v3.raw"
JSON = "application/vnd.github+json"
TEXT_MATCH = "application/vnd.github.text-match+json"  # adds matched snippets to search hits


class GHESError(RuntimeError):
    """An error from GHES, with a message that is useful to show to the model."""


def normalise_ref(ref: str | None) -> str | None:
    """Accept the official MCP server's ref forms: refs/heads/<b> and refs/tags/<t> become
    <b> and <t>. Other refs (refs/pull/<n>/head, SHAs, plain names) pass through."""
    if not ref:
        return None
    for prefix in ("refs/heads/", "refs/tags/"):
        if ref.startswith(prefix):
            return ref[len(prefix) :]
    return ref


class GHESClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(
            base_url=settings.api_url,
            headers={
                "Authorization": f"Bearer {settings.token}",
                "Accept": JSON,
                "User-Agent": "ghes-openapi-mcp",
            },
            verify=settings.verify,
            timeout=settings.timeout,
            transport=transport,
        )
        self._default_branch: dict[str, str] = {}
        self._blob_cache: dict[str, str] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(
        self, url: str, params: dict[str, Any] | None = None, accept: str | None = None
    ) -> httpx.Response:
        headers = {"Accept": accept} if accept else None
        try:
            resp = await self._client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise GHESError(f"Could not reach GHES at {self._client.base_url}: {exc}") from exc

        if resp.status_code < 400:
            return resp

        try:
            detail = resp.json().get("message", resp.text)
        except ValueError:
            detail = resp.text
        detail = (detail or "").strip()[:300]

        if resp.status_code == 401:
            raise GHESError("GHES rejected the token (401). Check GHES_TOKEN is valid and not expired.")
        if resp.status_code == 403:
            if resp.headers.get("x-ratelimit-remaining") == "0":
                raise GHESError(
                    f"GHES rate limit hit (resets at epoch {resp.headers.get('x-ratelimit-reset')}). "
                    "Wait and retry."
                )
            raise GHESError(
                f"Forbidden (403): {detail}. The PAT may lack 'repo' scope or SSO authorization."
            )
        if resp.status_code == 404:
            raise GHESError(
                f"Not found (404) for {url}. The repo/path/ref may not exist, "
                "or the token cannot see it."
            )
        if resp.status_code == 422:
            raise GHESError(f"GHES could not process the request (422): {detail}")
        raise GHESError(f"GHES returned {resp.status_code} for {url}: {detail}")

    # ---- repos -------------------------------------------------------------

    async def get_default_branch(self, owner: str, repo: str) -> str:
        key = f"{owner}/{repo}"
        if key not in self._default_branch:
            resp = await self._get(f"/repos/{owner}/{repo}")
            self._default_branch[key] = resp.json()["default_branch"]
        return self._default_branch[key]

    async def get_tree(
        self, owner: str, repo: str, ref: str | None = None, recursive: bool = True
    ) -> dict[str, Any]:
        """Tree listing. Returns {'ref', 'sha', 'truncated', 'entries'}."""
        ref = normalise_ref(ref) or await self.get_default_branch(owner, repo)
        resp = await self._get(
            f"/repos/{owner}/{repo}/git/trees/{quote(ref, safe='')}",
            params={"recursive": "1"} if recursive else None,
        )
        data = resp.json()
        return {
            "ref": ref,
            "sha": data.get("sha"),
            "truncated": bool(data.get("truncated")),
            "entries": data.get("tree", []),
        }

    async def get_contents(
        self, owner: str, repo: str, path: str, ref: str | None = None
    ) -> str | list[dict[str, Any]]:
        """Raw file contents (works up to 100 MB), or the entries if path is a directory."""
        ref = normalise_ref(ref)
        resp = await self._get(
            f"/repos/{owner}/{repo}/contents/{quote(path.strip('/'), safe='/')}",
            params={"ref": ref} if ref else None,
            accept=RAW,
        )
        if resp.headers.get("content-type", "").startswith("application/json"):
            # A directory listing comes back as JSON even with the raw media type.
            body = resp.json()
            if isinstance(body, list):
                return body
        return resp.text

    async def get_file(self, owner: str, repo: str, path: str, ref: str | None = None) -> str:
        content = await self.get_contents(owner, repo, path, ref)
        if isinstance(content, list):
            raise GHESError(f"'{path}' is a directory, not a file.")
        return content

    async def get_blob(self, owner: str, repo: str, sha: str) -> str:
        """Raw blob contents by SHA. Cached, because blob SHAs are immutable."""
        if sha not in self._blob_cache:
            resp = await self._get(f"/repos/{owner}/{repo}/git/blobs/{sha}", accept=RAW)
            self._blob_cache[sha] = resp.text
        return self._blob_cache[sha]

    # ---- search ------------------------------------------------------------

    async def search_code(
        self,
        query: str,
        per_page: int = 30,
        page: int = 1,
        sort: str | None = None,
        order: str | None = None,
    ) -> dict[str, Any]:
        params = {"q": query, **_paging(per_page, page)}
        if sort:
            params["sort"] = sort
            params["order"] = order or "desc"
        resp = await self._get("/search/code", params=params, accept=TEXT_MATCH)
        return resp.json()


def _paging(per_page: int, page: int) -> dict[str, int]:
    return {"per_page": max(1, min(per_page, 100)), "page": max(page, 1)}
