# lore — MCP server

The MCP server is a thin access layer over lore: it has no index, search or rules of its own. Six tools
only read. `lore_remember` **creates** a new memory (a new `.md` in lore's memories folder) and
`lore_forget` **forgets** a memory the MCP server itself created (moves it to a recoverable trash). No tool
edits a document, decision or memory, and none touches the other sources.

```
AI client (Claude Code, Codex, Cursor…)
   │  stdio — the client starts the process; there is no network port
   ▼
lore-mcp  (backend/lore/mcp_server.py)
   │  domain wall · privacy · limits · secret redaction · audit
   ▼
lore: Context Builder · search · decisions · graph   (backend/lore/*.py)
   ▼
Local index (SQLite + FTS5 in .lore/lore.sqlite3)  ←  .md files of the sources (read only)
                                                   ←  memories/*.md (lore_remember only creates)
```

There is no semantic search yet: search is full-text (SQLite FTS5). When embeddings arrive, the MCP
interface stays the same.

## Command

```bash
<checkout>/backend/.venv/bin/lore-mcp      # or: lore mcp   (or the lore-mcp script of a pip install)
```

It speaks MCP over stdio. It needs an index: run `lore index` (or keep `lore serve` running, which syncs
the sources). The MCP server **never** creates or rebuilds the index.

- **Start:** you don't — the AI client starts the process when needed and ends it when it exits. To try it
  in a terminal, run the command (it waits for MCP messages on stdin; `Ctrl+C` quits).
- **Status:** the web UI (`http://127.0.0.1:8610`, MCP page) shows "online" while an MCP process is alive,
  with its client and last use; the `lore_status` tool answers the same through MCP.

## Connecting clients

Claude Code:

```bash
claude mcp add --scope user lore -- /path/to/lore/backend/.venv/bin/lore-mcp
# per project, locked to a domain (the name goes before -e):
claude mcp add --scope local lore -e LORE_MCP_DOMAIN=billing -- /path/to/lore/backend/.venv/bin/lore-mcp
```

Codex CLI:

```bash
codex mcp add lore -- /path/to/lore/backend/.venv/bin/lore-mcp
```

Other clients that start **stdio** servers usually take this JSON:

```json
{
  "mcpServers": {
    "lore": {
      "command": "/path/to/lore/backend/.venv/bin/lore-mcp",
      "args": [],
      "env": { "LORE_SOURCES_FILE": "/path/to/project/sources.yaml" }
    }
  }
}
```

`LORE_*` variables can go in `env` or in the `.env` at lore's project root.

## Domains: the wall between projects

One lore installation, one index, but every MCP process is **locked to a domain** (`LORE_MCP_DOMAIN`,
default `default`) and only sees the sources of `sources.yaml` with that `domain`. For it, the rest does
not exist: search, context, documents, decisions, graph and `lore_status` count and return only its own
domain; a `document_id` or `source:path` of another domain answers "Document not found", exactly like a
missing id. The filter is applied in SQL (other domains never take a result slot) and the graph is built
only from the domain's documents, so a `DEC-001` of one project never mixes with the `DEC-001` of another.

- Domain names are arbitrary (`^[a-z][a-z0-9-]{1,20}$`). `domains.<name>.description` in `sources.yaml`
  goes into the server instructions ("lore: local, read-only knowledge base *of the Acme billing service*").
- Without `LORE_MEMORIES_DIR`, the memories folder follows the domain (`memories/` for `default`,
  `memories-<domain>/` otherwise). If the configured folder belongs to a source of another domain,
  `lore_remember` refuses and writes nothing: memories never cross the wall.
- A domain without any source: the process does not start (configuration error).
- `lore_status` returns `domain`.
- The local web UI shows every domain: the wall is between AI agents, not a barrier against the owner of
  the machine (who can change the variable).

## Tools

The six read tools have `readOnlyHint=true`. `lore_remember` has `readOnlyHint=false` (it creates a file)
and `lore_forget` has `readOnlyHint=false`, `destructiveHint=true` (it takes something out of lore, even if
recoverable), so clients usually ask before each use.

| Tool | Input | Returns |
|---|---|---|
| `lore_status` | — | `lore_available`, `domain`, database, `documents`, `documents_hidden_by_policy`, `chunks`, `last_run`, `last_check`, `index_state` (`up_to_date`/`stale`/`empty`), `pending_source_changes`, `search`, `mcp` (version, read_only, memory permissions, limits). No local paths. |
| `lore_search` | `query`, `limit` (≤ `LORE_MCP_MAX_SEARCH_RESULTS`) | Short chunks with `content`, `source`, `heading`, `document_id`, `chunk_id`, `chunk_index`, `lines`, `score` (0–1, relative to the best). Says whether there is more (`more_available`, `note`) and `hidden_by_policy`. |
| `lore_get_context` | `task` | **Main tool.** Small Context Builder package: `chunks` (with origin), `sources`, `decisions` (with state), `memories`, `entities`, `omitted` (why), `more_available`, `savings`, `warnings`. Same shape as `GET /api/lore/context`. |
| `lore_get_document` | `document_id` (number, `doc:N` or `source:path`), `chunks` / `heading` / `full` | Without options: outline (and the text only when the document is small). `chunks=[…]` or `heading="…"`: only those parts. `full=true`: whole, up to the size limit (`omitted_chunks_over_limit`). |
| `lore_get_decisions` | `query`, `state`, `limit` | Decisions with `state` (`active`, `proposed`, `superseded`, `deprecated`, `undeclared`), `declared_status`, `superseded_by` (with evidence) and `cited_by_later_decisions`. |
| `lore_get_entity` | `entity` | A graph node (decision code, category, document, commit, migration, file), its relations with evidence, related documents and decisions. Without a matching node: `found=false` + suggestions — never invents. |
| `lore_remember` | `title`, `content`, `kind`, `subject`, `evidence[]`, `related_decisions[]` | Creates a new, unverified memory (below). Returns `status` (`created`/`already_existed`), `memory_id`, `source`, `verification: "unverified"`, `indexing` (`indexed` with `document_id`, or `pending`). |
| `lore_forget` | `memory`, `reason` | Forgets a **memory created through MCP**. `memory` = `memory_id`, `source` or `document_id`; `reason` is required (goes to the audit log). Returns `status: "forgotten"`, `recoverable`, `trash`, `indexing`. |

There is no update or delete tool: a wrong memory is corrected by forgetting it and recording a new one.

### Decision states — nothing is presumed

- `active`: the status says accepted/approved (pt: aceita/publicada); `proposed`: proposed/draft/under review;
  `deprecated`: deprecated/rejected/withdrawn/obsolete (pt: retirada/revogada/cancelada/obsoleta);
- `superseded`: the status says "superseded" (pt: "substituída"), **or** another decision writes
  "supersedes / replaces / overrides ADR-0003" (pt: "substitui / revoga / supera"), **or** the record
  itself writes "superseded by ADR-0005";
- `undeclared`: no status, or a status that says none of this (e.g. "Needs verification").
- A later decision that only **cites** an earlier one shows up in `cited_by_later_decisions` for review.

Decision records are recognized by code (`DEC-001`, `ADR-0001`, or the `decisions.patterns` of
`sources.yaml`) or by the numbered layout `adr/0001-title.md`. See [CONTRACT.md](CONTRACT.md#10-decisions).

## Memories written through MCP (`lore_remember`)

For an agent to record what it learned and is not in the sources yet: a fact confirmed with you, a
preference, a procedure, a known problem, a reference.

- **Where:** one new file per memory in `LORE_MEMORIES_DIR`, with a server-generated name:
  `YYYY-MM-DD-HHMMSS-title-summary-<sha256[:12]>.md` (UTC). The client never chooses a path; `../` or
  `/etc/…` in the title just become words of the summary. The folder must be the root of a `folder`
  source of the same domain in `sources.yaml` (otherwise nothing is written: it would never be indexed).
- **Never overwrites:** the file is written to a hidden temporary file and published with `os.link`, which
  fails if the name exists. Same content (sha256) → returns the existing memory (`already_existed`).
- **File content:** frontmatter with `id`, `title`, `kind`, `subject`, `status: unverified`,
  `origin: mcp lore_remember`, `author` (client name and version), `created_at`, `decisions`, `sha256`;
  then `# title`, an "Unverified" notice, the text, `## Evidence` and `## Related decisions`. The client
  does not choose a category: the taxonomy does (e.g. a rule on the memories source).
- **Refused (nothing written):** secret-looking text in any field (the redaction rules: private keys,
  tokens, `SOME_PASSWORD=value`, password in URL…); `kind` outside
  `fact|decision|preference|procedure|problem|reference`; title outside 3–120 characters; text outside
  20–`LORE_MCP_MAX_CHARS_PER_MEMORY`; more than 10 evidence items; a related decision that is not a
  decision code; more than `LORE_MCP_MAX_MEMORIES_PER_HOUR` new memories in the last hour.
- **Indexing:** the MCP server still never writes to the index. After writing it asks the local server
  `POST http://127.0.0.1:<port>/api/lore/reindex` (with `X-Lore-Confirm`, no proxy, 10 s). With the server
  stopped the answer says `pending` and the memory goes in on the next sync or `lore index`.
- **In the context:** memories get a 0.8 score weight; the MCP ones come with `verified: false` and a
  warning.
- **Forget (`lore_forget`):** moves the file to `<memories>/.trash/YYYYMMDD-HHMMSS-<name>.md`. Nothing is
  deleted or changed; the folder is hidden, so the scanner skips it and the memory leaves searches and
  context. **Restore:** move the file back with its original name and reindex. Only names generated by
  `lore_remember` and regular files are accepted (symbolic links are refused); a `document_id` of another
  source is refused with an explanation. Requires a `reason` (5+ characters, no secret). Same hourly cap,
  counted separately. Wrong information in another source is not changed: record the correction with
  `lore_remember`, citing the wrong source in `evidence`, and tell the user.
- **Turn off:** `LORE_MCP_ALLOW_MEMORY=false` (removes `lore_remember`), `LORE_MCP_ALLOW_FORGET=false`
  (removes `lore_forget`). With both off the server is read-only.

## Token control

| Variable | Default | Effect |
|---|---|---|
| `LORE_MCP_MAX_CHUNKS` | 8 | Chunks per response |
| `LORE_MCP_MAX_CHARS_PER_CHUNK` | 1200 | Characters per chunk (the rest becomes `truncated=true`) |
| `LORE_MCP_MAX_CHARS_TOTAL` | 12000 | Content characters per response (≈ 3k tokens) |
| `LORE_MCP_MAX_DOCUMENTS` | 6 | Distinct documents per response |
| `LORE_MCP_MIN_SCORE` | 0.3 | Minimum relevance, relative to the best result |
| `LORE_MCP_MAX_SEARCH_RESULTS` | 20 | Cap of `lore_search`'s `limit` |
| `LORE_MCP_MAX_MEMORIES_PER_HOUR` | 30 | New memories and, separately, forgotten memories per hour |
| `LORE_MCP_MAX_CHARS_PER_MEMORY` | 4000 | Maximum memory text |
| `LORE_MCP_MEMORY_REINDEX` | true | Ask the local server to index right after writing |

The Context Builder also requires every chunk to contain **more than half** of the task words and gives
AI memories a 0.8 weight (project documentation comes first). Everything over a limit is counted in
`omitted` and the response says there is more — it is not sent. `savings` estimates the tokens avoided
compared with sending the whole documents.

## Security

- **Local:** stdio, no port. lore's web server only listens on loopback.
- **Read-only database:** the index is opened with `mode=rw` **without** creating a file and with an SQLite
  authorizer that denies `INSERT/UPDATE/DELETE/DROP/ALTER/ATTACH/CREATE` and PRAGMAs with a value; the only
  accepted write is the `INSERT` of the audit record — also for `lore_remember`.
- **Files:** the `.md` files of the sources are never opened by the MCP server. The only disk writes are
  creating a new file in the memories folder (mode 600, folder 700) and moving a file the server created
  into `.trash/` — never editing, overwriting or deleting for good.
- **No disk paths:** `lore_get_document` only accepts an index id or the exact indexed `source:path` —
  `../../etc/passwd` simply does not exist there.
- **Privacy (`privacy.yaml`):** `deny` (never read nor delivered) and `sensitive` (indexed and visible in the
  local UI, never delivered through MCP). The scanner also honours the `.gitignore` of folder sources inside
  git work trees.
- **Secret redaction** on every outgoing text (and on the inputs stored in the audit log): private keys,
  GitHub/Anthropic/OpenAI/Slack/AWS tokens, JWT, `SOMETHING_PASSWORD|TOKEN|SECRET…=value`, password in URL.
- **Internal errors** become a generic message for the client; details go to the local stderr.

## Audit

Every call writes an `audit` entry (action `mcp_query`, or `mcp_memory` for `lore_remember`,
`mcp_memory_forgotten` for `lore_forget`; origin `mcp`; agent = client name and version) with `details`:

```jsonc
{
  "tool": "lore_search", "domain": "default", "client": { "name": "claude-code", "version": "2.1" },
  "input": { "query": "… (redacted, ≤ 300 chars)" },
  "documents": [3, 4], "chunks": [7, 9],          // ids returned (≤ 50)
  "response_characters": 1234, "approx_tokens": 309, "approx_tokens_avoided": 2100,
  "secrets_redacted": 0, "result": "ok" | "no_results" | "error", "error": null, "duration_ms": 12,
  "memory": {                                      // only for lore_remember / lore_forget
    "source": "memories", "file": "2026-…md", "status": "created" | "already_existed" | "forgotten",
    "sha256": "first 12", "indexing": "indexed" | "pending" | "already_indexed",
    "title": "…", "reason": "…", "trash": "…"       // forget only
  }
}
```

If the index is unavailable, the entry goes to `<data>/mcp-audit-pending.jsonl`. A reindex requested after
writing shows up as `reindex_requested` with agent `lore-mcp/<version> (lore_remember)`. See the MCP page and
the Audit page of the web UI.
