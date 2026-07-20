"use client";

/**
 * Compact Zendesk-status filter bar for the queue.
 *
 * Renders one clickable row per Zendesk status present (label left, right-
 * aligned count), driven by the dedicated facets aggregate — never a tally over
 * a capped page. Clicking a status filters the queue to it; clicking the active
 * status again clears the filter. Only meaningful for `source="zendesk"` rows,
 * so the parent mounts it only when Zendesk counts exist (toy_dataset emails
 * carry no meaningful zendesk_status). Composes with the lane / chair / search
 * filters — it never replaces them.
 */

interface ZendeskStatusBarProps {
  /** {zendesk_status -> count} from the facets aggregate. */
  counts: Record<string, number>;
  /** Currently selected status, or null when none is selected. */
  selected: string | null;
  /** Select a status, or null to clear (clicking the active one clears). */
  onSelect: (status: string | null) => void;
}

/** Canonical status order + accent color for the leading dot. */
const STATUS_META: { key: string; label: string; color: string }[] = [
  { key: "new", label: "New", color: "#6366f1" }, // indigo
  { key: "open", label: "Open", color: "#f59e0b" }, // amber
  { key: "pending", label: "Pending", color: "#22d3ee" }, // cyan
  { key: "hold", label: "Hold", color: "#a78bfa" }, // violet
  { key: "solved", label: "Solved", color: "#34d399" }, // emerald
  { key: "closed", label: "Closed", color: "#8b91a8" }, // muted
];

function labelFor(key: string): string {
  const meta = STATUS_META.find((m) => m.key === key);
  if (meta) return meta.label;
  return key ? key[0].toUpperCase() + key.slice(1) : key;
}

function colorFor(key: string): string {
  return STATUS_META.find((m) => m.key === key)?.color ?? "var(--text-muted)";
}

/** Order present statuses by the canonical list, unknowns appended. */
function orderStatuses(counts: Record<string, number>): string[] {
  const present = Object.keys(counts);
  const known = STATUS_META.map((m) => m.key).filter((k) => k in counts);
  const unknown = present.filter((k) => !STATUS_META.some((m) => m.key === k)).sort();
  return [...known, ...unknown];
}

export function ZendeskStatusBar({
  counts,
  selected,
  onSelect,
}: ZendeskStatusBarProps) {
  const keys = orderStatuses(counts);
  if (keys.length === 0) return null;

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <span
          className="text-xs font-medium uppercase tracking-wide"
          style={{ color: "var(--text-muted)" }}
        >
          Zendesk Status
        </span>
        {selected && (
          <button
            type="button"
            onClick={() => onSelect(null)}
            className="text-xs transition-colors hover:underline"
            style={{ color: "var(--accent)" }}
          >
            Clear
          </button>
        )}
      </div>

      <div
        role="group"
        aria-label="Filter by Zendesk status"
        className="flex flex-col overflow-hidden rounded-lg border"
        style={{ borderColor: "var(--border)" }}
      >
        {keys.map((key, i) => {
          const active = selected === key;
          return (
            <button
              key={key}
              type="button"
              aria-pressed={active}
              onClick={() => onSelect(active ? null : key)}
              className="flex items-center gap-2 px-3 py-1.5 text-left text-sm transition-colors hover:bg-[var(--surface-raised)]"
              style={{
                borderTop:
                  i === 0 ? undefined : "1px solid var(--border-subtle)",
                ...(active
                  ? { backgroundColor: "var(--accent-subtle)" }
                  : { backgroundColor: "var(--surface)" }),
              }}
            >
              <span
                aria-hidden
                className="h-2 w-2 shrink-0 rounded-full"
                style={{ backgroundColor: colorFor(key) }}
              />
              <span
                className="min-w-0 flex-1 truncate"
                style={{
                  color: active ? "var(--accent)" : "var(--text-secondary)",
                  fontWeight: active ? 600 : 400,
                }}
              >
                {labelFor(key)}
              </span>
              <span
                className="shrink-0 tabular-nums text-xs"
                style={{ color: "var(--text-muted)" }}
              >
                {counts[key]}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
