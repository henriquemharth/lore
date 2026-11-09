// Builds the explorer tree (root → categories → files) from the document list.
// Erasable TypeScript syntax only: runs directly under `node --test`.
import type { Confidence, DocumentSummary } from "../types.ts";
import { normalize } from "./format.ts";

export const UNCATEGORIZED = "Uncategorized";

export interface TreeNode {
  key: string; // "Architecture/Security", "doc:12:<category>" or "" (root)
  label: string;
  type: "root" | "category" | "document";
  children: TreeNode[];
  document?: DocumentSummary;
  total: number; // distinct documents below this node
  alert: boolean; // an error or pending change below
  confidence?: Confidence; // document: why it is in THIS category
  reason?: string;
  suggestions?: number; // category: how many documents are here only by suggestion
}

export interface TreeOptions {
  secondary?: boolean; // a document also appears under its tag (suggested) categories
  filter?: string;
  showEmpty?: boolean; // taxonomy categories without documents appear (with 0)
}

export function buildTree(
  documents: DocumentSummary[],
  categoryOrder: string[],
  rootLabel = "lore",
  options: TreeOptions = {},
): TreeNode {
  const root: TreeNode = { key: "", label: rootLabel, type: "root", children: [], total: 0, alert: false };
  const byKey = new Map<string, TreeNode>([["", root]]);
  const filter = normalize(options.filter ?? "").trim();

  const ensureCategory = (path: string): TreeNode => {
    const existing = byKey.get(path);
    if (existing) return existing;
    const parts = path.split("/");
    const parent = parts.length > 1 ? ensureCategory(parts.slice(0, -1).join("/")) : root;
    const node: TreeNode = {
      key: path,
      label: parts[parts.length - 1],
      type: "category",
      children: [],
      total: 0,
      alert: false,
    };
    parent.children.push(node);
    byKey.set(path, node);
    return node;
  };

  if (options.showEmpty && !filter) for (const c of categoryOrder) ensureCategory(c);

  for (const doc of documents) {
    if (doc.status === "removed") continue;
    if (filter && !normalize(`${doc.name} ${doc.title ?? ""} ${doc.path}`).includes(filter)) continue;
    const categories = options.secondary ? unique([doc.category, ...doc.categories]) : [doc.category];
    for (const category of categories) {
      const detail = doc.categories_detail?.find((a) => a.category === category);
      const confidence = detail?.confidence ?? (category === doc.category ? doc.category_confidence : "suggestion");
      ensureCategory(category || UNCATEGORIZED).children.push({
        key: `doc:${doc.id}:${category}`,
        label: doc.name,
        type: "document",
        children: [],
        document: doc,
        total: 1,
        alert: doc.status === "error" || doc.pending !== null,
        confidence,
        reason: detail?.reason,
      });
    }
  }

  const position = (key: string): number => {
    if (key === UNCATEGORIZED) return Number.MAX_SAFE_INTEGER;
    const exact = categoryOrder.indexOf(key);
    if (exact >= 0) return exact;
    const descendant = categoryOrder.findIndex((c) => c.startsWith(`${key}/`));
    return descendant >= 0 ? descendant : categoryOrder.length;
  };

  const finish = (node: TreeNode): Set<number> => {
    const ids = new Set<number>();
    let suggestions = 0;
    for (const child of node.children) {
      if (child.type === "document" && child.document) {
        ids.add(child.document.id);
        if (child.confidence === "suggestion") suggestions += 1;
      } else {
        for (const id of finish(child)) ids.add(id);
        suggestions += child.suggestions ?? 0;
      }
      node.alert ||= child.alert;
    }
    if (node.type !== "document") node.suggestions = suggestions;
    node.children.sort((a, b) => {
      if (a.type !== b.type) return a.type === "category" ? -1 : 1;
      if (a.type === "category") return position(a.key) - position(b.key) || a.label.localeCompare(b.label, "en");
      return a.label.localeCompare(b.label, "en", { numeric: true, sensitivity: "base" });
    });
    if (node.type !== "document") node.total = ids.size;
    return ids;
  };
  finish(root);
  return root;
}

/** Categories above a category, itself included: "A/B/C" → ["A", "A/B", "A/B/C"]. */
export function ancestors(category: string): string[] {
  const parts = category.split("/");
  return parts.map((_, i) => parts.slice(0, i + 1).join("/"));
}

/** Every category key in the tree (for "expand all"). */
export function categoryKeys(node: TreeNode): string[] {
  const out: string[] = [];
  const visit = (n: TreeNode) => {
    if (n.type === "category") out.push(n.key);
    n.children.forEach(visit);
  };
  visit(node);
  return out;
}

function unique(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))];
}
