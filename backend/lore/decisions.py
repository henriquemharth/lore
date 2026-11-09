"""Decisions (decision records: ``DEC-001``, ``ADR-0001``, ``adr/0001-title.md``…): state, date and the
relations to other decisions that are WRITTEN down.

The declared status comes from, in order: a ``- **Status:** …`` list at the top, the frontmatter ``status``,
a ``Status: …`` line at the top, or the first line of a ``## Status`` section (Nygard/MADR style).

State, from what is written — never guessed:
- ``superseded``: the status says "superseded"/"replaced" (pt: "substituída/superada"), OR another decision
  writes "supersedes / replaces / overrides ADR-0003" (pt: "substitui / revoga / supera"), OR the decision
  itself writes "superseded by ADR-0005" (the evidence goes along);
- ``deprecated``: the status says deprecated, rejected, withdrawn, retired, cancelled, abandoned, obsolete,
  revoked (pt: retirada, revogada, cancelada, abandonada, obsoleta, rejeitada…);
- ``active``: the status says accepted/approved/adopted/active (pt: aceita/publicada/vigente/aprovada);
- ``proposed``: the status says proposed/draft/under review (pt: proposta/rascunho/em análise);
- ``undeclared``: there is no status, or it says none of the above (e.g. "Needs verification").

A LATER decision (same prefix, higher number) that only cites an earlier one (without saying it supersedes it)
shows up in ``cited_by_later_decisions`` as something to check — it does not change the state.
"""

import re
import sqlite3
from collections.abc import Collection
from functools import lru_cache
from typing import Any

from lore.categories import normalize
from lore.db import json_load
from lore.search import InvalidQuery, build_query, coverage, relative, search
from lore.sources import DecisionRules, decision_number, decision_prefix

STATES = ("active", "proposed", "superseded", "deprecated", "undeclared")

_SUPERSEDES_VERBS = (
    r"supersed(?:es|ing)|replac(?:es|ing)|overrid(?:es|ing)|revok(?:es|ing)"
    r"|substitu(?:i|em|indo)|revog(?:a|am|ando)|super(?:a|am|ando)"
)
_SUPERSEDED_BY_VERBS = (
    r"(?:superseded|replaced|overridden|obsoleted)\s+by|substitu[íi]d[ao]s?\s+pel[ao]s?|superad[ao]s?\s+pel[ao]s?"
)
_FILLER = (
    r"(?:\s*:)?\s+(?:(?:the|a|as|o|os|à|ao)\s+)?(?:(?:decision|decisão|decisao|adr)\s+)?\[?"
)
_SUPERSEDED = re.compile(r"\bsuperseded\b|\breplaced\b|substitu[íi]d\w*|superad\w*", re.IGNORECASE)
_DEPRECATED = re.compile(
    r"\bdeprecated\b|\brejected\b|\bwithdrawn\b|\bretired\b|\bcancell?ed\b|\babandoned\b|\bobsolete\b|\brevoked\b"
    r"|retirad\w*|revogad\w*|cancelad\w*|abandonad\w*|obsolet\w*|descontinuad\w*|rejeitad\w*|sem efeito",
    re.IGNORECASE,
)
_ACTIVE = re.compile(
    r"\baccepted\b|\bapproved\b|\badopted\b|\bactive\b|\bin effect\b"
    r"|\baceit\w*|publicad\w*|vigente|em vigor|aprovad\w*",
    re.IGNORECASE,
)
_PROPOSED = re.compile(
    r"\bproposed\b|\bdraft\b|\bunder review\b|\bin review\b|\bpropost\w*|rascunho|em an[aá]lise", re.IGNORECASE
)
_MARKUP = re.compile(r"[*`_]+")
_KEY_LINE = re.compile(
    r"^\s{0,3}(?:[-*+]\s+)?(?:\*\*|__)?(status|estado|date|data)(?:\*\*|__)?\s*:\s*(?:\*\*|__)?\s*(.+?)\s*$",
    re.IGNORECASE,
)
_STATUS_KEYS = ("status", "estado")
_DATE_KEYS = ("date", "data")
_STATUS_HEADINGS = ("status", "estado", "situacao")


@lru_cache(maxsize=16)
def _supersedes(patterns: tuple[str, ...]) -> re.Pattern[str]:
    codes = "|".join(f"(?:{p})" for p in patterns)
    return re.compile(rf"\b(?:{_SUPERSEDES_VERBS}){_FILLER}({codes})", re.IGNORECASE)


