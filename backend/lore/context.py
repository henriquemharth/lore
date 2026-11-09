"""Context Builder: assembles a SMALL, traceable context package for a task.

It uses the existing search (``search.search``) and does not re-read the .md files. It prefers a few very
relevant chunks: it cuts by minimum relative score, number of chunks, number of documents, size of each chunk
and total size — and says what was left out instead of sending it.

Every returned item states its origin (document, path, heading, chunk, lines, score).
"""

import math
import re
import sqlite3
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

from lore.db import json_load
from lore.decisions import list_decisions, relevant
from lore.search import InvalidQuery, build_query, coverage, relative, search
from lore.sources import DecisionRules

# Words that describe the TASK rather than the subject: removed before searching.
_TASK_WORDS = {
    "en": {
        "change", "modify", "alter", "create", "implement", "adjust", "fix", "add", "remove", "improve", "make",
        "refactor", "review", "update", "include", "delete", "want", "need", "needs", "how", "where", "what",
        "which", "new", "feature", "task", "about", "should", "must", "let", "lets", "please", "we", "i",
        "the", "a", "an", "to", "of", "in", "for", "and", "or",
    },
    "pt": {
        "modificar", "alterar", "mudar", "criar", "implementar", "ajustar", "corrigir", "adicionar",
        "remover", "melhorar", "fazer", "refatorar", "revisar", "atualizar", "incluir", "excluir",
        "quero", "preciso", "como", "onde", "qual", "quais", "sistema", "nova", "novo", "funcionalidade",
        "tarefa", "sobre", "precisa", "deve", "devemos", "vamos",
    },
}
_WORD = re.compile(r"[\w-]+", re.UNICODE)
# Sources whose id starts with this prefix are AI memories (e.g. "memories", "memories-claude").
MEMORY_SOURCE_PREFIX = "memor"


@dataclass(frozen=True)
class Limits:
    max_chunks: int = 8
    max_chars_per_chunk: int = 1200
    max_chars_total: int = 12_000
    max_documents: int = 6
    min_score: float = 0.3  # score relative to the best result (0–1)
    max_entities: int = 10
    max_decisions: int = 8
    # Project documentation comes before AI memory when relevance is similar:
    # the score of memory chunks is multiplied by this weight.
    memory_weight: float = 0.8
    # With several words, a chunk must contain MORE than half of them (avoids context that only matches one
    # loose word when no chunk has all of them).
    min_coverage: float = 0.5


def task_terms(task: str, language: str = "en") -> str:
    task_words = _TASK_WORDS.get(language, _TASK_WORDS["en"])
    words = [w for w in _WORD.findall(task or "") if w.lower() not in task_words]
    return " ".join(words) or (task or "")


def estimate_tokens(characters: int) -> int:
    """Rough estimate (≈ 4 characters per token in technical text)."""
    return math.ceil(characters / 4)


