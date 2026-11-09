"""Knowledge graph derived from the index.

Nodes: the root, the taxonomy categories, the documents, the decisions (decision-record documents) and the
entities mentioned in the text (commits, migrations, files, cited decisions that have no file).

Edges — only these four types, and EVERY edge carries traceable evidence:
- ``subcategory``: structure declared in taxonomy.yaml;
- ``category``: document → category, with confidence (explicit/suggestion) and the reason of the rule;
- ``link``: wikilink or Markdown link written in the document (line + excerpt);
- ``mentions``: identifier written in the document — decision code, hash, path (line + excerpt).

There are no edges by topic similarity or co-occurrence: if the text does not say it, there is no edge.
"""

import sqlite3
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from lore.categories import UNCATEGORIZED, Taxonomy, normalize
from lore.db import json_load
from lore.sources import DecisionRules

NODE_TYPES = ("root", "category", "document", "decision", "commit", "migration", "hash", "file")
EDGE_TYPES = ("subcategory", "category", "link", "mentions")
EVIDENCE_IN_SUMMARY = 3


@dataclass
class Graph:
    root: str
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: dict[str, dict[str, Any]] = field(default_factory=dict)
    adjacency: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def node(self, ident: str, type: str, label: str, **extras: Any) -> dict[str, Any]:
        if ident not in self.nodes:
            self.nodes[ident] = {"id": ident, "type": type, "label": label, "degree": 0, **extras}
        return self.nodes[ident]

    def link(self, type: str, origin: str, target: str, evidence: dict[str, Any], **extras: Any) -> None:
        if origin == target:
            return  # a document that cites itself is not an edge
        ident = f"{type}|{origin}|{target}"
        edge = self.edges.get(ident)
        if edge is None:
            edge = {"id": ident, "type": type, "from": origin, "to": target, "evidence": [], **extras}
            self.edges[ident] = edge
            self.adjacency[origin].add(ident)
            self.adjacency[target].add(ident)
            self.nodes[origin]["degree"] += 1
            self.nodes[target]["degree"] += 1
        if evidence not in edge["evidence"]:
            edge["evidence"].append(evidence)

    def counts(self) -> dict[str, dict[str, int]]:
        nodes: dict[str, int] = defaultdict(int)
        for n in self.nodes.values():
            nodes[n["type"]] += 1
        edges: dict[str, int] = defaultdict(int)
        for e in self.edges.values():
            edges[e["type"]] += 1
        return {"nodes": dict(nodes), "edges": dict(edges)}

    def sorted_nodes(self) -> list[dict[str, Any]]:
        return sorted(self.nodes.values(), key=lambda n: (NODE_TYPES.index(n["type"]), n["label"].lower()))

    def summary(self) -> dict[str, Any]:
        edges = [
            {
                **{k: v for k, v in e.items() if k != "evidence"},
                "total_evidence": len(e["evidence"]),
                "evidence": e["evidence"][:EVIDENCE_IN_SUMMARY],
            }
            for e in self.edges.values()
        ]
        return {"root": self.root, "nodes": self.sorted_nodes(), "edges": edges, "counts": self.counts()}


