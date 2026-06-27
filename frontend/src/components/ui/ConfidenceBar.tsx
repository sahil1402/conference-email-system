import { cn } from "@/lib/utils";

interface ConfidenceBarProps {
  /** Confidence in [0, 1]. */
  value: number;
  showLabel?: boolean;
  className?: string;
}

/** Color thresholds: green ≥ 0.8, yellow 0.5–0.79, red < 0.5. */
function colorFor(value: number): string {
  if (value >= 0.8) return "var(--success)";
  if (value >= 0.5) return "var(--warning)";
  return "var(--danger)";
}

/** A thin horizontal confidence meter with an optional percentage label. */
export function ConfidenceBar({
  value,
  showLabel = false,
  className,
}: ConfidenceBarProps) {
  const clamped = Math.max(0, Math.min(1, value));
  const pct = Math.round(clamped * 100);
  const color = colorFor(clamped);

  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div
        className="h-1.5 flex-1 overflow-hidden rounded-full"
        style={{ backgroundColor: "var(--surface-raised)" }}
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className="h-full rounded-full transition-[width] duration-500 ease-out"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
      {showLabel && (
        <span
          className="w-9 shrink-0 text-right text-xs font-medium tabular-nums"
          style={{ color }}
        >
          {pct}%
        </span>
      )}
    </div>
  );
}
