"""Search over the index.

Full-text search (SQLite FTS5, accent- and case-insensitive, BM25 ranking). Words are folded to a simple
stem and matched as a prefix, with language-specific rules (``LORE_SEARCH_LANGUAGE``: ``en`` or ``pt``).
Semantic search sits behind ``EmbeddingProvider``; while none is configured, ``mode=hybrid`` falls back to
full-text and the response says so.
"""

import re
import sqlite3
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Protocol

from lore.categories import normalize
from lore.db import json_load

# Highlight markers in excerpts: control characters, so the UI never needs HTML.
MARK_START = "\x02"
MARK_END = "\x03"
QUERY_LIMIT = 200
LANGUAGES = ("en", "pt")

_TOKEN = re.compile(r'"([^"]+)"|([\w]+(?:[-_./:][\w]+)*)', re.UNICODE)
_STOP_WORDS = {
    "en": {
        "a", "an", "and", "or", "the", "of", "to", "in", "on", "for", "with", "by", "at", "from", "as", "is",
        "are", "be", "it", "its", "this", "that", "into", "per", "via",
    },
    "pt": {
        "a", "o", "as", "os", "e", "de", "da", "do", "das", "dos", "em", "no", "na", "nos", "nas",
        "um", "uma", "para", "por", "com", "que", "se", "ao", "aos", "ou", "the", "of",
    },
}

# Portuguese: plural, "-ão", "-ção" and "-mento" fold to the same root
# ("permissão"/"permissões" → "permiss"; "expiração" → "expira", which finds "expira" and "expirar").
_PT_PLURAL_SUFFIXES = ("coes", "cao", "mentos", "mento", "oes", "aes", "ais", "eis", "ao", "es", "s")
_PT_VERB_SUFFIXES = ("ar", "er", "ir")
_EN_KEEP_ENDINGS = ("ss", "us", "is")
_EN_ES_AFTER = ("sh", "ch", "x", "z", "ss")


def stem(word: str, language: str = "en") -> str:
    """Simple stem used for prefix search (accent-free, lowercase). Acronyms and codes stay as they are."""
    base = normalize(word)
    if not base.isalpha():
        return word
    if language == "pt":
        return _stem_pt(base)
    return _stem_en(base)


def _stem_pt(base: str) -> str:
    if len(base) < 5:
        return base
    for suffix in _PT_PLURAL_SUFFIXES:
        if base.endswith(suffix) and len(base) - len(suffix) >= 4:
            return base[: -len(suffix)]
    for suffix in _PT_VERB_SUFFIXES:
        if base.endswith(suffix) and len(base) - len(suffix) >= 5:
            return base[: -len(suffix)]
    return base


def _stem_en(base: str) -> str:
    """Conservative English folding; at most one rule applies and at least 3 letters remain.

    -ies → -y (policies → policy) · -sses → -ss (classes → class) · -shes/-ches/-xes/-zes → drop "es"
    (boxes → box) · -s → drop, except after s/u/i (tokens → token; status, analysis stay) ·
    -ation(s) → drop when 4+ letters remain (expiration → expir, finds "expires") ·
    -ing / -ed → drop when 4+ letters remain, undoubling a final consonant (indexing → index,
    stopped → stop).
    """
    if len(base) < 4:
        return base
    for suffix in ("ations", "ation"):
        if base.endswith(suffix) and len(base) - len(suffix) >= 4:
            return base[: -len(suffix)]
    if base.endswith("ies") and len(base) >= 5:
        return base[:-3] + "y"
    if base.endswith("sses"):
        return base[:-2]
    if base.endswith("es") and base[:-2].endswith(_EN_ES_AFTER) and len(base) - 2 >= 3:
        return base[:-2]
    if base.endswith("s") and not base.endswith(_EN_KEEP_ENDINGS):
        return base[:-1]
    for suffix in ("ing", "ed"):
        if base.endswith(suffix) and len(base) - len(suffix) >= 4:
            root = base[: -len(suffix)]
            if len(root) >= 4 and root[-1] == root[-2] and root[-1] not in "lsz" and root[-1] not in "aeiou":
                root = root[:-1]
            return root
    return base


def prefix(word: str, language: str = "en") -> str:
    """The prefix searched for a word: its stem, plus one English adjustment — a final "y" is dropped so
    that "policy" (and "policies" → "policy") also find "policies"."""
    root = stem(word, language)
    if language == "en" and root.isalpha() and root.endswith("y") and len(root) >= 5:
        return root[:-1]
    return root


class InvalidQuery(ValueError):
    pass


class EmbeddingProvider(Protocol):
    """Contract for semantic search (e.g. Ollama + bge-m3). None is configured yet."""

    model: str
    dimension: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class Query:
    expression: str
    terms: list[str]


