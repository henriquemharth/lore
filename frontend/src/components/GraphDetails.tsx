import { ArrowLeft, ArrowRight, Crosshair, ExternalLink, FileText, Network } from "lucide-react";
import type { ReactNode } from "react";

import { EDGE_TYPE_NAMES, NODE_TYPE_NAMES } from "../logic/graph.ts";
import type { EdgeDetail, Evidence, GraphNode, NodeDetail, RelatedChunk } from "../types.ts";
import { Tag } from "./ui.tsx";

export interface DetailActions {
  onOpenSource: (documentId: number, line?: number | null, chunk?: number | null) => void;
  onSelect: (id: string) => void;
  onExpand: (id: string) => void;
  onFocus: (id: string) => void;
}

export function ConfidenceTag({ value }: { value?: string | null }) {
  if (!value) return null;
  return value === "explicit" ? (
    <Tag tone="ok" title="The source itself says so (folder, source, file name or frontmatter)">
      explicit
    </Tag>
  ) : (
    <Tag tone="warn" title="Automatic interpretation (a rule marked as suggestion, or a keyword)">
      suggestion
    </Tag>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-t border-line px-3 py-2">
      <h3 className="mb-1.5 text-[12px] font-semibold tracking-wide text-fg-3 uppercase">{title}</h3>
      {children}
    </section>
  );
}

export function EvidenceItem({ e, actions }: { e: Evidence; actions: DetailActions }) {
  return (
    <li className="rounded border border-line bg-surface-2 px-2 py-1.5">
      <div className="text-[12px] text-fg-2">{e.rule}</div>
      {e.path && (
        <div className="mt-0.5 flex items-center gap-2 text-[12px]">
          <span className="code truncate text-fg-3">
            {e.source_id}:{e.path}
            {e.line ? `:${e.line}` : ""}
          </span>
          {e.document_id !== undefined && (
            <button
              type="button"
              className="link ml-auto shrink-0"
              onClick={() => actions.onOpenSource(e.document_id!, e.line ?? null, e.chunk_ordinal ?? null)}
            >
              open
            </button>
          )}
        </div>
      )}
      {!e.path && e.file && <div className="code mt-0.5 text-[12px] text-fg-3">{e.file}</div>}
      {e.excerpt && (
        <blockquote className="code mt-1 border-l-2 border-line-strong pl-2 text-[12px] break-words text-fg-2">{e.excerpt}</blockquote>
      )}
    </li>
  );
}

function Chunks({ chunks, actions, empty }: { chunks: RelatedChunk[]; actions: DetailActions; empty: string }) {
  if (chunks.length === 0) return <p className="text-[12px] text-fg-3">{empty}</p>;
  return (
    <ul className="flex flex-col gap-1.5">
      {chunks.map((c) => (
        <li key={`${c.document_id}-${c.ordinal}`} className="rounded border border-line px-2 py-1.5">
          <div className="flex items-center gap-2 text-[12px]">
            <FileText size={12} className="shrink-0 text-fg-3" aria-hidden />
            <span className="truncate font-medium">{c.section_path.join(" › ") || c.title}</span>
            <button type="button" className="link ml-auto shrink-0" onClick={() => actions.onOpenSource(c.document_id, null, c.ordinal)}>
              open
            </button>
          </div>
          <div className="code text-[11.5px] text-fg-3">
            {c.source_id}:{c.path} · lines {c.line_start}–{c.line_end}
          </div>
          <p className="mt-1 line-clamp-4 text-[12px] whitespace-pre-wrap text-fg-2">{c.text.slice(0, 400)}</p>
        </li>
      ))}
    </ul>
  );
}

function NodeLink({ node, actions }: { node: GraphNode; actions: DetailActions }) {
  return (
    <button type="button" className="link truncate" title={node.detail ?? node.label} onClick={() => actions.onSelect(node.id)}>
      {node.label}
    </button>
  );
}

