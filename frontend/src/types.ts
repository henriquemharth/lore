// Shapes returned by the /api/lore HTTP API (see docs/CONTRACT.md, section 6).

export type DocumentStatus = "indexed" | "error" | "removed";
export type PendingType = "new" | "changed" | "restored" | "reprocess" | "removed" | "error";
export type RunItemEvent = "new" | "changed" | "reprocessed" | "removed" | "restored" | "error" | "reclassified";
export type DecisionState = "active" | "proposed" | "superseded" | "deprecated" | "undeclared";

export interface PendingChange {
  source_id: string;
  path: string;
  type: PendingType;
  detail: string | null;
}

export interface RunItem {
  document_id: number | null;
  source_id: string;
  path: string;
  event: RunItemEvent;
  detail: string | null;
}

export interface Totals {
  discovered: number;
  new: number;
  changed: number;
  reprocessed: number;
  restored: number;
  removed: number;
  errors: number;
  reclassified: number;
  unchanged: number;
  documents: number;
  chunks: number;
}

export type RunStatus = "running" | "completed" | "failed" | "interrupted";

export interface Run {
  id: number;
  origin: "api" | "cli" | "scheduled" | "test";
  full: boolean;
  status: RunStatus;
  started_at: string;
  finished_at: string | null;
  totals: Partial<Totals>;
  error: string | null;
  items?: RunItem[];
}

export interface CategoryCount {
  category: string;
  documents: number;
  explicit: number;
  suggestions: number;
  tagged: number;
}

export type Confidence = "explicit" | "suggestion";

export interface CategoryAssignment {
  category: string;
  primary: boolean;
  confidence: Confidence;
  origin: "frontmatter" | "rule" | "keyword" | "none";
  reason: string;
  line: number | null;
}

export interface SourceStatus {
  id: string;
  name: string;
  type: "folder" | "git";
  domain: string;
  root: string;
  ref: string | null;
  ref_commit: string | null;
  available: boolean | null;
  warnings: string[];
  checked_at: string | null;
  documents: number;
}

export interface DocumentError {
  document_id: number;
  source_id: string;
  path: string;
  error: string;
  kind: "read" | "processing" | null;
}

export interface Status {
  version: string;
  read_only: boolean;
  documents: { total: number; indexed: number; with_errors: number; removed: number };
  chunks: number;
  links: { total: number; resolved: number };
  last_run: Run | null;
  last_check: string | null;
  run_in_progress: { id: number; origin: string; started_at: string } | null;
  pending: PendingChange[];
  errors: DocumentError[];
  categories: CategoryCount[];
  sources: SourceStatus[];
  domains?: { name: string; description: string | null; sources: string[] }[];
  search: { text: boolean; language?: string; semantic: { available: boolean; reason: string } };
  sync_seconds: number;
  taxonomy: { root: string; categories: string[] };
}

export interface DocumentSummary {
  id: number;
  source_id: string;
  path: string;
  name: string;
  size: number | null;
  hash: string | null;
  modified_at: string | null;
  title: string | null;
  category: string;
  categories: string[];
  category_confidence?: Confidence;
  categories_detail?: CategoryAssignment[];
  status: DocumentStatus;
  error: string | null;
  total_chunks: number;
  version: number;
  indexed_at: string | null;
  removed_at: string | null;
  pending: PendingType | null;
}

export interface Heading {
  level: number;
  text: string;
  line: number;
  anchor: string;
}

export interface Chunk {
  id: number;
  ordinal: number;
  title: string;
  section_path: string[];
  sections: string[];
  level: number;
  part: number;
  parts: number;
  line_start: number;
  line_end: number;
  char_start: number;
  char_end: number;
  size: number;
  hash: string;
  text: string;
  position_percent: number;
}

export interface OutgoingLink {
  line: number;
  type: "wikilink" | "embed" | "markdown";
  target: string;
  anchor: string | null;
  text: string | null;
  target_document_id: number | null;
  target_title: string | null;
}

export interface IncomingLink {
  document_id: number;
  title: string | null;
  source_id: string;
  path: string;
}

export interface DocumentVersion {
  version: number;
  event: "new" | "changed" | "removed" | "restored";
  hash: string | null;
  size: number | null;
  modified_at: string | null;
  run_id: number | null;
  recorded_at: string;
}

export interface DocumentDetail extends Omit<DocumentSummary, "pending"> {
  source_name: string;
  source_type: "folder" | "git" | null;
  frontmatter: Record<string, unknown> | null;
  metadata: Record<string, string>;
  headings: Heading[];
  warnings: string[];
  error_kind: string | null;
  body_start: number;
  total_lines: number;
  discovered_at: string;
  processor_version: string | null;
  content: string;
  chunks: Chunk[];
  links_out: OutgoingLink[];
  links_in: IncomingLink[];
  versions: DocumentVersion[];
  pending: PendingType | null;
}

export interface SearchResult {
  chunk_id: number;
  chunk_ordinal: number;
  document_id: number;
  document_title: string | null;
  source_id: string;
  path: string;
  category: string;
  title: string;
  section_path: string[];
  line_start: number;
  line_end: number;
  excerpt: string;
  score: number;
}

export interface SearchResponse {
  query: string;
  expression: string;
  mode: "all" | "any";
  total_chunks: number;
  total_documents: number;
  results: SearchResult[];
  markers: { start: string; end: string };
  requested_mode?: "text" | "hybrid";
  used_mode: string;
  warning?: string;
}

