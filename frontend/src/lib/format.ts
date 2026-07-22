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

/**
 * A small, curated palette for color-coding chairs (Phase 6A). Deterministic by
 * chair id so the same chair keeps its color (and its position in the cycle)
 * across the badge, the queue, and the analytics charts.
 *
 * Two theme variants: the `dark` set is the original — light hues on a
 * translucent tint, readable on the dark surface tokens. The `light` set uses
 * darker (600/700-weight) hues, because the dark set's pastel `color` is used as
 * badge TEXT and would fail contrast on a white pill; the `bg` is a low-opacity
 * tint of that darker hue so the pill stays a pale wash with legible text.
 */
type ThemeName = "dark" | "light";

const CHAIR_PALETTES: Record<ThemeName, { color: string; bg: string }[]> = {
  dark: [
    { color: "#818cf8", bg: "rgba(129, 140, 248, 0.15)" }, // indigo
    { color: "#34d399", bg: "rgba(52, 211, 153, 0.15)" }, // emerald
    { color: "#fbbf24", bg: "rgba(251, 191, 36, 0.15)" }, // amber
    { color: "#f472b6", bg: "rgba(244, 114, 182, 0.15)" }, // pink
    { color: "#22d3ee", bg: "rgba(34, 211, 238, 0.15)" }, // cyan
    { color: "#a78bfa", bg: "rgba(167, 139, 250, 0.15)" }, // violet
  ],
  light: [
    { color: "#4f46e5", bg: "rgba(79, 70, 229, 0.12)" }, // indigo
    { color: "#047857", bg: "rgba(4, 120, 87, 0.12)" }, // emerald
    { color: "#b45309", bg: "rgba(180, 83, 9, 0.12)" }, // amber
    { color: "#be185d", bg: "rgba(190, 24, 93, 0.12)" }, // pink
    { color: "#0e7490", bg: "rgba(14, 116, 144, 0.12)" }, // cyan
    { color: "#7c3aed", bg: "rgba(124, 58, 237, 0.12)" }, // violet
  ],
};

/**
 * Deterministic {color, bg} for a chair id (cycles through the palette).
 * `theme` selects the variant; defaults to "dark" so existing callers that
 * don't pass it keep the original behavior.
 */
export function chairColor(
  chairId: number,
  theme: ThemeName = "dark"
): { color: string; bg: string } {
  const palette = CHAIR_PALETTES[theme];
  const len = palette.length;
  return palette[(((chairId - 1) % len) + len) % len];
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
