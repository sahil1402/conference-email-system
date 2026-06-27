/**
 * Small presentation helpers shared across the email/review UI.
 * Pure functions — safe to import from server or client components.
 */

import type { BadgeVariant } from "@/components/ui";
import type { EmailLane } from "@/types";

/** "submission_deadline" → "Submission Deadline". */
export function formatIntentLabel(key: string): string {
  return key
    .split(/[_\s]+/)
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}

/** Compact relative time, e.g. "just now", "2h ago", "3d ago". */
export function timeAgo(input: string | number | null | undefined): string {
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

/** Absolute date-time, e.g. "Jun 26, 2026, 11:03 PM". */
export function formatDateTime(input: string | number | null | undefined): string {
  if (input == null) return "—";
  const ms = typeof input === "number" ? input : Date.parse(input);
  if (Number.isNaN(ms)) return "—";
  return new Date(ms).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Up to two uppercase initials from a display name, else the email local part. */
export function initials(
  name: string | null | undefined,
  email: string
): string {
  const base =
    name && name.trim() ? name.trim() : (email.split("@")[0] ?? email);
  const words = base.split(/[.\s_\-]+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

/** Human label for a lane. */
export function laneLabel(lane: EmailLane): string {
  return lane === "faq" ? "FAQ" : "Review";
}

/** Badge variant for a lane indicator. */
export function laneBadgeVariant(lane: EmailLane): BadgeVariant {
  return lane === "faq" ? "faq" : "review";
}

/** "DRAFT_GENERATED" → "Draft Generated", "approved" → "Approved". */
export function statusLabel(status: string): string {
  return status
    .split(/[_\s]+/)
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1).toLowerCase() : w))
    .join(" ");
}

/** Badge variant for a lifecycle status. */
export function statusBadgeVariant(status: string): BadgeVariant {
  switch (status.toUpperCase()) {
    case "APPROVED":
    case "SENT":
      return "success";
    case "DRAFT_GENERATED":
      return "warning";
    case "REROUTED":
      return "review";
    default:
      return "neutral";
  }
}
