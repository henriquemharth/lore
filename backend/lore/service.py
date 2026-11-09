"""Facade used by the API and the command line: configuration, indexer and read queries."""

import os
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from typing import Any

from lore import __version__, memories, search
from lore import decisions as decisions_module
from lore import graph as graph_module
from lore.categories import UNCATEGORIZED, load_taxonomy
from lore.config import Settings
from lore.context import Limits
from lore.context import build as build_context
from lore.db import connect, get_meta, json_load
from lore.indexer import Indexer, RunContext, RunSummary
from lore.privacy import load_privacy
from lore.sources import load_sources

PENDING_CACHE_SECONDS = 5.0


class Lore:
    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self.sources = load_sources(cfg.sources_file)
        self.taxonomy = load_taxonomy(cfg.taxonomy_file)
        self.privacy = load_privacy(cfg.privacy_file)
        self.indexer = Indexer(cfg.database, self.sources, self.taxonomy, privacy=self.privacy)
        self._pending: tuple[float, list[dict[str, Any]]] | None = None
        self._pending_lock = threading.Lock()
        self._graph: tuple[str, graph_module.Graph] | None = None
        self._graph_lock = threading.Lock()

    @property
    def rules(self) -> Any:
        return self.sources.decisions

    @property
    def language(self) -> str:
        return self.cfg.search_language

    # ------------------------------------------------------------------ graph (derived from the index; cached per version)

    def graph(self, conn: sqlite3.Connection, sources: tuple[str, ...] | None = None) -> graph_module.Graph:
        """``sources``: only the graph of these sources (the MCP passes its domain's); without them, the whole graph."""
        version = "|".join(
            str(x)
            for x in (
                get_meta(conn, "last_run"),
                conn.execute("SELECT COUNT(*), MAX(indexed_at) FROM document").fetchone()[:],
                sources,
            )
        )
        with self._graph_lock:
            if self._graph and self._graph[0] == version:
                return self._graph[1]
            built = graph_module.build(conn, self.taxonomy, sources, self.rules)
            self._graph = (version, built)
            return built

    def connect(self) -> sqlite3.Connection:
        return connect(self.cfg.database)

    def reindex(self, context: RunContext, full: bool = False) -> RunSummary:
        summary = self.indexer.run(context, full)
        with self._pending_lock:
            self._pending = None
        return summary

    # ------------------------------------------------------------------ pending changes (sources × index)

    def pending(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        with self._pending_lock:
            if self._pending and time.monotonic() - self._pending[0] < PENDING_CACHE_SECONDS:
                return self._pending[1]
            value = self.indexer.pending(conn)
            self._pending = (time.monotonic(), value)
            return value

    # ------------------------------------------------------------------ status

    def status(self, conn: sqlite3.Connection) -> dict[str, Any]:
        by_status = dict(conn.execute("SELECT status, COUNT(*) FROM document GROUP BY status").fetchall())
        by_source = {
            row[0]: row[1]
            for row in conn.execute("SELECT source_id, COUNT(*) FROM document WHERE status != 'removed' GROUP BY source_id")
        }
        chunks = conn.execute("SELECT COUNT(*) FROM chunk").fetchone()[0]
        links = conn.execute("SELECT COUNT(*), COUNT(target_document_id) FROM link").fetchone()

        last_id = get_meta(conn, "last_run")
        last = self._run(conn, int(last_id)) if last_id else None
        running = conn.execute(
            "SELECT id, origin, started_at FROM run WHERE status = 'running' ORDER BY id DESC LIMIT 1"
        ).fetchone()

        errors = [
            {
                "document_id": row["id"],
                "source_id": row["source_id"],
                "path": row["path"],
                "error": row["error"],
                "kind": row["error_kind"],
            }
            for row in conn.execute(
                "SELECT id, source_id, path, error, error_kind FROM document WHERE status = 'error' ORDER BY source_id, path"
            )
        ]

        stored = {row["id"]: row for row in conn.execute("SELECT * FROM source")}
        sources = []
        for s in self.sources.sources:
            row = stored.get(s.id)
            sources.append(
                {
                    "id": s.id,
                    "name": s.name,
                    "type": s.type,
                    "domain": s.domain,
                    "root": str(s.root),
                    "ref": s.ref,
                    "ref_commit": row["ref_commit"] if row else None,
                    "available": bool(row["available"]) if row else None,
                    "warnings": json_load(row["warnings"], []) if row else [],
                    "checked_at": row["checked_at"] if row else None,
                    "documents": by_source.get(s.id, 0),
                }
            )

        return {
            "version": __version__,
            "read_only": True,
            "documents": {
                "total": sum(v for k, v in by_status.items() if k != "removed"),
                "indexed": by_status.get("indexed", 0),
                "with_errors": by_status.get("error", 0),
                "removed": by_status.get("removed", 0),
            },
            "chunks": chunks,
            "links": {"total": links[0], "resolved": links[1]},
            "last_run": last,
            "last_check": get_meta(conn, "last_check"),
            "run_in_progress": dict(running) if running else None,
            "pending": self.pending(conn),
            "errors": errors,
            "categories": self.categories(conn),
            "sources": sources,
            "domains": [
                {"name": d, "description": self.sources.describe(d), "sources": [s.id for s in self.sources.in_domain(d)]}
                for d in self.sources.domains()
            ],
            "search": {"text": True, "language": self.language, "semantic": search.semantic_status()},
            "sync_seconds": self.cfg.sync_seconds,
            "taxonomy": {"root": self.taxonomy.root, "categories": list(self.taxonomy.categories)},
        }

    def categories(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        primary: Counter[str] = Counter()
        explicit: Counter[str] = Counter()
        suggestions: Counter[str] = Counter()
        for row in conn.execute("SELECT categories_detail FROM document WHERE status != 'removed'"):
            for a in json_load(row["categories_detail"], []):
                if a.get("primary"):
                    primary[a["category"]] += 1
                if a.get("confidence") == "explicit":
                    explicit[a["category"]] += 1
                else:
                    suggestions[a["category"]] += 1
        order = [*self.taxonomy.categories, UNCATEGORIZED]
        extras = sorted(c for c in {*primary, *explicit, *suggestions} if c not in order)
        return [
            {
                "category": c,
                "documents": primary.get(c, 0),
                "explicit": explicit.get(c, 0),
                "suggestions": suggestions.get(c, 0),
                "tagged": explicit.get(c, 0) + suggestions.get(c, 0),
            }
            for c in [*order, *extras]
            if primary.get(c) or explicit.get(c) or suggestions.get(c)
        ]

    def _run(self, conn: sqlite3.Connection, run_id: int) -> dict[str, Any] | None:
        row = conn.execute("SELECT * FROM run WHERE id = ?", (run_id,)).fetchone()
        if not row:
            return None
        items = [
            dict(i)
            for i in conn.execute(
                "SELECT document_id, source_id, path, event, detail FROM run_item WHERE run_id = ? ORDER BY id LIMIT 500",
                (run_id,),
            )
        ]
        return {**dict(row), "full": bool(row["full"]), "totals": json_load(row["totals"], {}), "items": items}

    def runs(self, conn: sqlite3.Connection, limit: int) -> list[dict[str, Any]]:
        return [
            {**dict(row), "full": bool(row["full"]), "totals": json_load(row["totals"], {})}
            for row in conn.execute("SELECT * FROM run ORDER BY id DESC LIMIT ?", (limit,))
        ]

    # ------------------------------------------------------------------ documents

    def documents(
        self,
        conn: sqlite3.Connection,
        *,
        source: str | None = None,
        category: str | None = None,
        status: str | None = None,
        q: str | None = None,
        include_removed: bool = False,
        limit: int = 1000,
        offset: int = 0,
    ) -> dict[str, Any]:
        filters: list[str] = []
        params: list[Any] = []
        if status:
            filters.append("d.status = ?")
            params.append(status)
        elif not include_removed:
            filters.append("d.status != 'removed'")
        if source:
            filters.append("d.source_id = ?")
            params.append(source)
        if category:
            filters.append(
                "EXISTS (SELECT 1 FROM json_each(d.categories) j WHERE j.value = ? OR j.value LIKE ? ESCAPE '\\')"
            )
            params.extend([category, search.escape_like(category) + "/%"])
        if q:
            filters.append("(d.path LIKE ? ESCAPE '\\' OR d.title LIKE ? ESCAPE '\\')")
            pattern = f"%{search.escape_like(q.strip()[:100])}%"
            params.extend([pattern, pattern])
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        total = conn.execute(f"SELECT COUNT(*) FROM document d {where}", params).fetchone()[0]
        rows = conn.execute(
            f"""SELECT d.id, d.source_id, d.path, d.name, d.size, d.hash, d.modified_at, d.title,
                       d.category, d.categories, d.category_confidence, d.categories_detail, d.status, d.error,
                       d.total_chunks, d.version, d.indexed_at, d.removed_at
                FROM document d {where}
                ORDER BY d.source_id, d.path LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
        pending = {(p["source_id"], p["path"]): p["type"] for p in self.pending(conn)}
        return {
            "total": total,
            "documents": [
                {
                    **dict(row),
                    "categories": json_load(row["categories"], []),
                    "categories_detail": json_load(row["categories_detail"], []),
                    "pending": pending.get((row["source_id"], row["path"])),
                }
                for row in rows
            ],
        }

    def document(self, conn: sqlite3.Connection, document_id: int) -> dict[str, Any] | None:
        row = conn.execute("SELECT * FROM document WHERE id = ?", (document_id,)).fetchone()
        if not row:
            return None
        chunks = [
            {**dict(c), "section_path": json_load(c["section_path"], []), "sections": json_load(c["sections"], [])}
            for c in conn.execute(
                "SELECT id, ordinal, title, section_path, sections, level, part, parts, line_start, line_end, "
                "char_start, char_end, size, hash, text FROM chunk WHERE document_id = ? ORDER BY ordinal",
                (document_id,),
            )
        ]
        outgoing = [
            dict(lk)
            for lk in conn.execute(
                "SELECT l.line, l.type, l.target, l.anchor, l.text, l.target_document_id, d.title AS target_title "
                "FROM link l LEFT JOIN document d ON d.id = l.target_document_id "
                "WHERE l.document_id = ? ORDER BY l.line",
                (document_id,),
            )
        ]
        incoming = [
            dict(lk)
            for lk in conn.execute(
                "SELECT DISTINCT d.id AS document_id, d.title, d.source_id, d.path "
                "FROM link l JOIN document d ON d.id = l.document_id "
                "WHERE l.target_document_id = ? AND d.id != ? ORDER BY d.path",
                (document_id, document_id),
            )
        ]
        versions = [
            dict(v)
            for v in conn.execute(
                "SELECT version, event, hash, size, modified_at, run_id, recorded_at "
                "FROM document_version WHERE document_id = ? ORDER BY id DESC",
                (document_id,),
            )
        ]
        content = row["content"] or ""
        total_chars = max(len(content), 1)
        for c in chunks:
            c["position_percent"] = round(100 * c["char_start"] / total_chars, 1)
        pending = next(
            (p["type"] for p in self.pending(conn) if p["source_id"] == row["source_id"] and p["path"] == row["path"]),
            None,
        )
        source = next((s for s in self.sources.sources if s.id == row["source_id"]), None)
        return {
            **{k: row[k] for k in row.keys() if k not in ("content", "identity")},
            "categories": json_load(row["categories"], []),
            "categories_detail": json_load(row["categories_detail"], []),
            "frontmatter": json_load(row["frontmatter"]),
            "metadata": json_load(row["metadata"], {}),
            "headings": json_load(row["headings"], []),
            "warnings": json_load(row["warnings"], []),
            "source_name": source.name if source else row["source_id"],
            "source_type": source.type if source else None,
            "content": content,
            "chunks": chunks,
            "links_out": outgoing,
            "links_in": incoming,
            "versions": versions,
            "pending": pending,
        }

    # ------------------------------------------------------------------ graph, decisions and context (read)

    def graph_summary(self, conn: sqlite3.Connection) -> dict[str, Any]:
        """Graph for the UI: nodes and edges without the evidence (it comes on demand per node/edge)."""
        g = self.graph(conn)
        return {
            "root": g.root,
            "nodes": g.sorted_nodes(),
            "edges": [
                {**{k: v for k, v in e.items() if k != "evidence"}, "total_evidence": len(e["evidence"])}
                for e in g.edges.values()
            ],
            "counts": g.counts(),
            "types": {"nodes": list(graph_module.NODE_TYPES), "edges": list(graph_module.EDGE_TYPES)},
        }

    def graph_node(self, conn: sqlite3.Connection, ident: str) -> dict[str, Any] | None:
        return graph_module.detail(conn, self.graph(conn), ident)

    def graph_edge(self, conn: sqlite3.Connection, ident: str) -> dict[str, Any] | None:
        g = self.graph(conn)
        e = g.edges.get(ident)
        if e is None:
            return None
        return {"edge": e, "from": g.nodes[e["from"]], "to": g.nodes[e["to"]]}

    def decisions(self, conn: sqlite3.Connection, q: str | None, state: str | None) -> dict[str, Any]:
        r = decisions_module.relevant(conn, q, limit=200, rules=self.rules, language=self.language)
        if state:
            r["decisions"] = [d for d in r["decisions"] if d["state"] == state]
        return r

    def mcp_limits(self) -> Limits:
        c = self.cfg
        return Limits(
            max_chunks=c.mcp_max_chunks,
            max_chars_per_chunk=c.mcp_max_chars_per_chunk,
            max_chars_total=c.mcp_max_chars_total,
            max_documents=c.mcp_max_documents,
            min_score=c.mcp_min_score,
        )

    def mcp_memory_source_id(self) -> str | None:
        source = memories.memory_source(self.sources, self.cfg.memories_dir)
        return source.id if source else None

    def context(self, conn: sqlite3.Connection, task: str) -> dict[str, Any]:
        """The same package the MCP server would deliver for the task (preview in the UI)."""
        return build_context(
            conn,
            task,
            self.mcp_limits(),
            self.privacy.blocked,
            rules=self.rules,
            language=self.language,
            mcp_memory_source=self.mcp_memory_source_id(),
        )

    # ------------------------------------------------------------------ MCP panel (audit + processes)

    def _mcp_memories(self, conn: sqlite3.Connection, entries: list[sqlite3.Row]) -> dict[str, Any]:
        """Memories created through MCP (lore_remember), from the audit log, with the indexed document."""
        source = memories.memory_source(self.sources, self.cfg.memories_dir)
        recent = []
        created = 0
        forgotten = 0
        for r in entries:
            d = json_load(r["details"], {}) or {}
            memory = d.get("memory") or {}
            if d.get("tool") not in ("lore_remember", "lore_forget") or not memory.get("file"):
                continue
            if d.get("result") == "error":
                continue
            created += memory.get("status") == "created"
            forgotten += memory.get("status") == "forgotten"
            if len(recent) >= 20:
                continue
            source_id = memory.get("source") or (source.id if source else None)
            document = (
                conn.execute(
                    "SELECT id, status FROM document WHERE source_id = ? AND path = ?", (source_id, memory["file"])
                ).fetchone()
                if source_id
                else None
            )
            client = d.get("client") or {}
            recent.append(
                {
                    "at": r["at"],
                    "client": " ".join(v for v in (client.get("name"), client.get("version")) if v) or "unidentified",
                    "title": (d.get("input") or {}).get("title") or memory.get("title") or memory["file"],
                    "reason": memory.get("reason"),
                    "file": memory["file"],
                    "status": memory.get("status"),
                    "document_id": document["id"] if document and document["status"] != "removed" else None,
                    "removed": bool(document and document["status"] == "removed"),
                }
            )
        return {
            "allowed": self.cfg.mcp_allow_memory,
            "forget_allowed": self.cfg.mcp_allow_forget,
            "folder": str(self.cfg.memories_dir),
            "source": source.id if source else None,
            "max_per_hour": self.cfg.mcp_max_memories_per_hour,
            "created": created,
            "forgotten": forgotten,
            "recent": recent,
        }

    def mcp_panel(self, conn: sqlite3.Connection, tools: list[dict[str, str]], command: str) -> dict[str, Any]:
        processes = []
        folder = self.cfg.data_dir / "mcp"
        if folder.is_dir():
            for file in sorted(folder.glob("*.json")):
                entry = json_load(file.read_text(encoding="utf-8"), {}) or {}
                pid = entry.get("pid")
                alive = False
                if isinstance(pid, int):
                    try:
                        os.kill(pid, 0)
                        alive = True
                    except OSError:
                        alive = False
                if alive:
                    processes.append(
                        {
                            "pid": pid,
                            "started_at": entry.get("started_at"),
                            "last_used_at": entry.get("last_used_at"),
                            "client": entry.get("client"),
                            "domain": entry.get("domain"),
                        }
                    )
        # "reset-mcp-usage" only moves the starting point; the audit log is never deleted.
        since = int(get_meta(conn, "mcp_usage_since_id") or 0)
        entries = conn.execute(
            "SELECT id, at, agent, details FROM audit WHERE origin = 'mcp' AND id > ? ORDER BY id DESC LIMIT 2000",
            (since,),
        ).fetchall()
        queries = []
        usage = {
            "queries": 0, "errors": 0, "no_results": 0, "chunks": 0, "characters": 0, "approx_tokens": 0,
            "approx_tokens_avoided": 0, "secrets_redacted": 0,
        }
        by_tool: Counter[str] = Counter()
        clients: dict[str, dict[str, Any]] = defaultdict(lambda: {"queries": 0, "last": None})
        for r in entries:
            d = json_load(r["details"], {}) or {}
            usage["queries"] += 1
            usage["errors"] += d.get("result") == "error"
            usage["no_results"] += d.get("result") == "no_results"
            usage["chunks"] += len(d.get("chunks") or [])
            usage["characters"] += int(d.get("response_characters") or 0)
            usage["approx_tokens"] += int(d.get("approx_tokens") or 0)
            usage["approx_tokens_avoided"] += int(d.get("approx_tokens_avoided") or 0)
            usage["secrets_redacted"] += int(d.get("secrets_redacted") or 0)
            by_tool[d.get("tool") or "?"] += 1
            client = d.get("client") or {}
            name = " ".join(v for v in (client.get("name"), client.get("version")) if v) or "unidentified"
            clients[name]["queries"] += 1
            clients[name]["last"] = clients[name]["last"] or r["at"]
            if len(queries) < 50:
                given = d.get("input") or {}
                queries.append(
                    {
                        "id": r["id"],
                        "at": r["at"],
                        "tool": d.get("tool"),
                        "domain": d.get("domain"),
                        "client": name,
                        "query": next((str(v) for v in given.values() if isinstance(v, str) and v), None),
                        "documents": len(d.get("documents") or []),
                        "chunks": len(d.get("chunks") or []),
                        "characters": d.get("response_characters"),
                        "approx_tokens": d.get("approx_tokens"),
                        "duration_ms": d.get("duration_ms"),
                        "result": d.get("result"),
                        "error": d.get("error"),
                    }
                )
        return {
            "online": bool(processes),
            "processes": processes,
            "command": command,
            "transport": "stdio (local; the AI client starts the process)",
            "read_only": not (self.cfg.mcp_allow_memory or self.cfg.mcp_allow_forget),
            "memories": self._mcp_memories(conn, entries),
            "tools": tools,
            "limits": self.mcp_limits().__dict__,
            "usage": {**usage, "by_tool": dict(by_tool), "token_estimate": "≈ 4 characters per token"},
            "clients": [
                {"client": k, **v} for k, v in sorted(clients.items(), key=lambda x: x[1]["last"] or "", reverse=True)
            ],
            "recent_queries": queries,
        }
