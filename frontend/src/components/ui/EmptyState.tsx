import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface EmptyStateProps {
  icon: ReactNode;
  title: string;
  description: string;
  action?: ReactNode;
  className?: string;
}

/** Centered empty placeholder: icon, heading, subtext, optional CTA. */
export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 px-6 py-14 text-center",
        className
      )}
    >
      <div
        className="flex h-12 w-12 items-center justify-center rounded-xl"
        style={{
          color: "var(--text-secondary)",
          backgroundColor: "var(--surface-raised)",
        }}
      >
        {icon}
      </div>
      <div className="flex flex-col gap-1">
        <h3
          className="text-sm font-semibold"
          style={{ color: "var(--text-primary)" }}
        >
          {title}
        </h3>
        <p
          className="mx-auto max-w-sm text-sm"
          style={{ color: "var(--text-muted)" }}
        >
          {description}
        </p>
      </div>
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
