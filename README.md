# ghes-openapi-mcp

An MCP server that connects to a **GitHub Enterprise Server (GHES)** with a personal access
token (PAT), searches a spec repository, and returns the OpenAPI spec that defines a given
API URL.

It has 4 tools:

- **`search_code`, `get_repository_tree`, `get_file_contents`** work like the tools of the
  same name in [GitHub's official MCP server](https://github.com/github/github-mcp-server),
  with the same parameter names. Use them where the official server can't reach your GHES.
- **`find_openapi_spec`** does the whole "which spec defines this URL?" lookup in one call.

The server calls the GHES REST API (`https://<your-ghes>/api/v3`) directly with your PAT.

## Tools

`owner` and `repo` are optional in every tool when `GHES_DEFAULT_OWNER` and
`GHES_DEFAULT_REPO` are set.

### `search_code`

Searches code with GitHub code-search syntax.

| Parameter | Default | Meaning |
|---|---|---|
| `query` | required | Search text plus qualifiers: `repo:`, `org:`, `path:`, `filename:`, `extension:`, `in:file`, `in:path`. At most 256 characters. |
| `sort` | best match | `indexed` to sort by when the file was indexed |
| `order` | `desc` | `asc` or `desc`, used with `sort` |
| `page`, `perPage` | `1`, `30` | Paging. `perPage` is at most 100. |
| `owner`, `repo` | defaults | If the query has no `repo:`, `org:` or `user:` qualifier, the search is limited to this repo |

It returns `total_count` and, for each hit, `name`, `repository`, `path`, `sha`, `html_url`
and `text_matches` (the snippets where the words matched).

Code-search limits, the same as the official tool:
- Punctuation (`. : / ? = & #` and quotes) is ignored, so search for distinctive words
  such as `customers address`, not a full URL.
- Only the default branch is indexed.
- Files over 384 KB aren't found.
- Code search must be enabled on your GHES.

### `get_repository_tree`

Lists the files and folders in a repo.

| Parameter | Default | Meaning |
|---|---|---|
| `owner`, `repo` | defaults | The repo |
| `tree_sha` | default branch | A branch, tag, commit SHA or tree SHA. `refs/heads/<branch>` also works. |
| `recursive` | `false` | `false` lists only the top level. `true` lists everything. |
| `path_filter` | none | Keep only paths that start with this prefix, e.g. `customers/profile` |
| `spec_files_only` | `false` | Extra, not in the official tool: keep only YAML/JSON files that could be OpenAPI specs |
| `limit` | `2000` | Extra: the most entries to return |

It returns each entry's `path`, `type` (`blob` = file, `tree` = folder), `size` and `sha`.

### `get_file_contents`

Gets a file's raw content, or lists a folder.

| Parameter | Default | Meaning |
|---|---|---|
| `owner`, `repo` | defaults | The repo |
| `path` | `/` | File or folder path. A folder returns a listing. |
| `ref` | default branch | `main`, `refs/heads/<branch>`, `refs/tags/<tag>`, `refs/pull/<n>/head`, or a SHA |
| `sha` | none | A commit SHA. Takes precedence over `ref`. |

Files up to 100 MB work. If a spec uses relative `$ref` to other files, call this tool again
for each of them, or use `find_openapi_spec`, which follows them for you.

### `find_openapi_spec`

Finds the spec that defines an API URL. It searches, locates and fetches in one call.

| Parameter | Default | Meaning |
|---|---|---|
| `api_url` | required | A full URL (`https://api.example.com/customers/v1/customers/C123`), host and path, or just a path with or without a leading `/` (`customers/v1/customers/C123`). Part of a path also works (`v1/address`). |
| `method` | none | `GET`, `POST`, … Optional. Breaks ties between operations. |
| `owner`, `repo` | defaults | The spec repo |
| `ref` | default branch | Branch, tag or SHA to search |
| `path_prefix` | none | Only scan this folder. Speeds up large repos. |
| `include_content` | `true` | Return the best spec's content |
| `resolve_refs` | `true` | Also return the files the best spec pulls in through relative `$ref` |
| `max_candidates` | `200` | The most YAML/JSON files to scan |

It returns:
- `matches`: the ranked specs, each with its `matched_operations`
- `best`: the top spec's path and content, plus its `$ref` files
- `notes`: warnings, e.g. when two specs match equally well

Why use it alongside the 3 GitHub tools:
- Concrete values match templates: `C123` matches `/customers/{customerId}`. Code search
  only matches the literal text.
- It reads files directly instead of using code search, so it still works when code
  search is off, and it isn't limited to files under 384 KB or to the default branch.
- If no spec defines a matching path, it falls back to a text search of the specs, the way
  `search_code` would, and returns the files and lines that mention the path.

#### How matching works

1. Read the repo tree (the default branch unless `ref` is given).
2. Keep the `.yaml`, `.yml` and `.json` files, skipping `node_modules`, `package.json` and
   similar. Files named like `openapi`, `swagger` or `api` are tried first.
3. Fetch them in parallel. Files are cached by SHA for as long as the server runs. Keep only
   the files with a top-level `openapi` or `swagger` key.
4. Score each path template against the URL:
   - **exact**: the URL matches the server base path plus the template
   - **suffix**: the template matches the end of the URL, e.g. behind a gateway prefix
   - **partial**: the URL is part of the full path, e.g. `v1/address` matches
     `/customers/{id}/v1/address`. At least one literal segment must match.
   - There are bonuses for literal segments over `{params}`, a matching host and a matching method.
5. If nothing matches, fall back to a case-insensitive text search of the specs.
6. Return the top specs. The best one comes with its content and its `$ref` files.

## Setup

You need Python 3.10 or later and pip.

### 1. Install

```bash
cd ghes-openapi-mcp
python3 -m venv .venv
source .venv/bin/activate                  # Windows: .venv\Scripts\activate
pip install -r requirements.txt -e .       # use requirements-dev.txt to also run the tests
```

This installs the pinned dependencies and the `ghes-openapi-mcp` command into
`.venv/bin/ghes-openapi-mcp` (Windows: `.venv\Scripts\ghes-openapi-mcp.exe`). The client
configs below start that command by its absolute path, so the venv doesn't need to be activated.

### 2. Create a token

On your GHES, go to your avatar → **Settings** → **Developer settings** → **Personal access
tokens**:

- **Fine-grained token**: under repository access, pick the spec repo. Set the
  **Contents** permission to **Read-only**. **Metadata: Read-only** is added automatically.
- **Classic token**: the `repo` scope.

If your org enforces SSO, also authorize the token for the org (**Configure SSO**).

### 3. Configure

```bash
cp .env.example .env
```

Then edit `.env`. The server reads it automatically, from the folder it's started in or from
this project folder. Variables set by the MCP client's `env` block take precedence.

| Variable | Required | Meaning |
|---|---|---|
| `GHES_BASE_URL` | yes | e.g. `https://github.your-company.internal` (`/api/v3` is added automatically). Use `https://github.com` to test against github.com. |
| `GHES_TOKEN` | yes | The PAT from step 2 |
| `GHES_DEFAULT_OWNER`, `GHES_DEFAULT_REPO` | no | The spec repo to use when a call doesn't name one |
| `GHES_CA_BUNDLE` | no | Path to your corporate root CA file (PEM), if you get TLS errors |
| `GHES_VERIFY_SSL` | no | `false` turns off TLS checks. Use it only for local debugging. |
| `GHES_TIMEOUT` | no | Seconds per request. Default `30`. |
| `GHES_MAX_CONCURRENCY` | no | Parallel file fetches. Default `8`. |

`.env` is in `.gitignore`. Never commit it.

#### Finding the host, owner and repo

Open the spec repo in your browser. Its address has the form `https://<host>/<owner>/<repo>`:

| Repo URL | `GHES_BASE_URL` | `GHES_DEFAULT_OWNER` | `GHES_DEFAULT_REPO` |
|---|---|---|---|
| `https://github.com/nikhil231993/contract-api-specs` | `https://github.com` | `nikhil231993` | `contract-api-specs` |
| `https://github.your-company.internal/api-platform/openapi-specs` | `https://github.your-company.internal` | `api-platform` | `openapi-specs` |

The owner is either a user (a person's username) or an organization (a team or group).
Create the token on the same host as `GHES_BASE_URL`: a github.com token doesn't work on
your GHES, and a GHES token doesn't work on github.com.

Example `.env` for a company GHES:

```
GHES_BASE_URL=https://github.your-company.internal
GHES_TOKEN=github_pat_xxxxxxxx
GHES_DEFAULT_OWNER=api-platform
GHES_DEFAULT_REPO=openapi-specs
GHES_CA_BUNDLE=/path/to/company-root-ca.pem     # only if you get TLS errors
```

### 4. Check the token

```bash
set -a && source .env && set +a
curl -H "Authorization: Bearer $GHES_TOKEN" "$GHES_BASE_URL/api/v3/user"    # github.com: https://api.github.com/user
```

You should see your user. A 401 means the token is wrong or expired.

## Which token to use

### On your own machine: your personal token

A personal access token (PAT) acts as you. This is the same model as GitHub's official MCP server.

- **It sees only what you can see.** The server can read a repo only if your account can
  open it in the browser. If you lose access, so does the server.
- **Make it read-only.** Use a fine-grained token for just the spec repo, with
  **Contents: Read-only**. Then it can't change anything even if it leaks. The tools only read anyway.
- **It stays on your machine.** It lives in your local `.env`, which is excluded from git,
  and is only sent to `GHES_BASE_URL`.
- **Each person uses their own token.** Teammates create their own PATs and keep them in
  their own `.env`. Never share a token.
- **Give it a short expiry,** for example 30–90 days.
- **If it leaks,** delete it on the token page and create a new one. The old one stops
  working immediately.

To avoid keeping the token in a file at all, use the VS Code configuration with `inputs`
(see below). VS Code asks for the token and keeps it in its secure storage.

### In a shared place (team server, CI, a hosted agent): a service account

Don't run a shared deployment on one person's PAT. It stops working when that person leaves
or their access changes, and every request is logged as them. Ask your GitHub admins for one of these:

| Option | How | Works with this code |
|---|---|---|
| **Service account** (recommended) | A non-person GitHub user (a "machine user") with read access to the spec repo, and a fine-grained read-only PAT for it. Set that token as `GHES_TOKEN`. | Yes, as is |
| **GitHub App** | An app installed on the org with **Contents: Read-only**. GitHub recommends it for shared tools. | No. Apps use short-lived installation tokens, so the code would need changes. |

On the shared host, set `GHES_TOKEN` through the platform's secret store (for example a
Kubernetes secret, Vault or CI secrets) as an environment variable rather than a `.env`
file. Environment variables take precedence over `.env`. Rotate the token before it expires.

### Company rules to check

Companies often restrict tokens. If you get a 403 from GHES although you can open the repo
in your browser, check whether:
- the org must **approve fine-grained tokens** before they work;
- the token must be **authorized for SSO** (token page → **Configure SSO**);
- the org sets a **maximum token lifetime**, or allows only classic or only fine-grained tokens.

## Run it

The server speaks MCP over stdio: an MCP client starts it and talks to it through
stdin and stdout. You don't normally run it by hand. If you do run it from a terminal,
it starts and waits silently for a client; press Ctrl+C to stop it.

```bash
.venv/bin/ghes-openapi-mcp
```

### Try it in the MCP Inspector (browser UI)

```bash
npx @modelcontextprotocol/inspector /absolute/path/to/ghes-openapi-mcp/.venv/bin/ghes-openapi-mcp
```

Open the URL it prints, click **Connect**, then **Tools** → **List Tools**, and call any tool.

### Claude Code

```bash
claude mcp add ghes-openapi -- /absolute/path/to/ghes-openapi-mcp/.venv/bin/ghes-openapi-mcp
```

The token and other settings come from `.env`. To pass them explicitly instead, add
`-e GHES_BASE_URL=… -e GHES_TOKEN=…` before the `--`. Check the connection with `claude mcp list`.

### VS Code (Copilot agent mode): `.vscode/mcp.json`

```json
{
  "servers": {
    "ghes-openapi": {
      "type": "stdio",
      "command": "/absolute/path/to/ghes-openapi-mcp/.venv/bin/ghes-openapi-mcp"
    }
  }
}
```

To have VS Code ask for the token instead of keeping it in `.env`:

```json
{
  "inputs": [
    { "type": "promptString", "id": "ghes-token", "description": "GHES PAT", "password": true }
  ],
  "servers": {
    "ghes-openapi": {
      "type": "stdio",
      "command": "/absolute/path/to/ghes-openapi-mcp/.venv/bin/ghes-openapi-mcp",
      "env": {
        "GHES_BASE_URL": "https://github.your-company.internal",
        "GHES_TOKEN": "${input:ghes-token}",
        "GHES_DEFAULT_OWNER": "api-platform",
        "GHES_DEFAULT_REPO": "openapi-specs"
      }
    }
  }
}
```

### Claude Desktop: `claude_desktop_config.json`

```json
{
  "mcpServers": {
    "ghes-openapi": {
      "command": "/absolute/path/to/ghes-openapi-mcp/.venv/bin/ghes-openapi-mcp"
    }
  }
}
```

On Windows, the command is `.venv\Scripts\ghes-openapi-mcp.exe`. In JSON, double the backslashes:
`"C:\\path\\to\\ghes-openapi-mcp\\.venv\\Scripts\\ghes-openapi-mcp.exe"`.

## Example calls

```jsonc
// One call: which spec defines this endpoint? Returns the spec and its $ref'd files.
find_openapi_spec { "api_url": "https://api.example-bank.com/customers/v1/customers/C123", "method": "GET" }
find_openapi_spec { "api_url": "customers/v1/customers/C123" }       // path only
find_openapi_spec { "api_url": "accounts/A1/balance" }               // part of a path
find_openapi_spec { "api_url": "/v1/payments", "path_prefix": "payments" }

// The same lookup with the 3 GitHub-style tools, step by step
search_code         { "query": "customerId repo:my-org/contract-api-specs" }           // 1. where is it used?
search_code         { "query": "CMCustomerProfile in:path repo:my-org/contract-api-specs" }
get_repository_tree { "owner": "my-org", "repo": "contract-api-specs", "recursive": true,
                      "path_filter": "customers" }                                       // 2. where is the spec?
get_file_contents   { "owner": "my-org", "repo": "contract-api-specs",
                      "path": "customers/profile/apis/CMCustomerProfile_v1.0.yaml",
                      "ref": "refs/heads/main" }                                         // 3. pull it
```

## Compared with GitHub's official MCP server

We ran the official `github-mcp-server` (v2.0.2) and this server side by side against a
github.com spec repo, with the same token and the same inputs:

| Tool | Calls | Result |
|---|---|---|
| `search_code` | 3 queries (plain words, `in:path`, `perPage`) | Same hits, SHAs and `text_matches` |
| `get_repository_tree` | recursive, `path_filter`, top level only | Same entries and SHAs |
| `get_file_contents` | file, file at `refs/heads/main`, folder | Byte-identical content, same listing |

The JSON around the results differs. For example, the official server returns the raw
GitHub API objects with extra URL fields. The data inside is the same.

The official server also supports GHES through its `--gh-host` option. If you are allowed to
run its binary against your GHES, it's an alternative to the 3 GitHub-style tools.

## Troubleshooting

| Error | Fix |
|---|---|
| `GHES_BASE_URL is not set` / `GHES_TOKEN is not set` | Fill in `.env`, or pass them in the client's `env` block |
| 401 `rejected the token` | The token is wrong or has expired. Create a new one. |
| 403 `Forbidden` | The token can't see the repo. Check its repository access, the Contents permission and SSO authorization. |
| 403 `rate limit` | Wait until the reset time in the message. GHES limits are set by your admins. |
| 404 `Not found` | Check the owner, repo, path and ref. A token without access to a private repo also gets a 404. |
| TLS or certificate errors | Set `GHES_CA_BUNDLE` to your corporate root CA file |
| `search_code` returns nothing or an error | Code search may be disabled on your GHES, or the file is over 384 KB or not on the default branch. Use `find_openapi_spec` or `get_repository_tree` with `get_file_contents`. |

## Development

```bash
pip install -r requirements-dev.txt -e .
pytest
```

The tests use a mocked GHES API (`httpx.MockTransport`), so they need no network or token.

### Dependency files

| File | Used by | Contents |
|---|---|---|
| `pyproject.toml` | `pip install -e .` | Package metadata, the `ghes-openapi-mcp` command, and the allowed version ranges (`mcp`, `httpx`, `pyyaml`; `pytest` under the `dev` extra) |
| `requirements.txt` | `pip install -r` | Exact pinned versions of every package needed to run the server, so every machine installs the same thing |
| `requirements-dev.txt` | `pip install -r` | `requirements.txt` plus `pytest` and `pytest-asyncio`, for running the tests |

To add or upgrade a dependency:

1. Change its version range in `pyproject.toml`.
2. Install it in the venv, e.g. `pip install -U httpx`, and run the tests.
3. Copy the version `pip show httpx` reports into `requirements.txt`, plus any new package it
   depends on (`pip freeze` lists everything installed). Keep the `; python_full_version < '3.11'`
   markers on the lines that have them; those packages are only needed on older Pythons.

### Project layout

```
src/ghes_openapi_mcp/
  server.py         MCP tool definitions
  github_client.py  GHES REST API client (auth, errors, caching)
  finder.py         find_openapi_spec: scan, match, resolve $ref
  spec.py           URL parsing and path-template matching (no I/O)
  config.py         Settings from the environment or .env
tests/              Mocked-GHES tests
```
