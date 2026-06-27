"use client";

import { useMemo, useState } from "react";
import { Search, ScrollText } from "lucide-react";

import { useAudit } from "@/hooks/useAudit";
import { EmptyState, ErrorBanner, LoadingSpinner } from "@/components/ui";
import { statusLabel, timeAgo } from "@/lib/format";
import type { AuditEntry } from "@/types";

/** Dot color by action type. */
function actionColor(action: string): string {
  switch (action.toLowerCase()) {
    case "approved":
      return "var(--success)";
    case "rerouted":
      return "var(--warning)";
    case "ingested":
      return "var(--accent)";
    default:
      return "var(--text-muted)";
  }
}

export default function AuditPage() {
  const { entries, isLoading, isError } = useAudit();
  const [search, setSearch] = useState("");

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return entries;
    return entries.filter((e) =>
      `${e.action} ${e.actor} ${e.email_id}`.toLowerCase().includes(q)
    );
  }, [entries, search]);

  return (
    <div className="mx-auto w-full max-w-3xl px-8 py-10">
      {/* Header */}
      <header className="mb-8 flex flex-col gap-1">
        <h1
          className="text-2xl font-semibold tracking-tight"
          style={{ color: "var(--text-primary)" }}
        >
          Audit Log
        </h1>
        <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
          Full history of all system actions
        </p>
      </header>

      {/* Search */}
      <div className="relative mb-6">
        <Search
          className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
          style={{ color: "var(--text-muted)" }}
        />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Filter by actor, action, or email id…"
          className="w-full rounded-lg border py-2 pl-9 pr-3 text-sm outline-none transition-colors focus:border-[var(--accent)]"
          style={{
            backgroundColor: "var(--surface)",
            borderColor: "var(--border)",
            color: "var(--text-primary)",
          }}
        />
      </div>

      {isError ? (
        <ErrorBanner message="Couldn't load the audit log." />
      ) : isLoading ? (
        <div className="flex items-center justify-center py-24">
          <LoadingSpinner size="lg" />
        </div>
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={<ScrollText className="h-5 w-5" />}
          title={search ? "No matching entries" : "No audit entries yet"}
          description={
            search
              ? "Try a different actor, action, or email id."
              : "System actions will appear here as emails are processed and reviewed."
          }
        />
      ) : (
        <ol>
          {filtered.map((entry, i) => (
            <TimelineRow
              key={`${entry.email_id}-${entry.action}-${entry.created_at}-${i}`}
              entry={entry}
              isLast={i === filtered.length - 1}
            />
          ))}
        </ol>
      )}
    </div>
  );
}

function TimelineRow({ entry, isLast }: { entry: AuditEntry; isLast: boolean }) {
  const [open, setOpen] = useState(false);
  const hasDetails = Object.keys(entry.details).length > 0;

  return (
    <li className="flex gap-4 pb-6 last:pb-0">
      {/* Dot + connecting line */}
      <div className="flex flex-col items-center pt-1.5">
        <span
          className="h-2.5 w-2.5 shrink-0 rounded-full"
          style={{ backgroundColor: actionColor(entry.action) }}
        />
        {!isLast && (
          <span
            className="mt-1 w-px flex-1"
            style={{ backgroundColor: "var(--border)" }}
          />
        )}
      </div>

      {/* Card */}
      <div
        className="mb-0 flex-1 rounded-lg border p-4"
        style={{
          backgroundColor: "var(--surface-raised)",
          borderColor: "var(--border)",
        }}
      >
        <div className="flex items-start justify-between gap-3">
          <span
            className="text-sm font-semibold"
            style={{ color: "var(--text-primary)" }}
          >
            {statusLabel(entry.action)}
          </span>
          <span
            className="shrink-0 text-xs tabular-nums"
            style={{ color: "var(--text-muted)" }}
          >
            {timeAgo(entry.created_at)}
          </span>
        </div>
        <div className="mt-1 text-xs" style={{ color: "var(--text-secondary)" }}>
          Email #{entry.email_id} · Actor: {entry.actor}
        </div>

        {hasDetails && (
          <div className="mt-2">
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="text-xs font-medium transition-opacity hover:opacity-80"
              style={{ color: "var(--accent)" }}
            >
              {open ? "Hide details" : "Show details ›"}
            </button>
            {open && (
              <pre
                className="mt-2 overflow-x-auto rounded-md p-3 text-xs leading-relaxed"
                style={{
                  backgroundColor: "var(--surface)",
                  color: "var(--text-secondary)",
                  fontFamily:
                    'ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace',
                }}
              >
                {JSON.stringify(entry.details, null, 2)}
              </pre>
            )}
          </div>
        )}
      </div>
    </li>
  );
}
