"""Incremental indexing: scans the sources, compares by identity (hash) and only reprocesses what changed.

Guarantees:
- sources are only read (see ``scanner``); nothing is written outside lore's own database;
- a document that disappears from its source is marked ``removed`` — the row and its history stay;
- an unavailable source (missing folder, unknown ref) marks nothing as removed;
- every run that changes the index is recorded in ``run`` + ``run_item`` + ``audit``.
"""

import posixpath
import sqlite3
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from lore import audit, entities
from lore.categories import Assignment, Taxonomy
from lore.chunking import ChunkingParams, split
from lore.db import connect, json_dump, json_load, now, set_meta, transaction
from lore.parser import parse
from lore.privacy import Privacy
from lore.scanner import DiscoveredFile, Scan, load, scan
from lore.sources import Source, SourcesConfig

# Change it when the parser, chunking or what goes into the index changes: forces reprocessing everything.
# The decision rules of sources.yaml are added to it (changing them also reprocesses).
PROCESSOR_VERSION = "1"
ABANDONED_RUN = timedelta(minutes=10)

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class IndexingInProgress(RuntimeError):
    def __init__(self) -> None:
        super().__init__("An indexing run is already in progress.")


def _lock(db_path: Path) -> threading.Lock:
    key = str(Path(db_path).resolve())
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


@dataclass
class RunContext:
    origin: str  # api | cli | scheduled | test
    ip: str | None = None
    agent: str | None = None


@dataclass
class Change:
    type: str  # new | changed | restored | reprocess | removed | error
    source_id: str
    path: str
    source: Source | None = None
    scan: Scan | None = None
    file: DiscoveredFile | None = None
    document: sqlite3.Row | None = None
    detail: str | None = None


@dataclass
class Plan:
    changes: list[Change] = field(default_factory=list)
    discovered: int = 0
    unchanged: int = 0
    unavailable: list[str] = field(default_factory=list)
    scans: list[Scan] = field(default_factory=list)


@dataclass
class RunSummary:
    run_id: int | None
    recorded: bool
    origin: str
    full: bool
    status: str
    started_at: str
    finished_at: str
    duration_ms: int
    totals: dict[str, int]
    unavailable_sources: list[str]
    errors: list[dict[str, str]]

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def plan(
    conn: sqlite3.Connection,
    scans: list[Scan],
    sources: SourcesConfig,
    full: bool = False,
    processor_version: str = PROCESSOR_VERSION,
) -> Plan:
    """Compares the scan with the index. Writes nothing."""
    existing = {
        (row["source_id"], row["path"]): row
        for row in conn.execute(
            "SELECT id, source_id, path, identity, hash, status, error, error_kind, processor_version, version "
            "FROM document"
        )
    }
    result = Plan(scans=scans)
    seen: set[tuple[str, str]] = set()
    configured = {s.id for s in sources.sources}

    for s in scans:
        sid = s.source.id
        if not s.available:
            result.unavailable.append(sid)
            seen.update(key for key in existing if key[0] == sid)
            continue
        result.discovered += len(s.files)
        for file in s.files:
            key = (sid, file.path)
            seen.add(key)
            doc = existing.get(key)
            force = full or (doc is not None and doc["processor_version"] != processor_version)
            if doc is None:
                kind = "new"
            elif doc["status"] == "removed":
                kind = "restored"
            elif doc["identity"] != file.identity:
                kind = "changed"
            elif force or (doc["status"] == "error" and doc["error_kind"] == "read"):
                # readable again: reprocess even with the same content as the last good version
                kind = "reprocess"
            else:
                result.unchanged += 1
                continue
            result.changes.append(Change(kind, sid, file.path, s.source, s, file, doc))
        for path, message in s.errors:
            key = (sid, path)
            seen.add(key)
            doc = existing.get(key)
            if doc is not None and doc["status"] == "error" and doc["error"] == message and not full:
                result.unchanged += 1
                continue
            result.changes.append(Change("error", sid, path, s.source, s, None, doc, message))

    for key, doc in existing.items():
        if key in seen or doc["status"] == "removed":
            continue
        detail = None if key[0] in configured else "Source removed from sources.yaml."
        result.changes.append(Change("removed", key[0], key[1], document=doc, detail=detail))
    return result


