"""Local index database (SQLite + FTS5).

The index is DERIVED from the .md files: documents, chunks, links and mentions can be rebuilt at any time
with a full reindex. ``audit``, ``run`` and ``document_version`` are the history — they only receive inserts
(triggers refuse UPDATE/DELETE where that matters).

A future version may move the same structure to PostgreSQL + pgvector (tsvector instead of FTS5 and an
embeddings table keyed by ``chunk.hash``). Since the index is derived, the switch is a reindex, not a data
migration.
"""

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

_SCHEMA_V1 = """
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE source (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('folder', 'git')),
    root TEXT NOT NULL,
    ref TEXT,
    ref_commit TEXT,
    domain TEXT,
    available INTEGER NOT NULL DEFAULT 0,
    warnings TEXT NOT NULL DEFAULT '[]',
    checked_at TEXT
);

CREATE TABLE document (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL,
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    size INTEGER,
    hash TEXT,
    identity TEXT,
    modified_at TEXT,
    title TEXT,
    category TEXT NOT NULL DEFAULT 'Uncategorized',
    categories TEXT NOT NULL DEFAULT '[]',
    category_confidence TEXT NOT NULL DEFAULT 'suggestion'
        CHECK (category_confidence IN ('explicit', 'suggestion')),
    categories_detail TEXT NOT NULL DEFAULT '[]',
    frontmatter TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    headings TEXT NOT NULL DEFAULT '[]',
    content TEXT,
    body_start INTEGER NOT NULL DEFAULT 0,
    total_lines INTEGER NOT NULL DEFAULT 0,
    total_chunks INTEGER NOT NULL DEFAULT 0,
    warnings TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK (status IN ('indexed', 'error', 'removed')),
    error TEXT,
    error_kind TEXT CHECK (error_kind IN ('read', 'processing')),
    version INTEGER NOT NULL DEFAULT 0,
    processor_version TEXT,
    discovered_at TEXT NOT NULL,
    indexed_at TEXT,
    removed_at TEXT,
    UNIQUE (source_id, path)
);
CREATE INDEX ix_document_status ON document (status);
CREATE INDEX ix_document_category ON document (category);

CREATE TABLE document_version (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES document (id),
    version INTEGER NOT NULL,
    event TEXT NOT NULL CHECK (event IN ('new', 'changed', 'removed', 'restored')),
    hash TEXT,
    size INTEGER,
    modified_at TEXT,
    run_id INTEGER REFERENCES run (id),
    recorded_at TEXT NOT NULL
);
CREATE INDEX ix_document_version_doc ON document_version (document_id);

CREATE TABLE chunk (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES document (id),
    ordinal INTEGER NOT NULL,
    title TEXT NOT NULL,
    section_path TEXT NOT NULL,
    sections TEXT NOT NULL,
    level INTEGER NOT NULL,
    part INTEGER NOT NULL,
    parts INTEGER NOT NULL,
    line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    char_start INTEGER NOT NULL,
    char_end INTEGER NOT NULL,
    text TEXT NOT NULL,
    size INTEGER NOT NULL,
    hash TEXT NOT NULL,
    UNIQUE (document_id, ordinal)
);

-- Full-text search: accent- and case-insensitive. rowid = chunk.id.
CREATE VIRTUAL TABLE chunk_fts USING fts5 (
    document, section, text,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TABLE link (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES document (id),
    line INTEGER NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('wikilink', 'embed', 'markdown')),
    target TEXT NOT NULL,
    anchor TEXT,
    text TEXT,
    target_document_id INTEGER REFERENCES document (id)
);
CREATE INDEX ix_link_document ON link (document_id);
CREATE INDEX ix_link_target ON link (target_document_id);

CREATE TABLE mention (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES document (id),
    type TEXT NOT NULL CHECK (type IN ('decision', 'commit', 'migration', 'hash', 'file')),
    key TEXT NOT NULL,
    line INTEGER NOT NULL,
    chunk_ordinal INTEGER,
    excerpt TEXT NOT NULL,
    rule TEXT NOT NULL
);
CREATE INDEX ix_mention_document ON mention (document_id);
CREATE INDEX ix_mention_key ON mention (type, key);

CREATE TABLE run (
    id INTEGER PRIMARY KEY,
    origin TEXT NOT NULL CHECK (origin IN ('api', 'cli', 'scheduled', 'test')),
    full INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'interrupted')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    totals TEXT NOT NULL DEFAULT '{}',
    error TEXT
);

CREATE TABLE run_item (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES run (id),
    document_id INTEGER REFERENCES document (id),
    source_id TEXT NOT NULL,
    path TEXT NOT NULL,
    event TEXT NOT NULL CHECK (event IN ('new', 'changed', 'reprocessed', 'removed', 'restored', 'error', 'reclassified')),
    detail TEXT
);
CREATE INDEX ix_run_item_run ON run_item (run_id);

CREATE TABLE audit (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    action TEXT NOT NULL,
    origin TEXT NOT NULL,
    ip TEXT,
    agent TEXT,
    details TEXT NOT NULL DEFAULT '{}'
);

CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'the audit log is append-only'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'the audit log is append-only'); END;
CREATE TRIGGER version_no_update BEFORE UPDATE ON document_version
BEGIN SELECT RAISE(ABORT, 'the version history is append-only'); END;
CREATE TRIGGER version_no_delete BEFORE DELETE ON document_version
BEGIN SELECT RAISE(ABORT, 'the version history is append-only'); END;
"""

_MIGRATIONS = {1: _SCHEMA_V1}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    new = not path.exists()
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA synchronous = NORMAL")
    if new:
        os.chmod(path, 0o600)  # the index holds a copy of the notes and memories
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"The database is at schema version {version}, newer than this installation ({SCHEMA_VERSION})."
        )
    for target in range(version + 1, SCHEMA_VERSION + 1):
        with transaction(conn):
            for statement in _statements(_MIGRATIONS[target]):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {target}")


def _statements(script: str) -> Iterator[str]:
    """Splits the script into complete statements (triggers contain inner ';')."""
    current: list[str] = []
    for line in script.splitlines():
        if line.strip().startswith("--") and not current:
            continue
        current.append(line)
        candidate = "\n".join(current).strip()
        if candidate and sqlite3.complete_statement(candidate):
            yield candidate
            current = []


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_load(text: str | None, default: Any = None) -> Any:
    if text is None:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
