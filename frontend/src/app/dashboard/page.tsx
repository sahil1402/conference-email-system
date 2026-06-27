"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Mail,
  Zap,
  Users,
  BarChart2,
  Inbox,
  ArrowRight,
} from "lucide-react";

import { useAnalytics } from "@/hooks/useAnalytics";
import { useEmailQueue } from "@/hooks/useEmailQueue";
import {
  Badge,
  ConfidenceBar,
  StatCard,
  EmptyState,
  LoadingSpinner,
  ErrorBanner,
} from "@/components/ui";
import { IngestPanel } from "@/components/dashboard/IngestPanel";
import type { Email } from "@/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** "submission_deadline" → "Submission Deadline". */
function formatIntentLabel(key: string): string {
  return key
    .split("_")
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}

/** Compact relative time, e.g. "just now", "2h ago", "3d ago". */
function timeAgo(input: string | number | null | undefined): string {
  if (input == null) return "—";
  const ms = typeof input === "number" ? input : Date.parse(input);
  if (Number.isNaN(ms)) return "—";
  const secs = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (secs < 45) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.round(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.round(months / 12)}y ago`;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function DashboardPage() {
  const router = useRouter();
  const { summary, isLoading: analyticsLoading, isError: analyticsError } =
    useAnalytics();
  const {
    emails,
    isLoading: queueLoading,
    isError: queueError,
    refetch,
  } = useEmailQueue();

  // Track when analytics data last refreshed, and re-render the label on the
  // poll cadence so "Updated …" stays honest.
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);
  const [, forceTick] = useState(0);

  useEffect(() => {
    if (summary) setLastUpdatedAt(Date.now());
  }, [summary]);

  useEffect(() => {
    const id = setInterval(() => forceTick((t) => t + 1), 30_000);
    return () => clearInterval(id);
  }, []);

  const isLoading = analyticsLoading || queueLoading;
  const isError = analyticsError || queueError;

  return (
    <div className="mx-auto w-full max-w-6xl px-8 py-10">
      {/* SECTION A — Header */}
      <header className="mb-8 flex items-end justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1
            className="text-2xl font-semibold tracking-tight"
            style={{ color: "var(--text-primary)" }}
          >
            Dashboard
          </h1>
          <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
            System overview and email routing analytics
          </p>
        </div>
        <span
          className="shrink-0 text-xs"
          style={{ color: "var(--text-muted)" }}
        >
          {lastUpdatedAt ? `Updated ${timeAgo(lastUpdatedAt)}` : "Updating…"}
        </span>
      </header>

      {isError && (
        <ErrorBanner
          className="mb-8"
          message="Couldn't load dashboard data. The backend may be unreachable."
          onRetry={() => {
            refetch();
            window.location.reload();
          }}
        />
      )}

      {isLoading && !summary ? (
        <div className="flex items-center justify-center py-32">
          <LoadingSpinner size="lg" />
        </div>
      ) : (
        <div className="flex flex-col gap-6">
          {/* SECTION B — Stats row */}
          <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <StatCard
              label="Total Emails"
              value={summary?.total_emails ?? 0}
              icon={<Mail className="h-4 w-4" />}
            />
            <StatCard
              label="Auto-Replied"
              value={summary?.faq_lane_count ?? 0}
              icon={<Zap className="h-4 w-4" />}
              accent="var(--faq-color)"
            />
            <StatCard
              label="Human Review"
              value={summary?.human_review_count ?? 0}
              icon={<Users className="h-4 w-4" />}
              accent="var(--review-color)"
            />
            <StatCard
              label="Avg Confidence"
              value={`${((summary?.avg_confidence ?? 0) * 100).toFixed(1)}%`}
              icon={<BarChart2 className="h-4 w-4" />}
            />
          </section>

          {/* SECTION C + D — two-column on wide screens */}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <IntentDistribution
              distribution={summary?.intent_distribution ?? {}}
            />
            <RecentEmails
              emails={emails}
              onOpen={() => router.push("/queue")}
            />
          </div>

          {/* Demo: inject a test email through the pipeline */}
          <IngestPanel />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SECTION C — Intent distribution
// ---------------------------------------------------------------------------

function Panel({
  title,
  action,
  children,
}: {
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section
      className="flex flex-col rounded-xl border"
      style={{
        backgroundColor: "var(--surface)",
        borderColor: "var(--border)",
      }}
    >
      <div className="flex items-center justify-between px-5 py-4">
        <h2
          className="text-sm font-semibold"
          style={{ color: "var(--text-primary)" }}
        >
          {title}
        </h2>
        {action}
      </div>
      <div
        className="px-5 pb-5"
        style={{ borderTop: "1px solid var(--border-subtle)" }}
      >
        {children}
      </div>
    </section>
  );
}

function IntentDistribution({
  distribution,
}: {
  distribution: Record<string, number>;
}) {
  const entries = Object.entries(distribution).sort((a, b) => b[1] - a[1]);
  const max = entries.length ? Math.max(...entries.map(([, n]) => n)) : 0;

  return (
    <Panel title="Intent Distribution">
      {entries.length === 0 ? (
        <EmptyState
          icon={<BarChart2 className="h-5 w-5" />}
          title="No classifications yet"
          description="Intent breakdown appears once emails have been processed."
        />
      ) : (
        <ul className="flex flex-col gap-3 pt-4">
          {entries.map(([intent, count]) => (
            <li key={intent} className="flex items-center gap-3">
              <span
                className="w-40 shrink-0 truncate text-sm"
                style={{ color: "var(--text-secondary)" }}
                title={formatIntentLabel(intent)}
              >
                {formatIntentLabel(intent)}
              </span>
              <div
                className="h-2 flex-1 overflow-hidden rounded-full"
                style={{ backgroundColor: "var(--surface-raised)" }}
              >
                <div
                  className="h-full rounded-full transition-[width] duration-500 ease-out"
                  style={{
                    width: `${max ? (count / max) * 100 : 0}%`,
                    backgroundColor: "var(--accent)",
                  }}
                />
              </div>
              <span
                className="w-6 shrink-0 text-right text-sm font-medium tabular-nums"
                style={{ color: "var(--text-primary)" }}
              >
                {count}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// SECTION D — Recent emails
// ---------------------------------------------------------------------------

function RecentEmails({
  emails,
  onOpen,
}: {
  emails: Email[];
  onOpen: () => void;
}) {
  const recent = emails.slice(0, 5);

  return (
    <Panel
      title="Recent Emails"
      action={
        <button
          type="button"
          onClick={onOpen}
          className="inline-flex items-center gap-1 text-xs font-medium transition-opacity hover:opacity-80"
          style={{ color: "var(--accent)" }}
        >
          View all
          <ArrowRight className="h-3.5 w-3.5" />
        </button>
      }
    >
      {recent.length === 0 ? (
        <EmptyState
          icon={<Inbox className="h-5 w-5" />}
          title="Queue is empty"
          description="Processed emails will show up here as they arrive."
        />
      ) : (
        <ul className="flex flex-col pt-1">
          {recent.map((email, i) => {
            const lane = email.routing?.lane;
            const confidence = email.classification?.confidence;
            return (
              <li key={email.id}>
                <button
                  type="button"
                  onClick={onOpen}
                  className="flex w-full items-center gap-4 rounded-lg px-2 py-3 text-left transition-colors"
                  onMouseEnter={(e) =>
                    (e.currentTarget.style.backgroundColor =
                      "var(--surface-raised)")
                  }
                  onMouseLeave={(e) =>
                    (e.currentTarget.style.backgroundColor = "transparent")
                  }
                  style={
                    i > 0
                      ? { borderTop: "1px solid var(--border-subtle)" }
                      : undefined
                  }
                >
                  <div className="flex min-w-0 flex-1 flex-col gap-1">
                    <span
                      className="truncate text-sm font-medium"
                      style={{ color: "var(--text-primary)" }}
                    >
                      {email.subject || "(no subject)"}
                    </span>
                    <span
                      className="truncate text-xs"
                      style={{ color: "var(--text-muted)" }}
                    >
                      {email.sender}
                    </span>
                    {typeof confidence === "number" && (
                      <ConfidenceBar
                        value={confidence}
                        className="mt-1 max-w-[160px]"
                      />
                    )}
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-1.5">
                    {lane && (
                      <Badge
                        variant={lane === "faq" ? "faq" : "review"}
                        size="sm"
                      >
                        {lane === "faq" ? "FAQ" : "Review"}
                      </Badge>
                    )}
                    <span
                      className="text-xs tabular-nums"
                      style={{ color: "var(--text-muted)" }}
                    >
                      {timeAgo(email.received_at ?? email.created_at)}
                    </span>
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
