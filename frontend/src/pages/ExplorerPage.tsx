import { ChevronsDownUp, ChevronsUpDown, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { useQuery } from "../api.ts";
import { DocumentTree } from "../components/DocumentTree.tsx";
import { DOCUMENT_TABS, DocumentView } from "../components/DocumentView.tsx";
import type { DocumentTab } from "../components/DocumentView.tsx";
import { QueryState } from "../components/ui.tsx";
import { ancestors, buildTree, categoryKeys } from "../logic/tree.ts";
import type { DocumentDetail, DocumentSummary, Status } from "../types.ts";

export function ExplorerPage() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const documentId = id && /^\d+$/.test(id) ? Number(id) : null;

  const list = useQuery<{ total: number; documents: DocumentSummary[] }>("/documents?limit=5000");
  const status = useQuery<Status>("/status");
  const detail = useQuery<DocumentDetail>(documentId !== null ? `/documents/${documentId}` : null);

  const [filter, setFilter] = useState("");
  const [secondary, setSecondary] = useState(false);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const initialized = useRef(false);

  const order = useMemo(() => status.data?.taxonomy.categories ?? [], [status.data]);
  const rootLabel = status.data?.taxonomy.root ?? "lore";
  const tree = useMemo(
    () => buildTree(list.data?.documents ?? [], order, rootLabel, { secondary, filter, showEmpty: true }),
    [list.data, order, rootLabel, secondary, filter],
  );

  // Opens the top-level categories on first load, the one asked for in the URL and the open document's.
  const requestedCategory = params.get("category");
  const documentCategory = list.data?.documents.find((d) => d.id === documentId)?.category;
  useEffect(() => {
    setOpen((current) => {
      const next = new Set(current);
      if (!initialized.current && tree.children.length > 0) {
        initialized.current = true;
        tree.children.forEach((c) => next.add(c.key));
      }
      if (requestedCategory) ancestors(requestedCategory).forEach((c) => next.add(c));
      if (documentCategory) ancestors(documentCategory).forEach((c) => next.add(c));
      return next.size === current.size ? current : next;
    });
  }, [tree, requestedCategory, documentCategory]);

  const effectiveOpen = useMemo(() => (filter.trim() ? new Set(categoryKeys(tree)) : open), [filter, tree, open]);

  const toggle = (key: string) =>
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const urlTab = params.get("tab") as DocumentTab | null;
  const tab: DocumentTab = DOCUMENT_TABS.some((t) => t.key === urlTab) ? (urlTab as DocumentTab) : "chunks";
  const chunk = params.get("chunk");
  const line = params.get("line");

  const update = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value === null) next.delete(key);
      else next.set(key, value);
    }
    setParams(next, { replace: true });
  };

  return (
    <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[340px_minmax(0,1fr)]">
      <aside className="panel flex min-h-0 flex-col lg:max-h-[calc(100vh-88px)]">
        <div className="flex flex-col gap-2 border-b border-line p-2">
          <label className="relative">
            <span className="sr-only">Filter files</span>
            <Search size={14} className="absolute top-2 left-2 text-fg-3" aria-hidden />
            <input
              className="field w-full pl-7"
              placeholder="Filter by name, title or path"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          </label>
          <div className="flex items-center gap-2 text-[12px] text-fg-2">
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={secondary} onChange={(e) => setSecondary(e.target.checked)} />
              Also show suggested categories
            </label>
            <span className="ml-auto flex gap-1">
              <button
                type="button"
                className="btn h-6 px-1.5"
                title="Expand all"
                aria-label="Expand all"
                onClick={() => setOpen(new Set(categoryKeys(tree)))}
              >
                <ChevronsUpDown size={13} aria-hidden />
              </button>
              <button type="button" className="btn h-6 px-1.5" title="Collapse all" aria-label="Collapse all" onClick={() => setOpen(new Set())}>
                <ChevronsDownUp size={13} aria-hidden />
              </button>
            </span>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          <QueryState loading={list.loading} error={list.error} onRetry={list.reload}>
            {list.data && (
              <DocumentTree
                root={tree}
                open={effectiveOpen}
                selected={documentId}
                onToggle={toggle}
                onSelect={(next) => navigate(`/explorer/${next}${tab !== "chunks" ? `?tab=${tab}` : ""}`)}
              />
            )}
          </QueryState>
        </div>
      </aside>

      <section className="flex min-h-0 flex-col lg:max-h-[calc(100vh-88px)]">
        {documentId === null ? (
          <div className="panel flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center text-fg-3">
            <p className="text-[15px] font-medium text-fg-2">Pick a file in the tree.</p>
            <p>You will see the file's details, its headings, chunks, original content and last index run.</p>
          </div>
        ) : (
          <QueryState loading={detail.loading} error={detail.error} onRetry={detail.reload}>
            {detail.data && (
              <DocumentView
                doc={detail.data}
                tab={tab}
                onTab={(next) => update({ tab: next })}
                activeChunk={chunk !== null ? Number(chunk) : null}
                activeLine={line !== null ? Number(line) : null}
                onChunk={(ordinal) => update({ tab: "original", chunk: String(ordinal), line: null })}
                onGoToLine={(n) => update({ tab: "original", line: String(n), chunk: null })}
                onOpenDocument={(other) => navigate(`/explorer/${other}`)}
                onShowInGraph={(docId) => navigate(`/graph?node=${encodeURIComponent(`doc:${docId}`)}&focus=1`)}
              />
            )}
          </QueryState>
        )}
      </section>
    </div>
  );
}
