import { useQuery } from "../api.ts";
import { AuditView } from "../components/AuditView.tsx";
import { QueryState } from "../components/ui.tsx";
import type { AuditEntry, Run } from "../types.ts";

export function AuditPage() {
  const runs = useQuery<Run[]>("/runs?limit=50");
  const entries = useQuery<AuditEntry[]>("/audit?limit=200");
  return (
    <QueryState
      loading={runs.loading || entries.loading}
      error={runs.error ?? entries.error}
      onRetry={() => {
        runs.reload();
        entries.reload();
      }}
    >
      {runs.data && entries.data && <AuditView runs={runs.data} entries={entries.data} />}
    </QueryState>
  );
}