@lru_cache(maxsize=16)
def _superseded_by(patterns: tuple[str, ...]) -> re.Pattern[str]:
    codes = "|".join(f"(?:{p})" for p in patterns)
    return re.compile(rf"\b(?:{_SUPERSEDED_BY_VERBS}){_FILLER}({codes})", re.IGNORECASE)


def list_decisions(
    conn: sqlite3.Connection, blocked: Any = None, rules: DecisionRules | None = None
) -> list[dict[str, Any]]:
    """All indexed decisions, by number. ``blocked(source_id, path)`` filters privacy and other domains."""
    rules = rules or DecisionRules()
    decisions: dict[str, dict[str, Any]] = {}
    for row in conn.execute("SELECT id, source_id, path FROM document WHERE status != 'removed' ORDER BY id"):
        code = rules.code_from_path(row["path"])
        if not code or code in decisions or (blocked and blocked(row["source_id"], row["path"])):
            continue
        d = conn.execute(
            "SELECT id, source_id, path, title, metadata, frontmatter, headings, content, body_start, indexed_at "
            "FROM document WHERE id = ?",
            (row["id"],),
        ).fetchone()
        status_text, date = _declared(d)
        title = re.sub(rf"^{re.escape(code)}\s*[—–:.-]?\s*", "", d["title"] or code, flags=re.IGNORECASE)
        title = re.sub(r"^\d+[.:]\s+", "", title) or code
        decisions[code] = {
            "code": code,
            "number": decision_number(code),
            "title": title,
            "date": date,
            "declared_status": status_text,
            "state": _state(status_text),
            "superseded_by": [],
            "cited_by_later_decisions": [],
            "document_id": int(d["id"]),
            "source_id": d["source_id"],
            "path": d["path"],
            "indexed_at": d["indexed_at"],
        }
        if decisions[code]["state"] == "superseded":
            for target in rules.find_in_text(status_text or ""):
                if target != code and all(s["code"] != target for s in decisions[code]["superseded_by"]):
                    decisions[code]["superseded_by"].append(
                        {
                            "code": target,
                            "document_id": int(d["id"]),
                            "path": d["path"],
                            "line": None,
                            "excerpt": f"Status of {code}: {status_text[:200]}",
                        }
                    )

    by_document = {d["document_id"]: d for d in decisions.values()}
    supersedes = _supersedes(rules.patterns)
    superseded_by = _superseded_by(rules.patterns)
    for mc in conn.execute(
        "SELECT document_id, key, line, excerpt FROM mention WHERE type = 'decision' ORDER BY document_id, line"
    ):
        origin = by_document.get(int(mc["document_id"]))
        target = decisions.get(mc["key"])
        if origin is None or target is None or origin["code"] == target["code"]:
            continue
        evidence = {
            "code": origin["code"],
            "document_id": origin["document_id"],
            "path": origin["path"],
            "line": int(mc["line"]),
            "excerpt": mc["excerpt"],
        }
        if any(m.group(1).upper() == target["code"] for m in supersedes.finditer(mc["excerpt"])):
            target["state"] = "superseded"
            if all(s["code"] != origin["code"] for s in target["superseded_by"]):
                target["superseded_by"].append(evidence)
        elif any(m.group(1).upper() == target["code"] for m in superseded_by.finditer(mc["excerpt"])):
            origin["state"] = "superseded"
            if all(s["code"] != target["code"] for s in origin["superseded_by"]):
                origin["superseded_by"].append({**evidence, "code": target["code"]})
        elif (
            decision_prefix(origin["code"]) == decision_prefix(target["code"])
            and origin["number"] > target["number"]
            and all(c["code"] != origin["code"] for c in target["cited_by_later_decisions"])
        ):
            target["cited_by_later_decisions"].append(evidence)
    return sorted(decisions.values(), key=lambda d: (d["number"], d["code"]))


