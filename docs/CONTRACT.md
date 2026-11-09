# lore — public contract

This file is the reference for everything outside the Python package can rely on: configuration
(environment variables and YAML files), the command line, the HTTP API used by the web UI (routes,
parameters and JSON shapes) and the MCP tool names.

Conventions:

- All timestamps are ISO 8601 in UTC with seconds, e.g. `2026-11-09T16:48:24+00:00`.
- `source_id` + `path` identify a file: `path` is relative to the source root, POSIX style.
  A human-readable reference is `"<source_id>:<path>"` (called `source` in MCP and context payloads).
- Every JSON field listed below is always present unless marked *optional*; `null` is used for "unknown".

---

## 1. Configuration — environment variables

Variables can also go in a `.env` file at the project root. Prefix: `LORE_`.

| Variable | Default | Meaning |
|---|---|---|
| `LORE_HTTP_HOST` | `127.0.0.1` | Listen address. Only loopback is accepted (`127.0.0.1`, `::1`, `localhost`); anything else is refused at startup. |
| `LORE_HTTP_PORT` | `8610` | Port (1024–65535). |
| `LORE_DATA_DIR` | `<root>/.lore` | Index database (`lore.sqlite3`), PID file, log, MCP heartbeat files (`mcp/<pid>.json`), `mcp-audit-pending.jsonl`. |
| `LORE_SOURCES_FILE` | `<root>/sources.yaml` | Sources (section 2). |
| `LORE_TAXONOMY_FILE` | `<root>/taxonomy.yaml` | Categories and classification rules (section 3). |
| `LORE_PRIVACY_FILE` | `<root>/privacy.yaml` | Deny list and sensitive documents (section 4). Optional file: without it, the default deny list applies. |
| `LORE_FRONTEND_DIR` | `<lore checkout>/frontend/dist` (ships with lore, not with the indexed project) | Compiled web UI served at `/`. |
| `LORE_SYNC_SECONDS` | `300` | Automatic source check interval of `lore serve`. `0` disables it; 1–29 is refused. |
| `LORE_SEARCH_LANGUAGE` | `en` | Query-side word folding: `en` or `pt` (section 9). |
| `LORE_MCP_DOMAIN` | `default` | Domain an MCP process is locked to (regex `^[a-z][a-z0-9-]{1,20}$`). |
| `LORE_MEMORIES_DIR` | `<root>/memories` for domain `default`, `<root>/memories-<domain>` otherwise | Folder where `lore_remember` writes. Must be the `root` of a `folder` source of the same domain. |
| `LORE_MCP_MAX_CHUNKS` | `8` | Chunks per MCP response (1–50). |
| `LORE_MCP_MAX_CHARS_PER_CHUNK` | `1200` | Characters per chunk (200–10000); longer chunks are cut and marked `truncated`. |
| `LORE_MCP_MAX_CHARS_TOTAL` | `12000` | Content characters per response (1000–100000). |
| `LORE_MCP_MAX_DOCUMENTS` | `6` | Distinct documents per response (1–50). |
| `LORE_MCP_MIN_SCORE` | `0.3` | Minimum relevance relative to the best result (0–1). |
| `LORE_MCP_MAX_SEARCH_RESULTS` | `20` | Cap of `lore_search`'s `limit` (1–100). |
| `LORE_MCP_ALLOW_MEMORY` | `true` | `false` removes `lore_remember`. |
| `LORE_MCP_MAX_MEMORIES_PER_HOUR` | `30` | New memories per hour, and separately forgotten memories per hour (all sessions, counted in the audit log). |
| `LORE_MCP_MAX_CHARS_PER_MEMORY` | `4000` | Maximum memory text (200–20000). |
| `LORE_MCP_ALLOW_FORGET` | `true` | `false` removes `lore_forget`. |
| `LORE_MCP_MEMORY_REINDEX` | `true` | After writing/forgetting, ask the local server (`POST /api/lore/reindex`) to index right away. |

