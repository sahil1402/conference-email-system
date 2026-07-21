"use client";

import { useMemo, useState } from "react";
import { ChevronDown, History as HistoryIcon, RotateCcw } from "lucide-react";

import {
  usePolicies,
  usePolicyAudit,
  useReactivatePolicy,
  useRetirePolicy,
  useRevertPolicyEdit,
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
import { cn } from "@/lib/utils";
import type { PolicyAuditEntry, PolicyDocument, PolicyStatus } from "@/types";

/** Badge variant per policy_audit_logs action. */
function actionBadgeVariant(action: string): BadgeVariant {
  switch (action) {
    case "policy_created":
      return "success";
    case "policy_retired":
      return "danger";
    case "policy_reactivated":
      return "review";
    case "policy_edited":
      return "warning";
    case "policy_edit_reverted":
      return "neutral";
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
 * latest entry. For status-toggle entries (create/retire/reactivate), revert
 * is the inverse of that change: if the policy is currently active, revert
 * retires it (undoing an add/reactivate); if currently inactive, revert
 * reactivates it (undoing a retirement). ``policy_edited`` entries are
 * content-aware instead: revert restores the prior version (POST
 * .../revert-edit — reactivates the superseded ancestor, retires this tip)
 * rather than merely toggling status. ``policy_edit_reverted`` entries are
 * themselves not independently revertable (see ``revertableIds`` below).
 */
export function PolicyHistory() {
  const { entries, isLoading, isError, refetch } = usePolicyAudit();
  const { policies } = usePolicies();
  const retireMutation = useRetirePolicy();
  const reactivateMutation = useReactivatePolicy();
  const revertEditMutation = useRevertPolicyEdit();
  const [confirmingId, setConfirmingId] = useState<number | null>(null);

  // Current status per policy_key, from the live policy list (not the audit
  // trail) — a key missing here means the policy no longer exists.
  const statusByKey = useMemo(() => {
    const map = new Map<string, PolicyStatus>();
    policies.forEach((p) => map.set(p.policy_key, p.status));
    return map;
  }, [policies]);

  // The live policy per key, so each history row can show the policy itself
  // (title + content), not just who changed it. A retired policy is still
  // present here (it's inactive, not deleted); a missing key means it's gone.
  const policyByKey = useMemo(() => {
    const map = new Map<string, PolicyDocument>();
    policies.forEach((p) => map.set(p.policy_key, p));
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
        if (entry.action !== "policy_edit_reverted") ids.add(entry.id);
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
      : revertEditMutation.isPending
        ? revertEditMutation.variables ?? null
        : null;

  function handleConfirmRevert(entry: PolicyAuditEntry) {
    if (entry.action === "policy_edited") {
      revertEditMutation.mutate(entry.policy_key);
      return;
    }
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
          policy={policyByKey.get(entry.policy_key) ?? null}
          isRevertable={
            revertableIds.has(entry.id) &&
            statusByKey.has(entry.policy_key) &&
            (entry.action !== "policy_edited" ||
              statusByKey.get(entry.policy_key) === "active")
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
  policy,
  isRevertable,
  isPending,
  isConfirming,
  onRevertClick,
  onConfirm,
  onCancel,
}: {
  entry: PolicyAuditEntry;
  policy: PolicyDocument | null;
  isRevertable: boolean;
  isPending: boolean;
  isConfirming: boolean;
  onRevertClick: () => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const beforeStatus = snapshotStatus(entry.before);
  const afterStatus = snapshotStatus(entry.after);
  const [expanded, setExpanded] = useState(false);

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

      {/* The policy itself — title always shown, content expandable. */}
      {policy ? (
        <div className="mt-2">
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="flex w-full items-start gap-2 text-left"
            aria-expanded={expanded}
          >
            <span
              className="min-w-0 flex-1 text-sm font-medium"
              style={{ color: "var(--text-primary)" }}
            >
              {policy.title}
            </span>
            <ChevronDown
              className={cn(
                "mt-0.5 h-4 w-4 shrink-0 transition-transform",
                expanded && "rotate-180"
              )}
              style={{ color: "var(--text-muted)" }}
            />
          </button>
          <p
            className={cn(
              "mt-1 whitespace-pre-wrap text-sm",
              !expanded && "line-clamp-2"
            )}
            style={{ color: "var(--text-secondary)" }}
          >
            {policy.content}
          </p>
        </div>
      ) : (
        <p className="mt-2 text-xs italic" style={{ color: "var(--text-muted)" }}>
          Policy no longer in the knowledge base.
        </p>
      )}

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