def relevant(
    conn: sqlite3.Connection,
    query: str | None,
    *,
    limit: int = 10,
    blocked: Any = None,
    min_coverage: float = 0.5,
    sources: Collection[str] | None = None,
    rules: DecisionRules | None = None,
    language: str = "en",
) -> dict[str, Any]:
    """Decisions related to the subject: those matching the search and those cited in matching chunks."""
    everything = list_decisions(conn, blocked, rules)
    if not query or not query.strip():
        return {"query": query, "decisions": everything[:limit], "total": len(everything), "criteria": "all, by number"}
    by_document = {d["document_id"]: d for d in everything}
    by_code = {d["code"]: d for d in everything}
    try:
        result = search(conn, query, limit=100, per_document=3, forced_mode="any", sources=sources, language=language)
    except InvalidQuery:
        return {"query": query, "decisions": [], "total": 0, "criteria": "empty query"}
    terms = build_query(query, language=language).terms

    def covers(r: dict[str, Any]) -> bool:
        if len(terms) <= 1:
            return True
        row = conn.execute("SELECT text FROM chunk WHERE id = ?", (r["chunk_id"],)).fetchone()
        target = f"{r['document_title'] or ''} {r['path']} {r['title']} {row['text'] if row else ''}"
        return coverage(terms, target, language) > min_coverage

    results = [r for r in result["results"] if covers(r)]
    chosen: dict[str, dict[str, Any]] = {}
    best = max((r["score"] for r in results), default=0.0)
    for r in results:
        if blocked and blocked(r["source_id"], r["path"]):
            continue
        direct = by_document.get(r["document_id"])
        if direct and direct["code"] not in chosen:
            chosen[direct["code"]] = {
                **direct,
                "relevance": round(relative(r["score"], best), 3),
                "reason": f"the text of the decision matches the search (chunk “{r['title']}”)",
            }
    # Decisions cited in the (non-decision) chunks that matched the search.
    for r in results:
        if r["document_id"] in by_document or (blocked and blocked(r["source_id"], r["path"])):
            continue
        for mc in conn.execute(
            "SELECT key, line FROM mention WHERE type = 'decision' AND document_id = ? AND chunk_ordinal = ?",
            (r["document_id"], r["chunk_ordinal"]),
        ):
            target = by_code.get(mc["key"])
            if target and target["code"] not in chosen:
                chosen[target["code"]] = {
                    **target,
                    "relevance": round(0.5 * relative(r["score"], best), 3),
                    "reason": f"cited in {r['source_id']}:{r['path']} line {mc['line']}, in a chunk that matches the search",
                }
    ordered = sorted(chosen.values(), key=lambda d: (-d["relevance"], d["number"], d["code"]))
    return {
        "query": query,
        "decisions": ordered[:limit],
        "total": len(ordered),
        "criteria": "full-text search in the decisions + decisions cited in the matching chunks",
    }


def _declared(d: sqlite3.Row) -> tuple[str | None, str | None]:
    """(status, date) as written in the decision record."""
    metadata = {str(k).lower(): v for k, v in (json_load(d["metadata"], {}) or {}).items()}
    frontmatter = {str(k).lower(): v for k, v in (json_load(d["frontmatter"], {}) or {}).items()}
    lines = (d["content"] or "").split("\n")
    top = lines[int(d["body_start"] or 0) : int(d["body_start"] or 0) + 40]
    found: dict[str, str] = {}
    for line in top:
        m = _KEY_LINE.match(line)
        if m:
            found.setdefault(m.group(1).lower(), m.group(2))

    status = _first(
        *(metadata.get(k) for k in _STATUS_KEYS),
        *(frontmatter.get(k) for k in _STATUS_KEYS),
        *(found.get(k) for k in _STATUS_KEYS),
        _status_section(d, lines),
    )
    date = _first(
        *(metadata.get(k) for k in _DATE_KEYS),
        *(frontmatter.get(k) for k in _DATE_KEYS),
        *(found.get(k) for k in _DATE_KEYS),
    )
    return status, date


def _status_section(d: sqlite3.Row, lines: list[str]) -> str | None:
    headings = json_load(d["headings"], []) or []
    for index, h in enumerate(headings):
        if h.get("level", 1) < 2 or normalize(str(h.get("text", ""))).strip() not in _STATUS_HEADINGS:
            continue
        end = headings[index + 1]["line"] - 1 if index + 1 < len(headings) else len(lines)
        for line in lines[int(h["line"]) : end]:
            if line.strip():
                return line.strip().lstrip("-*+ ").strip()
    return None


def _first(*values: Any) -> str | None:
    for value in values:
        cleaned = _clean(value)
        if cleaned:
            return cleaned
    return None


def _state(status: str | None) -> str:
    if not status:
        return "undeclared"
    if _SUPERSEDED.search(status):
        return "superseded"
    if _DEPRECATED.search(status):
        return "deprecated"
    if _ACTIVE.search(status):
        return "active"
    if _PROPOSED.search(status):
        return "proposed"
    return "undeclared"


def _clean(text: Any) -> str | None:
    if not isinstance(text, (str, int, float)) or isinstance(text, bool):
        return None
    text = str(text)
    if not text.strip():
        return None
    return re.sub(r"\s+", " ", _MARKUP.sub("", text)).strip() or None
