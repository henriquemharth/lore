# lore — knowledge graph and categories

## Golden rule

**Every relation has an origin.** The graph has only four edge types and each one carries its evidence
(file, line, excerpt, rule). There are no edges by topic similarity, co-occurrence or guesswork.

| Edge | From → to | Origin |
|---|---|---|
| `subcategory` | category → category/root | structure declared in `taxonomy.yaml` |
| `category` | document → category | taxonomy rule (explicit or suggestion), frontmatter, or keyword (always a suggestion) |
| `link` | document → document | wikilink `[[…]]` or Markdown link written in the text |
| `mentions` | document → decision/document/entity | a decision code in the text; a hash, migration or path in backticks |

Nodes: the root (labelled with `taxonomy.yaml`'s `root`), categories, documents, decisions (decision-record
files) and cited entities:

- **decision** — a file recognized as a decision record: a code in the file name (`DEC-001`, `ADR-0001`,
  or the `decisions.patterns` of `sources.yaml`) or the numbered layout (`adr/0001-title.md`,
  `docs/decisions/0001-*.md` → `ADR-0001`). A code cited in the text without a file becomes a decision
  node "without a document".
- **commit** — 7–11 or 40 hexadecimal characters in backticks (stored by the first 7).
- **migration** — 12 hexadecimal characters in backticks on a line that talks about migrations or revisions
  (Alembic-style revision ids; context words such as migration, alembic, down_revision, revision, head,
  rails, django, flyway, liquibase, and the Portuguese migração/revisão/cabeça).
- **hash** — 12 hexadecimal characters without such context.
- **file** — a path in backticks with a folder and a code/config extension (`.py`, `.ts`, `.sql`, `.yaml`,
  `.md`…). A `.md` path that exists among the sources becomes a link to that document.

Only text outside code blocks counts; data files (`.xlsx`, `.docx`, images) and absolute or `~` paths do
not become entities. A document that cites itself does not get an edge.

## Categories: explicit × suggestion

Each category of each document stores its `confidence` and `reason`:

- **explicit** — the source itself says so: frontmatter (`category`, `categories`, `tags`), the source
  (e.g. the memories folder), the folder where the author stored it (`adr/`, `runbooks/`…) or a file name
  that names the subject (`ARCHITECTURE`, `SECURITY`…), as declared by a rule with `confidence: explicit`;
- **suggestion** — an interpretation: rules marked `confidence: suggestion` (e.g. "server, backup and
  network are infrastructure") and keywords (`tags`) in the file name, title or headings.

A document without a rule gets its best suggestion, **marked as a suggestion**; with none, it goes to
`Uncategorized`. The UI marks suggestions (dashed) in the tree and in the graph, shows the reason of each
category on the document, and counts explicit × suggestions per category. To confirm a suggestion, edit
`taxonomy.yaml` (an `explicit` rule) or add `category:` to the document's frontmatter — lore never writes to
the `.md` files. Taxonomy changes apply on the next run without reprocessing files ("reclassified").

## API

- `GET /api/lore/graph` — nodes and edges without the evidence (`total_evidence` per edge), counts, types.
- `GET /api/lore/graph/node?id=` — node, relations (with evidence), its own chunks, chunks that cite it, and
  where to open the source.
- `GET /api/lore/graph/edge?id=` — edge with all its evidence and both ends.

Shapes: [CONTRACT.md](CONTRACT.md#68-get-graph). The MCP tool `lore_get_entity` exposes the same graph,
limited to the process domain.

## Graph page (web UI)

Zoom and pan (wheel/trackpad, +/− buttons, fit, re-layout) · click a node = details · click an edge =
evidence · double click = expand relations · "Focus" = only the neighbourhood (1 or 2 hops) · search by
name/code/path · filters by node type, edge type, suggestion policy, source and category · "Open original
source" jumps to the content at the line · chunks of the document and chunks that cite the node. Commits,
migrations and files start hidden to keep the picture readable; they appear when their type is enabled or a
node is expanded.
