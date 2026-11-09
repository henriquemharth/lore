"""lore MCP server — a thin, local layer over the existing lore service.

Transport: stdio (the AI client starts this process; there is no network port).
Read tools: lore_status, lore_search, lore_get_context, lore_get_document, lore_get_decisions,
lore_get_entity. The only tool that writes is lore_remember, which CREATES a new memory (a new .md in lore's
memories folder, see lore/memories.py) and can be disabled with LORE_MCP_ALLOW_MEMORY=false; lore_forget moves a
memory created by lore_remember to a recoverable trash. No tool changes, moves or deletes a document or decision.

Guarantees:
- the index database is opened without ever being created, and an SQLite authorizer refuses any write other
  than inserting the audit record of the call itself (a new memory enters the index through lore's own server,
  POST /api/lore/reindex, or on the next sync);
- domain wall: the process only sees the sources of its domain (LORE_MCP_DOMAIN, the "domain" field of
  sources.yaml); the rest does not exist for it;
- sensitive or denied documents (privacy.yaml) never leave; every text goes through secret redaction; sizes
  obey the LORE_MCP_* limits (few, very relevant chunks);
- every response states the origin of each item (source, path, heading, document, chunk);
- every call goes into the audit log (tool, client, redacted input, returned ids, size, duration, error).
"""

import json
import logging
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from lore import __version__, audit, decisions, memories
from lore import graph as graph_module
from lore.categories import normalize
from lore.config import Settings, settings
from lore.context import Limits, build, estimate_tokens
from lore.db import SCHEMA_VERSION, get_meta, json_load, now
from lore.privacy import redact
from lore.search import MARK_END, MARK_START, InvalidQuery, relative, search
from lore.service import Lore

log = logging.getLogger("lore.mcp")

SERVER_NAME = "lore"
INPUT_LIMIT = 500
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
# Creates a new file (not a read), never destroys; repeating the same content creates nothing new.
CREATE_ONLY = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
# Takes the memory out of lore (into the trash, recoverable); does not touch any other source.
FORGETS = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)

INSTRUCTIONS = (
    "lore: local, read-only knowledge base {description}. "
    "This process only sees the domain '{domain}': documents and memories of other domains do not exist for it. "
    "Start with lore_get_context and the task: it returns a few relevant chunks, decisions, memories, "
    "entities and sources, each with its origin (source, heading, document_id, chunk_id, score). "
    "Use lore_search to locate chunks, lore_get_document with chunks=[...] to read parts of a document, "
    "lore_get_decisions for decisions and their state (active/proposed/superseded/deprecated/undeclared) and "
    "lore_get_entity for decisions, categories, documents, commits, migrations and files of the graph. "
    "Cite the origin when you use a piece of information. AI memories may be outdated."
)
MEMORY_INSTRUCTIONS = (
    " When available, lore_remember records a NEW memory (it changes or deletes nothing): use it only for something "
    "durable and useful that is not in the sources yet — a fact confirmed with the user, a preference, a procedure, "
    "a known problem — with evidence. Never record secrets or personal data. The memory starts unverified."
)
FORGET_INSTRUCTIONS = (
    " lore_forget takes out of lore a memory created by lore_remember that turned out wrong or outdated (it requires "
    "a reason; the file goes to a trash folder and can be restored). It does NOT delete documents from the sources "
    "(notes, repositories) nor memories kept by other tools: if wrong information is in those sources, record the "
    "correction with lore_remember (citing the wrong source in evidence) and tell the user."
)

TOOLS: list[dict[str, str]] = [
    {"name": "lore_status", "description": "Operational status: index, database, documents, chunks and last run."},
    {"name": "lore_search", "description": "Full-text search over the indexed chunks; returns short chunks with their origin."},
    {"name": "lore_get_context", "description": "Small context package for a task (Context Builder)."},
    {"name": "lore_get_document", "description": "One document: outline by default, or the requested chunks."},
    {"name": "lore_get_decisions", "description": "Decisions related to a subject, with state and evidence."},
    {"name": "lore_get_entity", "description": "One graph entity, its relations and the sources."},
]
MEMORY_TOOL = {
    "name": "lore_remember",
    "description": "Creates a new memory (a new .md in the memories folder, unverified). Never changes or deletes.",
}
FORGET_TOOL = {
    "name": "lore_forget",
    "description": "Forgets a memory created through MCP (moved to the trash, recoverable). Does not touch other sources.",
}


def available_tools(cfg: Settings) -> list[dict[str, str]]:
    return [*TOOLS, *([MEMORY_TOOL] if cfg.mcp_allow_memory else []), *([FORGET_TOOL] if cfg.mcp_allow_forget else [])]


class IndexUnavailable(RuntimeError):
    pass


_READ_PRAGMAS = {"data_version", "user_version", "schema_version", "table_info", "table_xinfo", "index_list", "index_info"}


