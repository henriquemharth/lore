// Pure UI logic (tree, excerpts, formatting, graph). Node reads the .ts files directly (type stripping).
import assert from "node:assert/strict";
import { test } from "node:test";

import { formatBytes, formatDate, formatNumber, normalize, relativeTime, shortHash } from "../src/logic/format.ts";
import { chunkByLine, linesOf, splitExcerpt } from "../src/logic/excerpt.ts";
import { findNodes, computeVisible, defaultFilters, indexGraph, shortLabel } from "../src/logic/graph.ts";
import { ancestors, buildTree, categoryKeys } from "../src/logic/tree.ts";

// Dates are shown in the local time zone; pin it so the expectations are stable.
process.env.TZ = "UTC";

const doc = (id, name, category, extras = {}) => ({
  id,
  source_id: "docs",
  path: name,
  name,
  size: 10,
  hash: "abc",
  modified_at: null,
  title: null,
  category,
  categories: [category],
  status: "indexed",
  error: null,
  total_chunks: 1,
  version: 1,
  indexed_at: null,
  removed_at: null,
  pending: null,
  ...extras,
});

const ORDER = ["Platform/Architecture", "Platform/Security", "Platform/Deploy", "Decisions", "Memories/Agents"];

const DOCS = [
  doc(1, "backend.md", "Platform/Architecture"),
  doc(2, "database.md", "Platform/Architecture"),
  doc(3, "security.md", "Platform/Security", { categories: ["Platform/Security", "Decisions"] }),
  doc(4, "deploy.md", "Platform/Deploy", { pending: "changed" }),
  doc(5, "ADR-10.md", "Decisions"),
  doc(6, "ADR-2.md", "Decisions"),
  doc(7, "loose.md", "Uncategorized"),
  doc(8, "deleted.md", "Decisions", { status: "removed" }),
];

test("tree: root → categories in taxonomy order → files", () => {
  const root = buildTree(DOCS, ORDER);
  assert.equal(root.label, "lore");
  assert.equal(root.total, 7, "removed documents are left out");
  assert.deepEqual(
    root.children.map((c) => c.label),
    ["Platform", "Decisions", "Uncategorized"],
  );
  const platform = root.children[0];
  assert.deepEqual(
    platform.children.map((c) => [c.label, c.total]),
    [
      ["Architecture", 2],
      ["Security", 1],
      ["Deploy", 1],
    ],
  );
  assert.deepEqual(
    platform.children[0].children.map((c) => c.label),
    ["backend.md", "database.md"],
  );
  // natural sort: ADR-2 before ADR-10
  assert.deepEqual(
    root.children[1].children.map((c) => c.label),
    ["ADR-2.md", "ADR-10.md"],
  );
});

test("tree: an alert bubbles up to the root on a pending change or error", () => {
  const root = buildTree(DOCS, ORDER);
  const platform = root.children[0];
  assert.equal(platform.children.find((c) => c.label === "Deploy").alert, true);
  assert.equal(platform.children.find((c) => c.label === "Architecture").alert, false);
  assert.equal(platform.alert, true);
  assert.equal(root.alert, true);
});

test("tree: secondary categories repeat the file without counting it twice", () => {
  const root = buildTree(DOCS, ORDER, "lore", { secondary: true });
  const decisions = root.children.find((c) => c.label === "Decisions");
  assert.ok(decisions.children.some((c) => c.label === "security.md"));
  assert.equal(root.total, 7);
});

test("tree: the filter ignores accents and case and prunes empty categories", () => {
  const root = buildTree([...DOCS, doc(9, "café-menu.md", "Platform/Deploy")], ORDER, "lore", { filter: "CAFE" });
  assert.equal(root.total, 1);
  assert.deepEqual(root.children.map((c) => c.label), ["Platform"]);
  assert.deepEqual(categoryKeys(root), ["Platform", "Platform/Deploy"]);
});

test("ancestors of a category", () => {
  assert.deepEqual(ancestors("Platform/Security"), ["Platform", "Platform/Security"]);
  assert.deepEqual(ancestors("Decisions"), ["Decisions"]);
});

test("search excerpt: highlights come only from the markers, never from HTML", () => {
  assert.deepEqual(splitExcerpt("the \u0002session\u0003 expires <b>"), [
    { text: "the ", highlight: false },
    { text: "session", highlight: true },
    { text: " expires <b>", highlight: false },
  ]);
  assert.deepEqual(splitExcerpt("no highlight"), [{ text: "no highlight", highlight: false }]);
});

test("content lines and the chunk that owns each line", () => {
  assert.deepEqual(linesOf("a\nb\n"), ["a", "b"]);
  assert.deepEqual(linesOf(""), [""]);
  const map = chunkByLine(5, [
    { ordinal: 0, line_start: 1, line_end: 2 },
    { ordinal: 1, line_start: 4, line_end: 5 },
  ]);
  assert.deepEqual(map.slice(1), [0, 0, null, 1, 1]);
});

