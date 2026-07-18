"use client";

import { Badge, Button } from "@/components/ui";
import type { PolicyDocument } from "@/types";

interface PolicyListProps {
  policies: PolicyDocument[];
  onRetire: (key: string) => void;
  onReactivate: (key: string) => void;
  /** policy_key of the retire/reactivate mutation currently in flight, if any. */
  pendingKey: string | null;
}

/** Filtered list of policy documents, each row with a retire/reactivate action. */
export function PolicyList({
  policies,
  onRetire,
  onReactivate,
  pendingKey,
}: PolicyListProps) {
  return (
    <ul className="flex flex-col gap-3">
      {policies.map((policy) => (
        <PolicyRow
          key={policy.policy_key}
          policy={policy}
          onRetire={onRetire}
          onReactivate={onReactivate}
          isPending={pendingKey === policy.policy_key}
        />
      ))}
    </ul>
  );
}

function PolicyRow({
  policy,
  onRetire,
  onReactivate,
  isPending,
}: {
  policy: PolicyDocument;
  onRetire: (key: string) => void;
  onReactivate: (key: string) => void;
  isPending: boolean;
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
        <div className="shrink-0">
          {isActive ? (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => onRetire(policy.policy_key)}
              disabled={isPending}
            >
              Retire
            </Button>
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

      {/* Line 2: title */}
      <p
        className="mt-2 text-sm font-semibold"
        style={{ color: "var(--text-primary)" }}
      >
        {policy.title}
      </p>

      {/* Line 3: content, truncated */}
      <p
        className="mt-1 line-clamp-2 text-sm"
        style={{ color: "var(--text-secondary)" }}
      >
        {policy.content}
      </p>
    </li>
  );
}