class Indexer:
    def __init__(
        self,
        db_path: Path,
        sources: SourcesConfig,
        taxonomy: Taxonomy,
        params: ChunkingParams | None = None,
        privacy: Privacy | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.sources = sources
        self.taxonomy = taxonomy
        self.params = params or ChunkingParams()
        self.privacy = privacy or Privacy()
        self.processor_version = f"{PROCESSOR_VERSION}+{sources.decisions.fingerprint()}"

    # ------------------------------------------------------------------ reading

    def scan(self) -> list[Scan]:
        return scan(self.sources.sources, self.sources.max_bytes, self.privacy)

    def pending(self, conn: sqlite3.Connection) -> list[dict[str, str | None]]:
        """What changed in the sources since the last run (without indexing)."""
        result = plan(conn, self.scan(), self.sources, processor_version=self.processor_version)
        return [{"source_id": c.source_id, "path": c.path, "type": c.type, "detail": c.detail} for c in result.changes]

    # ------------------------------------------------------------------ writing (only in lore's database)

    def run(self, context: RunContext, full: bool = False) -> RunSummary:
        lock = _lock(self.db_path)
        if not lock.acquire(blocking=False):
            raise IndexingInProgress()
        try:
            return self._run(context, full)
        finally:
            lock.release()

    def _run(self, context: RunContext, full: bool) -> RunSummary:
        clock = time.monotonic()
        started_at = now()
        conn = connect(self.db_path)
        try:
            result = plan(conn, self.scan(), self.sources, full, self.processor_version)
            reclassify = self._reclassifications(conn, result)
            changed = bool(result.changes or reclassify)

            if context.origin == "scheduled" and not changed:
                with transaction(conn):
                    self._save_sources(conn, result)
                    set_meta(conn, "last_check", now())
                return RunSummary(
                    run_id=None,
                    recorded=False,
                    origin=context.origin,
                    full=full,
                    status="no_changes",
                    started_at=started_at,
                    finished_at=now(),
                    duration_ms=int((time.monotonic() - clock) * 1000),
                    totals=self._totals(conn, result, {}, 0),
                    unavailable_sources=result.unavailable,
                    errors=[],
                )

            with transaction(conn):
                self._refuse_concurrency(conn)
                run_id = int(
                    conn.execute(
                        "INSERT INTO run (origin, full, status, started_at) VALUES (?, ?, 'running', ?)",
                        (context.origin, int(full), started_at),
                    ).lastrowid
                    or 0
                )
                if context.origin != "scheduled":
                    audit.record(
                        conn, "reindex_requested", context.origin, {"run_id": run_id, "full": full}, context.ip, context.agent
                    )

            try:
                count: dict[str, int] = defaultdict(int)
                errors: list[dict[str, str]] = []
                for change in result.changes:
                    event = self._apply(conn, run_id, change)
                    count[event] += 1
                    if event == "error":
                        errors.append({"source_id": change.source_id, "path": change.path, "error": change.detail or ""})
                reclassified = self._apply_reclassifications(conn, run_id)
                self._resolve_links(conn)
                totals = self._totals(conn, result, count, reclassified)
                finished_at = now()
                with transaction(conn):
                    conn.execute(
                        "UPDATE run SET status = 'completed', finished_at = ?, totals = ? WHERE id = ?",
                        (finished_at, json_dump(totals), run_id),
                    )
                    self._save_sources(conn, result)
                    set_meta(conn, "last_run", str(run_id))
                    set_meta(conn, "last_check", finished_at)
                    audit.record(
                        conn,
                        "reindex_completed",
                        context.origin,
                        {
                            "run_id": run_id,
                            "full": full,
                            "totals": totals,
                            "unavailable_sources": result.unavailable,
                            "errors": errors[:50],
                        },
                        context.ip,
                        context.agent,
                    )
            except Exception as failure:
                with transaction(conn):
                    conn.execute(
                        "UPDATE run SET status = 'failed', finished_at = ?, error = ? WHERE id = ?",
                        (now(), str(failure)[:2000], run_id),
                    )
                    audit.record(
                        conn,
                        "reindex_failed",
                        context.origin,
                        {"run_id": run_id, "error": str(failure)[:2000]},
                        context.ip,
                        context.agent,
                    )
                raise

            return RunSummary(
                run_id=run_id,
                recorded=True,
                origin=context.origin,
                full=full,
                status="completed",
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=int((time.monotonic() - clock) * 1000),
                totals=totals,
                unavailable_sources=result.unavailable,
                errors=errors,
            )
        finally:
            conn.close()

    def _refuse_concurrency(self, conn: sqlite3.Connection) -> None:
        limit = (datetime.now(UTC) - ABANDONED_RUN).isoformat(timespec="seconds")
        conn.execute(
            "UPDATE run SET status = 'interrupted', finished_at = ?, "
            "error = 'No completion recorded (process ended in the middle of the run).' "
            "WHERE status = 'running' AND started_at < ?",
            (now(), limit),
        )
        if conn.execute("SELECT 1 FROM run WHERE status = 'running'").fetchone():
            raise IndexingInProgress()

    # ------------------------------------------------------------------ applying changes

    def _apply(self, conn: sqlite3.Connection, run_id: int, c: Change) -> str:
        if c.type == "removed":
            self._remove(conn, run_id, c)
            return "removed"
        if c.type == "error":
            self._record_error(conn, run_id, c, c.detail or "Read error.", "read")
            return "error"
        assert c.source is not None and c.file is not None and c.scan is not None
        try:
            file = load(c.source, c.file, c.scan.ref_commit)
            assert file.content is not None
            doc = parse(file.content, file.path)
            chunks = split(doc, self.params)
            assignments = self.taxonomy.classify(
                c.source_id, file.path, doc.title, [(h.text, h.line) for h in doc.headings], doc.frontmatter
            )
            mentions = entities.extract(doc, self.sources.decisions)
        except Exception as failure:  # one bad file does not bring the run down
            c.detail = f"Processing failed: {failure}"
            self._record_error(conn, run_id, c, c.detail, "processing")
            return "error"

        previous = c.document
        content_changed = previous is None or previous["hash"] != file.hash or c.type == "restored"
        version = (previous["version"] if previous else 0) + (1 if content_changed else 0)
        event = {"new": "new", "changed": "changed", "restored": "restored"}.get(c.type, "reprocessed")
        moment = now()

        with transaction(conn):
            values = {
                "name": file.name,
                "size": file.size,
                "hash": file.hash,
                "identity": file.identity,
                "modified_at": file.modified_at,
                "title": doc.title,
                **_category_columns(assignments),
                "frontmatter": json_dump(doc.frontmatter) if doc.frontmatter is not None else None,
                "metadata": json_dump(doc.metadata),
                "headings": json_dump([h.__dict__ for h in doc.headings]),
                "content": doc.text,
                "body_start": doc.body_start,
                "total_lines": len(doc.lines),
                "total_chunks": len(chunks),
                "warnings": json_dump(doc.warnings),
                "status": "indexed",
                "error": None,
                "error_kind": None,
                "version": version,
                "processor_version": self.processor_version,
                "indexed_at": moment,
                "removed_at": None,
            }
            document_id = self._save_document(conn, c, values, moment)
            self._clear_derived(conn, document_id)
            document_text = " ".join(p for p in (doc.title, file.name, file.path, doc.description or "") if p)
            for ch in chunks:
                chunk_id = conn.execute(
                    "INSERT INTO chunk (document_id, ordinal, title, section_path, sections, level, part, parts, "
                    "line_start, line_end, char_start, char_end, text, size, hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        document_id, ch.ordinal, ch.title, json_dump(ch.section_path), json_dump(ch.sections),
                        ch.level, ch.part, ch.parts, ch.line_start, ch.line_end, ch.char_start, ch.char_end,
                        ch.text, len(ch.text), ch.hash,
                    ),
                ).lastrowid
                section = " › ".join(dict.fromkeys([*ch.section_path, *ch.sections]))
                conn.execute(
                    "INSERT INTO chunk_fts (rowid, document, section, text) VALUES (?, ?, ?, ?)",
                    (chunk_id, document_text, section, ch.text),
                )
            conn.executemany(
                "INSERT INTO link (document_id, line, type, target, anchor, text) VALUES (?, ?, ?, ?, ?, ?)",
                [(document_id, lk.line, lk.type, lk.target, lk.anchor, lk.text) for lk in doc.links],
            )
            conn.executemany(
                "INSERT INTO mention (document_id, type, key, line, chunk_ordinal, excerpt, rule) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (document_id, m.type, m.key, m.line, _chunk_ordinal(chunks, m.line), m.excerpt, m.rule)
                    for m in mentions
                ],
            )
            if content_changed:
                conn.execute(
                    "INSERT INTO document_version (document_id, version, event, hash, size, modified_at, "
                    "run_id, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (document_id, version, event, file.hash, file.size, file.modified_at, run_id, moment),
                )
            self._item(conn, run_id, document_id, c, event, f"{len(chunks)} chunks")
        return event

    def _save_document(self, conn: sqlite3.Connection, c: Change, values: dict[str, Any], moment: str) -> int:
        if c.document is not None:
            document_id = int(c.document["id"])
            columns = ", ".join(f"{k} = ?" for k in values)
            conn.execute(f"UPDATE document SET {columns} WHERE id = ?", (*values.values(), document_id))
            return document_id
        values = {"source_id": c.source_id, "path": c.path, "discovered_at": moment, **values}
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        return int(conn.execute(f"INSERT INTO document ({columns}) VALUES ({marks})", tuple(values.values())).lastrowid or 0)

    def _clear_derived(self, conn: sqlite3.Connection, document_id: int) -> None:
        """Removes chunks, links and mentions (derived, rebuilt from the .md). Document and history stay."""
        conn.execute("DELETE FROM chunk_fts WHERE rowid IN (SELECT id FROM chunk WHERE document_id = ?)", (document_id,))
        conn.execute("DELETE FROM chunk WHERE document_id = ?", (document_id,))
        conn.execute("DELETE FROM link WHERE document_id = ?", (document_id,))
        conn.execute("DELETE FROM mention WHERE document_id = ?", (document_id,))
        conn.execute("UPDATE link SET target_document_id = NULL WHERE target_document_id = ?", (document_id,))

    def _record_error(self, conn: sqlite3.Connection, run_id: int, c: Change, message: str, kind: str) -> None:
        """Marks the error without deleting the last good version: old chunks stay searchable."""
        moment = now()
        with transaction(conn):
            file = c.file
            if c.document is not None:
                document_id = int(c.document["id"])
                conn.execute(
                    "UPDATE document SET status = 'error', error = ?, error_kind = ?, "
                    "identity = COALESCE(?, identity), processor_version = ? WHERE id = ?",
                    (message, kind, file.identity if file else None, self.processor_version, document_id),
                )
            else:
                document_id = self._save_document(
                    conn,
                    c,
                    {
                        "name": posixpath.basename(c.path),
                        "size": file.size if file else None,
                        "hash": file.hash if file else None,
                        "identity": file.identity if file else None,
                        "modified_at": file.modified_at if file else None,
                        "title": posixpath.splitext(posixpath.basename(c.path))[0],
                        "status": "error",
                        "error": message,
                        "error_kind": kind,
                        "processor_version": self.processor_version,
                    },
                    moment,
                )
            self._item(conn, run_id, document_id, c, "error", message)

    def _remove(self, conn: sqlite3.Connection, run_id: int, c: Change) -> None:
        assert c.document is not None
        document_id = int(c.document["id"])
        moment = now()
        with transaction(conn):
            conn.execute(
                "UPDATE document SET status = 'removed', removed_at = ?, error = NULL, error_kind = NULL, "
                "total_chunks = 0 WHERE id = ?",
                (moment, document_id),
            )
            self._clear_derived(conn, document_id)
            conn.execute(
                "INSERT INTO document_version (document_id, version, event, hash, run_id, recorded_at) "
                "VALUES (?, ?, 'removed', ?, ?, ?)",
                (document_id, c.document["version"], c.document["hash"], run_id, moment),
            )
            self._item(conn, run_id, document_id, c, "removed", c.detail or "File not found in the source.")

    def _item(
        self, conn: sqlite3.Connection, run_id: int, document_id: int | None, c: Change, event: str, detail: str | None
    ) -> None:
        conn.execute(
            "INSERT INTO run_item (run_id, document_id, source_id, path, event, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, document_id, c.source_id, c.path, event, detail),
        )

    # ------------------------------------------------------------------ classification and links

    def _reclassifications(self, conn: sqlite3.Connection, result: Plan) -> list[tuple[int, list[Assignment]]]:
        """Indexed documents whose classification changed (e.g. the taxonomy was edited)."""
        touched = {(c.source_id, c.path) for c in result.changes}
        out = []
        for d in conn.execute(
            "SELECT id, source_id, path, title, headings, frontmatter, categories_detail "
            "FROM document WHERE status = 'indexed'"
        ):
            if (d["source_id"], d["path"]) in touched:
                continue
            headings = [(h.get("text", ""), h.get("line")) for h in json_load(d["headings"], [])]
            assignments = self.taxonomy.classify(
                d["source_id"], d["path"], d["title"] or "", headings, json_load(d["frontmatter"])
            )
            if [a.as_dict() for a in assignments] != json_load(d["categories_detail"], []):
                out.append((int(d["id"]), assignments))
        return out

    def _apply_reclassifications(self, conn: sqlite3.Connection, run_id: int) -> int:
        changes = self._reclassifications(conn, Plan())
        if not changes:
            return 0
        with transaction(conn):
            for document_id, assignments in changes:
                row = conn.execute(
                    "SELECT source_id, path, category, category_confidence FROM document WHERE id = ?", (document_id,)
                ).fetchone()
                columns = _category_columns(assignments)
                conn.execute(
                    "UPDATE document SET category = ?, categories = ?, category_confidence = ?, categories_detail = ? "
                    "WHERE id = ?",
                    (*columns.values(), document_id),
                )
                new = assignments[0]
                conn.execute(
                    "INSERT INTO run_item (run_id, document_id, source_id, path, event, detail) "
                    "VALUES (?, ?, ?, ?, 'reclassified', ?)",
                    (
                        run_id, document_id, row["source_id"], row["path"],
                        f"{row['category']} ({row['category_confidence']}) → {new.category} ({new.confidence})",
                    ),
                )
        return len(changes)

    def _resolve_links(self, conn: sqlite3.Connection) -> None:
        documents = conn.execute("SELECT id, source_id, path FROM document WHERE status != 'removed'").fetchall()
        by_path = {(d["source_id"], d["path"].lower()): int(d["id"]) for d in documents}
        by_name: dict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
        for d in documents:
            by_name[(d["source_id"], posixpath.basename(d["path"]).lower())].append((d["path"], int(d["id"])))

        updates = []
        for lk in conn.execute(
            "SELECT l.id, l.type, l.target, l.target_document_id, d.source_id, d.path "
            "FROM link l JOIN document d ON d.id = l.document_id"
        ):
            target = resolve_link(lk["source_id"], lk["path"], lk["target"], lk["type"], by_path, by_name)
            if target != lk["target_document_id"]:
                updates.append((target, lk["id"]))
        if updates:
            with transaction(conn):
                conn.executemany("UPDATE link SET target_document_id = ? WHERE id = ?", updates)

    # ------------------------------------------------------------------ sources and totals

    def _save_sources(self, conn: sqlite3.Connection, result: Plan) -> None:
        moment = now()
        configured = set()
        for s in result.scans:
            src = s.source
            configured.add(src.id)
            conn.execute(
                "INSERT INTO source (id, name, type, root, ref, ref_commit, domain, available, warnings, checked_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET name = excluded.name, "
                "type = excluded.type, root = excluded.root, ref = excluded.ref, ref_commit = excluded.ref_commit, "
                "domain = excluded.domain, available = excluded.available, warnings = excluded.warnings, "
                "checked_at = excluded.checked_at",
                (
                    src.id, src.name, src.type, str(src.root), src.ref, s.ref_commit, src.domain, int(s.available),
                    json_dump(s.warnings), moment,
                ),
            )
        for row in conn.execute("SELECT id FROM source").fetchall():
            if row["id"] not in configured:
                conn.execute(
                    "UPDATE source SET available = 0, warnings = ?, checked_at = ? WHERE id = ?",
                    (json_dump(["Source removed from sources.yaml."]), moment, row["id"]),
                )

    def _totals(self, conn: sqlite3.Connection, result: Plan, count: dict[str, int], reclassified: int) -> dict[str, int]:
        documents = conn.execute("SELECT COUNT(*) FROM document WHERE status != 'removed'").fetchone()[0]
        chunks = conn.execute("SELECT COUNT(*) FROM chunk").fetchone()[0]
        return {
            "discovered": result.discovered,
            "new": count.get("new", 0),
            "changed": count.get("changed", 0),
            "reprocessed": count.get("reprocessed", 0),
            "restored": count.get("restored", 0),
            "removed": count.get("removed", 0),
            "errors": count.get("error", 0),
            "reclassified": reclassified,
            "unchanged": result.unchanged,
            "documents": int(documents),
            "chunks": int(chunks),
        }


