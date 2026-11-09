// Renders the real UI components (React on the server) with sample data and checks what a person
// sees. esbuild (already installed with Vite) bundles the .tsx files into a temporary file.
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";

// Dates are shown in the local time zone; pin it so the expectations are stable.
process.env.TZ = "UTC";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const NOW = new Date("2026-09-25T17:10:00Z");
let folder;
let ui;
const reactWarnings = [];
const originalConsoleError = console.error;

before(async () => {
  folder = mkdtempSync(join(tmpdir(), "lore-ui-"));
  const out = join(folder, "components.cjs");
  await build({
    stdin: {
      contents: `
        export { StatusPanel } from "./src/components/StatusPanel.tsx";
        export { DocumentTree } from "./src/components/DocumentTree.tsx";
        export { DocumentView } from "./src/components/DocumentView.tsx";
        export { SearchResults } from "./src/components/SearchResults.tsx";
        export { AuditView } from "./src/components/AuditView.tsx";
        export { buildTree } from "./src/logic/tree.ts";
        export { NodeDetailView, EdgeDetailView } from "./src/components/GraphDetails.tsx";
        export { McpPanelView, ContextPreview } from "./src/components/McpView.tsx";
        export { renderToStaticMarkup } from "react-dom/server";
        export { createElement } from "react";
      `,
      resolveDir: ROOT,
      loader: "tsx",
    },
    bundle: true,
    format: "cjs",
    platform: "node",
    jsx: "automatic",
    outfile: out,
    logLevel: "silent",
  });
  ui = createRequire(import.meta.url)(out);
  // A React warning (duplicate key, invalid prop…) fails the run.
  console.error = (...args) => reactWarnings.push(args.join(" "));
});

after(() => {
  console.error = originalConsoleError;
  if (folder) rmSync(folder, { recursive: true, force: true });
  assert.deepEqual(reactWarnings, [], "React must not emit warnings");
});

const html = (component, props) => ui.renderToStaticMarkup(ui.createElement(component, props));
const text = (markup) => markup.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

const STATUS = {
  version: "0.1.0",
  read_only: true,
  documents: { total: 137, indexed: 136, with_errors: 1, removed: 2 },
  chunks: 592,
  links: { total: 356, resolved: 350 },
  last_run: {
    id: 9,
    origin: "api",
    full: false,
    status: "completed",
    started_at: "2026-09-25T17:03:06+00:00",
    finished_at: "2026-09-25T17:03:07+00:00",
    totals: { new: 1 },
    error: null,
    items: [{ document_id: 12, source_id: "docs", path: "ARCHITECTURE.md", event: "changed", detail: "9 chunks" }],
  },
  last_check: "2026-09-25T17:08:00+00:00",
  run_in_progress: null,
  pending: [{ source_id: "docs", path: "SECURITY.md", type: "changed", detail: null }],
  errors: [{ document_id: 40, source_id: "docs", path: "bugs/BROKEN.md", error: "Could not read: Permission denied", kind: "read" }],
  categories: [
    { category: "Platform/Security", documents: 7, explicit: 8, suggestions: 32, tagged: 40 },
    { category: "Decisions", documents: 42, explicit: 42, suggestions: 0, tagged: 42 },
  ],
  sources: [
    { id: "docs", name: "Acme Shop docs", type: "folder", domain: "default", root: "/acme/docs", ref: null, ref_commit: null, available: true, warnings: [], checked_at: null, documents: 90 },
    { id: "repo", name: "Acme Shop repo", type: "git", domain: "default", root: "/acme", ref: "origin/main", ref_commit: "60041bbf9fd5", available: false, warnings: ["ref not found"], checked_at: null, documents: 11 },
  ],
  domains: [{ name: "default", description: null, sources: ["docs", "repo"] }],
  search: { text: true, language: "en", semantic: { available: false, reason: "Embeddings are not configured." } },
  sync_seconds: 300,
  taxonomy: { root: "Acme Shop", categories: ["Company", "Platform", "Platform/Security", "Decisions"] },
};

