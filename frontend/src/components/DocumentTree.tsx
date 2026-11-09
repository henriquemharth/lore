import { BookMarked, ChevronDown, ChevronRight, FileText, Folder, FolderOpen } from "lucide-react";
import type { KeyboardEvent } from "react";

import type { TreeNode } from "../logic/tree.ts";

export interface DocumentTreeProps {
  root: TreeNode;
  open: Set<string>;
  selected?: number | null;
  onToggle: (key: string) => void;
  onSelect: (documentId: number) => void;
}

/** Visual explorer: root → categories → files (a keyboard-accessible tree). */
export function DocumentTree({ root, open, selected, onToggle, onSelect }: DocumentTreeProps) {
  return (
    <div role="tree" aria-label={root.label} className="select-none py-1">
      <div className="flex items-center gap-1.5 px-2 py-1 font-semibold text-fg" role="none">
        <BookMarked size={16} className="text-brand" aria-hidden />
        {root.label}
        <span className="ml-auto text-[12px] font-normal tabular-nums text-fg-3">{root.total}</span>
      </div>
      <p className="flex items-center gap-2 px-2 pb-1 text-[11px] text-fg-3">
        <span>name = explicit category</span>
        <span className="rounded border border-dashed border-warn px-1 text-warn italic">suggestion</span>
        <span>= automatic interpretation</span>
      </p>
      {root.children.length === 0 ? (
        <p className="px-3 py-2 text-fg-3">No documents found.</p>
      ) : (
        <ul role="group">
          {root.children.map((node) => (
            <Branch key={node.key} node={node} depth={1} open={open} selected={selected} onToggle={onToggle} onSelect={onSelect} />
          ))}
        </ul>
      )}
    </div>
  );
}

function Branch({
  node,
  depth,
  open,
  selected,
  onToggle,
  onSelect,
}: Omit<DocumentTreeProps, "root"> & { node: TreeNode; depth: number }) {
  const indent = { paddingLeft: `${8 + (depth - 1) * 14}px` };

  if (node.type === "document" && node.document) {
    const doc = node.document;
    const active = selected === doc.id;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onSelect(doc.id);
      }
    };
    return (
      <li
        role="treeitem"
        aria-selected={active}
        tabIndex={0}
        onClick={() => onSelect(doc.id)}
        onKeyDown={onKeyDown}
        title={`${doc.source_id}:${doc.path}${doc.title ? `\n${doc.title}` : ""}${node.reason ? `\nCategory: ${node.reason}` : ""}`}
        className={`flex h-7 cursor-pointer items-center gap-1.5 pr-2 ${
          active ? "bg-brand-soft font-medium text-brand" : "hover:bg-surface-2"
        }`}
        style={{ ...indent, paddingLeft: `${8 + (depth - 1) * 14 + 16}px` }}
      >
        <FileText size={14} aria-hidden className={active ? "text-brand" : "text-fg-3"} />
        <span className={`truncate ${node.confidence === "suggestion" ? "italic" : ""}`}>{node.label}</span>
        {node.confidence === "suggestion" && (
          <span
            className="shrink-0 rounded border border-dashed border-warn px-1 text-[10.5px] leading-4 text-warn"
            title={`Category suggested automatically${node.reason ? `: ${node.reason}` : ""}`}
          >
            suggestion
          </span>
        )}
        {doc.status === "error" && <span className="ml-auto h-2 w-2 shrink-0 rounded-full bg-danger" title="Indexing error" />}
        {doc.status !== "error" && doc.pending && (
          <span className="ml-auto h-2 w-2 shrink-0 rounded-full bg-warn" title="Changed, waiting for a reindex" />
        )}
      </li>
    );
  }

  const isOpen = open.has(node.key);
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " " || (e.key === "ArrowRight" && !isOpen) || (e.key === "ArrowLeft" && isOpen)) {
      e.preventDefault();
      onToggle(node.key);
    }
  };
  return (
    <li role="treeitem" aria-expanded={isOpen} aria-selected={false}>
      <div
        tabIndex={0}
        onClick={() => onToggle(node.key)}
        onKeyDown={onKeyDown}
        className="flex h-7 cursor-pointer items-center gap-1.5 pr-2 hover:bg-surface-2"
        style={indent}
      >
        {isOpen ? (
          <ChevronDown size={14} aria-hidden className="text-fg-3" />
        ) : (
          <ChevronRight size={14} aria-hidden className="text-fg-3" />
        )}
        {isOpen ? <FolderOpen size={14} aria-hidden className="text-brand" /> : <Folder size={14} aria-hidden className="text-brand" />}
        <span className={`truncate font-medium ${node.total === 0 ? "text-fg-3" : ""}`}>{node.label}</span>
        {node.alert && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-warn" title="An error or pending change below" />}
        {node.total === 0 && (
          <span className="text-[11px] text-fg-3" title="No document has this category">
            empty
          </span>
        )}
        <span
          className="ml-auto text-[12px] tabular-nums text-fg-3"
          title={node.suggestions ? `${node.suggestions} by automatic suggestion` : "all from explicit information"}
        >
          {node.total}
          {node.suggestions ? <span className="text-warn"> · {node.suggestions}◌</span> : null}
        </span>
      </div>
      {isOpen && (
        <ul role="group">
          {node.children.map((child) => (
            <Branch key={child.key} node={child} depth={depth + 1} open={open} selected={selected} onToggle={onToggle} onSelect={onSelect} />
          ))}
        </ul>
      )}
    </li>
  );
}
