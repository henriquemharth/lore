import { CircleCheck, Database, FileWarning, FolderTree, GitBranch, History, Layers, RefreshCw, TriangleAlert } from "lucide-react";

import { eventLabel, formatDate, formatNumber, originLabel, relativeTime, shortHash } from "../logic/format.ts";
import type { ReindexSummary, Status } from "../types.ts";
import { Panel, Stat, Tag, eventTone } from "./ui.tsx";

export interface StatusPanelProps {
  status: Status;
  now?: Date;
  reindexing?: boolean;
  result?: ReindexSummary | null;
  reindexError?: string | null;
  onReindex?: (full: boolean) => void;
  onOpenDocument?: (id: number) => void;
  onOpenCategory?: (category: string) => void;
}

export function StatusPanel({
  status,
  now,
  reindexing = false,
  result,
  reindexError,
  onReindex,
  onOpenDocument,
  onOpenCategory,
}: StatusPanelProps) {
  const last = status.last_run;
  const items = (last?.items ?? []).filter((i) => i.event !== "reprocessed");
  const running = reindexing || status.run_in_progress !== null;

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat
          label="Documents"
          value={formatNumber(status.documents.total)}
          detail={`${formatNumber(status.documents.indexed)} indexed · ${formatNumber(status.documents.with_errors)} with errors · ${formatNumber(status.documents.removed)} removed`}
        />
        <Stat
          label="Chunks"
          value={formatNumber(status.chunks)}
          detail={`${formatNumber(status.links.resolved)} of ${formatNumber(status.links.total)} links resolved`}
        />
        <Stat
          label="Last index run"
          value={<span title={formatDate(last?.finished_at)}>{relativeTime(last?.finished_at, now)}</span>}
          detail={
            last ? `${formatDate(last.finished_at)} · ${originLabel(last.origin)}${last.full ? " · full" : ""}` : "Not indexed yet"
          }
        />
        <Stat
          label="Changed documents"
          value={formatNumber(status.pending.length)}
          detail={
            status.pending.length
              ? "changed in the sources, waiting for a reindex"
              : `sources checked ${relativeTime(status.last_check, now)}`
          }
        />
      </div>

      <div className="panel flex flex-wrap items-center gap-2 px-3 py-2">
        <button type="button" className="btn btn-primary" disabled={running || !onReindex} onClick={() => onReindex?.(false)}>
          <RefreshCw size={14} className={running ? "animate-spin" : ""} aria-hidden />
          {running ? "Indexing…" : "Reindex changes"}
        </button>
        <button type="button" className="btn" disabled={running || !onReindex} onClick={() => onReindex?.(true)}>
          Reindex everything
        </button>
        <span className="text-fg-3">
          Re-reads the sources and updates only the lore index. No .md file is modified.
          {status.sync_seconds > 0 && ` Automatic check every ${Math.round(status.sync_seconds / 60)} min.`}
        </span>
        {result && (
          <span role="status" className="ml-auto flex items-center gap-1 text-ok">
            <CircleCheck size={14} aria-hidden />
            {result.recorded
              ? `Done in ${formatNumber(result.duration_ms)} ms: ${result.totals.new} new, ${result.totals.changed} changed, ${result.totals.removed} removed, ${result.totals.errors} errors.`
              : "Nothing changed in the sources."}
          </span>
        )}
        {reindexError && (
          <span role="alert" className="ml-auto text-danger">
            {reindexError}
          </span>
        )}
      </div>

      <div className="grid gap-3 xl:grid-cols-2">
        <Panel title="Changed documents" icon={<History size={15} aria-hidden className="text-fg-3" />}>
          {status.pending.length === 0 && items.length === 0 ? (
            <p className="p-3 text-fg-3">No pending changes, and none in the last run.</p>
          ) : (
            <div className="max-h-80 overflow-auto">
              {status.pending.length > 0 && (
                <ChangeList
                  title="Waiting for a reindex"
                  rows={status.pending.map((p) => ({ ...p, event: p.type, document_id: null }))}
                />
              )}
              {items.length > 0 && (
                <ChangeList title={`In the last run (${formatDate(last?.finished_at)})`} rows={items} onOpenDocument={onOpenDocument} />
              )}
            </div>
          )}
        </Panel>

        <Panel
          title="Indexing errors"
          icon={
            status.errors.length ? (
              <TriangleAlert size={15} aria-hidden className="text-danger" />
            ) : (
              <CircleCheck size={15} aria-hidden className="text-ok" />
            )
          }
        >
          {status.errors.length === 0 ? (
            <p className="p-3 text-fg-3">No errors in the last run.</p>
          ) : (
            <ul className="max-h-80 divide-y divide-line overflow-auto">
              {status.errors.map((e) => (
                <li key={e.document_id} className="flex flex-col gap-0.5 px-3 py-2">
                  <button type="button" className="link code" onClick={() => onOpenDocument?.(e.document_id)}>
                    {e.source_id}:{e.path}
                  </button>
                  <span className="text-danger">
                    <FileWarning size={13} className="mr-1 inline" aria-hidden />
                    {e.error}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="Categories" icon={<FolderTree size={15} aria-hidden className="text-fg-3" />}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Category</th>
                <th className="text-right" title="Documents whose primary category is this one">
                  Primary
                </th>
                <th className="text-right" title="Assignments from explicit information (folder, source, file name, frontmatter)">
                  Explicit
                </th>
                <th className="text-right" title="Assignments from automatic interpretation">
                  Suggestions
                </th>
              </tr>
            </thead>
            <tbody>
              {status.categories.map((c) => (
                <tr key={c.category}>
                  <td>
                    <button type="button" className="link" onClick={() => onOpenCategory?.(c.category)}>
                      {c.category.replaceAll("/", " › ")}
                    </button>
                  </td>
                  <td className="text-right tabular-nums">{c.documents}</td>
                  <td className="text-right tabular-nums">{c.explicit}</td>
                  <td className="text-right tabular-nums text-warn">{c.suggestions || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>

        <Panel title="Sources (read-only)" icon={<Database size={15} aria-hidden className="text-fg-3" />}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Source</th>
                <th className="text-right">Docs</th>
                <th>State</th>
              </tr>
            </thead>
            <tbody>
              {status.sources.map((s) => (
                <tr key={s.id}>
                  <td className="min-w-0">
                    <div className="font-medium">{s.name}</div>
                    <div className="code truncate text-fg-3" title={s.root}>
                      {s.type === "git" ? (
                        <>
                          <GitBranch size={12} className="mr-1 inline" aria-hidden />
                          {s.ref} @ {shortHash(s.ref_commit, 7)}
                        </>
                      ) : (
                        s.root
                      )}
                    </div>
                    {s.warnings.map((w) => (
                      <div key={w} className="text-warn">
                        {w}
                      </div>
                    ))}
                  </td>
                  <td className="text-right tabular-nums">{s.documents}</td>
                  <td>
                    {s.available === null ? (
                      <Tag>not checked</Tag>
                    ) : s.available ? (
                      <Tag tone="ok">available</Tag>
                    ) : (
                      <Tag tone="warn">unavailable</Tag>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      </div>

      <p className="flex items-center gap-1 text-[12px] text-fg-3">
        <Layers size={13} aria-hidden />
        Full-text search {status.search.text ? "on" : "unavailable"} ·{" "}
        {status.search.semantic.available ? "semantic search on" : status.search.semantic.reason} · lore {status.version}
      </p>
    </div>
  );
}

function ChangeList({
  title,
  rows,
  onOpenDocument,
}: {
  title: string;
  rows: { source_id: string; path: string; event: string; detail: string | null; document_id: number | null }[];
  onOpenDocument?: (id: number) => void;
}) {
  return (
    <div>
      <h3 className="bg-surface-2 px-3 py-1 text-[12px] font-medium text-fg-3">{title}</h3>
      <ul className="divide-y divide-line">
        {rows.map((r) => (
          <li key={`${r.event}:${r.source_id}:${r.path}`} className="flex items-center gap-2 px-3 py-1.5">
            <Tag tone={eventTone(r.event)}>{eventLabel(r.event)}</Tag>
            {r.document_id !== null && onOpenDocument ? (
              <button type="button" className="link code truncate" onClick={() => onOpenDocument(r.document_id!)}>
                {r.source_id}:{r.path}
              </button>
            ) : (
              <span className="code truncate">
                {r.source_id}:{r.path}
              </span>
            )}
            {r.detail && <span className="ml-auto truncate text-fg-3">{r.detail}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
