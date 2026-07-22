import { useMemo } from "react";

import { wordDiff } from "@/lib/diff";
import { useTheme } from "@/hooks/useTheme";

// Inline diff colors (applied as SVG-free inline styles, so unlike the rest of
// the UI they can't reference CSS vars) mirrored per theme. `dark` reproduces
// the original values 1:1; `light` reuses the T2 [data-theme="light"] tokens —
// --success/--danger for text and --success-subtle/--danger-subtle for the
// highlight backgrounds, since low-opacity rgba tuned for a dark surface reads
// wrong over white. Legend swatches go solid in light mode so they stay visible.
const DIFF_PALETTE = {
  dark: {
    addText: "#34d399",
    addBg: "rgba(16,185,129,0.18)",
    removeText: "#f87171",
    removeBg: "rgba(239,68,68,0.16)",
    addSwatch: "rgba(16,185,129,0.5)",
    removeSwatch: "rgba(239,68,68,0.5)",
  },
  light: {
    addText: "#059669", // --success (light)
    addBg: "#e6f7f1", // --success-subtle (light)
    removeText: "#dc2626", // --danger (light)
    removeBg: "#fdeaea", // --danger-subtle (light)
    addSwatch: "#059669", // solid — a 0.5-opacity tint is near-invisible on white
    removeSwatch: "#dc2626",
  },
} as const;

/**
 * Renders an inline word-level diff of original → edited text.
 * Removed text: red, struck through. Added text: green. Unchanged: normal.
 */
export function DiffView({
  original,
  edited,
}: {
  original: string;
  edited: string;
}) {
  const ops = useMemo(() => wordDiff(original, edited), [original, edited]);
  const { theme } = useTheme();
  const C = DIFF_PALETTE[theme];

  return (
    <div
      className="rounded-lg border p-3 text-sm leading-relaxed"
      style={{
        backgroundColor: "var(--surface-raised)",
        borderColor: "var(--border-subtle)",
        color: "var(--text-primary)",
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
        fontFamily:
          'ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace',
      }}
    >
      {ops.map((op, i) => {
        if (op.type === "equal") return <span key={i}>{op.value}</span>;
        if (op.type === "added") {
          return (
            <span
              key={i}
              style={{
                backgroundColor: C.addBg,
                color: C.addText,
                borderRadius: 2,
              }}
            >
              {op.value}
            </span>
          );
        }
        return (
          <span
            key={i}
            style={{
              backgroundColor: C.removeBg,
              color: C.removeText,
              textDecoration: "line-through",
              borderRadius: 2,
            }}
          >
            {op.value}
          </span>
        );
      })}
    </div>
  );
}

/** Small legend explaining the diff colors. */
export function DiffLegend() {
  const { theme } = useTheme();
  const C = DIFF_PALETTE[theme];

  return (
    <div
      className="flex items-center gap-4 text-xs"
      style={{ color: "var(--text-muted)" }}
    >
      <span className="inline-flex items-center gap-1.5">
        <span
          className="inline-block h-2.5 w-2.5 rounded-sm"
          style={{ backgroundColor: C.addSwatch }}
        />
        added
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span
          className="inline-block h-2.5 w-2.5 rounded-sm"
          style={{ backgroundColor: C.removeSwatch }}
        />
        removed
      </span>
    </div>
  );
}
