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
- `matches`: the ranked specs, each with its `matched_operations`, `version` and `status`
  (from its metadata file, see below). Every spec tied for the top score is listed, plus
  lower-scoring ones up to 5 in total.
- `total_matches`: how many specs matched in all
- `best`: the top spec's path and content, plus its `$ref` files
- `notes`: warnings, e.g. which of several equally good specs was picked, and why

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
6. Rank the specs by score. When several tie, which is common when every version of an
   API defines the same path, prefer:
   1. the spec whose metadata file says `status: ACTIVE`. The metadata file sits next to
      the spec: `X_v1.14.yaml` → `X_v1.14.metadata.yaml` (or `.yml`/`.json`). `DEPRECATED`
      and `RETIRED` rank lowest; specs without a metadata file rank in between.
   2. then the highest `info.version`, compared as numbers (1.14 beats 1.9).

   Score always comes first: an exact match in a `DEPRECATED` spec still beats a weaker
   match in an `ACTIVE` one.
7. Return the specs. The best one comes with its content and its `$ref` files.

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

### Cursor: `.cursor/mcp.json`

Create `.cursor/mcp.json` in the project you open in Cursor (or `~/.cursor/mcp.json` to use it
in every project):

```json
{
  "mcpServers": {
    "ghes-openapi": {
      "command": "/absolute/path/to/ghes-openapi-mcp/.venv/bin/ghes-openapi-mcp"
    }
  }
}
```

The settings come from this project's `.env`, wherever Cursor is opened. Then:

1. Open **Cursor Settings → MCP** (**Tools & MCP** in newer versions). `ghes-openapi` should
   show a green dot and 4 tools. A red dot means the server failed to start; click it to see
   the error, which is usually a missing `.env` value or a wrong path.
2. Open the chat in **Agent** mode. MCP tools are only used in Agent mode.
3. Ask, for example: *"Use find_openapi_spec to find the spec for GET /payments/v1/payments/123
   and show the operation."* Cursor asks before running each tool call unless you allow it.

After changing `.env` or the code, turn the server off and on in the MCP settings so it restarts.

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

## Using it with a dependency graph

The intended flow: a code dependency graph tool (for example `downstream_apis`) lists the APIs a
service calls, and this server finds the OpenAPI spec for each one.

```
downstream_apis(service)                 →  ["GET https://pay.example.internal/payments/v1/payments/{id}", ...]
for each endpoint:
  find_openapi_spec(api_url=<url or path>, method=<GET/POST/...>, include_content=false)
                                         →  best.path, matched_operations, status, version
  get_file_contents(path=best.path)      →  the spec, only when the full text is needed
```

- **Pass the endpoint as it comes.** Full URLs, host plus path, bare paths, concrete IDs
  (`/payments/123`) and code placeholders (`${id}`, `:id`, `%s`) all match `{param}` templates.
  A query string is ignored.
- **Pass `method`** when you know it. It breaks ties between operations on the same path.
- **Use `include_content=false`** when looking up many endpoints. It returns only the ranking,
  which is much smaller. Fetch the one spec you need afterwards.
- **Check `notes`** in each result. They say when several versions tied and which one was
  picked, or when the scan was cut short.
