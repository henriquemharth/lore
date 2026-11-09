import { CircleAlert, LoaderCircle } from "lucide-react";
import type { ReactNode } from "react";

export function Panel({
  title,
  icon,
  actions,
  children,
  className = "",
}: {
  title?: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {title !== undefined && (
        <header className="panel-header">
          {icon}
          <h2 className="flex-1 truncate">{title}</h2>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export type Tone = "neutral" | "ok" | "danger" | "warn" | "brand";

export function Tag({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  const className = tone === "neutral" ? "tag" : `tag tag-${tone}`;
  return (
    <span className={className} title={title}>
      {children}
    </span>
  );
}

const EVENT_TONE: Record<string, Tone> = {
  new: "ok",
  restored: "ok",
  changed: "brand",
  reprocess: "brand",
  reprocessed: "neutral",
  reclassified: "neutral",
  removed: "warn",
  error: "danger",
  indexed: "ok",
};

export function eventTone(event: string): Tone {
  return EVENT_TONE[event] ?? "neutral";
}

const DECISION_STATE_TONE: Record<string, Tone> = {
  active: "ok",
  proposed: "brand",
  superseded: "warn",
  deprecated: "warn",
  undeclared: "neutral",
};

export function decisionStateTone(state: string): Tone {
  return DECISION_STATE_TONE[state] ?? "neutral";
}

/** Loading / error / empty — a failure never shows up as zero. */
export function QueryState({
  loading,
  error,
  empty,
  emptyMessage = "Nothing to show.",
  onRetry,
  children,
}: {
  loading: boolean;
  error: string | null;
  empty?: boolean;
  emptyMessage?: string;
  onRetry?: () => void;
  children?: ReactNode;
}) {
  if (error) {
    return (
      <div role="alert" className="flex items-center gap-2 p-4 text-danger">
        <CircleAlert size={16} aria-hidden />
        <span className="flex-1">Could not load: {error}</span>
        {onRetry && (
          <button type="button" className="btn" onClick={onRetry}>
            Try again
          </button>
        )}
      </div>
    );
  }
  if (loading && !children) {
    return (
      <div className="flex items-center gap-2 p-4 text-fg-3" aria-live="polite">
        <LoaderCircle size={16} className="animate-spin" aria-hidden />
        Loading…
      </div>
    );
  }
  if (empty) return <p className="p-4 text-fg-3">{emptyMessage}</p>;
  return <>{children}</>;
}

export function Stat({ label, value, detail }: { label: string; value: ReactNode; detail?: ReactNode }) {
  return (
    <div className="panel flex min-w-0 flex-col gap-1 px-3 py-2.5">
      <span className="text-[12px] text-fg-3">{label}</span>
      <span className="text-[22px] leading-7 font-semibold tabular-nums text-fg">{value}</span>
      {detail && <span className="truncate text-[12px] text-fg-3">{detail}</span>}
    </div>
  );
}
