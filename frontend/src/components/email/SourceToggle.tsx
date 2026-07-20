"use client";

/**
 * Self-hiding source segmented control (All / Zendesk / Toy Dataset).
 *
 * `toy_dataset` is temporary demo data that will eventually be retired. This
 * toggle keys off the DISTINCT sources actually present in the data (from the
 * facets aggregate): when only ONE source remains it renders nothing, so there
 * is no dead UI to clean up once the demo data is gone. With both sources
 * present it behaves as a normal filter.
 */

type SourceValue = "all" | string;

interface SourceToggleProps {
  /** Distinct sources present in the whole table (facets.sources). */
  sources: string[];
  /** Current selection: "all" or a specific source. */
  value: SourceValue;
  onChange: (value: SourceValue) => void;
}

/** Friendly labels for the known ingestion sources. */
const SOURCE_LABELS: Record<string, string> = {
  zendesk: "Zendesk",
  toy_dataset: "Toy Dataset",
};

/** Preferred display order; anything unknown is appended alphabetically. */
const SOURCE_ORDER = ["zendesk", "toy_dataset"];

function labelFor(source: string): string {
  return (
    SOURCE_LABELS[source] ??
    source
      .split(/[_\s]+/)
      .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
      .join(" ")
  );
}

function orderSources(sources: string[]): string[] {
  return [...sources].sort((a, b) => {
    const ia = SOURCE_ORDER.indexOf(a);
    const ib = SOURCE_ORDER.indexOf(b);
    if (ia !== -1 && ib !== -1) return ia - ib;
    if (ia !== -1) return -1;
    if (ib !== -1) return 1;
    return a.localeCompare(b);
  });
}

export function SourceToggle({ sources, value, onChange }: SourceToggleProps) {
  // Self-hide: with fewer than two distinct sources there is nothing to choose
  // between, so the whole control disappears (no future cleanup needed).
  if (sources.length < 2) return null;

  const options: { value: SourceValue; label: string }[] = [
    { value: "all", label: "All" },
    ...orderSources(sources).map((s) => ({ value: s, label: labelFor(s) })),
  ];

  return (
    <div
      role="group"
      aria-label="Filter by source"
      className="flex gap-1 rounded-lg p-1"
      style={{ backgroundColor: "var(--surface)" }}
    >
      {options.map(({ value: v, label }) => {
        const active = value === v;
        return (
          <button
            key={v}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(v)}
            className="flex-1 rounded-md px-2 py-1.5 text-xs font-medium transition-colors"
            style={
              active
                ? { backgroundColor: "var(--accent-subtle)", color: "var(--accent)" }
                : { color: "var(--text-secondary)" }
            }
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
