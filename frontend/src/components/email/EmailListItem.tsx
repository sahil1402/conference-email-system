"use client";

import { Badge, ChairBadge, ConfidenceBar } from "@/components/ui";
import {
  initials,
  laneBadgeVariant,
  laneLabel,
  timeAgo,
} from "@/lib/format";
import type { Email } from "@/types";

interface EmailListItemProps {
  email: Email;
  isSelected: boolean;
  onClick: () => void;
  /** Resolved name of the email's assigned chair (Phase 6A), if known. */
  chairName?: string | null;
}

/** A single row in the queue list. */
export function EmailListItem({
  email,
  isSelected,
  onClick,
  chairName,
}: EmailListItemProps) {
  const lane = email.routing?.lane ?? null;
  const confidence = email.classification?.confidence;

  // The 3px left rail is the lane color, or the accent when selected.
  const railColor = isSelected
    ? "var(--accent)"
    : lane
      ? lane === "faq"
        ? "var(--faq-color)"
        : "var(--review-color)"
      : "var(--border)";

  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center gap-3 px-3 py-3 text-left transition-colors duration-150 hover:bg-[var(--surface-raised)]"
      style={{
        borderLeft: `3px solid ${railColor}`,
        // Only set a background when selected, so the hover class can win in
        // the unselected state (inline styles override :hover otherwise).
        ...(isSelected ? { backgroundColor: "var(--accent-subtle)" } : {}),
      }}
    >
      {/* Avatar */}
      <span
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-xs font-semibold"
        style={{
          backgroundColor: "var(--surface-raised)",
          color: "var(--text-secondary)",
        }}
      >
        {initials(email.sender_name, email.sender)}
      </span>

      {/* Main content */}
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span
          className="truncate text-sm font-semibold"
          style={{ color: "var(--text-primary)" }}
        >
          {email.subject || "(no subject)"}
        </span>
        <span
          className="truncate text-xs"
          style={{ color: "var(--text-secondary)" }}
        >
          {email.sender} · {timeAgo(email.received_at ?? email.created_at)}
        </span>
      </span>

      {/* Right side */}
      <span className="flex shrink-0 flex-col items-end gap-1.5">
        {lane && (
          <Badge variant={laneBadgeVariant(lane)} size="sm">
            {laneLabel(lane)}
          </Badge>
        )}
        {/* Assigned chair — only meaningful for human-review emails. */}
        {lane === "human_review" && (
          <ChairBadge chairId={email.assigned_chair_id} chairName={chairName} />
        )}
        {typeof confidence === "number" && (
          <span className="w-[60px]">
            <ConfidenceBar value={confidence} />
          </span>
        )}
      </span>
    </button>
  );
}
