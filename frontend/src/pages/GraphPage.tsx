import { Crosshair, Search, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { useQuery } from "../api.ts";
import { COLORS, GraphCanvas } from "../components/GraphCanvas.tsx";
import { EdgeDetailView, NodeDetailView } from "../components/GraphDetails.tsx";
import type { DetailActions } from "../components/GraphDetails.tsx";
import { QueryState } from "../components/ui.tsx";
import { EDGE_TYPE_NAMES, NODE_TYPE_NAMES, computeVisible, countByType, defaultFilters, findNodes, indexGraph } from "../logic/graph.ts";
import type { GraphFilters, SuggestionPolicy } from "../logic/graph.ts";
import type { EdgeDetail, EdgeType, Graph, NodeDetail, NodeType, Status } from "../types.ts";

export function GraphPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const query = useQuery<Graph>("/graph");
  const status = useQuery<Status>("/status");
  const graph = query.data;
  const index = useMemo(() => (graph ? indexGraph(graph) : null), [graph]);

  const [filters, setFilters] = useState<GraphFilters>(defaultFilters);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [focus, setFocus] = useState<{ id: string; depth: number } | null>(null);
  const [term, setTerm] = useState("");
  const [centerOn, setCenterOn] = useState<{ id: string; at: number } | null>(null);
  const [layoutVersion, setLayoutVersion] = useState(0);

  const selected = params.get("node");
  const selectedEdge = params.get("edge");

  // Opens the graph already focused (coming from the explorer: /graph?node=doc:12&focus=1).
  useEffect(() => {
    const requested = params.get("focus") === "1" ? params.get("node") : null;
    if (requested) setFocus({ id: requested, depth: 1 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const visible = useMemo(
    () => (index ? computeVisible(index, filters, expanded, focus) : { nodes: new Set<string>(), edges: new Set<string>() }),
    [index, filters, expanded, focus],
  );
  useEffect(() => setLayoutVersion((v) => v + 1), [index, filters, expanded, focus]);

  const matches = useMemo(() => (graph && term.trim() ? findNodes(graph, term, 8) : []), [graph, term]);
  const matchSet = useMemo(() => new Set(matches.map((n) => n.id)), [matches]);

  const nodeDetail = useQuery<NodeDetail>(selected ? `/graph/node?id=${encodeURIComponent(selected)}` : null);
  const edgeDetail = useQuery<EdgeDetail>(selectedEdge ? `/graph/edge?id=${encodeURIComponent(selectedEdge)}` : null);

  const select = (id: string) => {
    if (!visible.nodes.has(id)) setExpanded((current) => new Set(current).add(id));
    setParams({ node: id }, { replace: true });
    setCenterOn({ id, at: Date.now() });
  };

  const actions: DetailActions = {
    onOpenSource: (documentId, line, chunk) => {
      const extra = chunk != null ? `&chunk=${chunk}` : line ? `&line=${line}` : "";
      navigate(`/explorer/${documentId}?tab=original${extra}`);
    },
    onSelect: select,
    onExpand: (id) =>
      setExpanded((current) => {
        const next = new Set(current);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      }),
    onFocus: (id) => {
      setFocus({ id, depth: focus?.id === id ? focus.depth : 1 });
      setParams({ node: id }, { replace: true });
    },
  };

  const toggle = <T,>(set: Set<T>, value: T): Set<T> => {
    const next = new Set(set);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    return next;
  };

  const nodeCounts = graph ? countByType(graph.nodes) : {};
  const edgeCounts = graph ? countByType(graph.edges) : {};
  const dark = typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  const colors = dark ? COLORS.dark : COLORS.light;
  const focusNode = focus && index?.byId.get(focus.id);

  return (
    <QueryState loading={query.loading} error={query.error} onRetry={query.reload}>
      {graph && index && (
        <div className="grid min-h-0 flex-1 gap-3 xl:grid-cols-[270px_minmax(0,1fr)_390px] lg:grid-cols-[250px_minmax(0,1fr)]">
          <aside className="panel flex min-h-0 flex-col gap-3 overflow-auto p-3 lg:max-h-[calc(100vh-88px)]" aria-label="Graph filters">
            <div>
              <label className="relative block">
                <span className="sr-only">Find in the graph</span>
                <Search size={14} className="absolute top-2 left-2 text-fg-3" aria-hidden />
                <input
                  className="field w-full pl-7"
                  placeholder="Find a node (ADR-0004, 60041bb, Architecture…)"
                  value={term}
                  onChange={(e) => setTerm(e.target.value)}
                />
              </label>
              {term.trim() && (
                <ul className="mt-1 flex flex-col" aria-label="Matching nodes">
                  {matches.length === 0 && <li className="px-1 py-1 text-[12px] text-fg-3">No node with that name.</li>}
                  {matches.map((n) => (
                    <li key={n.id}>
                      <button
                        type="button"
                        className="flex w-full items-center gap-2 rounded px-1 py-1 text-left hover:bg-surface-2"
                        onClick={() => select(n.id)}
                      >
                        <span className="w-20 shrink-0 text-[11px] text-fg-3">{NODE_TYPE_NAMES[n.type]}</span>
                        <span className="truncate text-[12.5px]">{n.label}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <fieldset>
              <legend className="mb-1 text-[12px] font-semibold text-fg-3 uppercase">Nodes</legend>
              {graph.types.nodes.map((type) => (
                <label key={type} className="flex items-center gap-2 py-0.5 text-[12.5px]">
                  <input
                    type="checkbox"
                    checked={filters.nodeTypes.has(type)}
                    onChange={() => setFilters((f) => ({ ...f, nodeTypes: toggle(f.nodeTypes, type) }))}
                  />
                  <Shape type={type} colors={colors} />
                  <span className="flex-1">{NODE_TYPE_NAMES[type]}</span>
                  <span className="tabular-nums text-fg-3">{nodeCounts[type] ?? 0}</span>
                </label>
              ))}
            </fieldset>

            <fieldset>
              <legend className="mb-1 text-[12px] font-semibold text-fg-3 uppercase">Relations</legend>
              {graph.types.edges.map((type) => (
                <label key={type} className="flex items-center gap-2 py-0.5 text-[12.5px]">
                  <input
                    type="checkbox"
                    checked={filters.edgeTypes.has(type)}
                    onChange={() => setFilters((f) => ({ ...f, edgeTypes: toggle(f.edgeTypes, type) }))}
                  />
                  <Stroke type={type} />
                  <span className="flex-1">{EDGE_TYPE_NAMES[type]}</span>
                  <span className="tabular-nums text-fg-3">{edgeCounts[type] ?? 0}</span>
                </label>
              ))}
            </fieldset>

            <fieldset>
              <legend className="mb-1 text-[12px] font-semibold text-fg-3 uppercase">Suggested categories</legend>
              {(
                [
                  ["none", "Explicit only"],
                  ["primary", "Explicit + suggested primary"],
                  ["all", "All suggestions"],
                ] as [SuggestionPolicy, string][]
              ).map(([value, label]) => (
                <label key={value} className="flex items-center gap-2 py-0.5 text-[12.5px]">
                  <input
                    type="radio"
                    name="suggestions"
                    checked={filters.suggestions === value}
                    onChange={() => setFilters((f) => ({ ...f, suggestions: value }))}
                  />
                  {label}
                </label>
              ))}
              <p className="mt-1 flex items-center gap-2 text-[11.5px] text-fg-3">
                <svg width="26" height="6" aria-hidden>
                  <line x1="0" y1="3" x2="26" y2="3" stroke="var(--color-warn)" strokeWidth="2" strokeDasharray="4 3" />
                </svg>
                dashed orange = automatic suggestion
              </p>
            </fieldset>

            <fieldset className="flex flex-col gap-1.5">
              <legend className="mb-1 text-[12px] font-semibold text-fg-3 uppercase">Filter</legend>
              <select
                className="field"
                aria-label="Source"
                value={filters.source ?? ""}
                onChange={(e) => setFilters((f) => ({ ...f, source: e.target.value || null }))}
              >
                <option value="">All sources</option>
                {(status.data?.sources ?? []).map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
              <select
                className="field"
                aria-label="Category"
                value={filters.category ?? ""}
                onChange={(e) => setFilters((f) => ({ ...f, category: e.target.value || null }))}
              >
                <option value="">All categories</option>
                {(status.data?.taxonomy.categories ?? []).map((c) => (
                  <option key={c} value={c}>
                    {c.replaceAll("/", " › ")}
                  </option>
                ))}
              </select>
            </fieldset>

            {(focus || expanded.size > 0) && (
              <div className="rounded border border-line bg-surface-2 p-2 text-[12.5px]">
                {focusNode && (
                  <div className="flex items-center gap-1.5">
                    <Crosshair size={13} aria-hidden /> Focus: <strong className="truncate">{focusNode.label}</strong>
                    <select
                      className="field ml-auto h-6 px-1"
                      aria-label="Focus depth"
                      value={focus!.depth}
                      onChange={(e) => setFocus({ id: focus!.id, depth: Number(e.target.value) })}
                    >
                      <option value={1}>1 hop</option>
                      <option value={2}>2 hops</option>
                    </select>
                  </div>
                )}
                {expanded.size > 0 && <div className="mt-1">{expanded.size} expanded node(s)</div>}
                <button
                  type="button"
                  className="btn mt-2 h-7"
                  onClick={() => {
                    setFocus(null);
                    setExpanded(new Set());
                  }}
                >
                  <X size={13} aria-hidden /> Back to the overview
                </button>
              </div>
            )}

            <p className="text-[11.5px] leading-4 text-fg-3">
              Every relation comes from the text of the sources (a link, or a mention of a decision, commit, migration or
              file) or from the taxonomy — none is inferred by similarity. Click a relation to see its evidence; double-click
              a node to expand its relations.
            </p>
          </aside>

          <section className="panel relative order-first flex h-[70vh] min-h-[420px] flex-col overflow-hidden lg:order-none lg:h-[calc(100vh-88px)]">
            <div className="flex items-center gap-2 border-b border-line px-3 py-1.5 text-[12px] text-fg-3">
              <span>
                {visible.nodes.size} of {graph.nodes.length} nodes · {visible.edges.size} of {graph.edges.length} relations
              </span>
              {!filters.nodeTypes.has("commit") && (
                <span className="ml-auto">Commits, migrations and files are hidden — check them under “Nodes” or expand a node.</span>
              )}
            </div>
            <div className="min-h-0 flex-1">
              <GraphCanvas
                graph={graph}
                visible={visible}
                selected={selected}
                selectedEdge={selectedEdge}
                found={matchSet}
                expanded={expanded}
                layoutVersion={layoutVersion}
                centerOn={centerOn}
                onNode={(id) => setParams({ node: id }, { replace: true })}
                onEdge={(id) => setParams({ edge: id }, { replace: true })}
                onExpand={actions.onExpand}
                onBackground={() => setParams({}, { replace: true })}
              />
            </div>
          </section>

          <aside className="panel min-h-0 overflow-auto lg:col-span-2 xl:col-span-1 xl:max-h-[calc(100vh-88px)]" aria-label="Details">
            {!selected && !selectedEdge && (
              <div className="p-4 text-fg-3">
                <p className="font-medium text-fg-2">Click a node or a relation.</p>
                <p className="mt-1 text-[12.5px]">
                  You will see where each relation comes from (file, line and excerpt), the related chunks and a button to open
                  the original source.
                </p>
              </div>
            )}
            {selected && (
              <QueryState loading={nodeDetail.loading} error={nodeDetail.error} onRetry={nodeDetail.reload}>
                {nodeDetail.data && <NodeDetailView detail={nodeDetail.data} actions={actions} />}
              </QueryState>
            )}
            {selectedEdge && !selected && (
              <QueryState loading={edgeDetail.loading} error={edgeDetail.error} onRetry={edgeDetail.reload}>
                {edgeDetail.data && <EdgeDetailView detail={edgeDetail.data} actions={actions} />}
              </QueryState>
            )}
          </aside>
        </div>
      )}
    </QueryState>
  );
}

function Shape({ type, colors }: { type: NodeType; colors: { document: string; decision: string; entity: string } }) {
  const ink = "var(--color-fg-2)";
  const s = 12;
  switch (type) {
    case "root":
      return <svg width={s} height={s} aria-hidden><rect x="1" y="2" width="10" height="8" rx="2" fill="var(--color-fg)" /></svg>;
    case "category":
      return <svg width={s} height={s} aria-hidden><rect x="1" y="2" width="10" height="8" rx="2" fill="var(--color-surface-2)" stroke={ink} /></svg>;
    case "document":
      return <svg width={s} height={s} aria-hidden><circle cx="6" cy="6" r="5" fill={colors.document} /></svg>;
    case "decision":
      return <svg width={s} height={s} aria-hidden><polygon points="6,0 12,6 6,12 0,6" fill={colors.decision} /></svg>;
    case "commit":
      return <svg width={s} height={s} aria-hidden><polygon points="3,1 9,1 12,6 9,11 3,11 0,6" fill={colors.entity} /></svg>;
    case "migration":
      return <svg width={s} height={s} aria-hidden><rect x="1" y="1" width="10" height="10" rx="4" ry="2" fill={colors.entity} /></svg>;
    case "hash":
      return <svg width={s} height={s} aria-hidden><polygon points="3,1 9,1 12,6 9,11 3,11 0,6" fill="none" stroke={colors.entity} strokeWidth="2" /></svg>;
    case "file":
      return <svg width={s} height={s} aria-hidden><polygon points="0,1 8,1 12,6 8,11 0,11" fill={colors.entity} /></svg>;
  }
}

function Stroke({ type }: { type: EdgeType }) {
  const dashes: Record<EdgeType, string | undefined> = { subcategory: undefined, category: undefined, link: undefined, mentions: "1.5 2" };
  const color = type === "category" ? "var(--color-brand)" : type === "subcategory" ? "var(--color-fg-3)" : "var(--color-line-strong)";
  return (
    <svg width="22" height="6" aria-hidden>
      <line x1="0" y1="3" x2="22" y2="3" stroke={color} strokeWidth={type === "subcategory" ? 2.5 : 1.8} strokeDasharray={dashes[type]} />
    </svg>
  );
}