def _authorizer(action: int, arg1: str | None, arg2: str | None, *_: Any) -> int:
    """Read only; the only write allowed is INSERT into the audit log."""
    if action == sqlite3.SQLITE_PRAGMA:
        # FTS5 runs "PRAGMA data_version" internally; a PRAGMA with a value (which changes something) is denied.
        return sqlite3.SQLITE_OK if arg2 is None and (arg1 or "").lower() in _READ_PRAGMAS else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_INSERT:
        return sqlite3.SQLITE_OK if arg1 == "audit" else sqlite3.SQLITE_DENY
    if action in (
        sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TRIGGER, sqlite3.SQLITE_DROP_VIEW, sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_CREATE_VIEW,
        sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH,
        sqlite3.SQLITE_CREATE_TEMP_TABLE, sqlite3.SQLITE_DROP_TEMP_TABLE, sqlite3.SQLITE_CREATE_VTABLE,
        sqlite3.SQLITE_DROP_VTABLE,
    ):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def open_read_only(path: Path) -> sqlite3.Connection:
    """Opens the existing index (never creates it) and blocks writes outside the audit log."""
    if not Path(path).is_file():
        raise IndexUnavailable("The lore index was not found. Run 'lore index' (or scripts/lore.sh index) first.")
    conn = None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=5, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.execute("SELECT COUNT(*) FROM document").fetchone()
    except sqlite3.DatabaseError as error:
        if conn is not None:
            conn.close()
        raise IndexUnavailable(f"The lore index could not be read ({error.__class__.__name__}).") from error
    if version != SCHEMA_VERSION:
        conn.close()
        raise IndexUnavailable(
            f"The index is at schema version {version} and the MCP server expects {SCHEMA_VERSION}; "
            "reindex with 'lore index'."
        )
    conn.isolation_level = None
    conn.set_authorizer(_authorizer)
    return conn


