import { Eye } from "lucide-react";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { useQuery } from "../api.ts";
import { ContextPreview, McpPanelView } from "../components/McpView.tsx";
import { Panel, QueryState } from "../components/ui.tsx";
import type { ContextPackage, McpPanel } from "../types.ts";

export function McpPage() {
  const navigate = useNavigate();
  const panel = useQuery<McpPanel>("/mcp");
  const { reload } = panel;
  const [task, setTask] = useState("");
  const [requested, setRequested] = useState<string | null>(null);
  const preview = useQuery<ContextPackage>(requested ? `/context?task=${encodeURIComponent(requested)}` : null);

  useEffect(() => {
    const timer = window.setInterval(reload, 15_000);
    return () => window.clearInterval(timer);
  }, [reload]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (task.trim()) setRequested(task.trim());
  };

  return (
    <div className="flex flex-col gap-3">
      <QueryState loading={panel.loading} error={panel.error} onRetry={reload}>
        {panel.data && <McpPanelView panel={panel.data} onOpenDocument={(id) => navigate(`/explorer/${id}`)} />}
      </QueryState>

      <Panel title="Preview what MCP would deliver" icon={<Eye size={15} aria-hidden className="text-fg-3" />}>
        <form onSubmit={submit} className="flex gap-2 border-b border-line px-3 py-2">
          <input
            className="field flex-1"
            placeholder="Describe a task, e.g. Move the orders database to PostgreSQL"
            value={task}
            maxLength={500}
            onChange={(e) => setTask(e.target.value)}
          />
          <button type="submit" className="btn btn-primary">
            Build context
          </button>
        </form>
        {!requested ? (
          <p className="px-3 py-2 text-[12.5px] text-fg-3">
            Shows the same package lore_get_context would return to an AI client (not recorded as an MCP query).
          </p>
        ) : (
          <QueryState loading={preview.loading} error={preview.error} onRetry={preview.reload}>
            {preview.data && <ContextPreview pack={preview.data} />}
          </QueryState>
        )}
      </Panel>
    </div>
  );
}
