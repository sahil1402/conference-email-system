"use client";

import { UserRound } from "lucide-react";

import { cn } from "@/lib/utils";
import { chairColor } from "@/lib/format";
import { useTheme } from "@/hooks/useTheme";

interface ChairBadgeProps {
  /** The assigned chair id, or null when the email has no chair. */
  chairId: number | null;
  /** Resolved chair name; falls back to `Chair #id` if the roster lacks it. */
  chairName?: string | null;
  size?: "sm" | "md";
  className?: string;
}

/**
 * A pill showing which chair an email is assigned to, color-coded per chair.
 * Matches the visual language of {@link Badge} (rounded-full, token-driven
 * spacing) but takes a per-chair color from {@link chairColor}. Renders a
 * muted "Unassigned" pill when `chairId` is null (a human-review email that has
 * no chair yet), so the absence is explicit rather than invisible.
 */
export function ChairBadge({
  chairId,
  chairName,
  size = "sm",
  className,
}: ChairBadgeProps) {
  const { theme } = useTheme();
  const base = cn(
    "inline-flex items-center gap-1 whitespace-nowrap rounded-full font-medium leading-none",
    size === "sm" ? "px-2 py-1 text-[11px]" : "px-2.5 py-1 text-xs",
    className
  );

  if (chairId == null) {
    return (
      <span
        className={base}
        style={{
          color: "var(--text-muted)",
          backgroundColor: "var(--surface-raised)",
        }}
      >
        <UserRound className="h-3 w-3" />
        Unassigned
      </span>
    );
  }

  const { color, bg } = chairColor(chairId, theme);
  return (
    <span className={base} style={{ color, backgroundColor: bg }}>
      <UserRound className="h-3 w-3" />
      {chairName ?? `Chair #${chairId}`}
    </span>
  );
}
