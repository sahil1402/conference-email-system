"use client";

import { useState } from "react";
import { ChevronDown } from "lucide-react";

import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { EmailAuditTrailEntry } from "@/types";

/**
 * Collapsible activity list for the ticket route, rendering the audit trail
 * returned by GET /emails/by-ticket/{id}.
 *
 * Collapsed by DEFAULT: the header (with the entry count) always shows, and the
 * list expands on click — it's reference detail, not something the chair needs
 * open while triaging, and collapsed it keeps the draft/actions above the fold.
 *
 * Deliberately a LOCAL adapter (not a shared timeline): it is typed directly to
 * {@link EmailAuditTrailEntry} — the actual wire shape of this endpoint's
 * `audit_trail` (`timestamp` / `metadata`, string `email_id`) — so the data is
 * NOT coerced to the differently-shaped {@link AuditEntry} used by the analytics
 * feed.
 */
export function TicketAuditTrail({
  entries,
}: {
  entries: EmailAuditTrailEntry[];
}) {
  const [expanded, setExpanded] = useState(false);

  // Latest first. Sort by timestamp descending (ISO strings compare
  // lexicographically); entries without a timestamp fall to the end. Copy so we
  // never mutate the prop array.
  const ordered = [...entries].sort((a, b) =>
    (b.timestamp ?? "").localeCompare(a.timestamp ?? "")
  );

  return (
    <div
      className="shrink-0 px-6 py-3"
      style={{ borderTop: "1px solid var(--border)" }}
      data-testid="ticket-audit-trail"
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        aria-controls="ticket-audit-trail-list"
        className="flex w-full items-center gap-1.5 text-xs font-semibold uppercase tracking-wide transition-colors hover:text-[var(--text-secondary)]"
        style={{ color: "var(--text-muted)" }}
      >
        <ChevronDown
          className={cn("h-3.5 w-3.5 transition-transform", !expanded && "-rotate-90")}
          aria-hidden
        />
        Activity ({entries.length})
      </button>

      {expanded && (
        <div id="ticket-audit-trail-list" className="mt-2 max-h-40 overflow-y-auto">
          {entries.length === 0 ? (
            <p className="text-xs" style={{ color: "var(--text-muted)" }}>
              No recorded activity.
            </p>
          ) : (
            <ul className="space-y-1">
              {ordered.map((entry) => (
                <li
                  key={entry.id}
                  className="flex items-baseline gap-2 text-xs"
                  style={{ color: "var(--text-secondary)" }}
                >
                  <span className="font-medium" style={{ color: "var(--text-primary)" }}>
                    {entry.action}
                  </span>
                  <span style={{ color: "var(--text-muted)" }}>· {entry.actor}</span>
                  {/* Reads `timestamp` (EmailAuditTrailEntry), NOT `created_at`
                      (AuditEntry) — proof the shape is not coerced. */}
                  {entry.timestamp && (
                    <span className="ml-auto tabular-nums" style={{ color: "var(--text-muted)" }}>
                      {formatDateTime(entry.timestamp)}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