**Project root (`<root>`)**: the folder of `LORE_SOURCES_FILE` when that variable is set (MCP clients
often start the server elsewhere); otherwise the current directory when it contains a `sources.yaml` (e.g. after
`lore init`); otherwise, when running from a source checkout (`<root>/backend/lore`), the parent of
`backend/`; otherwise the current directory. `scripts/lore.sh` always runs from the checkout root.

---

## 2. `sources.yaml`

```yaml
max_bytes_per_file: 2000000        # optional, default 2 000 000; larger files become indexing errors

domains:                           # optional
  default:
    description: "of the Acme Shop project"     # used in the MCP instructions: "lore: local, read-only knowledge base <description>."
  billing:
    description: "of the Acme billing service"

decisions:                         # optional; these are the defaults
  patterns: ["DEC-\\d{3,4}", "ADR-\\d{3,4}"]           # regex of a whole decision code (file names and text)
  numbered_folders: [adr, adrs, decisions, decision-records]   # <folder>/0001-title.md is decision ADR-0001

sources:                           # required, at least one
  - id: docs                       # required, ^[a-z0-9][a-z0-9-]{1,40}$, unique
    domain: default                # optional, default "default", ^[a-z][a-z0-9-]{1,20}$
    name: Acme Shop docs           # optional, default = id
    type: folder                   # required: folder | git
    root: ./docs                   # required; relative paths resolve from the folder of this file; ~ expands
    include: ["**/*.md"]           # optional, default ["**/*.md"]
    exclude: []                    # optional
    respect_gitignore: true        # optional (folder sources inside a git work tree), default true

  - id: repo
    type: git
    root: .
    ref: HEAD                      # required for git: branch, tag, HEAD, origin/main… (^[A-Za-z0-9._/-]+$, not starting with -)
    include: ["*.md", "docs/**/*.md"]

  - id: memories                   # the folder where lore_remember writes (= LORE_MEMORIES_DIR)
    type: folder
    root: ./memories
    include: ["*.md"]
    respect_gitignore: false
```

Rules: only `.md` files are read; hidden files/folders (`.git`, `.obsidian`, `.trash`…) are always
skipped. Patterns: `*` and `?` do not cross `/`, `**` as a whole segment matches zero or more folders,
`[a-z]`/`[!a]` classes; case-insensitive. Git sources are read at the commit of `ref`
(`ls-tree`/`cat-file`, never the working tree, never `fetch`).

Conventions on source ids: a source whose id starts with `memor` (e.g. `memories`, `memories-claude`) is
treated as **AI memory** by the Context Builder (score weight 0.8, warning). The source whose root is
`LORE_MEMORIES_DIR` holds MCP-written memories (`verified: false`).

## 3. `taxonomy.yaml`

```yaml
root: Acme Shop                    # optional, default "Lore": label of the graph root
categories:                        # "/" separates levels; parents are created implicitly
  - Architecture
  - Decisions
  - Operations/Runbooks
rules:                             # the FIRST matching rule sets the primary category
  - category: Decisions            # must be in categories
    sources: [docs]                # optional: source ids (empty = any)
    paths: ["adr/*.md"]            # optional: path patterns (empty = any)
    confidence: explicit           # required: explicit | suggestion
    reason: stored in the adr/ folder   # required, shown in the UI
tags:                              # extra categories by word (always suggestions)
  - category: Operations/Runbooks
    words: [deploy, rollback, runbook]   # match the start of a word in the file name, title or headings (accent/case-insensitive)
```

Primary category precedence: frontmatter (`category`, `categories`, `tags`; also `categoria`,
`categorias`) → first rule → best tag (suggestion) → `Uncategorized`.

## 4. `privacy.yaml`

```yaml
deny:                              # files never read nor returned (by file name and by path); added to the defaults
  - "*.pem"
sensitive:                         # indexed and visible in the local UI, never delivered through MCP
  - sources: [docs]                # optional (empty = any source)
    paths: ["people/*.md"]         # required
    reason: personal data          # required
```

Default deny list (always applies): `.env`, `.env.*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.keystore`,
`id_rsa*`, `id_ed25519*`, `*secret*`, `*credential*`.

