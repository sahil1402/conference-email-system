"use client";

import { formatDateTime } from "@/lib/format";
import type { EmailAuditTrailEntry } from "@/types";

/**
 * Minimal activity list for the ticket route, rendering the audit trail returned
 * by GET /emails/by-ticket/{id}.
 *
 * Deliberately a LOCAL adapter (not a shared timeline): it is typed directly to
 * {@link EmailAuditTrailEntry} — the actual wire shape of this endpoint's
 * `audit_trail` (`timestamp` / `metadata`, string `email_id`) — so the data is
 * NOT coerced to the differently-shaped {@link AuditEntry} used by the analytics
 * feed. Presentation is intentionally bare (C2); C3 handles loading/empty/error
 * polish and visual design.
 */
export function TicketAuditTrail({
  entries,
}: {
  entries: EmailAuditTrailEntry[];
}) {
  return (
    <div
      className="max-h-40 shrink-0 overflow-y-auto px-6 py-3"
      style={{ borderTop: "1px solid var(--border)" }}
      data-testid="ticket-audit-trail"
    >
      <h3
        className="mb-2 text-xs font-semibold uppercase tracking-wide"
        style={{ color: "var(--text-muted)" }}
      >
        Activity ({entries.length})
      </h3>
      {entries.length === 0 ? (
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          No recorded activity.
        </p>
      ) : (
        <ul className="space-y-1">
          {entries.map((entry) => (
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
  );
}
