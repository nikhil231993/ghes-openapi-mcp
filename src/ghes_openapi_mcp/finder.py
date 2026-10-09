"""Find the OpenAPI spec in a repo that defines a given API URL."""

from __future__ import annotations

import asyncio
from typing import Any

import yaml

from .github_client import GHESClient, GHESError
from .spec import (
    external_refs,
    load_spec,
    match_paths,
    parse_api_url,
    rank_candidates,
    resolve_ref_path,
    spec_summary,
)

MAX_REF_FILES = 200
MAX_TEXT_HITS = 5


def _text_hits(text: str, needle: str) -> list[dict[str, Any]]:
    """Lines containing needle, case-insensitive, the way code search would find them."""
    needle = needle.lower()
    hits = []
    for number, line in enumerate(text.splitlines(), 1):
        if needle in line.lower():
            hits.append({"line": number, "text": line.strip()[:200]})
            if len(hits) >= MAX_TEXT_HITS:
                break
    return hits


async def _load_candidates(
    client: GHESClient,
    owner: str,
    repo: str,
    entries: list[dict[str, Any]],
    concurrency: int,
) -> list[tuple[str, str, dict[str, Any]]]:
    """Fetch candidate files in parallel; keep the ones that parse as OpenAPI/Swagger."""
    sem = asyncio.Semaphore(concurrency)

    async def load(entry: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
        async with sem:
            try:
                text = await client.get_blob(owner, repo, entry["sha"])
            except GHESError:
                return None
        doc = load_spec(text)
        return (entry["path"], text, doc) if doc is not None else None

    results = await asyncio.gather(*(load(e) for e in entries))
    return [r for r in results if r is not None]


async def _resolve_refs(
    client: GHESClient,
    owner: str,
    repo: str,
    root_path: str,
    root_doc: dict[str, Any],
    blob_shas: dict[str, str],
    ref: str,
) -> dict[str, Any]:
    """Fetch every file reachable through relative $refs. Returns {'files', 'missing'}."""
    files: dict[str, str] = {}
    missing: list[str] = []
    queue = [resolve_ref_path(root_path, r) for r in external_refs(root_doc)]
    seen = {root_path, *queue}

    while queue and len(files) < MAX_REF_FILES:
        path = queue.pop(0)
        try:
            sha = blob_shas.get(path)
            text = await (client.get_blob(owner, repo, sha) if sha else client.get_file(owner, repo, path, ref))
        except GHESError:
            missing.append(path)
            continue
        files[path] = text
        try:
            nested = external_refs(yaml.safe_load(text))
        except yaml.YAMLError:
            continue
        for r in nested:
            target = resolve_ref_path(path, r)
            if target not in seen:
                seen.add(target)
                queue.append(target)

    return {"files": files, "missing": missing}


async def find_spec(
    client: GHESClient,
    api_url: str,
    owner: str,
    repo: str,
    ref: str | None = None,
    method: str | None = None,
    path_prefix: str | None = None,
    max_candidates: int = 200,
    include_content: bool = True,
    resolve_refs: bool = True,
    top_n: int = 5,
    concurrency: int = 8,
) -> dict[str, Any]:
    tree = await client.get_tree(owner, repo, ref)
    blob_shas = {e["path"]: e["sha"] for e in tree["entries"] if e.get("type") == "blob"}
    candidates = rank_candidates(tree["entries"], path_prefix)
    specs = await _load_candidates(client, owner, repo, candidates[:max_candidates], concurrency)

    ranked: list[dict[str, Any]] = []
    texts: dict[str, tuple[str, dict[str, Any]]] = {}
    for path, text, doc in specs:
        matches = match_paths(doc, api_url, method)
        if not matches:
            continue
        texts[path] = (text, doc)
        ranked.append(
            {
                "path": path,
                "score": matches[0].score,
                **spec_summary(doc),
                "matched_operations": [m.to_dict() for m in matches[:10]],
            }
        )
    ranked.sort(key=lambda r: r["score"], reverse=True)

    # No spec defines a matching path, so fall back to a plain text search of the specs,
    # like search_code: the path may appear in a description, an x- extension, a $ref...
    text_search = None
    if not ranked:
        text_search = parse_api_url(api_url)[1].strip("/")
        for path, text, doc in specs if text_search else []:
            hits = _text_hits(text, text_search)
            if hits:
                texts[path] = (text, doc)
                ranked.append(
                    {"path": path, "score": len(hits), **spec_summary(doc), "text_matches": hits}
                )
        ranked.sort(key=lambda r: r["score"], reverse=True)

    result: dict[str, Any] = {
        "query": {"api_url": api_url, "method": method},
        "repo": f"{owner}/{repo}",
        "ref": tree["ref"],
        "tree_truncated": tree["truncated"],
        "candidate_files": len(candidates),
        "candidates_scanned": min(len(candidates), max_candidates),
        "specs_parsed": len(specs),
        "matches": ranked[:top_n],
        "best": None,
    }
    notes = []
    if tree["truncated"]:
        notes.append("The repo tree was truncated by GHES; use path_prefix to narrow the scan.")
    if len(candidates) > max_candidates:
        notes.append(
            f"Only the first {max_candidates} of {len(candidates)} candidate files were scanned; "
            "raise max_candidates or set path_prefix."
        )

    if text_search is not None:
        if not ranked:
            notes.append(
                "No spec defines or mentions this path. Check the path, try another ref, "
                "or use search_code to look outside OpenAPI files."
            )
        else:
            notes.append(
                f"No spec defines a path matching this URL; these specs mention '{text_search}' "
                "in their text (see text_matches)."
            )
    if not ranked:
        result["notes"] = notes
        return result

    best_path = ranked[0]["path"]
    text, doc = texts[best_path]
    best: dict[str, Any] = {"path": best_path}
    if include_content:
        best["content"] = text
    if resolve_refs:
        refs = await _resolve_refs(client, owner, repo, best_path, doc, blob_shas, tree["ref"])
        if refs["files"] and include_content:
            best["referenced_files"] = refs["files"]
        else:
            best["referenced_file_paths"] = sorted(refs["files"])
        if refs["missing"]:
            best["missing_refs"] = refs["missing"]
    if len(ranked) > 1 and ranked[1]["score"] == ranked[0]["score"]:
        notes.append("More than one spec matched equally well; check 'matches' before trusting 'best'.")

    result["best"] = best
    if notes:
        result["notes"] = notes
    return result
