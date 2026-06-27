import type { CSSProperties, ReactNode } from "react";

import { cn } from "@/lib/utils";

export type BadgeVariant =
  | "faq"
  | "review"
  | "success"
  | "warning"
  | "danger"
  | "neutral";

interface BadgeProps {
  variant?: BadgeVariant;
  size?: "sm" | "md";
  children: ReactNode;
  className?: string;
}

/** Per-variant foreground + background, all sourced from design tokens. */
const VARIANT_STYLE: Record<BadgeVariant, CSSProperties> = {
  faq: { color: "var(--faq-color)", backgroundColor: "var(--accent-subtle)" },
  review: {
    color: "var(--review-color)",
    backgroundColor: "var(--warning-subtle)",
  },
  success: { color: "var(--success)", backgroundColor: "var(--success-subtle)" },
  warning: { color: "var(--warning)", backgroundColor: "var(--warning-subtle)" },
  danger: { color: "var(--danger)", backgroundColor: "var(--danger-subtle)" },
  neutral: {
    color: "var(--text-secondary)",
    backgroundColor: "var(--surface-raised)",
  },
};

/** A small pill badge. Color is driven entirely by `variant`. */
export function Badge({
  variant = "neutral",
  size = "md",
  children,
  className,
}: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full font-medium leading-none",
        size === "sm" ? "px-2 py-1 text-[11px]" : "px-2.5 py-1 text-xs",
        className
      )}
      style={VARIANT_STYLE[variant]}
    >
      {children}
    </span>
  );
}
