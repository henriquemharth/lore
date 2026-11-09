// lore API client. Read-only, with one exception: asking for a reindex (re-reads the sources; never
// writes to a .md file). The server requires the X-Lore-Confirm header for it.
import { useCallback, useEffect, useRef, useState } from "react";

import type { ReindexSummary } from "./types.ts";

export const API_BASE = "/api/lore";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function handle<T>(response: Response): Promise<T> {
  const type = response.headers.get("content-type") ?? "";
  const body = type.includes("json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof body === "object" && body && "detail" in body ? body.detail : body;
    throw new ApiError(response.status, typeof detail === "string" && detail ? detail : `Error ${response.status}`);
  }
  return body as T;
}

export async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { signal, credentials: "same-origin" });
  return handle<T>(response);
}

export async function reindex(full: boolean): Promise<ReindexSummary> {
  const response = await fetch(`${API_BASE}/reindex`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Lore-Confirm": "reindex" },
    body: JSON.stringify({ full }),
  });
  return handle<ReindexSummary>(response);
}

export interface Query<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/** Fetches `path` (or nothing when null). A failure becomes an error message — never "zero". */
export function useQuery<T>(path: string | null): Query<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const [version, setVersion] = useState(0);
  const last = useRef<string | null>(null);

  useEffect(() => {
    if (!path) {
      setData(null);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    if (last.current !== path) setData(null);
    last.current = path;
    setLoading(true);
    setError(null);
    get<T>(path, controller.signal)
      .then((value) => setData(value))
      .catch((failure: unknown) => {
        if (controller.signal.aborted) return;
        setError(failure instanceof Error ? failure.message : "Could not reach the lore server.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [path, version]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { data, error, loading, reload };
}