test("en-US formatting", () => {
  assert.equal(formatDate("2026-09-25T17:03:07+00:00"), "Sep 25, 2026 17:03");
  assert.equal(formatDate(null), "—");
  assert.equal(formatBytes(18_636), "18.2 KB");
  assert.equal(formatBytes(512), "512 B");
  assert.equal(formatNumber(20447), "20,447");
  assert.equal(shortHash("git:0123456789abcdef"), "0123456789ab");
  assert.equal(normalize("Café Déjà"), "cafe deja");
  const now = new Date("2026-09-25T17:10:00Z");
  assert.equal(relativeTime("2026-09-25T17:03:00Z", now), "7 min ago");
  assert.equal(relativeTime("2026-09-25T15:10:00Z", now), "2 h ago");
  assert.equal(relativeTime("2026-09-25T17:09:50Z", now), "just now");
  assert.equal(relativeTime(null, now), "never");
});

// ---------------------------------------------------------------- graph

const GRAPH = {
  root: "root",
  nodes: [
    { id: "root", type: "root", label: "Acme Shop", degree: 2 },
    { id: "cat:Decisions", type: "category", label: "Decisions", degree: 3, category: "Decisions" },
    { id: "cat:Architecture", type: "category", label: "Architecture", degree: 2, category: "Architecture" },
    { id: "doc:1", type: "decision", label: "ADR-0004 — Move orders to PostgreSQL", degree: 3, document_id: 1, source_id: "docs", category: "Decisions", code: "ADR-0004" },
    { id: "doc:2", type: "document", label: "Architecture", degree: 3, document_id: 2, source_id: "docs", category: "Architecture", name: "ARCHITECTURE.md" },
    { id: "doc:3", type: "document", label: "README", degree: 1, document_id: 3, source_id: "repo", category: "Architecture", name: "README.md" },
    { id: "ent:commit:06e4b4d", type: "commit", label: "06e4b4d", degree: 1, key: "06e4b4d" },
    { id: "ent:file:app/x.py", type: "file", label: "app/x.py", degree: 0, key: "app/x.py" },
  ],
  edges: [
    { id: "s1", type: "subcategory", from: "cat:Decisions", to: "root", total_evidence: 1 },
    { id: "s2", type: "subcategory", from: "cat:Architecture", to: "root", total_evidence: 1 },
    { id: "c1", type: "category", from: "doc:1", to: "cat:Decisions", confidence: "explicit", primary: true, total_evidence: 1 },
    { id: "c2", type: "category", from: "doc:2", to: "cat:Architecture", confidence: "explicit", primary: true, total_evidence: 1 },
    { id: "c3", type: "category", from: "doc:2", to: "cat:Decisions", confidence: "suggestion", primary: false, total_evidence: 1 },
    { id: "c4", type: "category", from: "doc:3", to: "cat:Architecture", confidence: "suggestion", primary: true, total_evidence: 1 },
    { id: "l1", type: "link", from: "doc:2", to: "doc:1", total_evidence: 2 },
    { id: "m1", type: "mentions", from: "doc:1", to: "ent:commit:06e4b4d", total_evidence: 1 },
  ],
  counts: { nodes: {}, edges: {} },
  types: { nodes: [], edges: [] },
};

test("graph: the overview hides entities and shows only the primary suggestion", () => {
  const index = indexGraph(GRAPH);
  const v = computeVisible(index, defaultFilters(), new Set(), null);
  assert.ok(!v.nodes.has("ent:commit:06e4b4d"));
  assert.ok(v.nodes.has("doc:1") && v.nodes.has("doc:2"));
  assert.ok(v.edges.has("c4"), "a suggestion that is the primary category is shown");
  assert.ok(!v.edges.has("c3"), "a secondary suggestion is hidden");
  const f = defaultFilters();
  f.suggestions = "all";
  assert.ok(computeVisible(index, f, new Set(), null).edges.has("c3"));
  f.suggestions = "none";
  assert.ok(!computeVisible(index, f, new Set(), null).edges.has("c4"));
});

test("graph: expanding shows relations of a hidden type; focus shows only the neighborhood", () => {
  const index = indexGraph(GRAPH);
  const expanded = computeVisible(index, defaultFilters(), new Set(["doc:1"]), null);
  assert.ok(expanded.nodes.has("ent:commit:06e4b4d"));
  assert.ok(expanded.edges.has("m1"));
  const focus = computeVisible(index, defaultFilters(), new Set(), { id: "doc:2", depth: 1 });
  assert.deepEqual([...focus.nodes].sort(), ["cat:Architecture", "doc:1", "doc:2"]);
});

test("graph: source filter, and an entity without a visible relation disappears", () => {
  const index = indexGraph(GRAPH);
  const f = defaultFilters();
  f.source = "repo";
  const v = computeVisible(index, f, new Set(), null);
  assert.ok(v.nodes.has("doc:3") && !v.nodes.has("doc:1"));
  f.source = null;
  f.nodeTypes.add("file");
  assert.ok(!computeVisible(index, f, new Set(), null).nodes.has("ent:file:app/x.py"));
});

test("graph: node search and short labels", () => {
  assert.equal(findNodes(GRAPH, "adr-0004")[0].id, "doc:1");
  assert.equal(findNodes(GRAPH, "06e4")[0].id, "ent:commit:06e4b4d");
  assert.deepEqual(findNodes(GRAPH, "Authentication"), []);
  assert.equal(shortLabel(GRAPH.nodes[3]), "ADR-0004");
  assert.equal(shortLabel(GRAPH.nodes[4]), "ARCHITECTURE");
  assert.equal(shortLabel(GRAPH.nodes[7]), "x.py");
});
