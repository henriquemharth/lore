import { Search } from "lucide-react";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { useQuery } from "../api.ts";
import { SearchResults } from "../components/SearchResults.tsx";
import { Panel, QueryState } from "../components/ui.tsx";
import type { SearchResponse, Status } from "../types.ts";

export function SearchPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const q = (params.get("q") ?? "").trim();
  const category = params.get("category") ?? "";
  const source = params.get("source") ?? "";
  const [typed, setTyped] = useState(q);
  useEffect(() => setTyped(q), [q]);

  const status = useQuery<Status>("/status");
  const query = new URLSearchParams({ q, limit: "50" });
  if (category) query.set("category", category);
  if (source) query.set("source", source);
  const search = useQuery<SearchResponse>(q ? `/search?${query}` : null);

  const apply = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    setParams(next);
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    apply({ q: typed.trim() });
  };

  return (
    <div className="flex flex-col gap-3">
      <form onSubmit={submit} className="panel flex flex-wrap items-center gap-2 p-2" role="search">
        <label className="relative min-w-[240px] flex-1">
          <span className="sr-only">Search the documents</span>
          <Search size={14} className="absolute top-2 left-2 text-fg-3" aria-hidden />
          <input
            className="field w-full pl-7"
            placeholder="e.g. order status, ADR-0004, audit_log, “payment retries”"
            value={typed}
            maxLength={200}
            onChange={(e) => setTyped(e.target.value)}
          />
        </label>
        <select className="field" aria-label="Category" value={category} onChange={(e) => apply({ category: e.target.value })}>
          <option value="">All categories</option>
          {(status.data?.categories ?? []).map((c) => (
            <option key={c.category} value={c.category}>
              {c.category.replaceAll("/", " › ")}
            </option>
          ))}
        </select>
        <select className="field" aria-label="Source" value={source} onChange={(e) => apply({ source: e.target.value })}>
          <option value="">All sources</option>
          {(status.data?.sources ?? []).map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <button type="submit" className="btn btn-primary">
          Search
        </button>
      </form>

      <Panel title={q ? `Results for “${q}”` : "Search"}>
        {!q ? (
          <p className="p-4 text-fg-3">
            Full-text search over every indexed chunk: accents and case are ignored, plurals find the singular (“policies”
            finds “policy”), and codes such as ADR-0004 or commit hashes are matched as typed. Use quotes for an exact
            phrase.
          </p>
        ) : (
          <QueryState loading={search.loading} error={search.error} onRetry={search.reload}>
            {search.data && (
              <SearchResults
                response={search.data}
                onOpen={(r) => navigate(`/explorer/${r.document_id}?tab=original&chunk=${r.chunk_ordinal}`)}
              />
            )}
          </QueryState>
        )}
      </Panel>
    </div>
  );
}
