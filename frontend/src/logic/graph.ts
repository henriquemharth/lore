// What the graph shows: type filters, suggestion policy, focus and expansions.
// Erasable TypeScript syntax only: runs directly under `node --test`.
import type { EdgeType, Graph, GraphEdge, GraphNode, NodeType } from "../types.ts";
import { normalize } from "./format.ts";

export type SuggestionPolicy = "none" | "primary" | "all";

export interface GraphFilters {
  nodeTypes: Set<NodeType>;
  edgeTypes: Set<EdgeType>;
  suggestions: SuggestionPolicy;
  source: string | null;
  category: string | null;
}

export function defaultFilters(): GraphFilters {
  return {
    nodeTypes: new Set<NodeType>(["root", "category", "document", "decision"]),
    edgeTypes: new Set<EdgeType>(["subcategory", "category", "link", "mentions"]),
    suggestions: "primary",
    source: null,
    category: null,
  };
}

export const ENTITIES: NodeType[] = ["commit", "migration", "hash", "file"];

export interface GraphIndex {
  byId: Map<string, GraphNode>;
  byEdge: Map<string, GraphEdge>;
  adjacency: Map<string, GraphEdge[]>;
}

export function indexGraph(graph: Graph): GraphIndex {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const byEdge = new Map(graph.edges.map((e) => [e.id, e]));
  const adjacency = new Map<string, GraphEdge[]>();
  for (const e of graph.edges) {
    for (const end of [e.from, e.to]) {
      const list = adjacency.get(end) ?? [];
      list.push(e);
      adjacency.set(end, list);
    }
  }
  return { byId, byEdge, adjacency };
}

/** Is the edge shown? Its type is checked and, for a suggested category, the policy allows it. */
export function edgePasses(e: GraphEdge, filters: GraphFilters): boolean {
  if (!filters.edgeTypes.has(e.type)) return false;
  if (e.type === "category" && e.confidence === "suggestion") {
    if (filters.suggestions === "none") return false;
    if (filters.suggestions === "primary") return Boolean(e.primary);
  }
  return true;
}

function withinCategory(category: string | undefined, filter: string): boolean {
  return Boolean(category) && (category === filter || category!.startsWith(`${filter}/`));
}

/** Does the node pass the filters (focus and expansion aside)? */
export function nodePasses(n: GraphNode, filters: GraphFilters): boolean {
  if (!filters.nodeTypes.has(n.type)) return false;
  if (n.document_id !== undefined) {
    if (filters.source && n.source_id !== filters.source) return false;
    if (filters.category && !withinCategory(n.category, filters.category)) return false;
  }
  if (n.type === "category" && filters.category) {
    const c = n.category ?? "";
    return withinCategory(c, filters.category) || filters.category.startsWith(`${c}/`);
  }
  return true;
}

export interface Visible {
  nodes: Set<string>;
  edges: Set<string>;
}

export function computeVisible(
  index: GraphIndex,
  filters: GraphFilters,
  expanded: Set<string>,
  focus: { id: string; depth: number } | null,
): Visible {
  const nodes = new Set<string>();
  const other = (e: GraphEdge, id: string) => (e.from === id ? e.to : e.from);

  if (focus && index.byId.has(focus.id)) {
    nodes.add(focus.id);
    let frontier = [focus.id];
    for (let level = 0; level < focus.depth; level++) {
      const next: string[] = [];
      for (const id of frontier) {
        for (const e of index.adjacency.get(id) ?? []) {
          const neighbor = other(e, id);
          const node = index.byId.get(neighbor);
          if (!node || nodes.has(neighbor) || !edgePasses(e, filters)) continue;
          if (!filters.nodeTypes.has(node.type) && !expanded.has(id) && id !== focus.id) continue;
          nodes.add(neighbor);
          next.push(neighbor);
        }
      }
      frontier = next;
    }
  } else {
    for (const n of index.byId.values()) if (nodePasses(n, filters)) nodes.add(n.id);
  }

  // Expanding shows the node's neighbors through any allowed edge, even of a hidden type.
  for (const id of expanded) {
    if (!index.byId.has(id)) continue;
    nodes.add(id);
    for (const e of index.adjacency.get(id) ?? []) if (edgePasses(e, filters)) nodes.add(other(e, id));
  }

  const edges = new Set<string>();
  for (const e of index.byEdge.values()) {
    if (nodes.has(e.from) && nodes.has(e.to) && edgePasses(e, filters)) edges.add(e.id);
  }

  // A loose entity (no visible edge) does not help reading the graph: drop it unless it was asked for.
  const connected = new Set<string>();
  for (const id of edges) {
    const e = index.byEdge.get(id)!;
    connected.add(e.from);
    connected.add(e.to);
  }
  for (const id of [...nodes]) {
    const n = index.byId.get(id)!;
    const requested = expanded.has(id) || focus?.id === id;
    if (ENTITIES.includes(n.type) && !connected.has(id) && !requested) nodes.delete(id);
  }
  return { nodes, edges };
}

/** Nodes whose label, code, key or path matches the term: exact, prefix, then substring. */
export function findNodes(graph: Graph, term: string, limit = 8): GraphNode[] {
  const target = normalize(term.trim());
  if (!target) return [];
  const scored: { node: GraphNode; points: number }[] = [];
  for (const n of graph.nodes) {
    const keys = [n.label, n.code, n.key, n.path, n.name, n.category]
      .filter((v): v is string => Boolean(v))
      .map(normalize);
    let points = 0;
    if (keys.includes(target)) points = 3;
    else if (keys.some((k) => k.startsWith(target))) points = 2;
    else if (keys.some((k) => k.includes(target))) points = 1;
    if (points) scored.push({ node: n, points });
  }
  scored.sort((a, b) => b.points - a.points || b.node.degree - a.node.degree || a.node.label.localeCompare(b.node.label, "en"));
  return scored.slice(0, limit).map((s) => s.node);
}

export function countByType<T extends string>(items: { type: T }[]): Partial<Record<T, number>> {
  const out: Partial<Record<T, number>> = {};
  for (const i of items) out[i.type] = (out[i.type] ?? 0) + 1;
  return out;
}

/** Short label for drawing (the full name is in the details panel). */
export function shortLabel(n: GraphNode): string {
  if (n.type === "decision") return n.code ?? n.label.slice(0, 10);
  if (n.type === "document") return (n.name ?? n.label).replace(/\.md$/i, "").slice(0, 26);
  if (n.type === "file") return (n.key ?? n.label).split("/").slice(-1)[0];
  return n.label.length > 28 ? `${n.label.slice(0, 27)}…` : n.label;
}

export const NODE_TYPE_NAMES: Record<NodeType, string> = {
  root: "Root",
  category: "Category",
  document: "Document",
  decision: "Decision",
  commit: "Commit",
  migration: "Migration",
  hash: "Identifier",
  file: "Cited file",
};

export const EDGE_TYPE_NAMES: Record<EdgeType, string> = {
  subcategory: "Subcategory (taxonomy)",
  category: "Document category",
  link: "Written link",
  mentions: "Mention in text",
};
