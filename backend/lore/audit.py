"""Audit log (append-only; the database refuses updates and deletes)."""

import sqlite3
from typing import Any

from lore.db import json_dump, json_load, now


def record(
    conn: sqlite3.Connection,
    action: str,
    origin: str,
    details: dict[str, Any] | None = None,
    ip: str | None = None,
    agent: str | None = None,
) -> int:
    cursor = conn.execute(
        "INSERT INTO audit (at, action, origin, ip, agent, details) VALUES (?, ?, ?, ?, ?, ?)",
        (now(), action, origin, ip, (agent or "")[:300] or None, json_dump(details or {})),
    )
    return int(cursor.lastrowid or 0)


def list_entries(conn: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, at, action, origin, ip, agent, details FROM audit ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [{**dict(row), "details": json_load(row["details"], {})} for row in rows]
