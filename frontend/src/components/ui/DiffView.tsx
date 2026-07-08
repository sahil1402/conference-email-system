import { useMemo } from "react";

import { wordDiff } from "@/lib/diff";

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
                backgroundColor: "rgba(16,185,129,0.18)",
                color: "#34d399",
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
              backgroundColor: "rgba(239,68,68,0.16)",
              color: "#f87171",
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
  return (
    <div
      className="flex items-center gap-4 text-xs"
      style={{ color: "var(--text-muted)" }}
    >
      <span className="inline-flex items-center gap-1.5">
        <span
          className="inline-block h-2.5 w-2.5 rounded-sm"
          style={{ backgroundColor: "rgba(16,185,129,0.5)" }}
        />
        added
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span
          className="inline-block h-2.5 w-2.5 rounded-sm"
          style={{ backgroundColor: "rgba(239,68,68,0.5)" }}
        />
        removed
      </span>
    </div>
  );
}