class McpLayer:
    """Tool logic, independent of the transport (used by the server and by the tests)."""

    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self.lore = Lore(cfg)
        self.domain = cfg.mcp_domain
        self.visible_sources = tuple(s.id for s in self.lore.sources.in_domain(self.domain))
        if not self.visible_sources:
            raise ValueError(
                f"No source in sources.yaml belongs to the domain '{self.domain}' (LORE_MCP_DOMAIN); "
                f"configured domains: {', '.join(self.lore.sources.domains())}."
            )
        self.limits = Limits(
            max_chunks=cfg.mcp_max_chunks,
            max_chars_per_chunk=cfg.mcp_max_chars_per_chunk,
            max_chars_total=cfg.mcp_max_chars_total,
            max_documents=cfg.mcp_max_documents,
            min_score=cfg.mcp_min_score,
        )
        self.process_file = cfg.data_dir / "mcp" / f"{os.getpid()}.json"

    # ------------------------------------------------------------------ infrastructure

    def blocked(self, source_id: str, path: str) -> bool:
        """Outside the domain (including a source removed from sources.yaml) or sensitive: never leaves through MCP."""
        return source_id not in self.visible_sources or self.lore.privacy.blocked(source_id, path)

    def instructions(self) -> str:
        description = self.lore.sources.describe(self.domain) or "of this project"
        return INSTRUCTIONS.format(description=description, domain=self.domain)

    def run(
        self,
        tool: str,
        given: dict[str, Any],
        client: dict[str, str | None],
        function: Callable[[sqlite3.Connection], tuple[dict[str, Any], dict[str, Any]]],
        action: str = "mcp_query",
    ) -> str:
        """Runs the tool, redacts secrets, serializes compactly and audits (success or error)."""
        start = time.monotonic()
        conn = None
        trace: dict[str, Any] = {}
        error = None
        response_text = ""
        try:
            conn = open_read_only(self.cfg.database)
            response, trace = function(conn)
            response, redacted = redact_values(response)
            response_text = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
            trace["secrets_redacted"] = redacted
            return response_text
        except (ToolError, IndexUnavailable, InvalidQuery, ValueError) as failure:
            error = str(failure)
            raise ToolError(error) from failure
        except Exception as failure:  # never leaks internal details to the client
            log.exception("Tool %s failed", tool)
            error = f"internal error ({failure.__class__.__name__})"
            raise ToolError("Internal failure in lore; see the local log.") from failure
        finally:
            self._audit(conn, action, tool, given, client, trace, response_text, error, start)
            if conn is not None:
                conn.close()

    def _audit(
        self,
        conn: sqlite3.Connection | None,
        action: str,
        tool: str,
        given: dict[str, Any],
        client: dict[str, str | None],
        trace: dict[str, Any],
        response: str,
        error: str | None,
        start: float,
    ) -> None:
        safe_input = {key: _for_audit(value) for key, value in given.items()}
        details = {
            "tool": tool,
            "domain": self.domain,
            "client": client,
            "input": safe_input,
            "documents": trace.get("documents", [])[:50],
            "chunks": trace.get("chunks", [])[:50],
            "response_characters": len(response),
            "approx_tokens": estimate_tokens(len(response)),
            "approx_tokens_avoided": trace.get("tokens_avoided", 0),
            "secrets_redacted": trace.get("secrets_redacted", 0),
            "result": "error" if error else ("no_results" if trace.get("empty") else "ok"),
            "error": error,
            "duration_ms": int((time.monotonic() - start) * 1000),
        }
        if trace.get("memory"):
            details["memory"] = trace["memory"]
        agent = " ".join(v for v in (client.get("name"), client.get("version")) if v) or None
        try:
            if conn is None:
                raise IndexUnavailable("no connection")
            audit.record(conn, action, "mcp", details, agent=agent)
        except Exception:
            # Index unavailable: the call is not lost, it goes to a local pending file.
            try:
                folder = self.cfg.data_dir
                if folder.is_dir():
                    with open(folder / "mcp-audit-pending.jsonl", "a", encoding="utf-8") as file:
                        file.write(json.dumps({"at": now(), **details}, ensure_ascii=False) + "\n")
            except OSError:
                log.warning("Could not record the audit entry of the call %s", tool)
        self._heartbeat(client)

    def _heartbeat(self, client: dict[str, str | None] | None = None) -> None:
        """<data>/mcp/<pid>.json tells the UI that this MCP process is up and who it talks to."""
        try:
            self.process_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            current = json_load(self.process_file.read_text(encoding="utf-8")) if self.process_file.exists() else {}
            current = current or {"pid": os.getpid(), "started_at": now(), "domain": self.domain}
            current["last_used_at"] = now()
            if client and client.get("name"):
                current["client"] = client
            self.process_file.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def close(self) -> None:
        try:
            self.process_file.unlink(missing_ok=True)
        except OSError:
            pass

    # ------------------------------------------------------------------ tools

    def status(self, conn: sqlite3.Connection) -> tuple[dict[str, Any], dict[str, Any]]:
        marks = ", ".join("?" * len(self.visible_sources))
        documents = conn.execute(
            f"SELECT source_id, path FROM document WHERE status != 'removed' AND source_id IN ({marks})",
            self.visible_sources,
        ).fetchall()
        hidden = sum(1 for d in documents if self.blocked(d["source_id"], d["path"]))
        chunks = conn.execute(
            f"SELECT COUNT(*) FROM chunk c JOIN document d ON d.id = c.document_id WHERE d.source_id IN ({marks})",
            self.visible_sources,
        ).fetchone()[0]
        last_id = get_meta(conn, "last_run")
        last = (
            conn.execute("SELECT status, origin, finished_at FROM run WHERE id = ?", (int(last_id),)).fetchone()
            if last_id
            else None
        )
        pending = sum(1 for p in self.lore.indexer.pending(conn) if p["source_id"] in self.visible_sources)
        state = "empty" if not documents else ("stale" if pending else "up_to_date")
        source = self.memory_source()
        response = {
            "lore_available": True,
            "domain": self.domain,
            "database": {"available": True, "engine": "SQLite + FTS5", "schema_version": SCHEMA_VERSION},
            "documents": len(documents) - hidden,
            "documents_hidden_by_policy": hidden,
            "chunks": chunks,
            "last_run": (
                {"finished_at": last["finished_at"], "status": last["status"], "origin": last["origin"]} if last else None
            ),
            "last_check": get_meta(conn, "last_check"),
            "index_state": state,
            "pending_source_changes": pending,
            "search": {
                "text": True,
                "semantic": False,
                "language": self.cfg.search_language,
                "note": "Embeddings / semantic search are not available yet.",
            },
            "mcp": {
                "version": __version__,
                "read_only": not (self.cfg.mcp_allow_memory or self.cfg.mcp_allow_forget),
                "memory": {
                    "can_create": self.cfg.mcp_allow_memory,
                    "can_forget": self.cfg.mcp_allow_forget,
                    "can_edit": False,
                    "max_per_hour": self.cfg.mcp_max_memories_per_hour,
                    "source": source.id if source else None,
                },
                "limits": self.limits.__dict__,
            },
        }
        return response, {}

    def search(self, conn: sqlite3.Connection, query: str, limit: int) -> tuple[dict[str, Any], dict[str, Any]]:
        text = _text(query, "query")
        limit = max(1, min(int(limit), self.cfg.mcp_max_search_results))
        result = search(
            conn, text, limit=100, per_document=2, sources=self.visible_sources, language=self.cfg.search_language
        )
        items = []
        hidden = 0
        best = max((r["score"] for r in result["results"]), default=0.0)
        for r in result["results"]:
            if self.blocked(r["source_id"], r["path"]):
                hidden += 1
                continue
            rel = relative(r["score"], best)
            if rel < self.limits.min_score or len(items) >= limit:
                continue
            items.append(
                {
                    "content": r["excerpt"].replace(MARK_START, "").replace(MARK_END, ""),
                    "source": f"{r['source_id']}:{r['path']}",
                    "heading": " › ".join(r["section_path"]) or r["title"],
                    "document_id": r["document_id"],
                    "chunk_id": r["chunk_id"],
                    "chunk_index": r["chunk_ordinal"],
                    "lines": [r["line_start"], r["line_end"]],
                    "score": round(rel, 3),
                }
            )
        more = max(0, result["total_chunks"] - len(items) - hidden)
        response = {
            "query": text,
            "mode": "text" if result["mode"] == "all" else "text (any of the words)",
            "results": items,
            "returned": len(items),
            "more_available": more > 0,
            "note": (
                f"There are {more} more chunk(s) with lower relevance; refine the search or use lore_get_context."
                if more
                else ("No chunk found." if not items else None)
            ),
            "hidden_by_policy": hidden,
        }
        return response, {
            "documents": sorted({i["document_id"] for i in items}),
            "chunks": [i["chunk_id"] for i in items],
            "empty": not items,
        }

    def get_context(self, conn: sqlite3.Connection, task: str) -> tuple[dict[str, Any], dict[str, Any]]:
        text = _text(task, "task")
        source = self.memory_source()
        package = build(
            conn,
            text,
            self.limits,
            self.blocked,
            self.visible_sources,
            rules=self.lore.rules,
            language=self.cfg.search_language,
            mcp_memory_source=source.id if source else None,
        )
        return package, {
            "documents": [s["document_id"] for s in package["sources"]],
            "chunks": [c["chunk_id"] for c in package["chunks"]],
            "tokens_avoided": package["savings"]["approx_tokens_avoided"],
            "empty": not package["chunks"],
        }

    def get_document(
        self,
        conn: sqlite3.Connection,
        document_id: str,
        chunks: list[int] | None,
        heading: str | None,
        full: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        d = _resolve_document(conn, document_id)
        if d is not None and d["source_id"] not in self.visible_sources:
            d = None  # from another domain: for this process, it does not exist
        if d is None:
            raise ToolError(
                "Document not found. Use the numeric document_id returned by lore_search/lore_get_context "
                "(or 'source:path' exactly as it appears in 'source')."
            )
        if self.blocked(d["source_id"], d["path"]):
            raise ToolError("This document is classified as sensitive and is not delivered through MCP.")
        rows = conn.execute(
            "SELECT id, ordinal, title, section_path, line_start, line_end, text FROM chunk "
            "WHERE document_id = ? ORDER BY ordinal",
            (d["id"],),
        ).fetchall()
        outline = [
            {
                "chunk_index": int(c["ordinal"]),
                "chunk_id": int(c["id"]),
                "heading": " › ".join(json_load(c["section_path"], [])) or c["title"],
                "lines": [c["line_start"], c["line_end"]],
                "characters": len(c["text"]),
            }
            for c in rows
        ]
        size = len(d["content"] or "")
        small = size <= 2 * self.limits.max_chars_per_chunk
        if chunks:
            asked = sorted({int(i) for i in chunks})[: self.limits.max_chunks]
            chosen = [c for c in rows if int(c["ordinal"]) in asked]
            selection = f"requested chunks: {asked}"
        elif heading and heading.strip():
            target = normalize(heading.strip()[:200])
            chosen = [
                c for c in rows if target in normalize(" › ".join(json_load(c["section_path"], [])) + " " + c["title"])
            ][: self.limits.max_chunks]
            selection = f"chunks whose heading contains “{heading.strip()[:80]}”"
        elif full or small:
            chosen = list(rows)
            selection = "whole document" if full else "small document: whole"
        else:
            chosen = []
            selection = "outline only; ask for chunks=[...] or heading=... to read parts, or full=true"

        content = []
        total = 0
        missing = []
        for c in chosen:
            text = c["text"]
            if total + len(text) > self.limits.max_chars_total:
                rest = self.limits.max_chars_total - total
                if rest < 200:
                    missing.append(int(c["ordinal"]))
                    continue
                text = text[:rest].rstrip() + "…"
            total += len(text)
            content.append(
                {
                    "chunk_index": int(c["ordinal"]),
                    "chunk_id": int(c["id"]),
                    "heading": " › ".join(json_load(c["section_path"], [])) or c["title"],
                    "lines": [c["line_start"], c["line_end"]],
                    "content": text,
                    "truncated": text.endswith("…") and len(text) < len(c["text"]),
                }
            )
        response = {
            "document_id": int(d["id"]),
            "source": f"{d['source_id']}:{d['path']}",
            "title": d["title"],
            "category": d["category"],
            "category_confidence": d["category_confidence"],
            "metadata": json_load(d["metadata"], {}),
            "modified_at": d["modified_at"],
            "indexed_at": d["indexed_at"],
            "total_characters": size,
            "chunks_outline": outline[:80],
            "selection": selection,
            "chunks": content,
            "omitted_chunks_over_limit": missing,
            "note": (
                f"Limit of {self.limits.max_chars_total} characters reached; ask for chunks {missing} separately."
                if missing
                else None
            ),
        }
        returned = sum(len(c["content"]) for c in content)
        return response, {
            "documents": [int(d["id"])],
            "chunks": [c["chunk_id"] for c in content],
            "tokens_avoided": max(0, estimate_tokens(size) - estimate_tokens(returned)),
        }

    def get_decisions(
        self, conn: sqlite3.Connection, query: str | None, state: str | None, limit: int
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        text = query.strip()[:INPUT_LIMIT] if query and query.strip() else None
        if state and state not in decisions.STATES:
            raise ToolError(f"state must be one of {', '.join(decisions.STATES)}.")
        limit = max(1, min(int(limit), 20))
        found = decisions.relevant(
            conn,
            text,
            limit=100,
            blocked=self.blocked,
            sources=self.visible_sources,
            rules=self.lore.rules,
            language=self.cfg.search_language,
        )
        items = [d for d in found["decisions"] if not state or d["state"] == state]
        out = []
        for d in items[:limit]:
            item = {
                "code": d["code"],
                "title": d["title"],
                "state": d["state"],
                "date": d["date"],
                "declared_status": d["declared_status"],
                "document_id": d["document_id"],
                "source": f"{d['source_id']}:{d['path']}",
                "superseded_by": d["superseded_by"],
                "cited_by_later_decisions": [
                    {"code": c["code"], "source_line": f"{c['path']}:{c['line']}", "excerpt": c["excerpt"][:200]}
                    for c in d["cited_by_later_decisions"][:5]
                ],
            }
            if "relevance" in d:
                item["relevance"] = d["relevance"]
                item["why"] = d["reason"]
            out.append(item)
        response = {
            "query": text,
            "criteria": found["criteria"],
            "decisions": out,
            "returned": len(out),
            "more_available": len(items) > len(out),
            "states": {
                "active": "status declared as accepted/approved",
                "proposed": "status declared as proposed/draft/under review",
                "superseded": "the record itself, or another decision, says in writing that it was superseded/replaced",
                "deprecated": "status declared as deprecated/rejected/withdrawn/obsolete",
                "undeclared": "no status, or a status that states none of the above",
            },
            "note": (
                "No decision found."
                if not out
                else "A decision cited by a later one is not treated as superseded without explicit text; "
                "check cited_by_later_decisions."
            ),
        }
        return response, {"documents": [i["document_id"] for i in out], "empty": not out}

    def get_entity(self, conn: sqlite3.Connection, entity: str) -> tuple[dict[str, Any], dict[str, Any]]:
        term = _text(entity, "entity")
        g = self.lore.graph(conn, self.visible_sources)
        found = [a for a in graph_module.find(g, term, 10) if self._node_visible(a["node"])]
        if not found or found[0]["points"] < 60:
            response = {
                "entity": term,
                "found": False,
                "suggestions": [
                    {"id": a["node"]["id"], "type": a["node"]["type"], "name": a["node"]["label"], "match": a["reason"]}
                    for a in found[:5]
                ],
                "note": (
                    "The graph only has entities written in the sources: decisions (DEC-001, ADR-0001…), categories, "
                    "documents, commits, migrations and cited files. Concepts like this one do not become entities "
                    "without text that defines them; use lore_get_context or lore_search."
                ),
            }
            return response, {"empty": True}
        node = found[0]["node"]
        detail = graph_module.detail(conn, g, node["id"], chunk_limit=0) or {}
        relations = []
        documents: dict[int, dict[str, Any]] = {}
        related_decisions: dict[str, dict[str, Any]] = {}
        hidden = 0
        for r in detail.get("relations", []):
            other = r["other"]
            if not self._node_visible(other):
                hidden += 1
                continue
            evidence = [
                {
                    "source": f"{e['source_id']}:{e['path']}" if e.get("path") else e.get("file"),
                    "line": e.get("line"),
                    "chunk_index": e.get("chunk_ordinal"),
                    "excerpt": (e.get("excerpt") or "")[:200] or None,
                    "rule": e["rule"],
                }
                for e in r["edge"]["evidence"][:2]
                if not (e.get("path") and self.blocked(e["source_id"], e["path"]))
            ]
            relations.append(
                {
                    "relation": r["edge"]["type"],
                    "direction": r["direction"],
                    "other": {"id": other["id"], "type": other["type"], "name": other["label"]},
                    "confidence": r["edge"].get("confidence"),
                    "evidence_count": len(r["edge"]["evidence"]),
                    "evidence": evidence,
                }
            )
            if other.get("document_id") is not None:
                target = related_decisions if other["type"] == "decision" else documents
                key = other.get("code") if other["type"] == "decision" else other["document_id"]
                target.setdefault(key, {"document_id": other["document_id"], "source": other["detail"], "title": other["label"]})
        max_relations = 25
        response = {
            "entity": term,
            "found": True,
            "match": found[0]["reason"],
            "node": {
                "id": node["id"],
                "type": node["type"],
                "name": node["label"],
                "detail": node.get("detail"),
                "document_id": node.get("document_id"),
                "category": node.get("category"),
                "category_confidence": node.get("confidence"),
            },
            "relations": relations[:max_relations],
            "relations_total": len(relations),
            "more_relations": len(relations) > max_relations,
            "related_documents": list(documents.values())[:10],
            "related_decisions": list(related_decisions.values())[:10],
            "hidden_by_policy": hidden,
            "other_matches": [
                {"id": a["node"]["id"], "type": a["node"]["type"], "name": a["node"]["label"]} for a in found[1:5]
            ],
            "note": "Every relation comes from the text of the sources or from the taxonomy; see 'evidence' (source, line, rule).",
        }
        docs = [d["document_id"] for d in documents.values()] + [d["document_id"] for d in related_decisions.values()]
        return response, {"documents": docs[:50]}

    # ------------------------------------------------------------------ memory (the only writes)

    def memory_source(self) -> Any:
        """The source that reads the memories folder — only if it is of this domain (memory never crosses the wall)."""
        source = memories.memory_source(self.lore.sources, self.cfg.memories_dir)
        return source if source is not None and source.domain == self.domain else None

    def remember(
        self,
        conn: sqlite3.Connection,
        client: dict[str, str | None],
        title: str,
        content: str,
        kind: str | None,
        subject: str | None,
        evidence: list[str] | None,
        related_decisions: list[str] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.cfg.mcp_allow_memory:
            raise ToolError("Writing memories through MCP is disabled (LORE_MCP_ALLOW_MEMORY=false).")
        source = self.memory_source()
        if source is None:
            raise ToolError(
                f"No source of the domain '{self.domain}' in sources.yaml reads the memories folder (LORE_MEMORIES_DIR); "
                "the memory would never be indexed here, so nothing was written."
            )
        new = memories.validate(
            title, content, kind, subject, evidence, related_decisions,
            max_chars=self.cfg.mcp_max_chars_per_memory, rules=self.lore.rules,
        )
        folder = self.cfg.memories_dir
        if memories.existing(folder, new.sha256) is None:
            recent = self._memories_last_hour(conn)
            if recent >= self.cfg.mcp_max_memories_per_hour:
                raise ToolError(
                    f"Limit of {self.cfg.mcp_max_memories_per_hour} memories per hour reached; nothing was written. "
                    "Merge related items into a single memory."
                )
        author = " ".join(v for v in (client.get("name"), client.get("version")) if v) or None
        saved = memories.save(folder, new, author=author, source=source, privacy=self.lore.privacy)

        document = self._memory_document(conn, source.id, saved.file)
        indexing = "already_indexed" if document else "pending"
        reason = None
        if document is None and self.cfg.mcp_memory_reindex:
            indexing, reason = self._request_reindex()
            document = self._memory_document(conn, source.id, saved.file)
            indexing = "indexed" if document else "pending"
        if document is None:
            indexing_note = (
                "Not in the index yet"
                + (f" ({reason})" if reason else "")
                + ": it goes in on lore's next automatic sync (every LORE_SYNC_SECONDS) or with 'lore index'."
            )
        else:
            indexing_note = "Already searchable with lore_search and lore_get_context."
        response = {
            "status": "created" if saved.created else "already_existed",
            "memory_id": saved.file.removesuffix(".md"),
            "source": f"{source.id}:{saved.file}",
            "title": new.title,
            "kind": new.kind,
            "verification": "unverified",
            "indexing": {
                "state": "indexed" if document else "pending",
                "document_id": int(document["id"]) if document else None,
                "note": indexing_note,
            },
            "note": (
                "Memory written to a new file, marked as unverified; no existing document was changed."
                if saved.created
                else "A memory with exactly this content already existed; nothing was written again."
            ),
        }
        return response, {
            "documents": [int(document["id"])] if document else [],
            "memory": {
                "source": source.id,
                "file": saved.file,
                "status": "created" if saved.created else "already_existed",
                "sha256": saved.sha256[:12],
                "indexing": indexing,
            },
        }

    def _memories_last_hour(self, conn: sqlite3.Connection, action: str = "mcp_memory", status: str = "created") -> int:
        since = (datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="seconds")
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM audit WHERE origin = 'mcp' AND action = ? AND at >= ? "
                "AND json_extract(details, '$.memory.status') = ?",
                (action, since, status),
            ).fetchone()[0]
        )

    def forget(
        self, conn: sqlite3.Connection, client: dict[str, str | None], memory: str, reason: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.cfg.mcp_allow_forget:
            raise ToolError("Forgetting memories through MCP is disabled (LORE_MCP_ALLOW_FORGET=false).")
        source = self.memory_source()
        if source is None:
            raise ToolError(f"No source of the domain '{self.domain}' reads the memories folder; there is nothing to forget.")
        why = " ".join(_text(reason, "reason").split())
        if len(why) < 5:
            raise ToolError("'reason' must explain in a few words why the memory goes away (at least 5 characters).")
        if redact(why)[1]:
            raise ToolError("'reason' seems to contain a secret. Nothing was done.")
        name, document = self._resolve_memory(conn, source.id, memory)
        if self._memories_last_hour(conn, "mcp_memory_forgotten", "forgotten") >= self.cfg.mcp_max_memories_per_hour:
            raise ToolError(f"Limit of {self.cfg.mcp_max_memories_per_hour} forgotten memories per hour reached; nothing was done.")
        try:
            in_trash = memories.forget(self.cfg.memories_dir, name)
        except memories.MemoryRejected as error:
            raise ToolError(str(error)) from error

        indexing = "pending"
        if self.cfg.mcp_memory_reindex:
            indexing, _ = self._request_reindex()
        still = self._memory_document(conn, source.id, name)
        response = {
            "status": "forgotten",
            "memory_id": name.removesuffix(".md"),
            "title": document["title"] if document else None,
            "reason": why,
            "recoverable": True,
            "trash": f"{memories.TRASH}/{in_trash}",
            "indexing": {
                "state": "pending" if still else "removed_from_index",
                "note": (
                    "It still shows up in searches until lore's next sync (or 'lore index')."
                    if still
                    else "Already out of searches and context."
                ),
            },
            "note": (
                "The file was moved to the trash of the memories folder (nothing was deleted); to restore it, move it "
                "back with its original name. Documents of other sources are not affected."
            ),
        }
        return response, {
            "documents": [int(document["id"])] if document else [],
            "memory": {
                "source": source.id,
                "file": name,
                "status": "forgotten",
                "title": document["title"] if document else None,
                "reason": why[:300],
                "trash": in_trash,
                "indexing": "indexed" if not still else indexing,
            },
        }

    def _resolve_memory(self, conn: sqlite3.Connection, source_id: str, identifier: str) -> tuple[str, sqlite3.Row | None]:
        """File name (and index row) of an MCP memory, by memory_id, 'source:file' or document_id."""
        text = _text(identifier, "memory")
        if text.startswith("doc:"):
            text = text[4:]
        if text.isdigit():
            row = conn.execute(
                "SELECT id, source_id, path, title FROM document WHERE id = ? AND status != 'removed'", (int(text),)
            ).fetchone()
            if row is None or row["source_id"] not in self.visible_sources:
                raise ToolError("Document not found.")
            if row["source_id"] != source_id:
                raise ToolError(
                    f"Document {text} belongs to the source '{row['source_id']}', which lore never changes. lore_forget only "
                    "forgets memories created through MCP; to correct this one, record the correction with lore_remember "
                    "and tell the user."
                )
            return row["path"], row
        name = text.removeprefix(f"{source_id}:")
        if not name.endswith(".md"):
            name += ".md"
        if not memories.GENERATED_NAME.match(name):
            raise ToolError(
                "Give the memory_id (or 'source') of a memory created by lore_remember, or its document_id. "
                "Memories of other sources are not forgotten through MCP."
            )
        row = conn.execute(
            "SELECT id, source_id, path, title FROM document WHERE source_id = ? AND path = ? AND status != 'removed'",
            (source_id, name),
        ).fetchone()
        return name, row

    @staticmethod
    def _memory_document(conn: sqlite3.Connection, source_id: str, file: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT id FROM document WHERE source_id = ? AND path = ? AND status != 'removed'", (source_id, file)
        ).fetchone()

    def _request_reindex(self) -> tuple[str, str | None]:
        """Asks the local lore server to index now (the MCP server never writes to the index). Failure loses nothing."""
        host = self.cfg.http_host
        address = f"[{host}]" if ":" in host else host
        request = urllib.request.Request(
            f"http://{address}:{self.cfg.http_port}/api/lore/reindex",
            data=b"{}",
            method="POST",
            headers={
                "X-Lore-Confirm": "reindex",
                "Content-Type": "application/json",
                "User-Agent": f"lore-mcp/{__version__} (lore_remember)",
            },
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never through a proxy
        try:
            with opener.open(request, timeout=10) as response:
                response.read()
            return "indexed", None
        except urllib.error.HTTPError as error:
            if error.code == 409:
                return "pending", "an indexing run was in progress"
            return "pending", f"lore answered HTTP {error.code}"
        except (urllib.error.URLError, OSError, TimeoutError):
            return "pending", "the local lore server is not running"

    def _node_visible(self, node: dict[str, Any]) -> bool:
        return not (node.get("path") and self.blocked(node.get("source_id") or "", node["path"]))


def redact_values(value: Any) -> tuple[Any, int]:
    """Applies secret redaction to every text of the response (before it becomes JSON)."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        total = 0
        out = {}
        for key, item in value.items():
            out[key], n = redact_values(item)
            total += n
        return out, total
    if isinstance(value, list):
        total = 0
        items = []
        for item in value:
            new, n = redact_values(item)
            items.append(new)
            total += n
        return items, total
    return value, 0


def _for_audit(value: Any) -> Any:
    """Input as it goes into the audit log: texts truncated and redacted, short lists."""
    if isinstance(value, str):
        return redact(value[:300])[0]
    if isinstance(value, list):
        return [_for_audit(item) for item in value[:20]]
    return value


def _text(value: str | None, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"'{name}' cannot be empty.")
    return value.strip()[:INPUT_LIMIT]


def _resolve_document(conn: sqlite3.Connection, identifier: str | int) -> sqlite3.Row | None:
    """Only through the index: numeric id, 'doc:N' or the exact 'source:path'. Never opens a path on disk."""
    text = str(identifier).strip()
    if text.startswith("doc:"):
        text = text[4:]
    columns = (
        "SELECT id, source_id, path, title, category, category_confidence, metadata, modified_at, "
        "indexed_at, content FROM document WHERE status != 'removed' AND "
    )
    if text.isdigit():
        return conn.execute(columns + "id = ?", (int(text),)).fetchone()
    source, separator, path = text.partition(":")
    if not separator or not source or not path or len(text) > 400:
        return None
    return conn.execute(columns + "source_id = ? AND path = ?", (source, path)).fetchone()


def _client(ctx: Context | None) -> dict[str, str | None]:
    try:
        params = ctx.session.client_params if ctx else None
        info = params.client_info if params else None
        return {"name": info.name if info else None, "version": info.version if info else None}
    except Exception:
        return {"name": None, "version": None}


def create_server(layer: McpLayer) -> MCPServer:
    allow_memory = layer.cfg.mcp_allow_memory
    allow_forget = layer.cfg.mcp_allow_forget
    server = MCPServer(
        name=SERVER_NAME,
        version=__version__,
        instructions=layer.instructions()
        + (MEMORY_INSTRUCTIONS if allow_memory else "")
        + (FORGET_INSTRUCTIONS if allow_forget else ""),
    )

    @server.tool(name="lore_status", annotations=READ_ONLY)
    async def lore_status(ctx: Context = None) -> str:  # type: ignore[assignment]
        """Operational status of lore: index, database, number of documents and chunks, last indexing run."""
        return layer.run("lore_status", {}, _client(ctx), layer.status)

    @server.tool(name="lore_search", annotations=READ_ONLY)
    async def lore_search(query: str, limit: int = 10, ctx: Context = None) -> str:  # type: ignore[assignment]
        """Full-text search over the indexed chunks (accent- and case-insensitive, plurals find singulars, codes
        such as DEC-031 or ADR-0007 match exactly). Returns short chunks with source, heading, document_id,
        chunk_id and score (0–1, relative to the best). To read a whole chunk use lore_get_document with
        chunks=[chunk_index]."""
        return layer.run("lore_search", {"query": query, "limit": limit}, _client(ctx), lambda c: layer.search(c, query, limit))

    @server.tool(name="lore_get_context", annotations=READ_ONLY)
    async def lore_get_context(task: str, ctx: Context = None) -> str:  # type: ignore[assignment]
        """MAIN TOOL. Builds a small context package for the task: a few very relevant chunks, decisions (with
        state), memories, entities and sources — each item with its origin. Says what was left out by the limits."""
        return layer.run("lore_get_context", {"task": task}, _client(ctx), lambda c: layer.get_context(c, task))

    @server.tool(name="lore_get_document", annotations=READ_ONLY)
    async def lore_get_document(
        document_id: str,
        chunks: list[int] | None = None,
        heading: str | None = None,
        full: bool = False,
        ctx: Context = None,  # type: ignore[assignment]
    ) -> str:
        """One document by document_id (a number) or 'source:path'. Without options it returns the outline (and
        the text only when the document is small); chunks=[indexes] or heading='...' return only those parts;
        full=true returns the whole document up to the size limit."""
        return layer.run(
            "lore_get_document",
            {"document_id": str(document_id), "chunks": chunks, "heading": heading, "full": full},
            _client(ctx),
            lambda c: layer.get_document(c, document_id, chunks, heading, full),
        )

    @server.tool(name="lore_get_decisions", annotations=READ_ONLY)
    async def lore_get_decisions(
        query: str | None = None,
        state: str | None = None,
        limit: int = 10,
        ctx: Context = None,  # type: ignore[assignment]
    ) -> str:
        """Project decisions related to the subject (or all of them, without query), with state active / proposed /
        superseded / deprecated / undeclared taken from what is written. A decision cited by a later one appears in
        cited_by_later_decisions for review — it is not marked as superseded without explicit text."""
        return layer.run(
            "lore_get_decisions",
            {"query": query, "state": state, "limit": limit},
            _client(ctx),
            lambda c: layer.get_decisions(c, query, state, limit),
        )

    @server.tool(name="lore_get_entity", annotations=READ_ONLY)
    async def lore_get_entity(entity: str, ctx: Context = None) -> str:  # type: ignore[assignment]
        """One entity of the knowledge graph (decision such as DEC-001/ADR-0001, category, document, commit,
        migration or cited file): relations, related documents and decisions, with the evidence of each relation."""
        return layer.run("lore_get_entity", {"entity": entity}, _client(ctx), lambda c: layer.get_entity(c, entity))

    if allow_memory:

        @server.tool(name="lore_remember", annotations=CREATE_ONLY)
        async def lore_remember(
            title: str,
            content: str,
            kind: str = "fact",
            subject: str | None = None,
            evidence: list[str] | None = None,
            related_decisions: list[str] | None = None,
            ctx: Context = None,  # type: ignore[assignment]
        ) -> str:
            """Records a NEW memory in lore: creates a new .md in lore's memories folder, marked as unverified, and
            asks for indexing. Never changes, moves or deletes a document, memory or decision.
            Use it only for something durable that is not in the sources yet (check first with lore_get_context):
            a fact confirmed with the user, a preference, a procedure, a known problem, a reference.
            kind: fact | decision | preference | procedure | problem | reference.
            evidence: where it came from (lore source/document_id, commit, file, conversation with the user).
            related_decisions: codes such as DEC-031 or ADR-0007. Text with a secret (password, token, key) is
            refused; do not record personal data. The same content is never written twice."""
            client = _client(ctx)
            return layer.run(
                "lore_remember",
                {
                    "title": title,
                    "content": content,
                    "kind": kind,
                    "subject": subject,
                    "evidence": evidence,
                    "related_decisions": related_decisions,
                },
                client,
                lambda c: layer.remember(c, client, title, content, kind, subject, evidence, related_decisions),
                action="mcp_memory",
            )

    if allow_forget:

        @server.tool(name="lore_forget", annotations=FORGETS)
        async def lore_forget(
            memory: str,
            reason: str,
            ctx: Context = None,  # type: ignore[assignment]
        ) -> str:
            """Forgets a memory created by lore_remember that is wrong or outdated: the file goes to a recoverable
            trash and the memory leaves searches and context. memory: the memory_id, the 'source' or the
            document_id that lore returned. reason: why it goes away (required, goes to the audit log). It does NOT
            delete documents of the sources — for wrong information there, use lore_remember with the correction.
            Confirm with the user before forgetting something they wrote or validated."""
            client = _client(ctx)
            return layer.run(
                "lore_forget",
                {"memory": memory, "reason": reason},
                client,
                lambda c: layer.forget(c, client, memory, reason),
                action="mcp_memory_forgotten",
            )

    return server


def main() -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        layer = McpLayer(settings())
    except Exception as error:
        print(f"lore MCP: invalid configuration: {error}", file=sys.stderr)
        return 2
    layer._heartbeat()
    try:
        create_server(layer).run("stdio")
    except KeyboardInterrupt:
        pass
    finally:
        layer.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
