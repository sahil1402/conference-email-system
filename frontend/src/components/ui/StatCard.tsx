import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface StatCardProps {
  label: string;
  value: string | number;
  delta?: string;
  icon?: ReactNode;
  /** Accent color for the icon chip (defaults to the indigo accent). */
  accent?: string;
  className?: string;
}

/** A dashboard metric card: big value, label, optional icon + delta. */
export function StatCard({
  label,
  value,
  delta,
  icon,
  accent = "var(--accent)",
  className,
}: StatCardProps) {
  return (
    <div
      className={cn(
        "flex flex-col gap-3 rounded-xl border p-5 transition-[transform,box-shadow] duration-150 hover:-translate-y-px hover:shadow-lg hover:shadow-black/20",
        className
      )}
      style={{
        backgroundColor: "var(--surface)",
        borderColor: "var(--border)",
      }}
    >
      <div className="flex items-start justify-between">
        <span
          className="text-sm font-medium"
          style={{ color: "var(--text-secondary)" }}
        >
          {label}
        </span>
        {icon && (
          <span
            className="flex h-8 w-8 items-center justify-center rounded-lg"
            style={{ color: accent, backgroundColor: "var(--accent-subtle)" }}
          >
            {icon}
          </span>
        )}
      </div>

      <div className="flex flex-col gap-1">
        <span
          className="text-3xl font-semibold tracking-tight tabular-nums"
          style={{ color: "var(--text-primary)" }}
        >
          {value}
        </span>
        {delta && (
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>
            {delta}
          </span>
        )}
      </div>
    </div>
  );
}
