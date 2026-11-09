import { Activity, Cable, CircleCheck, CircleDashed, FilePlus2, Lock, Terminal, Wrench } from "lucide-react";

import { decisionStateLabel, formatDate, formatNumber, relativeTime } from "../logic/format.ts";
import type { ContextPackage, McpPanel } from "../types.ts";
import { Panel, Stat, Tag, decisionStateTone } from "./ui.tsx";

const LIMIT_LABELS: Record<string, string> = {
  max_chunks: "Chunks per response",
  max_chars_per_chunk: "Characters per chunk",
  max_chars_total: "Content characters per response",
  max_documents: "Documents per response",
  min_score: "Minimum relevance (relative to the best)",
  max_entities: "Entities per response",
  max_decisions: "Decisions per response",
  memory_weight: "Weight of AI memories",
  min_coverage: "Minimum term coverage",
};

const OMITTED_LABELS: Record<string, string> = {
  by_privacy: "sensitive",
  below_min_score: "below the minimum score",
  low_term_coverage: "low term coverage",
  document_limit: "over the document limit",
  chunk_limit: "over the chunk limit",
  size_limit: "over the size limit",
  other_chunks_same_documents: "other chunks of the same documents",
};

export function McpPanelView({
  panel,
  now,
  onOpenDocument,
}: {
  panel: McpPanel;
  now?: Date;
  onOpenDocument?: (id: number) => void;
}) {
  const usage = panel.usage;
  const memories = panel.memories;
  const claudeCommand = `claude mcp add --scope user lore -- ${panel.command}`;
  const codexCommand = `codex mcp add lore -- ${panel.command}`;
  return (
    <div className="flex flex-col gap-3">
      <section className="panel flex flex-wrap items-center gap-3 px-3 py-2.5">
        {panel.online ? (
          <span className="flex items-center gap-1.5 font-semibold text-ok">
            <span className="h-2.5 w-2.5 rounded-full bg-ok" aria-hidden /> MCP online
          </span>
        ) : (
          <span className="flex items-center gap-1.5 font-semibold text-fg-3">
            <CircleDashed size={14} aria-hidden /> No MCP client connected right now
          </span>
        )}
        {panel.read_only ? (
          <Tag tone="brand">
            <Lock size={11} aria-hidden /> read-only
          </Tag>
        ) : (
          <Tag
            tone="brand"
            title="lore_remember creates new memories; lore_forget moves MCP memories to a recoverable trash folder. Nothing else is changed."
          >
            <FilePlus2 size={11} aria-hidden /> read + create/forget memories
          </Tag>
        )}
        <Tag>{panel.transport}</Tag>
        <span className="text-[12.5px] text-fg-3">
          {panel.online
            ? `${panel.processes.length} MCP process(es) running on this machine.`
            : "The server does not stay running on its own: the AI client (Claude Code, Codex…) starts the process when it needs it, with no network port."}
        </span>
      </section>

      {panel.processes.length > 0 && (
        <Panel title="Active MCP processes" icon={<Activity size={15} aria-hidden className="text-fg-3" />}>
          <div className="overflow-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>PID</th>
                  <th>Client</th>
                  <th>Domain</th>
                  <th>Started</th>
                  <th>Last used</th>
                </tr>
              </thead>
              <tbody>
                {panel.processes.map((p) => (
                  <tr key={p.pid}>
                    <td className="code">{p.pid}</td>
                    <td>{p.client?.name ? `${p.client.name} ${p.client.version ?? ""}` : "waiting for the first query"}</td>
                    <td className="code">{p.domain ?? "—"}</td>
                    <td>{formatDate(p.started_at)}</td>
                    <td>{relativeTime(p.last_used_at, now)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Queries" value={formatNumber(usage.queries)} detail={`${usage.errors} with errors · ${usage.no_results} without results`} />
        <Stat label="Chunks delivered" value={formatNumber(usage.chunks)} detail="summed over every query" />
        <Stat
          label="Context delivered"
          value={`≈ ${formatNumber(usage.approx_tokens)} tokens`}
          detail={`${formatNumber(usage.characters)} characters (${usage.token_estimate})`}
        />
        <Stat
          label="Tokens saved (estimate)"
          value={`≈ ${formatNumber(usage.approx_tokens_avoided)}`}
          detail="whole documents the chunks came from − what was delivered"
        />
      </div>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Panel title="Available tools" icon={<Wrench size={15} aria-hidden className="text-fg-3" />}>
          <ul className="divide-y divide-line">
            {panel.tools.map((t) => (
              <li key={t.name} className="flex items-start gap-2 px-3 py-2">
                <span className="code w-40 shrink-0 font-medium">{t.name}</span>
                <span className="flex-1 text-fg-2">{t.description}</span>
                <span className="shrink-0 text-[12px] tabular-nums text-fg-3">{usage.by_tool[t.name] ?? 0} uses</span>
              </li>
            ))}
          </ul>
          <p className="border-t border-line px-3 py-2 text-[12px] text-fg-3">
            {memories.allowed
              ? "Only lore_remember (creates a new .md file) and lore_forget (moves a memory created through MCP to the trash) write. No tool edits documents, decisions or memories from other sources."
              : "No tool writes, moves or deletes documents, memories or decisions."}{" "}
            {usage.secrets_redacted} secret(s) redacted from responses so far.
          </p>
        </Panel>

        <Panel title="Recent clients" icon={<Cable size={15} aria-hidden className="text-fg-3" />}>
          {panel.clients.length === 0 ? (
            <p className="p-3 text-fg-3">No client has queried lore yet.</p>
          ) : (
            <div className="overflow-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Client</th>
                    <th className="text-right">Queries</th>
                    <th>Last</th>
                  </tr>
                </thead>
                <tbody>
                  {panel.clients.map((c) => (
                    <tr key={c.client}>
                      <td>{c.client}</td>
                      <td className="text-right tabular-nums">{c.queries}</td>
                      <td>{formatDate(c.last)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>

      <MemoriesPanel memories={memories} onOpenDocument={onOpenDocument} />

      <Panel title="Recent queries" icon={<Activity size={15} aria-hidden className="text-fg-3" />}>
        {panel.recent_queries.length === 0 ? (
          <p className="p-3 text-fg-3">No queries recorded.</p>
        ) : (
          <div className="overflow-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>At</th>
                  <th>Tool</th>
                  <th>Client</th>
                  <th>Query</th>
                  <th className="text-right">Docs</th>
                  <th className="text-right">Chunks</th>
                  <th className="text-right">≈ Tokens</th>
                  <th className="text-right">ms</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>
                {panel.recent_queries.map((q) => (
                  <tr key={q.id}>
                    <td className="whitespace-nowrap">{formatDate(q.at)}</td>
                    <td className="code">{q.tool}</td>
                    <td className="whitespace-nowrap">{q.client}</td>
                    <td className="max-w-[360px] truncate" title={q.query ?? ""}>
                      {q.query ?? "—"}
                    </td>
                    <td className="text-right tabular-nums">{q.documents}</td>
                    <td className="text-right tabular-nums">{q.chunks}</td>
                    <td className="text-right tabular-nums">{formatNumber(q.approx_tokens)}</td>
                    <td className="text-right tabular-nums">{q.duration_ms ?? "—"}</td>
                    <td>
                      {q.result === "error" ? (
                        <Tag tone="danger" title={q.error ?? undefined}>
                          error
                        </Tag>
                      ) : q.result === "no_results" ? (
                        <Tag tone="warn">no results</Tag>
                      ) : (
                        <Tag tone="ok">ok</Tag>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Panel title="How to connect" icon={<Terminal size={15} aria-hidden className="text-fg-3" />}>
          <div className="flex flex-col gap-2 px-3 py-2 text-[12.5px]">
            <p className="text-fg-2">Claude Code (available in every project of this user):</p>
            <pre className="code overflow-x-auto rounded border border-line bg-surface-2 p-2">{claudeCommand}</pre>
            <p className="text-fg-2">Codex CLI (writes to ~/.codex/config.toml):</p>
            <pre className="code overflow-x-auto rounded border border-line bg-surface-2 p-2">{codexCommand}</pre>
            <p className="text-fg-3">
              The command only connects lore to a client; the server runs locally over stdio and opens no port. Details in
              docs/MCP.md.
            </p>
          </div>
        </Panel>
        <Panel title="Context limits" icon={<CircleCheck size={15} aria-hidden className="text-fg-3" />}>
          <table className="data-table">
            <tbody>
              {Object.entries(panel.limits).map(([key, value]) => (
                <tr key={key}>
                  <td>{LIMIT_LABELS[key] ?? key}</td>
                  <td className="text-right tabular-nums">{String(value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="border-t border-line px-3 py-2 text-[12px] text-fg-3">
            Tune them with the LORE_MCP_* environment variables (see docs/CONTRACT.md). Anything over a limit is not sent:
            the response says there is more.
          </p>
        </Panel>
      </div>
    </div>
  );
}

function MemoriesPanel({
  memories,
  onOpenDocument,
}: {
  memories: McpPanel["memories"];
  onOpenDocument?: (id: number) => void;
}) {
  return (
    <Panel title="Memories written through MCP" icon={<FilePlus2 size={15} aria-hidden className="text-fg-3" />}>
      <p className="border-b border-line px-3 py-2 text-[12.5px] text-fg-2">
        {memories.allowed ? (
          <>
            lore_remember is on: up to {memories.max_per_hour} memories per hour, each a new file in{" "}
            <span className="code break-all">{memories.folder}</span>, marked as <strong>unverified</strong>. Secrets are
            refused; repeated content is not duplicated.{" "}
            {memories.forget_allowed ? (
              <>
                lore_forget (a reason is required) moves an MCP memory to <span className="code">.trash/</span> in that folder
                — nothing is deleted; to restore one, move the file back. Forgotten so far: {memories.forgotten}.
              </>
            ) : (
              "Forgetting through MCP is off (LORE_MCP_ALLOW_FORGET=false)."
            )}
          </>
        ) : (
          "lore_remember is off (LORE_MCP_ALLOW_MEMORY=false): MCP is read-only."
        )}
        {memories.allowed && !memories.source && (
          <span className="mt-1 block text-warn">
            No source in sources.yaml reads this folder: MCP refuses to write until a folder source points at it.
          </span>
        )}
      </p>
      {memories.recent.length === 0 ? (
        <p className="p-3 text-fg-3">No memories written through MCP yet.</p>
      ) : (
        <div className="overflow-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>At</th>
                <th>Title</th>
                <th>Client</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {memories.recent.map((m) => (
                <tr key={`${m.at}-${m.file}`}>
                  <td className="whitespace-nowrap">{formatDate(m.at)}</td>
                  <td className="max-w-[420px]">
                    {m.document_id !== null && onOpenDocument ? (
                      <button type="button" className="link text-left" onClick={() => onOpenDocument(m.document_id as number)}>
                        {m.title ?? m.file}
                      </button>
                    ) : (
                      <span>{m.title ?? m.file}</span>
                    )}
                    <span className="code block truncate text-[11.5px] text-fg-3" title={m.file}>
                      {m.file}
                    </span>
                  </td>
                  <td className="whitespace-nowrap">{m.client}</td>
                  <td>
                    {m.status === "forgotten" ? (
                      <Tag tone="warn" title={m.reason ?? undefined}>
                        forgotten (trash)
                      </Tag>
                    ) : m.removed ? (
                      <Tag tone="warn">file removed</Tag>
                    ) : m.status === "already_existed" ? (
                      <Tag>duplicate (not written)</Tag>
                    ) : m.document_id !== null ? (
                      <Tag tone="ok">indexed</Tag>
                    ) : (
                      <Tag tone="warn">waiting for indexing</Tag>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

export function ContextPreview({ pack }: { pack: ContextPackage }) {
  const omitted = Object.entries(pack.omitted).filter(([, v]) => v > 0);
  return (
    <div className="flex flex-col gap-2 px-3 py-2 text-[12.5px]">
      <div className="flex flex-wrap gap-3 text-fg-2">
        <span>
          <strong>{pack.returned.chunks}</strong> {pack.returned.chunks === 1 ? "chunk" : "chunks"} from{" "}
          <strong>{pack.returned.documents}</strong> {pack.returned.documents === 1 ? "document" : "documents"}
        </span>
        <span>≈ {formatNumber(pack.returned.approx_tokens)} tokens of content</span>
        <span>≈ {formatNumber(pack.savings.approx_tokens_avoided)} tokens avoided (estimate)</span>
        <span className="text-fg-3">terms: {pack.search_terms.join(", ")}</span>
      </div>
      <ol className="flex flex-col gap-1">
        {pack.chunks.map((c) => (
          <li key={c.chunk_id} className="flex items-baseline gap-2">
            <span className="w-10 shrink-0 text-right tabular-nums text-fg-3">{c.score.toFixed(2)}</span>
            <span className="code truncate">{c.source}</span>
            <span className="truncate text-fg-3">› {c.heading}</span>
            {c.truncated && <Tag>truncated</Tag>}
          </li>
        ))}
      </ol>
      {pack.decisions.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-fg-3">Decisions:</span>
          {pack.decisions.map((d) => (
            <span key={d.code} className="flex items-center gap-1" title={d.declared_status ? `${d.why} · ${d.declared_status}` : d.why}>
              {d.code}
              <Tag tone={decisionStateTone(d.state)}>{decisionStateLabel(d.state)}</Tag>
            </span>
          ))}
        </div>
      )}
      {omitted.length > 0 && (
        <p className="text-fg-3">Left out: {omitted.map(([k, v]) => `${v} ${OMITTED_LABELS[k] ?? k.replaceAll("_", " ")}`).join(" · ")}</p>
      )}
      {pack.warnings.map((w) => (
        <p key={w} className="text-warn">
          {w}
        </p>
      ))}
    </div>
  );
}