test("dashboard shows documents, chunks, last run, changes, errors, categories and sources", () => {
  const t = text(html(ui.StatusPanel, { status: STATUS, now: NOW }));
  for (const expected of [
    "Documents 137",
    "136 indexed · 1 with errors · 2 removed",
    "Chunks 592",
    "350 of 356 links resolved",
    "Last index run 7 min ago",
    "Sep 25, 2026 17:03 · from the UI/API",
    "Changed documents 1",
    "Waiting for a reindex",
    "docs:SECURITY.md",
    "In the last run",
    "docs:ARCHITECTURE.md",
    "Indexing errors",
    "bugs/BROKEN.md",
    "Permission denied",
    "Categories",
    "Platform › Security",
    "Decisions",
    "origin/main @ 60041bb",
    "ref not found",
    "unavailable",
    "No .md file is modified",
    "Automatic check every 5 min",
    "Embeddings are not configured.",
    "lore 0.1.0",
  ]) {
    assert.ok(t.includes(expected), `missing: ${expected}\n${t}`);
  }
  assert.match(html(ui.StatusPanel, { status: STATUS, onReindex: () => {} }), /Reindex changes/);
});

test("dashboard does not pretend zero before the first run", () => {
  const t = text(html(ui.StatusPanel, { status: { ...STATUS, last_run: null }, now: NOW }));
  assert.ok(t.includes("Last index run never"));
  assert.ok(t.includes("Not indexed yet"));
});

test("dashboard reports the result of a reindex", () => {
  const result = {
    run_id: 10, recorded: true, origin: "api", full: true, status: "completed", duration_ms: 1234,
    totals: { new: 2, changed: 1, removed: 0, errors: 0 }, unavailable_sources: [], errors: [],
  };
  const t = text(html(ui.StatusPanel, { status: STATUS, now: NOW, result }));
  assert.ok(t.includes("Done in 1,234 ms: 2 new, 1 changed, 0 removed, 0 errors."), t);
  const nothing = text(html(ui.StatusPanel, { status: STATUS, now: NOW, result: { ...result, recorded: false } }));
  assert.ok(nothing.includes("Nothing changed in the sources."));
});

const DOCS = [
  { id: 1, source_id: "docs", path: "ARCHITECTURE.md", name: "backend.md", title: "Architecture", category: "Platform/Architecture", categories: ["Platform/Architecture"], status: "indexed", pending: null },
  { id: 2, source_id: "docs", path: "DATABASE.md", name: "database.md", title: "Database", category: "Platform/Architecture", categories: ["Platform/Architecture"], status: "indexed", pending: null },
  { id: 3, source_id: "docs", path: "SECURITY.md", name: "security.md", title: "Security", category: "Platform/Security", categories: ["Platform/Security"], status: "error", pending: null },
  { id: 4, source_id: "repo", path: "deploy/README.md", name: "deploy.md", title: "Deploy", category: "Platform/Deploy", categories: ["Platform/Deploy"], status: "indexed", pending: "changed" },
];

