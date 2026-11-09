import { ScrollText, ShieldCheck } from "lucide-react";

import { formatDate, originLabel } from "../logic/format.ts";
import type { AuditEntry, Run } from "../types.ts";
import { Panel, Tag } from "./ui.tsx";

const STATUS_TONE = { completed: "ok", failed: "danger", interrupted: "warn", running: "brand" } as const;

const ACTION_LABELS: Record<string, string> = {
  reindex_requested: "Reindex requested",
  reindex_completed: "Reindex completed",
  reindex_failed: "Reindex failed",
  mcp_query: "MCP query",
  mcp_memory: "MCP memory written",
  mcp_memory_forgotten: "MCP memory forgotten",
  mcp_usage_reset: "MCP usage reset",
};

export function AuditView({ runs, entries }: { runs: Run[]; entries: AuditEntry[] }) {
  return (
    <div className="flex flex-col gap-3">
      <Panel title="Index runs" icon={<ScrollText size={15} aria-hidden className="text-fg-3" />}>
        {runs.length === 0 ? (
          <p className="p-3 text-fg-3">No runs recorded.</p>
        ) : (
          <div className="overflow-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Started</th>
                  <th>Origin</th>
                  <th>Status</th>
                  <th className="text-right">New</th>
                  <th className="text-right">Changed</th>
                  <th className="text-right">Removed</th>
                  <th className="text-right">Errors</th>
                  <th className="text-right">Documents</th>
                  <th className="text-right">Chunks</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id}>
                    <td className="tabular-nums">{r.id}</td>
                    <td>{formatDate(r.started_at)}</td>
                    <td>
                      {originLabel(r.origin)}
                      {r.full && " · full"}
                    </td>
                    <td>
                      <Tag tone={STATUS_TONE[r.status]} title={r.error ?? undefined}>
                        {r.status}
                      </Tag>
                    </td>
                    <td className="text-right tabular-nums">{r.totals.new ?? "—"}</td>
                    <td className="text-right tabular-nums">{r.totals.changed ?? "—"}</td>
                    <td className="text-right tabular-nums">{r.totals.removed ?? "—"}</td>
                    <td className="text-right tabular-nums">{r.totals.errors ?? "—"}</td>
                    <td className="text-right tabular-nums">{r.totals.documents ?? "—"}</td>
                    <td className="text-right tabular-nums">{r.totals.chunks ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel
        title="Audit log"
        icon={<ShieldCheck size={15} aria-hidden className="text-fg-3" />}
        actions={<span className="text-[12px] font-normal text-fg-3">append-only — the database refuses updates and deletes</span>}
      >
        {entries.length === 0 ? (
          <p className="p-3 text-fg-3">No entries.</p>
        ) : (
          <div className="overflow-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>At</th>
                  <th>Action</th>
                  <th>Origin</th>
                  <th>IP</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((e) => (
                  <tr key={e.id}>
                    <td className="whitespace-nowrap">{formatDate(e.at)}</td>
                    <td>{ACTION_LABELS[e.action] ?? e.action}</td>
                    <td>{originLabel(e.origin)}</td>
                    <td className="code">{e.ip ?? "—"}</td>
                    <td className="code max-w-[520px] truncate text-fg-3" title={JSON.stringify(e.details, null, 1)}>
                      {summarizeDetails(e.details)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}

function summarizeDetails(details: Record<string, unknown>): string {
  if (typeof details.tool === "string") {
    const input = (details.input ?? {}) as Record<string, unknown>;
    const query = Object.values(input).find((v) => typeof v === "string" && v) as string | undefined;
    const parts = [String(details.tool)];
    if (query) parts.push(`“${query}”`);
    parts.push(`${(details.chunks as unknown[] | undefined)?.length ?? 0} chunks`, `${details.response_characters ?? 0} characters`);
    if (details.error) parts.push(`error: ${String(details.error)}`);
    return parts.join(" · ");
  }
  if (details.from_entry !== undefined) return `usage counted from entry ${String(details.from_entry)}`;
  const totals = details.totals as Record<string, number> | undefined;
  const parts = [`run ${details.run_id ?? "—"}`];
  if (details.full) parts.push("full");
  if (totals) {
    parts.push(`${totals.documents} docs`, `${totals.chunks} chunks`);
    for (const key of ["new", "changed", "removed", "errors"]) if (totals[key]) parts.push(`${totals[key]} ${key}`);
  }
  if (details.error) parts.push(String(details.error));
  return parts.join(" · ");
}
