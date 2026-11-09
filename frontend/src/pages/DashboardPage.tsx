import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { reindex, useQuery } from "../api.ts";
import { StatusPanel } from "../components/StatusPanel.tsx";
import { QueryState } from "../components/ui.tsx";
import type { ReindexSummary, Status } from "../types.ts";

export function DashboardPage() {
  const navigate = useNavigate();
  const status = useQuery<Status>("/status");
  const [reindexing, setReindexing] = useState(false);
  const [result, setResult] = useState<ReindexSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { reload } = status;

  useEffect(() => {
    const timer = window.setInterval(reload, 30_000);
    return () => window.clearInterval(timer);
  }, [reload]);

  const onReindex = async (full: boolean) => {
    setReindexing(true);
    setError(null);
    setResult(null);
    try {
      setResult(await reindex(full));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Reindex failed.");
    } finally {
      setReindexing(false);
      reload();
    }
  };

  return (
    <QueryState loading={status.loading} error={status.error} onRetry={reload}>
      {status.data && (
        <StatusPanel
          status={status.data}
          reindexing={reindexing}
          result={result}
          reindexError={error}
          onReindex={onReindex}
          onOpenDocument={(id) => navigate(`/explorer/${id}`)}
          onOpenCategory={(category) => navigate(`/explorer?category=${encodeURIComponent(category)}`)}
        />
      )}
    </QueryState>
  );
}
