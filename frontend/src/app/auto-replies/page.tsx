"use client";

import { Zap } from "lucide-react";

import { useAnalytics } from "@/hooks/useAnalytics";
import { useEmailQueue } from "@/hooks/useEmailQueue";
import {
  Badge,
  ConfidenceBar,
  EmptyState,
  ErrorBanner,
  LoadingSpinner,
  StatCard,
} from "@/components/ui";
import { formatDateTime, formatIntentLabel } from "@/lib/format";

export default function AutoRepliesPage() {
  // Lane-scoped, paginated query. `total` is the true faq-lane count (page-size
  // independent); `emails` is the current page. Both come from this one query —
  // no client-side filtering of a truncated generic-queue page (the old bug,
  // which silently dropped faq emails outside the newest 20).
  const { emails, total, isLoading, isError, refetch } = useEmailQueue({
    lane: "faq",
  });
  // Average confidence over the FULL faq-lane set comes from the server-side
  // aggregate (summary.faq_avg_confidence), NOT an average of the capped page.
  const { summary } = useAnalytics();
  const avgConfidence = summary?.faq_avg_confidence ?? 0;

  return (
    <div className="mx-auto w-full max-w-6xl px-8 py-10">
      {/* Header */}
      <header className="mb-8 flex flex-col gap-1">
        <h1
          className="text-2xl font-semibold tracking-tight"
          style={{ color: "var(--text-primary)" }}
        >
          Auto-Replies
        </h1>
        <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
          Emails handled automatically by the FAQ pipeline
        </p>
      </header>

      {isError && (
        <ErrorBanner
          className="mb-8"
          message="Couldn't load auto-replies."
          onRetry={() => refetch()}
        />
      )}

      {isLoading ? (
        <div className="flex items-center justify-center py-32">
          <LoadingSpinner size="lg" />
        </div>
      ) : (
        <div className="flex flex-col gap-6">
          {/* Stats strip */}
          <section className="grid grid-cols-2 gap-4 sm:max-w-md">
            <StatCard
              label="Total Auto-Replied"
              value={total}
              icon={<Zap className="h-4 w-4" />}
              accent="var(--faq-color)"
            />
            <StatCard
              label="Avg Confidence"
              value={`${(avgConfidence * 100).toFixed(1)}%`}
            />
          </section>

          {/* Honest note if the page ever truncates the lane (stat stays accurate). */}
          {emails.length < total && (
            <p className="text-xs" style={{ color: "var(--text-muted)" }}>
              Showing {emails.length} of {total} — load more to see the rest.
            </p>
          )}

          {/* Table */}
          {total === 0 ? (
            <EmptyState
              icon={<Zap className="h-5 w-5" />}
              title="No auto-replies yet"
              description="Emails routed to the FAQ lane will appear here once the pipeline processes them."
            />
          ) : (
            <div
              className="overflow-hidden rounded-xl border"
              style={{
                borderColor: "var(--border)",
                backgroundColor: "var(--surface)",
              }}
            >
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr style={{ borderBottom: "1px solid var(--border)" }}>
                      {[
                        "Sender",
                        "Subject",
                        "Intent",
                        "Confidence",
                        "Sent At",
                        "Citations",
                      ].map((h) => (
                        <th
                          key={h}
                          className="whitespace-nowrap px-4 py-3 text-xs font-semibold uppercase tracking-wide"
                          style={{ color: "var(--text-muted)" }}
                        >
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {emails.map((email) => {
                      const confidence = email.classification?.confidence ?? 0;
                      const citations =
                        email.retrieved_chunks?.length ??
                        email.draft?.citations.length ??
                        0;
                      return (
                        <tr
                          key={email.id}
                          style={{
                            borderBottom: "1px solid var(--border-subtle)",
                          }}
                        >
                          <td
                            className="max-w-[160px] truncate px-4 py-3"
                            style={{ color: "var(--text-secondary)" }}
                            title={email.sender}
                          >
                            {email.sender}
                          </td>
                          <td
                            className="max-w-[240px] truncate px-4 py-3 font-medium"
                            style={{ color: "var(--text-primary)" }}
                            title={email.subject}
                          >
                            {email.subject || "(no subject)"}
                          </td>
                          <td
                            className="whitespace-nowrap px-4 py-3"
                            style={{ color: "var(--text-secondary)" }}
                          >
                            {email.classification
                              ? formatIntentLabel(email.classification.intent)
                              : "—"}
                          </td>
                          <td className="px-4 py-3">
                            <span className="block w-28">
                              <ConfidenceBar value={confidence} showLabel />
                            </span>
                          </td>
                          <td
                            className="whitespace-nowrap px-4 py-3"
                            style={{ color: "var(--text-secondary)" }}
                          >
                            {formatDateTime(
                              email.updated_at ?? email.received_at
                            )}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3">
                            <Badge variant="neutral" size="sm">
                              {citations} {citations === 1 ? "source" : "sources"}
                            </Badge>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
