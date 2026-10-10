# Notes for AI coding agents

An MCP server (Python, stdio) that finds OpenAPI specs in a GitHub Enterprise Server repo.
Read `README.md` first: it documents every tool, the setup, and the known gaps.

- **Debugging:** follow README → Troubleshooting → "Find where it fails", layer by layer.
  Ask for the output of the first failing layer before changing code; most failures on a
  company laptop are network, proxy, TLS or token problems, not code.
- **Planned work:** README → "Known limitations and next steps" lists the open problems in
  priority order, with the functions to change.
- **Code layout:** `server.py` (tool definitions) → `finder.py` (`find_openapi_spec` logic) →
  `spec.py` (pure parsing and matching, no I/O) and `github_client.py` (REST calls, errors,
  caching). Settings come from `.env` via `config.py`.
- **Tests:** `.venv/bin/python -m pytest -q`. They mock GHES with `httpx.MockTransport`, so they
  need no network or token. Add a test for every change.
- **Never** commit `.env`, tokens, or company hostnames, org or repo names. This repo is public.
- Keep the 3 GitHub-style tools' parameter names identical to GitHub's official MCP server.