---

## 5. Command line

`lore` (entry point `lore.cli:main`) and `lore-mcp` (`lore.mcp_server:main`).

| Command | What it does |
|---|---|
| `lore init` | Writes `sources.yaml`, `taxonomy.yaml`, `privacy.yaml` in the current directory, only the missing ones. In a git work tree the main source is the repository at `HEAD` (`*.md`, `docs/**/*.md`); otherwise the `./docs` folder. Always adds a `memories` folder source (`./memories`). |
| `lore index [--full]` | Re-reads the sources and updates the index (only what changed; `--full` reprocesses everything). Exit 1 when a file failed. |
| `lore status [--json]` | Index summary, pending source changes and source availability (`--json` = the `GET /api/lore/status` payload). |
| `lore search <query> [--limit N]` | Full-text search from the terminal. |
| `lore serve [--no-sync]` | API + web UI on `LORE_HTTP_HOST:LORE_HTTP_PORT`; writes `<data>/lore.pid`. |
| `lore mcp` | MCP server over stdio (same as `lore-mcp`). |
| `lore reset-mcp-usage` | Resets the counters of the MCP panel (moves a starting point; the audit log is never deleted). |
| `lore-mcp` | MCP server over stdio; started by the AI client. |

`scripts/lore.sh build | start | stop | status | index [--full] | logs | test | install-login | remove-login`
— `install-login`/`remove-login` manage the launchd agent `dev.lore.agent`
(`~/Library/LaunchAgents/dev.lore.agent.plist`) and fail with a "macOS only" message elsewhere.

---

## 6. HTTP API

Base path: `/api/lore`. OpenAPI: `GET /api/lore/openapi.json`. All `/api/*` responses carry
`Cache-Control: no-store`. A `Host` header that is not local → **400**. No CORS. Errors use FastAPI's
`{"detail": "<message>"}` with 403/404/409/422.

The only non-GET route is `POST /api/lore/reindex`.

### 6.1 `GET /status`

```jsonc
{
  "version": "0.1.0",
  "read_only": true,
  "documents": { "total": 9, "indexed": 9, "with_errors": 0, "removed": 0 },
  "chunks": 9,
  "links": { "total": 3, "resolved": 2 },
  "last_run": Run | null,              // Run (6.6) plus "items": RunItem[] (≤ 500)
  "last_check": "2026-…" | null,
  "run_in_progress": { "id": 3, "origin": "api", "started_at": "…" } | null,
  "pending": PendingChange[],
  "errors": [ { "document_id": 1, "source_id": "docs", "path": "x.md", "error": "…", "kind": "read" | "processing" | null } ],
  "categories": [ { "category": "Decisions", "documents": 5, "explicit": 5, "suggestions": 0, "tagged": 5 } ],
  "sources": [ {
      "id": "docs", "name": "Acme Shop docs", "type": "folder" | "git", "domain": "default",
      "root": "/abs/path", "ref": null, "ref_commit": null,
      "available": true | false | null,   // null = never checked
      "warnings": ["…"], "checked_at": "…" | null, "documents": 8 } ],
  "domains": [ { "name": "default", "description": "…" | null, "sources": ["docs", "memories"] } ],
  "search": { "text": true, "language": "en", "semantic": { "available": false, "reason": "…" } },
  "sync_seconds": 300,
  "taxonomy": { "root": "Acme Shop", "categories": ["Architecture", "…"] }
}
```

`PendingChange` = `{ "source_id", "path", "type": "new"|"changed"|"restored"|"reprocess"|"removed"|"error", "detail": string|null }`.

### 6.2 `GET /documents`

Query: `source`, `category` (a parent includes its children), `status` (`indexed|error|removed`),
`q` (substring of path or title), `include_removed` (bool, default false), `limit` (1–5000, default 1000),
`offset` (default 0).

```jsonc
{ "total": 9, "documents": [ DocumentSummary ] }
```

