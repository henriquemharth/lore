import { FileText } from "lucide-react";

import { splitExcerpt } from "../logic/excerpt.ts";
import { plural } from "../logic/format.ts";
import type { SearchResponse, SearchResult } from "../types.ts";
import { Tag } from "./ui.tsx";

export function Highlight({ excerpt, start, end }: { excerpt: string; start?: string; end?: string }) {
  return (
    <>
      {splitExcerpt(excerpt, start, end).map((p, i) => (p.highlight ? <mark key={i}>{p.text}</mark> : <span key={i}>{p.text}</span>))}
    </>
  );
}

export function SearchResults({
  response,
  onOpen,
}: {
  response: SearchResponse;
  onOpen?: (result: SearchResult) => void;
}) {
  if (response.results.length === 0) {
    return <p className="p-4 text-fg-3">No chunks found for “{response.query}”.</p>;
  }
  return (
    <div>
      <p className="border-b border-line px-4 py-2 text-[12px] text-fg-3">
        {plural(response.total_documents, "document")} · {plural(response.total_chunks, "chunk")}
        {response.mode === "any" && " · no chunk had every word; showing chunks with any of them"}
        {response.warning && ` · ${response.warning}`}
      </p>
      <ol className="divide-y divide-line">
        {response.results.map((r) => (
          <li key={r.chunk_id} className="px-4 py-3">
            <button type="button" className="group flex w-full flex-col gap-1 text-left" onClick={() => onOpen?.(r)}>
              <span className="flex flex-wrap items-center gap-2">
                <FileText size={14} className="text-brand" aria-hidden />
                <span className="font-medium text-brand group-hover:underline">{r.document_title ?? r.path}</span>
                <Tag>{r.category.replaceAll("/", " › ")}</Tag>
                <span className="code text-fg-3">
                  {r.source_id}:{r.path}:{r.line_start}
                </span>
              </span>
              {r.section_path.length > 0 && <span className="text-[12px] text-fg-3">{r.section_path.join(" › ")}</span>}
              <span className="leading-5 text-fg-2">
                <Highlight excerpt={r.excerpt} start={response.markers.start} end={response.markers.end} />
              </span>
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}