- **If the graph returns a client method name** instead of a URL (for example
  `PaymentsClient.getPayment`), `find_openapi_spec` can't match it. Search for the `operationId`
  with `search_code` instead, or add operationId matching (see
  [Known limitations](#known-limitations-and-next-steps)).

An instruction you can give the agent:

> For each API returned by downstream_apis, call find_openapi_spec with the URL and the HTTP
> method, and include_content=false. Report the spec path, the status, the version and the
> matched operation. If notes say the result is uncertain, show them.

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

## First test on a company GHES

A checklist for the first run on a work laptop. Do it from inside the company network or VPN:
a company GHES is usually not reachable from outside.

The commands are for macOS or Linux (bash or zsh) and need `jq`. On Windows, run them in
Git Bash or WSL, and use `.venv\Scripts\ghes-openapi-mcp.exe` as the server path. Or skip the
commands and call the tools from the Inspector browser UI or from Cursor (see [Cursor](#cursor-cursormcpjson)).

### 1. Install and configure

Follow [Setup](#setup) steps 1–4. Take `GHES_BASE_URL`, `GHES_DEFAULT_OWNER` and
`GHES_DEFAULT_REPO` from the spec repo's browser address, `https://<host>/<owner>/<repo>`, and
copy the repo name exactly. Keep these values in `.env` only, not in any committed file.

Check that the fast YAML parser is installed. Large specs parse much more slowly without it:

```bash
.venv/bin/python -c "import yaml; print(yaml.__with_libyaml__)"    # should print True
```

### 2. Call each tool with the Inspector CLI

Run these from the project folder so the server picks up `.env`. `2>/dev/null` hides its
HTTP log lines. `npx` needs access to the npm registry; if it's blocked, use the browser UI
or your MCP client instead.

```bash
g() { npx -y @modelcontextprotocol/inspector --cli ./.venv/bin/ghes-openapi-mcp "$@"; }   # works in bash and zsh

# a. Can it read the repo? How big is it?
g --method tools/call --tool-name get_repository_tree \
  --tool-arg recursive=true --tool-arg spec_files_only=true --tool-arg limit=5000 \
  2>/dev/null > tree.json

# b. Is code search enabled on this GHES?
g --method tools/call --tool-name search_code \
  --tool-arg query=openapi --tool-arg perPage=5 2>/dev/null

# c. Does a real endpoint resolve to the right spec? (use one your code actually calls)
g --method tools/call --tool-name find_openapi_spec \
  --tool-arg api_url=/payments/v1/payments/123 --tool-arg method=GET \
  --tool-arg include_content=false 2>/dev/null

# d. Can it fetch a spec file? (a path from tree.json)
g --method tools/call --tool-name get_file_contents \
  --tool-arg path=<path/from/tree.json> 2>/dev/null | head -c 500
```

Summarise `tree.json`:

```bash
jq '.content[0].text | fromjson | {tree_truncated, spec_files: .total_entries,
    over_384KB: [.entries[] | select(.size > 393216)] | length,
    largest_KB: ([.entries[].size] | max / 1024 | floor)}' tree.json
```

### 3. Read the results

| Check | Good | If not |
|---|---|---|
| a. tree call | Returns entries | 401/403/404: see [Troubleshooting](#troubleshooting). TLS error: set `GHES_CA_BUNDLE`. |
| `tree_truncated` | `false` | The repo is too large for one tree listing. Pass `path_prefix` to `find_openapi_spec`. |
| `spec_files` | 200 or fewer | `find_openapi_spec` scans only the first `max_candidates` (default 200) files, in path order, and `.metadata.yaml` files count towards that. Pass a larger `max_candidates` or a `path_prefix`, and expect slow calls. |
| `over_384KB` | `0` | Code search can't see those files. `find_openapi_spec` and `get_file_contents` still can. |
| b. search | Returns hits | Code search is disabled or not indexed on this GHES. The other three tools don't use it. |
| c. find | `best.path` is the spec you expected | Check `matches` and `notes`. Try the full URL, or set `path_prefix`. |
| d. file | Spec text | Check the path and the ref. |

Note how long call c takes. Each call downloads and parses every candidate spec again, so a
repo of large bundled specs makes it slow.

## Known limitations and next steps

The server has been tested against a small github.com repo (24 YAML files), not yet against a
company GHES. These are the known gaps for a large central spec repo, most important first.
Each one names the code to change. Run `pytest` after every change and add a test next to the
existing ones in `tests/`.

### 1. Only the first 200 files are scanned

`find_openapi_spec` scans at most `max_candidates` (default 200) YAML/JSON files.
`rank_candidates` in `spec.py` sorts them so that names containing `openapi`, `swagger` or `api`
come first, then by folder depth, then alphabetically. Names like `Payments_v1.14.yaml` get no
priority, so in a repo with more than 200 files the specs of later folders are never read and
the tool reports no match although the spec exists.

Fix: remove the cap, or raise it well above the repo's file count. Together with fix 3 this
costs little, because each file is parsed only once.

### 2. Metadata files count as candidates

`*.metadata.yaml` files pass `is_candidate` in `spec.py`, take up candidate slots and are
downloaded, then dropped by `load_spec` because they have no `openapi` key.

Fix: in `is_candidate`, return `False` for names ending in `.metadata.yaml`, `.metadata.yml`
or `.metadata.json`. The status lookup reads them separately (`metadata_paths`,
`_load_status` in `finder.py`), so nothing else changes.

### 3. Every call re-reads the whole repo

`find_spec` in `finder.py` fetches the tree, then downloads and parses every candidate on each
call. File content is cached by SHA (`get_blob` in `github_client.py`), but parsing isn't, and
the tree is fetched again each time. With hundreds of large bundled specs and dozens of
endpoints, this is slow and uses many API requests.

Fix: build an index once and reuse it.
- Key it by the tree SHA, which `get_tree` already returns, so it rebuilds by itself when the
  repo changes.
- Per spec, store the path, `spec_summary(doc)`, the server base paths
  (`_base_paths_and_hosts`), the path templates with their methods, and the metadata status.
  Don't keep the parsed documents: they are large.
- Look endpoints up in the index with `match_paths`, which only needs `paths` and `servers`.
  Load the best spec's text only when `include_content=true`.
- Optionally also look up the current tree SHA at most once a minute, to save the tree request.

### 4. No retries

`_get` in `github_client.py` fails at once on a 5xx error, a 429, or a 403 caused by a secondary
rate limit. A lookup over many endpoints will hit these.

Fix: retry up to 3 times with exponential backoff, waiting for the `Retry-After` header when
GitHub sends one. Don't retry other 4xx errors.

### 5. Client method names don't match

`find_openapi_spec` matches URLs and paths only. If the dependency graph returns
`PaymentsClient.getPayment` instead of a URL, it finds nothing.

Fix: add an `operation_id` parameter that matches the `operationId` of each operation,
case-insensitively. Store operationIds in the index from fix 3.

### Alternative for the 3 GitHub-style tools

GitHub's official MCP server works with GHES when run locally with `GITHUB_HOST=https://<host>`
(or `--gh-host`). If the company allows that, it can provide `search_code`,
`get_repository_tree` and `get_file_contents`, and this server is needed only for
`find_openapi_spec`. Note its `get_file_contents` returns only a link for files of 1 MB or
more, and code search skips files over 384 KB, so large bundled specs still need
`find_openapi_spec`.

## Troubleshooting

### Find where it fails

Check each layer in order. Stop at the first one that fails: that's where the problem is.

| # | Layer | Check | Works if |
|---|---|---|---|
| 1 | Network and token | `set -a && source .env && set +a` then `curl -sS -H "Authorization: Bearer $GHES_TOKEN" "$GHES_BASE_URL/api/v3/repos/$GHES_DEFAULT_OWNER/$GHES_DEFAULT_REPO" \| head` | You see the repo's JSON. Otherwise, the problem is the network, the TLS certificate, the token or access, not this code. (For github.com, use `https://api.github.com/repos/...` instead.) |
| 2 | Python install | `.venv/bin/python -c "import ghes_openapi_mcp, mcp, httpx, yaml; print('ok')"` | It prints `ok`. Otherwise, redo [Setup](#setup) step 1. |
| 3 | The server itself | Run the Inspector commands in [First test](#2-call-each-tool-with-the-inspector-cli) without `2>/dev/null` | The tool result is JSON. The server's log lines, including each `HTTP Request: GET … "HTTP/1.1 200 OK"`, show the exact URL and status GHES returned. |
| 4 | Cursor | Cursor Settings → MCP shows a green dot and 4 tools | If layer 3 works but this doesn't, the cause is the path in `.cursor/mcp.json`. Check Cursor's server log: **View → Output**, then choose the MCP log in the dropdown. |
| 5 | The answer | Call `find_openapi_spec` and read `notes`, `candidate_files`, `candidates_scanned`, `specs_parsed` and `total_matches` | See "Wrong or missing results" below |

When asking Cursor's AI for help, paste the output of the first layer that fails, including
the server's log lines.

### Corporate network problems

These are likely on a company laptop and aren't caused by this code:

- **Proxy.** The server uses `HTTPS_PROXY`, `HTTP_PROXY` and `NO_PROXY` from the environment.
  An internal GHES usually must *not* go through the proxy: if requests time out or the proxy
  returns an error, add the GHES host to `NO_PROXY`, for example
  `NO_PROXY=github.your-company.internal`, in `.env` or in the shell that starts the server.
- **TLS certificate errors** (`CERTIFICATE_VERIFY_FAILED`). Python doesn't use the operating
  system's certificate store, so it doesn't trust the company's root CA. Get the CA as a PEM
  file and set `GHES_CA_BUNDLE` to its path.
  - Ask IT for the root CA, or export it from the browser's padlock dialog on the GHES page.
  - On macOS, this writes every certificate the system trusts to one file, which works as the
    bundle:
    `security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain /Library/Keychains/System.keychain > ~/company-ca.pem`
- **`pip install` fails** because PyPI is blocked. Use the company's package mirror, for
  example `pip install --index-url https://<mirror>/simple -r requirements.txt -e .`, or set
  `PIP_INDEX_URL`. Ask IT for the mirror URL. The same applies to `npx` and the npm registry.
- **Python too old.** Check `python3 --version` is 3.10 or later.

### Wrong or missing results

| Symptom | Cause | Fix |
|---|---|---|
| `best` is null and `notes` says nothing matched | The spec wasn't scanned, or its path differs from the URL | Compare `candidate_files` with `candidates_scanned`. If fewer were scanned, see [limitation 1](#1-only-the-first-200-files-are-scanned). Otherwise, check the spec's `servers` and `paths` with `get_file_contents`. |
| `specs_parsed` is much lower than `candidates_scanned` | Many YAML/JSON files aren't OpenAPI specs (or failed to parse) | Expected for metadata files. If real specs are missing, check that they have a top-level `openapi` or `swagger` key and are valid YAML/JSON. |
| The wrong version was picked | Several versions tied | Read `notes` and `matches`. Make sure the metadata files say `status: ACTIVE` for the current version. |
| A spec from another API was picked | A short or generic URL, e.g. `/v1/status`, matches many specs | Pass the full URL with host and base path, and `method`. |
| The call is very slow | Every call re-reads every spec | Use `path_prefix` and `include_content=false`; see [limitation 3](#3-every-call-re-reads-the-whole-repo). |

### Error messages

| Error | Fix |
|---|---|
| `GHES_BASE_URL is not set` / `GHES_TOKEN is not set` | Fill in `.env`, or pass them in the client's `env` block |
| 401 `rejected the token` | The token is wrong or has expired. Create a new one. |
| 403 `Forbidden` | The token can't see the repo. Check its repository access, the Contents permission and SSO authorization. |
| 403 `rate limit` | Wait until the reset time in the message. GHES limits are set by your admins. |
| 404 `Not found` | Check the owner, repo, path and ref. A token without access to a private repo also gets a 404. |
| TLS or certificate errors | Set `GHES_CA_BUNDLE` to your corporate root CA file; see [Corporate network problems](#corporate-network-problems) |
| `Could not reach GHES` (timeout or connection error) | You're off the company network or VPN, or the proxy is in the way; check `NO_PROXY` |
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
