"use client";

import { useMemo, useState } from "react";
import { History as HistoryIcon, RotateCcw } from "lucide-react";

import {
  usePolicies,
  usePolicyAudit,
  useReactivatePolicy,
  useRetirePolicy,
} from "@/hooks";
import {
  Badge,
  type BadgeVariant,
  Button,
  EmptyState,
  ErrorBanner,
  LoadingSpinner,
} from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import type { PolicyAuditEntry, PolicyStatus } from "@/types";

/** Badge variant per policy_audit_logs action. */
function actionBadgeVariant(action: string): BadgeVariant {
  switch (action) {
    case "policy_created":
      return "success";
    case "policy_retired":
      return "danger";
    case "policy_reactivated":
      return "review";
    default:
      return "neutral";
  }
}

/** "policy_created" -> "Policy Created". */
function actionLabel(action: string): string {
  return action
    .split(/[_\s]+/)
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}

/** Pull a `status` string off an audit entry's before/after snapshot, if present. */
function snapshotStatus(snapshot: Record<string, unknown> | null): string | null {
  const s = snapshot?.status;
  return typeof s === "string" ? s : null;
}

/**
 * Policy audit log, newest-first, with a Revert action on each policy's
 * latest entry. Revert is the inverse of that entry's change: if the policy
 * is currently active, revert retires it (undoing an add/reactivate); if
 * currently inactive, revert reactivates it (undoing a retirement).
 */
export function PolicyHistory() {
  const { entries, isLoading, isError, refetch } = usePolicyAudit();
  const { policies } = usePolicies();
  const retireMutation = useRetirePolicy();
  const reactivateMutation = useReactivatePolicy();
  const [confirmingId, setConfirmingId] = useState<number | null>(null);

  // Current status per policy_key, from the live policy list (not the audit
  // trail) — a key missing here means the policy no longer exists.
  const statusByKey = useMemo(() => {
    const map = new Map<string, PolicyStatus>();
    policies.forEach((p) => map.set(p.policy_key, p.status));
    return map;
  }, [policies]);

  // Only the first (= newest, since entries is newest-first) audit row seen
  // per policy_key is revertable — reverting an older entry would fight the
  // change(s) that came after it.
  const revertableIds = useMemo(() => {
    const ids = new Set<number>();
    const seenKeys = new Set<string>();
    entries.forEach((entry) => {
      if (!seenKeys.has(entry.policy_key)) {
        seenKeys.add(entry.policy_key);
        ids.add(entry.id);
      }
    });
    return ids;
  }, [entries]);

  // Mirrors the single-flight pending-key pattern used on the Policies view
  // (knowledge-base/page.tsx): only one retire/reactivate mutation is ever
  // in flight from this UI at a time.
  const pendingKey = retireMutation.isPending
    ? retireMutation.variables ?? null
    : reactivateMutation.isPending
      ? reactivateMutation.variables ?? null
      : null;

  function handleConfirmRevert(entry: PolicyAuditEntry) {
    const currentStatus = statusByKey.get(entry.policy_key);
    if (currentStatus === "active") {
      retireMutation.mutate(entry.policy_key);
    } else if (currentStatus === "inactive") {
      reactivateMutation.mutate(entry.policy_key);
    }
  }

  if (isError) {
    return (
      <ErrorBanner
        message="Couldn't load the policy history."
        onRetry={() => refetch()}
      />
    );
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-24">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (entries.length === 0) {
    return (
      <EmptyState
        icon={<HistoryIcon className="h-5 w-5" />}
        title="No history yet"
        description="Policy changes — additions, retirements, and reactivations — will appear here."
      />
    );
  }

  return (
    <ul className="flex flex-col gap-3">
      {entries.map((entry) => (
        <HistoryRow
          key={entry.id}
          entry={entry}
          isRevertable={
            revertableIds.has(entry.id) && statusByKey.has(entry.policy_key)
          }
          isPending={pendingKey === entry.policy_key}
          isConfirming={confirmingId === entry.id}
          onRevertClick={() =>
            setConfirmingId((current) => (current === entry.id ? null : entry.id))
          }
          onConfirm={() => handleConfirmRevert(entry)}
          onCancel={() => setConfirmingId(null)}
        />
      ))}
    </ul>
  );
}

function HistoryRow({
  entry,
  isRevertable,
  isPending,
  isConfirming,
  onRevertClick,
  onConfirm,
  onCancel,
}: {
  entry: PolicyAuditEntry;
  isRevertable: boolean;
  isPending: boolean;
  isConfirming: boolean;
  onRevertClick: () => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const beforeStatus = snapshotStatus(entry.before);
  const afterStatus = snapshotStatus(entry.after);

  return (
    <li
      className="rounded-lg border p-4"
      style={{
        backgroundColor: "var(--surface-raised)",
        borderColor: "var(--border)",
      }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Badge variant={actionBadgeVariant(entry.action)} size="sm">
            {actionLabel(entry.action)}
          </Badge>
          <span
            className="truncate text-xs"
            style={{
              color: "var(--text-muted)",
              fontFamily:
                'ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace',
            }}
          >
            {entry.policy_key}
          </span>
          {beforeStatus && afterStatus && (
            <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
              {beforeStatus} → {afterStatus}
            </span>
          )}
        </div>
        <span
          className="shrink-0 text-xs tabular-nums"
          style={{ color: "var(--text-muted)" }}
        >
          {formatDateTime(entry.timestamp)}
        </span>
      </div>

      <div className="mt-2 flex items-center justify-between gap-3">
        <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
          Actor: {entry.actor}
        </span>

        {isRevertable &&
          (isConfirming ? (
            <div className="flex shrink-0 items-center gap-2">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={onCancel}
                disabled={isPending}
              >
                Cancel
              </Button>
              <Button
                type="button"
                size="sm"
                onClick={onConfirm}
                disabled={isPending}
              >
                {isPending ? <LoadingSpinner size="sm" /> : "Confirm revert"}
              </Button>
            </div>
          ) : (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={onRevertClick}
              disabled={isPending}
            >
              <RotateCcw className="h-3.5 w-3.5" />
              Revert
            </Button>
          ))}
      </div>
    </li>
  );
}