`DocumentSummary`: `id, source_id, path, name, size, hash, modified_at, title, category, categories (string[]),
category_confidence ("explicit"|"suggestion"), categories_detail (Assignment[]), status, error, total_chunks,
version, indexed_at, removed_at, pending (PendingChange.type | null)`.

`Assignment`: `{ "category", "primary": bool, "confidence": "explicit"|"suggestion",
"origin": "frontmatter"|"rule"|"keyword"|"none", "reason": string, "line": int|null }`.

### 6.3 `GET /documents/{id}` → 404 when missing

All `DocumentSummary` fields except `pending` semantics are the same, plus:

```jsonc
{
  "error_kind": "read" | "processing" | null,
  "frontmatter": { … } | null,
  "metadata": { "Status": "Accepted", "Date": "2025-11-02" },   // "- **Key:** value" list at the top
  "headings": [ { "level": 2, "text": "Context", "line": 5, "anchor": "context" } ],
  "warnings": ["…"],
  "body_start": 0, "total_lines": 42, "discovered_at": "…", "processor_version": "1+1a2b3c4d",
  "source_name": "Acme Shop docs", "source_type": "folder" | "git" | null,
  "content": "full markdown text",
  "chunks": [ {
      "id": 12, "ordinal": 0, "title": "Context", "section_path": ["ADR-0004 …", "Context"], "sections": ["Context"],
      "level": 2, "part": 1, "parts": 1, "line_start": 5, "line_end": 9, "char_start": 120, "char_end": 480,
      "size": 360, "hash": "sha256", "text": "…", "position_percent": 12.5 } ],
  "links_out": [ { "line": 3, "type": "wikilink"|"embed"|"markdown", "target": "06-SECURITY", "anchor": null,
                   "text": null, "target_document_id": 4 | null, "target_title": "Security" | null } ],
  "links_in": [ { "document_id": 1, "title": "Index", "source_id": "docs", "path": "00-INDEX.md" } ],
  "versions": [ { "version": 2, "event": "new"|"changed"|"removed"|"restored", "hash": "…", "size": 812,
                  "modified_at": "…", "run_id": 3, "recorded_at": "…" } ],     // newest first
  "pending": PendingChange.type | null
}
```

(`identity` and the raw DB `content` column are never returned under other names.)

### 6.4 `GET /search`

Query: `q` (required, 1–200), `source`, `category`, `limit` (1–100, default 20), `per_document` (1–20,
default 3), `mode` (`text` | `hybrid`, default `text`). Unsearchable input → 422.

```jsonc
{
  "query": "orders postgres",
  "expression": "\"order\"* AND \"postgre\"*",
  "mode": "all" | "any",              // "any" = no chunk had every word, fell back to any word
  "total_chunks": 4, "total_documents": 3,
  "results": [ {
      "chunk_id": 7, "chunk_ordinal": 0, "document_id": 3, "document_title": "…",
      "source_id": "docs", "path": "adr/0004-….md", "category": "Decisions", "title": "…",
      "section_path": ["…"], "line_start": 1, "line_end": 20,
      "excerpt": "… \u0002orders\u0003 …",     // highlight markers, see "markers"
      "score": 3.21 } ],
  "markers": { "start": "\u0002", "end": "\u0003" },
  "requested_mode": "text" | "hybrid",
  "used_mode": "text",
  "warning": "…"                      // optional, only when mode=hybrid
}
```

### 6.5 `POST /reindex`

Header `X-Lore-Confirm: reindex` (else 403). Optional body `{"full": true}`. 409 when a run is in
progress. Response `RunSummary`:

```jsonc
{ "run_id": 2 | null, "recorded": true, "origin": "api", "full": true,
  "status": "completed" | "no_changes", "started_at": "…", "finished_at": "…", "duration_ms": 69,
  "totals": Totals, "unavailable_sources": ["…"], "errors": [ { "source_id", "path", "error" } ] }
```

`Totals`: `discovered, new, changed, reprocessed, restored, removed, errors, reclassified, unchanged, documents, chunks`.

### 6.6 `GET /runs`

