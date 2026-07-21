/**
 * Single source of truth for Zendesk-status dot colors.
 *
 * These are the app-harmonized palette (already integrated into the app's
 * visual language via the queue's ZendeskStatusBar) — NOT Zendesk's native
 * red/blue/gray. Both the ZendeskStatusBar filter bar and the SplitActionButton
 * submit control import from here, so a given status always reads the same
 * color across the UI. Colors only — labels/ordering stay with each component.
 *
 * Keyed by the raw Zendesk status string; callers fall back to
 * `var(--text-muted)` for any status not listed.
 */
export const ZENDESK_STATUS_COLORS: Record<string, string> = {
  new: "#6366f1", // indigo
  open: "#f59e0b", // amber
  pending: "#22d3ee", // cyan
  hold: "#a78bfa", // violet
  solved: "#34d399", // emerald
  closed: "#8b91a8", // muted
};

/** Dot color for a Zendesk status, with the shared muted fallback. */
export function zendeskStatusColor(status: string): string {
  return ZENDESK_STATUS_COLORS[status] ?? "var(--text-muted)";
}