def build(
    conn: sqlite3.Connection,
    taxonomy: Taxonomy,
    sources: Collection[str] | None = None,
    rules: DecisionRules | None = None,
) -> Graph:
    """``sources`` limits the graph to the documents of these sources (the domain wall): nothing outside
    becomes a node, edge or evidence, and only the categories those documents use appear."""
    rules = rules or DecisionRules()
    graph = Graph(root="root")
    graph.node("root", "root", taxonomy.root, detail="root of the taxonomy (taxonomy.yaml)")

    where, params = "", []
    if sources is not None:
        where = f" AND source_id IN ({', '.join('?' * len(sources)) or 'NULL'})"
        params = list(sources)
    documents = conn.execute(
        "SELECT id, source_id, path, name, title, category, category_confidence, categories_detail, "
        f"content, status FROM document WHERE status != 'removed'{where} ORDER BY source_id, path",
        params,
    ).fetchall()
    lines = {int(d["id"]): (d["content"] or "").split("\n") for d in documents}
    chunks: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    for c in conn.execute("SELECT document_id, ordinal, line_start, line_end FROM chunk"):
        if int(c["document_id"]) not in lines:
            continue
        chunks[int(c["document_id"])].append((int(c["line_start"]), int(c["line_end"]), int(c["ordinal"])))
    info = {int(d["id"]): d for d in documents}

    def evidence(document_id: int, line: int | None, rule: str, **extras: Any) -> dict[str, Any]:
        d = info[document_id]
        text = None
        ordinal = None
        if line is not None and 1 <= line <= len(lines[document_id]):
            text = lines[document_id][line - 1].strip()[:300]
            ordinal = next((o for a, b, o in chunks[document_id] if a <= line <= b), None)
        return {
            "document_id": document_id,
            "source_id": d["source_id"],
            "path": d["path"],
            "line": line,
            "chunk_ordinal": ordinal,
            "excerpt": text,
            "rule": rule,
            **extras,
        }

    # Categories: the declared tree, plus any used category that is not in it.
    used = {a["category"] for d in documents for a in json_load(d["categories_detail"], [])}
    categories = list(taxonomy.categories) + sorted(c for c in used if c not in taxonomy.categories)
    if sources is not None:
        # Only the part of the tree that the visible documents use (with ancestors), not other domains'.
        visible = {"/".join(c.split("/")[: i + 1]) for c in used for i in range(c.count("/") + 1)}
        categories = [c for c in categories if c in visible]
    for path in categories:
        graph.node(f"cat:{path}", "category", path.rsplit("/", 1)[-1], category=path, detail=path)
    for path in categories:
        parent = path.rsplit("/", 1)[0] if "/" in path else None
        rule = (
            "structure declared in taxonomy.yaml"
            if path in taxonomy.categories
            else "category used by documents without a rule that classifies them"
        )
        graph.link(
            "subcategory",
            f"cat:{path}",
            f"cat:{parent}" if parent else "root",
            {"rule": rule, "file": "taxonomy.yaml" if path != UNCATEGORIZED else None},
        )

    # Documents and decisions.
    decisions: dict[str, int] = {}
    for d in documents:
        document_id = int(d["id"])
        code = rules.code_from_path(d["path"])
        if code:
            decisions.setdefault(code, document_id)
        graph.node(
            f"doc:{document_id}",
            "decision" if code else "document",
            d["title"] or d["name"],
            document_id=document_id,
            source_id=d["source_id"],
            path=d["path"],
            name=d["name"],
            category=d["category"],
            confidence=d["category_confidence"],
            status=d["status"],
            code=code,
            detail=f"{d['source_id']}:{d['path']}",
        )
        for a in json_load(d["categories_detail"], []):
            graph.link(
                "category",
                f"doc:{document_id}",
                f"cat:{a['category']}",
                evidence(document_id, a.get("line"), a["reason"], origin=a["origin"], file="taxonomy.yaml"),
                confidence=a["confidence"],
                primary=bool(a["primary"]),
            )

    # Written links (wikilink / Markdown), already resolved by the indexer.
    for lk in conn.execute(
        "SELECT document_id, line, type, target, target_document_id FROM link WHERE target_document_id IS NOT NULL"
    ):
        origin, target = int(lk["document_id"]), int(lk["target_document_id"])
        if origin not in info or target not in info:
            continue
        rule = f"wikilink [[{lk['target']}]]" if lk["type"] != "markdown" else f"Markdown link to {lk['target']}"
        graph.link("link", f"doc:{origin}", f"doc:{target}", evidence(origin, int(lk["line"]), rule))

    # Explicit mentions (decision codes, hashes, paths).
    by_path: dict[str, list[int]] = defaultdict(list)
    for d in documents:
        by_path[d["path"].lower()].append(int(d["id"]))
    for mc in conn.execute("SELECT document_id, type, key, line, rule FROM mention ORDER BY document_id, line"):
        origin = int(mc["document_id"])
        if origin not in info:
            continue
        kind, key, line = mc["type"], mc["key"], int(mc["line"])
        target_id = None
        rule = mc["rule"]
        if kind == "decision" and key in decisions:
            target = decisions[key]
            target_id = f"doc:{target}"
            rule = f"{rule}; {key} is the file {info[target]['path']}"
        elif kind == "file" and key.lower().endswith(".md"):
            target_md = _document_by_path(key, by_path)
            if target_md is not None:
                target_id = f"doc:{target_md}"
                rule = f"{rule}; it is the document {info[target_md]['source_id']}:{info[target_md]['path']}"
        if target_id is None:
            target_id = f"ent:{kind}:{key}"
            graph.node(
                target_id,
                kind,
                key,
                key=key,
                detail="decision cited without a file among the sources" if kind == "decision" else _entity_detail(kind),
            )
        graph.link("mentions", f"doc:{origin}", target_id, evidence(origin, line, rule))
    return graph