def build_query(text: str, mode: str = "all", language: str = "en") -> Query:
    """Turns the typed text into a safe FTS5 expression.

    Every word becomes a quoted term (nothing the user types is interpreted as an FTS5 operator). Words with
    3+ letters match by their stem as a prefix ("permissions" → "permission"* → permission, permissions).
    Text in double quotes becomes an exact phrase.
    """
    text = (text or "").strip()[:QUERY_LIMIT]
    parts: list[tuple[str, bool]] = []  # (text, is phrase)
    for phrase, word in _TOKEN.findall(text):
        if re.search(r"\w", phrase):
            parts.append((phrase.strip(), True))
        elif word:
            parts.append((word, False))
    if not parts:
        raise InvalidQuery("Type at least one word to search.")

    # A stop word is dropped only when typed in lowercase: "OR" (an acronym) stays, "or" goes.
    stop_words = _STOP_WORDS.get(language, _STOP_WORDS["en"])
    useful = [p for p in parts if p[1] or p[0].isupper() or p[0].lower() not in stop_words]
    parts = useful or parts

    terms = []
    for item, phrase in parts:
        if phrase or len(item) < 3:
            terms.append(f'"{item}"')
        else:
            terms.append(f'"{prefix(item, language)}"*')
    joiner = " AND " if mode == "all" else " OR "
    return Query(expression=joiner.join(terms), terms=[t for t, _ in parts])


def search(
    conn: sqlite3.Connection,
    text: str,
    *,
    source: str | None = None,
    category: str | None = None,
    limit: int = 20,
    per_document: int = 3,
    forced_mode: str | None = None,
    sources: Collection[str] | None = None,
    language: str = "en",
) -> dict[str, Any]:
    """``forced_mode="any"`` searches for any of the words (the caller filters by coverage).
    ``sources`` restricts the search to these ids already in SQL (the MCP domain wall): what is left out
    neither takes a result slot nor counts in the totals."""
    mode = forced_mode or "all"
    query = build_query(text, mode, language)
    rows = _execute(conn, query.expression, source, category, sources)
    if not rows and mode == "all" and len(query.terms) > 1:
        query = build_query(text, "any", language)
        mode = "any"
        rows = _execute(conn, query.expression, source, category, sources)

    results: list[dict[str, Any]] = []
    per_doc: dict[int, int] = {}
    documents: set[int] = set()
    for row in rows:
        doc_id = int(row["document_id"])
        documents.add(doc_id)
        if per_doc.get(doc_id, 0) >= per_document:
            continue
        per_doc[doc_id] = per_doc.get(doc_id, 0) + 1
        if len(results) >= limit:
            continue
        results.append(
            {
                "chunk_id": int(row["chunk_id"]),
                "chunk_ordinal": int(row["ordinal"]),
                "document_id": doc_id,
                "document_title": row["document_title"],
                "source_id": row["source_id"],
                "path": row["path"],
                "category": row["category"],
                "title": row["title"],
                "section_path": json_load(row["section_path"], []),
                "line_start": int(row["line_start"]),
                "line_end": int(row["line_end"]),
                "excerpt": row["excerpt"],
                "score": -float(row["score"]),  # not rounded: a very common term gives a tiny value
            }
        )
    return {
        "query": text,
        "expression": query.expression,
        "mode": mode,
        "total_chunks": len(rows),
        "total_documents": len(documents),
        "results": results,
        "markers": {"start": MARK_START, "end": MARK_END},
    }


def _execute(
    conn: sqlite3.Connection,
    expression: str,
    source: str | None,
    category: str | None,
    sources: Collection[str] | None = None,
) -> list[sqlite3.Row]:
    filters = ["chunk_fts MATCH ?", "d.status != 'removed'"]
    params: list[Any] = [expression]
    if source:
        filters.append("d.source_id = ?")
        params.append(source)
    if sources is not None:
        filters.append(f"d.source_id IN ({', '.join('?' * len(sources)) or 'NULL'})")
        params.extend(sources)
    if category:
        filters.append(
            "EXISTS (SELECT 1 FROM json_each(d.categories) j WHERE j.value = ? OR j.value LIKE ? ESCAPE '\\')"
        )
        params.extend([category, escape_like(category) + "/%"])
    sql = f"""
        SELECT c.id AS chunk_id, c.ordinal, c.document_id, c.title, c.section_path,
               c.line_start, c.line_end,
               d.title AS document_title, d.source_id, d.path, d.category,
               bm25(chunk_fts, 4.0, 3.0, 1.0) AS score,
               snippet(chunk_fts, 2, char(2), char(3), '…', 28) AS excerpt
        FROM chunk_fts
        JOIN chunk c ON c.id = chunk_fts.rowid
        JOIN document d ON d.id = c.document_id
        WHERE {' AND '.join(filters)}
        ORDER BY score
        LIMIT 300
    """
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as error:  # should not happen: the expression is built here
        raise InvalidQuery(f"Query not accepted by the index: {error}") from error


def escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def relative(score: float, best: float) -> float:
    """Score relative to the best result (0–1). With nothing to tell apart (all ~0), everything is 1."""
    if best <= 1e-9:
        return 1.0
    return max(0.0, min(1.0, score / best))


def coverage(terms: list[str], text: str, language: str = "en") -> float:
    """Fraction of the query terms present in the text (accent-free; words by their stem prefix)."""
    if not terms:
        return 1.0
    target = normalize(text)
    present = 0
    for term in terms:
        key = normalize(term) if " " in term or not term.isalpha() else prefix(term, language)
        if re.search(r"(?<!\w)" + re.escape(normalize(key)), target):
            present += 1
    return present / len(terms)


def semantic_status() -> dict[str, Any]:
    return {"available": False, "reason": "Embeddings are not configured; search is full-text (FTS5)."}