export interface AuditEntry {
  id: number;
  at: string;
  action: string;
  origin: string;
  ip: string | null;
  agent: string | null;
  details: Record<string, unknown>;
}

export interface ReindexSummary {
  run_id: number | null;
  recorded: boolean;
  origin?: string;
  full?: boolean;
  status: string;
  started_at?: string;
  finished_at?: string;
  duration_ms: number;
  totals: Totals;
  unavailable_sources: string[];
  errors: { source_id: string; path: string; error: string }[];
}

// ---------------------------------------------------------------- graph

export type NodeType = "root" | "category" | "document" | "decision" | "commit" | "migration" | "hash" | "file";
export type EdgeType = "subcategory" | "category" | "link" | "mentions";

export interface GraphNode {
  id: string;
  type: NodeType;
  label: string;
  degree: number;
  detail?: string;
  document_id?: number;
  source_id?: string;
  path?: string;
  name?: string;
  category?: string;
  confidence?: Confidence;
  status?: DocumentStatus;
  code?: string | null;
  key?: string;
}

export interface GraphEdge {
  id: string;
  type: EdgeType;
  from: string;
  to: string;
  confidence?: Confidence;
  primary?: boolean;
  total_evidence: number;
}

export interface Evidence {
  document_id?: number;
  source_id?: string;
  path?: string;
  line?: number | null;
  chunk_ordinal?: number | null;
  excerpt?: string | null;
  rule: string;
  origin?: string;
  file?: string | null;
}

export interface EdgeWithEvidence extends Omit<GraphEdge, "total_evidence"> {
  evidence: Evidence[];
}

export interface Graph {
  root: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  counts: { nodes: Partial<Record<NodeType, number>>; edges: Partial<Record<EdgeType, number>> };
  types: { nodes: NodeType[]; edges: EdgeType[] };
}

export interface RelatedChunk {
  document_id: number;
  document_title: string | null;
  source_id: string;
  path: string;
  ordinal: number;
  title: string;
  section_path: string[];
  line_start: number;
  line_end: number;
  text: string;
  reason: string;
}

export interface NodeDetail {
  node: GraphNode;
  relations: { edge: EdgeWithEvidence; direction: "out" | "in"; other: GraphNode }[];
  own_chunks: RelatedChunk[];
  related_chunks: RelatedChunk[];
  open: { document_id: number; line: number | null } | null;
}

export interface EdgeDetail {
  edge: EdgeWithEvidence;
  from: GraphNode;
  to: GraphNode;
}

// ---------------------------------------------------------------- MCP

export interface McpQuery {
  id: number;
  at: string;
  tool: string | null;
  domain?: string | null;
  client: string;
  query: string | null;
  documents: number;
  chunks: number;
  characters: number | null;
  approx_tokens: number | null;
  duration_ms: number | null;
  result: "ok" | "no_results" | "error" | null;
  error: string | null;
}

export interface McpMemory {
  at: string;
  client: string;
  title: string | null;
  file: string;
  status: "created" | "already_existed" | "forgotten" | null;
  reason?: string | null;
  document_id: number | null;
  removed: boolean;
}

export interface McpPanel {
  online: boolean;
  processes: {
    pid: number;
    started_at: string | null;
    last_used_at: string | null;
    client: { name: string | null; version: string | null } | null;
    domain?: string | null;
  }[];
  command: string;
  transport: string;
  read_only: boolean;
  memories: {
    allowed: boolean;
    forget_allowed: boolean;
    folder: string;
    source: string | null;
    max_per_hour: number;
    created: number;
    forgotten: number;
    recent: McpMemory[];
  };
  tools: { name: string; description: string }[];
  limits: Record<string, number>;
  usage: {
    queries: number;
    errors: number;
    no_results: number;
    chunks: number;
    characters: number;
    approx_tokens: number;
    approx_tokens_avoided: number;
    secrets_redacted: number;
    by_tool: Record<string, number>;
    token_estimate: string;
  };
  clients: { client: string; queries: number; last: string | null }[];
  recent_queries: McpQuery[];
}

export interface ContextPackage {
  task: string;
  search_terms: string[];
  search_mode?: "all" | "any";
  chunks: {
    content: string;
    truncated: boolean;
    source: string;
    heading: string;
    document_id: number;
    chunk_id: number;
    chunk_index: number;
    lines: [number, number];
    score: number;
  }[];
  sources: {
    document_id: number;
    source: string;
    title: string | null;
    category: string;
    category_confidence: Confidence;
    is_memory: boolean;
  }[];
  decisions: {
    code: string;
    title: string;
    state: DecisionState;
    date: string | null;
    document_id: number;
    source: string;
    why: string;
    superseded_by?: string[];
    cited_by_later_decisions?: string[];
    declared_status?: string;
  }[];
  memories?: { source: string; chunk_id: number; heading: string; verified: boolean | null }[];
  entities: { type: string; key: string; source: string; line: number }[];
  returned: { chunks: number; documents: number; characters: number; approx_tokens: number };
  available?: { matching_chunks: number; matching_documents: number };
  omitted: Record<string, number>;
  more_available: boolean;
  note: string | null;
  savings: { full_documents_characters: number; approx_tokens_avoided: number; basis: string };
  warnings: string[];
}
