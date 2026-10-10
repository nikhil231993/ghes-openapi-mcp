"""Pure helpers for finding OpenAPI files and matching URLs against them.

Nothing in here does I/O, so it is easy to unit test.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

import yaml

try:  # libyaml is much faster on big specs, if it is installed
    _Loader = yaml.CSafeLoader
except AttributeError:  # pragma: no cover
    _Loader = yaml.SafeLoader

SPEC_EXTENSIONS = (".yaml", ".yml", ".json")
NAME_HINT = re.compile(r"(openapi|swagger|api[-_.]?spec|\boas\b|\bapi\b)", re.IGNORECASE)
EXCLUDED_DIRS = {"node_modules", ".github", "vendor", "dist", "build", "target", ".git"}
EXCLUDED_FILES = {
    "package.json",
    "package-lock.json",
    "composer.json",
    "renovate.json",
    "docker-compose.yml",
    "docker-compose.yaml",
    "mkdocs.yml",
    ".pre-commit-config.yaml",
}
HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


# ---- choosing which files to look at ------------------------------------------


def is_candidate(path: str) -> bool:
    lower = path.lower()
    if not lower.endswith(SPEC_EXTENSIONS):
        return False
    parts = lower.split("/")
    if any(p in EXCLUDED_DIRS for p in parts[:-1]):
        return False
    name = parts[-1]
    return name not in EXCLUDED_FILES and not name.startswith("tsconfig")


def rank_candidates(
    entries: list[dict[str, Any]], path_prefix: str | None = None
) -> list[dict[str, Any]]:
    """Filter tree entries to likely spec files, most likely first.

    Files named like openapi/swagger come first, then files in directories named
    like that, then every other YAML/JSON file (in a dedicated spec repo those are
    usually specs too).
    """
    prefix = (path_prefix or "").strip("/")
    files = [
        e
        for e in entries
        if e.get("type") == "blob"
        and is_candidate(e["path"])
        and (not prefix or e["path"].startswith(prefix + "/") or e["path"] == prefix)
    ]

    def priority(entry: dict[str, Any]) -> tuple[int, int, str]:
        directory, name = posixpath.split(entry["path"])
        if NAME_HINT.search(name):
            rank = 0
        elif NAME_HINT.search(directory) or re.search(r"specs?\b", directory, re.I):
            rank = 1
        else:
            rank = 2
        return rank, entry["path"].count("/"), entry["path"]

    return sorted(files, key=priority)


# ---- parsing -------------------------------------------------------------------


def load_spec(text: str) -> dict[str, Any] | None:
    """Parse YAML or JSON text; return it only if it looks like an OpenAPI/Swagger doc."""
    if "openapi" not in text and "swagger" not in text:
        return None
    try:
        doc = yaml.load(text, Loader=_Loader)  # noqa: S506 - safe loader
    except yaml.YAMLError:
        return None
    if isinstance(doc, dict) and ("openapi" in doc or "swagger" in doc):
        return doc
    return None


def spec_summary(doc: dict[str, Any]) -> dict[str, Any]:
    info = doc.get("info") or {}
    return {
        "title": info.get("title"),
        "version": info.get("version"),
        "spec_version": doc.get("openapi") or doc.get("swagger"),
        "servers": [s.get("url") for s in doc.get("servers") or [] if isinstance(s, dict)]
        or ([f"{doc.get('host', '')}{doc.get('basePath', '')}"] if doc.get("swagger") else []),
        "path_count": len(doc.get("paths") or {}),
    }


# ---- choosing between versions of the same spec --------------------------------

# Lifecycle states, best first. Unknown or missing states rank between ACTIVE and these.
STATUS_RANK = {"ACTIVE": 3, "CURRENT": 3, "PUBLISHED": 3, "BETA": 1, "DEPRECATED": 0, "RETIRED": 0}


def metadata_paths(spec_path: str) -> list[str]:
    """Where a spec's metadata file may live: 'a/X_v1.2.yaml' -> 'a/X_v1.2.metadata.yaml', ..."""
    stem = posixpath.splitext(spec_path)[0]
    return [f"{stem}.metadata{ext}" for ext in SPEC_EXTENSIONS]


def status_rank(status: str | None) -> int:
    return STATUS_RANK.get((status or "").strip().upper(), 2)


def version_key(version: Any) -> tuple[int, ...]:
    """Compare versions as numbers: '1.14.0' -> (1, 14, 0), which beats '1.9.0' -> (1, 9, 0)."""
    return tuple(int(n) for n in re.findall(r"\d+", str(version or "")))


def external_refs(doc: Any) -> set[str]:
    """File parts of every relative `$ref` (e.g. './schemas/pay.yaml#/Pay' -> './schemas/pay.yaml')."""
    found: set[str] = set()
    stack = [doc]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref" and isinstance(value, str):
                    file_part = value.split("#", 1)[0]
                    if file_part and not re.match(r"^[a-z]+://", file_part, re.I):
                        found.add(file_part)
                else:
                    stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    return found


def resolve_ref_path(spec_path: str, ref: str) -> str:
    """Resolve a relative $ref against the directory of the file that contains it."""
    return posixpath.normpath(posixpath.join(posixpath.dirname(spec_path), ref)).lstrip("/")


# ---- URL matching --------------------------------------------------------------


@dataclass
class PathMatch:
    path: str
    methods: list[str]
    # "exact" (server base + template), "suffix" (template at end of URL) or
    # "partial" (URL is a fragment of the full path, e.g. missing a leading segment)
    match_type: str
    score: int
    method_matched: bool | None
    host_matched: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalise_path(path: str) -> str:
    path = re.sub(r"/{2,}", "/", "/" + path.strip())
    return path.rstrip("/") or "/"


