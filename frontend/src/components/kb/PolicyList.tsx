"use client";

import { useMemo, useState } from "react";
import { AlertTriangle, Check, ChevronDown, RotateCw } from "lucide-react";

import { usePolicy } from "@/hooks";
import { Badge, Button, ErrorBanner, LoadingSpinner } from "@/components/ui";
import { PolicyEditor } from "@/components/kb/PolicyEditor";
import { cn } from "@/lib/utils";
import type { ConflictReport, PolicyDocument } from "@/types";

interface PolicyListProps {
  policies: PolicyDocument[];
  onRetire: (key: string) => void;
  onReactivate: (key: string) => void;
  onRecheck: (key: string) => void;
  /** policy_key of the retire/reactivate mutation currently in flight, if any. */
  pendingKey: string | null;
  /** policy_key of the conflict re-check currently in flight, if any. */
  recheckingKey: string | null;
}

/** Filtered list of policy documents, each row with a retire/reactivate action. */
export function PolicyList({
  policies,
  onRetire,
  onReactivate,
  onRecheck,
  pendingKey,
  recheckingKey,
}: PolicyListProps) {
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(new Set());
  const [editingKey, setEditingKey] = useState<string | null>(null);

  // Lets a conflict strip seed its inline editor with the target policy's
  // visibility + updated_at when that policy is loaded in the current view.
  const byKey = useMemo(
    () => new Map(policies.map((p) => [p.policy_key, p])),
    [policies]
  );
  const resolvePolicy = (key: string) => byKey.get(key);

  function toggleExpanded(key: string) {
    setExpandedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <ul className="flex flex-col gap-3">
      {policies.map((policy) => (
        <PolicyRow
          key={policy.policy_key}
          policy={policy}
          onRetire={onRetire}
          onReactivate={onReactivate}
          onRecheck={() => onRecheck(policy.policy_key)}
          resolvePolicy={resolvePolicy}
          isPending={pendingKey === policy.policy_key}
          isRechecking={recheckingKey === policy.policy_key}
          isExpanded={expandedKeys.has(policy.policy_key)}
          onToggleExpanded={() => toggleExpanded(policy.policy_key)}
          isEditing={editingKey === policy.policy_key}
          onEdit={() => setEditingKey(policy.policy_key)}
          onEditDone={() => setEditingKey(null)}
        />
      ))}
    </ul>
  );
}

function PolicyRow({
  policy,
  onRetire,
  onReactivate,
  onRecheck,
  resolvePolicy,
  isPending,
  isRechecking,
  isExpanded,
  onToggleExpanded,
  isEditing,
  onEdit,
  onEditDone,
}: {
  policy: PolicyDocument;
  onRetire: (key: string) => void;
  onReactivate: (key: string) => void;
  onRecheck: () => void;
  resolvePolicy: (key: string) => PolicyDocument | undefined;
  isPending: boolean;
  isRechecking: boolean;
  isExpanded: boolean;
  onToggleExpanded: () => void;
  isEditing: boolean;
  onEdit: () => void;
  onEditDone: () => void;
}) {
  const isActive = policy.status === "active";

  return (
    <li
      className="rounded-lg border p-4"
      style={{
        backgroundColor: "var(--surface-raised)",
        borderColor: "var(--border)",
        opacity: isActive ? 1 : 0.6,
      }}
    >
      {/* Line 1: policy_key + badges + action */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span
            className="truncate text-xs"
            style={{
              color: "var(--text-muted)",
              fontFamily:
                'ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace',
            }}
          >
            {policy.policy_key}
          </span>
          <Badge
            variant={policy.visibility === "internal" ? "warning" : "neutral"}
            size="sm"
          >
            {policy.visibility}
          </Badge>
          <Badge
            variant={policy.status === "active" ? "success" : "neutral"}
            size="sm"
          >
            {policy.status}
          </Badge>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {isActive ? (
            <>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={onEdit}
                disabled={isPending || isEditing}
              >
                Edit
              </Button>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => onRetire(policy.policy_key)}
                disabled={isPending}
              >
                Retire
              </Button>
            </>
          ) : (
            <Button
              type="button"
              size="sm"
              onClick={() => onReactivate(policy.policy_key)}
              disabled={isPending}
            >
              Reactivate
            </Button>
          )}
        </div>
      </div>

      {isEditing ? (
        <div className="mt-3">
          <PolicyEditor
            policyKey={policy.policy_key}
            initialTitle={policy.title}
            initialContent={policy.content}
            initialCategory={policy.category}
            initialVisibility={policy.visibility}
            expectedUpdatedAt={policy.updated_at}
            onDone={onEditDone}
            onCancel={onEditDone}
          />
        </div>
      ) : (
        <>
          {/* Line 2: title */}
          <p
            className="mt-2 text-sm font-semibold"
            style={{ color: "var(--text-primary)" }}
          >
            {policy.title}
          </p>
          {/* Line 3: content — truncated unless expanded */}
          <p
            className={cn("mt-1 text-sm", !isExpanded && "line-clamp-2")}
            style={{ color: "var(--text-secondary)" }}
          >
            {policy.content}
          </p>
          <button
            type="button"
            onClick={onToggleExpanded}
            aria-expanded={isExpanded}
            className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium transition-opacity hover:opacity-80"
            style={{ color: "var(--accent)" }}
          >
            <ChevronDown
              className={cn(
                "h-3.5 w-3.5 transition-transform duration-200",
                isExpanded && "rotate-180"
              )}
            />
            {isExpanded ? "Show less" : "Show more"}
          </button>
          <ConflictStrip
            report={policy.conflict_report}
            onRecheck={onRecheck}
            isRechecking={isRechecking}
            resolvePolicy={resolvePolicy}
          />
        </>
      )}
    </li>
  );
}

