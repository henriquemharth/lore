// Formatting for the UI (en-US, the browser's local time zone). No dependencies: runs under `node --test`.

let dateTime: Intl.DateTimeFormat | null = null;

/** Created lazily so the time zone is read when first needed (tests pin it with TZ). */
function dateTimeFormat(): Intl.DateTimeFormat {
  dateTime ??= new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  return dateTime;
}

/** "Sep 25, 2026 17:03" */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return dateTimeFormat().format(date).replace(/,(?=[^,]*$)/, "");
}

export function relativeTime(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "never";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const seconds = Math.round((now.getTime() - date.getTime()) / 1000);
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "1 day ago" : `${days} days ago`;
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toLocaleString("en-US", { maximumFractionDigits: 1 })} KB`;
  return `${(kb / 1024).toLocaleString("en-US", { maximumFractionDigits: 1 })} MB`;
}

export function formatNumber(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : value.toLocaleString("en-US");
}

export function shortHash(hash: string | null | undefined, length = 12): string {
  return hash ? hash.replace(/^git:/, "").slice(0, length) : "—";
}

/** Accent- and case-insensitive form, for matching. */
export function normalize(text: string): string {
  return text.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

export function plural(count: number, singular: string, pluralForm = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : pluralForm}`;
}

const EVENTS: Record<string, string> = {
  new: "New",
  changed: "Changed",
  reprocess: "Reprocess",
  reprocessed: "Reprocessed",
  restored: "Restored",
  removed: "Removed",
  error: "Error",
  reclassified: "Reclassified",
};

export function eventLabel(event: string): string {
  return EVENTS[event] ?? event;
}

const ORIGINS: Record<string, string> = {
  api: "from the UI/API",
  cli: "from the command line",
  scheduled: "automatic",
  test: "test",
  mcp: "MCP",
};

export function originLabel(origin: string | null | undefined): string {
  return origin ? (ORIGINS[origin] ?? origin) : "—";
}

const DECISION_STATES: Record<string, string> = {
  active: "active",
  proposed: "proposed",
  superseded: "superseded",
  deprecated: "deprecated",
  undeclared: "no status",
};

export function decisionStateLabel(state: string): string {
  return DECISION_STATES[state] ?? state;
}

export function lastSegment(category: string): string {
  const parts = category.split("/");
  return parts[parts.length - 1];
}
