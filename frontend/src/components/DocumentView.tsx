import { FileText, GitBranch, Hash, Link2, ListTree, Lock, Network, ScrollText } from "lucide-react";
import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

import { chunkByLine, linesOf } from "../logic/excerpt.ts";
import { eventLabel, formatBytes, formatDate, relativeTime, shortHash } from "../logic/format.ts";
import type { DocumentDetail } from "../types.ts";
import { ConfidenceTag } from "./GraphDetails.tsx";
import { Tag, eventTone } from "./ui.tsx";

export type DocumentTab = "headings" | "chunks" | "original" | "links";

export const DOCUMENT_TABS: { key: DocumentTab; title: string }[] = [
  { key: "headings", title: "Headings" },
  { key: "chunks", title: "Chunks" },
  { key: "original", title: "Original content" },
  { key: "links", title: "Links and versions" },
];

export interface DocumentViewProps {
  doc: DocumentDetail;
  tab: DocumentTab;
  onTab: (tab: DocumentTab) => void;
  activeChunk?: number | null;
  activeLine?: number | null;
  onChunk?: (ordinal: number) => void;
  onGoToLine?: (line: number) => void;
  onOpenDocument?: (id: number) => void;
  onShowInGraph?: (id: number) => void;
  now?: Date;
}

export function DocumentView({
  doc,
  tab,
  onTab,
  activeChunk = null,
  activeLine = null,
  onChunk,
  onGoToLine,
  onOpenDocument,
  onShowInGraph,
  now,
}: DocumentViewProps) {
  const counts: Record<DocumentTab, number> = {
    headings: doc.headings.length,
    chunks: doc.chunks.length,
    original: doc.total_lines,
    links: doc.links_out.length + doc.links_in.length,
  };
  return (
    <article className="panel flex min-h-0 flex-col" aria-label={`Document ${doc.name}`}>
      <header className="flex flex-col gap-1 border-b border-line px-4 py-3">
        <div className="flex items-start gap-2">
          <FileText size={18} className="mt-0.5 shrink-0 text-brand" aria-hidden />
          <h2 className="text-[16px] font-semibold leading-6">{doc.title || doc.name}</h2>
          <span className="ml-auto flex shrink-0 gap-1">
            <Tag tone={eventTone(doc.status)}>{doc.status}</Tag>
            {doc.pending && (
              <Tag tone="warn" title="The file changed in its source after the last index run">
                {eventLabel(doc.pending)} in source
              </Tag>
            )}
            <Tag title="lore only reads this file">
              <Lock size={11} aria-hidden /> read-only
            </Tag>
            {onShowInGraph && (
              <button type="button" className="btn h-6 px-2 text-[12px]" onClick={() => onShowInGraph(doc.id)}>
                <Network size={12} aria-hidden /> Show in graph
              </button>
            )}
          </span>
        </div>
        <div className="code truncate text-fg-3" title={`${doc.source_id}:${doc.path}`}>
          {doc.source_name} · {doc.path}
        </div>
        {doc.error && (
          <div role="alert" className="text-danger">
            Indexing error ({doc.error_kind ?? "—"}): {doc.error}
          </div>
        )}
        {doc.warnings.map((w) => (
          <div key={w} className="text-warn">
            {w}
          </div>
        ))}
      </header>

      <dl className="grid grid-cols-2 gap-x-6 gap-y-1.5 border-b border-line px-4 py-3 md:grid-cols-4">
        <Field label="File">{doc.name}</Field>
        <Field label="Size">{formatBytes(doc.size)}</Field>
        <Field label="Modified in source">{formatDate(doc.modified_at)}</Field>
        <Field label="Last indexed">
          <span title={doc.indexed_at ?? ""}>
            {formatDate(doc.indexed_at)} <span className="text-fg-3">({relativeTime(doc.indexed_at, now)})</span>
          </span>
        </Field>
        <Field label="Category">
          <span className="flex items-center gap-1.5">
            {doc.category.replaceAll("/", " › ")} <ConfidenceTag value={doc.category_confidence} />
          </span>
        </Field>
        <Field label="Also suggested in">
          {doc.categories
            .filter((c) => c !== doc.category)
            .map((c) => c.replaceAll("/", " › "))
            .join(", ") || "—"}
        </Field>
        <Field label="Hash (sha256)">
          <span className="code" title={doc.hash ?? ""}>
            <Hash size={11} className="mr-0.5 inline" aria-hidden />
            {shortHash(doc.hash)}
          </span>
        </Field>
        <Field label="Version · lines · chunks">
          v{doc.version} · {doc.total_lines} · {doc.chunks.length}
        </Field>
        {Object.entries(doc.metadata).map(([key, value]) => (
          <Field key={key} label={key}>
            {value}
          </Field>
        ))}
        {doc.frontmatter &&
          Object.entries(doc.frontmatter).map(([key, value]) => (
            <Field key={`fm-${key}`} label={`frontmatter · ${key}`}>
              {typeof value === "string" ? value : JSON.stringify(value)}
            </Field>
          ))}
      </dl>

      {(doc.categories_detail?.length ?? 0) > 0 && (
        <details className="border-b border-line px-4 py-2 text-[12.5px]">
          <summary className="cursor-pointer text-fg-2">Why this document is in these categories</summary>
          <ul className="mt-1.5 flex flex-col gap-1">
            {doc.categories_detail!.map((a) => (
              <li key={a.category} className="flex flex-wrap items-center gap-1.5">
                <span className="font-medium">{a.category.replaceAll("/", " › ")}</span>
                {a.primary && <Tag>primary</Tag>}
                <ConfidenceTag value={a.confidence} />
                <span className="text-fg-3">
                  {a.reason}
                  {a.line ? ` (line ${a.line})` : ""}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}

      <div role="tablist" aria-label="Document parts" className="flex gap-1 border-b border-line px-2">
        {DOCUMENT_TABS.map((t) => (
          <button key={t.key} type="button" role="tab" aria-selected={tab === t.key} className="tab" onClick={() => onTab(t.key)}>
            {t.title} <span className="tabular-nums text-fg-3">{counts[t.key]}</span>
          </button>
        ))}
      </div>

      <div role="tabpanel" className="min-h-0 flex-1 overflow-auto">
        {tab === "headings" && <Headings doc={doc} onGoToLine={onGoToLine} />}
        {tab === "chunks" && <Chunks doc={doc} activeChunk={activeChunk} onChunk={onChunk} />}
        {tab === "original" && <OriginalContent doc={doc} activeChunk={activeChunk} activeLine={activeLine} onChunk={onChunk} />}
        {tab === "links" && <LinksAndVersions doc={doc} onOpenDocument={onOpenDocument} />}
      </div>
    </article>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11.5px] text-fg-3">{label}</dt>
      <dd className="truncate" title={typeof children === "string" ? children : undefined}>
        {children}
      </dd>
    </div>
  );
}

function Headings({ doc, onGoToLine }: { doc: DocumentDetail; onGoToLine?: (line: number) => void }) {
  if (doc.headings.length === 0) return <p className="p-4 text-fg-3">This document has no headings.</p>;
  return (
    <ol className="py-2">
      {doc.headings.map((h) => (
        <li key={`${h.line}-${h.anchor}`} style={{ paddingLeft: `${16 + (h.level - 1) * 16}px` }} className="pr-4">
          <button
            type="button"
            className="flex w-full items-baseline gap-2 py-0.5 text-left hover:text-brand"
            onClick={() => onGoToLine?.(h.line)}
          >
            <span className="code w-7 shrink-0 text-fg-3">H{h.level}</span>
            <span className={h.level <= 2 ? "font-medium" : ""}>{h.text}</span>
            <span className="code ml-auto shrink-0 text-fg-3">line {h.line}</span>
          </button>
        </li>
      ))}
    </ol>
  );
}

function Chunks({
  doc,
  activeChunk,
  onChunk,
}: {
  doc: DocumentDetail;
  activeChunk: number | null;
  onChunk?: (ordinal: number) => void;
}) {
  if (doc.chunks.length === 0) return <p className="p-4 text-fg-3">No chunks (empty document).</p>;
  return (
    <ol className="divide-y divide-line">
      {doc.chunks.map((c) => (
        <li key={c.ordinal} id={`chunk-${c.ordinal}`} className={`px-4 py-3 ${activeChunk === c.ordinal ? "bg-brand-soft" : ""}`}>
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <Tag tone="brand">#{c.ordinal + 1}</Tag>
            <span className="font-medium">{c.title}</span>
            {c.parts > 1 && (
              <Tag>
                part {c.part} of {c.parts}
              </Tag>
            )}
            <span className="ml-auto flex items-center gap-3 text-[12px] text-fg-3">
              <span>
                lines {c.line_start}–{c.line_end}
              </span>
              <span>{c.position_percent}% of the document</span>
              <span>{c.size} characters</span>
              <button type="button" className="link" onClick={() => onChunk?.(c.ordinal)}>
                show in original
              </button>
            </span>
          </div>
          {(c.section_path.length > 0 || c.sections.length > 1) && (
            <div className="mb-1 text-[12px] text-fg-3">
              {c.section_path.join(" › ")}
              {c.sections.length > 1 && ` · sections: ${c.sections.join(", ")}`}
            </div>
          )}
          <pre className="code max-h-56 overflow-auto whitespace-pre-wrap break-words rounded border border-line bg-surface-2 p-2">
            {c.text}
          </pre>
        </li>
      ))}
    </ol>
  );
}

function OriginalContent({
  doc,
  activeChunk,
  activeLine,
  onChunk,
}: {
  doc: DocumentDetail;
  activeChunk: number | null;
  activeLine: number | null;
  onChunk?: (ordinal: number) => void;
}) {
  const lines = linesOf(doc.content);
  const owners = chunkByLine(lines.length, doc.chunks);
  const target = useRef<HTMLTableRowElement | null>(null);
  const chunk = activeChunk !== null ? doc.chunks.find((c) => c.ordinal === activeChunk) : undefined;
  const targetLine = chunk?.line_start ?? activeLine;

  useEffect(() => {
    target.current?.scrollIntoView({ block: "center" });
  }, [targetLine]);

  return (
    <div className="px-2 py-2">
      <p className="px-2 pb-2 text-[12px] text-fg-3">
        Text exactly as it is in the source (read-only). The bar on the left shows which chunk each line belongs to
        {doc.body_start > 0 && `; lines 1–${doc.body_start} are the frontmatter`}.
      </p>
      <table className="code w-full border-collapse">
        <tbody>
          {lines.map((text, index) => {
            const number = index + 1;
            const owner = owners[number];
            const inChunk = chunk !== undefined && owner === chunk.ordinal;
            const highlighted = inChunk || activeLine === number;
            return (
              <tr key={number} ref={number === targetLine ? target : undefined} className={highlighted ? "bg-highlight" : undefined}>
                <td className="w-10 select-none pr-2 text-right align-top text-fg-3">
                  {owner !== null ? (
                    <button
                      type="button"
                      className="hover:text-brand"
                      title={`Line ${number} · chunk ${owner + 1} (click to highlight)`}
                      onClick={() => onChunk?.(owner)}
                    >
                      {number}
                    </button>
                  ) : (
                    number
                  )}
                </td>
                <td
                  className={`whitespace-pre-wrap break-words border-l-[3px] pl-3 align-top ${
                    owner === null ? "border-transparent" : owner % 2 === 0 ? "border-brand/70" : "border-brand/25"
                  }`}
                >
                  {text || " "}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function LinksAndVersions({ doc, onOpenDocument }: { doc: DocumentDetail; onOpenDocument?: (id: number) => void }) {
  return (
    <div className="grid gap-4 p-4 lg:grid-cols-2">
      <section>
        <h3 className="mb-1 flex items-center gap-1 font-medium">
          <Link2 size={14} aria-hidden /> Links in this document ({doc.links_out.length})
        </h3>
        {doc.links_out.length === 0 ? (
          <p className="text-fg-3">No links to other documents.</p>
        ) : (
          <ul className="flex flex-col gap-0.5">
            {doc.links_out.map((l, i) => (
              <li key={`${l.line}-${i}`} className="flex items-baseline gap-2">
                <span className="code w-14 shrink-0 text-fg-3">l. {l.line}</span>
                {l.target_document_id !== null ? (
                  <button type="button" className="link truncate" onClick={() => onOpenDocument?.(l.target_document_id!)}>
                    {l.target_title ?? l.target}
                  </button>
                ) : (
                  <span className="truncate text-fg-3" title="Target not found in any source">
                    {l.target} (not found)
                  </span>
                )}
                {l.type !== "wikilink" && <Tag>{l.type}</Tag>}
              </li>
            ))}
          </ul>
        )}
      </section>
      <section>
        <h3 className="mb-1 flex items-center gap-1 font-medium">
          <ListTree size={14} aria-hidden /> Linked from ({doc.links_in.length})
        </h3>
        {doc.links_in.length === 0 ? (
          <p className="text-fg-3">No document links here.</p>
        ) : (
          <ul className="flex flex-col gap-0.5">
            {doc.links_in.map((l) => (
              <li key={l.document_id}>
                <button type="button" className="link" onClick={() => onOpenDocument?.(l.document_id)}>
                  {l.title ?? l.path}
                </button>{" "}
                <span className="code text-fg-3">{l.path}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="lg:col-span-2">
        <h3 className="mb-1 flex items-center gap-1 font-medium">
          <ScrollText size={14} aria-hidden /> History in lore
        </h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>Version</th>
              <th>Event</th>
              <th>Recorded</th>
              <th>Modified in source</th>
              <th>Size</th>
              <th>Hash</th>
            </tr>
          </thead>
          <tbody>
            {doc.versions.map((v, i) => (
              <tr key={`${v.version}-${v.event}-${i}`}>
                <td>v{v.version}</td>
                <td>
                  <Tag tone={eventTone(v.event)}>{eventLabel(v.event)}</Tag>
                </td>
                <td>{formatDate(v.recorded_at)}</td>
                <td>{formatDate(v.modified_at)}</td>
                <td>{formatBytes(v.size)}</td>
                <td className="code">{shortHash(v.hash)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-2 flex items-center gap-1 text-[12px] text-fg-3">
          <GitBranch size={12} aria-hidden /> Discovered {formatDate(doc.discovered_at)} · processor v{doc.processor_version ?? "—"}
        </p>
      </section>
    </div>
  );
}