def build(
    conn: sqlite3.Connection,
    task: str,
    limits: Limits,
    blocked: Callable[[str, str], bool] | None = None,
    visible_sources: Collection[str] | None = None,
    *,
    rules: DecisionRules | None = None,
    language: str = "en",
    mcp_memory_source: str | None = None,
) -> dict[str, Any]:
    """``mcp_memory_source``: id of the source that holds the memories written through MCP (unverified)."""

    def is_memory(source_id: str) -> bool:
        return source_id.startswith(MEMORY_SOURCE_PREFIX) or source_id == mcp_memory_source

    def is_mcp_memory(source: str) -> bool:
        return mcp_memory_source is not None and source.startswith(mcp_memory_source + ":")

    query = task_terms(task, language)
    try:
        result = search(
            conn, query, limit=100, per_document=3, forced_mode="any", sources=visible_sources, language=language
        )
    except InvalidQuery as error:
        raise InvalidQuery(f"The task has no searchable words: {error}") from error

    candidates = result["results"]
    terms = build_query(query, language=language).terms
    omitted = {
        "by_privacy": 0,
        "below_min_score": 0,
        "low_term_coverage": 0,
        "document_limit": 0,
        "chunk_limit": 0,
        "size_limit": 0,
        # the search already returns at most 3 chunks per document; the others count here
        "other_chunks_same_documents": max(0, result["total_chunks"] - len(candidates)),
    }
    allowed = []
    for r in candidates:
        if blocked and blocked(r["source_id"], r["path"]):
            omitted["by_privacy"] += 1
            continue
        weight = limits.memory_weight if is_memory(r["source_id"]) else 1.0
        allowed.append({**r, "score": r["score"] * weight})
    allowed.sort(key=lambda r: -r["score"])
    best = max((r["score"] for r in allowed), default=0.0)

    chosen: list[dict[str, Any]] = []
    documents: dict[int, dict[str, Any]] = {}
    total_chars = 0
    for r in allowed:
        rel = relative(r["score"], best)
        if rel < limits.min_score:
            omitted["below_min_score"] += 1
            continue
        if r["document_id"] not in documents and len(documents) >= limits.max_documents:
            omitted["document_limit"] += 1
            continue
        if len(chosen) >= limits.max_chunks:
            omitted["chunk_limit"] += 1
            continue
        row = conn.execute(
            "SELECT id, text, title, section_path, line_start, line_end FROM chunk WHERE id = ?", (r["chunk_id"],)
        ).fetchone()
        text = row["text"]
        chunk_coverage = coverage(terms, f"{r['document_title'] or ''} {r['path']} {row['title']} {text}", language)
        if len(terms) > 1 and chunk_coverage <= limits.min_coverage:
            omitted["low_term_coverage"] += 1
            continue
        truncated = len(text) > limits.max_chars_per_chunk
        content = text[: limits.max_chars_per_chunk].rstrip() + ("…" if truncated else "")
        if total_chars + len(content) > limits.max_chars_total:
            omitted["size_limit"] += 1
            continue
        total_chars += len(content)
        chosen.append(
            {
                "content": content,
                "truncated": truncated,
                "source": f"{r['source_id']}:{r['path']}",
                "heading": " › ".join(json_load(row["section_path"], [])) or row["title"],
                "document_id": r["document_id"],
                "chunk_id": r["chunk_id"],
                "chunk_index": r["chunk_ordinal"],
                "lines": [row["line_start"], row["line_end"]],
                "score": round(rel, 3),
            }
        )
        documents.setdefault(r["document_id"], {"source_id": r["source_id"], "path": r["path"]})

    # Documents (sources) with minimal metadata and their full size, to measure the savings.
    sources = []
    document_chars = 0
    for document_id in documents:
        d = conn.execute(
            "SELECT id, source_id, path, title, category, category_confidence, indexed_at, "
            "LENGTH(content) AS size FROM document WHERE id = ?",
            (document_id,),
        ).fetchone()
        document_chars += int(d["size"] or 0)
        sources.append(
            {
                "document_id": int(d["id"]),
                "source": f"{d['source_id']}:{d['path']}",
                "title": d["title"],
                "category": d["category"],
                "category_confidence": d["category_confidence"],
                "is_memory": is_memory(d["source_id"]),
            }
        )

    # Decisions: those that are one of the chosen documents and those cited in the chosen chunks.
    all_decisions = {d["code"]: d for d in list_decisions(conn, blocked, rules)}
    by_doc = {d["document_id"]: d for d in all_decisions.values()}
    decisions: dict[str, dict[str, Any]] = {}
    entities: dict[tuple[str, str], dict[str, Any]] = {}
    for item in chosen:
        own = by_doc.get(item["document_id"])
        if own:
            decisions.setdefault(own["code"], _decision_summary(own, "it is one of the context documents"))
        for mc in conn.execute(
            "SELECT type, key, line FROM mention WHERE document_id = ? AND chunk_ordinal = ? ORDER BY line",
            (item["document_id"], item["chunk_index"]),
        ):
            if mc["type"] == "decision":
                target = all_decisions.get(mc["key"])
                if target:
                    decisions.setdefault(
                        target["code"], _decision_summary(target, f"cited in {item['source']} line {mc['line']}")
                    )
            else:
                key = (mc["type"], mc["key"])
                if key not in entities:
                    entities[key] = {"type": mc["type"], "key": mc["key"], "source": item["source"], "line": mc["line"]}

    # Decisions related to the subject even when their text did not make it into the chunks (summary only).
    related = relevant(
        conn, query, limit=limits.max_decisions, blocked=blocked, sources=visible_sources, rules=rules, language=language
    )
    for d in related["decisions"]:
        decisions.setdefault(d["code"], _decision_summary(d, d.get("reason", "related to the search")))

    memories = [i for i in chosen if is_memory(i["source"].split(":", 1)[0])]
    total_omitted = sum(omitted.values())
    warnings = ["Full-text search only (embeddings not configured yet): words different from the text are not found."]
    if memories:
        warnings.append("AI memories may be outdated; check them against more recent documents and decisions.")
    if any(is_mcp_memory(m["source"]) for m in memories):
        warnings.append(
            "Some memories were written by an AI through MCP and nobody has verified them; treat them as hints, not facts."
        )
    if any(d["state"] != "active" for d in decisions.values()):
        warnings.append("Some decisions are not active (superseded, deprecated, proposed or without a declared status).")
    if any(d["cited_by_later_decisions"] for d in decisions.values()):
        warnings.append("Some decisions are cited by later decisions; check whether they still hold.")

    return {
        "task": task,
        "search_terms": terms,
        "search_mode": result["mode"],
        "chunks": chosen,
        "sources": sources,
        "decisions": list(decisions.values())[: limits.max_decisions],
        "memories": [
            {
                "source": m["source"],
                "chunk_id": m["chunk_id"],
                "heading": m["heading"],
                "verified": False if is_mcp_memory(m["source"]) else None,
            }
            for m in memories
        ],
        "entities": list(entities.values())[: limits.max_entities],
        "returned": {
            "chunks": len(chosen),
            "documents": len(documents),
            "characters": total_chars,
            "approx_tokens": estimate_tokens(total_chars),
        },
        "available": {"matching_chunks": result["total_chunks"], "matching_documents": result["total_documents"]},
        "omitted": omitted,
        "more_available": total_omitted > 0,
        "note": (
            f"{total_omitted} relevant chunk(s) were left out by the limits; use lore_search or "
            "lore_get_document (with chunks) to see more."
            if total_omitted
            else None
        ),
        "savings": {
            "full_documents_characters": document_chars,
            "approx_tokens_avoided": max(0, estimate_tokens(document_chars) - estimate_tokens(total_chars)),
            "basis": "full size of the documents the chunks came from, minus what was returned (estimate)",
        },
        "warnings": warnings,
    }


def _decision_summary(d: dict[str, Any], reason: str) -> dict[str, Any]:
    summary = {
        "code": d["code"],
        "title": d["title"],
        "state": d["state"],
        "date": d["date"],
        "document_id": d["document_id"],
        "source": f"{d['source_id']}:{d['path']}",
        "why": reason,
        "superseded_by": [s["code"] for s in d["superseded_by"]],
        "cited_by_later_decisions": [c["code"] for c in d["cited_by_later_decisions"]],
    }
    if d["state"] != "active":
        summary["declared_status"] = (d["declared_status"] or "no Status line in the document")[:160]
    return summary