export function NodeDetailView({ detail, actions }: { detail: NodeDetail; actions: DetailActions }) {
  const { node, relations } = detail;
  const groups = new Map<string, typeof relations>();
  for (const r of relations) {
    const key = `${r.edge.type}|${r.direction}`;
    groups.set(key, [...(groups.get(key) ?? []), r]);
  }
  return (
    <div className="flex flex-col">
      <header className="flex flex-col gap-1 px-3 py-2">
        <div className="flex items-center gap-2">
          <Tag tone="brand">{NODE_TYPE_NAMES[node.type]}</Tag>
          <ConfidenceTag value={node.confidence} />
          <span className="ml-auto text-[12px] text-fg-3">{node.degree} relations</span>
        </div>
        <h2 className="text-[15px] leading-5 font-semibold break-words">{node.label}</h2>
        {node.detail && <div className="code text-[12px] break-all text-fg-3">{node.detail}</div>}
        {node.category && node.type !== "category" && (
          <div className="text-[12px] text-fg-2">Category: {node.category.replaceAll("/", " › ")}</div>
        )}
        <div className="mt-1 flex flex-wrap gap-1.5">
          {detail.open && (
            <button
              type="button"
              className="btn btn-primary h-7"
              onClick={() => actions.onOpenSource(detail.open!.document_id, detail.open!.line)}
            >
              <ExternalLink size={13} aria-hidden /> Open original source
            </button>
          )}
          <button type="button" className="btn h-7" onClick={() => actions.onExpand(node.id)}>
            <Network size={13} aria-hidden /> Expand relations
          </button>
          <button type="button" className="btn h-7" onClick={() => actions.onFocus(node.id)}>
            <Crosshair size={13} aria-hidden /> Focus
          </button>
        </div>
      </header>

      <Section title={`Relations (${relations.length})`}>
        {relations.length === 0 ? (
          <p className="text-[12px] text-fg-3">No relation written in the sources.</p>
        ) : (
          <div className="flex flex-col gap-2">
            {[...groups.entries()].map(([key, list]) => {
              const [type, direction] = key.split("|");
              return (
                <details key={key} open={list.length <= 6}>
                  <summary className="cursor-pointer text-[12.5px] font-medium">
                    {direction === "out" ? (
                      <ArrowRight size={12} className="mr-1 inline" aria-hidden />
                    ) : (
                      <ArrowLeft size={12} className="mr-1 inline" aria-hidden />
                    )}
                    {EDGE_TYPE_NAMES[type as keyof typeof EDGE_TYPE_NAMES]} · {direction === "out" ? "outgoing" : "incoming"} ({list.length})
                  </summary>
                  <ul className="mt-1 flex flex-col gap-1.5 pl-1">
                    {list.slice(0, 40).map((r) => (
                      <li key={r.edge.id} className="flex flex-col gap-1">
                        <div className="flex items-center gap-1.5 text-[12.5px]">
                          <NodeLink node={r.other} actions={actions} />
                          <span className="text-[11.5px] text-fg-3">({NODE_TYPE_NAMES[r.other.type]})</span>
                          <ConfidenceTag value={r.edge.confidence} />
                          <span className="ml-auto shrink-0 text-[11.5px] text-fg-3">
                            {r.edge.evidence.length} {r.edge.evidence.length === 1 ? "piece" : "pieces"} of evidence
                          </span>
                        </div>
                        <ul className="flex flex-col gap-1">
                          {r.edge.evidence.slice(0, 2).map((e, i) => (
                            <EvidenceItem key={i} e={e} actions={actions} />
                          ))}
                        </ul>
                      </li>
                    ))}
                    {list.length > 40 && <li className="text-[12px] text-fg-3">… and {list.length - 40} more.</li>}
                  </ul>
                </details>
              );
            })}
          </div>
        )}
      </Section>

      <Section title={`Chunks that cite this node (${detail.related_chunks.length})`}>
        <Chunks chunks={detail.related_chunks} actions={actions} empty="No chunk of another document cites this node." />
      </Section>
      {node.document_id !== undefined && (
        <Section title={`Chunks of the document (${detail.own_chunks.length})`}>
          <Chunks chunks={detail.own_chunks} actions={actions} empty="The document has no chunks." />
        </Section>
      )}
    </div>
  );
}

export function EdgeDetailView({ detail, actions }: { detail: EdgeDetail; actions: DetailActions }) {
  const { edge, from, to } = detail;
  return (
    <div className="flex flex-col">
      <header className="flex flex-col gap-1 px-3 py-2">
        <div className="flex items-center gap-2">
          <Tag tone="brand">{EDGE_TYPE_NAMES[edge.type]}</Tag>
          <ConfidenceTag value={edge.confidence} />
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-[13px]">
          <NodeLink node={from} actions={actions} />
          <ArrowRight size={13} className="text-fg-3" aria-hidden />
          <NodeLink node={to} actions={actions} />
        </div>
      </header>
      <Section title={`Evidence (${edge.evidence.length}) — where this relation comes from`}>
        <ul className="flex flex-col gap-1.5">
          {edge.evidence.map((e, i) => (
            <EvidenceItem key={i} e={e} actions={actions} />
          ))}
        </ul>
      </Section>
    </div>
  );
}