function timeAgo(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const s = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function RecheckButton({
  onRecheck,
  isRechecking,
}: {
  onRecheck: () => void;
  isRechecking: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onRecheck}
      disabled={isRechecking}
      className="inline-flex items-center gap-1 text-xs font-medium transition-opacity hover:opacity-80 disabled:opacity-50"
      style={{ color: "var(--accent)" }}
    >
      <RotateCw className={cn("h-3 w-3", isRechecking && "animate-spin")} aria-hidden />
      Re-check
    </button>
  );
}

/** Inline editor for a CONFLICTING policy, opened from the strip — mirrors the
 *  "Check for related policies" list. Fetches the target's full text (the
 *  report only stores key/title/snippets), then reuses PolicyEditor. Editing it
 *  into a new version retires the version this policy conflicted with, so the
 *  stale conflict prunes away on the next load. Visibility + concurrency stamp
 *  come from the loaded row when available, else PolicyEditor's defaults. */
function ConflictEditRow({
  policyKey,
  resolved,
  onDone,
  onCancel,
}: {
  policyKey: string;
  resolved: PolicyDocument | undefined;
  onDone: () => void;
  onCancel: () => void;
}) {
  const { policy, isLoading, isError } = usePolicy(policyKey);
  if (isLoading) {
    return (
      <div className="mt-2 flex justify-center py-3">
        <LoadingSpinner size="sm" />
      </div>
    );
  }
  if (isError || !policy) {
    return (
      <div className="mt-2">
        <ErrorBanner message="Couldn't load this policy to edit." />
      </div>
    );
  }
  return (
    <div className="mt-2">
      <PolicyEditor
        policyKey={policyKey}
        initialTitle={policy.title}
        initialContent={policy.content}
        initialCategory={policy.category}
        initialVisibility={resolved?.visibility}
        expectedUpdatedAt={resolved?.updated_at}
        onDone={onDone}
        onCancel={onCancel}
      />
    </div>
  );
}

/** Persisted conflict report shown inline on the card (2e). Nothing when the
 *  policy was never checked / the check was unavailable. */
function ConflictStrip({
  report,
  onRecheck,
  isRechecking,
  resolvePolicy,
}: {
  report?: ConflictReport | null;
  onRecheck: () => void;
  isRechecking: boolean;
  resolvePolicy: (key: string) => PolicyDocument | undefined;
}) {
  const [open, setOpen] = useState(false);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  if (!report || report.available === false) return null;
  const conflicts = report.conflicts ?? [];
  const stamp = report.checked_at ? timeAgo(report.checked_at) : "";

  if (conflicts.length === 0) {
    return (
      <div
        className="mt-2 flex items-center gap-2 text-xs"
        style={{ color: "var(--text-muted)" }}
      >
        <Check className="h-3.5 w-3.5" aria-hidden />
        No conflicts{stamp ? ` · checked ${stamp}` : ""}
        <RecheckButton onRecheck={onRecheck} isRechecking={isRechecking} />
      </div>
    );
  }

  return (
    <div
      className="mt-2 overflow-hidden rounded-md border"
      style={{ borderColor: "var(--danger)", backgroundColor: "var(--danger-subtle)" }}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 px-3 py-1.5 text-xs font-medium"
        style={{ color: "var(--danger)" }}
      >
        <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
        {conflicts.length} conflict{conflicts.length > 1 ? "s" : ""}
        <ChevronDown
          className={cn("h-3.5 w-3.5 transition-transform duration-200", open && "rotate-180")}
          aria-hidden
        />
      </button>
      {open && (
        <div className="space-y-2 px-3 pb-2">
          {conflicts.map((c) => (
            <div key={c.policy_key} className="text-xs" style={{ color: "var(--text-primary)" }}>
              <span className="font-medium">{c.title || c.policy_key}</span>
              <span style={{ color: "var(--text-muted)" }}> ({c.policy_key})</span>
              {c.explanation ? <span> — {c.explanation}</span> : null}
              {editingKey !== c.policy_key && (
                <button
                  type="button"
                  onClick={() => setEditingKey(c.policy_key)}
                  className="ml-2 font-medium transition-opacity hover:opacity-80"
                  style={{ color: "var(--accent)" }}
                >
                  Edit
                </button>
              )}
              {c.snippets.map((s, i) => (
                <span
                  key={i}
                  className="mt-0.5 block italic"
                  style={{ color: "var(--text-secondary)" }}
                >
                  “{s}”
                </span>
              ))}
              {editingKey === c.policy_key && (
                <ConflictEditRow
                  policyKey={c.policy_key}
                  resolved={resolvePolicy(c.policy_key)}
                  onDone={() => setEditingKey(null)}
                  onCancel={() => setEditingKey(null)}
                />
              )}
            </div>
          ))}
          <div className="flex items-center gap-2 pt-0.5" style={{ color: "var(--text-muted)" }}>
            {stamp ? <span className="text-xs">checked {stamp}</span> : null}
            <RecheckButton onRecheck={onRecheck} isRechecking={isRechecking} />
          </div>
        </div>
      )}
    </div>
  );
}