_HOST_RE = re.compile(
    r"(localhost|\d{1,3}(\.\d{1,3}){3}|([a-z0-9-]+\.)+[a-z][a-z0-9-]*)(:\d+)?", re.IGNORECASE
)


def _looks_like_host(segment: str) -> bool:
    """'api.example.com', 'localhost:8080', '10.0.0.1' yes; 'v1.0', 'customers' no."""
    return bool(_HOST_RE.fullmatch(segment))


def parse_api_url(api_url: str) -> tuple[str | None, str]:
    """Split a URL, host+path, or bare path (with or without a leading /) into (host, path)."""
    value = api_url.strip()
    if "://" not in value and not value.startswith("/"):
        first = re.split(r"[/?#]", value, maxsplit=1)[0]
        if _looks_like_host(first):  # e.g. api.example.com/v1/x
            value = "//" + value
        else:  # bare path like customers/v1/address
            value = "/" + value
    parsed = urlparse(value)
    host = parsed.hostname.lower() if parsed.hostname else None
    return host, _normalise_path(parsed.path)


def _base_paths_and_hosts(doc: dict[str, Any]) -> tuple[list[str], set[str]]:
    bases: list[str] = []
    hosts: set[str] = set()
    for server in doc.get("servers") or []:
        url = server.get("url") if isinstance(server, dict) else None
        if not url:
            continue
        parsed = urlparse(url if "://" in url or url.startswith("/") else "//" + url)
        if parsed.hostname and "{" not in parsed.netloc:
            hosts.add(parsed.hostname.lower())
        bases.append(parsed.path)
    if doc.get("swagger"):
        bases.append(doc.get("basePath") or "")
        if doc.get("host"):
            hosts.add(str(doc["host"]).split(":")[0].lower())
    return list(dict.fromkeys(bases)), hosts


def template_to_regex(template: str) -> str:
    """'/v1/payments/{id}' -> '/v1/payments/[^/]+' (literal parts escaped)."""
    pieces = re.split(r"(\{[^}/]+\})", template)
    return "".join("[^/]+" if p.startswith("{") and p.endswith("}") else re.escape(p) for p in pieces)


def _specificity(template: str) -> int:
    segments = [s for s in template.split("/") if s]
    params = sum(1 for s in segments if "{" in s)
    return (len(segments) - params) * 10 - params


def _segment_regexes(template: str) -> list[tuple[re.Pattern[str], bool]]:
    """One (regex, is_literal) per segment of a path template."""
    return [
        (re.compile(template_to_regex(seg), re.IGNORECASE), "{" not in seg)
        for seg in template.split("/")
        if seg
    ]


def _partial_match(full_template: str, url_path: str) -> int | None:
    """Specificity if url_path's segments appear as a contiguous run inside full_template.

    This mirrors how code search finds a path fragment anywhere in a file: `v1/address`
    matches `/customers/{id}/v1/address/{type}`. At least one literal segment must match,
    so a bare id never matches a lone `{param}`.
    """
    want = [s for s in url_path.split("/") if s]
    have = _segment_regexes(full_template)
    if not want or len(want) > len(have):
        return None
    for start in range(len(have) - len(want) + 1):
        window = have[start : start + len(want)]
        if all(rx.fullmatch(seg) for (rx, _), seg in zip(window, want)):
            literals = sum(1 for _, is_literal in window if is_literal)
            if literals:
                return literals * 10 - (len(want) - literals)
    return None


def match_paths(doc: dict[str, Any], api_url: str, method: str | None = None) -> list[PathMatch]:
    """Every path template in `doc` that matches `api_url`, best first."""
    host, url_path = parse_api_url(api_url)
    bases, spec_hosts = _base_paths_and_hosts(doc)
    host_matched = bool(host and host in spec_hosts)
    want_method = method.lower() if method else None

    matches: list[PathMatch] = []
    for template, item in (doc.get("paths") or {}).items():
        if not isinstance(template, str) or not isinstance(item, dict):
            continue
        tmpl = _normalise_path(template)
        tmpl_re = template_to_regex(tmpl)

        match_type = None
        specificity = _specificity(tmpl)
        for base in bases:
            if re.fullmatch(template_to_regex(_normalise_path(base + tmpl)), url_path):
                match_type = "exact"
                break
        if match_type is None:
            if tmpl == "/":
                continue
            if re.fullmatch(tmpl_re, url_path):
                match_type = "exact"
            elif re.fullmatch(r"(?:/[^?#]*)?" + tmpl_re, url_path):
                match_type = "suffix"
        if match_type is None:
            partial = [_partial_match(_normalise_path(b + tmpl), url_path) for b in bases or [""]]
            partial = [p for p in partial if p is not None]
            if not partial:
                continue
            match_type, specificity = "partial", max(partial)

        methods = sorted(m for m in item if m.lower() in HTTP_METHODS)
        method_matched = None if want_method is None else want_method in methods
        score = {"exact": 1000, "suffix": 500, "partial": 300}[match_type] + specificity
        score += 100 if host_matched else 0
        if method_matched is True:
            score += 50
        elif method_matched is False:
            score -= 300

        matches.append(
            PathMatch(
                path=template,
                methods=methods,
                match_type=match_type,
                score=score,
                method_matched=method_matched,
                host_matched=host_matched,
            )
        )

    return sorted(matches, key=lambda m: m.score, reverse=True)