Query: `limit` (1–500, default 30). Newest first. `Run`:
`{ "id", "origin": "api"|"cli"|"scheduled"|"test", "full": bool, "status": "running"|"completed"|"failed"|"interrupted",
"started_at", "finished_at": string|null, "totals": Totals (partial while running), "error": string|null }`.
In `status.last_run` it also has `"items": [ { "document_id": int|null, "source_id", "path",
"event": "new"|"changed"|"reprocessed"|"removed"|"restored"|"error"|"reclassified", "detail": string|null } ]`.

### 6.7 `GET /audit`

Query: `limit` (1–1000, default 100). Newest first:
`[ { "id", "at", "action", "origin", "ip": string|null, "agent": string|null, "details": { … } } ]`.

Actions: `reindex_requested`, `reindex_completed`, `reindex_failed` (details: `run_id, full, totals,
unavailable_sources, errors` / `error`), `mcp_query`, `mcp_memory`, `mcp_memory_forgotten` (details: see
[MCP.md](MCP.md#audit)), `mcp_usage_reset` (details: `from_entry`). Origins: `api`, `cli`, `scheduled`,
`test`, `mcp`.

### 6.8 `GET /graph`

```jsonc
{
  "root": "root",
  "nodes": [ GraphNode ],             // sorted by type order, then label
  "edges": [ { "id": "link|doc:1|doc:4", "type": "subcategory"|"category"|"link"|"mentions",
               "from": "doc:1", "to": "doc:4",
               "confidence": "explicit"|"suggestion",   // optional: category edges only
               "primary": true,                          // optional: category edges only
               "total_evidence": 1 } ],
  "counts": { "nodes": { "document": 7, … }, "edges": { "link": 3, … } },
  "types": { "nodes": ["root","category","document","decision","commit","migration","hash","file"],
             "edges": ["subcategory","category","link","mentions"] }
}
```

`GraphNode`: `{ "id", "type", "label", "degree", "detail"? }` plus, by type:
- `category` (`id` = `cat:<path>`): `category`;
- `document` / `decision` with a file (`id` = `doc:<document_id>`): `document_id, source_id, path, name, category,
  confidence, status, code` (`code` = decision code or `null`);
- entities (`id` = `ent:<type>:<key>`; types `decision` without a file, `commit`, `migration`, `hash`, `file`): `key`;
- root (`id` = `root`).

### 6.9 `GET /graph/node?id=` → 404 when missing

```jsonc
{
  "node": GraphNode,
  "relations": [ { "edge": Edge (with "evidence": Evidence[] instead of "total_evidence"),
                   "direction": "in" | "out", "other": GraphNode } ],
  "own_chunks": [ RelatedChunk ],       // chunks of the node's own document
  "related_chunks": [ RelatedChunk ],   // chunks that cite this node
  "open": { "document_id": 3, "line": 12 | null } | null
}
```

`Evidence`: `{ "rule", "document_id"?, "source_id"?, "path"?, "line"?, "chunk_ordinal"?, "excerpt"?,
"origin"? (category edges: frontmatter|rule|keyword|none), "file"? ("taxonomy.yaml") }`.
`RelatedChunk`: `{ "document_id", "document_title", "source_id", "path", "ordinal", "title", "section_path",
"line_start", "line_end", "text" (≤ 900 chars), "reason" }`.

### 6.10 `GET /graph/edge?id=` → 404 when missing

`{ "edge": Edge (with "evidence": Evidence[]), "from": GraphNode, "to": GraphNode }`.

### 6.11 `GET /decisions`

Query: `q` (optional), `state` (`active|proposed|superseded|deprecated|undeclared`). Without `q`: all, by number.

```jsonc
{
  "query": "orders" | null,
  "decisions": [ {
      "code": "ADR-0001", "number": 1, "title": "Use MySQL 8 for the orders database",
      "date": "2025-03-04" | null, "declared_status": "Superseded by ADR-0004" | null,
      "state": "superseded",
      "superseded_by": [ { "code": "ADR-0004", "document_id": 3, "path": "adr/0001-use-mysql.md",
                           "line": 12 | null, "excerpt": "…" } ],   // code = the superseding decision; document/path/line = where it is written
      "cited_by_later_decisions": [ { "code", "document_id", "path", "line", "excerpt" } ],
      "document_id": 2, "source_id": "docs", "path": "adr/0001-use-mysql.md", "indexed_at": "…",
      "relevance": 0.83,                // optional, only with q
      "reason": "…"                     // optional, only with q
  } ],
  "total": 5,
  "criteria": "…"
}
```

### 6.12 `GET /context?task=`

`task` 1–500 chars; no searchable word → 422. Same package as the MCP tool `lore_get_context`:

```jsonc
{
  "task": "…", "search_terms": ["move", "orders", "postgresql"], "search_mode": "any",
  "chunks": [ { "content", "truncated": bool, "source": "docs:adr/0004-….md", "heading": "A › B",
                "document_id", "chunk_id", "chunk_index", "lines": [1, 20], "score": 1.0 } ],
  "sources": [ { "document_id", "source", "title", "category", "category_confidence", "is_memory": bool } ],
  "decisions": [ { "code", "title", "state", "date", "document_id", "source", "why",
                   "superseded_by": ["ADR-0004"], "cited_by_later_decisions": ["…"],
                   "declared_status": "…" /* optional, only when state != active */ } ],
  "memories": [ { "source", "chunk_id", "heading", "verified": false | null } ],
  "entities": [ { "type": "commit"|"migration"|"hash"|"file", "key", "source", "line" } ],
  "returned": { "chunks", "documents", "characters", "approx_tokens" },
  "available": { "matching_chunks", "matching_documents" },
  "omitted": { "by_privacy", "below_min_score", "low_term_coverage", "document_limit", "chunk_limit",
               "size_limit", "other_chunks_same_documents" },
  "more_available": bool, "note": string | null,
  "savings": { "full_documents_characters", "approx_tokens_avoided", "basis" },
  "warnings": ["…"]
}
```

### 6.13 `GET /mcp` (MCP panel)

```jsonc
{
  "online": false,
  "processes": [ { "pid", "started_at", "last_used_at", "client": { "name", "version" } | null, "domain" } ],
  "command": "/…/.venv/bin/lore-mcp",
  "transport": "stdio (local; the AI client starts the process)",
  "read_only": false,
  "memories": { "allowed": true, "forget_allowed": true, "folder": "/…/memories", "source": "memories" | null,
                "max_per_hour": 30, "created": 1, "forgotten": 0,
                "recent": [ { "at", "client", "title", "reason": string|null, "file",
                              "status": "created"|"already_existed"|"forgotten", "document_id": int|null, "removed": bool } ] },
  "tools": [ { "name": "lore_status", "description": "…" } ],
  "limits": { "max_chunks", "max_chars_per_chunk", "max_chars_total", "max_documents", "min_score",
              "max_entities", "max_decisions", "memory_weight", "min_coverage" },
  "usage": { "queries", "errors", "no_results", "chunks", "characters", "approx_tokens", "approx_tokens_avoided",
             "secrets_redacted", "by_tool": { "lore_search": 3 }, "token_estimate": "≈ 4 characters per token" },
  "clients": [ { "client": "claude-code 2.1", "queries": 4, "last": "…" } ],
  "recent_queries": [ { "id", "at", "tool", "domain", "client", "query": string|null, "documents": int,
                        "chunks": int, "characters", "approx_tokens", "duration_ms",
                        "result": "ok"|"no_results"|"error", "error": string|null } ]
}
```

### 6.14 Web UI

`GET /` and any path without an extension that is not under `/api/` serve `index.html`
(`Cache-Control: no-cache`); assets are served as files. Without a build, `/` returns a short HTML hint.

---

## 7. Enumerations

| Field | Values |
|---|---|
| document `status` | `indexed`, `error`, `removed` |
| `error_kind` / status `errors[].kind` | `read`, `processing` |
| pending `type` | `new`, `changed`, `restored`, `reprocess`, `removed`, `error` |
| run item `event` | `new`, `changed`, `reprocessed`, `removed`, `restored`, `error`, `reclassified` |
| version `event` | `new`, `changed`, `removed`, `restored` |
| run `origin` | `api`, `cli`, `scheduled`, `test` |
| run `status` | `running`, `completed`, `failed`, `interrupted` (+ `no_changes` in a RunSummary of an unrecorded scheduled check) |
| source `type` | `folder`, `git` |
| `confidence` | `explicit`, `suggestion` |
| assignment `origin` | `frontmatter`, `rule`, `keyword`, `none` |
| search `mode` | `all`, `any`; query param `mode`: `text`, `hybrid` |
| node `type` | `root`, `category`, `document`, `decision`, `commit`, `migration`, `hash`, `file` |
| edge `type` | `subcategory`, `category`, `link`, `mentions` |
| relation `direction` | `in`, `out` |
| link `type` | `wikilink`, `embed`, `markdown` |
| decision `state` | `active`, `proposed`, `superseded`, `deprecated`, `undeclared` |
| MCP `result` | `ok`, `no_results`, `error` |
| memory `status` (panel/audit) | `created`, `already_existed`, `forgotten` |
| default category | `Uncategorized` |

## 8. MCP

Server name `lore`; tools `lore_status`, `lore_search`, `lore_get_context`, `lore_get_document`,
`lore_get_decisions`, `lore_get_entity` (read-only) and `lore_remember`, `lore_forget`. Inputs, outputs and
guarantees: [MCP.md](MCP.md). Graph model: [GRAPH.md](GRAPH.md).

## 9. Search folding (`LORE_SEARCH_LANGUAGE`)

Accent and case folding always apply. Each word of 3+ letters is searched as a prefix of its stem;
quoted text is an exact phrase; codes, hashes and identifiers (`DEC-031`, `audit_log`, `06e4b4d`) are
matched as typed (as a prefix). A lowercase stop word is dropped; an uppercase acronym (`IT`, `OR`) is kept.

- `en`: at most one rule, at least 3–4 letters kept: `-ations/-ation` → drop (expiration → expir) ·
  `-ies` → `-y` (policies → policy) · `-sses` → `-ss` · `-ches/-shes/-xes/-zes/-sses` → drop `es`
  (boxes → box) · `-s` → drop except after `s`/`u`/`i` (status, analysis stay) · `-ing`/`-ed` → drop and
  undouble the final consonant (running → run, stopped → stop). The searched prefix also drops a final `y`
  of 5+ letter stems, so `policy` finds `policies`.
- `pt`: the Portuguese rules (plural, `-ção`, `-ões`, `-mento`, verb endings `-ar/-er/-ir`).

## 10. Decisions

Recognized by code in the file name (`decisions/DEC-001-x.md`, `ADR-0003-x.md`; patterns configurable) or by
the numbered layout (`adr/0001-title.md`, `docs/adr/0001-*.md`, `docs/decisions/0001-*.md` → `ADR-0001`).
Declared status, in order: `- **Status:** …` list, frontmatter `status`, a `Status: …` line near the top,
the first line of a `## Status` section. Date: `Date`/`Data` the same way, or frontmatter `date`.

| State | Status words (English · Portuguese) |
|---|---|
| `superseded` | superseded, replaced · substituída, superada — or another decision writes "supersedes / replaces / overrides / revokes X" (pt: "substitui / revoga / supera X"), or the record itself writes "superseded by / replaced by X" (pt: "substituída pela X") |
| `deprecated` | deprecated, rejected, withdrawn, retired, cancelled, abandoned, obsolete, revoked · retirada, revogada, cancelada, abandonada, obsoleta, descontinuada, rejeitada, sem efeito |
| `active` | accepted, approved, adopted, active, in effect · aceita, publicada, vigente, em vigor, aprovada |
| `proposed` | proposed, draft, under review, in review · proposta, rascunho, em análise |
| `undeclared` | no status, or none of the above |