test("explorer tree: root → categories → files, only what is open", () => {
  const root = ui.buildTree(DOCS, ["Platform/Architecture", "Platform/Security", "Platform/Deploy"], "Acme Shop");
  const open = new Set(["Platform", "Platform/Architecture"]);
  const markup = html(ui.DocumentTree, { root, open, selected: 2, onToggle() {}, onSelect() {} });
  const t = text(markup);
  for (const expected of ["Acme Shop", "Platform", "Architecture", "backend.md", "database.md", "Security", "Deploy"]) {
    assert.ok(t.includes(expected), `missing: ${expected}`);
  }
  assert.ok(!t.includes("security.md"), "a closed category does not show its files");
  assert.match(markup, /role="tree"/);
  assert.match(markup, /aria-selected="true"[^>]*title="docs:DATABASE.md/);

  const all = html(ui.DocumentTree, {
    root,
    open: new Set(["Platform", "Platform/Security", "Platform/Deploy"]),
    onToggle() {},
    onSelect() {},
  });
  assert.match(all, /title="Indexing error"/);
  assert.match(all, /title="Changed, waiting for a reindex"/);
});

const DETAIL = {
  id: 7,
  source_id: "docs",
  source_name: "Acme Shop docs",
  source_type: "folder",
  path: "SECURITY.md",
  name: "SECURITY.md",
  size: 18_636,
  hash: "3be46b1dd0a1ffff",
  modified_at: "2026-09-17T20:07:00+00:00",
  title: "Security",
  category: "Platform/Security",
  categories: ["Platform/Security", "Platform/Backend"],
  category_confidence: "explicit",
  status: "indexed",
  error: null,
  error_kind: null,
  total_chunks: 2,
  version: 3,
  indexed_at: "2026-09-25T17:03:07+00:00",
  removed_at: null,
  discovered_at: "2026-09-25T17:00:00+00:00",
  processor_version: "1",
  frontmatter: { description: "access rules" },
  metadata: { Date: "2026-09-16" },
  headings: [
    { level: 1, text: "Security", line: 1, anchor: "security" },
    { level: 2, text: "Sessions", line: 4, anchor: "sessions" },
  ],
  warnings: [],
  body_start: 0,
  total_lines: 6,
  content: "# Security\n\nIntro.\n## Sessions\nA session expires after <10 min>.\n",
  chunks: [
    { id: 1, ordinal: 0, title: "Security", section_path: ["Security"], sections: ["Security"], level: 1, part: 1, parts: 1, line_start: 1, line_end: 3, char_start: 0, char_end: 18, size: 18, hash: "a", text: "# Security\n\nIntro.", position_percent: 0 },
    { id: 2, ordinal: 1, title: "Sessions", section_path: ["Security", "Sessions"], sections: ["Sessions"], level: 2, part: 1, parts: 1, line_start: 4, line_end: 5, char_start: 19, char_end: 63, size: 44, hash: "b", text: "## Sessions\nA session expires after <10 min>.", position_percent: 34.5 },
  ],
  links_out: [{ line: 3, type: "wikilink", target: "DATABASE", anchor: null, text: null, target_document_id: 2, target_title: "Database" }],
  links_in: [{ document_id: 1, title: "Index", source_id: "docs", path: "INDEX.md" }],
  versions: [{ version: 3, event: "changed", hash: "3be46b1dd0a1", size: 18_636, modified_at: null, run_id: 9, recorded_at: "2026-09-25T17:03:07+00:00" }],
  pending: null,
};

test("an open file shows its details, headings, chunks, original content and last run", () => {
  const base = { doc: DETAIL, onTab() {}, now: NOW };
  const info = text(html(ui.DocumentView, { ...base, tab: "headings" }));
  for (const expected of [
    "Security",
    "Acme Shop docs · SECURITY.md",
    "read-only",
    "File SECURITY.md",
    "Size 18.2 KB",
    "Last indexed Sep 25, 2026 17:03",
    "Category Platform › Security explicit",
    "Also suggested in Platform › Backend",
    "3be46b1dd0a1",
    "Date 2026-09-16",
    "frontmatter · description access rules",
    "Headings 2",
    "Chunks 2",
    "Original content 6",
    "H2 Sessions line 4",
  ]) {
    assert.ok(info.includes(expected), `missing: ${expected}\n${info}`);
  }

  const chunks = text(html(ui.DocumentView, { ...base, tab: "chunks", activeChunk: 1 }));
  assert.ok(chunks.includes("#2 Sessions"));
  assert.ok(chunks.includes("lines 4–5"));
  assert.ok(chunks.includes("34.5% of the document"));
  assert.ok(chunks.includes("Security › Sessions"));

  const original = html(ui.DocumentView, { ...base, tab: "original", activeChunk: 1 });
  assert.ok(original.includes("A session expires after &lt;10 min&gt;."), "content is text, not HTML");
  assert.equal((original.match(/class="bg-highlight"/g) ?? []).length, 2, "both lines of chunk 2 are highlighted");

  const links = text(html(ui.DocumentView, { ...base, tab: "links" }));
  for (const expected of ["Links in this document (1)", "Database", "Linked from (1)", "Index", "v3", "Changed"]) {
    assert.ok(links.includes(expected), `missing: ${expected}`);
  }
});

test("search highlights the terms without interpreting the document's HTML", () => {
  const response = {
    query: "session",
    expression: '"session"*',
    mode: "all",
    total_chunks: 1,
    total_documents: 1,
    markers: { start: "\u0002", end: "\u0003" },
    requested_mode: "text",
    used_mode: "text",
    results: [
      {
        chunk_id: 2,
        chunk_ordinal: 1,
        document_id: 7,
        document_title: "Security",
        source_id: "docs",
        path: "SECURITY.md",
        category: "Platform/Security",
        title: "Sessions",
        section_path: ["Security", "Sessions"],
        line_start: 4,
        line_end: 5,
        excerpt: "A \u0002session\u0003 expires <script>alert(1)</script>",
        score: 3.2,
      },
    ],
  };
  const markup = html(ui.SearchResults, { response });
  assert.match(markup, /<mark>session<\/mark>/);
  assert.ok(!markup.includes("<script>"));
  assert.ok(markup.includes("&lt;script&gt;"));
  const t = text(markup);
  assert.ok(t.includes("1 document · 1 chunk"), t);
  assert.ok(t.includes("docs:SECURITY.md:4"));
  assert.ok(t.includes("Platform › Security"));
  const any = text(html(ui.SearchResults, { response: { ...response, mode: "any", total_documents: 2, total_chunks: 3 } }));
  assert.ok(any.includes("2 documents · 3 chunks · no chunk had every word"), any);
  assert.ok(text(html(ui.SearchResults, { response: { ...response, results: [] } })).includes("No chunks found for “session”"));
});

test("audit lists runs and log entries", () => {
  const t = text(
    html(ui.AuditView, {
      runs: [{ id: 9, origin: "api", full: true, status: "completed", started_at: "2026-09-25T17:03:06+00:00", finished_at: null, totals: { new: 1, documents: 137, chunks: 592 }, error: null }],
      entries: [
        { id: 1, at: "2026-09-25T17:03:07+00:00", action: "reindex_completed", origin: "api", ip: "127.0.0.1", agent: null, details: { run_id: 9, totals: { documents: 137, chunks: 592 } } },
        { id: 2, at: "2026-09-25T17:04:00+00:00", action: "mcp_query", origin: "mcp", ip: null, agent: null, details: { tool: "lore_search", input: { query: "orders" }, chunks: [1, 2], response_characters: 900 } },
      ],
    }),
  );
  for (const expected of [
    "Index runs",
    "from the UI/API · full",
    "completed",
    "Reindex completed",
    "127.0.0.1",
    "run 9 · 137 docs · 592 chunks",
    "MCP query",
    "lore_search · “orders” · 2 chunks · 900 characters",
    "append-only",
  ]) {
    assert.ok(t.includes(expected), `missing: ${expected}\n${t}`);
  }
});

// ---------------------------------------------------------------- categories as suggestions

test("the tree marks a suggested category and shows an empty taxonomy category", () => {
  const docs = [
    { ...DOCS[0], category_confidence: "explicit", categories_detail: [{ category: "Platform/Architecture", primary: true, confidence: "explicit", origin: "rule", reason: "the file name says ARCHITECTURE", line: null }] },
    { ...DOCS[3], pending: null, category_confidence: "suggestion", categories_detail: [{ category: "Platform/Deploy", primary: true, confidence: "suggestion", origin: "keyword", reason: "keyword deploy", line: null }] },
  ];
  const root = ui.buildTree(docs, ["Company", "Platform", "Platform/Architecture", "Platform/Deploy"], "Acme Shop", { showEmpty: true });
  const markup = html(ui.DocumentTree, {
    root,
    open: new Set(["Platform", "Platform/Architecture", "Platform/Deploy"]),
    onToggle() {},
    onSelect() {},
  });
  const t = text(markup);
  assert.ok(t.includes("Acme Shop"));
  assert.ok(/Company empty 0/.test(t), t);
  assert.equal((markup.match(/>suggestion</g) ?? []).length, 2, "legend + the suggested file");
  assert.match(markup, /title="Category suggested automatically: keyword deploy"/);
  assert.ok(!/backend\.md[^<]*<\/span><span[^>]*>suggestion/.test(markup), "an explicit file is not marked");
});

// ---------------------------------------------------------------- graph

const NODE_ADR = { id: "doc:41", type: "decision", label: "ADR-0004 — Move orders to PostgreSQL", degree: 3, document_id: 41, source_id: "docs", path: "adr/0004-move-orders-to-postgresql.md", category: "Decisions", confidence: "explicit", status: "indexed", code: "ADR-0004", detail: "docs:adr/0004-move-orders-to-postgresql.md" };
const NODE_ARCH = { id: "doc:2", type: "document", label: "Architecture", degree: 9, document_id: 2, source_id: "docs", path: "ARCHITECTURE.md", category: "Architecture", confidence: "explicit", status: "indexed", code: null, detail: "docs:ARCHITECTURE.md" };
const NODE_CAT = { id: "cat:Decisions", type: "category", label: "Decisions", degree: 42, category: "Decisions", detail: "Decisions" };
const NODE_COMMIT = { id: "ent:commit:06e4b4d", type: "commit", label: "06e4b4d", degree: 2, key: "06e4b4d", detail: "cited commit" };

const ACTIONS = { onOpenSource() {}, onSelect() {}, onExpand() {}, onFocus() {} };

test("node detail shows where each relation comes from and opens the source", () => {
  const detail = {
    node: NODE_ADR,
    relations: [
      {
        direction: "out",
        other: NODE_CAT,
        edge: { id: "category|doc:41|cat:Decisions", type: "category", from: "doc:41", to: "cat:Decisions", confidence: "explicit", primary: true, evidence: [{ document_id: 41, source_id: "docs", path: "adr/0004-move-orders-to-postgresql.md", line: null, rule: "stored in the adr/ folder", origin: "rule", file: "taxonomy.yaml" }] },
      },
      {
        direction: "in",
        other: NODE_ARCH,
        edge: { id: "link|doc:2|doc:41", type: "link", from: "doc:2", to: "doc:41", evidence: [{ document_id: 2, source_id: "docs", path: "ARCHITECTURE.md", line: 203, chunk_ordinal: 20, excerpt: "See [[adr/0004-move-orders-to-postgresql]] <b>orders</b>.", rule: "wikilink [[adr/0004-move-orders-to-postgresql]]" }] },
      },
      {
        direction: "out",
        other: NODE_COMMIT,
        edge: { id: "mentions|doc:41|ent:commit:06e4b4d", type: "mentions", from: "doc:41", to: NODE_COMMIT.id, evidence: [{ document_id: 41, source_id: "docs", path: "adr/0004-move-orders-to-postgresql.md", line: 3, excerpt: "commit `06e4b4d`", rule: "hash `06e4b4d` in code" }] },
      },
    ],
    own_chunks: [{ document_id: 41, document_title: "ADR-0004", source_id: "docs", path: "adr/0004-move-orders-to-postgresql.md", ordinal: 0, title: "Context", section_path: ["ADR-0004", "Context"], line_start: 1, line_end: 17, text: "Decision text", reason: "chunk of the document itself" }],
    related_chunks: [{ document_id: 2, document_title: "Architecture", source_id: "docs", path: "ARCHITECTURE.md", ordinal: 20, title: "Orders", section_path: ["Architecture", "Orders"], line_start: 200, line_end: 210, text: "The orders service moved to PostgreSQL.", reason: "chunk that cites this node" }],
    open: { document_id: 41, line: null },
  };
  const markup = html(ui.NodeDetailView, { detail, actions: ACTIONS });
  const t = text(markup);
  for (const expected of [
    "Decision", "explicit", "ADR-0004 — Move orders to PostgreSQL", "docs:adr/0004-move-orders-to-postgresql.md", "Open original source",
    "Expand relations", "Focus", "Relations (3)", "Document category · outgoing (1)", "Written link · incoming (1)",
    "stored in the adr/ folder", "wikilink [[adr/0004-move-orders-to-postgresql]]", "docs:ARCHITECTURE.md:203",
    "hash `06e4b4d` in code", "Chunks that cite this node (1)", "The orders service moved to PostgreSQL.", "Chunks of the document (1)",
  ]) {
    assert.ok(t.includes(expected), `missing: ${expected}\n${t}`);
  }
  assert.ok(!markup.includes("<b>orders</b>"), "a source excerpt is text, not HTML");
});

test("edge detail lists all the evidence", () => {
  const markup = html(ui.EdgeDetailView, {
    detail: {
      edge: { id: "link|doc:2|doc:41", type: "link", from: "doc:2", to: "doc:41", evidence: [
        { document_id: 2, source_id: "docs", path: "ARCHITECTURE.md", line: 203, excerpt: "See [[adr/0004]]", rule: "wikilink [[adr/0004]]" },
        { document_id: 2, source_id: "docs", path: "ARCHITECTURE.md", line: 240, excerpt: "again [[adr/0004]]", rule: "wikilink [[adr/0004]]" },
      ] },
      from: NODE_ARCH,
      to: NODE_ADR,
    },
    actions: ACTIONS,
  });
  const t = text(markup);
  assert.ok(t.includes("Evidence (2)"));
  assert.ok(t.includes("Architecture") && t.includes("ADR-0004 — Move orders to PostgreSQL"));
  assert.ok(t.includes("ARCHITECTURE.md:203") && t.includes("ARCHITECTURE.md:240"));
});

// ---------------------------------------------------------------- MCP

const PANEL = {
  online: true,
  processes: [{ pid: 6847, started_at: "2026-09-25T17:45:00+00:00", last_used_at: "2026-09-25T17:46:00+00:00", client: { name: "claude-code", version: "2.1.231" }, domain: "default" }],
  command: "/home/dev/lore/backend/.venv/bin/lore-mcp",
  transport: "stdio (local; the AI client starts the process)",
  read_only: false,
  memories: {
    allowed: true,
    forget_allowed: true,
    forgotten: 1,
    folder: "/home/dev/acme-shop/memories",
    source: "memories",
    max_per_hour: 30,
    created: 1,
    recent: [
      { at: "2026-09-25T17:47:00+00:00", client: "claude-code 2.1.231", title: "Partitions use UTC", file: "2026-09-25-174700-partitions-use-utc-3f2a9c1b0d4e.md", status: "created", reason: null, document_id: 140, removed: false },
      { at: "2026-09-25T17:49:00+00:00", client: "claude-code 2.1.231", title: "Old", file: "2026-09-25-174900-old-bbbbbbbbbbbb.md", status: "forgotten", reason: "contradicted", document_id: null, removed: true },
      { at: "2026-09-25T17:48:00+00:00", client: "claude-code 2.1.231", title: "Another", file: "2026-09-25-174800-another-aaaaaaaaaaaa.md", status: "created", reason: null, document_id: null, removed: false },
    ],
  },
  tools: [
    { name: "lore_status", description: "Operational status." },
    { name: "lore_get_context", description: "A small context package." },
  ],
  limits: { max_chunks: 8, max_chars_total: 12000 },
  usage: { queries: 19, errors: 6, no_results: 2, chunks: 24, characters: 81764, approx_tokens: 20447, approx_tokens_avoided: 56304, secrets_redacted: 1, by_tool: { lore_get_context: 3 }, token_estimate: "≈ 4 characters per token" },
  clients: [{ client: "claude-code 2.1.231", queries: 19, last: "2026-09-25T17:46:00+00:00" }],
  recent_queries: [
    { id: 9, at: "2026-09-25T17:46:00+00:00", tool: "lore_search", domain: "default", client: "claude-code 2.1.231", query: "session API_TOKEN=[REDACTED: secret value]", documents: 2, chunks: 3, characters: 900, approx_tokens: 225, duration_ms: 4, result: "ok", error: null },
    { id: 8, at: "2026-09-25T17:45:30+00:00", tool: "lore_get_document", domain: "default", client: "claude-code 2.1.231", query: "docs:people/payroll.md", documents: 0, chunks: 0, characters: 0, approx_tokens: 0, duration_ms: 1, result: "error", error: "This document is marked as sensitive and is not delivered through MCP." },
    { id: 7, at: "2026-09-25T17:45:00+00:00", tool: "lore_search", domain: "default", client: "claude-code 2.1.231", query: "kubernetes", documents: 0, chunks: 0, characters: 0, approx_tokens: 0, duration_ms: 2, result: "no_results", error: null },
  ],
};

test("MCP page shows status, clients, queries, tools, usage and how to connect", () => {
  const t = text(html(ui.McpPanelView, { panel: PANEL, now: NOW }));
  for (const expected of [
    "MCP online", "read + create/forget memories", "claude-code 2.1.231", "Queries 19", "6 with errors · 2 without results",
    "Chunks delivered 24", "≈ 20,447 tokens", "≈ 56,304", "lore_status", "lore_get_context", "3 uses", "Recent clients",
    "Recent queries", "[REDACTED: secret value]", "no results",
    "claude mcp add --scope user lore -- /home/dev/lore/backend/.venv/bin/lore-mcp", "codex mcp add lore --",
    "Chunks per response 8", "Memories written through MCP", "up to 30 memories per hour", "unverified", "Partitions use UTC",
    "2026-09-25-174700-partitions-use-utc-3f2a9c1b0d4e.md", "indexed", "waiting for indexing", "lore_forget",
    "forgotten (trash)", "Forgotten so far: 1", "Only lore_remember (creates a new .md file) and lore_forget",
  ]) {
    assert.ok(t.includes(expected), `missing: ${expected}\n${t}`);
  }
  assert.ok(!t.includes("read-only"));
  const readOnly = text(
    html(ui.McpPanelView, {
      panel: { ...PANEL, read_only: true, memories: { ...PANEL.memories, allowed: false, recent: [] } },
      now: NOW,
    }),
  );
  for (const expected of ["read-only", "lore_remember is off", "No tool writes, moves or deletes"]) {
    assert.ok(readOnly.includes(expected), `missing: ${expected}\n${readOnly}`);
  }
  const idle = text(html(ui.McpPanelView, { panel: { ...PANEL, online: false, processes: [] }, now: NOW }));
  assert.ok(idle.includes("No MCP client connected right now"));
  assert.ok(!idle.includes("MCP online"));
});

test("context preview shows origin, size, decision states and what was left out", () => {
  const t = text(
    html(ui.ContextPreview, {
      pack: {
        task: "Move the orders database to PostgreSQL",
        search_terms: ["move", "orders", "postgresql"],
        search_mode: "any",
        chunks: [{ content: "…", truncated: true, source: "docs:adr/0004-move-orders-to-postgresql.md", heading: "ADR-0004 › Context", document_id: 41, chunk_id: 7, chunk_index: 0, lines: [1, 17], score: 1 }],
        sources: [],
        decisions: [
          { code: "ADR-0004", title: "Move orders to PostgreSQL", state: "active", date: null, document_id: 41, source: "docs:adr/0004-move-orders-to-postgresql.md", why: "x", superseded_by: [], cited_by_later_decisions: [] },
          { code: "ADR-0001", title: "Use MySQL 8", state: "superseded", date: null, document_id: 2, source: "docs:adr/0001-use-mysql.md", why: "x", superseded_by: ["ADR-0004"], cited_by_later_decisions: [], declared_status: "Superseded by ADR-0004" },
          { code: "ADR-0005", title: "GraphQL gateway", state: "proposed", date: null, document_id: 5, source: "docs:adr/0005-graphql-gateway.md", why: "x", superseded_by: [], cited_by_later_decisions: [], declared_status: "Proposed" },
        ],
        memories: [],
        entities: [],
        returned: { chunks: 1, documents: 1, characters: 1200, approx_tokens: 300 },
        available: { matching_chunks: 6, matching_documents: 3 },
        omitted: { chunk_limit: 5, by_privacy: 0 },
        more_available: true,
        note: "5 chunks were left out",
        savings: { full_documents_characters: 3600, approx_tokens_avoided: 600, basis: "x" },
        warnings: ["Full-text search only"],
      },
    }),
  );
  for (const expected of [
    "1 chunk from 1 document", "≈ 300 tokens", "≈ 600 tokens avoided", "docs:adr/0004-move-orders-to-postgresql.md", "truncated",
    "ADR-0004 active", "ADR-0001 superseded", "ADR-0005 proposed", "5 over the chunk limit", "Full-text search only",
  ]) {
    assert.ok(t.includes(expected), `missing: ${expected}\n${t}`);
  }
});