def _document_by_path(path: str, by_path: dict[str, list[int]]) -> int | None:
    target = path.lower().removeprefix("./")
    exact = by_path.get(target, [])
    if len(exact) == 1:
        return exact[0]
    suffix = [ids[0] for p, ids in by_path.items() if len(ids) == 1 and target.endswith("/" + p)]
    return suffix[0] if len(suffix) == 1 else None


def _entity_detail(kind: str) -> str:
    return {
        "commit": "cited commit (first 7 characters)",
        "migration": "cited migration revision",
        "hash": "cited hexadecimal identifier",
        "file": "cited file",
    }.get(kind, kind)


def detail(conn: sqlite3.Connection, graph: Graph, ident: str, chunk_limit: int = 40) -> dict[str, Any] | None:
    node = graph.nodes.get(ident)
    if node is None:
        return None
    relations = []
    for edge_id in graph.adjacency.get(ident, set()):
        e = graph.edges[edge_id]
        outgoing = e["from"] == ident
        other = graph.nodes[e["to"] if outgoing else e["from"]]
        relations.append({"edge": e, "direction": "out" if outgoing else "in", "other": other})
    type_order = {t: i for i, t in enumerate(EDGE_TYPES)}
    relations.sort(key=lambda r: (type_order[r["edge"]["type"]], r["direction"], r["other"]["label"].lower()))

    own: list[dict[str, Any]] = []
    if node.get("document_id") is not None:
        own = _chunks(conn, [(node["document_id"], None)], "chunk of the document itself", chunk_limit)
    cited: list[tuple[int, int | None]] = []
    for r in relations:
        if r["direction"] != "in" or r["edge"]["type"] not in ("link", "mentions"):
            continue
        for ev in r["edge"]["evidence"]:
            if ev.get("chunk_ordinal") is not None and (ev["document_id"], ev["chunk_ordinal"]) not in cited:
                cited.append((ev["document_id"], ev["chunk_ordinal"]))
    related = _chunks(conn, cited, "chunk that cites this node", chunk_limit)

    open_at = None
    if node.get("document_id") is not None:
        open_at = {"document_id": node["document_id"], "line": None}
    else:
        first = next(
            (ev for r in relations for ev in r["edge"]["evidence"] if ev.get("document_id") and ev.get("line")),
            None,
        )
        if first:
            open_at = {"document_id": first["document_id"], "line": first["line"]}
    return {"node": node, "relations": relations, "own_chunks": own, "related_chunks": related, "open": open_at}


def _chunks(
    conn: sqlite3.Connection, targets: list[tuple[int, int | None]], reason: str, limit: int
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if limit <= 0:
        return out
    for document_id, ordinal in targets:
        sql = (
            "SELECT c.document_id, c.ordinal, c.title, c.section_path, c.line_start, c.line_end, c.text, "
            "d.source_id, d.path, d.title AS document_title FROM chunk c JOIN document d ON d.id = c.document_id "
            "WHERE c.document_id = ?"
        )
        params: list[Any] = [document_id]
        if ordinal is not None:
            sql += " AND c.ordinal = ?"
            params.append(ordinal)
        for c in conn.execute(sql + " ORDER BY c.ordinal", params):
            text = c["text"]
            out.append(
                {
                    "document_id": int(c["document_id"]),
                    "document_title": c["document_title"],
                    "source_id": c["source_id"],
                    "path": c["path"],
                    "ordinal": int(c["ordinal"]),
                    "title": c["title"],
                    "section_path": json_load(c["section_path"], []),
                    "line_start": int(c["line_start"]),
                    "line_end": int(c["line_end"]),
                    "text": text if len(text) <= 900 else text[:900] + "…",
                    "reason": reason,
                }
            )
            if len(out) >= limit:
                return out
    return out


def find(graph: Graph, term: str, limit: int = 5) -> list[dict[str, Any]]:
    """Nodes whose name/code/path matches the term: exact, then prefix, then substring."""
    target = normalize((term or "").strip())
    if not target:
        return []
    found = []
    for n in graph.nodes.values():
        keys = [
            normalize(str(v))
            for v in (n.get("label"), n.get("key"), n.get("code"), n.get("category"), n.get("path"), n.get("name"))
            if v
        ]
        if n.get("path"):
            keys.append(normalize(PurePosixPath(n["path"]).stem))
        if target in keys:
            points, reason = 100, "same name or code"
        elif any(k.startswith(target) for k in keys):
            points, reason = 60, "name starts with the term"
        elif any(target in k for k in keys):
            points, reason = 40, "name contains the term"
        else:
            continue
        found.append({"node": n, "points": points, "reason": reason})
    found.sort(key=lambda a: (-a["points"], -a["node"]["degree"], a["node"]["label"].lower()))
    return found[:limit]