def _category_columns(assignments: list[Assignment]) -> dict[str, str]:
    primary = assignments[0]
    return {
        "category": primary.category,
        "categories": json_dump([a.category for a in assignments]),
        "category_confidence": primary.confidence,
        "categories_detail": json_dump([a.as_dict() for a in assignments]),
    }


def _chunk_ordinal(chunks: list[Any], line: int) -> int | None:
    for c in chunks:
        if c.line_start <= line <= c.line_end:
            return int(c.ordinal)
    return None


def resolve_link(
    source_id: str,
    origin_path: str,
    target: str,
    kind: str,
    by_path: dict[tuple[str, str], int],
    by_name: dict[tuple[str, str], list[tuple[str, int]]],
) -> int | None:
    """Resolves a link target the way Obsidian does: relative, from the root, or by file name."""
    target = target.strip().replace("\\", "/")
    if not target:
        return None
    if not target.lower().endswith(".md"):
        target += ".md"
    folder = posixpath.dirname(origin_path)

    candidates = []
    if kind == "markdown" or target.startswith(("./", "../")):
        candidates.append(posixpath.normpath(posixpath.join(folder, target)))
    candidates.append(posixpath.normpath(target.lstrip("/")))
    candidates.append(posixpath.normpath(posixpath.join(folder, target)))
    for candidate in candidates:
        found = by_path.get((source_id, candidate.lower()))
        if found is not None:
            return found

    if "/" in target.removeprefix("./"):
        return None
    same = by_name.get((source_id, target.lower().removeprefix("./")), [])
    if not same:
        return None
    in_folder = [d for p, d in same if posixpath.dirname(p) == folder]
    if in_folder:
        return in_folder[0]
    return sorted(same, key=lambda x: (len(x[0]), x[0]))[0][1]
